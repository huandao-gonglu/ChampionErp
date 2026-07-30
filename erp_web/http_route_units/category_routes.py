# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable

from erp_web.schemas.requests import validate_request_payload

from .common import JsonRequestHandler
from ..facades import category_facade


PostHandler = Callable[[JsonRequestHandler], None]


def handle_category_attrs(handler: JsonRequestHandler) -> None:
    result, status = category_facade.category_attrs_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_category_search(handler: JsonRequestHandler) -> None:
    result, status = category_facade.category_search_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_category_ai_suggest(handler: JsonRequestHandler) -> None:
    result, status = category_facade.category_ai_suggest_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_category_ai_identify_product(handler: JsonRequestHandler) -> None:
    result, status = category_facade.category_ai_identify_product_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_category_ai_fill(handler: JsonRequestHandler) -> None:
    result, status = category_facade.category_ai_fill_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_category_attribute_translations(handler: JsonRequestHandler) -> None:
    result, status = category_facade.category_attribute_translations_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_category_result_translations(handler: JsonRequestHandler) -> None:
    result, status = category_facade.category_result_translations_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_category_precheck(handler: JsonRequestHandler) -> None:
    result, status = category_facade.category_precheck_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/category-attrs": handle_category_attrs,
    "/api/category-search": handle_category_search,
    "/api/category-ai-suggest": handle_category_ai_suggest,
    "/api/category-ai-identify-product": handle_category_ai_identify_product,
    "/api/category-ai-fill": handle_category_ai_fill,
    "/api/category-attribute-translations": handle_category_attribute_translations,
    "/api/category-result-translations": handle_category_result_translations,
    "/api/category-precheck": handle_category_precheck,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
