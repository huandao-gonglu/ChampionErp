from __future__ import annotations

"""发布校验与确认后提交的薄 Capability adapter。"""

from dataclasses import dataclass
import hmac
from typing import Any, Protocol

from erp_web.context import AppContext, get_context
from erp_web.product_model import normalize_draft_image_refs
from erp_web.runtime_units.draft_publish_context import (
    load_required_draft_publish_context,
    save_draft_precheck_result,
)
from erp_web.runtime_units.publish_adapter import (
    publishing_adapter_for,
)
from erp_web.runtime_units.publish_confirmation import (
    canonical_publish_digest,
    resolve_publish_store_binding,
)
from erp_web.schemas.publish_capabilities import (
    ProductPublishRequest,
    ProductPublishRequestResult,
    ProductPublishSummary,
    ProductPublishValidateRequest,
    ProductPublishValidationResult,
    PublishValidationIssue,
)
from erp_web.services.capability_errors import BusinessCapabilityError


class PublishingBusLike(Protocol):
    def recover_publish_job(
        self,
        *,
        idempotency_key: str,
        product_id: str,
        draft_id: str,
        validation_digest: str,
        platform: str,
        site: str,
    ) -> dict[str, Any] | None:
        ...

    def enqueue(
        self,
        product: dict[str, Any],
        platforms: list[str],
        *,
        targets: dict[str, dict[str, Any]],
        idempotency_key: str,
        approved_publications: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class _ValidationEvaluation:
    result: ProductPublishValidationResult
    platform: str
    site: str
    context: dict[str, Any]
    prepared_product: dict[str, Any]
    approved_payload: dict[str, Any] | None
    # save_draft_precheck_result 的落盘结果（draft/上下文/索引快照）。
    saved_precheck: dict[str, Any]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _issue(
    raw: Any,
    *,
    severity: str,
    default_code: str,
) -> PublishValidationIssue:
    item = raw if isinstance(raw, dict) else {}
    message = _text(item.get("message") if item else raw) or "发布校验失败"
    normalized_severity = "warning" if _text(item.get("severity")).lower() == "warning" or severity == "warning" else "error"
    return PublishValidationIssue(
        code=_text(item.get("code")) or default_code,
        field=_text(item.get("field")),
        message=message,
        severity=normalized_severity,
        next_action=_text(item.get("next_action")),
    )


def _canonical_digest(
    *,
    product_id: str,
    draft_id: str,
    platform: str,
    site: str,
    store_identity: str,
    payload: dict[str, Any],
) -> str:
    try:
        return canonical_publish_digest(
            product_id=product_id,
            draft_id=draft_id,
            platform=platform,
            site=site,
            store_identity=store_identity,
            payload=payload,
        )
    except (TypeError, ValueError) as exc:
        raise BusinessCapabilityError(
            "PUBLISH_PAYLOAD_NOT_DETERMINISTIC",
            f"发布 payload 无法稳定序列化：{exc}",
        ) from exc


def _summary(
    context: dict[str, Any],
    prepared_product: dict[str, Any],
    config: dict[str, Any],
) -> ProductPublishSummary:
    platform = _text(context.get("platform")).lower()
    drafts = (
        prepared_product.get("drafts")
        if isinstance(prepared_product.get("drafts"), dict)
        else {}
    )
    draft = drafts.get(platform) if isinstance(drafts.get(platform), dict) else {}
    selected = (
        draft.get("selected_pricing")
        if isinstance(draft.get("selected_pricing"), dict)
        else {}
    )
    applied = (
        selected.get("applied_price")
        if isinstance(selected.get("applied_price"), dict)
        else {}
    )
    try:
        store_binding = resolve_publish_store_binding(platform, config)
    except ValueError as exc:
        raise BusinessCapabilityError(
            "PUBLISH_STORE_IDENTITY_MISSING",
            "当前店铺缺少可绑定发布确认的稳定账号身份，请先完成店铺授权测试。",
        ) from exc
    return ProductPublishSummary(
        product_id=_text(prepared_product.get("product_id")),
        draft_id=_text(context.get("draft", {}).get("draft_id")),
        platform=platform,
        site=_text(context.get("site")),
        store_identity=store_binding.identity,
        store_label=store_binding.label,
        title=_text(draft.get("title")),
        category_id=_text(draft.get("category_id")),
        listing_currency=_text(
            applied.get("currency") or draft.get("listing_currency")
        ).upper(),
        price=_text(applied.get("amount") or draft.get("price")),
        stock=_text(draft.get("stock")),
        image_count=len(normalize_draft_image_refs(draft.get("images"))),
    )


def _load_context(
    request: ProductPublishValidateRequest,
    *,
    context: AppContext | None = None,
) -> dict[str, Any]:
    body = {
        "draft_id": request.draft_id,
        "platform": request.platform,
        "site": request.site,
    }
    try:
        loaded, error, _status = load_required_draft_publish_context(
            body,
            context=context,
        )
    except Exception as exc:
        raise BusinessCapabilityError(
            "PUBLISH_CONTEXT_LOAD_FAILED",
            f"发布上下文读取失败：{exc}",
            retryable=True,
        ) from exc
    if error is not None:
        raise BusinessCapabilityError(
            _text(error.get("error_code")) or "PUBLISH_CONTEXT_INVALID",
            _text(error.get("error")) or "发布上下文无效。",
        )
    return loaded


def evaluate_publish_validation(
    request: ProductPublishValidateRequest,
    *,
    context: AppContext | None = None,
) -> _ValidationEvaluation:
    active_context = context or get_context()
    publish_context = _load_context(request, context=active_context)
    platform = _text(publish_context.get("platform")).lower()
    adapter = publishing_adapter_for(platform)
    if adapter is None:
        raise BusinessCapabilityError(
            "PUBLISH_PLATFORM_UNSUPPORTED",
            f"平台 {platform or request.platform} 尚未接入发布能力。",
        )
    try:
        config = active_context.config.load_store_config()
        prepared_product = adapter.prepare_product(
            publish_context["product"],
            config,
        )
        if not isinstance(prepared_product, dict):
            raise TypeError("平台 adapter 返回的商品不是对象")
        precheck = adapter.validate_draft(prepared_product, config)
        if not isinstance(precheck, dict):
            raise TypeError("平台 adapter 返回的校验结果不是对象")
    except Exception as exc:
        raise BusinessCapabilityError(
            "PUBLISH_VALIDATION_EXECUTION_FAILED",
            f"发布校验执行失败：{exc}",
            retryable=True,
        ) from exc

    errors = [
        _issue(item, severity="error", default_code="PUBLISH_VALIDATION_FAILED")
        for item in precheck.get("errors") or []
    ]
    warnings = [
        _issue(item, severity="warning", default_code="PUBLISH_VALIDATION_WARNING")
        for item in precheck.get("warnings") or []
    ]
    payload: dict[str, Any] | None = None
    if not errors:
        try:
            built = adapter.build_payload(prepared_product, config)
            if not isinstance(built, dict):
                raise TypeError("payload 不是对象")
            payload = built
            payload_errors = adapter.validate_payload(payload, config) or []
            errors.extend(
                _issue(
                    {
                        "code": "PUBLISH_PAYLOAD_INVALID",
                        "field": "payload",
                        "message": _text(item),
                        "severity": "error",
                    },
                    severity="error",
                    default_code="PUBLISH_PAYLOAD_INVALID",
                )
                for item in payload_errors
            )
        except BusinessCapabilityError:
            raise
        except Exception as exc:
            errors.append(
                _issue(
                    {
                        "code": "PUBLISH_PAYLOAD_BUILD_FAILED",
                        "field": "payload",
                        "message": f"发布 payload 构造失败：{exc}",
                    },
                    severity="error",
                    default_code="PUBLISH_PAYLOAD_BUILD_FAILED",
                )
            )

    combined_precheck = {
        **precheck,
        "platform": platform,
        "ok": not errors,
        "errors": [item.model_dump() for item in errors],
        "warnings": [item.model_dump() for item in warnings],
    }
    publish_context["product"] = prepared_product
    try:
        saved_precheck = save_draft_precheck_result(
            publish_context,
            combined_precheck,
            context=active_context,
        )
    except Exception as exc:
        raise BusinessCapabilityError(
            "PUBLISH_PRECHECK_PERSIST_FAILED",
            f"发布校验结果保存失败：{exc}",
            retryable=True,
        ) from exc
    summary = _summary(publish_context, prepared_product, config)
    digest = ""
    if not errors and payload is not None:
        digest = _canonical_digest(
            product_id=summary.product_id,
            draft_id=summary.draft_id,
            platform=summary.platform,
            site=summary.site,
            store_identity=summary.store_identity,
            payload=payload,
        )
    result = ProductPublishValidationResult(
        passed=not errors and bool(digest),
        summary=summary,
        errors=errors,
        warnings=warnings,
        validation_digest=digest,
    )
    return _ValidationEvaluation(
        result=result,
        platform=platform,
        site=_text(publish_context.get("site")),
        context=publish_context,
        prepared_product=prepared_product,
        approved_payload=payload,
        saved_precheck=saved_precheck if isinstance(saved_precheck, dict) else {},
    )


def validate_product_publish(
    request: ProductPublishValidateRequest,
    *,
    context: AppContext | None = None,
) -> ProductPublishValidationResult:
    """复用平台 adapter 完成草稿预检、payload 校验与摘要 digest。"""

    return evaluate_publish_validation(request, context=context).result


def request_product_publish(
    request: ProductPublishRequest,
    *,
    publishing_bus: PublishingBusLike | None = None,
    context: AppContext | None = None,
) -> ProductPublishRequestResult:
    """重校验确认 digest 后，以可信幂等键提交 PublishingBus。"""

    validation_request = ProductPublishValidateRequest(
        draft_id=request.draft_id,
        platform=request.platform,
        site=request.site,
    )
    active_context = context or get_context()
    bus = publishing_bus or active_context.publishing_bus
    recovery_context = _load_context(
        validation_request,
        context=active_context,
    )
    recovery_product = (
        recovery_context.get("product")
        if isinstance(recovery_context.get("product"), dict)
        else {}
    )
    try:
        recovered = bus.recover_publish_job(
            idempotency_key=request.idempotency_key,
            product_id=_text(recovery_product.get("product_id")),
            draft_id=request.draft_id,
            validation_digest=request.confirmation.validation_digest,
            platform=_text(recovery_context.get("platform")).lower(),
            site=_text(recovery_context.get("site")),
        )
    except Exception as exc:
        raise BusinessCapabilityError(
            _text(getattr(exc, "code", "")) or "PUBLISH_RECOVERY_FAILED",
            _text(exc) or "发布任务恢复失败。",
            retryable=not bool(_text(getattr(exc, "code", ""))),
        ) from exc
    if recovered is not None:
        job_id = _text(recovered.get("job_id"))
        if not job_id:
            raise BusinessCapabilityError(
                "PUBLISH_JOB_ID_MISSING",
                "PublishingBus 恢复的任务缺少 job_id。",
            )
        return ProductPublishRequestResult(
            job_id=job_id,
            draft_id=request.draft_id,
            platform=_text(recovery_context.get("platform")).lower(),
            status=_text(recovered.get("status")) or "queued",
            idempotent_replay=True,
        )

    evaluation = evaluate_publish_validation(
        validation_request,
        context=active_context,
    )
    if not evaluation.result.passed:
        raise BusinessCapabilityError(
            "PUBLISH_VALIDATION_FAILED",
            "发布条件已不满足，请修复校验错误后重新确认。",
        )
    if not hmac.compare_digest(
        evaluation.result.validation_digest,
        request.confirmation.validation_digest,
    ):
        raise BusinessCapabilityError(
            "PUBLISH_CONFIRMATION_STALE",
            "商品或发布 payload 已变化，原发布确认已失效。",
        )

    refreshed = _load_context(validation_request, context=active_context)
    product = refreshed["product"]
    try:
        eligible = active_context.products.publish_queue_platforms(
            product,
            [evaluation.platform],
        )
    except Exception as exc:
        raise BusinessCapabilityError(
            "PUBLISH_QUEUE_CHECK_FAILED",
            f"发布队列准入检查失败：{exc}",
            retryable=True,
        ) from exc
    if evaluation.platform not in eligible:
        raise BusinessCapabilityError(
            "PUBLISH_QUEUE_NOT_READY",
            "当前草稿未通过发布队列准入。",
        )
    if evaluation.approved_payload is None:
        raise BusinessCapabilityError(
            "PUBLISH_PAYLOAD_MISSING",
            "发布校验未生成可绑定的 payload。",
        )
    try:
        result = bus.enqueue(
            product,
            [evaluation.platform],
            targets={
                evaluation.platform: {
                    "draft_id": request.draft_id,
                    "site": evaluation.site,
                    "product_id": _text(product.get("product_id")),
                }
            },
            idempotency_key=request.idempotency_key,
            approved_publications={
                evaluation.platform: {
                    "payload": evaluation.approved_payload,
                    "validation_digest": evaluation.result.validation_digest,
                    "store_identity": evaluation.result.summary.store_identity,
                }
            },
        )
    except Exception as exc:
        raise BusinessCapabilityError(
            _text(getattr(exc, "code", "")) or "PUBLISH_ENQUEUE_FAILED",
            _text(exc) or "发布任务提交失败。",
            retryable=not bool(_text(getattr(exc, "code", ""))),
        ) from exc
    job_id = _text(result.get("job_id"))
    if not job_id:
        raise BusinessCapabilityError(
            "PUBLISH_JOB_ID_MISSING",
            "PublishingBus 未返回 job_id。",
        )
    return ProductPublishRequestResult(
        job_id=job_id,
        draft_id=request.draft_id,
        platform=evaluation.platform,
        status=_text(result.get("status")) or "queued",
        idempotent_replay=bool(
            result.get("idempotent_replay") or result.get("reused")
        ),
    )


__all__ = [
    "PublishingBusLike",
    "request_product_publish",
    "validate_product_publish",
]
