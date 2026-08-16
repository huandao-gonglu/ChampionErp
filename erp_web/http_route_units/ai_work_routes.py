"""Pydantic message history 只读检查 API 与官方 UIMessage 派生读取。"""

from __future__ import annotations

import json
import urllib.parse

from erp_web.context import get_context
from erp_web.facades import ai_chat_facade
from erp_web.stores.pydantic_message_store import PydanticMessageStoreError

from .common import JsonRequestHandler


CONVERSATIONS_PATH = "/api/v1/ai-work/conversations"
UI_MESSAGES_SUFFIX = "/ui-messages"
GET_API_ROUTES = frozenset({CONVERSATIONS_PATH})


def _limit_param(params: dict[str, list[str]]) -> int:
    unknown = set(params) - {"limit"}
    if unknown:
        raise ValueError("AI Work 列表包含不支持的查询参数。")
    try:
        return max(1, min(int((params.get("limit") or ["50"])[0]), 200))
    except (TypeError, ValueError):
        raise ValueError("AI Work 列表 limit 必须是整数。") from None


def handle_conversation_list(handler: JsonRequestHandler, parsed: object) -> None:
    try:
        limit = _limit_param(urllib.parse.parse_qs(parsed.query))
    except ValueError as exc:
        handler.send_json(
            {
                "ok": False,
                "error": str(exc),
                "error_code": "PYDANTIC_MESSAGE_HISTORY_QUERY_INVALID",
            },
            400,
        )
        return
    summaries = get_context().pydantic_messages.list(limit=limit)
    handler.send_json(
        {
            "ok": True,
            "conversations": [
                {
                    "conversation_id": summary.conversation_id,
                    "created_at": summary.created_at,
                    "updated_at": summary.updated_at,
                }
                for summary in summaries
            ],
        }
    )


def _has_control_chars(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _conversation_id(path: str) -> str | None:
    """共享的 dependency-light ID 解码/校验：拒绝空值、斜杠、反斜杠和控制字符。"""

    suffix = path[len(CONVERSATIONS_PATH) :]
    if not suffix.startswith("/"):
        return None
    encoded = suffix[1:]
    if not encoded or "/" in encoded:
        return None
    conversation_id = urllib.parse.unquote(encoded).strip()
    if not conversation_id or "/" in conversation_id or "\\" in conversation_id:
        return None
    if _has_control_chars(conversation_id):
        return None
    return conversation_id


def _ui_messages_conversation_id(path: str) -> str | None:
    if not path.endswith(UI_MESSAGES_SUFFIX):
        return None
    return _conversation_id(path[: -len(UI_MESSAGES_SUFFIX)])


def handle_conversation(handler: JsonRequestHandler, parsed: object) -> None:
    conversation_id = _conversation_id(parsed.path)
    if conversation_id is None or parsed.query:
        handler.send_json({"ok": False, "error": "未知的 AI Work 操作。"}, 404)
        return
    try:
        history = get_context().pydantic_messages.get(conversation_id)
    except PydanticMessageStoreError as exc:
        handler.send_json(
            {
                "ok": False,
                "error": str(exc),
                "error_code": exc.code,
            },
            500,
        )
        return
    if history is None:
        handler.send_json({"ok": False, "error": "Pydantic 对话不存在。"}, 404)
        return
    handler.send_json(
        {
            "ok": True,
            "conversation_id": history.conversation_id,
            "created_at": history.created_at,
            "updated_at": history.updated_at,
            "messages": json.loads(history.messages_json),
        }
    )


def handle_conversation_ui_messages(
    handler: JsonRequestHandler,
    parsed: object,
) -> None:
    """用官方 Adapter 派生只读 UIMessage[]；route 不手写 Vercel part shape。"""

    conversation_id = _ui_messages_conversation_id(parsed.path)
    if conversation_id is None or parsed.query:
        handler.send_json({"ok": False, "error": "未知的 AI Work 操作。"}, 404)
        return
    result, status = ai_chat_facade.ui_messages_payload(conversation_id)
    handler.send_json(result, status)


def handle_get(handler: JsonRequestHandler, parsed: object) -> bool:
    if parsed.path == CONVERSATIONS_PATH:
        handle_conversation_list(handler, parsed)
        return True
    if parsed.path.startswith(f"{CONVERSATIONS_PATH}/"):
        if parsed.path.endswith(UI_MESSAGES_SUFFIX):
            handle_conversation_ui_messages(handler, parsed)
        else:
            handle_conversation(handler, parsed)
        return True
    return False


GET_HANDLERS = {CONVERSATIONS_PATH: handle_conversation_list}
DYNAMIC_GET_HANDLERS = {
    f"{CONVERSATIONS_PATH}/": handle_conversation,
    f"{CONVERSATIONS_PATH}/<conversation_id>{UI_MESSAGES_SUFFIX}": (
        handle_conversation_ui_messages
    ),
}
HANDLED_PATHS = frozenset(GET_HANDLERS) | frozenset(DYNAMIC_GET_HANDLERS)


__all__ = [
    "CONVERSATIONS_PATH",
    "DYNAMIC_GET_HANDLERS",
    "GET_HANDLERS",
    "GET_API_ROUTES",
    "HANDLED_PATHS",
    "UI_MESSAGES_SUFFIX",
    "handle_get",
]
