# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import urllib.parse

from erp_web.http_route_units import image_routes
from .services.external_request_context import request_operation
from .services.desktop_connection_service import desktop_request
from .http_route_units import (
    account_routes,
    business_query_routes,
    task_queue_routes,
    wechat_routes,
    ai_chat_routes,
    ai_work_routes,
    auth_config_routes,
    alibaba_authorization_routes,
    alibaba_direct_purchase_routes,
    image_hosting_routes,
    category_routes,
    collect_routes,
    desktop_routes,
    copy_routes,
    get_routes,
    order_routes,
    fulfillment_routes,
    product_routes,
    product_research_routes,
    pricing_workspace_routes,
    publish_routes,
    online_product_routes,
    translation_routes,
)
from .http_route_units.common import JsonRequestHandler, UserInputError
from .http_request import safe_json_body, validate_request_metadata
from .schemas.requests import RequestValidationError
logger = logging.getLogger(__name__)

FRONTEND_PAGE_ROUTES = get_routes.FRONTEND_PAGE_ROUTES
GET_ROUTE_UNITS = (
    account_routes,
    business_query_routes,
    alibaba_authorization_routes,
    alibaba_direct_purchase_routes,
    task_queue_routes,
    wechat_routes,
    pricing_workspace_routes,
    fulfillment_routes,
    order_routes,
    online_product_routes,
    get_routes,
    ai_work_routes,
    category_routes,
    product_research_routes,
)
GET_API_ROUTES = frozenset(
    path
    for route_unit in GET_ROUTE_UNITS
    for path in getattr(route_unit, "GET_API_ROUTES", frozenset())
)
POST_ROUTE_UNITS = (
    account_routes,
    desktop_routes,
    alibaba_authorization_routes,
    alibaba_direct_purchase_routes,
    task_queue_routes,
    wechat_routes,
    pricing_workspace_routes,
    fulfillment_routes,
    image_hosting_routes,
    ai_chat_routes,
    collect_routes,
    copy_routes,
    auth_config_routes,
    category_routes,
    product_routes,
    product_research_routes,
    order_routes,
    publish_routes,
    online_product_routes,
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


@request_operation("http_get")
@desktop_request
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


@request_operation("http_post")
@desktop_request
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
        # 无请求体的写端点也先校验浏览器来源，再分派至业务处理。
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
    _dispatch_post(handler, parsed)


def _dispatch_post(handler: JsonRequestHandler, parsed) -> None:
    try:
        if image_routes.handle_post(handler, parsed.path):
            return
        route_unit = POST_ROUTE_UNITS_BY_PATH.get(parsed.path)
        if route_unit and route_unit.handle_post(handler, parsed):
            return
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
    except UserInputError as exc:
        logger.warning("Rejected POST request %s: %s", parsed.path, exc)
        handler.send_json({"ok": False, "error": str(exc)}, 400)
        return
    except Exception as exc:
        logger.exception(
            "Unhandled POST request failed: %s",
            parsed.path,
        )
        handler.send_json({"ok": False, "error": str(exc)}, 500)
        return
    # HANDLED_PATHS 与 handle_post 分派表不一致属于服务端契约错误。
    logger.error("POST route declared but not handled: %s", parsed.path)
    handler.send_json(
        {"ok": False, "error": "写接口分派失败"},
        500,
    )
