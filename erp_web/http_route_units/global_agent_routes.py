"""全局 Agent 顺序任务的薄 HTTP 路由。"""

from __future__ import annotations

from typing import Callable

from erp_web.facades import global_agent_facade
from erp_web.schemas.requests import validate_request_payload

from .common import JsonRequestHandler


PostHandler = Callable[[JsonRequestHandler], None]


def _validated_body(handler: JsonRequestHandler) -> dict:
    return validate_request_payload(handler.read_body(), endpoint=handler.path)


def handle_global_task_start(handler: JsonRequestHandler) -> None:
    result, status = global_agent_facade.start_global_task_payload(
        _validated_body(handler)
    )
    handler.send_json(result, status)


def handle_global_task_state(handler: JsonRequestHandler) -> None:
    result, status = global_agent_facade.get_global_task_payload(
        _validated_body(handler)
    )
    handler.send_json(result, status)


def handle_global_task_input(handler: JsonRequestHandler) -> None:
    result, status = global_agent_facade.submit_global_task_input_payload(
        _validated_body(handler)
    )
    handler.send_json(result, status)


def handle_global_task_publish_confirm(handler: JsonRequestHandler) -> None:
    result, status = global_agent_facade.confirm_global_task_publish_payload(
        _validated_body(handler)
    )
    handler.send_json(result, status)


def handle_global_task_cancel(handler: JsonRequestHandler) -> None:
    result, status = global_agent_facade.cancel_global_task_payload(
        _validated_body(handler)
    )
    handler.send_json(result, status)


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/global-task-start": handle_global_task_start,
    "/api/global-task-state": handle_global_task_state,
    "/api/global-task-input": handle_global_task_input,
    "/api/global-task-publish-confirm": handle_global_task_publish_confirm,
    "/api/global-task-cancel": handle_global_task_cancel,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True


__all__ = [
    "HANDLED_PATHS",
    "POST_HANDLERS",
    "handle_post",
]
