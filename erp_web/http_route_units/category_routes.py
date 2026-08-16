# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable

from erp_web.schemas.requests import validate_request_payload

from .common import JsonRequestHandler
from ..facades import category_facade


PostHandler = Callable[[JsonRequestHandler], None]

CATEGORY_MATCH_PATH = "/api/v1/category-match"


def handle_category_attrs(handler: JsonRequestHandler) -> None:
    result, status = category_facade.category_attrs_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_category_attribute_values(handler: JsonRequestHandler) -> None:
    result, status = category_facade.category_attribute_values_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_category_search(handler: JsonRequestHandler) -> None:
    result, status = category_facade.category_search_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_category_match(handler: JsonRequestHandler) -> None:
    """同步 focused 类目匹配：类型化业务结果由本接口独占。

    实时展示关联由 HTTP 公共边界（X-AI-Presentation-ID claim）完成；
    本 route 不读取 presentation header，不导入 registry/SSE。
    """

    result, status = category_facade.category_match_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_category_ai_fill(handler: JsonRequestHandler) -> None:
    result, status = category_facade.category_ai_fill_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_category_precheck(handler: JsonRequestHandler) -> None:
    result, status = category_facade.category_precheck_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_get(handler: JsonRequestHandler, parsed: object) -> bool:
    # 类目匹配不再有独立 GET 入口；presentation 流由
    # /api/v1/ai-presentations 边界统一提供。保留空实现以满足
    # http_routes.GET_ROUTE_UNITS 的统一分派协议。
    return False


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/category-attribute-values": handle_category_attribute_values,
    "/api/category-attrs": handle_category_attrs,
    "/api/category-search": handle_category_search,
    CATEGORY_MATCH_PATH: handle_category_match,
    "/api/category-ai-fill": handle_category_ai_fill,
    "/api/category-precheck": handle_category_precheck,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True


__all__ = [
    "CATEGORY_MATCH_PATH",
    "HANDLED_PATHS",
    "POST_HANDLERS",
    "handle_get",
    "handle_post",
]
