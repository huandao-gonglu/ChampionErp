from __future__ import annotations

from copy import deepcopy
from typing import Any

from erp_web.schemas.category_constraints import category_attribute_constraint_error
from erp_web.schemas.category_grouping import is_listing_grouping_attribute, listing_grouping_value
from erp_web.schemas.category import (
    category_attribute_schema,
    category_attribute_value_is_valid,
    category_attribute_value_mode,
)

from .defaults import default_draft
from .merge_model import normalize_product_model
from .sku_model import selected_skus


def _category_path_text(record: dict[str, Any] | None) -> str:
    record = record if isinstance(record, dict) else {}
    path_cn = record.get("path_cn") if isinstance(record.get("path_cn"), list) else []
    path_original = record.get("path_original") if isinstance(record.get("path_original"), list) else []
    path = path_cn or path_original
    return " > ".join([str(item).strip() for item in path if str(item).strip()])


def apply_category_target_updates(
    draft: dict[str, Any],
    platform: str,
    updates: dict[str, Any],
    *,
    site: str = "",
) -> dict[str, Any]:
    """把类目写入限定到唯一目标；根字段由 normalize 派生。"""

    updated = deepcopy(draft)
    targets = [
        deepcopy(target)
        for target in updated.get("target_sites", [])
        if isinstance(target, dict)
    ]
    platform_key = str(platform or "").strip().lower()
    site_key = str(site or "").strip().casefold()
    platform_indexes = [
        index
        for index, target in enumerate(targets)
        if str(target.get("platform") or "").strip().lower() == platform_key
    ]
    exact_indexes = [
        index
        for index in platform_indexes
        if site_key
        and str(targets[index].get("site") or "").strip().casefold()
        == site_key
    ]
    candidates = exact_indexes if site_key else platform_indexes
    if len(candidates) != 1:
        raise ValueError(
            f"类目写入目标不唯一：{platform_key or '<empty>'}:{site or '<empty>'}"
        )
    index = candidates[0]
    targets[index] = {**targets[index], **deepcopy(updates)}
    updated["target_sites"] = targets
    return updated


def apply_category_selection(product: dict[str, Any], platform: str, category_record: dict[str, Any] | None) -> dict[str, Any]:
    """保存类目身份到平台草稿；平台规则不再持久化进商品模型。"""

    normalized = normalize_product_model(product or {})
    platform = str(platform or "").strip().lower()
    record = category_record if isinstance(category_record, dict) else {}
    category_id = str(record.get("category_id") or record.get("subject_id") or record.get("type_id") or "").strip()
    category_path = _category_path_text(record) or str(record.get("category_path") or "").strip()
    draft = deepcopy(normalized.get("drafts", {}).get(platform) if isinstance(normalized.get("drafts"), dict) else default_draft(platform))
    previous_category_id = str(draft.get("category_id") or "").strip()
    draft["category_id"] = category_id or str(draft.get("category_id") or "").strip()
    draft["category_path"] = category_path or str(draft.get("category_path") or "").strip()
    if str(record.get("description_category_id") or "").strip():
        draft["description_category_id"] = str(record["description_category_id"]).strip()
    elif (
        platform == "ozon"
        and category_id
        and category_id != previous_category_id
    ):
        # type_id 改变后旧 description_category_id 已不再构成同一类目对；
        # 清空后交给实时 Ozon 类目解析补齐，不能保存 NEW-TYPE/OLD-DESC。
        draft["description_category_id"] = ""
    allowed_attribute_ids = {
        str(definition.get("id") or "").strip()
        for definition in category_attribute_schema(record)
        if str(definition.get("id") or "").strip()
        and not bool(definition.get("read_only"))
        and not is_listing_grouping_attribute(platform, definition)
    }
    derived_attribute_ids = {
        "PACKAGE_LENGTH",
        "PACKAGE_WIDTH",
        "PACKAGE_HEIGHT",
        "PACKAGE_WEIGHT",
        "SELLER_PACKAGE_LENGTH",
        "SELLER_PACKAGE_WIDTH",
        "SELLER_PACKAGE_HEIGHT",
        "SELLER_PACKAGE_WEIGHT",
        "SELLER_SKU",
        "GTIN",
        "UPC",
        "UNIVERSAL_PRODUCT_CODE",
        "ITEM_CONDITION",
    }
    current_attributes = (
        draft.get("attributes")
        if isinstance(draft.get("attributes"), dict)
        else {}
    )
    draft["attributes"] = {
        str(attr_id): deepcopy(value)
        for attr_id, value in current_attributes.items()
        if str(attr_id) in allowed_attribute_ids
        and str(attr_id).upper() not in derived_attribute_ids
    }
    draft = apply_category_target_updates(
        draft,
        platform,
        {
            "category_id": draft["category_id"],
            "description_category_id": str(
                draft.get("description_category_id") or ""
            ).strip(),
            "category_path": draft["category_path"],
            "attributes": draft["attributes"],
        },
        site=str(record.get("site") or "").strip(),
    )
    normalized["drafts"][platform] = draft
    return normalize_product_model(normalized)




_PACKAGE_ATTRIBUTE_FIELDS = {
    "PACKAGE_LENGTH": "length_cm",
    "PACKAGE_WIDTH": "width_cm",
    "PACKAGE_HEIGHT": "height_cm",
    "PACKAGE_WEIGHT": "weight_kg",
    "SELLER_PACKAGE_LENGTH": "length_cm",
    "SELLER_PACKAGE_WIDTH": "width_cm",
    "SELLER_PACKAGE_HEIGHT": "height_cm",
    "SELLER_PACKAGE_WEIGHT": "weight_kg",
}
_GTIN_ATTRIBUTE_IDS = {"GTIN", "UPC", "UNIVERSAL_PRODUCT_CODE"}


