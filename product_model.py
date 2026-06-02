from __future__ import annotations

from copy import deepcopy
import json
import re
from pathlib import Path
from typing import Any

PLATFORMS = ("mercadolibre", "wildberries", "ozon")
IMAGE_ORIGINS = ("source", "amazon", "1688", "browser", "html_import", "manual", "local_upload", "ai_generated", "chatgpt_import", "extension")
IMAGE_USAGES = ("main", "detail", "size", "scene", "package", "selling_point", "material", "unknown", "other")
SOURCE_COMPAT_IMAGE_ORIGINS = {"source", "amazon", "1688", "browser", "html_import", "manual", "extension"}
APP_DIR = Path(__file__).resolve().parent
CATEGORY_CACHE_DIR = APP_DIR / "data" / "category_cache"
CATEGORY_CACHE_FILES = {
    "mercadolibre": CATEGORY_CACHE_DIR / "mercadolibre_mlm_categories.json",
    "wildberries": CATEGORY_CACHE_DIR / "wb_subjects.json",
    "ozon": CATEGORY_CACHE_DIR / "ozon_categories.json",
}


def normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def parse_dimensions_text(value: Any) -> dict[str, str]:
    text = str(value or "").strip()
    if not text:
        return {"length_cm": "", "width_cm": "", "height_cm": ""}
    match = re.search(
        r"([0-9]+(?:\.[0-9]+)?)\s*[x×*]\s*([0-9]+(?:\.[0-9]+)?)\s*[x×*]\s*([0-9]+(?:\.[0-9]+)?)",
        text.replace("厘米", "cm"),
        flags=re.I,
    )
    if match:
        return {
            "length_cm": match.group(1),
            "width_cm": match.group(2),
            "height_cm": match.group(3),
        }
    return {"length_cm": "", "width_cm": "", "height_cm": ""}


