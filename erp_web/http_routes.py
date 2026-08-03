# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import urllib.parse

from erp_web.http_route_units import image_routes
from .http_route_units import (
    ai_work_routes,
    auth_config_routes,
    category_routes,
    collect_routes,
    copy_routes,
    get_routes,
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


logger = logging.getLogger(__name__)

FRONTEND_PAGE_ROUTES = get_routes.FRONTEND_PAGE_ROUTES
GET_ROUTE_UNITS = (
    get_routes,
    ai_work_routes,
    product_research_routes,
)
GET_API_ROUTES = frozenset(
    path
    for route_unit in GET_ROUTE_UNITS
    for path in getattr(route_unit, "GET_API_ROUTES", frozenset())
)
POST_ROUTE_UNITS = (
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
        # 无请求体的写端点也必须先经过 Host/Origin 浏览器边界。
        validate_request_metadata(handler)
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
