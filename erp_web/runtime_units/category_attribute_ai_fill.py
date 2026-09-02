# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from erp_web.product_model import (
    apply_ai_attribute_fill,
    apply_category_target_updates,
    normalize_product_model,
    unresolved_required_category_attributes,
)
from erp_web.schemas.category import (
    category_attribute_uses_unit,
    category_attribute_uses_numeric_unit,
    category_attribute_schema,
    category_attribute_value_is_valid,
    normalize_category_attribute_unit,
    normalize_category_attribute_number_unit_value,
)
from erp_web.schemas.category_attribute import CategoryAttributeValueLedger
from erp_web.schemas.category_brand import (
    is_brand_attribute,
    is_no_brand_fact,
    is_official_no_brand_value,
    product_context_declares_no_brand,
)
from erp_web.services.category_attribute_fill_agent_service import (
    CategoryAttributeFillAgentRun,
    run_category_attribute_fill_agent,
)

from .category_attribute_tools import build_category_attribute_value_toolset
from .category_brand_values import apply_no_brand_attribute
from .category_store import fetch_category_attribute_values


#: 单次运行纳入 AI 填充的可选属性上限；控制 prompt 体量与字典查询预算。
OPTIONAL_ATTRIBUTE_FILL_LIMIT = 20


def _normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [
            item.strip()
            for item in re.split(r"[,，;；\n]+", value)
            if item.strip()
        ]
    return []


def _category_path_text(record: dict[str, Any] | None) -> str:
    raw = record if isinstance(record, dict) else {}
    for key in ("category_path", "path", "name_original", "name_cn", "name"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("path_original", "path_cn"):
        value = raw.get(key)
        if isinstance(value, list):
            text = " / ".join(
                str(item).strip() for item in value if str(item).strip()
            )
            if text:
                return text
    return ""


def _short_text(value: Any, limit: int = 3000) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _product_context(product: dict[str, Any], platform: str) -> dict[str, Any]:
    source = (
        product.get("source") if isinstance(product.get("source"), dict) else {}
    )
    draft = (
        product.get("drafts", {}).get(platform)
        if isinstance(product.get("drafts"), dict)
        else {}
    )
    return {
        "product": {
            "name": product.get("name"),
            "brand": product.get("brand"),
            "model": product.get("model"),
            "weight_kg": product.get("weight_kg"),
            "category": product.get("category"),
            "colors": product.get("colors"),
            "materials": product.get("materials"),
            "attributes": product.get("attributes"),
        },
        "source": {
            "platform": source.get("source_platform"),
            "brand": source.get("brand"),
            "url": source.get("source_url"),
            "title": _short_text(source.get("title"), 500),
            "description": _short_text(source.get("description"), 2500),
            "bullets": _normalize_list(source.get("bullets"))[:30],
            "attributes": (
                source.get("attributes")
                if isinstance(source.get("attributes"), dict)
                else {}
            ),
            "dimensions": (
                source.get("dimensions")
                if isinstance(source.get("dimensions"), dict)
                else {}
            ),
            "weight_kg": source.get("weight_kg"),
            "material": source.get("material"),
            "colors": source.get("colors"),
            "package_contents": source.get("package_contents"),
        },
        "draft": {
            "title": _short_text(draft.get("title"), 500),
            "description": _short_text(draft.get("description"), 1800),
            "brand": draft.get("brand"),
            "model": draft.get("model"),
            "sku": draft.get("sku"),
            "upc": draft.get("upc"),
            "attributes": (
                draft.get("attributes")
                if isinstance(draft.get("attributes"), dict)
                else {}
            ),
            "package_dimensions": (
                draft.get("package_dimensions")
                if isinstance(draft.get("package_dimensions"), dict)
                else {}
            ),
        },
    }


def _agent_payload(
    product: dict[str, Any],
    platform: str,
    category_record: dict[str, Any] | None,
    schema: list[dict[str, Any]],
) -> dict[str, Any]:
    record = category_record if isinstance(category_record, dict) else {}
    return {
        "platform": platform,
        "site": str(record.get("site") or ""),
        "category_id": str(record.get("category_id") or ""),
        "category_path": _category_path_text(category_record),
        "product_context": _product_context(product, platform),
        "attributes": schema,
    }


def _dictionary_value_id(value: Any) -> int | str:
    text = str(value or "").strip()
    try:
        return int(text)
    except (TypeError, ValueError):
        return text


def _evidence_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [
            evidence
            for item in value.values()
            for evidence in _evidence_values(item)
        ]
    if isinstance(value, (list, tuple)):
        return [
            evidence
            for item in value
            for evidence in _evidence_values(item)
        ]
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value).strip()
        return [text] if text else []
    return []


