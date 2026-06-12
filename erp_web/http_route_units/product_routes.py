# -*- coding: utf-8 -*-
from __future__ import annotations


from typing import Callable

from .common import JsonRequestHandler
from .. import runtime as app
from ..runtime import *  # noqa: F403 - route units mirror legacy runtime globals.


PostHandler = Callable[[JsonRequestHandler], None]

def handle_calculate_price(handler: JsonRequestHandler) -> None:
    handler.send_json(calculate_price(handler.read_body()))
    return


def handle_assign_upc(handler: JsonRequestHandler) -> None:
    handler.send_json(assign_upc())
    return


def handle_save_product(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    product = save_product(body.get("product", {}))
    handler.send_json({"ok": True, "product": product, "productsIndex": load_products_index(), "imagePool": current_image_pool(product)})
    return


def handle_load_product(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    product = load_product_from_index(body.get("product_id", ""), body.get("product_file_path", ""))
    saved = save_product(product)
    handler.send_json({"ok": True, "product": saved, "productsIndex": load_products_index(), "imagePool": current_image_pool(saved), "sourceImages": current_source_images(saved)})
    return


def handle_delete_products(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    result = delete_products_from_index(body.get("product_ids") if isinstance(body.get("product_ids"), list) else [])
    handler.send_json(result, 200 if result.get("ok") else 400)
    return


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/calculate-price": handle_calculate_price,
    "/api/assign-upc": handle_assign_upc,
    "/api/save-product": handle_save_product,
    "/api/load-product": handle_load_product,
    "/api/delete-products": handle_delete_products,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
