# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable

from .common import JsonRequestHandler
from ..facades import product_research_facade


PostHandler = Callable[[JsonRequestHandler], None]
GetHandler = Callable[[JsonRequestHandler, object], None]


def handle_create_search_task(handler: JsonRequestHandler) -> None:
    result, status = product_research_facade.create_search_task_payload(handler.read_body())
    handler.send_json(result, status)


def handle_save_source_registry(handler: JsonRequestHandler) -> None:
    result, status = product_research_facade.save_source_registry_payload(handler.read_body())
    handler.send_json(result, status)


def handle_test_search_provider(handler: JsonRequestHandler) -> None:
    result, status = product_research_facade.test_search_provider_payload(handler.read_body())
    handler.send_json(result, status)


def handle_get_search_task(handler: JsonRequestHandler, parsed: object) -> None:
    result, status = product_research_facade.get_search_task_payload(parsed)
    handler.send_json(result, status)


def handle_get_source_registry(handler: JsonRequestHandler, parsed: object) -> None:
    result, status = product_research_facade.get_source_registry_payload()
    handler.send_json(result, status)


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/v1/product-research/search-tasks": handle_create_search_task,
    "/api/v1/product-research/source-registry/save": handle_save_source_registry,
    "/api/v1/product-research/search-providers/test": handle_test_search_provider,
}
GET_HANDLERS: dict[str, GetHandler] = {
    "/api/v1/product-research/search-tasks": handle_get_search_task,
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
