from __future__ import annotations

from typing import Any

from erp_web.context import get_context
from erp_web.runtime_units.draft_category_resolution import (
    resolve_draft_category_pairs as _resolve_draft_category_pairs,
)
from erp_web.runtime_units.image_pool import current_image_pool, current_source_images
from erp_web.schemas.upc_capabilities import UpcAssignRequest
from erp_web.schemas.api import ApiResponse
from erp_web.schemas.product import Product
from erp_web.services import draft_transfer_service, product_transfer_service

ResponseWithStatus = tuple[ApiResponse, int]


def assign_upc(body: dict[str, Any]) -> ResponseWithStatus:
    request = UpcAssignRequest.model_validate(body)
    context = get_context()
    try:
        assignments, saved = context.products.assign_upcs_to_product({"product_id": request.product_id}, request.sku_ids)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "error_code": "UPC_ASSIGN_FAILED"}, 400
    return {"ok": True, "assignments": assignments, "product": saved,
            "productsIndex": context.products.load_products_index(), "imagePool": current_image_pool(saved),
            "upcPool": context.db.upc_pool_stats(), "message": f"已为 {len(assignments)} 个 SKU 分配 UPC"}, 200


def save_product_payload(body: dict[str, Any]) -> ApiResponse:
    products = get_context().products
    product: Product = products.save_product_profile(
        body.get("product", {})
    )
    return {
        "ok": True,
        "product": product,
        "productsIndex": products.load_products_index(),
        "draftsIndex": products.load_drafts_index(scope="all"),
        "imagePool": current_image_pool(product),
    }


def load_product_payload(body: dict[str, Any]) -> ResponseWithStatus:
    products = get_context().products
    product = products.find_indexed_product(
        body.get("product_id", ""),
        body.get("product_file_path", ""),
    )
    if not product:
        return {"ok": False, "error": "商品不存在，请刷新采集箱后重试。"}, 404
    if body.get("editor_only") is True:
        return {"ok": True, "product": product, "imagePool": current_image_pool(product)}, 200
    return {
        "ok": True,
        "product": product,
        "productsIndex": products.load_products_index(),
        "draftsIndex": products.load_drafts_index(scope="all"),
        "imagePool": current_image_pool(product),
        "sourceImages": current_source_images(product),
    }, 200


def load_draft_payload(body: dict[str, Any]) -> ResponseWithStatus:
    products = get_context().products
    result, error, status = products.load_draft_detail_from_index(
        body.get("draft_id", "") or body.get("draftId", ""), editor_only=body.get("editor_only") is True,
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
    with get_context().image_batches.lock:
        get_context().image_batches.assert_draft_idle(str(draft.get("draft_id") or draft.get("draftId") or ""))
        result, error, status = products.save_draft_detail(resolved_draft, user_image_edit=True)
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


def export_products_payload(body: dict[str, Any]) -> ResponseWithStatus:
    context = get_context()
    product_ids = (
        body.get("product_ids")
        if isinstance(body.get("product_ids"), list)
        else []
    )
    return product_transfer_service.export_products(
        context.products,
        context.paths.app_dir,
        product_ids,
    ), 200


def import_products_payload(body: dict[str, Any]) -> ResponseWithStatus:
    context = get_context()
    return product_transfer_service.import_products(
        context.products,
        context.paths.app_dir,
        body,
    ), 200


def export_drafts_archive(body: dict[str, Any]) -> tuple[bytes, str, int, list[str]]:
    context = get_context()
    draft_ids = (
        body.get("draft_ids")
        if isinstance(body.get("draft_ids"), list)
        else []
    )
    return draft_transfer_service.export_drafts_archive(
        context.products,
        context.paths.app_dir,
        draft_ids,
    )


def import_drafts_archive(raw_zip: bytes) -> ResponseWithStatus:
    context = get_context()
    return draft_transfer_service.import_drafts_archive(
        context.products,
        context.paths.app_dir,
        raw_zip,
    ), 200


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
    "delete_draft_payload",
    "delete_products_payload",
    "duplicate_draft_payload",
    "export_drafts_archive",
    "export_products_payload",
    "import_drafts_archive",
    "import_products_payload",
    "import_upcs_payload",
    "load_draft_payload",
    "load_product_payload",
    "save_draft_payload",
    "save_product_payload",
]
