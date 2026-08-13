"""AI Work 只读对话查看 API。"""

from __future__ import annotations

import urllib.parse

from erp_web.context import get_context

from .common import JsonRequestHandler


CONVERSATIONS_PATH = "/api/v1/ai-work/conversations"
GET_API_ROUTES = frozenset({CONVERSATIONS_PATH})


def _int_param(params: dict[str, list[str]], name: str, default: int) -> int:
    try:
        return int((params.get(name) or [str(default)])[0] or default)
    except (TypeError, ValueError):
        return default


def _bool_param(
    params: dict[str, list[str]],
    name: str,
    default: bool = False,
) -> bool:
    raw = str((params.get(name) or [""])[0] or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def handle_conversation_list(handler: JsonRequestHandler, parsed: object) -> None:
    params = urllib.parse.parse_qs(parsed.query)
    limit = _int_param(params, "limit", 50)
    include_children = _bool_param(params, "include_children")
    handler.send_json(
        {
            "ok": True,
            "conversations": get_context().ai_journal.list_conversations(
                limit=limit,
                include_children=include_children,
            ),
        }
    )


def _conversation_parts(path: str) -> tuple[str, str, bool]:
    suffix = path[len(CONVERSATIONS_PATH) :].strip("/")
    if not suffix:
        return "", "", False
    parts = suffix.split("/")
    if len(parts) > 2 or any(not part for part in parts):
        return "", "", False
    return parts[0], parts[1] if len(parts) > 1 else "", True


def handle_conversation(handler: JsonRequestHandler, parsed: object) -> None:
    conversation_id, action, valid_path = _conversation_parts(parsed.path)
    if not valid_path:
        handler.send_json({"ok": False, "error": "未知的 AI Work 操作。"}, 404)
        return
    journal = get_context().ai_journal
    try:
        path = journal.find_conversation_path(conversation_id)
    except ValueError:
        handler.send_json(
            {
                "ok": False,
                "error": "AI 对话 ID 无效。",
                "error_code": "AI_WORK_CONVERSATION_ID_INVALID",
            },
            400,
        )
        return
    if path is None:
        handler.send_json({"ok": False, "error": "AI 对话不存在。"}, 404)
        return
    params = urllib.parse.parse_qs(parsed.query)
    after_seq = max(_int_param(params, "after_seq", 0), 0)
    if action == "children":
        limit = _int_param(params, "limit", 50)
        try:
            children = journal.list_child_conversations(
                conversation_id,
                limit=limit,
            )
        except ValueError as exc:
            handler.send_json({"ok": False, "error": str(exc)}, 400)
            return
        handler.send_json(
            {
                "ok": True,
                "conversations": children,
            }
        )
        return
    if action in {"events", "raw"}:
        wait_ms = 0 if action == "raw" else _int_param(params, "wait_ms", 0)
        events = journal.wait_for_events(
            conversation_id,
            after_seq=after_seq,
            wait_ms=wait_ms,
        )
        handler.send_ndjson(events)
        return
    if action:
        handler.send_json({"ok": False, "error": "未知的 AI Work 操作。"}, 404)
        return
    handler.send_json(
        {
            "ok": True,
            "conversation_id": conversation_id,
            "conversation": journal.get_conversation_summary(
                conversation_id
            ),
            "events": journal.read_events(
                conversation_id,
                after_seq=after_seq,
            ),
        }
    )


def handle_get(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = GET_HANDLERS.get(parsed.path)
    if route_handler:
        route_handler(handler, parsed)
        return True
    for prefix, dynamic_handler in DYNAMIC_GET_HANDLERS.items():
        if parsed.path.startswith(prefix):
            dynamic_handler(handler, parsed)
            return True
    return False


GET_HANDLERS = {
    CONVERSATIONS_PATH: handle_conversation_list,
}
DYNAMIC_GET_HANDLERS = {
    f"{CONVERSATIONS_PATH}/": handle_conversation,
}
HANDLED_PATHS = frozenset(GET_HANDLERS) | frozenset(DYNAMIC_GET_HANDLERS)


__all__ = [
    "DYNAMIC_GET_HANDLERS",
    "GET_HANDLERS",
    "GET_API_ROUTES",
    "HANDLED_PATHS",
    "handle_get",
]
