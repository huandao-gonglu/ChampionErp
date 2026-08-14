from __future__ import annotations

"""稳定草稿上的规则与 focused Agent 属性填写 Capability。"""

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from erp_web.product_model import unresolved_required_category_attributes
from erp_web.runtime_units.category_attribute_ai_fill import (
    apply_ai_model_attribute_fill,
)
from erp_web.runtime_units.category_store import fetch_category_record
from erp_web.runtime_units.draft_publish_context import draft_for_publish_target
from erp_web.runtime_units.market_capability_support import (
    CategoryRecordLoader,
    MarketPrepareStore,
    assert_target_mutable,
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
    ProductAttributesFillRequest,
    ProductAttributesFillResult,
)
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputRequired,
)


AttributeFiller = Callable[..., tuple[dict[str, Any], dict[str, Any]]]


def fill_product_attributes(
    request: ProductAttributesFillRequest,
    *,
    product_store: MarketPrepareStore,
    attribute_filler: AttributeFiller = apply_ai_model_attribute_fill,
    category_record_loader: CategoryRecordLoader = fetch_category_record,
) -> ProductAttributesFillResult:
    """复用规则/focused Agent；未解决的真实必填属性会暂停步骤。"""

    platform = require_platform(request.target_platform)
    draft, product = load_draft(product_store, request.draft_id)
    target = select_target(draft, platform=platform, site=request.site)
    target_draft = draft_for_publish_target(draft, target)
    assert_target_mutable(target_draft)
    selected_category_id = text(target_draft.get("category_id"))
    if not selected_category_id:
        raise CapabilityInputRequired(
            "PRODUCT_CATEGORY_REQUIRED",
            "填写平台属性前必须先确定类目。",
            key="category_id",
            label="平台类目",
            reason="请先运行类目匹配或明确选择平台类目。",
        )
    record = load_category_record(
        category_record_loader,
        platform=platform,
        site=text(target.get("site")),
        selected_category_id=selected_category_id,
    )
    existing_attributes = (
        target_draft.get("attributes")
        if isinstance(target_draft.get("attributes"), dict)
        else {}
    )
    input_draft = deepcopy(target_draft)
    input_draft["attributes"] = {
        **deepcopy(existing_attributes),
        **request.provided_attributes,
    }
    projected = product_with_target(product, platform, input_draft)
    try:
        updated_product, meta = attribute_filler(projected, platform, record)
    except (BusinessCapabilityError, CapabilityInputRequired):
        raise
    except Exception as exc:
        raise BusinessCapabilityError(
            "PRODUCT_ATTRIBUTES_FILL_FAILED",
            f"平台属性填写失败：{exc}",
            retryable=True,
        ) from exc
    if not isinstance(updated_product, dict) or not isinstance(meta, dict):
        raise BusinessCapabilityError(
            "PRODUCT_ATTRIBUTES_FILL_RESULT_INVALID",
            "属性填写 owner 返回了无效结果。",
        )
    drafts = (
        updated_product.get("drafts")
        if isinstance(updated_product.get("drafts"), dict)
        else {}
    )
    updated_draft = deepcopy(
        drafts.get(platform) if isinstance(drafts.get(platform), dict) else {}
    )
    if not updated_draft:
        raise BusinessCapabilityError(
            "PRODUCT_ATTRIBUTES_FILL_RESULT_INVALID",
            "属性填写结果缺少目标平台草稿。",
        )
    attributes = (
        updated_draft.get("attributes")
        if isinstance(updated_draft.get("attributes"), dict)
        else {}
    )
    updated_draft["category_attribute_schema"] = category_schema(
        record,
        platform=platform,
        site=text(target.get("site")),
        selected_category_id=selected_category_id,
    )
    unresolved = unresolved_required_category_attributes(
        product_with_target(updated_product, platform, updated_draft),
        platform,
        record,
    )
    updated_draft["validation_errors"] = [
        text(definition.get("id"))
        for definition in unresolved
        if text(definition.get("id"))
    ]
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
        "category_precheck",
        "last_precheck",
        "last_precheck_target",
        "last_publish_task",
        "publish_status",
        "status",
    ):
        updated_draft[key] = deepcopy(invalidated_projection.get(key))
    changed = existing_attributes != attributes or any(
        target_draft.get(key) != updated_draft.get(key)
        for key in (
            "category_attribute_schema",
            "validation_errors",
            "category_precheck",
            "last_precheck",
            "last_precheck_target",
            "last_publish_task",
            "publish_status",
            "status",
        )
    )
    persist_target_projection(
        product_store=product_store,
        product=product,
        draft=draft,
        target=target,
        updated_product=updated_product,
        updated_target_draft=updated_draft,
    )

    if unresolved:
        definition = unresolved[0]
        attr_id = text(definition.get("id")) or "attributes"
        label = text(definition.get("name")) or attr_id
        options = [
            text(option)
            for option in (
                definition.get("options")
                if isinstance(definition.get("options"), list)
                else []
            )
            if text(option)
        ]
        warning = text(meta.get("warning"))
        reason = f"平台类目要求填写 {label}。"
        if warning:
            reason = f"{reason}{warning}"
        raise CapabilityInputRequired(
            "PRODUCT_ATTRIBUTE_INPUT_REQUIRED",
            f"必填属性 {label} 仍无法自动确定。",
            key=attr_id,
            label=label,
            reason=reason,
            options=options,
            input_type="select" if options else "text",
            input_owner="provided_attributes",
        )

    return ProductAttributesFillResult(
        draft_id=request.draft_id,
        platform=platform,
        site=text(target.get("site")),
        attributes=attributes,
        filled_attribute_ids=sorted(text(key) for key in attributes if text(key)),
        need_review_attribute_ids=[],
        fill_source=text(meta.get("source")),
        warning=text(meta.get("warning")),
        changed=changed,
    )


__all__ = ["AttributeFiller", "fill_product_attributes"]
