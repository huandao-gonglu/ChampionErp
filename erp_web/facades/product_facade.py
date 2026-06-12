from __future__ import annotations

from typing import Any

from erp_web.runtime_units.image_pool import current_image_pool, current_source_images
from erp_web.runtime_units.pricing_runtime import calculate_price
from erp_web.runtime_units.product_store import (
    delete_products_from_index,
    load_product_from_index,
    load_products_index,
    save_product,
)
from erp_web.runtime_units.publish_helpers import assign_upc
from erp_web.schemas.api import ApiResponse
from erp_web.schemas.product import Product

ResponseWithStatus = tuple[ApiResponse, int]


def calculate_product_price(body: dict[str, Any]) -> ApiResponse:
    return calculate_price(body)


def assign_product_upc() -> ApiResponse:
    return assign_upc()


def save_product_payload(body: dict[str, Any]) -> ApiResponse:
    product: Product = save_product(body.get("product", {}))
    return {
        "ok": True,
        "product": product,
        "productsIndex": load_products_index(),
        "imagePool": current_image_pool(product),
    }


def load_product_payload(body: dict[str, Any]) -> ApiResponse:
    product = load_product_from_index(body.get("product_id", ""), body.get("product_file_path", ""))
    saved: Product = save_product(product)
    return {
        "ok": True,
        "product": saved,
        "productsIndex": load_products_index(),
        "imagePool": current_image_pool(saved),
        "sourceImages": current_source_images(saved),
    }


def delete_products_payload(body: dict[str, Any]) -> ResponseWithStatus:
    result = delete_products_from_index(body.get("product_ids") if isinstance(body.get("product_ids"), list) else [])
    return result, 200 if result.get("ok") else 400


__all__ = [
    "assign_product_upc",
    "calculate_product_price",
    "delete_products_payload",
    "load_product_payload",
    "save_product_payload",
]
