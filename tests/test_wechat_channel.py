"""微信仅转发消息；验证持久收件、原生运行、审批和回复投递的边界。"""
import asyncio
import copy
import json
import sqlite3
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ThinkingPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.settings import ModelSettings

from erp_web.context import get_context
from erp_web.http_handler import Handler
from erp_web.schemas.wechat_channel import WECHAT_FORWARD_TYPES
from erp_web.facades.ai_chat_facade import build_conversation_service, build_ui_service
from erp_web.services.ai_model_factory import PydanticModelBinding
from erp_web.services.chat_conversation_service import ChatConversationError
from erp_web.services import wechat_channel_service as channel_module
from erp_web.services.wechat_channel_service import WechatChannelService, text_chunks, visible_reply
from erp_web.stores.wechat_channel_store import WechatChannelStore
from wechatbot.protocol import ILinkApi

CONVERSATION = "conversation_global_chat_" + "a" * 32
CREDENTIALS = {"token": "TEST_BOT_SECRET", "account_id": "bot", "user_id": "owner@im.wechat", "base_url": "https://ilinkai.weixin.qq.com"}


class FakeApi:
    build_text_message = staticmethod(ILinkApi.build_text_message)

    def __init__(self):
        self.sent = []
        self.fail = False

    async def send_message(self, base_url, token, msg):
        self.sent.append(copy.deepcopy(msg))
        if self.fail:
            raise TimeoutError("测试：发送结果不明")
        return {"ret": 0, "message_id": "sent"}


@pytest.fixture()
def channel(tmp_path, monkeypatch):
    context = get_context()
    claim = context.chat_turn_claims.claim_turn(conversation_id=CONVERSATION, client_message_id="seed", profile_id="global.chat")
    context.chat_turn_claims.finish_turn(claim.claim_id, status="completed")
    context.pydantic_messages.save(CONVERSATION, [
        ModelRequest(parts=[UserPromptPart("原有网页问题")]),
        ModelResponse(parts=[TextPart("原有回答不应补发")], metadata={"chat_final": True}, run_id="seed"),
    ])
    conversations = build_conversation_service(context)
    calls = []

    async def respond(messages, info):
        calls.append(messages)
        yield "ERP 已处理"

    binding = PydanticModelBinding(model=FunctionModel(stream_function=respond), model_settings=ModelSettings(),
                                  model_id="test", model_name="test", provider_id="test", provider_family="test", api_style="chat_completions")
    conversations.chat_service.factory.model_binding_factory = lambda *args, **kwargs: binding
    store = WechatChannelStore(tmp_path / "wechat.sqlite3", get_context().config)
    store.configure(conversation_id=CONVERSATION, history_position=2, credentials=CREDENTIALS)
    service = WechatChannelService(
        store=store, conversations=conversations, api=FakeApi(),
        forward_types_reader=lambda: context.config.system_settings()["wechat_forward_types"],
        forward_types_writer=lambda values: context.config.save_system_settings({"wechat_forward_types": values}),
    )
    monkeypatch.setattr(channel_module, "get_detached_chat_runner", lambda: SimpleNamespace(submit=lambda coro: asyncio.run(coro)))
    return SimpleNamespace(service=service, store=store, context=context, calls=calls)


def incoming(message_id="123", text="查询商品", **changes):
    raw = {"message_id": message_id, "from_user_id": CREDENTIALS["user_id"], "message_type": 1,
           "message_state": 2, "context_token": "TEST_CONTEXT_SECRET", "item_list": [{"type": 1, "text_item": {"text": text}}]}
    return {**raw, **changes}


def ingest(channel, messages, cursor="cursor-1"):
    channel.service.ingest_updates(channel.store.state(), {"get_updates_buf": cursor, "msgs": messages})


