# -*- coding: utf-8 -*-
from __future__ import annotations

from .runtime_common import *

def mock_category_attrs(platform: str, category_id: str) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    record = find_category_record(platform, category_id)
    if record:
        attrs = record.get("attributes_cache") if isinstance(record.get("attributes_cache"), dict) else {}
        required = list(attrs.get("required") or [])
        optional = list(attrs.get("optional") or [])
        return {
            "ok": True,
            "source": "cache",
            "cache_status": category_cache_status(platform),
            "category": record,
            "required": required,
            "optional": optional,
            "attributes": required + optional,
            "category_path": record.get("path_cn") or record.get("path_original") or [],
        }
    if platform == "mercadolibre":
        return {
            "ok": True,
            "source": "mock",
            "cache_status": category_cache_status(platform),
            "required": [
                {"id": "BRAND", "name": "Brand", "required": True, "value_type": "string"},
                {"id": "MODEL", "name": "Model", "required": True, "value_type": "string"},
                {"id": "GTIN", "name": "GTIN", "required": False, "value_type": "string"},
                {"id": "PACKAGE_LENGTH", "name": "Package length", "required": True, "value_type": "number", "unit": "cm"},
                {"id": "PACKAGE_WIDTH", "name": "Package width", "required": True, "value_type": "number", "unit": "cm"},
                {"id": "PACKAGE_HEIGHT", "name": "Package height", "required": True, "value_type": "number", "unit": "cm"},
                {"id": "PACKAGE_WEIGHT", "name": "Package weight", "required": True, "value_type": "number", "unit": "kg"},
            ],
            "optional": [
                {"id": "MATERIAL", "name": "Material", "required": False, "value_type": "string"},
                {"id": "UNSURE_COLOR", "name": "Color", "required": False, "value_type": "select", "options": ["Black", "White", "Blue"]},
            ],
        }
    return {
        "ok": True,
        "source": "mock",
        "cache_status": category_cache_status(platform),
        "required": [
            {"id": "brand", "name": "Brand", "required": True, "value_type": "string"},
            {"id": "subject", "name": "Subject", "required": True, "value_type": "string"},
            {"id": "price", "name": "Price", "required": True, "value_type": "number"},
        ],
        "optional": [
            {"id": "material", "name": "Material", "required": False, "value_type": "string"},
        ],
        "attributes": [
            {"id": "brand", "name": "Brand", "required": True, "value_type": "string"},
            {"id": "subject", "name": "Subject", "required": True, "value_type": "string"},
            {"id": "price", "name": "Price", "required": True, "value_type": "number"},
            {"id": "material", "name": "Material", "required": False, "value_type": "string"},
        ],
    }


def assign_upc() -> dict[str, Any]:
    pool_path = APP_DIR / "upc_pool.json"
    if not pool_path.exists():
        return {"ok": False, "error": "UPC 池为空，请先在设置中导入 UPC"}
    try:
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "error": "UPC 池读取失败"}
    values = [str(value or "").strip() for value in list(pool.get("values") or []) if str(value or "").strip()]
    used = {str(value or "").strip() for value in list(pool.get("used") or []) if str(value or "").strip()}
    for value in values:
        if value in used:
            continue
        product = normalize_product_fields(load_product())
        product["upc"] = value
        drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
        for draft in drafts.values():
            if isinstance(draft, dict):
                draft["upc"] = value
                draft["gtin"] = value
                draft["barcode"] = value
        saved = save_product(product)
        used.add(value)
        pool["used"] = sorted(used)
        write_json(pool_path, pool)
        return {
            "ok": True,
            "upc": value,
            "product": saved,
            "productsIndex": load_products_index(),
            "imagePool": current_image_pool(saved),
            "message": f"UPC 已分配：{value}",
        }
    return {"ok": False, "error": "UPC 池为空，请先在设置中导入 UPC"}


