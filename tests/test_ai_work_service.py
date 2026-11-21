from __future__ import annotations

import urllib.parse
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart,
)
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from erp_web.context import get_context
from erp_web.http_route_units import ai_work_routes
from erp_web.services.vercel_ai_ui_service import VERCEL_SDK_VERSION
from erp_web.facades.ai_chat_facade import build_ui_service
from erp_web.services.vercel_ai_ui_service import VercelUiProtocolError
from erp_web.services.global_agent_chat_service import GLOBAL_CHAT_PROFILE_ID


class _Handler:
    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None
        self.status: int | None = None

    def send_json(self, data: dict[str, Any], status: int = 200) -> None:
        self.payload = data
        self.status = status


def _save_history(conversation_id: str = "conversation_1") -> None:
    get_context().pydantic_messages.save(
        conversation_id,
        [
            ModelRequest(parts=[UserPromptPart("检查草稿")]),
            ModelResponse(
                parts=[TextPart("草稿有效")],
                model_name="test-model",
                provider_name="test",
            ),
        ],
    )


def _get(path: str) -> _Handler:
    handler = _Handler()
    assert ai_work_routes.handle_get(handler, urllib.parse.urlparse(path))
    return handler


def test_list_returns_only_message_history_index_and_does_not_build_business_store() -> (
    None
):
    _save_history()

    handler = _get("/api/v1/ai-work/conversations")

    assert handler.status == 200
    assert handler.payload is not None
    assert handler.payload["ok"] is True
    summary = handler.payload["conversations"][0]
    assert set(summary) == {"conversation_id", "created_at", "updated_at"}
    assert summary["conversation_id"] == "conversation_1"
    assert get_context()._agent_calls is None


def test_detail_returns_validated_official_pydantic_message_json() -> None:
    _save_history()

    handler = _get("/api/v1/ai-work/conversations/conversation_1")

    assert handler.status == 200
    assert handler.payload is not None
    assert set(handler.payload) == {
        "ok",
        "conversation_id",
        "created_at",
        "updated_at",
        "messages",
    }
    assert [message["kind"] for message in handler.payload["messages"]] == [
        "request",
        "response",
    ]


def test_missing_history_returns_404() -> None:
    handler = _get("/api/v1/ai-work/conversations/missing")

    assert handler.status == 404
    assert handler.payload == {"ok": False, "error": "Pydantic 对话不存在。"}


@pytest.mark.parametrize("prefix", ["conversation_global_chat_", "conversation_"])
@pytest.mark.parametrize("display_text", [None, "为千斤顶垫匹配 OZON 类目。", ""])
def test_history_replay_distinguishes_business_input_from_chat_input(prefix, display_text) -> None:
    conversation_id = prefix + "a" * 32
    prompt = "请根据以下商品事实匹配目标平台类目：{\"product\":{\"title\":\"千斤顶垫\"}}"
    messages = [
        ModelRequest(parts=[UserPromptPart(prompt)], metadata={"presentation_user_message": display_text} if display_text is not None else None),
        ModelResponse(parts=[TextPart("正在查询类目"), ToolCallPart("browse_categories", {}, tool_call_id="call_1")]),
        ModelRequest(parts=[ToolReturnPart("browse_categories", {"nodes": []}, tool_call_id="call_1")]),
        ModelResponse(parts=[TextPart("匹配完成")]),
    ]
    store = get_context().pydantic_messages
    store.save(conversation_id, messages)
    original = store.get(conversation_id)
    expected = [m.model_dump(mode="json", by_alias=True, exclude_none=True)
                for m in VercelAIAdapter.dump_messages(original.model_messages(), sdk_version=VERCEL_SDK_VERSION)
                ]
    if display_text == "":
        expected = expected[1:]
    elif display_text is not None:
        expected[0]["parts"] = [{"type": "text", "text": display_text}]

    # 模拟运行结束后离开再打开历史，展示结果不能依赖实时 presentation 状态。
    for _ in range(2):
        handler = _get(f"/api/v1/ai-work/conversations/{conversation_id}/ui-messages")
        assert handler.status == 200
        assert handler.payload["messages"] == expected
        users = [m for m in handler.payload["messages"] if m["role"] == "user"]
        assert len(users) == int(display_text != "")
        if users:
            # 不按 ID 前缀或内容猜来源；未标记的真人输入始终保留。
            assert users[0]["parts"][0]["text"] == (prompt if display_text is None else display_text)
        tool = next(p for m in expected for p in m["parts"] if p["type"] == "tool-browse_categories")
        assert tool["state"] == "output-available"
        assert tool["output"] == {"nodes": []}

    raw = _get(f"/api/v1/ai-work/conversations/{conversation_id}")
    assert raw.payload["messages"][0]["parts"][0]["content"] == prompt
    saved = store.get(conversation_id)
    assert saved.messages_json == original.messages_json
    assert saved.history_version == original.history_version