def test_wechat_input_uses_same_native_history_and_web_projection(channel):
    ingest(channel, [incoming()])
    channel.service.transfer_inputs(channel.store.state())
    assert len(channel.calls) == 1
    payload = build_ui_service(channel.context).dump_ui_messages(CONVERSATION)
    texts = [part["text"] for msg in payload["messages"] for part in msg["parts"] if part["type"] == "text"]
    assert texts == ["原有网页问题", "原有回答不应补发", "查询商品", "ERP 已处理"]
    assert payload["run_status"] == "completed"
    channel.service.collect_replies(channel.store.state())
    asyncio.run(channel.service.send_pending(channel.store.state()))
    assert [m["item_list"][0]["text_item"]["text"] for m in channel.service.api.sent] == ["ERP 已处理"]


def test_duplicate_replay_and_restart_do_not_execute_again(channel):
    ingest(channel, [incoming(), incoming()])
    channel.service.transfer_inputs(channel.store.state())
    restored = WechatChannelStore(channel.store.path, get_context().config)
    channel.service.store = restored
    channel.store = restored
    ingest(channel, [incoming()], "cursor-2")
    channel.service.transfer_inputs(restored.state())
    assert len(channel.calls) == 1
    assert restored.state()["cursor"] == "cursor-2"
    assert restored.inbox(restored.state()["generation"]) == []


def test_rebinding_same_account_to_another_conversation_does_not_replay_seen_input(channel):
    ingest(channel, [incoming()])
    channel.service.transfer_inputs(channel.store.state())
    channel.store.configure(conversation_id=CONVERSATION, history_position=4, credentials=CREDENTIALS)
    ingest(channel, [incoming()], "new-cursor")
    channel.service.transfer_inputs(channel.store.state())
    assert len(channel.calls) == 1


def test_empty_cursor_does_not_erase_resume_position(channel):
    ingest(channel, [], "saved-cursor")
    channel.service.ingest_updates(channel.store.state(), {"get_updates_buf": None, "msgs": []})
    assert channel.store.state()["cursor"] == "saved-cursor"


def test_new_input_uses_current_common_service_configuration(channel):
    calls = []
    current = channel.service.conversations
    channel.service.conversation_factory = lambda: calls.append("current") or current
    channel.service.transfer_inputs(channel.store.state())
    assert calls == []
    ingest(channel, [incoming()])
    channel.service.transfer_inputs(channel.store.state())
    assert calls == ["current"]
    assert len(channel.calls) == 1


def test_crash_after_erp_acceptance_recovers_without_replaying_business(channel, monkeypatch):
    ingest(channel, [incoming()])
    received = channel.store.received
    monkeypatch.setattr(channel.store, "received", lambda *args: (_ for _ in ()).throw(RuntimeError("测试中断")))
    with pytest.raises(RuntimeError):
        channel.service.transfer_inputs(channel.store.state())
    monkeypatch.setattr(channel.store, "received", received)
    channel.service.transfer_inputs(channel.store.state())
    assert len(channel.calls) == 1
    assert channel.store.inbox(channel.store.state()["generation"]) == []


def test_inbox_and_cursor_roll_back_together(channel):
    state = channel.store.state()
    with pytest.raises(TypeError):
        channel.store.ingest(state["generation"], "too-early", [{"message_id": "bad", "invalid": {object()}}])
    assert channel.store.state()["cursor"] == ""
    assert channel.store.inbox(state["generation"]) == []


def test_reply_failure_retries_only_delivery_with_same_identifier(channel):
    ingest(channel, [incoming()])
    channel.service.transfer_inputs(channel.store.state())
    channel.service.collect_replies(channel.store.state())
    channel.service.api.fail = True
    with pytest.raises(TimeoutError):
        asyncio.run(channel.service.send_pending(channel.store.state()))
    channel.service.store = WechatChannelStore(channel.store.path, get_context().config)
    channel.service.collect_replies(channel.service.store.state())
    channel.service.api.fail = False
    asyncio.run(channel.service.send_pending(channel.service.store.state()))
    assert len(channel.calls) == 1
    assert len(channel.service.api.sent) == 2
    assert channel.service.api.sent[0]["client_id"] == channel.service.api.sent[1]["client_id"]
    assert channel.service.store.pending(channel.service.store.state()["generation"]) == []


