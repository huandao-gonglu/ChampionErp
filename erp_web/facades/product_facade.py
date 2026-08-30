from __future__ import annotations

from typing import Any

from erp_web.context import get_context
from erp_web.runtime_units.draft_category_resolution import (
    resolve_draft_category_pairs as _resolve_draft_category_pairs,
)
from erp_web.runtime_units.image_pool import current_image_pool, current_source_images
from erp_web.runtime_units.pricing_runtime import calculate_price
from erp_web.runtime_units.publish_helpers import assign_upc
from erp_web.schemas.api import ApiResponse
from erp_web.schemas.product import Product

ResponseWithStatus = tuple[ApiResponse, int]


def save_product_payload(body: dict[str, Any]) -> ApiResponse:
    products = get_context().products
    product: Product = products.save_product_profile(
        body.get("product", {})
    )
    return {
        "ok": True,
        "product": product,
        "productsIndex": products.load_products_index(),
        "draftsIndex": products.load_drafts_index(),
        "imagePool": current_image_pool(product),
    }


def load_product_payload(body: dict[str, Any]) -> ApiResponse:
    products = get_context().products
    product = products.load_product_from_index(
        body.get("product_id", ""),
        body.get("product_file_path", ""),
    )
    return {
        "ok": True,
        "product": product,
        "productsIndex": products.load_products_index(),
        "draftsIndex": products.load_drafts_index(),
        "imagePool": current_image_pool(product),
        "sourceImages": current_source_images(product),
    }


def load_draft_payload(body: dict[str, Any]) -> ResponseWithStatus:
    products = get_context().products
    result, error, status = products.load_draft_detail_from_index(
        body.get("draft_id", "") or body.get("draftId", "")
    )
    return (error or result), status


def duplicate_draft_payload(body: dict[str, Any]) -> ResponseWithStatus:
    products = get_context().products
    result, error, status = products.duplicate_draft_from_index(
        body.get("draft_id", "")
    )
    return (error or result), status


def save_draft_payload(body: dict[str, Any]) -> ResponseWithStatus:
    products = get_context().products
    draft = body.get("draft") if isinstance(body.get("draft"), dict) else body
    try:
        resolved_draft = _resolve_draft_category_pairs(draft)
    except (RuntimeError, TimeoutError, ValueError) as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_code": "OZON_CATEGORY_PAIR_RESOLVE_FAILED",
        }, 400
    result, error, status = products.save_draft_detail(resolved_draft)
    return (error or result), status


def delete_products_payload(body: dict[str, Any]) -> ResponseWithStatus:
    products = get_context().products
    product_ids = (
        body.get("product_ids")
        if isinstance(body.get("product_ids"), list)
        else []
    )
    result = products.delete_products_from_index(product_ids)
    return result, 200 if result.get("ok") else 400


def delete_draft_payload(body: dict[str, Any]) -> ResponseWithStatus:
    products = get_context().products
    draft_ids = body.get("draft_ids")
    if draft_ids is None:
        draft_ids = body.get("draftIds")
    if draft_ids is None:
        draft_ids = body.get("draft_id", "") or body.get("draftId", "")
    result = products.delete_draft_from_index(draft_ids)
    return result, 200 if result.get("ok") else 404


def import_upcs_payload(body: dict[str, Any]) -> ResponseWithStatus:
    database = get_context().db
    values = body.get("values")
    if not isinstance(values, list):
        return {"ok": False, "error": "values 必须是 UPC 数组"}, 400
    added = database.import_upcs(values)
    return {
        "ok": True,
        "imported": added,
        "upcPool": database.upc_pool_stats(),
    }, 200


__all__ = [
    "assign_upc",
    "calculate_price",
    "delete_draft_payload",
    "delete_products_payload",
    "duplicate_draft_payload",
    "import_upcs_payload",
    "load_draft_payload",
    "load_product_payload",
    "save_draft_payload",
    "save_product_payload",
]
