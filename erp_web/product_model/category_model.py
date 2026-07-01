from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .common import CATEGORY_CACHE_DIR, CATEGORY_CACHE_FILES, normalize_list
from .defaults import default_draft
from .merge_model import normalize_product_model

def _seed_category_cache_records(platform: str) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    if platform == "mercadolibre":
        records = [
            {
                "category_id": "MLM-100",
                "subject_id": "",
                "type_id": "",
                "name_original": "Electrónica > Audio > Audífonos",
                "name_cn": "耳机",
                "path_original": ["Electrónica", "Audio", "Audífonos"],
                "path_cn": ["电子产品", "音频", "耳机"],
                "parent_id": "MLM-10",
                "level": 3,
                "site": "MLM",
                "country": "MX",
                "platform": "mercadolibre",
                "keywords": ["耳机", "audifonos", "headphones", "audífonos"],
                "attributes_cache": {
                    "required": [
                        {"id": "BRAND", "name": "Brand", "required": True, "value_type": "string"},
                        {"id": "MODEL", "name": "Model", "required": True, "value_type": "string"},
                        {"id": "PACKAGE_LENGTH", "name": "Package length", "required": True, "value_type": "number", "unit": "cm"},
                        {"id": "PACKAGE_WIDTH", "name": "Package width", "required": True, "value_type": "number", "unit": "cm"},
                        {"id": "PACKAGE_HEIGHT", "name": "Package height", "required": True, "value_type": "number", "unit": "cm"},
                        {"id": "PACKAGE_WEIGHT", "name": "Package weight", "required": True, "value_type": "number", "unit": "kg"},
                    ],
                    "optional": [
                        {"id": "MATERIAL", "name": "Material", "required": False, "value_type": "string"},
                        {"id": "UNSURE_COLOR", "name": "Color", "required": False, "value_type": "select", "options": ["Black", "White", "Blue"]},
                    ],
                },
            },
            {
                "category_id": "MLM-200",
                "subject_id": "",
                "type_id": "",
                "name_original": "Hogar > Cocina > Botellas",
                "name_cn": "水瓶",
                "path_original": ["Hogar", "Cocina", "Botellas"],
                "path_cn": ["家居", "厨房", "水瓶"],
                "parent_id": "MLM-20",
                "level": 3,
                "site": "MLM",
                "country": "MX",
                "platform": "mercadolibre",
                "keywords": ["botella", "water bottle", "水杯", "水瓶"],
                "attributes_cache": {
                    "required": [
                        {"id": "BRAND", "name": "Brand", "required": True, "value_type": "string"},
                        {"id": "MODEL", "name": "Model", "required": True, "value_type": "string"},
                    ],
                    "optional": [
                        {"id": "MATERIAL", "name": "Material", "required": False, "value_type": "string"},
                    ],
                },
            },
        ]
        return {"platform": "mercadolibre", "site": "MLM", "updated_at": "2026-05-27T00:00:00", "records": records}
    if platform == "wildberries":
        records = [
            {
                "category_id": "WB-100",
                "subject_id": "WB-100",
                "type_id": "",
                "name_original": "Электроника > Аудио > Наушники",
                "name_cn": "耳机",
                "path_original": ["Электроника", "Аудио", "Наушники"],
                "path_cn": ["电子产品", "音频", "耳机"],
                "parent_id": "WB-10",
                "level": 3,
                "site": "WB",
                "country": "RU",
                "platform": "wildberries",
                "keywords": ["наушники", "headphones", "耳机"],
                "attributes_cache": {
                    "required": [
                        {"id": "brand", "name": "Brand", "required": True, "value_type": "string"},
                        {"id": "subject", "name": "Subject", "required": True, "value_type": "string"},
                        {"id": "package_length", "name": "Package length", "required": True, "value_type": "number", "unit": "cm"},
                        {"id": "package_width", "name": "Package width", "required": True, "value_type": "number", "unit": "cm"},
                        {"id": "package_height", "name": "Package height", "required": True, "value_type": "number", "unit": "cm"},
                        {"id": "package_weight", "name": "Package weight", "required": True, "value_type": "number", "unit": "kg"},
                    ],
                    "optional": [
                        {"id": "material", "name": "Material", "required": False, "value_type": "string"},
                        {"id": "color", "name": "Color", "required": False, "value_type": "string"},
                    ],
                },
            }
        ]
        return {"platform": "wildberries", "site": "WB", "updated_at": "2026-05-27T00:00:00", "records": records}
    records = [
        {
            "category_id": "OZON-100",
            "subject_id": "",
            "type_id": "OZ-100",
            "name_original": "Электроника > Аудио > Наушники",
            "name_cn": "耳机",
            "path_original": ["Электроника", "Аудио", "Наушники"],
            "path_cn": ["电子产品", "音频", "耳机"],
            "parent_id": "OZON-10",
            "level": 3,
            "site": "OZON",
            "country": "RU",
            "platform": "ozon",
            "keywords": ["наушники", "headphones", "耳机"],
            "attributes_cache": {
                "required": [
                    {"id": "brand", "name": "Brand", "required": True, "value_type": "string"},
                    {"id": "model", "name": "Model", "required": True, "value_type": "string"},
                    {"id": "package_length", "name": "Package length", "required": True, "value_type": "number", "unit": "cm"},
                    {"id": "package_width", "name": "Package width", "required": True, "value_type": "number", "unit": "cm"},
                    {"id": "package_height", "name": "Package height", "required": True, "value_type": "number", "unit": "cm"},
                    {"id": "package_weight", "name": "Package weight", "required": True, "value_type": "number", "unit": "kg"},
                ],
                "optional": [
                    {"id": "material", "name": "Material", "required": False, "value_type": "string"},
                ],
            },
        }
    ]
    return {"platform": "ozon", "site": "OZON", "updated_at": "2026-05-27T00:00:00", "records": records}


