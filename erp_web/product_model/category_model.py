from __future__ import annotations

from copy import deepcopy
from typing import Any

from erp_web.schemas.category import (
    category_attribute_schema,
    category_attribute_value_is_valid,
)

from .common import normalize_list
from .defaults import default_draft
from .merge_model import normalize_product_model


def _category_path_text(record: dict[str, Any] | None) -> str:
    record = record if isinstance(record, dict) else {}
    path_cn = record.get("path_cn") if isinstance(record.get("path_cn"), list) else []
    path_original = record.get("path_original") if isinstance(record.get("path_original"), list) else []
    path = path_cn or path_original
    return " > ".join([str(item).strip() for item in path if str(item).strip()])


def _source_dimension_dict(product: dict[str, Any]) -> dict[str, str]:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    dimensions = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    return {
        "length_cm": str(dimensions.get("length_cm") or product.get("package_length_cm") or "").strip(),
        "width_cm": str(dimensions.get("width_cm") or product.get("package_width_cm") or "").strip(),
        "height_cm": str(dimensions.get("height_cm") or product.get("package_height_cm") or "").strip(),
        "weight_kg": str(source.get("weight_kg") or product.get("weight_kg") or "").strip(),
    }


def apply_category_selection(product: dict[str, Any], platform: str, category_record: dict[str, Any] | None) -> dict[str, Any]:
    """保存类目身份到平台草稿；平台规则不再持久化进商品模型。"""

    normalized = normalize_product_model(product or {})
    platform = str(platform or "").strip().lower()
    record = category_record if isinstance(category_record, dict) else {}
    category_id = str(record.get("category_id") or record.get("subject_id") or record.get("type_id") or "").strip()
    category_path = _category_path_text(record) or str(record.get("category_path") or "").strip()
    draft = deepcopy(normalized.get("drafts", {}).get(platform) if isinstance(normalized.get("drafts"), dict) else default_draft(platform))
    draft["category_id"] = category_id or str(draft.get("category_id") or "").strip()
    draft["category_path"] = category_path or str(draft.get("category_path") or "").strip()
    if str(record.get("description_category_id") or "").strip():
        draft["description_category_id"] = str(record["description_category_id"]).strip()
    draft["attributes"] = deepcopy(draft.get("attributes") or {})
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
    normalized["drafts"][platform] = draft
    return normalize_product_model(normalized)


def _attribute_value_from_source(product: dict[str, Any], platform: str, attr: dict[str, Any]) -> tuple[str, bool]:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    draft = product.get("drafts", {}).get(platform) if isinstance(product.get("drafts"), dict) else {}
    attr_id = str(attr.get("id") or "").strip()
    attr_name = str(attr.get("name") or "").strip().lower()
    source_dims = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    draft_pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    source_material = str(source.get("material") or "").strip()
    source_package = normalize_list(source.get("package_contents"))

    def result(value: str, confident: bool = True) -> tuple[str, bool]:
        return (str(value).strip(), confident)

    if "brand" in attr_id.lower() or "brand" in attr_name:
        return result(str(draft.get("brand") or product.get("brand") or source.get("brand") or "Generic").strip() or "Generic")
    if "model" in attr_id.lower() or "model" in attr_name:
        return result(str(draft.get("model") or product.get("model") or source.get("model") or "General").strip() or "General")
    if attr_id.upper() == "EMPTY_GTIN_REASON" or "empty gtin reason" in attr_name:
        gtin_value = str(draft.get("upc") or product.get("upc") or source.get("upc") or "").strip()
        if gtin_value:
            return result("", True)
        if not draft.get("allow_gtin_exemption"):
            return result("", False)
        options = [str(option).strip() for option in (attr.get("options") if isinstance(attr.get("options"), list) else []) if str(option).strip()]
        preferred = [
            "Product exempt from GTIN",
            "The product does not have a registered code",
            "No registrado",
            "Otro",
        ]
        for candidate in preferred:
            for option in options:
                if candidate.lower() in option.lower() or option.lower() in candidate.lower():
                    return result(option, True)
        return result(options[0] if options else "Product exempt from GTIN", True)
    if attr_id.upper() in {"GTIN", "UPC", "UNIVERSAL_PRODUCT_CODE"} or attr_id.lower() in {"gtin", "upc"} or "universal product code" in attr_name:
        value = str(draft.get("upc") or product.get("upc") or source.get("upc") or "").strip()
        return result(value, bool(value))
    attr_id_upper = attr_id.upper()
    is_package_attr = "PACKAGE" in attr_id_upper or "package" in attr_name
    if is_package_attr and any(token in attr_id.lower() or token in attr_name for token in ["package_length", "length", "longitud", "largo"]):
        value = str(draft_pkg.get("length_cm") or source_dims.get("length_cm") or "").strip()
        return result(value, bool(value))
    if is_package_attr and any(token in attr_id.lower() or token in attr_name for token in ["package_width", "width", "ancho"]):
        value = str(draft_pkg.get("width_cm") or source_dims.get("width_cm") or "").strip()
        return result(value, bool(value))
    if is_package_attr and any(token in attr_id.lower() or token in attr_name for token in ["package_height", "height", "alto"]):
        value = str(draft_pkg.get("height_cm") or source_dims.get("height_cm") or "").strip()
        return result(value, bool(value))
    if is_package_attr and any(token in attr_id.lower() or token in attr_name for token in ["package_weight", "weight", "peso"]):
        value = str(draft_pkg.get("weight_kg") or source_dims.get("weight_kg") or source.get("weight_kg") or "").strip()
        return result(value, bool(value))
    if "material" in attr_id.lower() or "material" in attr_name or "材质" in attr_name:
        return result(source_material, bool(source_material))
    if attr_id_upper in {"PACKAGE_CONTENTS", "PACKAGE_INCLUDES"} or "package contents" in attr_name or "包装清单" in attr_name:
        value = " / ".join(source_package)
        return result(value, bool(value))
    if attr_id_upper in {"TITLE", "CATALOG_TITLE", "INVOICE_PRODUCT_NAME"} or attr_name in {"title", "catalog title", "invoice product name"}:
        value = str(source.get("title") or product.get("name") or "").strip()
        return result(value, bool(value))
    if "price" in attr_id.lower():
        value = str(source.get("price") or product.get("cost") or "").strip()
        return result(value, bool(value))
    if "sku" in attr_id.lower():
        value = str(product.get("sku") or "").strip()
        return result(value, bool(value))
    if "color" in attr_id.lower() or "color" in attr_name or "颜色" in attr_name:
        colors = normalize_list(product.get("colors")) or normalize_list(source.get("colors"))
        if colors:
            return result(colors[0], True)
        return result("", False)
    options = attr.get("options") if isinstance(attr.get("options"), list) else []
    if options:
        source_text = " ".join([str(source.get("title") or ""), str(source.get("description") or ""), " ".join(normalize_list(source.get("bullets")))]).lower()
        for option in options:
            option_text = str(option or "").strip()
            normalized_option = option_text.lower()
            if len(normalized_option) >= 3 and normalized_option in source_text:
                return result(option_text, True)
        return result("", False)
    return result("", False)


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
) -> bool:
    attr_id = str(definition.get("id") or "").strip()
    attr_id_upper = attr_id.upper()
    attributes = (
        draft.get("attributes")
        if isinstance(draft.get("attributes"), dict)
        else {}
    )
    gtin_value = str(
        draft.get("upc")
        or normalized.get("upc")
        or attributes.get("GTIN")
        or ""
    ).strip()
    if attr_id_upper == "EMPTY_GTIN_REASON" and gtin_value:
        return True
    if attr_id_upper in _GTIN_ATTRIBUTE_IDS and str(
        attributes.get("EMPTY_GTIN_REASON") or ""
    ).strip():
        return True
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
        and not _required_attribute_is_satisfied(normalized, draft, definition)
    ]


