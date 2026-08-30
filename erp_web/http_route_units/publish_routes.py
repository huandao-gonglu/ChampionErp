# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable

from erp_web.schemas.requests import validate_request_payload

from .common import JsonRequestHandler
from ..facades import publish_facade

PostHandler = Callable[[JsonRequestHandler], None]


def handle_publish_precheck(handler: JsonRequestHandler) -> None:
    result, status = publish_facade.precheck_publish_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_publish_payload_preview(handler: JsonRequestHandler) -> None:
    result, status = publish_facade.preview_publish_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_publish_product(handler: JsonRequestHandler) -> None:
    result, status = publish_facade.publish_product_payload(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_mercadolibre_pause_user_product(handler: JsonRequestHandler) -> None:
    result, status = publish_facade.pause_mercadolibre_user_product(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_publish_bus_enqueue(handler: JsonRequestHandler) -> None:
    result, status = publish_facade.enqueue_publish_job(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


def handle_publish_bus_reconcile(handler: JsonRequestHandler) -> None:
    result, status = publish_facade.reconcile_publish_job(
        validate_request_payload(handler.read_body(), endpoint=handler.path)
    )
    handler.send_json(result, status)


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/publish-precheck": handle_publish_precheck,
    "/api/publish-payload-preview": handle_publish_payload_preview,
    "/api/publish-product": handle_publish_product,
    "/api/mercadolibre/pause-user-product": handle_mercadolibre_pause_user_product,
    "/api/publish-bus/enqueue": handle_publish_bus_enqueue,
    "/api/publish-bus/reconcile": handle_publish_bus_reconcile,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