@pytest.mark.parametrize("change", [{"from_user_id": "other"}, {"message_type": 2}, {"group_id": "group"}, {"message_state": 1}])
def test_only_bound_owner_final_private_messages_are_accepted(channel, change):
    ingest(channel, [incoming(**change)])
    channel.service.transfer_inputs(channel.store.state())
    assert channel.calls == []
    assert channel.store.state()["context_token"] == ""


def test_nontext_gives_notice_without_running_agent(channel):
    ingest(channel, [incoming(item_list=[{"type": 2, "image_item": {}}])])
    channel.service.transfer_inputs(channel.store.state())
    assert channel.calls == []
    assert "只支持文字" in channel.store.pending(channel.store.state()["generation"])[0]["text"]


def test_switch_discards_inflight_old_generation_and_replayed_old_messages(channel):
    old = channel.store.state()
    channel.store.configure(conversation_id=CONVERSATION, history_position=2)
    channel.service.ingest_updates(old, {"get_updates_buf": "old", "msgs": [incoming()]})
    ingest(channel, [incoming(create_time_ms=1)])
    assert channel.store.inbox(channel.store.state()["generation"]) == []
    assert channel.store.state()["cursor"] == "cursor-1"


def test_ordinary_web_turn_is_forwarded_and_thinking_and_tools_are_hidden(channel):
    run = channel.service.conversations.accept_text(CONVERSATION, "web-user", "网页继续")
    asyncio.run(run.run())
    history = channel.context.pydantic_messages.get(CONVERSATION).model_messages()
    history.append(ModelResponse(parts=[ThinkingPart("原生推理"), TextPart("<think>标签推理</think>最终回答")], metadata={"chat_final": True}, run_id="final"))
    history.append(ModelResponse(parts=[TextPart("中间工具说明"), ToolCallPart("product_read", {})], run_id="tools"))
    channel.context.pydantic_messages.save(CONVERSATION, history)
    channel.service.collect_replies(channel.store.state())
    texts = [r["text"] for r in channel.store.pending(channel.store.state()["generation"])]
    assert texts == ["ERP 已处理", "最终回答"]


def test_native_approval_is_not_granted_by_wechat_text(channel):
    store = channel.context.agent_calls
    history = channel.context.pydantic_messages.get(CONVERSATION)
    requests = DeferredToolRequests(approvals=[ToolCallPart("product_delete", {"id": "x"}, tool_call_id="approve-1")])
    store.commit(CONVERSATION, history.model_messages(), expected_version=history.history_version, requests=requests)
    channel.service.collect_replies(channel.store.state())
    channel.service.collect_replies(channel.store.state())
    assert len(channel.store.pending(channel.store.state()["generation"])) == 1
    assert "ERP 网页确认" in channel.store.pending(channel.store.state()["generation"])[0]["text"]
    ingest(channel, [incoming(text="确认 approve-1")])
    channel.service.transfer_inputs(channel.store.state())
    assert store.pending(CONVERSATION)[1].approvals == {}
    assert channel.calls == []
    # 网页批准后仍由共用入口恢复；渠道不会自己执行工具。
    store.record_results(CONVERSATION, DeferredToolResults(approvals={"approve-1": False}))
    assert store.ready(CONVERSATION)


def test_unbind_removes_credentials_and_pending_transfer_but_keeps_erp_history(channel):
    ingest(channel, [incoming()])
    before = channel.context.pydantic_messages.get(CONVERSATION)
    channel.service.unbind()
    assert channel.store.state()["credentials"] is None
    assert channel.store.state()["context_token"] == ""
    assert channel.store.inbox(channel.store.state()["generation"]) == []
    assert channel.context.pydantic_messages.get(CONVERSATION) == before
    assert channel.store.path.stat().st_mode & 0o777 == 0o600