def _category_cache_path(platform: str) -> Path:
    return CATEGORY_CACHE_FILES.get(str(platform or "").strip().lower(), CATEGORY_CACHE_DIR / f"{str(platform or '').strip().lower()}_categories.json")


def _normalize_category_attr(attr: Any) -> dict[str, Any]:
    attr = attr if isinstance(attr, dict) else {}
    value_type = str(attr.get("value_type") or "string").strip() or "string"
    options = attr.get("options") if isinstance(attr.get("options"), list) else normalize_list(attr.get("options"))
    return {
        "id": str(attr.get("id") or attr.get("code") or "").strip(),
        "name": str(attr.get("name") or attr.get("label") or attr.get("id") or "").strip(),
        "required": bool(attr.get("required", False)),
        "value_type": value_type,
        "unit": str(attr.get("unit") or "").strip(),
        "options": normalize_list(options),
        "description": str(attr.get("description") or "").strip(),
    }


def _normalize_category_record(platform: str, item: Any) -> dict[str, Any]:
    item = item if isinstance(item, dict) else {}
    path_original = item.get("path_original") if isinstance(item.get("path_original"), list) else normalize_list(item.get("path_original"))
    path_cn = item.get("path_cn") if isinstance(item.get("path_cn"), list) else normalize_list(item.get("path_cn"))
    attrs = item.get("attributes_cache") if isinstance(item.get("attributes_cache"), dict) else {}
    return {
        "platform": str(item.get("platform") or platform or "").strip().lower(),
        "site": str(item.get("site") or item.get("country") or "").strip(),
        "country": str(item.get("country") or "").strip(),
        "category_id": str(item.get("category_id") or item.get("subject_id") or item.get("type_id") or "").strip(),
        "subject_id": str(item.get("subject_id") or item.get("category_id") or "").strip(),
        "type_id": str(item.get("type_id") or "").strip(),
        "name_original": str(item.get("name_original") or item.get("name") or "").strip(),
        "name_cn": str(item.get("name_cn") or item.get("name_zh") or "").strip(),
        "path_original": path_original,
        "path_cn": path_cn,
        "parent_id": str(item.get("parent_id") or "").strip(),
        "level": int(item.get("level") or len(path_original) or 0),
        "keywords": normalize_list(item.get("keywords")),
        "attributes_cache": {
            "required": [_normalize_category_attr(attr) for attr in (attrs.get("required") if isinstance(attrs.get("required"), list) else [])],
            "optional": [_normalize_category_attr(attr) for attr in (attrs.get("optional") if isinstance(attrs.get("optional"), list) else [])],
        },
        "updated_at": str(item.get("updated_at") or "").strip(),
    }


