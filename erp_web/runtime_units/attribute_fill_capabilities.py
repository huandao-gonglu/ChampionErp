from __future__ import annotations

"""稳定草稿上的规则与 focused Agent 属性填写 Capability。"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Any

from erp_web.product_model import unresolved_required_category_attributes
from erp_web.runtime_units.category_attribute_ai_fill import (
    apply_ai_model_attribute_fill,
)
from erp_web.runtime_units.category_store import (
    fetch_category_attribute_values,
    fetch_category_record,
)
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
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.category import (
    category_attribute_schema,
    is_category_dictionary_attribute,
)
from erp_web.schemas.market_prepare_capabilities import (
    ProductAttributesFillRequest,
    ProductAttributesFillResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputRequired,
)


AttributeFiller = Callable[..., tuple[dict[str, Any], dict[str, Any]]]
AttributeValuesLoader = Callable[..., dict[str, Any]]

#: 字典属性候选值拉取上限；超出时保留前 N 个合法值供人工选择。
DICTIONARY_OPTIONS_LIMIT = 50


def _fetch_dictionary_values(
    loader: AttributeValuesLoader,
    *,
    platform: str,
    category_id: str,
    attribute_id: str,
    site: str,
) -> dict[str, Any]:
    """实时拉取平台字典值；任何失败都回落为空结果，不阻断主流程。"""

    try:
        payload = loader(
            platform,
            category_id,
            attribute_id,
            site=site,
            limit=DICTIONARY_OPTIONS_LIMIT,
            timeout_seconds=10,
        )
    except Exception:
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _dictionary_value_rows(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    values = payload.get("values") if isinstance(payload, Mapping) else None
    if not isinstance(values, list):
        return rows
    for item in values:
        if not isinstance(item, Mapping):
            continue
        value = text(item.get("value"))
        if not value:
            continue
        rows.append(
            {
                "dictionary_value_id": text(item.get("id")),
                "value": value,
            }
        )
        if len(rows) >= DICTIONARY_OPTIONS_LIMIT:
            break
    return rows


def _load_dictionary_options(
    loader: AttributeValuesLoader,
    *,
    platform: str,
    category_id: str,
    attribute_id: str,
    site: str,
) -> list[str]:
    """字典属性暂停待输入时实时拉取平台合法候选值；失败回落空候选。"""

    payload = _fetch_dictionary_values(
        loader,
        platform=platform,
        category_id=category_id,
        attribute_id=attribute_id,
        site=site,
    )
    options: list[str] = []
    for row in _dictionary_value_rows(payload):
        if row["value"] not in options:
            options.append(row["value"])
    return options


def _match_dictionary_rows(
    rows: list[dict[str, str]],
    raw_value: str,
) -> list[dict[str, str]]:
    """把用户提交的候选文本解析为平台字典值结构。

    多选属性允许用 ``;`` 分隔多个候选；匹配不区分大小写与首尾空白。
    全部无法匹配时返回空列表，由调用方保留原值交给统一校验。
    """

    tokens = [
        text(token)
        for token in str(raw_value or "").split(";")
        if text(token)
    ]
    matched: list[dict[str, str]] = []
    for token in tokens:
        row = next(
            (
                item
                for item in rows
                if item["value"].casefold() == token.casefold()
            ),
            None,
        )
        if row is not None and row not in matched:
            matched.append(dict(row))
    return matched


def _normalize_provided_dictionary_values(
    provided: Mapping[str, Any],
    *,
    platform: str,
    category_id: str,
    site: str,
    record: Mapping[str, Any],
    loader: AttributeValuesLoader,
) -> dict[str, Any]:
    """字典属性的用户文本提交解析为 ``{"values": [...]}`` 结构。

    待输入卡片只能提交候选文本；strict_enum 校验要求带
    ``dictionary_value_id`` 的结构化值，这里按字典值名称完成解析。
    已是结构化值或非字典属性的提交原样保留。
    """

    normalized: dict[str, Any] = dict(provided)
    definitions = {
        text(definition.get("id")): definition
        for definition in category_attribute_schema(dict(record))
        if text(definition.get("id"))
    }
    for attr_id, value in provided.items():
        definition = definitions.get(text(attr_id))
        if definition is None or not is_category_dictionary_attribute(definition):
            continue
        tokens = value if isinstance(value, (list, tuple)) else [value]
        texts = [text(item) for item in tokens if text(item)]
        if not texts or any(isinstance(item, Mapping) for item in tokens):
            continue
        payload = _fetch_dictionary_values(
            loader,
            platform=platform,
            category_id=category_id,
            attribute_id=text(attr_id),
            site=site,
        )
        rows = _dictionary_value_rows(payload)
        if not rows:
            continue
        matched: list[dict[str, str]] = []
        for item in texts:
            for row in _match_dictionary_rows(rows, item):
                if row not in matched:
                    matched.append(row)
        if matched:
            normalized[text(attr_id)] = {"values": matched}
    return normalized


def fill_product_attributes(
    request: ProductAttributesFillRequest,
    *,
    product_store: MarketPrepareStore,
    attribute_filler: AttributeFiller = apply_ai_model_attribute_fill,
    category_record_loader: CategoryRecordLoader = fetch_category_record,
    attribute_values_loader: AttributeValuesLoader = fetch_category_attribute_values,
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
    provided_attributes = _normalize_provided_dictionary_values(
        request.provided_attributes,
        platform=platform,
        category_id=selected_category_id,
        site=text(target.get("site")),
        record=record,
        loader=attribute_values_loader,
    )
    input_draft = deepcopy(target_draft)
    input_draft["attributes"] = {
        **deepcopy(existing_attributes),
        **provided_attributes,
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
        dictionary_attribute = is_category_dictionary_attribute(definition)
        if not options and dictionary_attribute:
            # 平台类目属性定义不内联字典值（只有 dictionary_id），
            # 暂停待输入时必须实时拉取合法候选，用户才知道能填什么。
            options = _load_dictionary_options(
                attribute_values_loader,
                platform=platform,
                category_id=selected_category_id,
                attribute_id=attr_id,
                site=text(target.get("site")),
            )
        warning = text(meta.get("warning"))
        reason = f"平台类目要求填写 {label}。"
        if dictionary_attribute:
            reason = f"{reason}该属性为平台枚举属性，请从候选值中选择。"
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


@dataclass(frozen=True)
class AttributeFillCapabilityScope:
    """属性填写 Capability 的可信依赖边界。"""

    products: MarketPrepareStore


PRODUCT_ATTRIBUTES_FILL_TOOL = "product_attributes_fill"


@ai_tool(
    name=PRODUCT_ATTRIBUTES_FILL_TOOL,
    description="为草稿目标市场填写平台必填属性；无法确定时暂停并请求补充。",
    permission="product.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def product_attributes_fill(
    request: ProductAttributesFillRequest,
    scope: Annotated[AttributeFillCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ProductAttributesFillResult:
    del execution
    return fill_product_attributes(request, product_store=scope.products)


ATTRIBUTE_FILL_AI_CAPABILITIES = (product_attributes_fill,)


__all__ = [
    "ATTRIBUTE_FILL_AI_CAPABILITIES",
    "AttributeFillCapabilityScope",
    "AttributeFiller",
    "PRODUCT_ATTRIBUTES_FILL_TOOL",
    "fill_product_attributes",
    "product_attributes_fill",
]
