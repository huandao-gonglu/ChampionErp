"""全局任务受信 UI 的薄 HTTP 路由。

任务创建只通过 ``global.chat`` 的类型化 ``global_task_start`` 工具参数进入；
这里保留状态、补资料、取消、审批与长任务刷新的受信接口。批准/拒绝必须
携带 ``X-Approval-Token`` 请求头（只随 /api/state 下发给受信 UI），服务端
从校验通过的凭据派生审批身份；模型工具不暴露 approve/reject。
"""

from __future__ import annotations

from typing import Callable

from erp_web.facades import global_task_facade
from erp_web.schemas.requests import validate_request_payload

from .common import JsonRequestHandler


PostHandler = Callable[[JsonRequestHandler], None]

APPROVAL_TOKEN_HEADER = "X-Approval-Token"


def _validated_body(handler: JsonRequestHandler) -> dict:
    return validate_request_payload(handler.read_body(), endpoint=handler.path)


def _approval_token(handler: JsonRequestHandler) -> str:
    """从受信请求头读取审批凭据；协议不声明 headers，运行时由 BaseHTTPRequestHandler 提供。"""

    headers = getattr(handler, "headers", None)
    get = getattr(headers, "get", None)
    if callable(get):
        return str(get(APPROVAL_TOKEN_HEADER) or "").strip()
    return ""


def handle_global_task_state(handler: JsonRequestHandler) -> None:
    result, status = global_task_facade.get_global_task_payload(
        _validated_body(handler)
    )
    handler.send_json(result, status)


def handle_global_task_input(handler: JsonRequestHandler) -> None:
    result, status = global_task_facade.submit_global_task_input_payload(
        _validated_body(handler)
    )
    handler.send_json(result, status)


def handle_global_task_approve(handler: JsonRequestHandler) -> None:
    result, status = global_task_facade.approve_global_task_payload(
        _validated_body(handler),
        approval_token=_approval_token(handler),
    )
    handler.send_json(result, status)


def handle_global_task_reject(handler: JsonRequestHandler) -> None:
    result, status = global_task_facade.reject_global_task_payload(
        _validated_body(handler),
        approval_token=_approval_token(handler),
    )
    handler.send_json(result, status)


def handle_global_task_cancel(handler: JsonRequestHandler) -> None:
    result, status = global_task_facade.cancel_global_task_payload(
        _validated_body(handler)
    )
    handler.send_json(result, status)


def handle_global_task_refresh(handler: JsonRequestHandler) -> None:
    result, status = global_task_facade.refresh_global_task_payload(
        _validated_body(handler)
    )
    handler.send_json(result, status)


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/global-task-state": handle_global_task_state,
    "/api/global-task-input": handle_global_task_input,
    "/api/global-task-approve": handle_global_task_approve,
    "/api/global-task-reject": handle_global_task_reject,
    "/api/global-task-cancel": handle_global_task_cancel,
    "/api/global-task-refresh": handle_global_task_refresh,
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
