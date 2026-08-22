from __future__ import annotations

"""高层 ``draft.prepare_for_market`` 顺序编排。"""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any

from erp_web.context import get_context
from erp_web.product_model import normalize_draft_image_refs, normalize_list
from erp_web.runtime_units.attribute_fill_capabilities import (
    fill_product_attributes,
)
from erp_web.runtime_units.category_capabilities import CategoryMatcher
from erp_web.runtime_units.collect_helpers import claim_products_to_platforms
from erp_web.runtime_units.copy_generation import generate_ai_copy_bundle
from erp_web.runtime_units.draft_publish_context import (
    draft_for_publish_target,
    draft_publish_targets,
    merge_target_listing_into_draft,
)
from erp_web.runtime_units.market_capability_support import (
    MarketPrepareStore,
    assert_target_mutable,
    invalidate_target_publish_preparation,
    load_draft,
    product_with_target,
    raise_store_error,
    require_platform,
    select_target,
    text,
)
from erp_web.runtime_units.market_pricing_capability import (
    PricingCalculator,
    prepare_target_pricing,
)
from erp_web.runtime_units.pricing_runtime import calculate_price
from erp_web.runtime_units.product_capabilities import prepare_product_images
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.draft_capabilities import DraftPublishReadiness
from erp_web.schemas.market_prepare_capabilities import (
    CategoryMatchCapabilityResult,
    CategoryMatchRequest,
    DraftPrepareForMarketRequest,
    DraftPrepareForMarketResult,
    ProductAttributesFillRequest,
    ProductAttributesFillResult,
)
from erp_web.schemas.product_capabilities import (
    ProductImagesPrepareRequest,
    ProductImagesPrepareResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputRequired,
)


ClaimTargetDrafts = Callable[[list[str], list[str] | None], dict[str, Any]]
CopyGenerator = Callable[
    [dict[str, Any], str, str, str, str, dict[str, Any]],
    dict[str, Any],
]
AppConfigLoader = Callable[[], dict[str, Any]]
ImagePrepareCapability = Callable[..., ProductImagesPrepareResult]
CategoryCapability = Callable[..., CategoryMatchCapabilityResult]
AttributeCapability = Callable[..., ProductAttributesFillResult]


def _load_app_config() -> dict[str, Any]:
    return get_context().config.load_app_config()


def _draft_for_existing_target(
    *,
    source_draft: dict[str, Any],
    product: dict[str, Any],
    target_platform: str,
) -> str:
    if any(
        text(target.get("platform")).lower() == target_platform
        for target in draft_publish_targets(source_draft)
    ):
        return text(source_draft.get("draft_id"))
    drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
    existing = drafts.get(target_platform) if isinstance(drafts.get(target_platform), dict) else {}
    return text(existing.get("draft_id"))


def _resolve_target_draft_id(
    request: DraftPrepareForMarketRequest,
    *,
    product_store: MarketPrepareStore,
    claim_target_drafts: ClaimTargetDrafts,
) -> str:
    source_draft, product = load_draft(product_store, request.draft_id)
    platform = require_platform(request.target_platform)
    existing_id = _draft_for_existing_target(
        source_draft=source_draft,
        product=product,
        target_platform=platform,
    )
    if existing_id:
        target_draft, _target_product = load_draft(product_store, existing_id)
        select_target(target_draft, platform=platform, site=request.site)
        return existing_id

    product_id = text(
        source_draft.get("source_product_id")
        or source_draft.get("product_id")
        or product.get("product_id")
    )
    if not product_id:
        raise BusinessCapabilityError(
            "DRAFT_PRODUCT_ID_MISSING",
            "来源草稿缺少稳定 product_id，无法创建目标草稿。",
        )
    try:
        claimed = claim_target_drafts([product_id], [platform])
    except (BusinessCapabilityError, CapabilityInputRequired):
        raise
    except Exception as exc:
        raise BusinessCapabilityError(
            "TARGET_DRAFT_CLAIM_FAILED",
            f"目标草稿创建失败：{exc}",
            retryable=True,
        ) from exc
    items = claimed.get("items") if isinstance(claimed.get("items"), list) else []
    item = next(
        (
            row
            for row in items
            if isinstance(row, dict)
            and text(row.get("product_id") or row.get("source_product_id"))
            == product_id
        ),
        {},
    )
    draft_ids = item.get("draft_ids") if isinstance(item.get("draft_ids"), list) else []
    for candidate_id in (text(value) for value in draft_ids):
        if not candidate_id:
            continue
        try:
            candidate, _candidate_product = load_draft(product_store, candidate_id)
            select_target(candidate, platform=platform, site=request.site)
            return candidate_id
        except CapabilityInputRequired:
            raise
        except BusinessCapabilityError:
            continue
    message = text(item.get("error")) or text(claimed.get("error"))
    raise BusinessCapabilityError(
        "TARGET_DRAFT_CLAIM_INCOMPLETE",
        message or "目标草稿创建后没有返回可验证的稳定 draft_id。",
    )


