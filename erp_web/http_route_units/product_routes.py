# -*- coding: utf-8 -*-
from __future__ import annotations


from .common import JsonRequestHandler
from .. import runtime as app
from ..runtime import *  # noqa: F403 - route units mirror legacy runtime globals.


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    if parsed.path == "/api/calculate-price":
        handler.send_json(calculate_price(handler.read_body()))
        return True
    if parsed.path == "/api/assign-upc":
        handler.send_json(assign_upc())
        return True
    if parsed.path == "/api/save-product":
        body = handler.read_body()
        product = save_product(body.get("product", {}))
        handler.send_json({"ok": True, "product": product, "productsIndex": load_products_index(), "imagePool": current_image_pool(product)})
        return True
    if parsed.path == "/api/load-product":
        body = handler.read_body()
        product = load_product_from_index(body.get("product_id", ""), body.get("product_file_path", ""))
        saved = save_product(product)
        handler.send_json({"ok": True, "product": saved, "productsIndex": load_products_index(), "imagePool": current_image_pool(saved), "sourceImages": current_source_images(saved)})
        return True
    if parsed.path == "/api/delete-products":
        body = handler.read_body()
        result = delete_products_from_index(body.get("product_ids") if isinstance(body.get("product_ids"), list) else [])
        handler.send_json(result, 200 if result.get("ok") else 400)
        return True
    return False