def _raw_scalar_present(value: Any) -> bool:
    if isinstance(value, dict):
        if isinstance(value.get("values"), list):
            return any(
                isinstance(item, dict) and str(item.get("value") or "").strip()
                for item in value["values"]
            )
        return bool(str(value.get("value") or "").strip())
    if isinstance(value, (list, tuple, set)):
        return any(str(item or "").strip() for item in value)
    return bool(str(value or "").strip())


def _required_attribute_is_satisfied(
    normalized: dict[str, Any],
    draft: dict[str, Any],
    definition: dict[str, Any],
    platform: str,
) -> bool:
    if is_listing_grouping_attribute(platform, definition):
        if (draft.get("grouping") or {}).get("mode") == "separate":
            selected = selected_skus(normalized, draft)
            if selected:
                return all(
                    bool(listing_grouping_value(draft, definition, seller_sku=row.get("sku", "")))
                    for _, row in selected
                )
        return bool(listing_grouping_value(draft, definition))
    attr_id = str(definition.get("id") or "").strip()
    attr_id_upper = attr_id.upper()
    attributes = (
        draft.get("attributes")
        if isinstance(draft.get("attributes"), dict)
        else {}
    )
    selected = selected_skus(normalized, draft)
    gtin_value = bool(selected) and all(str(fact.get("barcode") or "").strip() for fact, _ in selected)
    reason = attributes.get("EMPTY_GTIN_REASON")
    reason_present = (
        bool(reason.get("values")) if isinstance(reason, dict) and "values" in reason
        else bool(str(reason or "").strip())
    )
    legacy_exemption = (
        platform == "mercadolibre" and bool(draft.get("allow_gtin_exemption"))
        and "EMPTY_GTIN_REASON" not in attributes
    )
    if platform == "mercadolibre" and attr_id_upper == "EMPTY_GTIN_REASON" and (gtin_value or legacy_exemption):
        return True
    if attr_id_upper in _GTIN_ATTRIBUTE_IDS and (
        gtin_value or (platform == "mercadolibre" and (reason_present or legacy_exemption))
    ):
        return True
    if attr_id_upper in {"BRAND", "MODEL"}:
        raw_val = attributes.get(attr_id)
        if category_attribute_value_mode(definition) == "strict_enum":
            return category_attribute_value_is_valid(definition, raw_val)
        if _raw_scalar_present(raw_val):
            return category_attribute_value_is_valid(definition, raw_val)
        fallback_key = "brand" if attr_id_upper == "BRAND" else "model"
        return bool(str(normalized.get(fallback_key) or "").strip())
    if attr_id_upper == "SELLER_SKU":
        return bool(selected) and all(str(row.get("sku") or "").strip() for _, row in selected)
    if attr_id_upper == "ITEM_CONDITION":
        # 当前 Mercado 发布能力只支持 New，wire 值由 listing_model 契约生成。
        return platform == "mercadolibre"
    package_field = _PACKAGE_ATTRIBUTE_FIELDS.get(attr_id_upper)
    if package_field:
        return bool(selected) and all(
            str((fact.get("package_dimensions") or {}).get(package_field) or "").strip()
            for fact, _ in selected
        )
    return category_attribute_value_is_valid(definition, attributes.get(attr_id))


def unresolved_required_category_attributes(
    product: dict[str, Any],
    platform: str,
    category_record: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """返回规则、Agent 与发布预检共同认可的未解决必填属性。"""

    normalized = normalize_product_model(product or {})
    platform = str(platform or "").strip().lower()
    drafts = (
        normalized.get("drafts")
        if isinstance(normalized.get("drafts"), dict)
        else {}
    )
    draft = drafts.get(platform) if isinstance(drafts.get(platform), dict) else {}
    return unresolved_required_attributes_in_draft(
        normalized, draft, platform, category_attribute_schema(category_record),
    )


def unresolved_required_attributes_in_draft(
    product: dict[str, Any],
    draft: dict[str, Any],
    platform: str,
    definitions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """检查已规范化的当前草稿视图，复用必填规则而不再次复制整个商品。"""
    return [
        definition for definition in definitions
        if definition.get("required")
        and not _required_attribute_is_satisfied(product, draft, definition, platform)
    ]






def validate_category_precheck(product: dict[str, Any], platform: str, category_record: dict[str, Any] | None) -> list[str]:
    normalized = normalize_product_model(product or {})
    platform = str(platform or "").strip().lower()
    draft = normalized.get("drafts", {}).get(platform) if isinstance(normalized.get("drafts"), dict) else {}
    missing_fields: list[str] = []
    if not str(draft.get("category_id") or "").strip():
        missing_fields.append("category_id")
    definitions = category_attribute_schema(category_record)
    missing_fields.extend(
        f"attributes.{definition['id']}"
        for definition in unresolved_required_attributes_in_draft(
            normalized, draft, platform, definitions,
        )
    )
    attributes = draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {}
    for definition in definitions:
        attr_id = str(definition.get("id") or "").strip()
        if not attr_id or attr_id not in attributes:
            continue
        raw_value = attributes.get(attr_id)
        if not _raw_scalar_present(raw_value):
            continue
        if category_attribute_constraint_error(definition, raw_value):
            missing_fields.append(f"attributes.{attr_id}")
    return list(dict.fromkeys(missing_fields))