def _normalized_evidence_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _has_product_evidence(value: str, product_context: dict[str, Any]) -> bool:
    """模型建议值必须在可信商品事实中有可复核文本锚点。"""

    candidate = _normalized_evidence_text(value)
    if not candidate:
        return False
    for raw_evidence in _evidence_values(product_context):
        evidence = _normalized_evidence_text(raw_evidence)
        if candidate == evidence:
            return True
        # 品牌、型号、规格和选项常作为更长标题/描述的一部分出现；过短值
        # 不做子串匹配，避免数字或单字符碰巧命中。
        if len(candidate) >= 3 and candidate in evidence:
            return True
    return False


_MEASUREMENT_UNIT_ALIAS_GROUPS = (
    frozenset({"kg", "kgs", "kilogram", "kilograms", "кг", "公斤", "千克"}),
    frozenset({"g", "gram", "grams", "гр", "г", "克"}),
    frozenset({"lb", "lbs", "pound", "pounds", "фунт", "磅"}),
    frozenset({"h", "hr", "hrs", "hour", "hours", "ч", "час", "小时", "小時"}),
    frozenset({"cm", "см", "centimeter", "centimeters", "厘米"}),
    frozenset({"mm", "мм", "millimeter", "millimeters", "毫米"}),
)


def _measurement_unit_aliases(unit: str) -> frozenset[str]:
    normalized = _normalized_evidence_text(unit)
    for aliases in _MEASUREMENT_UNIT_ALIAS_GROUPS:
        if normalized in aliases:
            return aliases
    return frozenset({normalized}) if normalized else frozenset()


def _has_number_unit_evidence(
    value: str,
    unit: str,
    product_context: dict[str, Any],
) -> bool:
    """数值和单位必须能由同一商品事实或显式单位字段共同证明。"""

    candidate = _decimal_value(value)
    unit_aliases = _measurement_unit_aliases(unit)
    if candidate is None or not unit_aliases:
        return False
    unit_pattern = "(?:" + "|".join(
        re.escape(alias)
        for alias in sorted(unit_aliases, key=len, reverse=True)
    ) + ")"
    measurement_pattern = re.compile(
        rf"(?<![\d.,])([+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+))"
        rf"(?![\d.,])\s*{unit_pattern}(?!\w)",
        re.IGNORECASE,
    )
    for raw_evidence in _evidence_values(product_context):
        evidence = _normalized_evidence_text(raw_evidence)
        for match in measurement_pattern.finditer(evidence):
            measured = _decimal_value(match.group(1))
            if measured is not None and measured == candidate:
                return True

    # ``weight_kg`` 等结构化字段把单位编码在字段名里；只接受商品/来源
    # 顶层事实，不把草稿 package_dimensions 当成商品类目属性证据。
    unit_suffixes = {
        f"_{alias.replace(' ', '_')}"
        for alias in unit_aliases
    }
    for scope_name in ("product", "source"):
        scope = product_context.get(scope_name)
        if not isinstance(scope, dict):
            continue
        for key, raw_value in scope.items():
            if not any(
                _normalized_evidence_text(key).endswith(suffix)
                for suffix in unit_suffixes
            ):
                continue
            measured = _decimal_value(raw_value)
            if measured is not None and measured == candidate:
                return True
    return False


