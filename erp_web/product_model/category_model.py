from __future__ import annotations

from copy import deepcopy
from typing import Any

from erp_web.schemas.category_grouping import is_listing_grouping_attribute, listing_grouping_value
from erp_web.schemas.category import (
    category_attribute_schema,
    category_attribute_value_is_valid,
)

from .attribute_matching import source_package_dimensions
from .defaults import default_draft
from .merge_model import normalize_product_model
from .sku_model import selected_skus


def _category_path_text(record: dict[str, Any] | None) -> str:
    record = record if isinstance(record, dict) else {}
    path_cn = record.get("path_cn") if isinstance(record.get("path_cn"), list) else []
    path_original = record.get("path_original") if isinstance(record.get("path_original"), list) else []
    path = path_cn or path_original
    return " > ".join([str(item).strip() for item in path if str(item).strip()])


def _source_dimension_dict(product: dict[str, Any]) -> dict[str, str]:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    dimensions = source_package_dimensions(source)
    return {
        "length_cm": str(dimensions.get("length_cm") or product.get("package_length_cm") or "").strip(),
        "width_cm": str(dimensions.get("width_cm") or product.get("package_width_cm") or "").strip(),
        "height_cm": str(dimensions.get("height_cm") or product.get("package_height_cm") or "").strip(),
        "weight_kg": str(source.get("weight_kg") or product.get("weight_kg") or "").strip(),
    }


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
        "EMPTY_GTIN_REASON",
        "ITEM_CONDITION",
    }
    if platform == "mercadolibre":
        # Mercado 的 BRAND/MODEL 在草稿根字段编辑并由发布编译器映射；
        # attributes 中不保留第二份可能互相冲突的值。
        derived_attribute_ids.update({"BRAND", "MODEL"})
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
    draft["brand"] = str(draft.get("brand") or normalized.get("brand") or normalized.get("source", {}).get("brand") or "Generic").strip() or "Generic"
    draft["model"] = str(draft.get("model") or normalized.get("model") or normalized.get("source", {}).get("model") or "General").strip() or "General"
    dims = _source_dimension_dict(normalized)
    draft_pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    draft["package_dimensions"] = {
        "length_cm": str(draft_pkg.get("length_cm") or dims["length_cm"] or "").strip(),
        "width_cm": str(draft_pkg.get("width_cm") or dims["width_cm"] or "").strip(),
        "height_cm": str(draft_pkg.get("height_cm") or dims["height_cm"] or "").strip(),
        "weight_kg": str(draft_pkg.get("weight_kg") or dims["weight_kg"] or "").strip(),
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
}
_GTIN_ATTRIBUTE_IDS = {"GTIN", "UPC", "UNIVERSAL_PRODUCT_CODE"}


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
    gtin_value = str(
        draft.get("upc")
        or attributes.get("GTIN")
        or ""
    ).strip()
    if attr_id_upper == "EMPTY_GTIN_REASON" and gtin_value:
        return True
    if attr_id_upper == "EMPTY_GTIN_REASON" and draft.get("allow_gtin_exemption"):
        return True
    if attr_id_upper in _GTIN_ATTRIBUTE_IDS and str(
        attributes.get("EMPTY_GTIN_REASON") or ""
    ).strip():
        return True
    if attr_id_upper in _GTIN_ATTRIBUTE_IDS and (
        gtin_value or draft.get("allow_gtin_exemption")
    ):
        return True
    if attr_id_upper == "BRAND":
        return bool(str(attributes.get(attr_id) or draft.get("brand") or "").strip())
    if attr_id_upper == "MODEL":
        return bool(str(attributes.get(attr_id) or draft.get("model") or "").strip())
    if attr_id_upper == "SELLER_SKU":
        return bool(str(draft.get("sku") or "").strip())
    if attr_id_upper == "ITEM_CONDITION":
        # 当前 Mercado 发布能力只支持 New，wire 值由 listing_model 契约生成。
        return platform == "mercadolibre"
    package_field = _PACKAGE_ATTRIBUTE_FIELDS.get(attr_id_upper)
    package = (
        draft.get("package_dimensions")
        if isinstance(draft.get("package_dimensions"), dict)
        else {}
    )
    if package_field and str(package.get(package_field) or "").strip():
        return True
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
    return [
        definition
        for definition in category_attribute_schema(category_record)
        if definition.get("required")
        and not _required_attribute_is_satisfied(
            normalized,
            draft,
            definition,
            platform,
        )
    ]






def validate_category_precheck(product: dict[str, Any], platform: str, category_record: dict[str, Any] | None) -> list[str]:
    normalized = normalize_product_model(product or {})
    platform = str(platform or "").strip().lower()
    draft = normalized.get("drafts", {}).get(platform) if isinstance(normalized.get("drafts"), dict) else {}
    missing_fields: list[str] = []
    if not str(draft.get("category_id") or "").strip():
        missing_fields.append("category_id")
    missing_fields.extend(
        f"attributes.{definition['id']}"
        for definition in unresolved_required_category_attributes(
            normalized,
            platform,
            category_record,
        )
    )
    return list(dict.fromkeys(missing_fields))
