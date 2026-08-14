from __future__ import annotations

"""稳定草稿上的 focused ``category.match`` Capability。"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from erp_web.product_model import apply_category_selection
from erp_web.runtime_units.category_store import fetch_category_record
from erp_web.runtime_units.draft_publish_context import draft_for_publish_target
from erp_web.runtime_units.market_capability_support import (
    CategoryRecordLoader,
    MarketPrepareStore,
    assert_target_mutable,
    category_path,
    category_schema,
    invalidate_target_publish_preparation,
    load_category_record,
    load_draft,
    persist_target_projection,
    product_with_target,
    require_platform,
    select_target,
    text,
)
from erp_web.schemas.market_prepare_capabilities import (
    CategoryMatchCapabilityResult,
    CategoryMatchRequest,
)
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputRequired,
)


CategoryMatcher = Callable[..., Mapping[str, Any]]


def match_category(
    request: CategoryMatchRequest,
    *,
    product_store: MarketPrepareStore,
    matcher: CategoryMatcher,
    category_record_loader: CategoryRecordLoader = fetch_category_record,
) -> CategoryMatchCapabilityResult:
    """复用现有 focused Agent，并把终检通过的类目写入稳定目标草稿。"""

    platform = require_platform(request.target_platform)
    draft, product = load_draft(product_store, request.draft_id)
    target = select_target(draft, platform=platform, site=request.site)
    target_draft = draft_for_publish_target(draft, target)
    assert_target_mutable(target_draft)
    selected_category_id = request.category_id or text(target_draft.get("category_id"))
    query = ""
    confidence = 0.0

    if not selected_category_id:
        try:
            match_args = (
                product_with_target(product, platform, target_draft),
                target_draft,
                target,
            )
            match_result = dict(matcher(*match_args))
        except (BusinessCapabilityError, CapabilityInputRequired):
            raise
        except Exception as exc:
            raise BusinessCapabilityError(
                "CATEGORY_MATCH_EXECUTION_FAILED",
                f"类目匹配执行失败：{exc}",
                retryable=True,
            ) from exc
        status = text(match_result.get("status")).lower()
        failure = (
            match_result.get("failure")
            if isinstance(match_result.get("failure"), Mapping)
            else {}
        )
        candidates = (
            match_result.get("candidates")
            if isinstance(match_result.get("candidates"), list)
            else []
        )
        if status == "unresolved":
            candidate_options = [
                text(candidate.get("category_id"))
                for candidate in candidates
                if isinstance(candidate, Mapping)
                and text(candidate.get("category_id"))
            ]
            raise CapabilityInputRequired(
                text(failure.get("code")) or "CATEGORY_MATCH_UNRESOLVED",
                text(failure.get("message")) or "类目匹配需要人工确认。",
                key="category_id",
                label="平台类目",
                reason=text(failure.get("message")) or "请选择一个候选类目。",
                options=candidate_options,
                input_type="select" if candidate_options else "text",
            )
        if status != "completed" or not bool(match_result.get("ok")):
            code = text(failure.get("code")) or "CATEGORY_MATCH_FAILED"
            message = text(failure.get("message")) or "类目匹配失败。"
            if code == "INPUT_INVALID":
                raise CapabilityInputRequired(
                    code,
                    message,
                    key="title",
                    label="商品标题",
                    reason="请先补充能识别商品类型的标题。",
                )
            if code == "TARGET_REQUIRED":
                raise CapabilityInputRequired(
                    code,
                    message,
                    key="site",
                    label="目标站点",
                    reason="请明确类目匹配的目标站点。",
                )
            raise BusinessCapabilityError(
                code,
                message,
                retryable=bool(failure.get("retryable")),
            )
        selected_category_id = text(match_result.get("selected_category_id"))
        if not selected_category_id:
            raise BusinessCapabilityError(
                "CATEGORY_MATCH_RESULT_INVALID",
                "类目匹配完成但没有返回 category_id。",
            )
        query = text(match_result.get("query"))
        decision = (
            match_result.get("decision")
            if isinstance(match_result.get("decision"), Mapping)
            else {}
        )
        try:
            confidence = float(decision.get("model_confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(1.0, max(0.0, confidence))
    record = load_category_record(
        category_record_loader,
        platform=platform,
        site=text(target.get("site")),
        selected_category_id=selected_category_id,
    )
    projected = product_with_target(product, platform, target_draft)
    updated_product = apply_category_selection(projected, platform, record)
    updated_draft = deepcopy(updated_product["drafts"][platform])
    updated_draft.update(
        {
            "category_id": selected_category_id,
            "description_category_id": text(
                record.get("description_category_id")
            ),
            "category_path": category_path(record),
            "category_attribute_schema": category_schema(
                record,
                platform=platform,
                site=text(target.get("site")),
                selected_category_id=selected_category_id,
            ),
        }
    )
    invalidated = invalidate_target_publish_preparation(
        product_store=product_store,
        product=updated_product,
        draft=draft,
        target=target,
        target_draft=updated_draft,
    )
    invalidated_target = select_target(
        invalidated,
        platform=platform,
        site=text(target.get("site")),
    )
    invalidated_projection = draft_for_publish_target(
        invalidated,
        invalidated_target,
    )
    for key in (
        "validation_errors",
        "category_precheck",
        "last_precheck",
        "last_precheck_target",
        "last_publish_task",
        "publish_status",
        "status",
    ):
        updated_draft[key] = deepcopy(invalidated_projection.get(key))
    changed = any(
        target_draft.get(key) != updated_draft.get(key)
        for key in (
            "category_id",
            "description_category_id",
            "category_path",
            "category_attribute_schema",
            "category_precheck",
            "last_precheck",
            "last_precheck_target",
            "last_publish_task",
            "publish_status",
            "status",
        )
    )
    saved = persist_target_projection(
        product_store=product_store,
        product=product,
        draft=draft,
        target=target,
        updated_product=updated_product,
        updated_target_draft=updated_draft,
    )
    saved_target = select_target(
        saved,
        platform=platform,
        site=text(target.get("site")),
    )
    saved_projection = draft_for_publish_target(saved, saved_target)
    if text(saved_projection.get("category_id")) != selected_category_id:
        raise BusinessCapabilityError(
            "CATEGORY_MATCH_PERSIST_INCOMPLETE",
            "类目匹配结果保存后无法从目标草稿验证。",
        )
    return CategoryMatchCapabilityResult(
        draft_id=request.draft_id,
        platform=platform,
        site=text(saved_target.get("site")),
        category_id=selected_category_id,
        category_path=text(saved_projection.get("category_path")),
        query=query,
        model_confidence=confidence,
        changed=changed,
    )


__all__ = ["CategoryMatcher", "match_category"]
