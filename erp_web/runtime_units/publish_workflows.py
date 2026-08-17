"""发布用例编排。

HTTP facade 只负责传入请求字典；本模块拥有预检、payload 预览、真实发布与
队列准入。所有入口都先解析平台注册的发布适配器，未实现平台不会写日志、
生成 artifact 或创建发布任务。
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid
from typing import Any

from pydantic import ValidationError

from erp_web.context import get_context
from erp_web.schemas.api import ApiResponse
from erp_web.schemas.publish_capabilities import (
    ProductPublishRequest,
    ProductPublishValidateRequest,
    PublishRequestConfirmation,
)
from erp_web.services.capability_errors import BusinessCapabilityError

from . import publish_logs_runtime
from .collect_helpers import collect_time_iso
from .draft_publish_context import (
    load_required_draft_publish_context,
    save_draft_precheck_result,
)
from .publish_adapter import (
    publishing_adapter_for,
    unsupported_publish_response,
)
from .publish_capabilities import (
    evaluate_publish_validation,
    request_product_publish,
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
    """人工 payload 预览：与发布确认共用同一次评估/digest 算法。"""

    unsupported = _unsupported_if_explicit(body)
    if unsupported:
        return unsupported
    context, error_response, status = load_required_draft_publish_context(body)
    if error_response:
        return error_response, status
    unsupported = _unsupported_context_response(context)
    if unsupported:
        return unsupported
    try:
        evaluation = evaluate_publish_validation(
            ProductPublishValidateRequest(
                draft_id=str(body.get("draft_id") or body.get("draftId") or ""),
                platform=str(context["platform"]),
                site=str(context["site"]),
            )
        )
    except BusinessCapabilityError as exc:
        return {"ok": False, "error": str(exc), "error_code": exc.code}, 400
    result = evaluation.result
    saved = evaluation.saved_precheck
    platform = evaluation.platform
    site = evaluation.site
    if not result.passed:
        return {
            "ok": False,
            "platform": platform,
            "site": site,
            "target": context["target"],
            "status": "precheck_failed",
            "error": "发布前预检未通过，已停止生成 payload。",
            "precheck": {
                "errors": [item.model_dump() for item in result.errors],
                "warnings": [item.model_dump() for item in result.warnings],
            },
            "draft": saved.get("draft") or context.get("draft") or {},
            "productContext": saved.get("productContext")
            or context.get("productContext")
            or {},
            "productsIndex": saved.get("productsIndex"),
            "draftsIndex": saved.get("draftsIndex"),
        }, 400
    try:
        payload = publish_logs_runtime._sanitize_for_log(
            dict(evaluation.approved_payload or {})
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
    except Exception as exc:
        return {
            "ok": False,
            "platform": platform,
            "site": site,
            "target": context["target"],
            "error": str(exc),
        }, 400
    return {
        "ok": True,
        "platform": platform,
        "site": site,
        "target": context["target"],
        "status": "preview_only",
        "payload": payload,
        # sanitized payload、摘要与 digest 一并返回，供页面展示与人工确认。
        "summary": result.summary.model_dump(),
        "validation_digest": result.validation_digest,
        "warnings": [item.model_dump() for item in result.warnings],
        "path": payload_path,
        "draft": saved.get("draft") or context.get("draft") or {},
        "productContext": saved.get("productContext")
        or context.get("productContext")
        or {},
        "productsIndex": saved.get("productsIndex"),
        "draftsIndex": saved.get("draftsIndex"),
    }, 200


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
    """人工确认入队：必须携带显式确认与预览 digest。

    可信 idempotency key 与确认时间只由服务端生成；客户端不能提交幂等键，
    也不能伪造 ``confirmed_at``。入队统一走 ``request_product_publish()``，
    由其重校验 digest 并写入 ``approved_publications``。
    """

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
    draft_id = str(context["draft"].get("draft_id") or "")
    site = str(context["site"] or "")
    validation_digest = str(body.get("validation_digest") or "").strip().lower()
    confirmed = bool(
        body.get("confirm") or body.get("confirmed") or body.get("confirm_publish")
    )
    if not confirmed or not validation_digest:
        return {
            "ok": False,
            "error": "发布任务需要显式确认（confirm）与预览返回的 validation_digest。",
            "error_code": "PUBLISH_CONFIRMATION_REQUIRED",
            "draft_id": draft_id,
            "target": context["target"],
        }, 400

    # 服务端生成可信幂等键；task_id 复用同一 key 作为确认归属标识。
    idempotency_key = f"manual:{uuid.uuid4().hex}"
    try:
        request = ProductPublishRequest(
            draft_id=draft_id,
            platform=platform,
            site=site,
            idempotency_key=idempotency_key,
            confirmation=PublishRequestConfirmation(
                task_id=idempotency_key,
                step_id="manual_enqueue",
                validation_digest=validation_digest,
                confirmed_at=datetime.now(timezone.utc),
            ),
        )
    except ValidationError as exc:
        return {
            "ok": False,
            "error": f"发布请求无效：{exc}",
            "error_code": "PUBLISH_REQUEST_INVALID",
            "draft_id": draft_id,
            "target": context["target"],
        }, 400

    try:
        result = request_product_publish(request)
    except BusinessCapabilityError as exc:
        status_code = (
            409
            if exc.code
            in {
                "PUBLISH_CONFIRMATION_STALE",
                "PUBLISH_IDEMPOTENCY_CONFLICT",
            }
            else 400
        )
        return {
            "ok": False,
            "error": str(exc),
            "error_code": exc.code,
            "draft_id": draft_id,
            "target": context["target"],
        }, status_code

    return {
        "ok": True,
        "job_id": result.job_id,
        "status": result.status,
        "platform": result.platform,
        "draft_id": result.draft_id,
        "idempotent_replay": result.idempotent_replay,
        "eligible_platforms": [result.platform],
        "rejected_platforms": [],
        "target": context["target"],
    }, 200


__all__ = [
    "close_mercadolibre_item",
    "confirm_mercadolibre_real_publish",
    "enqueue_publish_job",
    "precheck_publish_payload",
    "preview_publish_payload",
    "publish_product_payload",
]
