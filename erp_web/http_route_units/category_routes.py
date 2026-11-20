# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable

from erp_web.schemas.requests import validate_request_payload

from .common import JsonRequestHandler
from .manual_ai import send_manual_ai_result
from ..facades import category_facade


PostHandler = Callable[[JsonRequestHandler], None]

CATEGORY_MATCH_PATH = "/api/v1/category-match"
CATEGORY_MATCH_BATCH_PATH = "/api/v1/category-match/batch"


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
    send_manual_ai_result(handler, "category", category_facade.category_match_payload)


def handle_category_match_batch(handler: JsonRequestHandler) -> None:
    send_manual_ai_result(handler, "category", category_facade.category_match_batch_payload)

def handle_category_precheck(handler: JsonRequestHandler) -> None:
    result, status = category_facade.category_precheck_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_get(handler: JsonRequestHandler, parsed: object) -> bool:
    return False


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/category-attribute-values": handle_category_attribute_values,
    "/api/category-attrs": handle_category_attrs,
    "/api/category-search": handle_category_search,
    CATEGORY_MATCH_PATH: handle_category_match,
    CATEGORY_MATCH_BATCH_PATH: handle_category_match_batch,
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