def test_status_excludes_credentials_and_rejects_nonchat_history(channel):
    ingest(channel, [incoming()])
    payload = json.dumps(channel.service.status(), ensure_ascii=False)
    assert "TEST_BOT_SECRET" not in payload and "TEST_CONTEXT_SECRET" not in payload
    bad = "conversation_global_chat_" + "b" * 32
    channel.context.pydantic_messages.save(bad, [ModelResponse(parts=[TextPart("任务记录")])])
    with pytest.raises(ChatConversationError):
        channel.service.validate_conversation(bad)


def test_login_uses_sdk_redirect_and_pairing_and_returns_only_public_status(channel, monkeypatch):
    api = channel.service.api
    statuses = iter([{"status": "scaned_but_redirect", "redirect_host": "region.weixin.qq.com"},
                     {"status": "need_verifycode"},
                     {"status": "confirmed", "bot_token": "NEW_SECRET", "ilink_bot_id": "new-bot",
                      "ilink_user_id": "scanner", "baseurl": "https://region.weixin.qq.com"}])
    requests = []

    async def get_qr(*args):
        return {"qrcode": "qr-id", "qrcode_img_content": "https://weixin.qq.com/login/qr"}

    async def poll(base_url, qr, code):
        requests.append((base_url, code))
        return next(statuses)

    async def no_delay(_):
        if channel.service._status == "need_verifycode":
            channel.service.verify("123456")

    api.get_qr_code, api.poll_qr_status = get_qr, poll
    monkeypatch.setattr(channel_module.asyncio, "sleep", no_delay)
    asyncio.run(channel.service._login(0, None))
    assert requests[-1] == ("https://region.weixin.qq.com", "123456")
    assert channel.store.state()["credentials"]["user_id"] == "scanner"
    assert "NEW_SECRET" not in json.dumps(channel.service.status())
    assert channel.service.status()["qr_image"] == ""
    conversation_id = channel.store.state()["conversation_id"]
    assert conversation_id != CONVERSATION
    assert channel.context.pydantic_messages.get(conversation_id).model_messages() == []
    # 空会话已有可信归属，但没有伪造用户轮次；网页可以直接继续。
    assert channel.context.chat_turn_claims.find_for_conversation(conversation_id) is None
    build_ui_service(channel.context)._verify_history_ownership(conversation_id)
    assert conversation_id in [row.conversation_id for row in channel.context.pydantic_messages.list()]


def test_qr_is_available_without_existing_conversation_and_poll_timeout_keeps_it(channel, monkeypatch):
    channel.context.pydantic_messages.delete(CONVERSATION)
    observed = []

    async def get_qr(*args):
        return {"qrcode": "qr", "qrcode_img_content": "https://weixin.qq.com/login/test"}

    async def poll(*args):
        observed.append(channel.service.status())
        if len(observed) == 1:
            raise TimeoutError("测试长轮询超时")
        return {"status": "expired"}

    async def no_delay(_):
        pass

    channel.service.api.get_qr_code, channel.service.api.poll_qr_status = get_qr, poll
    monkeypatch.setattr(channel_module.asyncio, "sleep", no_delay)
    asyncio.run(channel.service._login(0, None))
    assert all(row["qr_image"].startswith("data:image/png;base64,") for row in observed)
    assert len(observed) == 2
    assert channel.context.pydantic_messages.list() == []