def _copy_ready(draft: dict[str, Any]) -> bool:
    status = text(draft.get("status")).lower()
    has_copy_marker = bool(
        draft.get("copy_generated_at")
        or draft.get("ai_copy_ready")
        or text(draft.get("copy_source")).lower()
        in {"ai", "deepseek", "openai", "fallback_ai"}
        or status in {"copy_ready", "images_ready", "ready_to_publish"}
    )
    return has_copy_marker and bool(
        text(draft.get("title")) and text(draft.get("description"))
    )


def _prepare_copy(
    request: DraftPrepareForMarketRequest,
    *,
    target_draft_id: str,
    product_store: MarketPrepareStore,
    copy_generator: CopyGenerator,
    app_config_loader: AppConfigLoader,
    copy_operation_key: str,
) -> None:
    draft, product = load_draft(product_store, target_draft_id)
    platform = require_platform(request.target_platform)
    target = select_target(draft, platform=platform, site=request.site)
    projection = draft_for_publish_target(draft, target)
    operation_key = text(copy_operation_key)
    if (
        request.regenerate_copy
        and operation_key
        and text(projection.get("copy_operation_key")) == operation_key
        and _copy_ready(projection)
    ):
        return
    if _copy_ready(projection) and not request.regenerate_copy:
        return
    projected_product = product_with_target(product, platform, projection)
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    source_platform = text(source.get("source_platform")) or platform
    try:
        response = copy_generator(
            projected_product,
            source_platform,
            platform,
            text(target.get("language")),
            "rewrite",
            app_config_loader(),
        )
    except (BusinessCapabilityError, CapabilityInputRequired):
        raise
    except Exception as exc:
        raise BusinessCapabilityError(
            "DRAFT_COPY_GENERATION_FAILED",
            f"目标市场文案生成失败：{exc}",
            retryable=True,
        ) from exc
    if not bool(response.get("ok")):
        raise BusinessCapabilityError(
            "DRAFT_COPY_GENERATION_FAILED",
            text(response.get("error")) or "目标市场文案生成失败。",
            retryable=True,
        )
    copy_payload = (
        response.get("copy") if isinstance(response.get("copy"), dict) else {}
    )
    title = text(copy_payload.get("title"))
    description = text(copy_payload.get("description"))
    if not title or not description:
        raise BusinessCapabilityError(
            "DRAFT_COPY_RESULT_INVALID",
            "文案生成结果缺少完整标题或描述。",
        )
    updated = deepcopy(draft)
    updated.update(
        {
            "title": title,
            "description": description,
            "bullets": normalize_list(copy_payload.get("bullets")),
            "search_terms": normalize_list(copy_payload.get("search_keywords")),
            "language": text(response.get("language"))
            or text(target.get("language")),
            "copy_source": "ai",
            "copy_generated_at": datetime.now(timezone.utc).isoformat(),
            **(
                {"copy_operation_key": operation_key}
                if operation_key
                else {}
            ),
        }
    )
    updated = invalidate_target_publish_preparation(
        product_store=product_store,
        product=product,
        draft=updated,
        target=target,
    )
    saved_result, error, _status = product_store.save_draft_detail(updated)
    raise_store_error(
        error,
        default_code="DRAFT_COPY_PERSIST_FAILED",
        default_message="目标市场文案保存失败。",
    )
    saved_row = (
        saved_result.get("draft")
        if isinstance(saved_result.get("draft"), dict)
        else {}
    )
    if text(saved_row.get("draft_id")) != target_draft_id:
        raise BusinessCapabilityError(
            "DRAFT_COPY_PERSIST_INCOMPLETE",
            "文案保存结果与稳定目标草稿不一致。",
        )
    saved, _saved_product = load_draft(product_store, target_draft_id)
    saved_target = select_target(
        saved,
        platform=platform,
        site=text(target.get("site")),
    )
    saved_projection = draft_for_publish_target(saved, saved_target)
    if not text(saved_projection.get("title")) or not text(
        saved_projection.get("description")
    ):
        raise BusinessCapabilityError(
            "DRAFT_COPY_PERSIST_INCOMPLETE",
            "文案生成返回成功，但稳定目标草稿没有完整标题和描述。",
        )
    if operation_key and text(
        saved_projection.get("copy_operation_key")
    ) != operation_key:
        raise BusinessCapabilityError(
            "DRAFT_COPY_PERSIST_INCOMPLETE",
            "文案生成已保存，但幂等 operation marker 未能从稳定目标草稿验证。",
        )