def text_or_empty(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "none" else text


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
    source_material = str(source.get("material") or "").strip()
    source_package = normalize_list(source.get("package_contents"))

    def result(value: str, confident: bool = True) -> tuple[str, bool]:
        return (str(value).strip(), confident)

    if "brand" in attr_id.lower() or "brand" in attr_name:
        return result(str(draft.get("brand") or product.get("brand") or source.get("brand") or "Generic").strip() or "Generic")
    if "model" in attr_id.lower() or "model" in attr_name:
        return result(str(draft.get("model") or product.get("model") or source.get("model") or "General").strip() or "General")
    if any(token in attr_id.lower() or token in attr_name for token in ["package_length", "length", "longitud", "largo"]):
        value = str(source_dims.get("length_cm") or "").strip()
        return result(value, bool(value))
    if any(token in attr_id.lower() or token in attr_name for token in ["package_width", "width", "ancho"]):
        value = str(source_dims.get("width_cm") or "").strip()
        return result(value, bool(value))
    if any(token in attr_id.lower() or token in attr_name for token in ["package_height", "height", "alto"]):
        value = str(source_dims.get("height_cm") or "").strip()
        return result(value, bool(value))
    if any(token in attr_id.lower() or token in attr_name for token in ["package_weight", "weight", "peso"]):
        value = str(source_dims.get("weight_kg") or source.get("weight_kg") or "").strip()
        return result(value, bool(value))
    if "material" in attr_id.lower() or "material" in attr_name or "材质" in attr_name:
        return result(source_material, bool(source_material))
    if "package" in attr_id.lower() or "package" in attr_name or "包装" in attr_name:
        value = " / ".join(source_package)
        return result(value, bool(value))
    if "title" in attr_id.lower() or "name" in attr_name:
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
            if option_text and option_text.lower() in source_text:
                return result(option_text, True)
        return result("", False)
    return result("", False)


def build_ai_attribute_fill(product: dict[str, Any], platform: str, category_record: dict[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_product_model(product or {})
    draft = deepcopy(normalized.get("drafts", {}).get(platform) if isinstance(normalized.get("drafts"), dict) else default_draft(platform))
    schema = _category_attribute_schema(category_record)
    attributes = deepcopy(draft.get("attributes") or {})
    need_review: list[str] = []
    for attr in schema:
        attr_id = str(attr.get("id") or "").strip()
        if not attr_id:
            continue
        value, confident = _attribute_value_from_source(normalized, platform, attr)
        if value:
            attributes[attr_id] = value
        elif attr.get("required"):
            need_review.append(attr_id)
        if not confident:
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
    for attr in _category_attribute_schema(category_record):
        if not attr.get("required"):
            continue
        attr_id = str(attr.get("id") or "").strip()
        if not attr_id:
            continue
        if not str(values.get(attr_id) or "").strip():
            errors.append(f"attributes.{attr_id}")
    return list(dict.fromkeys(errors))


def default_image_pool_item() -> dict[str, Any]:
    return {
        "id": "",
        "url": "",
        "path": "",
        "origin": "source",
        "usage": "detail",
        "platforms": list(PLATFORMS),
        "is_main": False,
        "selected": False,
        "order": 0,
        "status": "ready",
        "preview_url": "",
        "note": "",
    }


def normalize_platforms(value: Any) -> list[str]:
    items = normalize_list(value)
    if not items and isinstance(value, str):
        items = [value.strip()]
    return [item for item in items if item in PLATFORMS]


def normalize_image_pool_item(item: Any, order: int = 0, origin_hint: str = "source") -> dict[str, Any]:
    normalized = default_image_pool_item()
    if isinstance(item, str):
        text = text_or_empty(item)
        if text.startswith("http://") or text.startswith("https://") or text.startswith("ml-id:"):
            normalized["url"] = text
        else:
            normalized["path"] = text
        normalized["preview_url"] = normalized["url"] or normalized["path"]
        normalized["id"] = f"img_{order + 1}"
        normalized["origin"] = origin_hint if origin_hint in IMAGE_ORIGINS else "source"
        normalized["usage"] = "main" if order == 0 else "detail"
        normalized["platforms"] = list(PLATFORMS)
        normalized["is_main"] = order == 0
        normalized["order"] = order
        normalized["status"] = "ready" if normalized["preview_url"] else "empty"
        return normalized

    item = item if isinstance(item, dict) else {}
    normalized["id"] = text_or_empty(item.get("id")) or f"img_{order + 1}"
    normalized["url"] = text_or_empty(item.get("url"))
    normalized["path"] = text_or_empty(item.get("path"))
    normalized["origin"] = text_or_empty(item.get("origin")) or (origin_hint if origin_hint in IMAGE_ORIGINS else "source")
    usage = text_or_empty(item.get("usage")) or ("main" if order == 0 else "detail")
    normalized["usage"] = usage if usage in IMAGE_USAGES else "other"
    platforms = normalize_platforms(item.get("platforms"))
    normalized["platforms"] = platforms or list(PLATFORMS)
    normalized["is_main"] = bool(item.get("is_main", order == 0 and normalized["usage"] == "main"))
    normalized["selected"] = bool(item.get("selected", False))
    try:
        normalized["order"] = int(item.get("order", order))
    except Exception:
        normalized["order"] = order
    normalized["status"] = text_or_empty(item.get("status")) or ("ready" if (normalized["url"] or normalized["path"]) else "empty")
    normalized["preview_url"] = text_or_empty(item.get("preview_url")) or normalized["url"] or normalized["path"]
    normalized["note"] = text_or_empty(item.get("note"))
    for key, value in item.items():
        if key not in normalized:
            normalized[key] = deepcopy(value)
    return normalized


def normalize_image_pool(items: Any, legacy_images: list[Any] | None = None, origin_hint: str = "source") -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append_item(raw_item: Any, order: int, source_origin: str) -> None:
        item = normalize_image_pool_item(raw_item, order=order, origin_hint=source_origin)
        key = item.get("path") or item.get("url") or item.get("preview_url") or item.get("id")
        if key and key in seen:
            return
        if key:
            seen.add(str(key))
        normalized.append(item)

    if isinstance(items, list) and items:
        for index, raw_item in enumerate(items):
            append_item(raw_item, index, origin_hint)
    else:
        fallback = legacy_images if isinstance(legacy_images, list) else []
        for index, raw_item in enumerate(fallback):
            append_item(raw_item, index, origin_hint)

    if normalized and not any(item.get("is_main") for item in normalized):
        normalized[0]["is_main"] = True
        normalized[0]["usage"] = "main"

    for index, item in enumerate(normalized):
        item["order"] = index
    return normalized


def image_pool_legacy_views(image_pool: list[dict[str, Any]], allowed_origins: set[str] | None = None) -> dict[str, list[str]]:
    ordered = sorted(
        [item for item in image_pool if isinstance(item, dict)],
        key=lambda item: int(item.get("order") or 0),
    )
    if allowed_origins:
        ordered = [item for item in ordered if text_or_empty(item.get("origin")) in allowed_origins]
    source_items = ordered[:7]
    detail_items = ordered[7:]
    def as_ref(item: dict[str, Any]) -> str:
        return text_or_empty(item.get("path") or item.get("url") or item.get("preview_url"))
    return {
        "images": [as_ref(item) for item in ordered if as_ref(item)],
        "source_images": [as_ref(item) for item in source_items if as_ref(item)],
        "source_image_urls": [text_or_empty(item.get("url") or item.get("path") or item.get("preview_url")) for item in source_items if as_ref(item)],
        "detail_images": [as_ref(item) for item in detail_items if as_ref(item)],
        "detail_image_urls": [text_or_empty(item.get("url") or item.get("path") or item.get("preview_url")) for item in detail_items if as_ref(item)],
    }


def default_source() -> dict[str, Any]:
    return {
        "source_url": "",
        "source_platform": "",
        "title": "",
        "price": "",
        "currency": "",
        "bullets": [],
        "description": "",
        "images": [],
        "image_pool": [],
        "dimensions": {
            "length_cm": "",
            "width_cm": "",
            "height_cm": "",
        },
        "weight_kg": "",
        "material": "",
        "package_contents": [],
        "variants": [],
        "skus": [],
        "collect_status": "",
        "collect_logs": [],
        "collect_diagnostics": default_collect_diagnostics(),
    }


def default_collect_diagnostics() -> dict[str, Any]:
    return {
        "collect_mode": "",
        "source_url": "",
        "normalized_url": "",
        "platform_detected": "",
        "started_at": "",
        "finished_at": "",
        "success": False,
        "partial_success": False,
        "error_code": "",
        "error_message": "",
        "page_title": "",
        "final_url": "",
        "http_status": "",
        "is_login_page": False,
        "is_captcha_page": False,
        "is_security_check_page": False,
        "images_found_count": 0,
        "title_found": False,
        "price_found": False,
        "bullets_found_count": 0,
        "sku_found_count": 0,
        "dimensions_found": False,
        "weight_found": False,
        "html_snapshot_path": "",
        "screenshot_path": "",
        "collected_fields": [],
        "missing_fields": [],
        "next_action": "",
        "checked_at": "",
        "parser_version": "collect-v2",
    }


def default_pricing(platform: str = "") -> dict[str, Any]:
    platform_key = str(platform or "").strip().lower()
    return {
        "platform": platform_key,
        "purchase_cost": "",
        "domestic_freight": "",
        "packaging_cost": "",
        "international_shipping": "",
        "other_cost": "",
        "currency_rate": "",
        "weight_kg": "",
        "length_cm": "",
        "width_cm": "",
        "height_cm": "",
        "commission_rate": "",
        "payment_fee_rate": "",
        "shipping_cost": "",
        "warehousing_cost": "",
        "other_platform_fee": "",
        "advertising_cost": "",
        "target_margin": "",
        "cost_breakdown": {},
        "platform_fee_total": "",
        "suggested_price": "",
        "reverse_price": "",
        "expected_profit": "",
        "profit_margin": "",
        "converted_price": "",
        "gross_profit": "",
        "net_profit": "",
        "is_loss": False,
        "pricing_errors": [],
        "checked_at": "",
    }


def default_draft(platform: str) -> dict[str, Any]:
    return {
        "enabled": True,
        "title": "",
        "description": "",
        "bullets": [],
        "search_terms": [],
        "language": "",
        "country": "",
        "site": "",
        "category_id": "",
        "category_path": "",
        "attributes": {},
        "brand": "",
        "model": "",
        "sku": "",
        "upc": "",
        "gtin": "",
        "barcode": "",
        "price": "",
        "stock": "",
        "images": [],
        "package_dimensions": {
            "length_cm": "",
            "width_cm": "",
            "height_cm": "",
            "weight_kg": "",
        },
        "pricing": default_pricing(platform),
        "validation_errors": [],
        "status": "collected",
        "publish_status": "",
        "publish_logs": [],
    }


def default_product_model() -> dict[str, Any]:
    return {
        "name": "",
        "brand": "",
        "category": "",
        "target_customer": "",
        "source_images": [],
        "source_image_urls": [],
        "source_url": "",
        "source_platform": "",
        "materials": [],
        "dimensions": "",
        "colors": [],
        "selling_points": [],
        "package_includes": [],
        "avoid_claims": [],
        "marketplace_terms": {
            "mercadolibre": {
                "language": "es-MX",
                "product_type": "",
                "primary_keywords": [],
                "attribute_keywords": [],
            },
            "wildberries": {
                "language": "ru-RU",
                "product_type": "",
                "primary_keywords": [],
                "attribute_keywords": [],
            },
        },
        "attributes": {},
        "detail_images": [],
        "detail_image_urls": [],
        "listing_overrides": {},
        "copy_results": {},
        "sku_items": [],
        "selected_sku_indices": [],
        "pricing_defaults": {},
        "publish_preview": {},
        "detected_price_display": "",
        "detected_price": "",
        "detected_currency": "",
        "sku": "",
        "model": "",
        "weight_kg": "",
        "stock": "",
        "upc": "",
        "collect_status": "",
        "collect_logs": [],
        "category_id": "",
        "wb_subject_id": "",
        "ozon_category_id": "",
        "local_platform_categories": {},
        "description": "",
        "source": default_source(),
        "drafts": {platform: default_draft(platform) for platform in PLATFORMS},
    }


def _merge_source(product: dict[str, Any]) -> dict[str, Any]:
    source = default_source()
    incoming = product.get("source") if isinstance(product.get("source"), dict) else {}
    legacy_fallback = not isinstance(product.get("source"), dict)
    source["source_url"] = str(incoming.get("source_url") or (product.get("source_url") if legacy_fallback else "") or "").strip()
    source["source_platform"] = str(incoming.get("source_platform") or (product.get("source_platform") if legacy_fallback else "") or "").strip()
    source["title"] = str(incoming.get("title") or (product.get("name") if legacy_fallback else "") or "").strip()
    source["price"] = str(incoming.get("price") or ((product.get("detected_price") or product.get("cost")) if legacy_fallback else "") or "").strip()
    source["currency"] = str(incoming.get("currency") or ((product.get("detected_currency") or product.get("currency_id")) if legacy_fallback else "") or "").strip()
    source["bullets"] = normalize_list(incoming.get("bullets") or (product.get("selling_points") if legacy_fallback else []))
    source["description"] = str(incoming.get("description") or (product.get("description") if legacy_fallback else "") or "").strip()
    image_pool = incoming.get("image_pool") if isinstance(incoming.get("image_pool"), list) else []
    legacy_images: list[Any] = []
    if isinstance(incoming.get("images"), list) and incoming.get("images"):
        legacy_images.extend(incoming.get("images"))
    elif legacy_fallback:
        legacy_images.extend(list(product.get("source_images") or []))
        legacy_images.extend(list(product.get("detail_images") or []))
        legacy_images.extend(list(product.get("source_image_urls") or []))
        legacy_images.extend(list(product.get("detail_image_urls") or []))
    source["image_pool"] = normalize_image_pool(image_pool, legacy_images, "source")
    pool_views = image_pool_legacy_views(source["image_pool"], SOURCE_COMPAT_IMAGE_ORIGINS)
    source["images"] = normalize_list(pool_views["images"] or legacy_images)
    raw_dimensions = incoming.get("dimensions") if isinstance(incoming.get("dimensions"), dict) else {}
    fallback_dimensions = parse_dimensions_text(product.get("dimensions") if legacy_fallback else "")
    fallback_package_dimensions = {
        "length_cm": text_or_empty(product.get("package_length_cm") if legacy_fallback else ""),
        "width_cm": text_or_empty(product.get("package_width_cm") if legacy_fallback else ""),
        "height_cm": text_or_empty(product.get("package_height_cm") if legacy_fallback else ""),
    }
    source["dimensions"] = {
        "length_cm": str(raw_dimensions.get("length_cm") or fallback_package_dimensions["length_cm"] or fallback_dimensions["length_cm"] or parse_dimensions_text(product.get("dimensions")).get("length_cm") or "").strip(),
        "width_cm": str(raw_dimensions.get("width_cm") or fallback_package_dimensions["width_cm"] or fallback_dimensions["width_cm"] or parse_dimensions_text(product.get("dimensions")).get("width_cm") or "").strip(),
        "height_cm": str(raw_dimensions.get("height_cm") or fallback_package_dimensions["height_cm"] or fallback_dimensions["height_cm"] or parse_dimensions_text(product.get("dimensions")).get("height_cm") or "").strip(),
    }
    source["weight_kg"] = str(incoming.get("weight_kg") or (product.get("weight_kg") if legacy_fallback else "") or "").strip()
    source["material"] = str(incoming.get("material") or ((product.get("materials") or [""])[0] if legacy_fallback else "") or "").strip()
    source["package_contents"] = normalize_list(incoming.get("package_contents") or (product.get("package_includes") if legacy_fallback else []))
    source["variants"] = deepcopy(incoming.get("variants") or (product.get("variations") if legacy_fallback else []) or [])
    source["skus"] = deepcopy(incoming.get("skus") or (product.get("sku_items") if legacy_fallback else []) or [])
    source["collect_status"] = str(incoming.get("collect_status") or (product.get("collect_status") if legacy_fallback else "") or "").strip()
    source["collect_logs"] = deepcopy(incoming.get("collect_logs") or (product.get("collect_logs") if legacy_fallback else []) or [])
    diagnostics = incoming.get("collect_diagnostics") if isinstance(incoming.get("collect_diagnostics"), dict) else {}
    source["collect_diagnostics"] = _merge_collect_diagnostics({}, diagnostics)
    return source


def _draft_sources(product: dict[str, Any], platform: str) -> dict[str, Any]:
    drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
    current = deepcopy(drafts.get(platform)) if isinstance(drafts.get(platform), dict) else default_draft(platform)
    overrides = product.get("listing_overrides") if isinstance(product.get("listing_overrides"), dict) else {}
    copy_results = product.get("copy_results") if isinstance(product.get("copy_results"), dict) else {}
    copy = copy_results.get(platform) if isinstance(copy_results.get(platform), dict) else {}
    override = overrides.get(platform) if isinstance(overrides.get(platform), dict) else {}
    for field in ["title", "description", "bullets", "search_terms", "language"]:
        value = copy.get(field) if copy.get(field) not in (None, "") else override.get(field)
        if value not in (None, ""):
            current[field] = deepcopy(value)
    return current


def _apply_source_mappings_to_draft(product: dict[str, Any], platform: str, current: dict[str, Any]) -> dict[str, Any]:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    current = deepcopy(current if isinstance(current, dict) else default_draft(platform))

    if platform == "mercadolibre":
        local_categories = product.get("local_platform_categories") if isinstance(product.get("local_platform_categories"), dict) else {}
        selected = local_categories.get(platform) if isinstance(local_categories.get(platform), dict) else {}
        current["category_id"] = str(current.get("category_id") or selected.get("category_id") or product.get("category_id") or "").strip()
        current["category_path"] = str(current.get("category_path") or selected.get("category_path") or product.get("category_path") or "").strip()
        current["language"] = str(current.get("language") or product.get("marketplace_terms", {}).get("mercadolibre", {}).get("language") or "es-MX").strip()
        current["site"] = str(current.get("site") or selected.get("site") or "MLM").strip()
        current["country"] = str(current.get("country") or selected.get("country") or "MX").strip()
    elif platform == "wildberries":
        local_categories = product.get("local_platform_categories") if isinstance(product.get("local_platform_categories"), dict) else {}
        selected = local_categories.get(platform) if isinstance(local_categories.get(platform), dict) else {}
        current["category_id"] = str(current.get("category_id") or selected.get("category_id") or product.get("wb_subject_id") or "").strip()
        current["category_path"] = str(current.get("category_path") or selected.get("category_path") or product.get("category_path") or "").strip()
        current["language"] = str(current.get("language") or product.get("marketplace_terms", {}).get("wildberries", {}).get("language") or "ru-RU").strip()
        current["site"] = str(current.get("site") or selected.get("site") or "WB").strip()
        current["country"] = str(current.get("country") or selected.get("country") or "RU").strip()
    elif platform == "ozon":
        local_categories = product.get("local_platform_categories") if isinstance(product.get("local_platform_categories"), dict) else {}
        selected = local_categories.get(platform) if isinstance(local_categories.get(platform), dict) else {}
        current["category_id"] = str(current.get("category_id") or selected.get("category_id") or product.get("ozon_category_id") or "").strip()
        current["category_path"] = str(current.get("category_path") or selected.get("category_path") or product.get("category_path") or "").strip()
        current["language"] = str(current.get("language") or "ru-RU").strip()
        current["site"] = str(current.get("site") or selected.get("site") or "OZON").strip()
        current["country"] = str(current.get("country") or selected.get("country") or "RU").strip()

    current["brand"] = str(current.get("brand") or product.get("brand") or source.get("brand") or "Generic").strip() or "Generic"
    current["model"] = str(current.get("model") or product.get("model") or source.get("model") or "General").strip() or "General"
    current["sku"] = str(current.get("sku") or product.get("sku") or "").strip()
    current["upc"] = str(current.get("upc") or product.get("upc") or "").strip()
    current["gtin"] = str(current.get("gtin") or current["upc"] or "").strip()
    current["barcode"] = str(current.get("barcode") or current["upc"] or "").strip()
    current["price"] = str(current.get("price") or source.get("price") or product.get("detected_price") or "").strip()
    current["stock"] = str(current.get("stock") or product.get("stock") or "").strip()
    current_pkg = current.get("package_dimensions") if isinstance(current.get("package_dimensions"), dict) else {}
    source_dims = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    current["package_dimensions"] = {
        "length_cm": str(current_pkg.get("length_cm") or source_dims.get("length_cm") or product.get("package_length_cm") or "").strip(),
        "width_cm": str(current_pkg.get("width_cm") or source_dims.get("width_cm") or product.get("package_width_cm") or "").strip(),
        "height_cm": str(current_pkg.get("height_cm") or source_dims.get("height_cm") or product.get("package_height_cm") or "").strip(),
        "weight_kg": str(current_pkg.get("weight_kg") or source.get("weight_kg") or product.get("weight_kg") or "").strip(),
    }
    current["attributes"] = deepcopy(current.get("attributes") or product.get("attributes") or {})
    return current


def _merge_platform_draft(product: dict[str, Any], platform: str) -> dict[str, Any]:
    current = _apply_source_mappings_to_draft(product, platform, _draft_sources(product, platform))
    current["enabled"] = bool(current.get("enabled", True))
    current["images"] = normalize_list(current.get("images") or product.get("source_image_urls") or product.get("detail_image_urls"))
    current["bullets"] = normalize_list(current.get("bullets"))
    current["search_terms"] = normalize_list(current.get("search_terms"))
    current["publish_logs"] = deepcopy(current.get("publish_logs") or [])
    current["validation_errors"] = deepcopy(current.get("validation_errors") or [])
    current["attributes"] = deepcopy(current.get("attributes") or {})
    pricing = current.get("pricing") if isinstance(current.get("pricing"), dict) else {}
    merged_pricing = default_pricing(platform)
    merged_pricing.update({key: deepcopy(value) for key, value in pricing.items() if key in merged_pricing and value not in (None, "")})
    merged_pricing["platform"] = platform
    merged_pricing["weight_kg"] = str(merged_pricing.get("weight_kg") or current["package_dimensions"].get("weight_kg") or "").strip()
    merged_pricing["length_cm"] = str(merged_pricing.get("length_cm") or current["package_dimensions"].get("length_cm") or "").strip()
    merged_pricing["width_cm"] = str(merged_pricing.get("width_cm") or current["package_dimensions"].get("width_cm") or "").strip()
    merged_pricing["height_cm"] = str(merged_pricing.get("height_cm") or current["package_dimensions"].get("height_cm") or "").strip()
    current["pricing"] = merged_pricing
    return current


def _merge_collect_diagnostics(existing: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any]:
    merged = default_collect_diagnostics()
    existing = existing if isinstance(existing, dict) else {}
    incoming = incoming if isinstance(incoming, dict) else {}
    merged.update({key: value for key, value in existing.items() if key in merged and value not in (None, "")})
    for key, value in incoming.items():
        if key not in merged:
            merged[key] = deepcopy(value)
            continue
        if isinstance(merged[key], bool):
            merged[key] = bool(value)
        elif isinstance(merged[key], int):
            try:
                merged[key] = int(value)
            except Exception:
                pass
        elif value not in (None, ""):
            merged[key] = deepcopy(value)
    return merged


def merge_source_partial_result(
    product: dict[str, Any] | None,
    source_updates: dict[str, Any] | None,
    diagnostics_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_product_model(product or {})
    source = deepcopy(normalized.get("source") or default_source())
    updates = source_updates if isinstance(source_updates, dict) else {}
    diagnostics = diagnostics_updates if isinstance(diagnostics_updates, dict) else updates.get("collect_diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    try:
        diagnostics_image_count = int(diagnostics.get("images_found_count") or 0)
    except (TypeError, ValueError):
        diagnostics_image_count = 0
    has_incoming_images = bool(updates.get("images")) or bool(updates.get("image_pool"))
    should_clear_collect_images = (
        not has_incoming_images
        and bool(diagnostics)
        and not bool(diagnostics.get("success"))
        and diagnostics_image_count <= 0
        and (bool(diagnostics.get("error_code")) or "images_found_count" in diagnostics)
    )

    def apply_if_present(target: dict[str, Any], key: str, value: Any) -> None:
        if value in (None, "", [], {}):
            return
        if isinstance(value, dict):
            if not isinstance(target.get(key), dict):
                target[key] = {}
            for nested_key, nested_value in value.items():
                if nested_value in (None, "", [], {}):
                    continue
                target[key][nested_key] = deepcopy(nested_value)
            return
        if isinstance(value, list):
            if value:
                target[key] = deepcopy(value)
            return
        target[key] = deepcopy(value)

    for field in ["source_url", "source_platform", "title", "price", "currency", "description", "weight_kg", "material", "collect_status"]:
        apply_if_present(source, field, updates.get(field))
    for field in ["bullets", "images", "image_pool", "package_contents", "variants", "skus", "collect_logs"]:
        apply_if_present(source, field, updates.get(field))
    apply_if_present(source, "dimensions", updates.get("dimensions"))
    if should_clear_collect_images:
        kept_pool: list[dict[str, Any]] = []
        for item in source.get("image_pool") if isinstance(source.get("image_pool"), list) else []:
            if not isinstance(item, dict):
                continue
            origin = text_or_empty(item.get("origin")) or "source"
            if origin not in SOURCE_COMPAT_IMAGE_ORIGINS:
                kept_pool.append(deepcopy(item))
        source["image_pool"] = kept_pool
        source["images"] = []
        kept_refs = {
            ref
            for item in kept_pool
            for ref in [text_or_empty(item.get("url") or item.get("path") or item.get("preview_url"))]
            if ref
        }
        drafts = normalized.get("drafts") if isinstance(normalized.get("drafts"), dict) else {}
        for draft in drafts.values():
            if not isinstance(draft, dict):
                continue
            draft["images"] = [ref for ref in normalize_list(draft.get("images")) if ref in kept_refs] if kept_refs else []
        for sku_item in normalized.get("sku_items") if isinstance(normalized.get("sku_items"), list) else []:
            if isinstance(sku_item, dict) and text_or_empty(sku_item.get("image")) not in kept_refs:
                sku_item["image"] = ""
        for field in ["source_images", "source_image_urls", "detail_images", "detail_image_urls"]:
            normalized[field] = []
    if isinstance(source.get("image_pool"), list):
        pool_views = image_pool_legacy_views(normalize_image_pool(source["image_pool"], [], "source"), SOURCE_COMPAT_IMAGE_ORIGINS)
        source["images"] = pool_views["images"] or source.get("images", [])

    current_diag = source.get("collect_diagnostics") if isinstance(source.get("collect_diagnostics"), dict) else default_collect_diagnostics()
    source["collect_diagnostics"] = _merge_collect_diagnostics(current_diag, diagnostics_updates or updates.get("collect_diagnostics"))

    normalized["source"] = source
    normalized["name"] = str(source.get("title") or normalized.get("name") or "").strip()
    normalized["source_url"] = str(source.get("source_url") or normalized.get("source_url") or "").strip()
    normalized["source_platform"] = str(source.get("source_platform") or normalized.get("source_platform") or "").strip()
    normalized["materials"] = normalize_list(normalized.get("materials") or [source.get("material")])
    normalized["selling_points"] = normalize_list(normalized.get("selling_points") or source.get("bullets"))
    normalized["package_includes"] = normalize_list(normalized.get("package_includes") or source.get("package_contents"))
    normalized["source_images"] = normalize_list(normalized.get("source_images") or source.get("images"))
    normalized["source_image_urls"] = normalize_list(normalized.get("source_image_urls") or source.get("images"))
    normalized["description"] = str(normalized.get("description") or source.get("description") or "").strip()
    normalized["weight_kg"] = str(normalized.get("weight_kg") or source.get("weight_kg") or "").strip()
    normalized["collect_status"] = str(normalized.get("collect_status") or source.get("collect_status") or "").strip()
    normalized["collect_logs"] = deepcopy(normalized.get("collect_logs") or source.get("collect_logs") or [])
    normalized["detected_price"] = str(normalized.get("detected_price") or source.get("price") or "").strip()
    normalized["detected_currency"] = str(normalized.get("detected_currency") or source.get("currency") or "").strip()
    if normalized.get("detected_price") and normalized.get("detected_currency"):
        normalized["detected_price_display"] = f"{normalized['detected_price']} {normalized['detected_currency']}"
    dimensions = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    if any(str(dimensions.get(part) or "").strip() for part in ["length_cm", "width_cm", "height_cm"]):
        normalized["dimensions"] = " x ".join(
            str(dimensions.get(part) or "").strip() for part in ["length_cm", "width_cm", "height_cm"] if str(dimensions.get(part) or "").strip()
        ) + (" cm" if all(str(dimensions.get(part) or "").strip() for part in ["length_cm", "width_cm", "height_cm"]) else "")
    return normalized


def normalize_product_model(product: dict[str, Any] | None) -> dict[str, Any]:
    incoming = deepcopy(product or {})
    normalized = default_product_model()
    normalized.update({key: value for key, value in incoming.items() if key not in {"source", "drafts"}})
    normalized["source"] = _merge_source(incoming)
    normalized["drafts"] = {platform: _merge_platform_draft(incoming, platform) for platform in PLATFORMS}

    normalized["name"] = str(normalized["source"].get("title") or normalized.get("name") or "").strip()
    normalized["source_url"] = str(normalized["source"].get("source_url") or normalized.get("source_url") or "").strip()
    normalized["source_platform"] = str(normalized["source"].get("source_platform") or normalized.get("source_platform") or "").strip()
    normalized["materials"] = normalize_list(normalized.get("materials") or [normalized["source"].get("material")])
    normalized["selling_points"] = normalize_list(normalized.get("selling_points") or normalized["source"].get("bullets"))
    normalized["package_includes"] = normalize_list(normalized.get("package_includes") or normalized["source"].get("package_contents"))
    pool_views = image_pool_legacy_views(
        normalized["source"].get("image_pool") if isinstance(normalized["source"].get("image_pool"), list) else [],
        SOURCE_COMPAT_IMAGE_ORIGINS,
    )
    source_images = normalize_list(normalized.get("source_images"))
    source_image_urls = normalize_list(normalized.get("source_image_urls"))
    detail_images = normalize_list(normalized.get("detail_images"))
    detail_image_urls = normalize_list(normalized.get("detail_image_urls"))
    normalized["source_images"] = source_images or pool_views["source_images"] or normalize_list(normalized["source"].get("images"))
    normalized["source_image_urls"] = source_image_urls or normalized["source_images"] or pool_views["source_image_urls"] or normalize_list(normalized["source"].get("images"))
    normalized["detail_images"] = detail_images or pool_views["detail_images"]
    normalized["detail_image_urls"] = detail_image_urls or normalized["detail_images"] or pool_views["detail_image_urls"]
    normalized["description"] = str(normalized.get("description") or normalized["drafts"]["mercadolibre"].get("description") or normalized["source"].get("description") or "").strip()

    if not normalized.get("detected_price") and normalized["source"].get("price"):
        normalized["detected_price"] = str(normalized["source"].get("price"))
    if not normalized.get("detected_currency") and normalized["source"].get("currency"):
        normalized["detected_currency"] = str(normalized["source"].get("currency"))
    if normalized.get("detected_price") and normalized.get("detected_currency"):
        normalized["detected_price_display"] = f"{normalized['detected_price']} {normalized['detected_currency']}"

    normalized["collect_status"] = str(normalized.get("collect_status") or normalized["source"].get("collect_status") or "").strip()
    normalized["collect_logs"] = deepcopy(normalized.get("collect_logs") or normalized["source"].get("collect_logs") or [])
    if not isinstance(normalized["source"].get("collect_diagnostics"), dict):
        normalized["source"]["collect_diagnostics"] = default_collect_diagnostics()
    else:
        normalized["source"]["collect_diagnostics"] = _merge_collect_diagnostics(default_collect_diagnostics(), normalized["source"].get("collect_diagnostics"))

    normalized["category_id"] = str(normalized.get("category_id") or normalized["drafts"]["mercadolibre"].get("category_id") or "").strip()
    normalized["wb_subject_id"] = str(normalized.get("wb_subject_id") or normalized["drafts"]["wildberries"].get("category_id") or "").strip()
    normalized["ozon_category_id"] = str(normalized.get("ozon_category_id") or normalized["drafts"]["ozon"].get("category_id") or "").strip()

    return normalized