def test_http_login_needs_no_conversation_and_status_exposes_qr_without_credentials():
    context = get_context()
    service = context.wechat

    async def get_qr(*args):
        return {"qrcode": "test-qr", "qrcode_img_content": "https://weixin.qq.com/login/test"}

    async def poll(*args):
        await asyncio.sleep(60)

    service.api.get_qr_code, service.api.poll_qr_status = get_qr, poll
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    from tests.account_test_utils import authenticated_headers
    auth_headers = authenticated_headers(server)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        request = urllib.request.Request(url + "/api/wechat/login", data=b"{}", headers={"Content-Type": "application/json", **auth_headers})
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.load(response)
        assert payload["ok"] and not payload["bound"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with urllib.request.urlopen(urllib.request.Request(url + "/api/wechat/status", headers=auth_headers), timeout=3) as response:
                payload = json.load(response)
            if payload["qr_image"]:
                break
            time.sleep(0.01)
        assert payload["qr_image"].startswith("data:image/png;base64,")
        assert "conversations" not in payload and "token" not in payload
        assert context.pydantic_messages.list() == []
    finally:
        service.unbind()
        service.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_unregistered_empty_history_cannot_be_claimed_as_a_regular_conversation(channel):
    unowned = "conversation_global_chat_" + "d" * 32
    channel.context.pydantic_messages.save(unowned, [])
    with pytest.raises(ChatConversationError, match="不属于全局对话"):
        channel.service.conversations.accept_text(unowned, "forged", "不能接管")


def test_new_command_creates_and_binds_empty_conversation_without_model(channel):
    old = channel.context.pydantic_messages.get(CONVERSATION)
    ingest(channel, [incoming(text="/new")])
    cursor = channel.store.state()["cursor"]
    channel.service.transfer_inputs(channel.store.state())
    state = channel.store.state()
    assert state["conversation_id"] != CONVERSATION
    assert state["cursor"] == cursor
    assert state["context_token"] == "TEST_CONTEXT_SECRET"
    assert channel.context.pydantic_messages.get(state["conversation_id"]).model_messages() == []
    assert channel.context.pydantic_messages.get(CONVERSATION) == old
    assert channel.calls == []
    assert channel.store.pending(state["generation"])[0]["text"] == "已新建对话，请发送新的消息。"


def test_new_command_routes_following_messages_in_same_batch_to_new_history(channel):
    ingest(channel, [incoming("before", "原会话的问题"), incoming("command", "/new"), incoming("after", "新会话的问题")])
    channel.service.transfer_inputs(channel.store.state())
    state = channel.store.state()
    assert len(channel.calls) == 2
    user_text = [p.content for m in channel.calls[-1] for p in m.parts if isinstance(p, UserPromptPart)]
    assert user_text == ["新会话的问题"]
    assert any(isinstance(p, UserPromptPart) and p.content == "原会话的问题"
               for m in channel.context.pydantic_messages.get(CONVERSATION).model_messages() for p in m.parts)
    channel.service.collect_replies(state)
    texts = [r["text"] for r in channel.store.pending(state["generation"])]
    assert texts == ["已新建对话，请发送新的消息。", "ERP 已处理"]
    assert channel.store.inbox(state["generation"]) == []


def test_new_command_replay_after_crash_reuses_reservation_and_does_not_overwrite_history(channel, monkeypatch):
    ingest(channel, [incoming(text="/new")])
    switch = channel.store.new_conversation
    monkeypatch.setattr(channel.store, "new_conversation", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("切换前中断")))
    with pytest.raises(RuntimeError):
        channel.service.transfer_inputs(channel.store.state())
    reserved = [row.conversation_id for row in channel.context.pydantic_messages.list() if row.conversation_id != CONVERSATION]
    assert len(reserved) == 1
    monkeypatch.setattr(channel.store, "new_conversation", switch)
    channel.service.transfer_inputs(channel.store.state())
    assert channel.store.state()["conversation_id"] == reserved[0]
    ingest(channel, [incoming(text="/new")], "replayed")
    channel.service.transfer_inputs(channel.store.state())
    assert len(channel.context.pydantic_messages.list()) == 2
    assert len(channel.store.pending(channel.store.state()["generation"])) == 1
    assert channel.calls == []


def test_new_does_not_cancel_old_run_and_embedded_command_is_regular_text(channel):
    registry = channel.service.conversations.run_registry
    assert registry.acquire(CONVERSATION)
    try:
        ingest(channel, [incoming(text="/new")])
        channel.service.transfer_inputs(channel.store.state())
        assert registry.is_active(CONVERSATION)
        assert not registry.token(CONVERSATION).cancelled
        ingest(channel, [incoming("regular", "解释 /new 的含义")])
        conversation_id = channel.store.state()["conversation_id"]
        channel.service.transfer_inputs(channel.store.state())
        assert channel.store.state()["conversation_id"] == conversation_id
        assert len(channel.calls) == 1
    finally:
        registry.release(CONVERSATION)