def build_publish_payload(product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
    plan = apply_product_drafts_to_plan(product, build_plan_for_platform(product, platform))
    if platform == "mercadolibre":
        return publisher.build_mercadolibre_payload(product, plan, config, normalize_list(product.get("source_image_urls")))
    if platform == "wildberries":
        return publisher.build_wildberries_payload(product, plan, config)
    if platform == "ozon":
        return publisher.build_ozon_payload(product, plan, config)
    raise RuntimeError("不支持的平台")


def validate_publish_payload(platform: str, payload: Any, config: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if platform == "mercadolibre":
        if not config.get("mercadolibre", {}).get("access_token"):
            missing.append("Mercado Libre Access Token")
        if not payload.get("title"):
            missing.append("标题")
        if not payload.get("category_id"):
            missing.append("类目 ID")
        if not payload.get("price"):
            missing.append("价格")
        if not payload.get("attributes"):
            missing.append("类目属性")
        pictures = payload.get("pictures") or payload.get("sites_to_sell", [{}])[0].get("pictures", [])
        if not pictures:
            missing.append("图片")
    elif platform == "wildberries":
        if not config.get("wildberries", {}).get("content_token"):
            missing.append("WB Token")
        if not payload:
            missing.append("发布结构")
    elif platform == "ozon":
        missing.append("该平台发布接口尚未配置，当前仅完成数据校验。")
    return missing


def precheck_item(code: str, field: str, message: str, severity: str = "error", next_action: str = "") -> dict[str, str]:
    return {
        "code": str(code or "").strip(),
        "field": str(field or "").strip(),
        "message": str(message or "").strip(),
        "severity": str(severity or "error").strip() or "error",
        "next_action": str(next_action or "").strip(),
    }


def compact_precheck_items(items: list[Any]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    index_by_key: dict[tuple[str, str, str, str, str], int] = {}
    counts: list[int] = []
    for raw in items:
        if not isinstance(raw, dict):
            raw = precheck_item("", "", str(raw or ""))
        item = precheck_item(
            str(raw.get("code") or ""),
            str(raw.get("field") or ""),
            str(raw.get("message") or ""),
            str(raw.get("severity") or "error"),
            str(raw.get("next_action") or ""),
        )
        key = (item["code"], item["field"], item["message"], item["severity"], item["next_action"])
        if key in index_by_key:
            idx = index_by_key[key]
            counts[idx] += 1
            compacted[idx]["message"] = f"{key[2]}（共 {counts[idx]} 次）"
            compacted[idx]["count"] = counts[idx]
            continue
        index_by_key[key] = len(compacted)
        counts.append(1)
        item["count"] = 1
        compacted.append(item)
    return compacted


def compact_precheck(precheck: dict[str, Any]) -> dict[str, Any]:
    errors = list(precheck.get("errors") or [])
    warnings = list(precheck.get("warnings") or [])
    compacted = dict(precheck)
    compacted["errors"] = compact_precheck_items(errors)
    compacted["warnings"] = compact_precheck_items(warnings)
    compacted["error_count"] = sum(int(item.get("count") or 1) for item in compacted["errors"])
    compacted["warning_count"] = sum(int(item.get("count") or 1) for item in compacted["warnings"])
    return compacted


def mercadolibre_picture_upload_error_message(exc: Exception) -> str:
    raw = str(exc)
    if "File not compatible with pictures engine" in raw:
        return "Mercado Libre 图片上传失败：图片文件格式或内容不兼容 Mercado Libre 图片引擎"
    if len(raw) > 240:
        raw = raw[:237].rstrip() + "..."
    return f"Mercado Libre 图片上传失败：{raw}"


def compact_publish_failure_response(status: str, error: str, saved: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    response: dict[str, Any] = {"ok": False, "status": status, "error": error}
    precheck = extra.pop("precheck", None)
    if isinstance(precheck, dict):
        response["precheck"] = compact_precheck(precheck)
    if saved:
        response["product_id"] = str(saved.get("product_id") or "")
        response["productsIndex"] = load_products_index()
    for key, value in extra.items():
        if value not in (None, "", [], {}):
            response[key] = value
    return response


def _draft_for_platform(product: dict[str, Any], platform: str) -> dict[str, Any]:
    drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
    draft = drafts.get(platform) if isinstance(drafts, dict) else {}
    return draft if isinstance(draft, dict) else default_draft(platform)


def _draft_images(product: dict[str, Any], platform: str, draft: dict[str, Any]) -> list[str]:
    images = normalize_list(draft.get("images"))
    return images or image_pool_refs_for_platform(product, platform)


def _has_main_image(product: dict[str, Any], platform: str, draft: dict[str, Any]) -> bool:
    pool = current_image_pool(product)
    platform_items = []
    for item in pool:
        platforms = [str(value).strip().lower() for value in (item.get("platforms") or [])]
        if platforms and platform not in platforms:
            continue
        if str(item.get("status") or "").strip().lower() == "empty":
            continue
        platform_items.append(item)
        if bool(item.get("is_main")):
            return True
    if platform_items:
        return False
    return bool(_draft_images(product, platform, draft))


def _field_error_map(items: list[dict[str, Any]]) -> dict[str, list[str]]:
    mapped: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        if not field:
            continue
        message = str(item.get("message") or item.get("code") or "").strip()
        mapped.setdefault(field, [])
        if message:
            mapped[field].append(message)
    return mapped


def _required_attribute_summary(product: dict[str, Any], platform: str) -> dict[str, Any]:
    draft = _draft_for_platform(product, platform)
    category_id = str(draft.get("category_id") or "").strip()
    record = find_category_record(platform, category_id) if category_id else None
    if not isinstance(record, dict):
        return {"required_count": 0, "filled_count": 0, "missing": []}
    missing = validate_category_precheck(product, platform, record)
    required_fields = [item for item in missing if str(item).startswith("attributes.")]
    attrs = record.get("attributes_cache") if isinstance(record.get("attributes_cache"), dict) else {}
    required_schema = [
        attr for attr in (attrs.get("required") or [])
        if isinstance(attr, dict) and bool(attr.get("required"))
    ]
    required_count = len(required_schema)
    return {
        "required_count": required_count,
        "filled_count": max(0, required_count - len(required_fields)),
        "missing": required_fields,
    }


def _masked_auth_status(platform: str, config: dict[str, Any]) -> tuple[str, str]:
    summary = summarize_store_auth_states(config).get(platform, {})
    return str(summary.get("status") or "未配置"), str(summary.get("next_action") or "")