def test_list_rejects_unknown_query_parameters() -> None:
    unknown_query = "?include_" + "children=true"
    handler = _get("/api/v1/ai-work/conversations" + unknown_query)

    assert handler.status == 400
    assert handler.payload is not None
    assert handler.payload["error_code"] == ("PYDANTIC_MESSAGE_HISTORY_QUERY_INVALID")


@pytest.mark.parametrize("state", ["claimed", "failed", "cancelled", "completed"])
def test_branch_rejects_unfinished_tool_history_even_with_completed_claim(state):
    context = get_context()
    source = "conversation_global_chat_" + "c" * 32
    claim = context.chat_turn_claims.claim_turn(
        conversation_id=source, client_message_id="user", profile_id=GLOBAL_CHAT_PROFILE_ID,
    )
    if state != "claimed":
        context.chat_turn_claims.finish_turn(claim.claim_id, status=state)
    context.pydantic_messages.save(source, [
        ModelRequest(parts=[UserPromptPart("开始")], metadata={"user_message_id": "user"}),
        ModelResponse(parts=[ToolCallPart("run_code", {}, tool_call_id="pending"), TextPart("仍在等待")]),
    ])
    service = build_ui_service()
    target = service.dump_ui_messages(source)["messages"][-1]["id"]
    before = context.pydantic_messages.list()
    with pytest.raises(VercelUiProtocolError, match="完成|未结束"):
        service.fork_conversation(source, target)
    assert context.pydantic_messages.list() == before


def test_branch_uses_selected_turn_boundary_and_retains_closed_tools():
    context = get_context()
    source = "conversation_global_chat_" + "d" * 32
    for message_id in ("one", "two"):
        claim = context.chat_turn_claims.claim_turn(
            conversation_id=source, client_message_id=message_id, profile_id=GLOBAL_CHAT_PROFILE_ID,
        )
        context.chat_turn_claims.finish_turn(claim.claim_id, status="completed")
    context.pydantic_messages.save(source, [
        ModelRequest(parts=[UserPromptPart("第一轮")], metadata={"user_message_id": "one"}),
        ModelResponse(parts=[ToolCallPart("run_code", {}, tool_call_id="closed")]),
        ModelRequest(parts=[ToolReturnPart("run_code", {"ok": True}, tool_call_id="closed")]),
        ModelResponse(parts=[TextPart("第一轮完成")]),
        ModelRequest(parts=[UserPromptPart("第二轮")], metadata={"user_message_id": "two"}),
        ModelResponse(parts=[TextPart("第二轮完成")]),
    ])
    service = build_ui_service()
    ui = service.dump_ui_messages(source)["messages"]
    with pytest.raises(VercelUiProtocolError):
        service.fork_conversation(source, ui[1]["id"])
    branch = service.fork_conversation(source, ui[2]["id"])
    messages = context.pydantic_messages.get(branch["conversation_id"]).model_messages()
    assert len(messages) == 4
    assert isinstance(messages[2].parts[0], ToolReturnPart)
    assert messages[-1].parts[0].content == "第一轮完成"
    assert context.agent_calls.receipt(branch["conversation_id"], "closed") is None
