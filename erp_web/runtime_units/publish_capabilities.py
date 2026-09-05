from __future__ import annotations

"""发布校验与确认后提交的薄 Capability adapter。"""

from dataclasses import dataclass
import hmac
from typing import Annotated, Any, Protocol

from erp_web.context import AppContext, get_context
from erp_web.product_model import (
    normalize_draft_image_refs,
    normalize_mercadolibre_sites_to_sell,
)
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
from erp_web.runtime_units.publish_context import prepare_publish_context
from erp_web.schemas.ai_tools import (
    PUBLISH_JOB_TYPE,
    JobReferenceResult,
    TaskApprovalSnapshot,
)
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.publish_capabilities import (
    ProductPublishCapabilityRequest,
    ProductPublishDestination,
    ProductPublishRequest,
    ProductPublishRequestResult,
    ProductPublishSummary,
    ProductPublishSkuSummary,
    ProductPublishValidateRequest,
    ProductPublishValidationResult,
    PublishRequestConfirmation,
    PublishValidationIssue,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.task_approval import verify_execution_approval


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
    # 合并后的预检结果；评估本身是纯计算，是否落盘由调用方决定。
    precheck: dict[str, Any]


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
    payload: dict[str, Any] | None = None,
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
    destinations = ()
    if platform == "mercadolibre" and _text(context.get("site")).upper() == "CBT":
        source_targets = (
            payload.get("sites_to_sell")
            if isinstance(payload, dict) and isinstance(payload.get("sites_to_sell"), list)
            else draft.get("sites_to_sell")
        )
        destinations = tuple(
            ProductPublishDestination(**target)
            for target in normalize_mercadolibre_sites_to_sell(source_targets)
            if target.get("site_id") and target.get("logistic_type")
        )
    sku_summaries = []
    key = f"{platform}:{context.get('site', '')}".lower()
    for row in draft.get("sku_items", []):
        if not row.get("selected"):
            continue
        quote = row.get("pricing", {}).get("targets", {}).get(key, {})
        price = quote.get("applied_price", {})
        sku_summaries.append(ProductPublishSkuSummary(
            sku_id=row["sku_id"], sku=row["sku"], stock=_text(row.get("stock")),
            price=_text(price.get("amount")), currency=_text(price.get("currency")),
            destinations=tuple(ProductPublishDestination(**target) for target in quote.get("sites_to_sell", [])),
        ))
    return ProductPublishSummary(
        sku_items=tuple(sku_summaries), grouping_mode=_text(draft.get("grouping", {}).get("mode")),
        product_id=_text(prepared_product.get("product_id")),
        draft_id=_text(context.get("draft", {}).get("draft_id")),
        platform=platform,
        site=_text(context.get("site")),
        store_identity=store_binding.identity,
        store_label=store_binding.label,
        title=_text(
            payload.get("title")
            if isinstance(payload, dict)
            else ""
        )
        or _text(
            payload.get("family_name")
            if isinstance(payload, dict)
            else ""
        )
        or _text(draft.get("title")),
        category_id=_text(draft.get("category_id")),
        listing_currency=_text(
            applied.get("currency") or draft.get("listing_currency")
        ).upper(),
        price=_text(applied.get("amount") or draft.get("price")),
        stock=_text(draft.get("stock")),
        image_count=len(normalize_draft_image_refs(draft.get("images"))),
        destinations=destinations,
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


def _evaluate_prepared_publish_context(
    publish_context: dict[str, Any],
    *,
    platform: str,
    adapter: Any,
    config: dict[str, Any],
    prepared_context: Any,
    precheck: dict[str, Any],
) -> _ValidationEvaluation:
    """对已确定的商品/类目上下文做纯 payload 编译与 digest 计算。"""

    prepared_product = prepared_context.product
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
            built = adapter.build_payload(prepared_context, config)
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
    summary = _summary(publish_context, prepared_product, config, payload)
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
        precheck=combined_precheck,
    )


def evaluate_publish_validation(
    request: ProductPublishValidateRequest,
    *,
    context: AppContext | None = None,
) -> _ValidationEvaluation:
    """只读地校验当前已持久化发布事实并生成 payload digest。

    该入口不会调用 ``adapter.prepare_product``。Mercado 本地图片上传等素材
    准备只能由显式的 payload 预览写流程触发，避免 ``side_effect=none`` 的
    AI capability 在“校验”期间悄悄写平台和本地商品状态。
    """

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
        prepared_product = publish_context["product"]
        if not isinstance(prepared_product, dict):
            raise TypeError("发布上下文中的商品不是对象")
        # 一次评估只加载一次类目定义；预检与 payload 编译共享该上下文。
        prepared_context = prepare_publish_context(prepared_product, platform)
        precheck = adapter.validate_draft(prepared_context, config)
        if not isinstance(precheck, dict):
            raise TypeError("平台 adapter 返回的校验结果不是对象")
    except Exception as exc:
        raise BusinessCapabilityError(
            "PUBLISH_VALIDATION_EXECUTION_FAILED",
            f"发布校验执行失败：{exc}",
            retryable=True,
        ) from exc

    return _evaluate_prepared_publish_context(
        publish_context,
        platform=platform,
        adapter=adapter,
        config=config,
        prepared_context=prepared_context,
        precheck=precheck,
    )


def prepare_and_evaluate_publish_validation(
    request: ProductPublishValidateRequest,
    *,
    context: AppContext | None = None,
) -> _ValidationEvaluation:
    """显式准备发布素材后生成最终 payload 预览与 digest。

    这是写路径：Mercado 会在这里上传本地图片并持久化 picture ID。为避免
    无效草稿产生外部写入，先用同一份类目定义执行草稿预检；只有没有阻断
    项时才准备素材。准备后通过 ``PreparedPublishContext.with_product`` 复用
    已加载的定义，保证预检与 payload 编译同源。

    Yandex/Ozon 的素材准备只是本地图片物化（没有平台外写），在预检前执行；
    否则“非 HTTPS URL”校验会拦住自己的物化路径，本地图片永远无法发布。
    """

    active_context = context or get_context()
    publish_context = _load_context(request, context=active_context)
    platform = _text(publish_context.get("platform")).lower()
    adapter = publishing_adapter_for(platform)
    if adapter is None:
        raise BusinessCapabilityError(
            "PUBLISH_PLATFORM_UNSUPPORTED",
            f"平台 {platform or request.platform} 尚未接入发布能力。",
        )
    local_only_prepare = bool(getattr(adapter, "prepare_is_local_only", False))
    try:
        config = active_context.config.load_store_config()
        source_product = publish_context["product"]
        if not isinstance(source_product, dict):
            raise TypeError("发布上下文中的商品不是对象")
        if local_only_prepare:
            source_product = adapter.prepare_product(source_product, config)
            if not isinstance(source_product, dict):
                raise TypeError("平台 adapter 返回的商品不是对象")
            publish_context["product"] = source_product
        prepared_context = prepare_publish_context(source_product, platform)
        precheck = adapter.validate_draft(prepared_context, config)
        if not isinstance(precheck, dict):
            raise TypeError("平台 adapter 返回的校验结果不是对象")
    except Exception as exc:
        raise BusinessCapabilityError(
            "PUBLISH_VALIDATION_EXECUTION_FAILED",
            f"发布校验执行失败：{exc}",
            retryable=True,
        ) from exc

    # 只有平台 adapter 明确返回 ok=true 且没有阻断项时才允许外部写入。
    # 契约异常的 ``ok=false + errors=[]`` 也必须 fail closed。
    if precheck.get("ok") is not True or precheck.get("errors"):
        if not precheck.get("errors"):
            precheck = {
                **precheck,
                "errors": [
                    {
                        "code": "PUBLISH_VALIDATION_FAILED",
                        "field": "",
                        "message": "平台预检未明确通过，已停止准备发布素材。",
                        "severity": "error",
                    }
                ],
            }
        return _evaluate_prepared_publish_context(
            publish_context,
            platform=platform,
            adapter=adapter,
            config=config,
            prepared_context=prepared_context,
            precheck=precheck,
        )

    prepared_product = source_product
    if not local_only_prepare:
        try:
            prepared_product = adapter.prepare_product(source_product, config)
            if not isinstance(prepared_product, dict):
                raise TypeError("平台 adapter 返回的商品不是对象")
        except Exception as exc:
            raise BusinessCapabilityError(
                "PUBLISH_ASSET_PREPARATION_FAILED",
                f"发布素材准备失败：{exc}",
                details={"outcome_unknown": True},
            ) from exc

    publish_context["product"] = prepared_product
    return _evaluate_prepared_publish_context(
        publish_context,
        platform=platform,
        adapter=adapter,
        config=config,
        prepared_context=prepared_context.with_product(prepared_product),
        precheck=precheck,
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

    # 提交发布是写路径：以最新评估结果落盘预检，作为发布队列准入的可信事实。
    # 纯只读的 product_publish_validate 不落盘（side_effect="none" 契约），
    # 预检持久化统一发生在受信的写入口。
    try:
        save_draft_precheck_result(
            evaluation.context,
            evaluation.precheck,
            context=active_context,
        )
    except Exception as exc:
        raise BusinessCapabilityError(
            "PUBLISH_PRECHECK_PERSIST_FAILED",
            f"发布预检落盘失败：{exc}",
            retryable=True,
        ) from exc

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


@dataclass(frozen=True)
class PublishCapabilityScope:
    """发布 Capability 的可信依赖边界。"""

    context: AppContext
    publishing_bus: PublishingBusLike


PRODUCT_PUBLISH_VALIDATE_TOOL = "product_publish_validate"
PRODUCT_PUBLISH_REQUEST_TOOL = "product_publish_request"


@ai_tool(
    name=PRODUCT_PUBLISH_VALIDATE_TOOL,
    description=(
        "对草稿目标市场执行确定性发布校验，返回摘要、校验错误与 "
        "validation_digest；通过后才允许提交发布。"
    ),
    permission="product.publish",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def product_publish_validate(
    request: ProductPublishValidateRequest,
    scope: Annotated[PublishCapabilityScope, Injected()],
) -> ProductPublishValidationResult:
    return validate_product_publish(request, context=scope.context)


def _publish_request_approval_snapshot(
    request: ProductPublishCapabilityRequest,
    scope: PublishCapabilityScope,
) -> TaskApprovalSnapshot:
    """服务端生成的发布审批快照：冻结发布校验摘要与 validation_digest。

    审批页面展示它与执行真正提交的内容同源；任务创建与执行复核两个时点
    都重算它，任何草稿/配置/payload 变化都会使旧审批失效。
    """

    evaluation = evaluate_publish_validation(
        ProductPublishValidateRequest(
            draft_id=request.draft_id,
            platform=request.platform,
            site=request.site,
        ),
        context=scope.context,
    )
    if not evaluation.result.passed:
        raise BusinessCapabilityError(
            "PUBLISH_VALIDATION_FAILED",
            "发布条件当前不满足，请修复校验错误后重试。",
        )
    summary = evaluation.result.summary
    destination_rows = [
        item.model_dump(mode="json", exclude_none=True)
        for item in summary.destinations
    ]
    destination_text = "、".join(
        f"{item.site_id}/{item.logistic_type}"
        for item in summary.destinations
    )
    return TaskApprovalSnapshot(
        summary=(
            f"发布草稿 {summary.draft_id} 到 {summary.platform}："
            f"《{summary.title}》 {summary.listing_currency} {summary.price}"
            f"，图片 {summary.image_count} 张"
            + (f"，销售目标 {destination_text}" if destination_text else "")
        ),
        canonical_payload={
            "destinations": destination_rows,
            "draft_id": summary.draft_id,
            "image_count": summary.image_count,
            "listing_currency": summary.listing_currency,
            "platform": summary.platform,
            "price": summary.price,
            "product_id": summary.product_id,
            "site": summary.site,
            "stock": summary.stock,
            "store_identity": summary.store_identity,
            "title": summary.title,
            "validation_digest": evaluation.result.validation_digest,
        },
    )


@ai_tool(
    name=PRODUCT_PUBLISH_REQUEST_TOOL,
    description=(
        "提交真实发布；破坏性操作，需要人工在受信界面批准后才会执行，"
        "执行时会重新校验并核对冻结的审批快照。"
    ),
    permission="product.publish",
    side_effect="write",
    approval_required=True,
    approval_snapshot=_publish_request_approval_snapshot,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="idempotent",
    execution_mode="persistent_job",
    version="1",
)
def product_publish_request(
    request: ProductPublishCapabilityRequest,
    scope: Annotated[PublishCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> JobReferenceResult:
    from datetime import datetime

    operation_key = str(
        execution.idempotency_context.get("operation_key") or ""
    ).strip()
    if not operation_key:
        raise BusinessCapabilityError(
            "PUBLISH_OPERATION_KEY_REQUIRED",
            "发布请求缺少可信 operation_key。",
        )
    task_id = str(execution.business_scope.get("task_id") or "").strip()
    step_id = str(execution.business_scope.get("step_id") or "").strip()
    confirmed_at_raw = str(
        execution.business_scope.get("approval_confirmed_at") or ""
    ).strip()
    if not task_id or not step_id or not confirmed_at_raw:
        raise BusinessCapabilityError(
            "PUBLISH_APPROVAL_CONTEXT_REQUIRED",
            "发布请求缺少可信审批上下文。",
        )
    try:
        confirmed_at = datetime.fromisoformat(confirmed_at_raw)
    except ValueError as exc:
        raise BusinessCapabilityError(
            "PUBLISH_APPROVAL_CONTEXT_INVALID",
            "发布审批确认时间无效。",
        ) from exc
    snapshot = _publish_request_approval_snapshot(request, scope)
    verify_execution_approval(
        execution,
        snapshot=snapshot,
        capability_name=PRODUCT_PUBLISH_REQUEST_TOOL,
        capability_version="1",
        stale_code="PUBLISH_CONFIRMATION_STALE",
    )
    validation_digest = str(
        snapshot.canonical_payload.get("validation_digest") or ""
    )
    result = request_product_publish(
        ProductPublishRequest(
            draft_id=request.draft_id,
            platform=request.platform,
            site=request.site,
            idempotency_key=operation_key,
            confirmation=PublishRequestConfirmation(
                task_id=task_id,
                step_id=step_id,
                validation_digest=validation_digest,
                confirmed_at=confirmed_at,
            ),
        ),
        publishing_bus=scope.publishing_bus,
        context=scope.context,
    )
    return JobReferenceResult(
        job_id=result.job_id,
        job_type=PUBLISH_JOB_TYPE,
        status=(
            result.status
            if result.status in {"queued", "pending", "running", "retrying"}
            else "queued"
        ),
        summary="发布任务已提交，正在等待平台真实终态。",
    )


PUBLISH_AI_CAPABILITIES = (
    product_publish_validate,
    product_publish_request,
)


__all__ = [
    "PRODUCT_PUBLISH_REQUEST_TOOL",
    "PRODUCT_PUBLISH_VALIDATE_TOOL",
    "PUBLISH_AI_CAPABILITIES",
    "PublishCapabilityScope",
    "PublishingBusLike",
    "product_publish_request",
    "product_publish_validate",
    "prepare_and_evaluate_publish_validation",
    "request_product_publish",
    "validate_product_publish",
]
