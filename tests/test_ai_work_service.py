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


def test_retired_event_raw_and_children_endpoints_return_404() -> None:
    _save_history()

    # events 已由 Deferred 迁移恢复为正式订阅端点，不再是 retired。
    for action in ("raw", "children"):
        handler = _get(f"/api/v1/ai-work/conversations/conversation_1/{action}")
        assert handler.status == 404
        assert handler.payload == {"ok": False, "error": "未知的 AI Work 操作。"}


def test_retired_long_poll_query_is_not_accepted() -> None:
    _save_history()

    retired_query = "?after_" + "seq=1&wait_" + "ms=20000"
    handler = _get("/api/v1/ai-work/conversations/conversation_1" + retired_query)

    assert handler.status == 404


def test_list_rejects_unknown_query_parameters() -> None:
    unknown_query = "?include_" + "children=true"
    handler = _get("/api/v1/ai-work/conversations" + unknown_query)

    assert handler.status == 400
    assert handler.payload is not None
    assert handler.payload["error_code"] == ("PYDANTIC_MESSAGE_HISTORY_QUERY_INVALID")
