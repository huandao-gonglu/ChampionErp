from __future__ import annotations

from typing import Any

from erp_web.context import get_context
from erp_web.runtime_units.image_pool import current_image_pool, current_source_images
from erp_web.runtime_units.product_store import (
    delete_draft_from_index,
    delete_products_from_index,
    load_draft_detail_from_index,
    load_drafts_index,
    load_product_from_index,
    load_products_index,
    save_draft_detail,
    save_product_profile,
)
from erp_web.schemas.api import ApiResponse
from erp_web.schemas.product import Product

ResponseWithStatus = tuple[ApiResponse, int]


def save_product_payload(body: dict[str, Any]) -> ApiResponse:
    product: Product = save_product_profile(body.get("product", {}))
    return {
        "ok": True,
        "product": product,
        "productsIndex": load_products_index(),
        "draftsIndex": load_drafts_index(),
        "imagePool": current_image_pool(product),
    }


def load_product_payload(body: dict[str, Any]) -> ApiResponse:
    product = load_product_from_index(body.get("product_id", ""), body.get("product_file_path", ""))
    return {
        "ok": True,
        "product": product,
        "productsIndex": load_products_index(),
        "draftsIndex": load_drafts_index(),
        "imagePool": current_image_pool(product),
        "sourceImages": current_source_images(product),
    }


def load_draft_payload(body: dict[str, Any]) -> ResponseWithStatus:
    result, error, status = load_draft_detail_from_index(body.get("draft_id", "") or body.get("draftId", ""))
    return (error or result), status


def save_draft_payload(body: dict[str, Any]) -> ResponseWithStatus:
    draft = body.get("draft") if isinstance(body.get("draft"), dict) else body
    result, error, status = save_draft_detail(draft)
    return (error or result), status


def delete_products_payload(body: dict[str, Any]) -> ResponseWithStatus:
    result = delete_products_from_index(body.get("product_ids") if isinstance(body.get("product_ids"), list) else [])
    return result, 200 if result.get("ok") else 400


def delete_draft_payload(body: dict[str, Any]) -> ResponseWithStatus:
    draft_ids = body.get("draft_ids")
    if draft_ids is None:
        draft_ids = body.get("draftIds")
    if draft_ids is None:
        draft_ids = body.get("draft_id", "") or body.get("draftId", "")
    result = delete_draft_from_index(draft_ids)
    return result, 200 if result.get("ok") else 404


def import_upcs_payload(body: dict[str, Any]) -> ResponseWithStatus:
    values = body.get("values")
    if not isinstance(values, list):
        return {"ok": False, "error": "values 必须是 UPC 数组"}, 400
    added = get_context().db.import_upcs(values)
    return {
        "ok": True,
        "imported": added,
        "upcPool": get_context().db.upc_pool_stats(),
    }, 200


__all__ = [
    "delete_draft_payload",
    "delete_products_payload",
    "import_upcs_payload",
    "load_draft_payload",
    "load_product_payload",
    "save_draft_payload",
    "save_product_payload",
]
