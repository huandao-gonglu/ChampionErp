# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Callable

from .common import JsonRequestHandler
from ..facades import product_facade

PostHandler = Callable[[JsonRequestHandler], None]


def handle_calculate_price(handler: JsonRequestHandler) -> None:
    handler.send_json(product_facade.calculate_product_price(handler.read_body()))


def handle_assign_upc(handler: JsonRequestHandler) -> None:
    handler.send_json(product_facade.assign_product_upc())


def handle_save_product(handler: JsonRequestHandler) -> None:
    handler.send_json(product_facade.save_product_payload(handler.read_body()))


def handle_load_product(handler: JsonRequestHandler) -> None:
    handler.send_json(product_facade.load_product_payload(handler.read_body()))


def handle_load_draft(handler: JsonRequestHandler) -> None:
    result, status = product_facade.load_draft_payload(handler.read_body())
    handler.send_json(result, status)


def handle_save_draft(handler: JsonRequestHandler) -> None:
    result, status = product_facade.save_draft_payload(handler.read_body())
    handler.send_json(result, status)


def handle_delete_products(handler: JsonRequestHandler) -> None:
    result, status = product_facade.delete_products_payload(handler.read_body())
    handler.send_json(result, status)


def handle_delete_draft(handler: JsonRequestHandler) -> None:
    result, status = product_facade.delete_draft_payload(handler.read_body())
    handler.send_json(result, status)


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/calculate-price": handle_calculate_price,
    "/api/assign-upc": handle_assign_upc,
    "/api/save-product": handle_save_product,
    "/api/save-draft": handle_save_draft,
    "/api/load-product": handle_load_product,
    "/api/load-draft": handle_load_draft,
    "/api/delete-draft": handle_delete_draft,
    "/api/delete-products": handle_delete_products,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
