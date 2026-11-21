from __future__ import annotations

import asyncio
import gc
import json
import socket
import threading
import time
import warnings
from http.server import ThreadingHTTPServer
from tests.account_test_utils import authenticated_headers, AuthenticatedPort
from typing import Any, Iterator

import http.client
import pytest
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaThinkingPart,
    DeltaToolCall,
    FunctionModel,
)
from pydantic_ai.settings import ModelSettings

from tests.ai_code_mode_helpers import discovering_tools, python_call
from erp_web.context import get_context
from erp_web.http_handler import Handler

from erp_web.services.ai_model_factory import PydanticModelBinding
from erp_web.services.global_agent_chat_service import GLOBAL_CHAT_PROFILE
from erp_web.services.vercel_ai_ui_service import VERCEL_SDK_VERSION


CHAT_PATH = "/api/v1/ai-chat/runs"


def test_branch_copies_saved_prefix_without_running_and_can_continue(chat_server):
    calls = []

    async def model(messages, info):
        calls.append(messages)
        yield "第一轮结果" if len(calls) == 1 else "继续后的结果"

    chat_server["model"]["model"] = FunctionModel(stream_function=model)
    source_id = "conversation_global_chat_" + "b" * 32
    port = chat_server["port"]
    assert _post(port, CHAT_PATH, _submit_body(source_id, "branch-user", "记住订单说明"))[0] == 200
    _, raw = _get(port, f"/api/v1/ai-work/conversations/{source_id}/ui-messages")
    detail = json.loads(raw)
    reply = detail["messages"][-1]
    claim = get_context().chat_turn_claims.get(source_id, "branch-user")
    assert reply["metadata"]["ai_turn"] == {
        "started_at": claim.claimed_at, "finished_at": claim.finished_at, "status": "completed",
    }
    before = get_context().pydantic_messages.get(source_id)
    status, _, raw = _post(port, "/api/v1/ai-chat/fork", {"id": source_id, "message_id": reply["id"]})
    assert status == 200
    branch = json.loads(raw)
    branch_id = branch["conversation_id"]
    assert branch_id != source_id
    assert len(calls) == 1
    assert branch["messages"][-1]["parts"] == reply["parts"]
    assert branch["messages"][-1]["metadata"]["ai_turn"] == reply["metadata"]["ai_turn"]
    assert get_context().pydantic_messages.get(source_id) == before
    assert get_context().agent_calls.pending(branch_id) is None
    assert get_context().agent_calls.selected_drafts(branch_id) == ()
    assert _post(port, CHAT_PATH, _submit_body(branch_id, "branch-followup", "继续解释"))[0] == 200
    assert len(calls) == 2
    assert any(isinstance(p, TextPart) and p.content == "第一轮结果" for m in calls[-1] for p in m.parts)
    assert get_context().pydantic_messages.get(source_id) == before


@pytest.mark.parametrize("payload", [{}, {"id": "bad", "message_id": "reply"}])
def test_branch_endpoint_validates_request(chat_server, payload):
    assert _post(chat_server["port"], "/api/v1/ai-chat/fork", payload)[0] in {400, 422}


def test_stop_endpoint_interrupts_http_stream(chat_server):
    entered, interrupted = threading.Event(), threading.Event()

    async def model(messages, info):
        yield "部分回复"
        entered.set()
        try:
            await asyncio.sleep(60)
        finally:
            interrupted.set()

    chat_server["model"]["model"] = FunctionModel(stream_function=model)
    conversation_id = "conversation_global_chat_" + "c" * 32
    port = chat_server["port"]
    result = []
    thread = threading.Thread(target=lambda: result.append(_post(port, CHAT_PATH, _submit_body(conversation_id, "stop-http", "开始"))))
    thread.start()
    try:
        assert entered.wait(3)
        status, _, payload = _post(port, "/api/v1/ai-chat/cancel", {"id": conversation_id, "message_id": "stop-http"})
        assert status == 200 and json.loads(payload)["ok"]
        assert interrupted.wait(2)
        thread.join(3)
        assert not thread.is_alive()
        assert b'"type":"abort"' in result[0][2]
    finally:
        get_context().chat_runs.token(conversation_id).cancel()
        thread.join(3)


@pytest.mark.parametrize("payload", [{}, {"id": "bad", "message_id": "u"}, {"id": "conversation_global_chat_" + "c" * 32}])
def test_stop_endpoint_validates_request(chat_server, payload):
    status, _, _ = _post(chat_server["port"], "/api/v1/ai-chat/cancel", payload)
    assert status in {400, 422}
CONVERSATION = "conversation_global_chat_" + "f" * 32


def test_global_chat_profile_supports_bounded_multi_step_task_observation() -> None:
    assert GLOBAL_CHAT_PROFILE.max_model_requests == 80
    assert GLOBAL_CHAT_PROFILE.max_tool_calls == 240


def _submit_body(conversation_id: str, message_id: str, text: str) -> dict:
    return {
        "trigger": "submit-message",
        "id": conversation_id,
        "messages": [
            {
                "id": message_id,
                "role": "user",
                "parts": [{"type": "text", "text": text}],
            }
        ],
    }


