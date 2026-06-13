# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable

from .common import JsonRequestHandler
from ..runtime_units.mercadolibre_orders import record_mercadolibre_order_notification

PostHandler = Callable[[JsonRequestHandler], None]


def handle_mercadolibre_notification(handler: JsonRequestHandler) -> None:
    result = record_mercadolibre_order_notification(handler.read_body())
    handler.send_json(result, 200 if result.get("ok") else 202)


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/mercadolibre/notifications": handle_mercadolibre_notification,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
