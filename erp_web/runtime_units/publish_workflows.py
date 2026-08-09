"""发布用例编排。

HTTP facade 只负责传入请求字典；本模块拥有预检、payload 预览、真实发布与
队列准入。所有入口都先解析平台注册的发布适配器，未实现平台不会写日志、
生成 artifact 或创建发布任务。
"""

from __future__ import annotations

from typing import Any

from erp_web.context import get_context
from erp_web.schemas.api import ApiResponse

from . import publish_logs_runtime
from .collect_helpers import collect_time_iso
from .draft_publish_context import (
    load_required_draft_publish_context,
    save_draft_precheck_result,
)
from .publish_adapter import (
    get_publishing_bus,
    publishing_adapter_for,
    unsupported_publish_response,
)
from .publish_mercadolibre import (
    mercadolibre_close_remote_item,
    mercadolibre_real_publish,
)
from .runtime_api import publish_product

ResponseWithStatus = tuple[ApiResponse, int]


def _requested_platform(body: dict[str, Any], default: str = "") -> str:
    return str(body.get("platform") or default).strip().lower()


def _unsupported_if_explicit(
    body: dict[str, Any],
    *,
    default: str = "",
) -> ResponseWithStatus | None:
    platform = _requested_platform(body, default)
    if platform and publishing_adapter_for(platform) is None:
        return unsupported_publish_response(platform), 501
    return None


def _unsupported_context_response(context: dict[str, Any]) -> ResponseWithStatus | None:
    platform = str(context["platform"]).strip().lower()
    if publishing_adapter_for(platform) is not None:
        return None
    return {
        **unsupported_publish_response(platform),
        "draft": context.get("draft") or {},
        "target": context.get("target") or {},
    }, 501


def precheck_publish_payload(body: dict[str, Any]) -> ResponseWithStatus:
    unsupported = _unsupported_if_explicit(body)
    if unsupported:
        return unsupported
    context, error_response, status = load_required_draft_publish_context(body)
    if error_response:
        return error_response, status
    unsupported = _unsupported_context_response(context)
    if unsupported:
        return unsupported
    config = get_context().config.load_store_config()
    platform = str(context["platform"])
    adapter = publishing_adapter_for(platform)
    if adapter is None:
        return unsupported_publish_response(platform), 501
    context["product"] = adapter.prepare_product(context["product"], config)
    result = adapter.validate_draft(context["product"], config)
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
    unsupported = _unsupported_if_explicit(body)
    if unsupported:
        return unsupported
    context, error_response, status = load_required_draft_publish_context(body)
    if error_response:
        return error_response, status
    unsupported = _unsupported_context_response(context)
    if unsupported:
        return unsupported
    platform = str(context["platform"])
    adapter = publishing_adapter_for(platform)
    if adapter is None:
        return unsupported_publish_response(platform), 501
    config = get_context().config.load_store_config()
    context["product"] = adapter.prepare_product(context["product"], config)
    precheck = adapter.validate_draft(context["product"], config)
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
    try:
        payload = publish_logs_runtime._sanitize_for_log(
            adapter.build_payload(context["product"], config)
        )
        payload_path, _ = publish_logs_runtime.append_platform_publish_log(
            context["product"],
            platform,
            "payload_preview",
            collect_time_iso(),
            payload,
            {"ok": True, "status": "payload_preview"},
            next_action="仅预览 payload，未调用真实发布",
        )
        return {
            "ok": True,
            "platform": platform,
            "site": context["site"],
            "target": context["target"],
            "status": "preview_only",
            "payload": payload,
            "path": payload_path,
            "draft": saved["draft"],
            "productContext": saved["productContext"],
            "productsIndex": saved["productsIndex"],
            "draftsIndex": saved["draftsIndex"],
        }, 200
    except Exception as exc:
        return {
            "ok": False,
            "platform": platform,
            "site": context["site"],
            "target": context["target"],
            "error": str(exc),
        }, 400


def publish_product_payload(body: dict[str, Any]) -> ResponseWithStatus:
    unsupported = _unsupported_if_explicit(body, default="mercadolibre")
    if unsupported:
        return unsupported
    platform = _requested_platform(body, "mercadolibre")
    product, error_response, status = (
        get_context().products.load_required_product_from_body(body)
    )
    if error_response:
        return error_response, status
    try:
        result = publish_product(
            product,
            platform,
            get_context().config.load_store_config(),
        )
        return result, 200 if result.get("ok") else 400
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400


def confirm_mercadolibre_real_publish(body: dict[str, Any]) -> ResponseWithStatus:
    product, error_response, status = (
        get_context().products.load_required_product_from_body(body)
    )
    if error_response:
        return error_response, status
    confirm = bool(body.get("confirm_real_publish") or body.get("confirm"))
    try:
        result = mercadolibre_real_publish(product, confirm)
        return result, 200 if result.get("ok") else 400
    except Exception as exc:
        return {
            "ok": False,
            "status": "real_publish_failed",
            "error": str(exc),
        }, 400


def close_mercadolibre_item(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        result = mercadolibre_close_remote_item(
            str(body.get("item_id") or body.get("id") or "")
        )
        return result, 200 if result.get("ok") else 400
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400


def enqueue_publish_job(body: dict[str, Any]) -> ResponseWithStatus:
    unsupported = _unsupported_if_explicit(body)
    if unsupported:
        return unsupported
    context, error_response, status = load_required_draft_publish_context(body)
    if error_response:
        return error_response, status
    unsupported = _unsupported_context_response(context)
    if unsupported:
        return unsupported
    product = context["product"]
    platforms = [str(context["platform"])]
    eligible_platforms = get_context().products.publish_queue_platforms(
        product,
        platforms,
    )
    rejected_platforms = [
        platform for platform in platforms if platform not in eligible_platforms
    ]
    if not eligible_platforms:
        return {
            "ok": False,
            "error": "当前草稿目标未通过发布队列准入：请先完成发布预检。",
            "error_code": "PUBLISH_QUEUE_NOT_READY",
            "eligible_platforms": [],
            "rejected_platforms": rejected_platforms,
            "workflow_statuses": (
                get_context()
                .products.sync_product_workflow_statuses(product)
                .get("workflow_statuses")
                or {}
            ),
            "draft": context["draft"],
            "target": context["target"],
        }, 400
    try:
        result = get_publishing_bus().enqueue(
            product,
            eligible_platforms,
            targets={
                str(context["platform"]): {
                    "draft_id": str(context["draft"].get("draft_id") or ""),
                    "site": str(context["site"] or ""),
                    "product_id": str(product.get("product_id") or ""),
                }
            },
        )
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
