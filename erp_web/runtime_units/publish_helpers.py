# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from typing import Any

from erp_web import marketplaces as publisher
from erp_web.context import get_context
from erp_web.product_model import (
    default_draft,
    normalize_draft_image_refs,
    validate_category_precheck,
)
from erp_web.stores.config_store import summarize_store_auth_states
from erp_web.stores.product_store import normalize_product_fields

from .copy_generation import apply_product_drafts_to_plan, build_plan_for_platform
from .image_pool_core import (
    _source_pool_items,
    current_image_pool,
    image_pool_refs_for_platform,
)

def assign_upc() -> dict[str, Any]:
    """在同一事务内为当前商品占用 UPC 并保存商品/草稿。"""
    product = normalize_product_fields(get_context().products.load_product())
    value, saved = get_context().products.assign_upc_to_product(product)
    if not value:
        return {"ok": False, "error": "UPC 池为空，请先在设置中导入 UPC"}
    return {
        "ok": True,
        "upc": value,
        "product": saved,
        "productsIndex": get_context().products.load_products_index(),
        "imagePool": current_image_pool(saved),
        "upcPool": get_context().db.upc_pool_stats(),
        "message": f"UPC 已分配：{value}",
    }


def build_mercadolibre_publish_payload(
    product: dict[str, Any],
    config: dict[str, Any],
    picture_refs: list[str] | None = None,
) -> dict[str, Any]:
    plan = apply_product_drafts_to_plan(product, build_plan_for_platform(product, "mercadolibre"))
    draft = _draft_for_platform(product, "mercadolibre")
    payload_config = deepcopy(config)
    store = payload_config.setdefault("mercadolibre", {})
    store["category_id"] = str(draft.get("category_id") or "").strip()
    site_id = str(draft.get("site") or draft.get("site_id") or "").strip().upper()
    if site_id:
        store["site_id"] = site_id
    listing = payload_config.setdefault("listing", {})
    selected_price, listing_currency = _selected_price_and_currency(
        draft, "mercadolibre", site_id
    )
    package_dimensions = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    shipping = draft.get("shipping") if isinstance(draft.get("shipping"), dict) else {}
    for key, value in {
        "mercadolibre_price": selected_price,
        "price": selected_price,
        "currency_id": listing_currency,
        "stock": draft.get("stock"),
        "sku": draft.get("sku"),
        "upc": draft.get("upc"),
        "model": draft.get("model"),
        "mercadolibre_title": draft.get("title"),
        "package_length_cm": package_dimensions.get("length_cm"),
        "package_width_cm": package_dimensions.get("width_cm"),
        "package_height_cm": package_dimensions.get("height_cm"),
        "package_weight_kg": package_dimensions.get("weight_kg"),
        "mercadolibre_logistic_type": shipping.get("logistic_type") or shipping.get("mode"),
        "mercadolibre_attributes": draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {},
    }.items():
        if value not in (None, "", {}):
            listing[key] = value
    if isinstance(draft.get("sale_terms"), list) and draft.get("sale_terms"):
        listing["mercadolibre_sale_terms"] = draft.get("sale_terms")
    refs = (
        image_pool_refs_for_platform(product, "mercadolibre")
        if picture_refs is None
        else list(picture_refs)
    )
    payload = publisher.build_mercadolibre_payload(
        product,
        plan,
        payload_config,
        refs,
    )
    last_publish_task = (
        draft.get("last_publish_task")
        if isinstance(draft.get("last_publish_task"), dict)
        else {}
    )
    item_id = str(
        last_publish_task.get("item_id")
        or last_publish_task.get("external_id")
        or ""
    ).strip()
    if item_id:
        payload["_item_id"] = item_id
    return payload


def remote_publish_identity(result: Any) -> dict[str, Any]:
    """从各平台包装层中提取可持久化的远端刊登身份。"""

    current = result if isinstance(result, dict) else {}
    candidates: list[dict[str, Any]] = []
    for _ in range(4):
        if not isinstance(current, dict) or current in candidates:
            break
        candidates.append(current)
        nested = current.get("result")
        if not isinstance(nested, dict):
            break
        current = nested

    identity: dict[str, Any] = {}
    for candidate in candidates:
        item_id = candidate.get("item_id") or candidate.get("id")
        product_id = candidate.get("product_id")
        offer_id = candidate.get("offer_id")
        external_id = candidate.get("external_id") or item_id or product_id
        operation = candidate.get("operation")
        if item_id not in (None, "") and "item_id" not in identity:
            identity["item_id"] = str(item_id)
        if product_id not in (None, "", 0) and "product_id" not in identity:
            identity["product_id"] = product_id
        if offer_id not in (None, "") and "offer_id" not in identity:
            identity["offer_id"] = str(offer_id)
        if external_id not in (None, "", 0) and "external_id" not in identity:
            identity["external_id"] = str(external_id)
        if operation not in (None, "") and "operation" not in identity:
            identity["operation"] = str(operation)
    return identity


