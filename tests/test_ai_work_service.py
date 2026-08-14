from __future__ import annotations

import urllib.parse
from typing import Any

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from erp_web.context import get_context
from erp_web.http_route_units import ai_work_routes


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


def test_list_returns_only_message_history_index_and_does_not_build_business_store() -> None:
    _save_history()

    handler = _get("/api/v1/ai-work/conversations")

    assert handler.status == 200
    assert handler.payload is not None
    assert handler.payload["ok"] is True
    summary = handler.payload["conversations"][0]
    assert set(summary) == {"conversation_id", "created_at", "updated_at"}
    assert summary["conversation_id"] == "conversation_1"
    assert get_context()._global_tasks is None


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


def test_retired_event_raw_and_children_endpoints_return_404() -> None:
    _save_history()

    for action in ("events", "raw", "children"):
        handler = _get(
            f"/api/v1/ai-work/conversations/conversation_1/{action}"
        )
        assert handler.status == 404
        assert handler.payload == {"ok": False, "error": "未知的 AI Work 操作。"}


def test_retired_long_poll_query_is_not_accepted() -> None:
    _save_history()

    retired_query = "?after_" + "seq=1&wait_" + "ms=20000"
    handler = _get(
        "/api/v1/ai-work/conversations/conversation_1" + retired_query
    )

    assert handler.status == 404


def test_list_rejects_unknown_query_parameters() -> None:
    unknown_query = "?include_" + "children=true"
    handler = _get("/api/v1/ai-work/conversations" + unknown_query)

    assert handler.status == 400
    assert handler.payload is not None
    assert handler.payload["error_code"] == (
        "PYDANTIC_MESSAGE_HISTORY_QUERY_INVALID"
    )