def test_context_close_stops_only_created_channel_without_network(tmp_path):
    context = get_context()
    assert context._wechat is None
    service = context.wechat
    service.start()
    context.close()
    context.close()
    assert not service._thread.is_alive()


def test_visible_text_and_unicode_byte_chunks():
    assert visible_reply(" <think>隐藏</think>\n<think>也隐藏</think>回答") == "回答"
    assert visible_reply("<think>未闭合") == ""
    assert visible_reply("正文提及 <think> 标签") == "正文提及 <think> 标签"
    text = "中文😀" * 2000
    chunks = text_chunks(text)
    assert "".join(chunks) == text
    assert all(len(part.encode()) <= 3500 for part in chunks)


def test_forwarding_preferences_persist_without_changing_other_settings_or_binding(channel):
    before = channel.context.config.load_app_config()
    binding = channel.store.state()
    assert channel.service.status()["forward_types"] == list(WECHAT_FORWARD_TYPES)
    channel.service.save_forward_types(["error", "reply", "reply"])
    assert channel.service.status()["forward_types"] == ["reply", "error"]
    expected = copy.deepcopy(before)
    expected["system_settings"]["wechat_forward_types"] = ["reply", "error"]
    assert channel.context.config.load_app_config() == expected
    assert channel.store.state() == binding
    channel.service.unbind()
    assert channel.service.status()["forward_types"] == ["reply", "error"]


@pytest.mark.parametrize("selected", ["reply", "progress", "approval", "error"])
def test_only_selected_forwarding_type_is_delivered(channel, selected):
    channel.service.save_forward_types([selected])
    history = channel.context.pydantic_messages.get(CONVERSATION)
    messages = [*history.model_messages(),
                ModelResponse(parts=[ThinkingPart("隐藏"), TextPart("<think>也隐藏</think>阶段说明"), ToolCallPart("drafts_query", {})],
                              metadata={"chat_progress": True}, run_id="stage"),
                ModelResponse(parts=[TextPart("最终结果")], metadata={"chat_final": True}, run_id="result"),
                ModelRequest(parts=[UserPromptPart("失败的输入")], metadata={"ai_run_error": {"code": "TEST_FAILURE"}})]
    requests = DeferredToolRequests(approvals=[ToolCallPart("product_delete", {}, tool_call_id="approval-filter")])
    channel.context.agent_calls.commit(CONVERSATION, messages, expected_version=history.history_version, requests=requests)
    channel.service.collect_replies(channel.store.state())
    rows = channel.store.pending(channel.store.state()["generation"])
    assert len(rows) == 1 and rows[0]["forward_type"] == selected
    assert "隐藏" not in rows[0]["text"]


def test_disabling_type_skips_queued_replies_and_reenabling_does_not_resend(channel):
    ingest(channel, [incoming()])
    channel.service.transfer_inputs(channel.store.state())
    channel.service.collect_replies(channel.store.state())
    assert len(channel.store.pending(channel.store.state()["generation"])) == 1
    channel.service.save_forward_types([])
    assert channel.store.pending(channel.store.state()["generation"]) == []
    channel.service.save_forward_types(list(WECHAT_FORWARD_TYPES))
    channel.service.collect_replies(channel.store.state())
    asyncio.run(channel.service.send_pending(channel.store.state()))
    assert channel.service.api.sent == []
    # 新的输入仍正常生成并转发结果，旧消息不会复活。
    ingest(channel, [incoming("next", "下一条")])
    channel.service.transfer_inputs(channel.store.state())
    channel.service.collect_replies(channel.store.state())
    asyncio.run(channel.service.send_pending(channel.store.state()))
    assert len(channel.service.api.sent) == 1


