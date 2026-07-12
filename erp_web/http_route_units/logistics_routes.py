# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable

from .common import JsonRequestHandler
from ..facades import logistics_facade

PostHandler = Callable[[JsonRequestHandler], None]


def handle_yunexpress_preview(handler: JsonRequestHandler) -> None:
    result, status = logistics_facade.preview_yunexpress_shipment(handler.read_body())
    handler.send_json(result, status)


def handle_yunexpress_create_shipment(handler: JsonRequestHandler) -> None:
    result, status = logistics_facade.create_yunexpress_shipment(handler.read_body())
    handler.send_json(result, status)


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/logistics/yunexpress/preview": handle_yunexpress_preview,
    "/api/logistics/yunexpress/create-shipment": handle_yunexpress_create_shipment,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