def _finalize_readiness(
    *,
    target_draft_id: str,
    platform: str,
    site: str,
    product_store: MarketPrepareStore,
) -> DraftPublishReadiness:
    draft, product = load_draft(product_store, target_draft_id)
    target = select_target(draft, platform=platform, site=site)
    projection = draft_for_publish_target(draft, target)
    product_projection = product_with_target(product, platform, projection)
    workflow_status = product_store.draft_workflow_status(
        product_projection,
        platform,
    )
    updates = {
        "validation_errors": [],
        "category_precheck": {},
        "last_precheck": {},
        "last_precheck_target": {},
        "last_publish_task": {},
        "publish_status": "",
        "status": workflow_status,
    }
    merged = merge_target_listing_into_draft(draft, target, updates)
    if merged != draft:
        saved, error, _status = product_store.save_draft_detail(merged)
        raise_store_error(
            error,
            default_code="DRAFT_PREPARE_FINALIZE_FAILED",
            default_message="目标草稿准备状态保存失败。",
        )
        draft = saved.get("draft") if isinstance(saved.get("draft"), dict) else merged
        target = select_target(draft, platform=platform, site=site)
        projection = draft_for_publish_target(draft, target)

    validation = (
        projection.get("validation_errors")
        if isinstance(projection.get("validation_errors"), list)
        else []
    )
    warning_count = sum(
        1
        for issue in validation
        if isinstance(issue, dict)
        and text(issue.get("severity")).lower() == "warning"
    )
    return DraftPublishReadiness(
        workflow_status=workflow_status,
        publish_status=text(projection.get("publish_status")),
        precheck_passed=False,
        image_count=len(normalize_draft_image_refs(projection.get("images"))),
        attribute_count=len(
            projection.get("attributes")
            if isinstance(projection.get("attributes"), dict)
            else {}
        ),
        validation_error_count=max(0, len(validation) - warning_count),
        validation_warning_count=warning_count,
    )