def _write_category_cache_file(path: Path, platform: str) -> dict[str, Any]:
    seed = _seed_category_cache_records(platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")
    return seed


def load_category_cache(platform: str) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    path = _category_cache_path(platform)
    if not path.exists():
        return _write_category_cache_file(path, platform)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _write_category_cache_file(path, platform)
    if not isinstance(raw, dict):
        return _write_category_cache_file(path, platform)
    records = raw.get("records") if isinstance(raw.get("records"), list) else []
    normalized = {
        "platform": str(raw.get("platform") or platform).strip().lower(),
        "site": str(raw.get("site") or "").strip(),
        "updated_at": str(raw.get("updated_at") or "").strip(),
        "records": [_normalize_category_record(platform, record) for record in records],
    }
    if not normalized["records"]:
        normalized = _write_category_cache_file(path, platform)
    return normalized


def category_cache_status(platform: str) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    path = _category_cache_path(platform)
    cache = load_category_cache(platform)
    return {
        "platform": platform,
        "path": str(path),
        "exists": path.exists(),
        "records": len(cache.get("records") or []),
        "updated_at": str(cache.get("updated_at") or "").strip(),
    }


def search_category_cache(platform: str, query: str = "", site: str = "", limit: int = 20) -> list[dict[str, Any]]:
    cache = load_category_cache(platform)
    query_text = str(query or "").strip().lower()
    site_text = str(site or "").strip().lower()
    results: list[dict[str, Any]] = []
    for record in cache.get("records") or []:
        if not isinstance(record, dict):
            continue
        if site_text and site_text not in {
            str(record.get("site") or "").strip().lower(),
            str(record.get("country") or "").strip().lower(),
        }:
            continue
        haystack = " ".join(
            [
                str(record.get("category_id") or ""),
                str(record.get("subject_id") or ""),
                str(record.get("type_id") or ""),
                str(record.get("name_original") or ""),
                str(record.get("name_cn") or ""),
                " > ".join(record.get("path_original") or []),
                " > ".join(record.get("path_cn") or []),
                " ".join(record.get("keywords") or []),
            ]
        ).lower()
        if query_text and query_text not in haystack:
            continue
        results.append(deepcopy(record))
        if len(results) >= max(1, int(limit or 20)):
            break
    return results


def find_category_record(platform: str, category_id: str, site: str = "") -> dict[str, Any] | None:
    wanted = str(category_id or "").strip().lower()
    if not wanted:
        return None
    cache = load_category_cache(platform)
    site_text = str(site or "").strip().lower()
    for record in cache.get("records") or []:
        if not isinstance(record, dict):
            continue
        record_ids = {
            str(record.get("category_id") or "").strip().lower(),
            str(record.get("subject_id") or "").strip().lower(),
            str(record.get("type_id") or "").strip().lower(),
        }
        if wanted not in record_ids:
            continue
        if site_text and site_text not in {str(record.get("site") or "").strip().lower(), str(record.get("country") or "").strip().lower()}:
            continue
        return deepcopy(record)
    return None


def _category_attribute_schema(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    record = record if isinstance(record, dict) else {}
    attrs = record.get("attributes_cache") if isinstance(record.get("attributes_cache"), dict) else {}
    required = [_normalize_category_attr(attr) for attr in (attrs.get("required") if isinstance(attrs.get("required"), list) else [])]
    optional = [_normalize_category_attr(attr) for attr in (attrs.get("optional") if isinstance(attrs.get("optional"), list) else [])]
    return required + optional


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
    normalized = normalize_product_model(product or {})
    platform = str(platform or "").strip().lower()
    record = category_record if isinstance(category_record, dict) else {}
    category_id = str(record.get("category_id") or record.get("subject_id") or record.get("type_id") or "").strip()
    category_path = _category_path_text(record)
    local_categories = deepcopy(normalized.get("local_platform_categories") if isinstance(normalized.get("local_platform_categories"), dict) else {})
    if category_id:
        local_categories[platform] = {
            **deepcopy(record),
            "category_id": category_id,
            "category_path": category_path,
        }
    normalized["local_platform_categories"] = local_categories
    if platform == "mercadolibre":
        normalized["category_id"] = category_id or normalized.get("category_id", "")
        normalized["category_path"] = category_path or normalized.get("category_path", "")
    elif platform == "wildberries":
        normalized["wb_subject_id"] = category_id or normalized.get("wb_subject_id", "")
        normalized["category_path"] = category_path or normalized.get("category_path", "")
    elif platform == "ozon":
        normalized["ozon_category_id"] = category_id or normalized.get("ozon_category_id", "")
        normalized["category_path"] = category_path or normalized.get("category_path", "")

    draft = deepcopy(normalized.get("drafts", {}).get(platform) if isinstance(normalized.get("drafts"), dict) else default_draft(platform))
    draft["category_id"] = category_id or str(draft.get("category_id") or "").strip()
    draft["category_path"] = category_path or str(draft.get("category_path") or "").strip()
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
        gtin_value = str(draft.get("upc") or product.get("upc") or source.get("upc") or source.get("gtin") or "").strip()
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
        value = str(draft.get("upc") or product.get("upc") or source.get("upc") or source.get("gtin") or "").strip()
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
        value = str(source.get("price") or product.get("detected_price") or "").strip()
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


def build_ai_attribute_fill(product: dict[str, Any], platform: str, category_record: dict[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_product_model(product or {})
    draft = deepcopy(normalized.get("drafts", {}).get(platform) if isinstance(normalized.get("drafts"), dict) else default_draft(platform))
    schema = _category_attribute_schema(category_record)
    attributes = deepcopy(draft.get("attributes") or {})
    need_review: list[str] = []
    safe_auto_fill_ids = {
        "BRAND",
        "MODEL",
        "GTIN",
        "UPC",
        "UNIVERSAL_PRODUCT_CODE",
        "EMPTY_GTIN_REASON",
        "PACKAGE_LENGTH",
        "PACKAGE_WIDTH",
        "PACKAGE_HEIGHT",
        "PACKAGE_WEIGHT",
        "SELLER_SKU",
        "SKU",
    }
    for attr in schema:
        attr_id = str(attr.get("id") or "").strip()
        if not attr_id:
            continue
        current_value = str(attributes.get(attr_id) or "").strip()
        if current_value and current_value.upper() == attr_id.upper():
            attributes.pop(attr_id, None)
        gtin_value = str(draft.get("upc") or normalized.get("upc") or "").strip()
        if attr_id.upper() == "EMPTY_GTIN_REASON" and gtin_value:
            attributes.pop(attr_id, None)
            continue
        if attr_id.upper() in {"GTIN", "UPC", "UNIVERSAL_PRODUCT_CODE"} and not gtin_value and draft.get("allow_gtin_exemption"):
            attributes.pop(attr_id, None)
            continue
        attr_required = bool(attr.get("required"))
        can_auto_fill = attr_required or attr_id.upper() in safe_auto_fill_ids or attr_id in attributes
        if not can_auto_fill:
            continue
        value, confident = _attribute_value_from_source(normalized, platform, attr)
        if value:
            attributes[attr_id] = value
        elif attr_required:
            need_review.append(attr_id)
        if attr_required and not confident:
            need_review.append(attr_id)
    if not attributes.get("BRAND"):
        attributes["BRAND"] = str(draft.get("brand") or normalized.get("brand") or normalized.get("source", {}).get("brand") or "Generic").strip() or "Generic"
    if not attributes.get("MODEL"):
        attributes["MODEL"] = str(draft.get("model") or normalized.get("model") or normalized.get("source", {}).get("model") or "General").strip() or "General"
    return {
        "attributes": attributes,
        "need_review": sorted({str(item).strip() for item in need_review if str(item).strip()}),
        "category_id": str(draft.get("category_id") or "").strip(),
        "category_path": str(draft.get("category_path") or "").strip(),
    }


def apply_ai_attribute_fill(product: dict[str, Any], platform: str, category_record: dict[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_product_model(product or {})
    platform = str(platform or "").strip().lower()
    filled = build_ai_attribute_fill(normalized, platform, category_record)
    draft = deepcopy(normalized.get("drafts", {}).get(platform) if isinstance(normalized.get("drafts"), dict) else default_draft(platform))
    draft["attributes"] = deepcopy(filled.get("attributes") or {})
    draft["validation_errors"] = list(filled.get("need_review") or [])
    normalized.setdefault("drafts", {})[platform] = draft
    local_categories = deepcopy(normalized.get("local_platform_categories") if isinstance(normalized.get("local_platform_categories"), dict) else {})
    if isinstance(category_record, dict) and str(category_record.get("category_id") or category_record.get("subject_id") or category_record.get("type_id") or "").strip():
        local_categories[platform] = {
            **deepcopy(category_record),
            "category_id": str(category_record.get("category_id") or category_record.get("subject_id") or category_record.get("type_id") or "").strip(),
            "category_path": _category_path_text(category_record),
        }
    normalized["local_platform_categories"] = local_categories
    return normalize_product_model(normalized)


def validate_category_precheck(product: dict[str, Any], platform: str, category_record: dict[str, Any] | None) -> list[str]:
    normalized = normalize_product_model(product or {})
    platform = str(platform or "").strip().lower()
    draft = normalized.get("drafts", {}).get(platform) if isinstance(normalized.get("drafts"), dict) else {}
    errors: list[str] = []
    if platform == "mercadolibre":
        if not str(draft.get("category_id") or normalized.get("category_id") or "").strip():
            errors.append("category_id")
    elif platform == "wildberries":
        if not str(draft.get("category_id") or normalized.get("wb_subject_id") or "").strip():
            errors.append("category_id")
    elif platform == "ozon":
        if not str(draft.get("category_id") or normalized.get("ozon_category_id") or "").strip():
            errors.append("category_id")
    if not str(draft.get("brand") or "").strip():
        errors.append("brand")
    if not str(draft.get("model") or "").strip():
        errors.append("model")
    pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    for field in ["length_cm", "width_cm", "height_cm", "weight_kg"]:
        if not str(pkg.get(field) or "").strip():
            errors.append(f"package_dimensions.{field}")
    values = draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {}
    package_attr_values = {
        "PACKAGE_LENGTH": str(pkg.get("length_cm") or "").strip(),
        "PACKAGE_WIDTH": str(pkg.get("width_cm") or "").strip(),
        "PACKAGE_HEIGHT": str(pkg.get("height_cm") or "").strip(),
        "PACKAGE_WEIGHT": str(pkg.get("weight_kg") or "").strip(),
    }
    for attr in _category_attribute_schema(category_record):
        if not attr.get("required"):
            continue
        attr_id = str(attr.get("id") or "").strip()
        if not attr_id:
            continue
        if attr_id.upper() == "EMPTY_GTIN_REASON" and str(draft.get("upc") or normalized.get("upc") or values.get("GTIN") or "").strip():
            continue
        if attr_id.upper() in {"GTIN", "UPC", "UNIVERSAL_PRODUCT_CODE"} and str(values.get("EMPTY_GTIN_REASON") or "").strip():
            continue
        if package_attr_values.get(attr_id):
            continue
        if not str(values.get(attr_id) or "").strip():
            errors.append(f"attributes.{attr_id}")
    return list(dict.fromkeys(errors))
