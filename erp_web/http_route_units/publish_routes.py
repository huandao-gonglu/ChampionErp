# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable

from .common import JsonRequestHandler
from ..facades import publish_facade

PostHandler = Callable[[JsonRequestHandler], None]


def handle_publish_precheck(handler: JsonRequestHandler) -> None:
    result, status = publish_facade.precheck_publish_payload(handler.read_body())
    handler.send_json(result, status)


def handle_publish_payload_preview(handler: JsonRequestHandler) -> None:
    result, status = publish_facade.preview_publish_payload(handler.read_body())
    handler.send_json(result, status)


def handle_publish_product(handler: JsonRequestHandler) -> None:
    result, status = publish_facade.publish_product_payload(handler.read_body())
    handler.send_json(result, status)


def handle_mercadolibre_confirm_real_publish(handler: JsonRequestHandler) -> None:
    result, status = publish_facade.confirm_mercadolibre_real_publish(handler.read_body())
    handler.send_json(result, status)


def handle_mercadolibre_close_item(handler: JsonRequestHandler) -> None:
    result, status = publish_facade.close_mercadolibre_item(handler.read_body())
    handler.send_json(result, status)


def handle_publish_bus_enqueue(handler: JsonRequestHandler) -> None:
    result, status = publish_facade.enqueue_publish_job(handler.read_body())
    handler.send_json(result, status)


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/publish-precheck": handle_publish_precheck,
    "/api/publish-payload-preview": handle_publish_payload_preview,
    "/api/publish-product": handle_publish_product,
    "/api/mercadolibre/confirm-real-publish": handle_mercadolibre_confirm_real_publish,
    "/api/mercadolibre/close-item": handle_mercadolibre_close_item,
    "/api/publish-bus/enqueue": handle_publish_bus_enqueue,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
