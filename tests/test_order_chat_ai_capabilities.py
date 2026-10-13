"""买家聊天 AI 接入：真实领域服务配合网络替身，不向真实买家发送。"""

import asyncio
import json

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from erp_web.context import get_context
from erp_web.facades import order_chat_facade
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.runtime_units.order_chat_capabilities import (
    OrderChatCapabilityScope, _send_snapshot, order_buyer_chat_read, order_buyer_chat_send,
)
from erp_web.schemas.ai_tools import AiToolExecutionError
from erp_web.schemas.order_capabilities import OrderChatSendRequest, OrderRequest
from erp_web.schemas.platform_errors import PublishAdapterError
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.external_request_context import request_context, request_operation
from tests.approval_support import _approved_execution, _execution
from tests.test_order_chat import domain, result, chat, message, CONTEXT
from tests.test_native_agent_integration import CONVERSATION, body, service as chat_service


def scope(domain):
    return OrderChatCapabilityScope(service=lambda: domain.service)


def payload(domain, text="Здравствуйте! Уточните, пожалуйста, вариант товара."):
    return OrderChatSendRequest(order_id=domain.order.id, message=text)


def test_catalog_exposes_read_and_approved_send_without_model_credentials():
    bindings = build_global_chat_toolset().bindings
    read, send = (bindings[name].definition for name in ("order_buyer_chat_read", "order_buyer_chat_send"))
    assert read.side_effect == "none" and not read.approval_required
    assert send.side_effect == "write" and send.approval_required and send.recovery_policy == "manual"
    assert send.required_permission == "order.message"
    assert set(send.input_schema["properties"]) == {"order_id", "message"}
    assert send.input_schema["properties"]["message"]["maxLength"] == 4096


def test_history_reuses_pagination_and_never_sends(domain):
    domain.request.side_effect = [result(chats=[chat()]),
        result(context=CONTEXT, messages=[message(2)], paging={"nextPageToken": "next"}),
        result(context=CONTEXT, messages=[message(1)])]
    history = order_buyer_chat_read(OrderRequest(order_id=domain.order.id), scope(domain))
    assert history.order_id == domain.order.id and history.checked_at
    assert [row["id"] for row in history.data["messages"]] == ["1", "2"]
    assert all(not call.args[1].endswith(("/new", "/message")) for call in domain.request.call_args_list)


def test_send_requires_approval_and_preserves_exact_body_without_readback(domain):
    request = payload(domain, "  您好\n请确认规格。  ")
    frozen = _send_snapshot(request, scope(domain))
    assert request.message in frozen.summary
    assert frozen.canonical_payload["recipient"]["business_id"] == "8"
    assert "secret" not in frozen.model_dump_json()
    domain.request.assert_not_called()
    with pytest.raises(AiToolExecutionError, match="审批"):
        order_buyer_chat_send(request, scope(domain), _execution())
    domain.request.assert_not_called()
    domain.request.side_effect = [result(chatId=17), {"status": "OK"}]
    receipt = order_buyer_chat_send(request, scope(domain), _approved_execution(frozen, "order_buyer_chat_send"))
    assert receipt.order_id == domain.order.id and receipt.data == {"ok": True, "chat_id": "17"}
    assert len(domain.request.call_args_list) == 2
    assert domain.request.call_args_list[1].args[3] == {"message": request.message}


@pytest.mark.parametrize("change", ["message", "order", "campaign", "business", "state"])
def test_changed_recipient_or_body_invalidates_approval(domain, change):
    request = payload(domain)
    approved = _approved_execution(_send_snapshot(request, scope(domain)), "order_buyer_chat_send")
    if change == "message":
        request = request.model_copy(update={"message": "另一段未经批准的正文"})
    elif change == "order":
        request = request.model_copy(update={"order_id": "yandex:4:FBS:456"})
        domain.reader.return_value = domain.order.model_copy(update={"id": request.order_id, "order_id": "456"})
    elif change in {"campaign", "business"}:
        domain.config["yandex"][change + "_id"] = "9"
    else:
        domain.reader.return_value = domain.order.model_copy(update={"state": "cancelled", "status": "CANCELLED"})
    with pytest.raises((AiToolExecutionError, BusinessCapabilityError)):
        order_buyer_chat_send(request, scope(domain), approved)
    domain.request.assert_not_called()


def test_long_approval_keeps_complete_message_in_native_parameters(domain):
    request = payload(domain, "正文\n" * 1024)
    frozen = _send_snapshot(request, scope(domain))
    assert "查看操作参数" in frozen.summary and len(frozen.summary) <= 2000
    assert frozen.canonical_payload["request"]["message"] == request.message
    domain.request.side_effect = [result(chatId=17), {"status": "OK"}]
    order_buyer_chat_send(request, scope(domain), _approved_execution(frozen, "order_buyer_chat_send"))
    assert domain.request.call_args_list[-1].args[3]["message"] == request.message


@pytest.mark.parametrize("text", ["", " \n", "字" * 4097])
def test_invalid_body_is_rejected_before_approval(domain, text):
    with pytest.raises(ValidationError):
        payload(domain, text)
    domain.request.assert_not_called()


@pytest.mark.parametrize("failure", [
    PublishAdapterError("EXTERNAL_WRITE_OUTCOME_UNKNOWN", "回执丢失"),
    PublishAdapterError("YANDEX_INVALID_RESPONSE", "平台回执无法解析"),
    PublishAdapterError("YANDEX_NETWORK_TIMEOUT", "平台请求超时"),
    PublishAdapterError("YANDEX_SERVER_ERROR", "平台服务错误", details={"http_status": 500}),
])
def test_unknown_send_is_explicit_and_not_retried(domain, failure):
    request = payload(domain)
    approved = _approved_execution(_send_snapshot(request, scope(domain)), "order_buyer_chat_send")
    domain.request.side_effect = [result(chatId=17), failure]
    with pytest.raises(BusinessCapabilityError) as caught:
        order_buyer_chat_send(request, scope(domain), approved)
    assert caught.value.code == "ORDER_CHAT_SEND_OUTCOME_UNKNOWN"
    assert caught.value.details["outcome_unknown"] and not caught.value.retryable
    assert domain.request.call_count == 2


