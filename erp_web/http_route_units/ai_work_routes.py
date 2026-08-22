"""Pydantic message history 只读检查 API 与官方 UIMessage 派生读取。"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse

from erp_web.context import get_context
from erp_web.facades import ai_chat_facade, global_task_facade
from erp_web.stores.pydantic_message_store import PydanticMessageStoreError

from .common import JsonRequestHandler


_logger = logging.getLogger(__name__)

CONVERSATIONS_PATH = "/api/v1/ai-work/conversations"
UI_MESSAGES_SUFFIX = "/ui-messages"
TASK_LINK_SUFFIX = "/task-link"
EVENTS_SUFFIX = "/events"
GLOBAL_TASKS_PATH = "/api/v1/global-tasks"
GET_API_ROUTES = frozenset({CONVERSATIONS_PATH, GLOBAL_TASKS_PATH})


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


def _task_link_conversation_id(path: str) -> str | None:
    if not path.endswith(TASK_LINK_SUFFIX):
        return None
    return _conversation_id(path[: -len(TASK_LINK_SUFFIX)])


def handle_conversation_task_link(
    handler: JsonRequestHandler,
    parsed: object,
) -> None:
    """conversation → 未解决 Deferred 任务的纯读关联（只返回 ready link）。"""

    conversation_id = _task_link_conversation_id(parsed.path)
    if conversation_id is None or parsed.query:
        handler.send_json({"ok": False, "error": "未知的 AI Work 操作。"}, 404)
        return
    result, status = global_task_facade.conversation_task_link_payload(
        conversation_id
    )
    handler.send_json(result, status)


def _events_conversation_id(path: str) -> str | None:
    if not path.endswith(EVENTS_SUFFIX):
        return None
    return _conversation_id(path[: -len(EVENTS_SUFFIX)])


def _after_history_version(query: str) -> int:
    params = urllib.parse.parse_qs(query)
    unknown = set(params) - {"after_history_version"}
    if unknown:
        raise ValueError("事件订阅包含不支持的查询参数。")
    raw = (params.get("after_history_version") or ["0"])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("after_history_version 必须是整数。") from None
    return max(0, value)


def handle_conversation_events(
    handler: JsonRequestHandler,
    parsed: object,
) -> None:
    """活动 conversation 的后台官方事件订阅（SSE）。"""

    conversation_id = _events_conversation_id(parsed.path)
    if conversation_id is None:
        handler.send_json({"ok": False, "error": "未知的 AI Work 操作。"}, 404)
        return
    try:
        after_history_version = _after_history_version(parsed.query)
    except ValueError as exc:
        handler.send_json(
            {
                "ok": False,
                "error": str(exc),
                "error_code": "AI_WORK_EVENTS_QUERY_INVALID",
            },
            400,
        )
        return
    stream = ai_chat_facade.build_conversation_event_stream(
        conversation_id,
        after_history_version=after_history_version,
    )
    handler.send_sse_headers(stream.sse_headers())
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(stream.stream(handler.write_sse_chunk))
    except (BrokenPipeError, ConnectionResetError, OSError):
        # 客户端断开只结束当前订阅；不影响服务端 run 与 history 提交。
        pass
    except Exception:
        _logger.exception(
            "AI Work 事件订阅异常结束：%s",
            conversation_id,
        )
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()


def _global_task_id(path: str) -> str | None:
    """解析 /api/v1/global-tasks/<task_id>；拒绝空值、斜杠与控制字符。"""

    suffix = path[len(GLOBAL_TASKS_PATH):]
    if not suffix.startswith("/"):
        return None
    encoded = suffix[1:]
    if not encoded or "/" in encoded:
        return None
    task_id = urllib.parse.unquote(encoded).strip()
    if not task_id or "/" in task_id or "\\" in task_id:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in task_id):
        return None
    return task_id


def handle_global_task_state(
    handler: JsonRequestHandler,
    parsed: object,
) -> None:
    """按 task_id 纯读任务状态；GET 刷新不推进任务。"""

    task_id = _global_task_id(parsed.path)
    if task_id is None or parsed.query:
        handler.send_json({"ok": False, "error": "未知的任务读取操作。"}, 404)
        return
    result, status = global_task_facade.read_global_task_state_payload(task_id)
    handler.send_json(result, status)


def handle_get(handler: JsonRequestHandler, parsed: object) -> bool:
    if parsed.path == CONVERSATIONS_PATH:
        handle_conversation_list(handler, parsed)
        return True
    if parsed.path.startswith(f"{CONVERSATIONS_PATH}/"):
        if parsed.path.endswith(UI_MESSAGES_SUFFIX):
            handle_conversation_ui_messages(handler, parsed)
        elif parsed.path.endswith(TASK_LINK_SUFFIX):
            handle_conversation_task_link(handler, parsed)
        elif parsed.path.endswith(EVENTS_SUFFIX):
            handle_conversation_events(handler, parsed)
        else:
            handle_conversation(handler, parsed)
        return True
    if parsed.path.startswith(f"{GLOBAL_TASKS_PATH}/"):
        handle_global_task_state(handler, parsed)
        return True
    return False


GET_HANDLERS = {CONVERSATIONS_PATH: handle_conversation_list}
DYNAMIC_GET_HANDLERS = {
    f"{CONVERSATIONS_PATH}/": handle_conversation,
    f"{CONVERSATIONS_PATH}/<conversation_id>{UI_MESSAGES_SUFFIX}": (
        handle_conversation_ui_messages
    ),
    f"{CONVERSATIONS_PATH}/<conversation_id>{TASK_LINK_SUFFIX}": (
        handle_conversation_task_link
    ),
    f"{CONVERSATIONS_PATH}/<conversation_id>{EVENTS_SUFFIX}": (
        handle_conversation_events
    ),
    f"{GLOBAL_TASKS_PATH}/<task_id>": handle_global_task_state,
}
HANDLED_PATHS = frozenset(GET_HANDLERS) | frozenset(DYNAMIC_GET_HANDLERS)


__all__ = [
    "CONVERSATIONS_PATH",
    "DYNAMIC_GET_HANDLERS",
    "EVENTS_SUFFIX",
    "GET_HANDLERS",
    "GET_API_ROUTES",
    "GLOBAL_TASKS_PATH",
    "HANDLED_PATHS",
    "TASK_LINK_SUFFIX",
    "UI_MESSAGES_SUFFIX",
    "handle_get",
]
