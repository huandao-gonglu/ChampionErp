# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import urllib.parse

from erp_web.http_route_units import image_routes
from .context import get_context
from .http_route_units import (
    ai_chat_routes,
    ai_presentation_routes,
    ai_work_routes,
    auth_config_routes,
    category_routes,
    collect_routes,
    copy_routes,
    get_routes,
    global_agent_routes,
    logistics_routes,
    mercadolibre_routes,
    product_routes,
    product_research_routes,
    publish_routes,
    translation_routes,
)
from .http_route_units.common import JsonRequestHandler, UserInputError
from .http_request import safe_json_body, validate_request_metadata
from .schemas.requests import RequestValidationError
from .services.ai_presentation_context import bind_presentation_context
from .services.ai_presentation_service import (
    CLAIM_REJECTED_CODE,
    CLAIM_REJECTED_MESSAGE,
    claim_presentation_scope,
)


logger = logging.getLogger(__name__)

FRONTEND_PAGE_ROUTES = get_routes.FRONTEND_PAGE_ROUTES
GET_ROUTE_UNITS = (
    get_routes,
    ai_work_routes,
    ai_presentation_routes,
    category_routes,
    product_research_routes,
)
GET_API_ROUTES = frozenset(
    path
    for route_unit in GET_ROUTE_UNITS
    for path in getattr(route_unit, "GET_API_ROUTES", frozenset())
)
POST_ROUTE_UNITS = (
    ai_chat_routes,
    ai_presentation_routes,
    global_agent_routes,
    collect_routes,
    copy_routes,
    auth_config_routes,
    category_routes,
    product_routes,
    product_research_routes,
    logistics_routes,
    mercadolibre_routes,
    publish_routes,
    translation_routes,
)
POST_ROUTE_UNITS_BY_PATH = {
    path: route_unit
    for route_unit in POST_ROUTE_UNITS
    for path in route_unit.HANDLED_PATHS
}
POST_API_ROUTES = frozenset(POST_ROUTE_UNITS_BY_PATH)

__all__ = [
    "FRONTEND_PAGE_ROUTES",
    "GET_API_ROUTES",
    "POST_API_ROUTES",
    "handle_get",
    "handle_post",
    "safe_json_body",
]

def handle_get(handler: JsonRequestHandler) -> None:
    parsed = urllib.parse.urlparse(handler.path)
    try:
        # GET 也可能读取本机文件、触发远程请求，OAuth callback 还会换取
        # token；因此必须在任何路由处理器运行前应用同一浏览器边界。
        validate_request_metadata(handler)
        for route_unit in GET_ROUTE_UNITS:
            if route_unit.handle_get(handler, parsed):
                return
    except RequestValidationError as exc:
        # 日志只记录 path，避免把 OAuth code 等 query 内容写入磁盘。
        logger.warning("Rejected GET request %s: %s", parsed.path, exc)
        handler.send_json(
            {
                "ok": False,
                "error": str(exc),
                "error_code": exc.error_code,
            },
            exc.status_code,
        )
        return
    handler.send_response(404)
    handler.end_headers()


_PRESENTATION_HEADER = "X-AI-Presentation-ID"
_CLAIM_REJECTED = object()


def _read_header(handler: JsonRequestHandler, name: str) -> str:
    headers = getattr(handler, "headers", None)
    if headers is None:
        return ""
    return str(headers.get(name, "") or "").strip()


def _claim_presentation_scope(handler: JsonRequestHandler):
    """解析 ``X-AI-Presentation-ID`` 并在 HTTP 公共边界原子 claim。

    未携带 header 返回 None；非法、过期或已 claim 返回 ``_CLAIM_REJECTED``
    （边界映射为稳定 409，不静默创建第二个展示）；成功返回 root scope。
    业务 route 不读取该 header。
    """

    raw = _read_header(handler, _PRESENTATION_HEADER)
    if not raw:
        return None
    scope = claim_presentation_scope(
        get_context().ai_presentations,
        presentation_id=raw,
    )
    if scope is None:
        return _CLAIM_REJECTED
    return scope


def handle_post(handler: JsonRequestHandler) -> None:
    parsed = urllib.parse.urlparse(handler.path)
    if (
        parsed.path not in image_routes.IMAGE_POST_PATHS
        and parsed.path not in POST_ROUTE_UNITS_BY_PATH
    ):
        handler.send_response(404)
        handler.end_headers()
        return
    try:
        # 无请求体的写端点也必须先经过 Host/Origin 浏览器边界；在 claim
        # 之前完成，避免非法请求消耗 presentation reservation。
        validate_request_metadata(handler)
    except RequestValidationError as exc:
        logger.warning("Rejected POST request %s: %s", parsed.path, exc)
        handler.send_json(
            {
                "ok": False,
                "error": str(exc),
                "error_code": exc.error_code,
            },
            exc.status_code,
        )
        return
    scope = _claim_presentation_scope(handler)
    if scope is _CLAIM_REJECTED:
        handler.send_json(
            {
                "ok": False,
                "error": CLAIM_REJECTED_MESSAGE,
                "error_code": CLAIM_REJECTED_CODE,
            },
            409,
        )
        return
    request_failed = True
    try:
        if scope is None:
            request_failed = _dispatch_post(handler, parsed)
        else:
            # contextvar 是 presentation 上下文的唯一传播路径；handler 返回
            # 或抛错后由边界统一收尾 presentation 的 request 生命周期。
            with bind_presentation_context(scope):
                request_failed = _dispatch_post(handler, parsed)
    finally:
        if scope is not None:
            get_context().ai_presentations.finish_request(
                scope.presentation_id,
                request_failed=request_failed,
            )


def _response_status_failed(handler: JsonRequestHandler) -> bool:
    """route 正常返回后，按实际 HTTP 响应状态裁定请求成败。

    业务判断型结果（200 + ``ok=false``）仍是成功；subject 错误、权限不足
    等正常返回的 4xx/5xx 属于请求失败，presentation 必须标记 failed 而非
    completed。handler 未记录状态（如测试 fake）时视为成功。
    """

    status = getattr(handler, "response_status", None)
    return isinstance(status, int) and status >= 400


def _dispatch_post(handler: JsonRequestHandler, parsed) -> bool:
    """分派到 route unit；请求以失败（拒绝/HTTP 错误状态/异常）结束时返回 True。"""

    try:
        if image_routes.handle_post(handler, parsed.path):
            return _response_status_failed(handler)
        route_unit = POST_ROUTE_UNITS_BY_PATH.get(parsed.path)
        if route_unit and route_unit.handle_post(handler, parsed):
            return _response_status_failed(handler)
    except RequestValidationError as exc:
        logger.warning("Rejected POST request %s: %s", parsed.path, exc)
        handler.send_json(
            {
                "ok": False,
                "error": str(exc),
                "error_code": exc.error_code,
            },
            exc.status_code,
        )
        return True
    except UserInputError as exc:
        logger.warning("Rejected POST request %s: %s", parsed.path, exc)
        handler.send_json({"ok": False, "error": str(exc)}, 400)
        return True
    except Exception as exc:
        logger.exception(
            "Unhandled POST request failed: %s",
            parsed.path,
        )
        handler.send_json({"ok": False, "error": str(exc)}, 500)
        return True
    # HANDLED_PATHS 与 handle_post 分派表不一致属于服务端契约错误。
    logger.error("POST route declared but not handled: %s", parsed.path)
    handler.send_json(
        {"ok": False, "error": "写接口分派失败"},
        500,
    )
    return True