def build_publish_payload(product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
    from .publish_adapter import require_publishing_adapter

    return require_publishing_adapter(platform).build_payload(product, config)


def validate_mercadolibre_publish_payload(payload: Any, config: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    payload = payload if isinstance(payload, dict) else {}
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
    return missing


def validate_publish_payload(platform: str, payload: Any, config: dict[str, Any]) -> list[str]:
    from .publish_adapter import require_publishing_adapter

    return require_publishing_adapter(platform).validate_payload(payload, config)


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
        response["productsIndex"] = (
            get_context().products.load_products_index()
        )
    for key, value in extra.items():
        if value not in (None, "", [], {}):
            response[key] = value
    return response


def _draft_for_platform(product: dict[str, Any], platform: str) -> dict[str, Any]:
    drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
    draft = drafts.get(platform) if isinstance(drafts, dict) else {}
    return draft if isinstance(draft, dict) else default_draft(platform)


def _selected_price_and_currency(
    draft: dict[str, Any], platform: str, site: str
) -> tuple[str, str]:
    currency = str(draft.get("listing_currency") or "").strip().upper()
    price = str(draft.get("price") or "").strip()
    if price and currency:
        return price, currency
    target_key = f"{str(platform).strip().lower()}:{str(site).strip().lower()}"
    pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
    targets = pricing.get("targets") if isinstance(pricing.get("targets"), dict) else {}
    record = next(
        (item for key, item in targets.items() if str(key).strip().lower() == target_key and isinstance(item, dict)),
        {},
    )
    applied = record.get("applied_price") if isinstance(record.get("applied_price"), dict) else {}
    target_sites = draft.get("target_sites") if isinstance(draft.get("target_sites"), list) else []
    selected_target = next(
        (
            item for item in target_sites
            if isinstance(item, dict)
            and str(item.get("platform") or "").lower() == str(platform).lower()
            and str(item.get("site") or "").lower() == str(site).lower()
        ),
        {},
    )
    currency = str(
        selected_target.get("listing_currency")
        or record.get("listing_currency")
        or applied.get("currency")
        or ""
    ).strip().upper()
    if str(applied.get("currency") or "").strip().upper() != currency:
        return "", currency
    return str(applied.get("amount") or "").strip(), currency


def _draft_images(product: dict[str, Any], platform: str, draft: dict[str, Any]) -> list[str]:
    refs = normalize_draft_image_refs(draft.get("images"))
    if not refs:
        return image_pool_refs_for_platform(product, platform)
    # 发布必须读取 canonical 图片池。展示图片池会为了本地预览把 URL 转成
    # /file?...，不能作为平台 payload 的来源。
    pool = _source_pool_items(product)
    asset_ref_map = {
        str(item.get("id") or item.get("asset_id") or "").strip(): str(item.get("url") or item.get("path") or item.get("preview_url") or "").strip()
        for item in pool
        if isinstance(item, dict)
    }
    images = [asset_ref_map.get(str(ref.get("asset_id") or "").strip(), "") for ref in refs]
    return [image for image in images if image]


def _has_main_image(product: dict[str, Any], platform: str, draft: dict[str, Any]) -> bool:
    draft_refs = normalize_draft_image_refs(draft.get("images"))
    if draft_refs:
        return any(ref.get("role") == "main" for ref in draft_refs)
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
    if platform in {"ozon", "yandex"}:
        # 必填属性以草稿上的实时类目 schema 为唯一事实来源，
        # 与 payload builder 消费同一份定义。
        schema = (
            draft.get("category_attribute_schema")
            if isinstance(draft.get("category_attribute_schema"), dict)
            else {}
        )
        record = {
            "category_id": category_id,
            "attributes": {
                "required": list(schema.get("required") or []),
                "optional": list(schema.get("optional") or []),
            },
        }
    else:
        categories = product.get("local_platform_categories") if isinstance(product.get("local_platform_categories"), dict) else {}
        record = categories.get(platform) if isinstance(categories.get(platform), dict) else None
    record_id = str((record or {}).get("category_id") or (record or {}).get("subject_id") or (record or {}).get("type_id") or "").strip()
    if category_id and record_id and record_id != category_id:
        record = None
    if not isinstance(record, dict):
        return {"required_count": 0, "filled_count": 0, "missing": []}
    missing = validate_category_precheck(product, platform, record)
    required_fields = [item for item in missing if str(item).startswith("attributes.")]
    attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
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


__all__ = [
    "_draft_for_platform",
    "_draft_images",
    "_field_error_map",
    "_has_main_image",
    "_masked_auth_status",
    "_required_attribute_summary",
    "assign_upc",
    "build_mercadolibre_publish_payload",
    "build_publish_payload",
    "compact_precheck",
    "compact_precheck_items",
    "compact_publish_failure_response",
    "precheck_item",
    "validate_mercadolibre_publish_payload",
    "validate_publish_payload",
]