def test_explicit_platform_rejection_is_not_reported_as_unknown(domain):
    request = payload(domain)
    approved = _approved_execution(_send_snapshot(request, scope(domain)), "order_buyer_chat_send")
    domain.request.side_effect = PublishAdapterError("DENIED", "请开通买家沟通权限", details={"http_status": 403})
    with pytest.raises(BusinessCapabilityError) as caught:
        order_buyer_chat_send(request, scope(domain), approved)
    assert caught.value.code == "DENIED" and not caught.value.details
    assert domain.request.call_count == 1


def test_chat_network_inherits_agent_deadline_and_cancellation(domain):
    checks = []
    def network(*args, **kwargs):
        context = request_context("https://example.com")
        assert context.deadline_at == 100
        context.cancellation_check()
        return result(chats=[])
    domain.request.side_effect = network
    with request_operation("ai_tool", deadline_at=100, cancellation_check=lambda: checks.append("检查取消")):
        order_buyer_chat_read(OrderRequest(order_id=domain.order.id), scope(domain))
    assert checks == ["检查取消"]


@pytest.mark.parametrize("entry", ["direct", "python"])
def test_native_agent_reads_buyer_messages_with_real_binding(domain, monkeypatch, tmp_path, entry):
    monkeypatch.setattr(order_chat_facade, "create_service", lambda context: domain.service)
    domain.request.side_effect = [result(chats=[chat()]), result(context=CONTEXT, messages=[message(1)])]
    outputs = []
    async def model(messages, info):
        returned = [part for msg in messages for part in msg.parts if isinstance(part, ToolReturnPart)]
        if returned:
            outputs.extend(part.content for part in returned)
            yield "买家消息已读取"
        else:
            args = {"order_id": domain.order.id}
            if entry == "python":
                args = {"code": f"r = await order_buyer_chat_read(order_id={domain.order.id!r})\nr"}
            yield {0: DeltaToolCall(name="run_code" if entry == "python" else "order_buyer_chat_read",
                tool_call_id="read-chat", json_args=json.dumps(args))}
    ui = chat_service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(get_context()))
    asyncio.run(ui.prepare_run(body("查看这笔订单的买家消息")).stream(lambda _: None))
    assert len(outputs) == 1 and outputs[0]["data"]["messages"][0]["text"] == "消息 1"
    assert domain.request.call_count == 2


@pytest.mark.parametrize("approved", [True, False])
def test_native_approval_sends_only_after_decision(domain, monkeypatch, tmp_path, approved):
    monkeypatch.setattr(order_chat_facade, "create_service", lambda context: domain.service)
    domain.request.side_effect = [result(chatId=17), {"status": "OK"}]
    request, outputs = payload(domain, "请确认 EE010 规格。"), []
    async def model(messages, info):
        returned = [part for msg in messages for part in msg.parts if isinstance(part, ToolReturnPart)]
        if returned:
            outputs.extend(part.content for part in returned)
            yield "已处理本次沟通请求"
        else:
            yield {0: DeltaToolCall(name="order_buyer_chat_send", tool_call_id="send-chat", json_args=request.model_dump_json())}
    ui = chat_service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(get_context()))
    asyncio.run(ui.prepare_run(body("把这段消息发给该订单的买家")).stream(lambda _: None))
    domain.request.assert_not_called()
    parts = [part for msg in ui.dump_ui_messages(CONVERSATION)["messages"] for part in msg["parts"]
             if part.get("state") == "approval-requested"]
    assert len(parts) == 1 and parts[0]["input"]["message"] == request.message
    parts[0]["state"] = "approval-responded"
    parts[0]["approval"]["approved"] = approved
    decision = json.dumps({"id": CONVERSATION, "trigger": "submit-message",
        "messages": [{"id": "decision", "role": "assistant", "parts": parts}]}).encode()
    asyncio.run(ui.prepare_run(decision, approval_token="test-token").stream(lambda _: None))
    assert domain.request.call_count == (2 if approved else 0)
    assert outputs
    if approved:
        assert outputs[-1]["data"] == {"ok": True, "chat_id": "17"}


def test_native_unknown_receipt_blocks_new_call_id_replay(domain, monkeypatch, tmp_path):
    monkeypatch.setattr(order_chat_facade, "create_service", lambda context: domain.service)
    domain.request.side_effect = [result(chatId=17), TimeoutError("响应超时")]
    request, outputs, runs = payload(domain), [], []
    async def model(messages, info):
        runs.append(1)
        returned = [part for msg in messages for part in msg.parts if isinstance(part, ToolReturnPart)]
        if len(runs) <= 2:
            yield {0: DeltaToolCall(name="order_buyer_chat_send", tool_call_id=f"send-{len(runs)}", json_args=request.model_dump_json())}
        else:
            outputs.extend(part.content for part in returned)
            yield "发送结果未知，需要核对会话"
    ui = chat_service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(get_context()))
    ui.chat_service.approval_mode_reader = lambda: "full"
    asyncio.run(ui.prepare_run(body("发送消息给该订单的买家")).stream(lambda _: None))
    assert domain.request.call_count == 2 and len(outputs) == 2
    assert all(value["error"]["details"]["outcome_unknown"] for value in outputs)