def _brand_fact_values(product_context: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for scope_name in ("draft", "product", "source"):
        scope = product_context.get(scope_name)
        if not isinstance(scope, dict):
            continue
        value = str(scope.get("brand") or "").strip()
        if value:
            values.append(value)
    return values


def _has_brand_evidence(
    value: str,
    product_context: dict[str, Any],
    *,
    platform: str,
) -> bool:
    """品牌枚举只能落到明确无品牌或商品品牌字段的精确候选。"""

    if is_official_no_brand_value(platform, value):
        return product_context_declares_no_brand(product_context)
    candidate = _normalized_evidence_text(value)
    if not candidate:
        return False
    concrete_brands = {
        normalized
        for fact in _brand_fact_values(product_context)
        if not is_no_brand_fact(fact)
        and (normalized := _normalized_evidence_text(fact))
    }
    # source/product/draft 若给出了相互冲突的真实品牌，不能任选其中一个落库。
    return concrete_brands == {candidate}


def _decimal_value(value: Any) -> Decimal | None:
    text = str(value or "").strip().replace(",", ".")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
        return None
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _attribute_expects_weight_grams(
    attr: dict[str, Any],
    *,
    platform: str,
) -> bool:
    attr_id = str(attr.get("id") or "").strip()
    # 只对已确认语义为“包装重量（克）”的平台字段做换算。不能仅凭名称中的
    # “重量/克”推断，否则净重、最大承重等字段会误用 package weight。
    return platform == "ozon" and attr_id == "4497"


def _has_deterministic_attribute_evidence(
    value: str,
    attr: dict[str, Any],
    product_context: dict[str, Any],
    *,
    platform: str,
) -> bool:
    """接受可由结构化商品事实精确推导的单位换算，不放宽任意技术参数。"""

    if not _attribute_expects_weight_grams(attr, platform=platform):
        return False
    candidate = _decimal_value(value)
    if candidate is None or candidate <= 0:
        return False
    source = product_context.get("source")
    product = product_context.get("product")
    draft = product_context.get("draft")
    package = draft.get("package_dimensions") if isinstance(draft, dict) else {}
    draft_weight = _decimal_value(
        package.get("weight_kg") if isinstance(package, dict) else None
    )
    if draft_weight is not None and draft_weight > 0:
        # 平台草稿是当前发布目标；用户已改过草稿重量时，旧来源值不能反向覆盖。
        return candidate == draft_weight * 1000
    fallback_weights = {
        weight
        for weight in (
            _decimal_value(
                product.get("weight_kg") if isinstance(product, dict) else None
            ),
            _decimal_value(
                source.get("weight_kg") if isinstance(source, dict) else None
            ),
        )
        if weight is not None and weight > 0
    }
    # 无草稿值时，商品与来源若互相冲突也不属于“确定性”换算。
    return (
        len(fallback_weights) == 1
        and candidate == next(iter(fallback_weights)) * 1000
    )


_CATEGORY_TYPE_ATTRIBUTE_NAMES = frozenset(
    {
        "тип",
        "тип товара",
        "type",
        "product type",
        "tipo",
        "tipo de producto",
        "类型",
        "商品类型",
        "产品类型",
    }
)

def _has_category_type_evidence(
    attr: dict[str, Any],
    candidate: dict[str, str],
    *,
    platform: str,
    category_id: str,
    category_path: str,
) -> bool:
    """类目“类型”枚举可由已确认类目身份/路径提供跨语言证据。"""

    name = _normalized_evidence_text(attr.get("name"))
    if name not in _CATEGORY_TYPE_ATTRIBUTE_NAMES:
        return False
    attr_id = str(attr.get("id") or "").strip()
    candidate_id = str(candidate.get("dictionary_value_id") or "").strip()
    selected_category_id = str(category_id or "").strip()
    if (
        platform == "ozon"
        and attr_id == "8229"
        and candidate_id
        and selected_category_id
        and candidate_id == selected_category_id
    ):
        return True
    candidate_value = _normalized_evidence_text(candidate.get("value"))
    path_segments = {
        _normalized_evidence_text(segment)
        for segment in re.split(r"\s*(?:/|>|›|→)\s*", category_path)
        if _normalized_evidence_text(segment)
    }
    return bool(candidate_value) and candidate_value in path_segments


def _validated_agent_attributes(
    agent_output: dict[str, Any],
    schema: list[dict[str, Any]],
    ledger: CategoryAttributeValueLedger,
    product_context: dict[str, Any],
    *,
    platform: str,
    category_id: str,
    category_path: str,
) -> tuple[dict[str, Any], set[str]]:
    schema_by_id = {str(attr.get("id") or ""): attr for attr in schema}
    accepted: dict[str, Any] = {}
    evidence_rejected: set[str] = set()
    dictionary_values: dict[str, list[dict[str, Any]]] = {}
    dictionary_units: dict[str, str] = {}
    invalid_dictionary_units: set[str] = set()
    collection_values: dict[str, list[dict[str, str]]] = {}
    collection_units: dict[str, str] = {}
    invalid_collection_units: set[str] = set()
    assignments = (
        agent_output.get("assignments")
        if isinstance(agent_output.get("assignments"), list)
        else []
    )
    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue
        attr_id = str(assignment.get("attribute_id") or "").strip()
        attr = schema_by_id.get(attr_id)
        if not attr:
            continue
        value = str(assignment.get("value") or "").strip()
        if not value:
            continue
        if attr.get("value_mode") == "strict_enum":
            if attr_id in invalid_dictionary_units:
                continue
            selected_unit = ""
            if category_attribute_uses_unit(attr):
                canonical_unit = normalize_category_attribute_unit(
                    attr,
                    assignment.get("unit"),
                )
                if canonical_unit is None or (
                    attr_id in dictionary_units
                    and dictionary_units[attr_id] != canonical_unit
                ):
                    evidence_rejected.add(attr_id)
                    invalid_dictionary_units.add(attr_id)
                    dictionary_values.pop(attr_id, None)
                    dictionary_units.pop(attr_id, None)
                    continue
                selected_unit = canonical_unit
            value_id = str(assignment.get("dictionary_value_id") or "").strip()
            candidate = ledger.get(attr_id, value_id)
            if candidate is None:
                continue
            if is_brand_attribute(attr, platform=platform):
                has_enum_evidence = _has_brand_evidence(
                    candidate["value"],
                    product_context,
                    platform=platform,
                )
            else:
                # Ledger 证明候选来自当前平台，但不证明技术规格适用于当前商品。
                # 普通枚举仍需商品事实；“类型”枚举额外接受已确认类目身份/路径，
                # 从而不会因中俄文字面不同把真实的类目类型候选误拒。
                has_enum_evidence = _has_product_evidence(
                    candidate["value"],
                    product_context,
                ) or _has_category_type_evidence(
                    attr,
                    candidate,
                    platform=platform,
                    category_id=category_id,
                    category_path=category_path,
                )
            if not has_enum_evidence:
                evidence_rejected.add(attr_id)
                continue
            if selected_unit:
                dictionary_units[attr_id] = selected_unit
            dictionary_values.setdefault(attr_id, []).append(
                {
                    "dictionary_value_id": _dictionary_value_id(
                        candidate["dictionary_value_id"]
                    ),
                    "value": candidate["value"],
                }
            )
            continue
        if category_attribute_uses_unit(attr):
            if attr_id in invalid_collection_units:
                continue
            canonical_unit = normalize_category_attribute_unit(
                attr,
                assignment.get("unit"),
            )
            if canonical_unit is None or (
                attr.get("is_collection")
                and attr_id in collection_units
                and collection_units[attr_id] != canonical_unit
            ):
                evidence_rejected.add(attr_id)
                if attr.get("is_collection"):
                    invalid_collection_units.add(attr_id)
                    collection_values.pop(attr_id, None)
                    collection_units.pop(attr_id, None)
                continue
            if category_attribute_uses_numeric_unit(attr):
                normalized_unit_value = (
                    normalize_category_attribute_number_unit_value(
                        attr,
                        value,
                        canonical_unit,
                    )
                )
                if normalized_unit_value is None:
                    evidence_rejected.add(attr_id)
                    continue
                if _attribute_expects_weight_grams(attr, platform=platform):
                    has_unit_evidence = _has_deterministic_attribute_evidence(
                        normalized_unit_value["value"],
                        attr,
                        product_context,
                        platform=platform,
                    )
                else:
                    has_unit_evidence = _has_number_unit_evidence(
                        normalized_unit_value["value"],
                        normalized_unit_value["unit"],
                        product_context,
                    )
            else:
                normalized_unit_value = {
                    "value": value[:255],
                    "unit": canonical_unit,
                }
                has_unit_evidence = _has_product_evidence(value, product_context)
            if not has_unit_evidence:
                evidence_rejected.add(attr_id)
                continue
            if attr.get("is_collection"):
                collection_units[attr_id] = canonical_unit
                collection_values.setdefault(attr_id, []).append(
                    {"value": normalized_unit_value["value"]}
                )
            else:
                accepted[attr_id] = normalized_unit_value
            continue
        if _attribute_expects_weight_grams(attr, platform=platform):
            # 包装重量字段只认结构化 kg 事实的精确换算；标题、SKU 或其他
            # 属性里碰巧出现相同数字，不能作为该技术参数的证据。
            has_evidence = _has_deterministic_attribute_evidence(
                value,
                attr,
                product_context,
                platform=platform,
            )
        else:
            has_evidence = _has_product_evidence(value, product_context)
        if not has_evidence:
            evidence_rejected.add(attr_id)
            continue
        if value.upper() != attr_id.upper():
            options = (
                attr.get("options")
                if isinstance(attr.get("options"), list)
                else []
            )
            canonical_option = next(
                (
                    str(option)
                    for option in options
                    if str(option).strip().casefold() == value.casefold()
                ),
                "",
            )
            if canonical_option:
                value = canonical_option
            if attr.get("is_collection"):
                collection_values.setdefault(attr_id, []).append(
                    {"value": value[:255]}
                )
            else:
                accepted[attr_id] = value[:255]
    for attr_id, values in dictionary_values.items():
        if attr_id in invalid_dictionary_units:
            continue
        selected: dict[str, Any] = {"values": values}
        if unit := dictionary_units.get(attr_id):
            selected["unit"] = unit
        accepted[attr_id] = selected
    for attr_id, values in collection_values.items():
        if attr_id in invalid_collection_units:
            continue
        selected: dict[str, Any] = {"values": values}
        if unit := collection_units.get(attr_id):
            selected["unit"] = unit
        definition = schema_by_id.get(attr_id) or {}
        if category_attribute_value_is_valid(definition, selected):
            accepted[attr_id] = selected
        else:
            evidence_rejected.add(attr_id)
    return accepted, evidence_rejected


def unresolved_optional_category_attributes(
    product: dict[str, Any],
    platform: str,
    category_record: dict[str, Any] | None,
    *,
    limit: int = OPTIONAL_ATTRIBUTE_FILL_LIMIT,
) -> list[dict[str, Any]]:
    """草稿中尚未填写的可选类目属性，作为 best-effort 填充对象。

    与必填属性不同：可选属性填不出时静默跳过，不进入 need_review，
    也不阻断步骤。平台类目可以没有任何必填参数（如部分 Yandex
    类目），但发布仍要求至少一个参数值，因此可选属性也必须尝试填充。
    """

    normalized = normalize_product_model(product or {})
    platform = str(platform or "").strip().lower()
    drafts = (
        normalized.get("drafts")
        if isinstance(normalized.get("drafts"), dict)
        else {}
    )
    draft = drafts.get(platform) if isinstance(drafts.get(platform), dict) else {}
    attributes = (
        draft.get("attributes")
        if isinstance(draft.get("attributes"), dict)
        else {}
    )
    result: list[dict[str, Any]] = []
    for definition in category_attribute_schema(category_record):
        if definition.get("required"):
            continue
        attr_id = str(definition.get("id") or "").strip()
        if not attr_id:
            continue
        if category_attribute_value_is_valid(definition, attributes.get(attr_id)):
            continue
        result.append(definition)
        if len(result) >= max(0, int(limit)):
            break
    return result


def apply_ai_model_attribute_fill(
    product: dict[str, Any],
    platform: str,
    category_record: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    platform = str(platform or "").strip().lower()
    base_product = apply_ai_attribute_fill(product, platform, category_record)
    record = category_record if isinstance(category_record, dict) else {}
    drafts = (
        base_product.get("drafts")
        if isinstance(base_product.get("drafts"), dict)
        else {}
    )
    base_draft = deepcopy(
        drafts.get(platform) if isinstance(drafts.get(platform), dict) else {}
    )
    rule_filled: list[str] = []
    if base_draft:
        brand_attr_id = apply_no_brand_attribute(
            base_draft,
            product=base_product,
            platform=platform,
            record=record,
            category_id=str(record.get("category_id") or "").strip(),
            site=str(record.get("site") or "").strip(),
            loader=fetch_category_attribute_values,
        )
        if brand_attr_id:
            base_draft = apply_category_target_updates(
                base_draft,
                platform,
                {"attributes": deepcopy(base_draft.get("attributes") or {})},
                site=str(record.get("site") or "").strip(),
            )
            base_draft["validation_errors"] = [
                str(definition.get("id") or "").strip()
                for definition in unresolved_required_category_attributes(
                    {
                        **base_product,
                        "drafts": {
                            **drafts,
                            platform: base_draft,
                        },
                    },
                    platform,
                    category_record,
                )
                if str(definition.get("id") or "").strip()
            ]
            base_draft = apply_category_target_updates(
                base_draft,
                platform,
                {
                    "attributes": deepcopy(base_draft.get("attributes") or {}),
                    "validation_errors": list(
                        base_draft.get("validation_errors") or []
                    ),
                },
                site=str(record.get("site") or "").strip(),
            )
            base_product.setdefault("drafts", {})[platform] = base_draft
            base_product = normalize_product_model(base_product)
            rule_filled.append(brand_attr_id)
    schema = category_attribute_schema(category_record)
    if not schema:
        return base_product, {"source": "rules", "warning": "当前类目没有可填属性。"}
    meta: dict[str, Any] = {"source": "rules"}
    if rule_filled:
        meta["rule_filled"] = rule_filled
    agent_run: CategoryAttributeFillAgentRun | None = None
    try:
        agent_schema = unresolved_required_category_attributes(
            base_product,
            platform,
            category_record,
        ) + unresolved_optional_category_attributes(
            base_product,
            platform,
            category_record,
        )
        if not agent_schema:
            result_meta: dict[str, Any] = {
                "source": "rules",
                "ai_filled": [],
            }
            if rule_filled:
                result_meta["rule_filled"] = rule_filled
            return base_product, result_meta
        ledger = CategoryAttributeValueLedger.from_schema(agent_schema)
        toolset = build_category_attribute_value_toolset(
            platform=platform,
            category_record=category_record,
            ledger=ledger,
        )
        payload = _agent_payload(
            base_product,
            platform,
            category_record,
            agent_schema,
        )
        agent_run = run_category_attribute_fill_agent(
            payload,
            toolset,
            ledger,
        )
        ai_attrs, evidence_rejected = _validated_agent_attributes(
            agent_run.output,
            agent_schema,
            ledger,
            payload["product_context"],
            platform=platform,
            category_id=str(payload.get("category_id") or ""),
            category_path=str(payload.get("category_path") or ""),
        )
    except Exception as exc:
        meta["warning"] = f"AI 属性填充失败，已使用规则填充：{exc}"
        return base_product, meta

    updated = normalize_product_model(deepcopy(base_product))
    draft = deepcopy(updated.get("drafts", {}).get(platform, {}))
    attrs = deepcopy(
        draft.get("attributes")
        if isinstance(draft.get("attributes"), dict)
        else {}
    )
    for attr_id, value in ai_attrs.items():
        attrs[attr_id] = value

    draft["attributes"] = attrs
    draft = apply_category_target_updates(
        draft,
        platform,
        {"attributes": attrs},
        site=str(record.get("site") or "").strip(),
    )
    updated.setdefault("drafts", {})[platform] = draft
    need_review = sorted(
        str(definition.get("id") or "").strip()
        for definition in unresolved_required_category_attributes(
            updated,
            platform,
            category_record,
        )
    )
    draft["validation_errors"] = need_review
    draft = apply_category_target_updates(
        draft,
        platform,
        {"attributes": attrs, "validation_errors": need_review},
        site=str(record.get("site") or "").strip(),
    )
    updated["drafts"][platform] = draft
    meta["source"] = "ai_model"
    meta["ai_filled"] = sorted(ai_attrs)
    if evidence_rejected:
        meta["evidence_rejected"] = sorted(evidence_rejected)
        meta["warning"] = (
            "以下属性的模型建议缺少商品事实证据，已保留待人工复核："
            + "、".join(sorted(evidence_rejected))
        )
    if ledger.failed_attribute_ids:
        dictionary_warning = (
            "部分平台字典值查询失败，已保留待人工复核："
            + "、".join(sorted(ledger.failed_attribute_ids))
        )
        meta["warning"] = "；".join(
            item
            for item in (str(meta.get("warning") or ""), dictionary_warning)
            if item
        )
    if agent_run is not None:
        agent_run.finish_business_result(
            {
                "status": "completed",
                "filled_attribute_ids": sorted(ai_attrs),
                "need_review_attribute_ids": need_review,
                "enum_lookup_count": len(ledger.attempts),
            }
        )
    return normalize_product_model(updated), meta
