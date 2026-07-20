from __future__ import annotations

from typing import Any

from erp_web.runtime_units import publish_logs_runtime as publish_log_helpers
from erp_web.runtime_units.category_store import read_json, write_json
from erp_web.runtime_units.collect_helpers import collect_time_iso
from erp_web.runtime_units.product_store import (
    load_store_config,
    load_required_product_from_body,
    publish_queue_platforms,
    sync_product_workflow_statuses,
)
from erp_web.runtime_units.draft_publish_context import load_required_draft_publish_context, save_draft_precheck_result
from erp_web.runtime_units.publish_adapter import PUBLISHING_BUS
from erp_web.runtime_units.publish_logs_runtime import append_ml_publish_log
from erp_web.runtime_units.publish_mercadolibre import (
    build_mercadolibre_payload_preview,
    mercadolibre_close_remote_item,
    mercadolibre_real_publish,
)
from erp_web.runtime_units.publish_validation import validate_platform_draft
from erp_web.runtime_units.runtime_api import publish_product
from erp_web.runtime_units.runtime_common import OUTPUT_DIR, PLATFORMS
from erp_web.schemas.api import ApiResponse

ResponseWithStatus = tuple[ApiResponse, int]


def precheck_publish_payload(body: dict[str, Any]) -> ResponseWithStatus:
    context, error_response, status = load_required_draft_publish_context(body)
    if error_response:
        return error_response, status
    config = load_store_config()
    platform = str(context["platform"])
    result = validate_platform_draft(context["product"], platform, config)
    saved = save_draft_precheck_result(context, result)
    return {
        "ok": True,
        "platform": platform,
        "site": context["site"],
        "target": context["target"],
        "targets": context["targets"],
        "platforms": {platform: result},
        "draft": saved["draft"],
        "productContext": saved["productContext"],
        "productsIndex": saved["productsIndex"],
        "draftsIndex": saved["draftsIndex"],
    }, 200


def preview_publish_payload(body: dict[str, Any]) -> ResponseWithStatus:
    context, error_response, status = load_required_draft_publish_context(body)
    if error_response:
        return error_response, status
    platform = str(context["platform"])
    if platform not in PLATFORMS:
        return {"ok": False, "error": "不支持的平台"}, 400
    config = load_store_config()
    precheck = validate_platform_draft(context["product"], platform, config)
    saved = save_draft_precheck_result(context, precheck)
    if not precheck.get("ok"):
        return {
            "ok": False,
            "platform": platform,
            "site": context["site"],
            "target": context["target"],
            "status": "precheck_failed",
            "error": "发布前预检未通过，已停止生成 payload。",
            "precheck": precheck,
            "draft": saved["draft"],
            "productContext": saved["productContext"],
            "productsIndex": saved["productsIndex"],
            "draftsIndex": saved["draftsIndex"],
        }, 400
    if platform != "mercadolibre":
        return {
            "ok": True,
            "platform": platform,
            "site": context["site"],
            "target": context["target"],
            "status": "pending_real_interface",
            "message": "payload 待真实接口完善",
            "payload": {"platform": platform, "message": "payload 待真实接口完善"},
            "draft": saved["draft"],
            "productContext": saved["productContext"],
            "productsIndex": saved["productsIndex"],
            "draftsIndex": saved["draftsIndex"],
        }, 200
    try:
        payload = publish_log_helpers._sanitize_for_log(build_mercadolibre_payload_preview(context["product"], config))
        path = OUTPUT_DIR / "last_mercadolibre_payload.json"
        write_json(path, payload)
        append_ml_publish_log(
            context["product"],
            "payload_preview",
            collect_time_iso(),
            payload,
            {"ok": True, "status": "payload_preview", "path": str(path)},
            "",
            "",
            {},
            "仅预览 payload，未调用真实发布",
        )
        return {
            "ok": True,
            "platform": platform,
            "site": context["site"],
            "target": context["target"],
            "status": "preview_only",
            "payload": payload,
            "path": str(path),
            "draft": saved["draft"],
            "productContext": saved["productContext"],
            "productsIndex": saved["productsIndex"],
            "draftsIndex": saved["draftsIndex"],
        }, 200
    except Exception as exc:
        path = OUTPUT_DIR / "last_mercadolibre_payload.json"
        if path.exists():
            return {
                "ok": True,
                "platform": platform,
                "site": context["site"],
                "target": context["target"],
                "status": "file_fallback",
                "payload": read_json(path, {}),
                "path": str(path),
                "warning": str(exc),
                "draft": saved["draft"],
                "productContext": saved["productContext"],
                "productsIndex": saved["productsIndex"],
                "draftsIndex": saved["draftsIndex"],
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
    context, draft_error_response, draft_status = load_required_draft_publish_context(body)
    if draft_error_response:
        return draft_error_response, draft_status
    product = context["product"]
    platforms = [str(context["platform"])]
    try:
        eligible_platforms = publish_queue_platforms(product, platforms)
        rejected_platforms = [platform for platform in platforms if platform not in eligible_platforms]
        if not eligible_platforms:
            return {
                "ok": False,
                "error": "当前草稿目标未通过发布队列准入：请先完成发布预检。",
                "error_code": "PUBLISH_QUEUE_NOT_READY",
                "eligible_platforms": [],
                "rejected_platforms": rejected_platforms,
                "workflow_statuses": (sync_product_workflow_statuses(product).get("workflow_statuses") or {}),
                "draft": context["draft"],
                "target": context["target"],
            }, 400
        result = PUBLISHING_BUS.enqueue(product, eligible_platforms, load_store_config())
        result["eligible_platforms"] = eligible_platforms
        result["rejected_platforms"] = rejected_platforms
        result["draft_id"] = str(context["draft"].get("draft_id") or "")
        result["target"] = context["target"]
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