def test_all_types_disabled_still_runs_erp_and_new_still_creates_binding(channel):
    channel.service.save_forward_types([])
    ingest(channel, [incoming()])
    channel.service.transfer_inputs(channel.store.state())
    channel.service.collect_replies(channel.store.state())
    assert len(channel.calls) == 1
    old_history = channel.context.pydantic_messages.get(CONVERSATION)
    assert old_history.model_messages()[-1].parts[0].content == "ERP 已处理"
    ingest(channel, [incoming("new-muted", "/new")])
    channel.service.transfer_inputs(channel.store.state())
    state = channel.store.state()
    assert state["conversation_id"] != CONVERSATION
    assert channel.context.pydantic_messages.get(state["conversation_id"]).model_messages() == []
    assert channel.store.pending(state["generation"]) == []
    assert channel.context.pydantic_messages.get(CONVERSATION) == old_history


def test_settings_changed_during_send_do_not_revive_skipped_queue_items(channel):
    ingest(channel, [incoming()])
    channel.store.received(channel.store.state()["generation"], "123")
    history = channel.context.pydantic_messages.get(CONVERSATION)
    channel.context.pydantic_messages.save(CONVERSATION, [*history.model_messages(),
        ModelResponse(parts=[TextPart("第一条")], metadata={"chat_final": True}, run_id="one"),
        ModelResponse(parts=[TextPart("第二条")], metadata={"chat_final": True}, run_id="two")])
    channel.service.collect_replies(channel.store.state())
    send = channel.service.api.send_message

    async def toggle_during_send(*args):
        result = await send(*args)
        channel.service.save_forward_types([])
        channel.service.save_forward_types(list(WECHAT_FORWARD_TYPES))
        return result

    channel.service.api.send_message = toggle_during_send
    asyncio.run(channel.service.send_pending(channel.store.state()))
    assert [msg["item_list"][0]["text_item"]["text"] for msg in channel.service.api.sent] == ["第一条"]
    assert channel.store.pending(channel.store.state()["generation"]) == []


def test_stopped_notice_waits_for_run_exit_and_is_not_repeated(channel):
    run = channel.service.conversations.accept_text(CONVERSATION, "stop-notice", "开始任务")
    try:
        channel.service.conversations.cancel_run(CONVERSATION, "stop-notice")
        channel.service.collect_replies(channel.store.state())
        assert channel.store.pending(channel.store.state()["generation"]) == []
    finally:
        channel.service.conversations.run_registry.release(run.conversation_id)
    channel.service.collect_replies(channel.store.state())
    channel.service.collect_replies(channel.store.state())
    rows = channel.store.pending(channel.store.state()["generation"])
    assert len(rows) == 1 and rows[0]["forward_type"] == "error"
    assert "已停止" in rows[0]["text"] and "不会自动撤销" in rows[0]["text"]


def test_native_complete_progress_is_sent_before_final_without_forwarding_partial_text(channel):
    async def scenario():
        stage_entered, release, progress_sent, final_sent = (asyncio.Event() for _ in range(4))
        turns = 0

        async def respond(messages, info):
            nonlocal turns
            turns += 1
            if turns == 1:
                yield "<think>内部推理</think>先查询"
                # 未完成的正文仍在原生流中，渠道没有可发送的片段。
                channel.service.collect_replies(channel.store.state())
                assert channel.store.pending(channel.store.state()["generation"]) == []
                yield "草稿。"
                yield {0: DeltaToolCall(name="search_tools", json_args='{"queries":["drafts_query"]}', tool_call_id="progress-search")}
            else:
                stage_entered.set()
                await release.wait()
                yield "最终结果。"

        binding = channel.service.conversations.chat_service.factory.model_binding_factory()
        channel.service.conversations.chat_service.factory.model_binding_factory = lambda *args, **kwargs: PydanticModelBinding(
            model=FunctionModel(stream_function=respond), model_settings=binding.model_settings,
            model_id="test", model_name="test", provider_id="test", provider_family="test", api_style="chat_completions",
        )
        send = channel.service.api.send_message

        async def notify_sent(*args):
            result = await send(*args)
            text = args[-1]["item_list"][0]["text_item"]["text"]
            (progress_sent if text == "先查询草稿。" else final_sent).set()
            return result

        channel.service.api.send_message = notify_sent
        channel.context._wechat = channel.service
        channel.service._loop = asyncio.get_running_loop()
        channel.service._wake_event = asyncio.Event()
        ingest(channel, [incoming("context-only")])
        channel.store.received(channel.store.state()["generation"], "context-only")
        pump = asyncio.create_task(channel.service._pump_loop())
        await asyncio.sleep(0)
        run = channel.service.conversations.accept_text(CONVERSATION, "progress-run", "查询草稿")
        running = asyncio.create_task(run.run())
        try:
            await asyncio.wait_for(stage_entered.wait(), 3)
            await asyncio.wait_for(progress_sent.wait(), 3)
            assert not running.done()
            assert [m["item_list"][0]["text_item"]["text"] for m in channel.service.api.sent] == ["先查询草稿。"]
            release.set()
            await asyncio.wait_for(running, 3)
            await asyncio.wait_for(final_sent.wait(), 3)
            assert [m["item_list"][0]["text_item"]["text"] for m in channel.service.api.sent] == ["先查询草稿。", "最终结果。"]
            texts = [visible_reply(part["text"]) for msg in build_ui_service(channel.context).dump_ui_messages(CONVERSATION)["messages"]
                     for part in msg["parts"] if part["type"] == "text"]
            assert "先查询草稿。" in texts and "最终结果。" in texts
        finally:
            release.set()
            running.cancel()
            pump.cancel()
            await asyncio.gather(running, pump, return_exceptions=True)

    asyncio.run(scenario())