def _post(
    port: int,
    path: str,
    payload: dict | None,
    *,
    content_type: str = "application/json",
    raw: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        body = raw if raw is not None else json.dumps(payload or {})
        conn.request(
            "POST",
            path,
            body=body,
            headers={"Content-Type": content_type, **getattr(port, "headers", {}), **(headers or {})},
        )
        response = conn.getresponse()
        data = response.read()
        headers = {key.lower(): value for key, value in response.getheaders()}
        return response.status, headers, data
    finally:
        conn.close()


def _get(port: int, path: str) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("GET", path, headers=getattr(port, "headers", {}))
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


class _BlockingModel(TestModel):
    """在模型请求阶段阻塞，等待 gate 释放；用于稳定复现并发持锁。"""

    def __init__(self, gate: threading.Event, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._gate = gate

    def _request(self, *args: Any, **kwargs: Any) -> Any:
        self._gate.wait(timeout=10)
        return super()._request(*args, **kwargs)


class _FailingModel(TestModel):
    def _request(self, *args: Any, **kwargs: Any) -> Any:
        raise ModelAPIError("test-model", "provider failure")


class _PaymentRequiredModel(TestModel):
    def _request(self, *args: Any, **kwargs: Any) -> Any:
        raise ModelHTTPError(
            402,
            "test-model",
            {"error": {"code": "invalid_request_error", "message": "Insufficient Balance"},
             "request_id": "payment-request-1"},
        )


class _SecretLeakModel(TestModel):
    """Provider 原始错误中带敏感信息，验证 SSE 不泄漏。"""

    def _request(self, *args: Any, **kwargs: Any) -> Any:
        raise ModelAPIError(
            "test-model",
            "upstream rejected api_key=SUPERSECRETKEY value "
            "and token Bearer abcdef123456.xyz and key sk-live0123456789abcdef",
        )


def _patch_chat_model(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """把 GlobalAgentChatService 使用的 factory 换成可替换 TestModel。"""

    from erp_web.services import ai_agent_factory as factory_module
    from erp_web.services import global_agent_chat_service as chat_module

    holder: dict[str, Any] = {
        "model": TestModel(custom_output_text="这是全局对话回复。", call_tools=[])
    }
    real_factory = factory_module.AiAgentFactory

    def binding(*args: Any, **kwargs: Any) -> PydanticModelBinding:
        del args, kwargs
        return PydanticModelBinding(
            model=holder["model"],
            model_settings=ModelSettings(temperature=0),
            model_id="test-model",
            model_name="test-model",
            provider_id="test",
            provider_family="test",
            api_style="chat_completions",
        )

    def patched_factory(**kwargs: Any) -> Any:
        kwargs.setdefault("model_binding_factory", binding)
        return real_factory(**kwargs)

    monkeypatch.setattr(chat_module, "AiAgentFactory", patched_factory)
    return holder


@pytest.fixture()
def chat_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """进程内 ThreadingHTTPServer + 可替换 TestModel 的聊天服务。"""

    holder = _patch_chat_model(monkeypatch)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    headers = authenticated_headers(server)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"port": AuthenticatedPort(server.server_address[1], headers), "headers": headers, "model": holder}
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture()
def chat_service(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """不经过 HTTP 的 VercelAiUiService，便于测试流生命周期与断连。"""

    from erp_web.facades.agent_capability_facade import build_global_chat_toolset
    from erp_web.services.global_agent_chat_service import (
        GlobalAgentChatService,
    )
    from erp_web.services.vercel_ai_ui_service import VercelAiUiService

    holder = _patch_chat_model(monkeypatch)
    context = get_context()
    agent_chat = GlobalAgentChatService(
        app_dir=context.paths.app_dir,
        app_config={},
        message_store=context.pydantic_messages,
        toolset=build_global_chat_toolset(context),
    )
    service = VercelAiUiService(
        chat_service=agent_chat,
        claim_store=context.chat_turn_claims,
        run_registry=context.chat_runs,
        call_store=context.agent_calls,
        approval_session=context.approval_session,
    )
    return {"service": service, "model": holder}


def test_manual_action_runs_directly_and_continues_in_one_chat(chat_server, monkeypatch):
    from erp_web.facades import translation_facade
    from erp_web.services.ai_conversation_scope import current_ai_conversation

    port = chat_server["port"]
    action_id = "conversation_global_chat_" + "7" * 32
    detail_url = f"/api/v1/ai-work/conversations/{action_id}/ui-messages"
    payload = {"target_language": "zh-CN", "content": {"name": "Ventiladores"}}
    headers = {"X-AI-Conversation-ID": action_id}
    release, entered = threading.Event(), threading.Event()
    executed, responses, model_calls = [], [], []

    def translate(language, content):
        executed.append((language, content, current_ai_conversation()))
        entered.set()
        assert release.wait(5)
        return {"name": "风扇"}

    async def model(messages, info):
        model_calls.append((messages, info.instructions))
        yield "翻译结果是风扇。"

    monkeypatch.setattr(translation_facade, "translate_texts", translate)
    chat_server["model"]["model"] = FunctionModel(stream_function=model)
    thread = threading.Thread(
        target=lambda: responses.append(_post(port, "/api/text-translate", payload, headers=headers)),
    )
    thread.start()
    try:
        assert entered.wait(4)
        status, raw = _get(port, detail_url)
        assert status == 200
        active = json.loads(raw)
        assert active["run_active"] and active["history_version"] == 1
        assert active["messages"][0]["metadata"]["manual_ai_action"]["state"] == "running"
        assert active["received_messages"] == []
        assert _post(port, "/api/text-translate", payload, headers=headers)[0] == 409
        assert model_calls == []
    finally:
        release.set()
        thread.join(5)

    assert responses[0][0] == 200
    assert json.loads(responses[0][2])["translations"] == {"name": "风扇"}
    status, raw = _get(port, detail_url)
    finished = json.loads(raw)
    assert status == 200 and finished["history_version"] == 2 and not finished["run_active"]
    receipt = finished["messages"][0]["metadata"]["manual_ai_action"]
    assert receipt["state"] == "completed"
    assert receipt["result"] == {"translations": {"name": "风扇"}, "source": {"name": "Ventiladores"}}
    assert _post(port, "/api/text-translate", payload, headers=headers)[0] == 409
    assert executed == [("zh-CN", payload["content"], action_id)]

    status, _, stream = _post(port, CHAT_PATH, _submit_body(action_id, "manual-followup", "刚才翻译的结果是什么？"))
    assert status == 200 and "翻译结果是风扇。".encode() in stream
    assert any("Ventiladores" in (instructions or "") and "风扇" in (instructions or "")
               for _, instructions in model_calls)
    status, raw = _get(port, detail_url)
    assert status == 200
    continued = json.loads(raw)
    assert continued["messages"][0]["metadata"]["manual_ai_action"] == receipt
    assert continued["messages"][-1]["role"] == "assistant"
    assert [entry.conversation_id for entry in get_context().pydantic_messages.list()] == [action_id]


def test_manual_action_failure_preserves_business_error_and_chat(chat_server, monkeypatch):
    from erp_web.facades import translation_facade
    from erp_web.runtime_units.text_translation import TranslationRequestError

    def failing_translation(*_args):
        raise TranslationRequestError("没有待翻译内容。")

    monkeypatch.setattr(translation_facade, "translate_texts", failing_translation)
    conversation_id = "conversation_global_chat_" + "8" * 32
    port = chat_server["port"]
    status, _, raw = _post(
        port, "/api/text-translate", {"target_language": "zh-CN", "content": {"name": "Fan"}},
        headers={"X-AI-Conversation-ID": conversation_id},
    )
    assert status == 400 and json.loads(raw)["error_code"] == "INVALID_TRANSLATION_REQUEST"
    status, raw = _get(port, f"/api/v1/ai-work/conversations/{conversation_id}/ui-messages")
    receipt = json.loads(raw)["messages"][0]["metadata"]["manual_ai_action"]
    assert status == 200 and receipt["state"] == "failed"
    assert receipt["message"] == "没有待翻译内容。"
    assert receipt["error_code"] == "INVALID_TRANSLATION_REQUEST"


def test_sse_run_streams_official_chunks_and_persists_history(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    status, headers, data = _post(
        port, CHAT_PATH, _submit_body(CONVERSATION, "ui-1", "你好")
    )

    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    assert headers["cache-control"] == "no-store"
    assert headers["connection"] == "close"
    assert headers["x-vercel-ai-ui-message-stream"] == "v1"
    assert "content-length" not in headers

    text = data.decode("utf-8")
    chunks = [
        line[len("data: ") :] for line in text.splitlines() if line.startswith("data: ")
    ]
    assert chunks[0] == '{"type":"start"}'
    assert chunks[-1] == "[DONE]"
    delta_chunks = [
        json.loads(chunk)
        for chunk in chunks
        if chunk != "[DONE]" and json.loads(chunk).get("type") == "text-delta"
    ]
    assert delta_chunks
    streamed_text = "".join(chunk["delta"] for chunk in delta_chunks)
    assert streamed_text == "这是全局对话回复。"
    assert any('"type":"finish"' in chunk for chunk in chunks)
    # 官方 SSE 之外不允许出现项目自定义 JSON 事件。
    assert all(chunk == "[DONE]" or json.loads(chunk).get("type") for chunk in chunks)

    history = get_context().pydantic_messages.get(CONVERSATION)
    assert history is not None
    # 完成后的历史仍可由官方 adapter 恢复。
    assert history.model_messages() == ModelMessagesTypeAdapter.validate_json(
        history.messages_json
    )


def test_sse_run_streams_official_tool_chunks(
    chat_server: dict[str, Any],
) -> None:
    turns = 0

    @discovering_tools('drafts_query')
    async def model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ) -> Any:
        nonlocal turns
        turns += 1
        if turns == 1:
            # 常驻基础读取与控制工具保留，其余业务工具按需发现。
            assert {
                tool.name for tool in agent_info.function_tools
            } == {"search_tools", "run_code", "request_user_input", "drafts_query", "business_data_catalog", "business_data_read"}
            yield {
                0: DeltaThinkingPart(content="先查询当前草稿。"),
                1: python_call(
                    name="drafts_query",
                    json_args='{"scope":"active","view":"summary"}',
                    tool_call_id="draft-query-1",
                ),
            }
            return
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert returns[-1].tool_call_id == "draft-query-1"
        yield "草稿查询完成。"

    chat_server["model"]["model"] = FunctionModel(stream_function=model)
    conversation = "conversation_global_chat_" + "7" * 32

    status, _, data = _post(
        chat_server["port"],
        CHAT_PATH,
        _submit_body(conversation, "tool-stream-1", "查询当前草稿"),
    )

    assert status == 200
    chunks = [
        json.loads(payload)
        for line in data.decode("utf-8").splitlines()
        if line.startswith("data: ") and (payload := line[len("data: ") :]) != "[DONE]"
    ]
    chunk_types = {chunk["type"] for chunk in chunks}
    assert "reasoning-delta" in chunk_types
    assert "tool-input-available" in chunk_types
    assert "tool-output-available" in chunk_types
    assert "finish" in chunk_types


def test_second_turn_reuses_trusted_history_without_duplicates(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]

    def user_texts() -> list[str]:
        history = get_context().pydantic_messages.get(CONVERSATION)
        texts: list[str] = []
        for message in history.model_messages():
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, UserPromptPart) and isinstance(
                        part.content, str
                    ):
                        texts.append(part.content)
        return texts

    _post(port, CHAT_PATH, _submit_body(CONVERSATION, "ui-1", "第一轮"))
    assert user_texts() == ["第一轮"]

    # 客户端即使重复携带历史，服务端也只信任自己的历史并只追加本轮消息。
    _post(port, CHAT_PATH, _submit_body(CONVERSATION, "ui-2", "第二轮"))
    assert user_texts() == ["第一轮", "第二轮"]


def test_resending_same_client_message_id_is_idempotent(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    body = _submit_body(CONVERSATION, "ui-dup", "幂等测试")

    status_first, _, _ = _post(port, CHAT_PATH, body)
    assert status_first == 200
    history_after_first = get_context().pydantic_messages.get(CONVERSATION)

    status_second, _, data_second = _post(port, CHAT_PATH, body)
    payload = json.loads(data_second.decode("utf-8"))
    assert status_second == 409
    assert payload["error_code"] == "AI_CHAT_TURN_ALREADY_ACCEPTED"

    # 重复领取不会再次运行 Agent，也不新增第二个 user turn。
    history_after_second = get_context().pydantic_messages.get(CONVERSATION)
    assert history_after_second.messages_json == history_after_first.messages_json


def test_same_conversation_concurrent_run_is_accepted_in_order(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    gate = threading.Event()
    chat_server["model"]["model"] = _BlockingModel(
        gate, custom_output_text="慢回复。", call_tools=[]
    )

    results: dict[str, tuple[int, bytes]] = {}

    def request(message_id: str) -> None:
        status, _headers, data = _post(
            port, CHAT_PATH, _submit_body(CONVERSATION, message_id, "并发")
        )
        results[message_id] = (status, data)

    first = threading.Thread(target=request, args=("slow-1",))
    first.start()

    # 等待第一个 run 真正持锁（阻塞在模型请求阶段）。
    registry = get_context().chat_runs
    deadline = time.monotonic() + 5
    while not registry.is_active(CONVERSATION):
        if time.monotonic() > deadline:
            gate.set()
            first.join(timeout=5)
            raise AssertionError("第一个 run 未能持锁")
        time.sleep(0.02)

    status_second, _, data_second = _post(
        port, CHAT_PATH, _submit_body(CONVERSATION, "slow-2", "并发第二轮")
    )
    assert status_second == 202
    assert json.loads(data_second.decode("utf-8"))["status"] == "received"

    gate.set()
    first.join(timeout=10)
    assert results["slow-1"][0] == 200
    # 结束后锁被释放，可以再次领取。
    assert registry.is_active(CONVERSATION) is False


def test_distinct_conversations_run_concurrently(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    other = "conversation_global_chat_" + "0" * 32
    results: dict[str, int] = {}

    def request(conversation_id: str) -> None:
        status, _, _ = _post(
            port,
            CHAT_PATH,
            _submit_body(conversation_id, "parallel", "并行"),
        )
        results[conversation_id] = status

    threads = [
        threading.Thread(target=request, args=(CONVERSATION,)),
        threading.Thread(target=request, args=(other,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert results[CONVERSATION] == 200
    assert results[other] == 200


def test_model_failure_streams_official_error_chunk_and_releases_lock(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    chat_server["model"]["model"] = _FailingModel(call_tools=[])

    status, headers, data = _post(
        port, CHAT_PATH, _submit_body(CONVERSATION, "fail-1", "失败测试")
    )

    # 失败也必须走官方 SSE，而不是退回 JSON。
    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    chunks = [
        line[len("data: ") :]
        for line in data.decode("utf-8").splitlines()
        if line.startswith("data: ")
    ]
    error_chunks = [
        json.loads(chunk)
        for chunk in chunks
        if chunk != "[DONE]" and json.loads(chunk).get("type") == "error"
    ]
    assert error_chunks
    assert chunks[-1] == "[DONE]"

    # 失败后锁被释放，可重新领取；claim 记录为 failed。
    assert get_context().chat_runs.is_active(CONVERSATION) is False
    claim = get_context().chat_turn_claims.find_for_conversation(CONVERSATION)
    assert claim is not None
    assert claim.status == "failed"


def test_coroutine_cancellation_lets_producer_complete_and_releases(
    chat_service: dict[str, Any],
) -> None:
    """B-04：producer 生命周期独立于 observer；取消请求协程后仍完成并提交。

    旧实现把 producer 随请求协程一起取消，留下未 await 的协程
    （RuntimeWarning）并把已接受的 run 误记为 cancelled。修复后：
    取消 observer 只停止 SSE 写入，producer 与收尾移交独立任务完成，
    claim 到达 completed，且不再出现 "was never awaited" 警告。
    """

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "4" * 32

    async def slow_final_stream(
        _messages: list[ModelRequest | ModelResponse],
        _agent_info: AgentInfo,
    ) -> Any:
        # 终态在 observer 被取消之后才产生：验证 producer 不被连坐取消。
        await asyncio.sleep(0.05)
        yield "回复在取消之后才完成。"

    chat_service["model"]["model"] = FunctionModel(stream_function=slow_final_stream)
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "cancel-1", "取消测试")).encode(),
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        async def cancel_observer_then_wait_detached() -> None:
            task = asyncio.create_task(run.stream(lambda _chunk: None))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            context = get_context()
            # detached 收尾任务独立继续：等待 claim 终态与 registry 释放。
            for _ in range(1000):
                pending = context.chat_turn_claims.find_for_conversation(conversation)
                if pending is not None and pending.status in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    break
                await asyncio.sleep(0.01)
            for _ in range(1000):
                if not context.chat_runs.is_active(conversation):
                    break
                await asyncio.sleep(0.01)

        asyncio.run(cancel_observer_then_wait_detached())
        gc.collect()
        unawaited = [
            str(item.message)
            for item in caught
            if issubclass(item.category, RuntimeWarning)
            and "never awaited" in str(item.message)
        ]

    context = get_context()
    assert context.chat_runs.is_active(conversation) is False
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    # 已接受的 run 不因 observer 取消而取消：producer 完成后是 completed。
    assert claim.status == "completed"
    # 官方最终历史已保存，可被 /ui-messages 恢复。
    history = context.pydantic_messages.get(conversation)
    assert history is not None
    assert history.model_messages()
    assert unawaited == []


def test_request_loop_closed_immediately_after_cancel_lets_producer_finalize(
    chat_service: dict[str, Any],
) -> None:
    """报告 R-02：路由为每个请求创建一次性 event loop 并立即关闭。

    最小探针窗口：取消请求协程后马上执行 ``shutdown_asyncgens()`` 与
    ``loop.close()``（与 ``ai_chat_routes.handle_chat_run`` 相同的收尾序列）。
    旧实现把 finish_detached 创建在同一个即将关闭的 loop 上，producer 随之被
    取消：claim=failed/claimed、history 不存在、两个 "never awaited" 警告。
    修复后 producer 由进程级 runner 托管：history/link/claim 正常收尾，
    无 RuntimeWarning。
    """

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "a2" + "0" * 30

    async def slow_final_stream(
        _messages: list[ModelRequest | ModelResponse],
        _agent_info: AgentInfo,
    ) -> Any:
        # 终态在请求 loop 关闭之后才产生：producer 必须在 runner 上存活。
        await asyncio.sleep(0.3)
        yield "回复在请求 loop 关闭之后才完成。"

    chat_service["model"]["model"] = FunctionModel(stream_function=slow_final_stream)
    run = service.prepare_run(
        json.dumps(_submit_body(conversation, "loop-close-1", "关闭测试")).encode(),
    )

    received_first_chunk = threading.Event()

    def write_chunk(_chunk: bytes) -> None:
        received_first_chunk.set()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        loop = asyncio.new_event_loop()
        try:

            async def cancel_after_first_chunk() -> None:
                task = loop.create_task(run.stream(write_chunk))
                while not received_first_chunk.is_set():
                    await asyncio.sleep(0.01)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

            loop.run_until_complete(cancel_after_first_chunk())
        finally:
            # 与真实路由完全一致：立即 shutdown_asyncgens + close。
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

        context = get_context()
        # producer 在进程级 runner 上继续：同步轮询 claim 终态。
        deadline = time.monotonic() + 10.0
        claim = context.chat_turn_claims.find_for_conversation(conversation)
        while time.monotonic() < deadline and (
            claim is None or claim.status not in {"completed", "failed", "cancelled"}
        ):
            time.sleep(0.05)
            claim = context.chat_turn_claims.find_for_conversation(conversation)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and context.chat_runs.is_active(conversation):
            time.sleep(0.05)
        gc.collect()
        unawaited = [
            str(item.message)
            for item in caught
            if issubclass(item.category, RuntimeWarning)
            and "never awaited" in str(item.message)
        ]

    assert claim is not None
    # 请求 loop 已关闭，producer 仍在 runner 上完成：claim completed。
    assert claim.status == "completed"
    assert claim.error_code == ""
    history = context.pydantic_messages.get(conversation)
    assert history is not None
    assert history.model_messages()
    assert context.chat_runs.is_active(conversation) is False
    assert unawaited == []


def test_chat_route_closes_pending_async_generators_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from erp_web.http_route_units import ai_chat_routes

    class RunWithPendingResource:
        conversation_id = "conversation_global_chat_" + "5" * 32

        def __init__(self) -> None:
            self.resource_closed = False
            self.resource: Any = None

        def sse_headers(self) -> dict[str, str]:
            return {"Content-Type": "text/event-stream"}

        async def stream(self, _writer: Any) -> None:
            async def resource() -> Any:
                try:
                    yield object()
                finally:
                    self.resource_closed = True

            self.resource = resource()
            await anext(self.resource)

    class HandlerStub:
        path = CHAT_PATH

        def send_sse_headers(self, _headers: dict[str, str]) -> None:
            return None

        def write_sse_chunk(self, _chunk: bytes) -> None:
            return None

    run = RunWithPendingResource()
    monkeypatch.setattr(
        ai_chat_routes,
        "safe_json_body_with_raw",
        lambda _handler: ({}, b"{}"),
    )
    monkeypatch.setattr(
        ai_chat_routes,
        "validate_request_payload",
        lambda _payload, *, endpoint: None,
    )
    monkeypatch.setattr(
        ai_chat_routes.ai_chat_facade,
        "run_chat_stream",
        lambda _raw, **_kwargs: run,
    )

    try:
        ai_chat_routes.handle_chat_run(HandlerStub())  # type: ignore[arg-type]
        assert run.resource_closed is True
    finally:
        if run.resource is not None and not run.resource_closed:
            asyncio.run(run.resource.aclose())


def test_header_write_disconnect_releases_run_lock_and_claim(
    chat_service: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报告 A-02：写 SSE 响应头阶段断开不得遗留永久 run lock。

    探针：registry/claim 已在 prepare_run() 领取，send_sse_headers 抛
    BrokenPipeError 后旧实现永久占用 conversation（后续请求全部
    AI_CHAT_RUN_ACTIVE）。修复后：claim=failed、registry 释放、无 history，
    同一 conversation 可立即重试。
    """

    from erp_web.http_route_units import ai_chat_routes

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "7" * 32

    class BrokenHeaderHandler:
        path = CHAT_PATH

        def send_sse_headers(self, _headers: dict[str, str]) -> None:
            raise BrokenPipeError("client gone before headers")

        def write_sse_chunk(self, _chunk: bytes) -> None:
            return None

    monkeypatch.setattr(
        ai_chat_routes,
        "safe_json_body_with_raw",
        lambda _handler: ({}, b"{}"),
    )
    monkeypatch.setattr(
        ai_chat_routes,
        "validate_request_payload",
        lambda _payload, *, endpoint: None,
    )
    monkeypatch.setattr(
        ai_chat_routes.ai_chat_facade,
        "run_chat_stream",
        lambda _raw, **_kwargs: service.prepare_run(
            json.dumps(_submit_body(conversation, "hdr-1", "响应头断线")).encode(),
        ),
    )

    with pytest.raises(BrokenPipeError):
        ai_chat_routes.handle_chat_run(BrokenHeaderHandler())  # type: ignore[arg-type]

    context = get_context()
    assert context.chat_runs.is_active(conversation) is False
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "claimed"
    assert context.agent_calls.inbox(conversation)
    assert context.pydantic_messages.get(conversation) is None

    # 同一 conversation 可立即重试：不卡在 AI_CHAT_RUN_ACTIVE。
    retry = service.prepare_run(
        json.dumps(_submit_body(conversation, "hdr-2", "断线后重试")).encode(),
    )
    assert retry.conversation_id == conversation
    asyncio.run(retry.stream(lambda _chunk: None))
    final_claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert final_claim is not None
    assert final_claim.status == "completed"


def test_disconnect_does_not_cancel_server_side_run(
    chat_service: dict[str, Any],
) -> None:
    """客户端断线只停止 SSE 写入；Agent run 继续 drain 到终态并保存历史。"""

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "6" * 32
    run = service.prepare_run(
        json.dumps(
            _submit_body(conversation, "dc-after-delta", "保留部分历史")
        ).encode(),
    )
    written: list[bytes] = []

    def disconnect_after_delta(chunk: bytes) -> None:
        written.append(chunk)
        if b'"type":"text-delta"' in chunk:
            raise BrokenPipeError("client gone after delta")

    asyncio.run(run.stream(disconnect_after_delta))

    assert any(b'"type":"text-delta"' in chunk for chunk in written)
    context = get_context()
    assert context.chat_runs.is_active(conversation) is False
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    # 已接受的 run 不因客户端断线取消：drain 完成后是 completed 终态。
    assert claim.status == "completed"
    history = context.pydantic_messages.get(conversation)
    assert history is not None
    messages = history.model_messages()
    # 服务端完整消费后保存的是官方最终历史，不存在中断占位状态。
    assert not any(
        isinstance(message, ModelResponse) and message.state == "interrupted"
        for message in messages
    )
    assert any(isinstance(message, ModelResponse) for message in messages)


def test_disconnect_during_multi_tool_turn_persists_complete_tool_pairs(
    chat_service: dict[str, Any],
) -> None:
    """断线后服务端 drain 完成多工具回合，历史里 call/return 真实成对。"""

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "9" * 32
    turns = 0

    @discovering_tools('drafts_query', 'products_index_query')
    async def two_tool_model(
        _messages: list[ModelRequest | ModelResponse],
        _agent_info: AgentInfo,
    ) -> Any:
        nonlocal turns
        turns += 1
        if turns == 1:
            yield {
                0: python_call(
                    name="drafts_query",
                    json_args='{"scope":"active","view":"summary"}',
                    tool_call_id="disconnect-drafts",
                ),
                1: python_call(
                    name="products_index_query",
                    json_args="{}",
                    tool_call_id="disconnect-products",
                ),
            }
            return
        yield "查询完成。"

    chat_service["model"]["model"] = FunctionModel(stream_function=two_tool_model)
    run = service.prepare_run(
        json.dumps(
            _submit_body(conversation, "multi-tool-disconnect", "查询商品和草稿")
        ).encode(),
    )

    def disconnect_after_first_output(chunk: bytes) -> None:
        if b'"type":"tool-output-available"' in chunk:
            raise BrokenPipeError("disconnect after first tool output")

    asyncio.run(run.stream(disconnect_after_first_output))

    history = get_context().pydantic_messages.get(conversation)
    assert history is not None
    messages = history.model_messages()
    call_ids = [
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]
    return_ids = [
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]

    assert sorted(call for call in call_ids if not call.startswith("discover-")) == ["disconnect-drafts", "disconnect-products"]
    assert sorted(return_ids) == sorted(call_ids)
    claim = get_context().chat_turn_claims.find_for_conversation(conversation)
    assert claim is not None
    assert claim.status == "completed"


def test_business_failure_in_multi_tool_turn_persists_complete_tool_pairs(
    chat_service: dict[str, Any],
) -> None:
    """复现真实对话中的“首个读取成功、第二个商品已删除”失败序列。"""

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "a" * 32
    turns = 0

    @discovering_tools('drafts_query', 'product_read')
    async def partially_failing_model(
        _messages: list[ModelRequest | ModelResponse],
        _agent_info: AgentInfo,
    ) -> Any:
        nonlocal turns
        turns += 1
        if turns == 1:
            yield {
                0: python_call(
                    name="drafts_query",
                    json_args='{"scope":"active","view":"summary"}',
                    tool_call_id="success-before-failure",
                ),
                1: python_call(
                    name="product_read",
                    json_args='{"product_id":"already-deleted"}',
                    tool_call_id="missing-product",
                ),
            }
            return
        yield "已发现商品不存在。"

    chat_service["model"]["model"] = FunctionModel(
        stream_function=partially_failing_model
    )
    run = service.prepare_run(
        json.dumps(
            _submit_body(conversation, "multi-tool-failure", "读取已删除商品")
        ).encode(),
    )

    asyncio.run(run.stream(lambda _chunk: None))

    history = get_context().pydantic_messages.get(conversation)
    assert history is not None
    messages = history.model_messages()
    call_ids = [
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]
    return_ids = [
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]

    assert sorted(call for call in call_ids if not call.startswith("discover-")) == ["missing-product", "success-before-failure"]
    assert sorted(return_ids) == sorted(call_ids)


def test_pre_stream_conversion_failure_finishes_claim_and_releases_lock(
    chat_service: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from erp_web.services.vercel_ai_ui_service import VercelUiProtocolError

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "8" * 32

    def fail_conversion(_message: Any) -> list[Any]:
        raise VercelUiProtocolError(
            400,
            "AI_CHAT_MESSAGE_INVALID",
            "模拟 Adapter 转换失败。",
        )

    monkeypatch.setattr(service, "_load_new_messages", fail_conversion)

    with pytest.raises(VercelUiProtocolError) as caught:
        service.prepare_run(
            json.dumps(
                _submit_body(conversation, "pre-stream-fail", "转换失败")
            ).encode(),
        )

    assert caught.value.code == "AI_CHAT_MESSAGE_INVALID"
    context = get_context()
    assert context.chat_runs.is_active(conversation) is False
    claim = context.chat_turn_claims.find_for_conversation(conversation)
    assert claim is None
    assert context.agent_calls.inbox(conversation) == []


def test_real_socket_receives_incremental_chunks_before_finish(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    gate = threading.Event()

    # 两阶段模型（修复计划 14.6）：先输出第一段文本并实时下发，再在 gate 上
    # 暂停；客户端此时必须已收到首个 text-delta 且尚未收到 finish；放行后
    # 输出剩余文本并完成。
    async def two_phase_model(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ):
        yield "增量"
        await asyncio.to_thread(gate.wait, 10)
        yield "回复。"

    chat_server["model"]["model"] = FunctionModel(stream_function=two_phase_model)
    conversation = "conversation_global_chat_" + "4" * 32
    body = json.dumps(_submit_body(conversation, "sock-1", "增量测试")).encode("utf-8")

    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        request = (
            f"POST {CHAT_PATH} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Cookie: {chat_server['headers']['Cookie']}\r\n"
            f"X-CSRF-Token: {chat_server['headers']['X-CSRF-Token']}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("utf-8") + body
        sock.sendall(request)

        buffer = b""
        # 在 finish 之前（模型仍被 gate 阻塞）就应读到首个真实 text-delta。
        deadline = time.monotonic() + 10
        while b'"type":"text-delta"' not in buffer:
            if time.monotonic() > deadline:
                raise AssertionError("未在 finish 前收到首个 text-delta")
            try:
                piece = sock.recv(4096)
            except socket.timeout:
                continue
            if not piece:
                break
            buffer += piece
        assert b"HTTP/1.0 200" in buffer or b"HTTP/1.1 200" in buffer
        assert b"text/event-stream" in buffer
        assert b'{"type":"start"}' in buffer
        # 此时模型尚未产出完整结果，finish 不应已经出现。
        assert b'"type":"finish"' not in buffer

        # 放行模型并把剩余流读完整，验证增量最终收敛到 [DONE]。
        gate.set()
        deadline = time.monotonic() + 10
        while b"[DONE]" not in buffer:
            if time.monotonic() > deadline:
                raise AssertionError("未在超时前收到 [DONE]")
            try:
                piece = sock.recv(4096)
            except socket.timeout:
                continue
            if not piece:
                break
            buffer += piece
        assert b'"type":"finish"' in buffer
        assert b"[DONE]" in buffer
        streamed_text = "".join(
            json.loads(line[len("data: ") :])["delta"]
            for line in buffer.decode("utf-8").splitlines()
            if line.startswith("data: ")
            and line[len("data: ") :] != "[DONE]"
            and json.loads(line[len("data: ") :]).get("type") == "text-delta"
        )
        assert streamed_text == "增量回复。"
    finally:
        gate.set()
        sock.close()

    # 流正常结束后历史完成持久化。
    deadline = time.monotonic() + 10
    while get_context().pydantic_messages.get(conversation) is None:
        if time.monotonic() > deadline:
            raise AssertionError("流结束后历史未持久化")
        time.sleep(0.02)


def test_provider_secret_does_not_leak_into_sse(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    chat_server["model"]["model"] = _SecretLeakModel(call_tools=[])

    status, headers, data = _post(
        port,
        CHAT_PATH,
        _submit_body(CONVERSATION, "secret-1", "密钥测试"),
    )

    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    text = data.decode("utf-8")
    assert "SUPERSECRETKEY" not in text
    assert "abcdef123456.xyz" not in text
    assert "sk-live0123456789abcdef" not in text
    # 错误仍以官方 error chunk 形式出现。
    assert any(
        json.loads(chunk).get("type") == "error"
        for chunk in (
            line[len("data: ") :]
            for line in text.splitlines()
            if line.startswith("data: ") and line[len("data: ") :] != "[DONE]"
        )
    )
    status, data = _get(
        port, f"/api/v1/ai-work/conversations/{CONVERSATION}/ui-messages",
    )
    assert status == 200
    restored = json.loads(data)["run_error"]
    assert "upstream rejected" in restored["message"]
    history = get_context().pydantic_messages.get(CONVERSATION)
    assert history is not None
    for value in (data.decode(), history.messages_json.decode()):
        assert "SUPERSECRETKEY" not in value
        assert "abcdef123456.xyz" not in value
        assert "sk-live0123456789abcdef" not in value


def test_provider_payment_required_has_stable_sse_and_claim_error(
    chat_server: dict[str, Any],
) -> None:
    conversation = "conversation_global_chat_" + "e" * 32
    message_id = "payment-required-1"
    chat_server["model"]["model"] = _PaymentRequiredModel(call_tools=[])

    status, headers, data = _post(
        chat_server["port"],
        CHAT_PATH,
        _submit_body(conversation, message_id, "测试计费错误"),
    )

    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    chunks = [
        json.loads(line[len("data: ") :])
        for line in data.decode("utf-8").splitlines()
        if line.startswith("data: ") and line[len("data: ") :] != "[DONE]"
    ]
    errors = [item for item in chunks if item.get("type") == "error"]
    assert errors == [
        {
            "type": "error",
            "errorText": "AI Provider 余额不足或计费配置不可用。HTTP 402: Insufficient Balance "
                         "(code=invalid_request_error) (request_id=payment-request-1)",
        }
    ]
    claim = get_context().chat_turn_claims.get(conversation, message_id)
    assert claim is not None
    assert claim.status == "failed"
    assert claim.error_code == "AI_PROVIDER_PAYMENT_REQUIRED"
    expected = {"code": claim.error_code, "message": errors[0]["errorText"]}
    status, data = _get(
        chat_server["port"], f"/api/v1/ai-work/conversations/{conversation}/ui-messages",
    )
    assert status == 200
    assert json.loads(data)["run_error"] == expected
    history = get_context().pydantic_messages.get(conversation)
    assert history is not None
    diagnostic = history.model_messages()[-1].metadata["ai_run_error"]
    assert diagnostic["details"] == {
        "status_code": 402, "code": "invalid_request_error",
        "message": "Insufficient Balance", "request_id": "payment-request-1",
    }

    # 新用户回合成功后不再显示旧错误，也不重放失败输入。
    chat_server["model"]["model"] = TestModel(custom_output_text="查询成功", call_tools=[])
    _post(chat_server["port"], CHAT_PATH, _submit_body(conversation, "payment-fixed", "重试查询"))
    _, data = _get(
        chat_server["port"], f"/api/v1/ai-work/conversations/{conversation}/ui-messages",
    )
    assert json.loads(data)["run_error"] is None


def test_model_configuration_failure_preserves_input_and_safe_cause(chat_service):
    from erp_web.services.ai_model_factory import AiModelFactoryError

    service = chat_service["service"]
    conversation = "conversation_global_chat_" + "d" * 32

    def invalid_binding(*args, **kwargs):
        raise AiModelFactoryError("所选模型缺少 API Key：api_key=CONFIGSECRET")

    service.chat_service.factory.model_binding_factory = invalid_binding
    run = service.prepare_run(json.dumps(
        _submit_body(conversation, "config-failed", "查询商品"),
    ).encode())
    chunks = []
    asyncio.run(run._run_and_finalize(chunks.append))
    errors = [json.loads(line[6:]) for line in "".join(chunks).splitlines()
              if line.startswith("data: ") and line[6:] != "[DONE]"
              and json.loads(line[6:]).get("type") == "error"]
    detail = service.dump_ui_messages(conversation)
    assert detail["run_error"] == {
        "code": "AI_MODEL_CONFIGURATION_INVALID",
        "message": errors[0]["errorText"],
    }
    assert "所选模型缺少 API Key" in detail["run_error"]["message"]
    assert detail["messages"][0]["parts"][0]["text"] == "查询商品"
    assert "CONFIGSECRET" not in json.dumps(detail)
    assert service.call_store.inbox(conversation) == []
    assert service.claim_store.get(conversation, "config-failed").status == "failed"


def test_old_payment_failure_displays_known_cause_without_provider_text(chat_service):
    service = chat_service["service"]
    claim = service.claim_store.claim_turn(
        conversation_id=CONVERSATION, client_message_id="old-payment",
        profile_id="global.chat",
    )
    service.claim_store.finish_turn(claim.claim_id, status="failed",
                                    error_code="AI_PROVIDER_PAYMENT_REQUIRED")
    error = service.dump_ui_messages(CONVERSATION)["run_error"]
    assert "HTTP 402" in error["message"]
    assert "余额不足" in error["message"]


@pytest.mark.parametrize("status,code", [(401, "invalid_api_key"), (429, "rate_limit_exceeded")])
def test_http_error_survives_service_recreation_without_leaking_credentials(chat_service, status, code):
    from erp_web.services.vercel_ai_ui_service import VercelAiUiService

    class HttpFailureModel(TestModel):
        def _request(self, *args, **kwargs):
            raise ModelHTTPError(status, "test-model", {"error": {
                "code": code,
                "message": "Provider rejected; api_key=HTTPSECRET; Bearer abcdef123456.xyz",
            }, "request_id": "request-safe"})

    service = chat_service["service"]
    chat_service["model"]["model"] = HttpFailureModel(call_tools=[])
    run = service.prepare_run(json.dumps(_submit_body(CONVERSATION, "http-failed", "查询")).encode())
    chunks = []
    asyncio.run(run._run_and_finalize(chunks.append))
    errors = [json.loads(line[6:]) for line in "".join(chunks).splitlines()
              if line.startswith("data: ") and line[6:] != "[DONE]"
              and json.loads(line[6:]).get("type") == "error"]
    recreated = VercelAiUiService(
        chat_service=service.chat_service, claim_store=service.claim_store,
        run_registry=service.run_registry, call_store=service.call_store,
    )
    detail = recreated.dump_ui_messages(CONVERSATION)
    assert detail["run_error"] == {"code": code, "message": errors[0]["errorText"]}
    assert f"HTTP {status}" in detail["run_error"]["message"]
    assert "request-safe" in detail["run_error"]["message"]
    history = service.chat_service.message_store.get(CONVERSATION)
    for value in ("".join(chunks), json.dumps(detail), history.messages_json.decode()):
        assert "HTTPSECRET" not in value
        assert "abcdef123456.xyz" not in value


@pytest.mark.parametrize(
    "body,status_code,error_code",
    [
        (
            {
                "trigger": "regenerate-message",
                "id": CONVERSATION,
                "messages": [
                    {
                        "id": "a",
                        "role": "user",
                        "parts": [{"type": "text", "text": "重新生成"}],
                    }
                ],
            },
            400,
            "AI_CHAT_TRIGGER_UNSUPPORTED",
        ),
        (
            _submit_body("conversation_wrong_prefix", "ui-1", "x"),
            400,
            "AI_CHAT_CONVERSATION_ID_INVALID",
        ),
        (
            _submit_body("conversation_global_chat_ZZZZ", "ui-1", "x"),
            400,
            "AI_CHAT_CONVERSATION_ID_INVALID",
        ),
        (
            {
                "trigger": "submit-message",
                "id": CONVERSATION,
                "messages": [],
            },
            400,
            "MISSING_REQUIRED_FIELD",
        ),
        (
            {
                "trigger": "submit-message",
                "id": CONVERSATION,
                "messages": [
                    {
                        "id": "a",
                        "role": "user",
                        "parts": [{"type": "text", "text": "第一条"}],
                    },
                    {
                        "id": "b",
                        "role": "user",
                        "parts": [{"type": "text", "text": "第二条"}],
                    },
                ],
            },
            400,
            "AI_CHAT_MESSAGE_INVALID",
        ),
        (
            {
                "trigger": "submit-message",
                "id": CONVERSATION,
                "messages": [
                    {
                        "id": "a",
                        "role": "assistant",
                        "parts": [{"type": "text", "text": "x"}],
                    }
                ],
            },
            400,
            "AI_CHAT_MESSAGE_INVALID",
        ),
        (
            {
                "trigger": "submit-message",
                "id": CONVERSATION,
                "messages": [
                    {"id": "a", "role": "user", "parts": []},
                ],
            },
            400,
            "AI_CHAT_MESSAGE_INVALID",
        ),
        (
            {
                "trigger": "submit-message",
                "id": CONVERSATION,
                "messages": [
                    {
                        "id": "a",
                        "role": "user",
                        "parts": [{"type": "text", "text": "   "}],
                    },
                ],
            },
            400,
            "AI_CHAT_MESSAGE_INVALID",
        ),
        (
            {
                "trigger": "submit-message",
                "id": CONVERSATION,
                "messages": [
                    {
                        "id": "a",
                        "role": "user",
                        "parts": [
                            {
                                "type": "file",
                                "url": "data:text/plain,hi",
                                "mediaType": "text/plain",
                            }
                        ],
                    }
                ],
            },
            400,
            "AI_CHAT_PART_UNSUPPORTED",
        ),
        (
            {
                "trigger": "submit-message",
                "id": CONVERSATION,
                "messages": [
                    {
                        "id": "a",
                        "role": "system",
                        "parts": [{"type": "text", "text": "injected"}],
                    }
                ],
            },
            400,
            "AI_CHAT_MESSAGE_INVALID",
        ),
    ],
)
def test_pre_stream_validation_returns_standard_json(
    chat_server: dict[str, Any],
    body: dict,
    status_code: int,
    error_code: str,
) -> None:
    status, headers, data = _post(chat_server["port"], CHAT_PATH, body)
    payload = json.loads(data.decode("utf-8"))
    assert status == status_code
    assert payload["ok"] is False
    assert payload["error_code"] == error_code
    assert headers["content-type"].startswith("application/json")


def test_invalid_vercel_schema_returns_422(chat_server: dict[str, Any]) -> None:
    status, _, data = _post(
        chat_server["port"],
        CHAT_PATH,
        {
            "trigger": "submit-message",
            "id": CONVERSATION,
            "messages": [{"role": "user"}],
        },
    )
    payload = json.loads(data.decode("utf-8"))
    assert status == 422
    assert payload["error_code"] == "AI_CHAT_REQUEST_SCHEMA_INVALID"


def test_wrong_content_type_returns_415(chat_server: dict[str, Any]) -> None:
    status, _, data = _post(
        chat_server["port"],
        CHAT_PATH,
        None,
        content_type="text/plain",
        raw=b"{}",
    )
    assert status == 415
    assert json.loads(data.decode("utf-8"))["error_code"] == "UNSUPPORTED_CONTENT_TYPE"


def test_invalid_json_returns_400(chat_server: dict[str, Any]) -> None:
    status, _, data = _post(
        chat_server["port"],
        CHAT_PATH,
        None,
        raw=b"{not-json",
    )
    assert status == 400
    assert json.loads(data.decode("utf-8"))["error_code"] == "INVALID_JSON"


def test_oversized_body_returns_413(
    chat_server: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import erp_web.http_request as http_request

    monkeypatch.setattr(http_request, "MAX_JSON_BODY_BYTES", 64)
    status, _, data = _post(
        chat_server["port"],
        CHAT_PATH,
        _submit_body(CONVERSATION, "big", "x" * 512),
    )
    assert status == 413
    assert json.loads(data.decode("utf-8"))["error_code"] == "REQUEST_BODY_TOO_LARGE"


def test_existing_history_without_claim_is_rejected(
    chat_server: dict[str, Any],
) -> None:
    # 预置一份合法 Pydantic 历史，但没有 global.chat claim 归属。
    foreign = "conversation_global_chat_" + "9" * 32
    history = [
        ModelRequest(parts=[UserPromptPart(content="旧消息")]),
        ModelResponse(parts=[TextPart(content="旧回复")]),
    ]
    get_context().pydantic_messages.save(foreign, history)

    status, _, data = _post(
        chat_server["port"],
        CHAT_PATH,
        _submit_body(foreign, "ui-1", "尝试继续"),
    )
    payload = json.loads(data.decode("utf-8"))
    assert status == 409
    assert payload["error_code"] == "AI_CHAT_CONVERSATION_UNOWNED"


def test_non_global_chat_history_cannot_continue(
    chat_server: dict[str, Any],
) -> None:
    # 类目匹配等其他 Agent 的 conversation 即使历史合法，也不允许继续运行。
    other = "conversation_" + "c" * 32
    history = [
        ModelRequest(parts=[UserPromptPart(content="其他 Agent 的消息")]),
        ModelResponse(parts=[TextPart(content="其他 Agent 的回复")]),
    ]
    get_context().pydantic_messages.save(other, history)

    # ID 前缀不满足 global chat 严格格式，直接在入口拒绝。
    status, _, data = _post(
        chat_server["port"],
        CHAT_PATH,
        _submit_body(other, "ui-1", "尝试继续"),
    )
    payload = json.loads(data.decode("utf-8"))
    assert status == 400
    assert payload["error_code"] == "AI_CHAT_CONVERSATION_ID_INVALID"


def test_ui_messages_derives_official_ui_messages(
    chat_server: dict[str, Any],
) -> None:
    port = chat_server["port"]
    _post(port, CHAT_PATH, _submit_body(CONVERSATION, "ui-1", "你好"))

    status, data = _get(
        port, f"/api/v1/ai-work/conversations/{CONVERSATION}/ui-messages"
    )
    payload = json.loads(data.decode("utf-8"))
    assert status == 200
    assert payload["ok"] is True
    assert payload["conversation_id"] == CONVERSATION
    messages = payload["messages"]
    assert messages
    roles = {message["role"] for message in messages}
    assert "user" in roles
    assert "assistant" in roles
    # UI 消息使用官方 camelCase alias。
    for message in messages:
        for part in message["parts"]:
            assert "type" in part


def test_ui_messages_missing_conversation_returns_404(
    chat_server: dict[str, Any],
) -> None:
    missing = "conversation_global_chat_" + "1" * 32
    status, data = _get(
        chat_server["port"],
        f"/api/v1/ai-work/conversations/{missing}/ui-messages",
    )
    payload = json.loads(data.decode("utf-8"))
    assert status == 404
    assert payload["error_code"] == "PYDANTIC_MESSAGE_HISTORY_NOT_FOUND"


def test_ui_messages_renders_text_reasoning_and_tool_parts(
    chat_server: dict[str, Any],
) -> None:
    # 预置包含 text、reasoning、tool call/result 的历史。
    rich = "conversation_global_chat_" + "2" * 32
    history = [
        ModelRequest(parts=[UserPromptPart(content="查询草稿")]),
        ModelResponse(
            parts=[
                ThinkingPart(content="先调用 drafts_query。"),
                ToolCallPart(
                    tool_name="drafts_query",
                    args={"query": {}},
                    tool_call_id="call_1",
                ),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="drafts_query",
                    content={"total": 0, "items": []},
                    tool_call_id="call_1",
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="当前没有草稿。")]),
    ]
    get_context().pydantic_messages.save(rich, history)

    status, data = _get(
        chat_server["port"],
        f"/api/v1/ai-work/conversations/{rich}/ui-messages",
    )
    payload = json.loads(data.decode("utf-8"))
    assert status == 200
    part_types = {
        part["type"] for message in payload["messages"] for part in message["parts"]
    }
    assert "text" in part_types
    assert "reasoning" in part_types
    assert any(part_type.startswith("tool-") for part_type in part_types)


def test_safe_json_body_with_raw_reads_body_once() -> None:
    import io

    from erp_web.http_request import safe_json_body_with_raw

    payload = {"trigger": "submit-message", "id": "x", "messages": []}
    raw = json.dumps(payload).encode("utf-8")

    class FakeHandler:
        def __init__(self) -> None:
            self.headers = {
                "Content-Length": str(len(raw)),
                "Content-Type": "application/json",
            }
            self.rfile = io.BytesIO(raw)

        def read_tracking(self) -> None:
            pass

    handler = FakeHandler()
    parsed, raw_out = safe_json_body_with_raw(handler)
    assert parsed == payload
    assert raw_out == raw
    # 单次读取后 socket 流已耗尽，二次读取返回空。
    assert handler.rfile.read() == b""
    assert VERCEL_SDK_VERSION == 7


def test_chat_images_reach_model_and_can_be_saved_by_reference(chat_service, monkeypatch):
    """用受控模型核对真实像素、附件顺序、恢复后的引用及商品写回。"""
    import base64
    import io
    from PIL import Image
    from pydantic_ai import BinaryContent
    from erp_web.runtime_units.image_capabilities import (
        ImageCapabilityScope, chat_images_save_to_product, product_image_read,
    )
    from erp_web.schemas.image_capabilities import ChatImagesSaveRequest, ProductImageReadRequest
    from erp_web.schemas.ai_trace import AiExecutionContext
    from erp_web.services.capability_errors import BusinessCapabilityError
    from erp_web.services import ai_direct_request_service
    from erp_web.services.vercel_ai_ui_service import VercelUiProtocolError
    from tests.test_domain_write_capabilities import _seed_product

    service = chat_service["service"]
    context = get_context()
    scope = ImageCapabilityScope(context)
    product = _seed_product("image-chat-fan")
    config = {"ai_models": [{"id": "vision", "model": "vision", "connection_type": "api",
                             "capabilities": ["chat", "tool_calling", "image_understanding"]}]}
    body = _submit_body(CONVERSATION, "photo-1", "读取第二张图")
    originals = []
    for color in ("red", "blue"):
        buffer = io.BytesIO()
        Image.new("RGB", (11, 8), color).save(buffer, "PNG")
        originals.append(buffer.getvalue())
        body["messages"][0]["parts"].append({
            "type": "file", "mediaType": "image/png",
            "url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
        })
    with pytest.raises(VercelUiProtocolError, match="图片理解"):
        service.prepare_run(json.dumps(body).encode())
    service.chat_service.app_config = config
    monkeypatch.setattr(context.config, "load_app_config", lambda: config)
    seen = []

    async def model(messages, _info):
        for message in messages:
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, list):
                    seen.extend(part.content)
        yield "已读图"

    chat_service["model"]["model"] = FunctionModel(stream_function=model)
    run = service.prepare_run(json.dumps(body).encode())
    assert "［图片］" in service.dump_ui_messages(CONVERSATION)["received_messages"][0]["text"]
    asyncio.run(run.stream(lambda _: None))
    assert [part.data for part in seen if isinstance(part, BinaryContent)] == originals
    attachment_ids = [item.identifier for item in seen if isinstance(item, BinaryContent)]
    assert all(f"对话图片 ID：{key}" in seen for key in attachment_ids)
    restored = service.dump_ui_messages(CONVERSATION)["messages"][0]["parts"]
    assert [p["url"] for p in restored if p["type"] == "file"] == [
        item["url"] for item in body["messages"][0]["parts"][1:]
    ]
    execution = AiExecutionContext.create(budget_profile="test", timeout_seconds=60,
                                          business_scope={"conversation_id": CONVERSATION})
    save = ChatImagesSaveRequest(product_id=product["product_id"], attachment_ids=[attachment_ids[1]])
    with pytest.raises(BusinessCapabilityError, match="没有对应"):
        chat_images_save_to_product(save.model_copy(update={"attachment_ids": ["other"]}), scope, execution)
    @discovering_tools("chat_images_save_to_product")
    async def save_model(messages, _info):
        if any(isinstance(part, ToolReturnPart) and part.tool_name == "chat_images_save_to_product"
               for message in messages for part in message.parts):
            yield "已保存"
        else:
            yield {0: DeltaToolCall(name="chat_images_save_to_product", json_args=save.model_dump_json(),
                                    tool_call_id="save-photo")}

    chat_service["model"]["model"] = FunctionModel(stream_function=save_model)
    followup = service.prepare_run(json.dumps(_submit_body(CONVERSATION, "save-2", "把第二张图加入商品图片池")).encode())
    asyncio.run(followup.stream(lambda _: None))
    receipt = context.agent_calls.receipt(CONVERSATION, "save-photo")
    assert receipt and json.loads(receipt["output_json"])["asset_ids"] == [attachment_ids[1]]
    result = chat_images_save_to_product(save, scope, execution)
    saved_product = context.products.load_product_from_index(product["product_id"])
    pool = saved_product["source"]["image_pool"]
    assert len(pool) == 2 and pool[0]["is_main"]
    assert (context.paths.app_dir / pool[1]["path"]).read_bytes() == originals[1]
    listing = ProductImageReadRequest(product_id=product["product_id"])
    assert [row.asset_id for row in product_image_read(listing, scope, execution).images] == [row["id"] for row in pool]
    saved_product["sku_items"] = [
        {"id": sku_id, "name": sku_id, "image_asset_id": pool[index]["id"]}
        for sku_id, index in [("a", 0), ("b", 1), ("c", 1)]
    ]
    saved_product["source"]["image_pool"].extend([
        {**pool[0], "id": "generated", "url": "https://cdn.example.com/generated.jpg", "origin": "ai_generated"},
        {**pool[0], "id": "derivative", "url": "https://cdn.example.com/derived.jpg", "derived_from_id": pool[0]["id"]},
    ])
    context.products.save_product(saved_product)
    for asset_id in ("generated", "derivative"):
        with pytest.raises(BusinessCapabilityError, match="未找到"):
            product_image_read(listing.model_copy(update={"asset_id": asset_id}), scope, execution)
    all_images = product_image_read(listing.model_copy(update={"source_only": False}), scope, execution).images
    assert {row.asset_id for row in all_images} == {pool[0]["id"], pool[1]["id"], "generated", "derivative"}
    listed = product_image_read(listing, scope, execution).images
    assert [(row.sku, row.sku_ids) for row in listed] == [("a", ["a"]), ("", ["b", "c"])]

    def read(messages, _info):
        assert messages[0].parts[0].content[1].data == originals[1]
        return ModelResponse(parts=[TextPart("蓝色")])

    def visual_binding(model, **kwargs):
        assert model["id"] == "vision" and "image_understanding" in kwargs["required_capabilities"]
        return PydanticModelBinding(model=FunctionModel(read), model_settings={}, model_id="vision",
                                   model_name="vision", provider_id="test", provider_family="test", api_style="test")

    monkeypatch.setattr(ai_direct_request_service, "create_pydantic_model_binding", visual_binding)
    assert product_image_read(listing.model_copy(update={"asset_id": result.asset_ids[0]}), scope, execution).content == "蓝色"
    with pytest.raises(BusinessCapabilityError, match="未找到"):
        product_image_read(listing.model_copy(update={"asset_id": "不属于此商品"}), scope, execution)
    body["messages"][0]["parts"][1]["url"] = "https://localhost/secret"
    with pytest.raises(VercelUiProtocolError, match="本地图片"):
        service.prepare_run(json.dumps(body).encode())
    body["messages"][0]["parts"][1]["url"] = "data:image/png;base64,SGVsbG8="
    with pytest.raises(VercelUiProtocolError, match="损坏"):
        service.prepare_run(json.dumps(body).encode())


def test_queue_buttons_operate_on_running_http_conversation(chat_server, monkeypatch):
    from erp_web.services import chat_queue_service

    entered, release = threading.Event(), threading.Event()

    async def model(messages, info):
        if not entered.is_set():
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.02)
            yield "原任务完成"
        else:
            yield "原任务仍在进行"

    monkeypatch.setattr(chat_queue_service, "chat_structured",
                        lambda *args, **kwargs: chat_queue_service.TaskRelation(superseded=[]))
    chat_server["model"]["model"] = FunctionModel(stream_function=model)
    port = chat_server["port"]
    thread = threading.Thread(target=lambda: _post(port, CHAT_PATH, _submit_body(CONVERSATION, "first", "生成图片")))
    thread.start()
    try:
        assert entered.wait(5)
        for message_id, text in [("drop", "删除这条"), ("check", "图片做好了吗")]:
            assert _post(port, CHAT_PATH, {**_submit_body(CONVERSATION, message_id, text), "queue_if_busy": True})[0] == 202
        path = "/api/v1/ai-chat/queue"
        assert _post(port, path, {"id": CONVERSATION, "message_id": "drop", "action": "delete"})[0] == 200
        assert _post(port, path, {"id": CONVERSATION, "message_id": "check", "action": "send"})[0] == 200
        deadline = time.monotonic() + 4
        while True:
            status, raw = _get(port, f"/api/v1/ai-work/conversations/{CONVERSATION}/ui-messages")
            assert status == 200
            view = json.loads(raw)
            reply = view["parallel_replies"][0]
            if reply["run_status"] == "completed":
                break
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert view["run_active"]
        assert view["received_messages"] == []
        assert reply["messages"][-1]["parts"][-1]["text"] == "原任务仍在进行"
        assert _post(port, path, {"id": CONVERSATION, "message_id": "check", "action": "send"})[0] == 409
    finally:
        release.set()
        thread.join(5)