def prepare_draft_for_market(
    request: DraftPrepareForMarketRequest,
    *,
    product_store: MarketPrepareStore,
    claim_target_drafts: ClaimTargetDrafts = claim_products_to_platforms,
    copy_generator: CopyGenerator = generate_ai_copy_bundle,
    app_config_loader: AppConfigLoader = _load_app_config,
    image_capability: ImagePrepareCapability = prepare_product_images,
    category_capability: CategoryCapability | None = None,
    attribute_capability: AttributeCapability = fill_product_attributes,
    pricing_calculator: PricingCalculator = calculate_price,
    copy_operation_key: str = "",
) -> DraftPrepareForMarketResult:
    """按稳定目标草稿串联准备步骤；不执行发布或发布预检。"""

    platform = require_platform(request.target_platform)
    target_draft_id = _resolve_target_draft_id(
        request,
        product_store=product_store,
        claim_target_drafts=claim_target_drafts,
    )
    completed_parts = ["target_draft"]

    target_draft, _target_product = load_draft(product_store, target_draft_id)
    target = select_target(target_draft, platform=platform, site=request.site)
    target_projection = draft_for_publish_target(target_draft, target)
    assert_target_mutable(target_projection)

    _prepare_copy(
        request,
        target_draft_id=target_draft_id,
        product_store=product_store,
        copy_generator=copy_generator,
        app_config_loader=app_config_loader,
        copy_operation_key=copy_operation_key,
    )
    completed_parts.append("copy")

    image_capability(
        ProductImagesPrepareRequest(
            draft_id=target_draft_id,
            asset_ids=request.asset_ids,
        ),
        product_store=product_store,
    )
    completed_parts.append("images")

    target_draft, _target_product = load_draft(product_store, target_draft_id)
    target = select_target(target_draft, platform=platform, site=request.site)
    target_projection = draft_for_publish_target(target_draft, target)
    if request.category_id or not text(target_projection.get("category_id")):
        if category_capability is None:
            raise BusinessCapabilityError(
                "CATEGORY_CAPABILITY_NOT_BOUND",
                "目标市场准备尚未绑定类目匹配 Capability。",
            )
        category_capability(
            CategoryMatchRequest(
                draft_id=target_draft_id,
                target_platform=platform,
                site=text(target.get("site")),
                category_id=request.category_id,
            ),
            product_store=product_store,
        )
    completed_parts.append("category")

    try:
        attribute_capability(
            ProductAttributesFillRequest(
                draft_id=target_draft_id,
                target_platform=platform,
                site=text(target.get("site")),
                provided_attributes=request.provided_attributes,
            ),
            product_store=product_store,
        )
    except CapabilityInputRequired as exc:
        exc.set_input_owner("provided_attributes")
        raise
    completed_parts.append("attributes")

    try:
        prepare_target_pricing(
            target_draft_id=target_draft_id,
            target_platform=platform,
            site=text(target.get("site")),
            pricing_input=request.pricing_input,
            product_store=product_store,
            pricing_calculator=pricing_calculator,
        )
    except CapabilityInputRequired as exc:
        exc.set_input_owner("pricing_input")
        raise
    completed_parts.append("pricing")

    readiness = _finalize_readiness(
        target_draft_id=target_draft_id,
        platform=platform,
        site=text(target.get("site")),
        product_store=product_store,
    )
    return DraftPrepareForMarketResult(
        draft_id=target_draft_id,
        source_draft_id=request.draft_id,
        target_platform=platform,
        site=text(target.get("site")),
        completed_parts=completed_parts,
        readiness=readiness,
    )


__all__ = ["MarketPrepareCapabilityScope", "draft_prepare_for_market", "prepare_draft_for_market", "DRAFT_PREPARE_FOR_MARKET_TOOL", "MARKET_PREPARE_AI_CAPABILITIES"]


@dataclass(frozen=True)
class MarketPrepareCapabilityScope:
    """目标市场准备 Capability 的可信依赖边界。"""

    products: MarketPrepareStore
    category_matcher: CategoryMatcher
    claim_target_drafts: ClaimTargetDrafts
    copy_generator: CopyGenerator
    app_config_loader: AppConfigLoader


DRAFT_PREPARE_FOR_MARKET_TOOL = "draft_prepare_for_market"


@ai_tool(
    name=DRAFT_PREPARE_FOR_MARKET_TOOL,
    description=(
        "把来源草稿准备为目标市场草稿：认领、文案、图片、类目、属性与定价。"
    ),
    permission="product.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def draft_prepare_for_market(
    request: DraftPrepareForMarketRequest,
    scope: Annotated[MarketPrepareCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> DraftPrepareForMarketResult:
    from erp_web.runtime_units.category_capabilities import match_category

    def category_capability(
        category_request: CategoryMatchRequest,
        **kwargs: Any,
    ) -> CategoryMatchCapabilityResult:
        del kwargs
        return match_category(
            category_request,
            product_store=scope.products,
            matcher=scope.category_matcher,
        )

    operation_key = str(
        execution.idempotency_context.get("operation_key") or ""
    ).strip()
    return prepare_draft_for_market(
        request,
        product_store=scope.products,
        claim_target_drafts=scope.claim_target_drafts,
        copy_generator=scope.copy_generator,
        app_config_loader=scope.app_config_loader,
        category_capability=category_capability,
        copy_operation_key=f"{operation_key}:copy" if operation_key else "",
    )


MARKET_PREPARE_AI_CAPABILITIES = (draft_prepare_for_market,)