def test_existing_delivery_database_adds_types_without_losing_queued_messages(tmp_path):
    path = tmp_path / "existing-wechat.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE channel_deliveries (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, generation INTEGER NOT NULL,
            delivery_key TEXT NOT NULL,text TEXT NOT NULL,client_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',UNIQUE(generation,delivery_key))""")
        for key in ("final:1", "approval:1", "new:1", "error:1", "unsupported:1", "rejected:1"):
            conn.execute("INSERT INTO channel_deliveries(generation,delivery_key,text,client_id) VALUES(1,?,?,?)", (key, "原有待发内容", key))
    store = WechatChannelStore(path, get_context().config)
    rows = store.pending(1)
    assert [row["forward_type"] for row in rows] == ["reply", "approval", "command", "error", "error", "error"]
    assert len(rows) == 6 and all(row["text"] == "原有待发内容" for row in rows)
    assert WechatChannelStore(path, get_context().config).pending(1) == rows


def test_http_forwarding_settings_validate_and_persist_without_binding():
    context = get_context()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    from tests.account_test_utils import authenticated_headers
    auth_headers = authenticated_headers(server)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/wechat/settings"
    try:
        request = urllib.request.Request(url, data=json.dumps({"forward_types": ["reply", "error"]}).encode(), headers={"Content-Type": "application/json", **auth_headers})
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.load(response)
        assert payload["forward_types"] == ["reply", "error"] and not payload["bound"]
        assert context.config.system_settings()["wechat_forward_types"] == ["reply", "error"]
        request = urllib.request.Request(url, data=b'{"forward_types":[]}', headers={"Content-Type": "application/json", **auth_headers})
        with urllib.request.urlopen(request, timeout=3) as response:
            assert json.load(response)["forward_types"] == []
        general_url = f"http://127.0.0.1:{server.server_port}/api/system-settings"
        request = urllib.request.Request(general_url, data=b'{"wechat_forward_types":[]}', headers={"Content-Type": "application/json", **auth_headers})
        with urllib.request.urlopen(request, timeout=3) as response:
            assert json.load(response)["wechat_forward_types"] == []
        request = urllib.request.Request(url, data=b'{}', headers={"Content-Type": "application/json", **auth_headers})
        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(request, timeout=3)
        assert missing.value.code == 400
        for invalid in ("reply", ["thinking"], [True], None):
            request = urllib.request.Request(url, data=json.dumps({"forward_types": invalid}).encode(), headers={"Content-Type": "application/json", **auth_headers})
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=3)
            assert error.value.code == 400
        assert context.config.system_settings()["wechat_forward_types"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
