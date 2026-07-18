from __future__ import annotations

from typing import Any

from erp_web.runtime_units import publish_logs_runtime as publish_log_helpers
from erp_web.runtime_units.category_store import read_json, write_json
from erp_web.runtime_units.collect_helpers import collect_time_iso
from erp_web.runtime_units.product_store import (
    load_drafts_index,
    load_products_index,
    load_store_config,
    load_required_product_from_body,
    publish_queue_platforms,
    save_product,
    sync_product_workflow_statuses,
)
from erp_web.runtime_units.publish_adapter import PUBLISHING_BUS
from erp_web.runtime_units.publish_logs_runtime import append_ml_publish_log
from erp_web.runtime_units.publish_mercadolibre import (
    build_mercadolibre_payload_preview,
    mercadolibre_close_remote_item,
    mercadolibre_real_publish,
)
from erp_web.runtime_units.publish_validation import apply_precheck_to_product, validate_platform_draft
from erp_web.runtime_units.runtime_api import publish_product
from erp_web.runtime_units.runtime_common import OUTPUT_DIR, PLATFORMS
from erp_web.schemas.api import ApiResponse
from erp_web.schemas.product import Product

ResponseWithStatus = tuple[ApiResponse, int]


def precheck_publish_payload(body: dict[str, Any]) -> ResponseWithStatus:
    product, error_response, status = load_required_product_from_body(body)
    if error_response:
        return error_response, status
    config = load_store_config()
    platforms = body.get("platforms") or []
    if isinstance(platforms, str):
        platforms = [platforms]
    platforms = [str(item).strip().lower() for item in platforms if str(item).strip()]
    if not platforms:
        platforms = [str(body.get("platform") or "mercadolibre").strip().lower()]
    results: dict[str, Any] = {}
    updated = product
    for platform in platforms:
        result = validate_platform_draft(updated, platform, config)
        updated = apply_precheck_to_product(updated, platform, result, status="ready" if result.get("ok") else "not_ready")
        results[platform] = result
    saved: Product = save_product(updated)
    return {
        "ok": True,
        "platform": platforms[0] if len(platforms) == 1 else "",
        "platforms": results,
        "product": saved,
        "productsIndex": load_products_index(),
        "draftsIndex": load_drafts_index(),
    }, 200


def preview_publish_payload(body: dict[str, Any]) -> ResponseWithStatus:
    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    product, error_response, status = load_required_product_from_body(body)
    if error_response:
        return error_response, status
    if platform not in PLATFORMS:
        return {"ok": False, "error": "不支持的平台"}, 400
    if platform != "mercadolibre":
        return {
            "ok": True,
            "platform": platform,
            "status": "pending_real_interface",
            "message": "payload 待真实接口完善",
            "payload": {"platform": platform, "message": "payload 待真实接口完善"},
        }, 200
    try:
        payload = publish_log_helpers._sanitize_for_log(build_mercadolibre_payload_preview(product, load_store_config()))
        path = OUTPUT_DIR / "last_mercadolibre_payload.json"
        write_json(path, payload)
        append_ml_publish_log(
            product,
            "payload_preview",
            collect_time_iso(),
            payload,
            {"ok": True, "status": "payload_preview", "path": str(path)},
            "",
            "",
            {},
            "仅预览 payload，未调用真实发布",
        )
        return {"ok": True, "platform": platform, "status": "preview_only", "payload": payload, "path": str(path)}, 200
    except Exception as exc:
        path = OUTPUT_DIR / "last_mercadolibre_payload.json"
        if path.exists():
            return {
                "ok": True,
                "platform": platform,
                "status": "file_fallback",
                "payload": read_json(path, {}),
                "path": str(path),
                "warning": str(exc),
            }, 200
        return {"ok": False, "platform": platform, "error": str(exc)}, 400


def publish_product_payload(body: dict[str, Any]) -> ResponseWithStatus:
    platform = body.get("platform", "mercadolibre")
    product, error_response, status = load_required_product_from_body(body)
    if error_response:
        return error_response, status
    try:
        result = publish_product(product, platform, load_store_config())
        return result, 200 if result.get("ok") else 400
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400


def confirm_mercadolibre_real_publish(body: dict[str, Any]) -> ResponseWithStatus:
    product, error_response, status = load_required_product_from_body(body)
    if error_response:
        return error_response, status
    confirm = bool(body.get("confirm_real_publish") or body.get("confirm"))
    try:
        result = mercadolibre_real_publish(product, confirm)
        return result, 200 if result.get("ok") else 400
    except Exception as exc:
        return {"ok": False, "status": "real_publish_failed", "error": str(exc)}, 400


def close_mercadolibre_item(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        result = mercadolibre_close_remote_item(str(body.get("item_id") or body.get("id") or ""))
        return result, 200 if result.get("ok") else 400
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400


def enqueue_publish_job(body: dict[str, Any]) -> ResponseWithStatus:
    product, error_response, status = load_required_product_from_body(body)
    if error_response:
        return error_response, status
    platforms = body.get("platforms") or []
    if isinstance(platforms, str):
        platforms = [platforms]
    platforms = [str(item).strip() for item in platforms if str(item).strip()]
    try:
        eligible_platforms = publish_queue_platforms(product, platforms)
        rejected_platforms = [platform for platform in platforms if platform not in eligible_platforms]
        if not eligible_platforms:
            return {
                "ok": False,
                "error": "当前商品未通过发布队列准入：请先把草稿推进到“校验通过”。",
                "error_code": "PUBLISH_QUEUE_NOT_READY",
                "eligible_platforms": [],
                "rejected_platforms": rejected_platforms,
                "workflow_statuses": (sync_product_workflow_statuses(product).get("workflow_statuses") or {}),
            }, 400
        result = PUBLISHING_BUS.enqueue(product, eligible_platforms, load_store_config())
        result["eligible_platforms"] = eligible_platforms
        result["rejected_platforms"] = rejected_platforms
        return result, 200
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400


__all__ = [
    "close_mercadolibre_item",
    "confirm_mercadolibre_real_publish",
    "enqueue_publish_job",
    "precheck_publish_payload",
    "preview_publish_payload",
    "publish_product_payload",
]