def build_ai_attribute_fill(
    product: dict[str, Any],
    platform: str,
    category_record: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized = normalize_product_model(product or {})
    platform = str(platform or "").strip().lower()
    draft = deepcopy(
        normalized.get("drafts", {}).get(platform)
        if isinstance(normalized.get("drafts"), dict)
        else default_draft(platform)
    )
    attributes = deepcopy(draft.get("attributes") or {})
    gtin_value = str(draft.get("upc") or normalized.get("upc") or "").strip()
    if gtin_value:
        attributes.pop("EMPTY_GTIN_REASON", None)
    elif draft.get("allow_gtin_exemption"):
        for attr_id in _GTIN_ATTRIBUTE_IDS:
            attributes.pop(attr_id, None)
    for definition in unresolved_required_category_attributes(
        normalized,
        platform,
        category_record,
    ):
        attr_id = str(definition.get("id") or "").strip()
        attributes.pop(attr_id, None)
        if definition.get("value_mode") == "strict_enum":
            continue
        value, confident = _attribute_value_from_source(
            normalized,
            platform,
            definition,
        )
        if not value or not confident:
            continue
        canonical_option = next(
            (
                str(option)
                for option in definition.get("options") or []
                if str(option).strip().casefold() == value.casefold()
            ),
            "",
        )
        attributes[attr_id] = canonical_option or value

    candidate = normalize_product_model(deepcopy(normalized))
    candidate_draft = deepcopy(draft)
    candidate_draft["attributes"] = attributes
    candidate.setdefault("drafts", {})[platform] = candidate_draft
    need_review = [
        str(definition.get("id") or "").strip()
        for definition in unresolved_required_category_attributes(
            candidate,
            platform,
            category_record,
        )
    ]
    return {
        "attributes": attributes,
        "need_review": need_review,
        "category_id": str(draft.get("category_id") or "").strip(),
        "category_path": str(draft.get("category_path") or "").strip(),
    }


def apply_ai_attribute_fill(product: dict[str, Any], platform: str, category_record: dict[str, Any] | None) -> dict[str, Any]:
    """规则填充只持久化属性值与校验结果；不保存平台规则副本。"""

    normalized = normalize_product_model(product or {})
    platform = str(platform or "").strip().lower()
    filled = build_ai_attribute_fill(normalized, platform, category_record)
    draft = deepcopy(normalized.get("drafts", {}).get(platform) if isinstance(normalized.get("drafts"), dict) else default_draft(platform))
    draft["attributes"] = deepcopy(filled.get("attributes") or {})
    draft["validation_errors"] = list(filled.get("need_review") or [])
    normalized.setdefault("drafts", {})[platform] = draft
    return normalize_product_model(normalized)


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
