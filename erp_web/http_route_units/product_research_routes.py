# -*- coding: utf-8 -*-
from __future__ import annotations

import urllib.parse
from typing import Callable

from .common import JsonRequestHandler
from ..facades import product_research_facade


PostHandler = Callable[[JsonRequestHandler], None]
GetHandler = Callable[[JsonRequestHandler, object], None]


def handle_create_hot_product_run(handler: JsonRequestHandler) -> None:
    result, status = product_research_facade.create_hot_product_run_payload(handler.read_body())
    handler.send_json(result, status)


def handle_save_source_registry(handler: JsonRequestHandler) -> None:
    result, status = product_research_facade.save_source_registry_payload(handler.read_body())
    handler.send_json(result, status)


def handle_test_search_provider(handler: JsonRequestHandler) -> None:
    result, status = product_research_facade.test_search_provider_payload(handler.read_body())
    handler.send_json(result, status)


def handle_complete_search_provider(handler: JsonRequestHandler) -> None:
    result, status = product_research_facade.complete_provider_config_payload(handler.read_body())
    handler.send_json(result, status)


def handle_get_source_registry(handler: JsonRequestHandler, parsed: object) -> None:
    result, status = product_research_facade.get_source_registry_payload()
    handler.send_json(result, status)


def handle_get_hot_product_run(handler: JsonRequestHandler, parsed: object) -> None:
    query = urllib.parse.parse_qs(getattr(parsed, "query", ""))
    run_id = (query.get("run_id") or query.get("runId") or [""])[0]
    if run_id:
        result, status = product_research_facade.get_hot_product_run_payload(run_id)
    else:
        result, status = product_research_facade.get_active_hot_product_run_payload()
    handler.send_json(result, status)


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/v1/product-research/hot-products/search": handle_create_hot_product_run,
    "/api/v1/product-research/source-registry/save": handle_save_source_registry,
    "/api/v1/product-research/search-providers/test": handle_test_search_provider,
    "/api/v1/product-research/search-providers/ai-complete": handle_complete_search_provider,
}
GET_HANDLERS: dict[str, GetHandler] = {
    "/api/v1/product-research/hot-products/runs": handle_get_hot_product_run,
    "/api/v1/product-research/source-registry": handle_get_source_registry,
}
GET_API_ROUTES = frozenset(GET_HANDLERS)
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True


def handle_get(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = GET_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler, parsed)
    return True


__all__ = [
    "GET_API_ROUTES",
    "GET_HANDLERS",
    "HANDLED_PATHS",
    "POST_HANDLERS",
    "handle_get",
    "handle_post",
]
