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
    get_publishing_bus,
    publishing_adapter_for,
    unsupported_publish_response,
)
from .publish_capabilities import (
    prepare_and_evaluate_publish_validation,
    request_product_publish,
)
from .publish_context import prepare_publish_context
from .publish_mercadolibre import mercadolibre_pause_user_product
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
    # 本地图片物化（Yandex/Ozon 复制到公开目录并生成公网 URL）没有平台
    # 外写，必须在预检前执行，否则“非 HTTPS URL”校验会拦住自己的物化路径。
    # Mercado 的素材准备会外写平台（上传图片），仍只允许校验通过后触发。
    if getattr(adapter, "prepare_is_local_only", False):
        context["product"] = adapter.prepare_product(context["product"], config)
    prepared_context = prepare_publish_context(context["product"], platform)
    result = adapter.validate_draft(prepared_context, config)
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
        evaluation = prepare_and_evaluate_publish_validation(
            ProductPublishValidateRequest(
                draft_id=str(body.get("draft_id") or body.get("draftId") or ""),
                platform=str(context["platform"]),
                site=str(context["site"]),
            )
        )
    except BusinessCapabilityError as exc:
        return {"ok": False, "error": str(exc), "error_code": exc.code}, 400
    result = evaluation.result
    # 该受信写入口显式准备平台素材，再把最终 payload 对应的预检结果落盘。
    try:
        saved = save_draft_precheck_result(
            evaluation.context,
            evaluation.precheck,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"发布校验结果保存失败：{exc}",
            "error_code": "PUBLISH_PRECHECK_PERSIST_FAILED",
        }, 500
    if not isinstance(saved, dict):
        saved = {}
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
        "summary": result.summary.model_dump(mode="json", exclude_none=True),
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
    platform = _requested_platform(body)
    if not platform:
        return {
            "ok": False,
            "error": "直接发布必须显式指定 platform。",
            "error_code": "PUBLISH_DIRECT_PLATFORM_REQUIRED",
        }, 400
    if platform == "mercadolibre":
        return {
            "ok": False,
            "error": (
                "Mercado Libre User Products 只能通过预览、人工确认与"
                " PublishingBus 持久队列发布。"
            ),
            "error_code": "MERCADOLIBRE_PUBLISH_BUS_REQUIRED",
        }, 409
    unsupported = _unsupported_if_explicit(body)
    if unsupported:
        return unsupported
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


def pause_mercadolibre_user_product(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        result = mercadolibre_pause_user_product(
            str(body.get("siteless_user_product_id") or "")
        )
        if result.get("ok"):
            return result, 200
        return result, 404 if result.get("error_code") == "MERCADOLIBRE_USER_PRODUCT_NOT_FOUND" else 400
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400


def reconcile_publish_job(body: dict[str, Any]) -> ResponseWithStatus:
    """对未知发布结果做只读远端确认，不重放任何发布写请求。"""

    job_id = str(body.get("job_id") or "").strip()
    platform = _requested_platform(body)
    try:
        result = get_publishing_bus().reconcile_outcome_unknown(
            job_id,
            platform,
        )
        return result, 200
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_code": "PUBLISH_JOB_NOT_FOUND",
        }, 404
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_code": "PUBLISH_RECONCILIATION_NOT_AVAILABLE",
        }, 409
    except RuntimeError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_code": "PUBLISH_RECONCILIATION_CONFLICT",
        }, 409
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_code": "PUBLISH_RECONCILIATION_FAILED",
        }, 502


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
    "pause_mercadolibre_user_product",
    "enqueue_publish_job",
    "precheck_publish_payload",
    "preview_publish_payload",
    "publish_product_payload",
]
