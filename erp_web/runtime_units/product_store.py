# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from erp_web import db as erp_db
from erp_web import marketplaces as publisher
from erp_web import app_config as app_config_runtime
from erp_web.marketplace_registry import marketplace_site
from erp_web.product_model import (
    PLATFORMS,
    apply_created_image_refs_to_draft,
    default_product_model,
    normalize_draft_image_refs,
    normalize_product_model,
)

from .category_store import ensure_sqlite_store, read_json, write_json
from .image_pool_core import (
    _display_image_ref,
    _source_pool_items,
    current_image_pool,
    enrich_product_image_dimensions,
)
from .runtime_common import APP_CONFIG_PATH, APP_DIR, STORE_CONFIG_PATH

def normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def mask_secret(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return f"{text[:2]}****"
    return f"{text[:4]}****{text[-4:]}"


def normalize_sku_items(product: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_items = product.get("sku_items")
    if isinstance(raw_items, list):
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "id": str(item.get("id") or index),
                    "selected": bool(item.get("selected", index == 0)),
                    "name": str(item.get("name") or item.get("sku") or item.get("spec") or f"SKU {index + 1}"),
                    "spec1": str(item.get("spec1") or item.get("variant1") or item.get("color") or ""),
                    "spec2": str(item.get("spec2") or item.get("variant2") or item.get("size") or ""),
                    "price": str(item.get("price") or ""),
                    "stock": str(item.get("stock") or ""),
                    "image": str(item.get("image") or item.get("image_url") or ""),
                    "sale_price": str(item.get("sale_price") or item.get("suggested_price") or ""),
                    "custom_stock": str(item.get("custom_stock") or item.get("publish_stock") or ""),
                }
            )
    if not rows:
        variations = product.get("variations")
        if isinstance(variations, list):
            for index, item in enumerate(variations):
                if not isinstance(item, dict):
                    continue
                attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
                rows.append(
                    {
                        "id": str(item.get("id") or index),
                        "selected": index == 0,
                        "name": str(item.get("title") or item.get("name") or attrs.get("name") or f"SKU {index + 1}"),
                        "spec1": str(item.get("spec1") or item.get("color") or attrs.get("color") or ""),
                        "spec2": str(item.get("spec2") or item.get("size") or attrs.get("size") or ""),
                        "price": str(item.get("price") or item.get("sale_price") or item.get("cost") or ""),
                        "stock": str(item.get("stock") or item.get("inventory") or ""),
                        "image": str(item.get("image") or item.get("image_url") or ""),
                        "sale_price": str(item.get("sale_price") or ""),
                        "custom_stock": str(item.get("custom_stock") or ""),
                    }
                )
    if not rows:
        rows.append(
            {
                "id": "0",
                "selected": True,
                "name": str(product.get("sku") or product.get("model") or product.get("name") or "SKU 1"),
                "spec1": "",
                "spec2": "",
                "price": str(product.get("detected_price") or product.get("cost") or ""),
                "stock": str(product.get("stock") or ""),
                "image": str((normalize_list(product.get("source_image_urls")) or [""])[0]),
                "sale_price": "",
                "custom_stock": "",
            }
        )
    return rows


def normalize_product_fields(product: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_product_model(product)
    for key in ["materials", "colors", "selling_points", "package_includes", "avoid_claims"]:
        normalized[key] = normalize_list(normalized.get(key))
    normalized.setdefault("sku", "")
    normalized.setdefault("model", "")
    normalized.setdefault("attributes", {})
    normalized.setdefault("detail_images", [])
    normalized.setdefault("detail_image_urls", [])
    normalized.setdefault("marketplace_terms", {})
    normalized.setdefault("listing_overrides", {})
    normalized.setdefault("copy_results", {})
    normalized.setdefault("sku_items", [])
    normalized.setdefault("selected_sku_indices", [])
    normalized.setdefault("pricing_defaults", {})
    normalized.setdefault("publish_preview", {})
    if normalized.get("detected_price") and normalized.get("detected_currency"):
        normalized["detected_price_display"] = f"{normalized['detected_price']} {normalized['detected_currency']}"
    else:
        normalized.setdefault("detected_price_display", "")
    if not isinstance(normalized.get("listing_overrides"), dict):
        normalized["listing_overrides"] = {}
    if not isinstance(normalized.get("copy_results"), dict):
        normalized["copy_results"] = {}
    if not isinstance(normalized.get("pricing_defaults"), dict):
        normalized["pricing_defaults"] = {}
    if not isinstance(normalized.get("publish_preview"), dict):
        normalized["publish_preview"] = {}
    normalized["sku_items"] = normalize_sku_items(normalized)
    if not normalized.get("selected_sku_indices"):
        normalized["selected_sku_indices"] = [0] if normalized["sku_items"] else []
    return normalized


def load_product() -> dict[str, Any]:
    ensure_sqlite_store()
    records = erp_db.list_product_records(APP_DIR, limit=1)
    if records:
        loaded = erp_db.load_product_model(APP_DIR, records[0]["product_id"])
        if loaded:
            return normalize_product_fields(loaded)
    return normalize_product_fields(default_product_model())


def save_product(data: dict[str, Any]) -> dict[str, Any]:
    product = sync_product_workflow_statuses(enrich_product_image_dimensions(normalize_product_fields(data)))
    product["product_id"] = product_identity(product)
    ensure_sqlite_store()
    product["product_id"] = erp_db.upsert_product_model(APP_DIR, product)
    return product


def save_product_profile(data: dict[str, Any]) -> dict[str, Any]:
    product_data = dict(data or {})
    product_data.pop("drafts", None)
    source = product_data.get("source") if isinstance(product_data.get("source"), dict) else None
    if source is not None and "name" in product_data:
        source["title"] = str(product_data.get("name") or source.get("title") or "").strip()
    return save_product(product_data)


def product_identity(product: dict[str, Any]) -> str:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    existing = str(product.get("product_id") or product.get("id") or source.get("product_id") or "").strip()
    if existing:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", existing)[:80] or "product"
    raw = "|".join(
        [
            str(source.get("source_url") or product.get("source_url") or "").strip(),
            str(source.get("title") or product.get("name") or "").strip(),
            str(source.get("created_at") or product.get("created_at") or "").strip(),
        ]
    )
    if not raw.strip("|"):
        raw = str(time.time())
    import hashlib

    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _draft_copy_ready(draft: dict[str, Any]) -> bool:
    return bool(
        draft.get("copy_generated_at")
        or draft.get("ai_copy_ready")
        or str(draft.get("copy_source") or "").lower() in {"ai", "deepseek", "openai", "fallback_ai"}
    ) and bool(str(draft.get("title") or "").strip() and str(draft.get("description") or "").strip())


def _draft_images_ready(product: dict[str, Any], platform: str, draft: dict[str, Any]) -> bool:
    images = normalize_draft_image_refs(draft.get("images"))
    return bool(images)


def _draft_publish_fields_ready(draft: dict[str, Any]) -> bool:
    attrs = draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {}
    pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
    return all(
        [
            str(draft.get("category_id") or "").strip(),
            bool(attrs),
            str(draft.get("price") or pricing.get("suggested_price") or "").strip(),
            str(draft.get("stock") or "").strip(),
        ]
    )


def _draft_precheck_ready(product: dict[str, Any], platform: str, draft: dict[str, Any]) -> bool:
    preview_map = product.get("publish_preview") if isinstance(product.get("publish_preview"), dict) else {}
    preview = preview_map.get(platform) if isinstance(preview_map.get(platform), dict) else {}
    publish_status = str(draft.get("publish_status") or "").strip().lower()
    return bool(preview.get("ok") is True or publish_status in {"ready", "published", "real_publish_success", "success"})


def draft_workflow_status(product: dict[str, Any], platform: str = "mercadolibre") -> str:
    product = normalize_product_fields(product or {})
    platform = str(platform or "mercadolibre").strip().lower() or "mercadolibre"
    draft = (product.get("drafts") or {}).get(platform) if isinstance(product.get("drafts"), dict) else {}
    draft = draft if isinstance(draft, dict) else {}
    publish_status = str(draft.get("publish_status") or "").strip().lower()
    if publish_status in {"published", "real_publish_success", "success"}:
        return "published"
    if not (draft.get("enabled") or draft.get("title") or draft.get("category_id") or draft.get("status")):
        return "collected"
    if _draft_publish_fields_ready(draft) and _draft_precheck_ready(product, platform, draft):
        return "ready_to_publish"
    if _draft_copy_ready(draft) and _draft_images_ready(product, platform, draft) and _draft_publish_fields_ready(draft) and _draft_precheck_ready(product, platform, draft):
        return "ready_to_publish"
    if _draft_copy_ready(draft) and _draft_images_ready(product, platform, draft):
        return "images_ready"
    if _draft_copy_ready(draft):
        return "copy_ready"
    return "claimed"


def publish_queue_platforms(product: dict[str, Any], requested_platforms: list[str] | None = None) -> list[str]:
    product = sync_product_workflow_statuses(product or {})
    targets = requested_platforms or list(PLATFORMS)
    normalized_targets = [str(platform or "").strip().lower() for platform in targets if str(platform or "").strip().lower() in PLATFORMS]
    eligible: list[str] = []
    for platform in normalized_targets:
        draft = (product.get("drafts") or {}).get(platform) if isinstance(product.get("drafts"), dict) else {}
        draft = draft if isinstance(draft, dict) else {}
        if draft_workflow_status(product, platform) == "ready_to_publish" or _draft_precheck_ready(product, platform, draft):
            eligible.append(platform)
    return eligible


def sync_product_workflow_statuses(product: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_product_fields(product or {})
    drafts = normalized.get("drafts") if isinstance(normalized.get("drafts"), dict) else {}
    for platform, draft in list(drafts.items()):
        if platform not in PLATFORMS or not isinstance(draft, dict):
            continue
        draft["status"] = draft_workflow_status(normalized, platform)
        drafts[platform] = draft
    normalized["workflow_statuses"] = {
        platform: drafts[platform].get("status", "collected")
        for platform in PLATFORMS
        if isinstance(drafts.get(platform), dict)
    }
    return normalized


def product_index_status(product: dict[str, Any], platform: str = "mercadolibre") -> dict[str, Any]:
    product = sync_product_workflow_statuses(product)
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    draft = (product.get("drafts") or {}).get(platform) if isinstance(product.get("drafts"), dict) else {}
    draft = draft if isinstance(draft, dict) else {}
    pool = _source_pool_items(product)
    workflow_status = draft_workflow_status(product, platform)
    has_copy = workflow_status in {"copy_ready", "images_ready", "ready_to_publish", "published"}
    has_generated_image = any(str(item.get("origin") or "") in {"ai_generated", "chatgpt_import"} for item in pool)
    queue_platforms = publish_queue_platforms(product, [platform])
    return {
        "collect_status": source.get("collect_status") or ("success" if source.get("title") else "pending"),
        "workflow_status": workflow_status,
        "draft_statuses": product.get("workflow_statuses") or {},
        "ai_copy_status": "done" if has_copy else "pending",
        "image_status": "done" if workflow_status in {"images_ready", "ready_to_publish", "published"} or pool else "pending",
        "category_status": "done" if draft.get("category_id") else "pending",
        "attributes_status": "done" if isinstance(draft.get("attributes"), dict) and draft.get("attributes") else "pending",
        "pricing_status": "done" if draft.get("price") or (isinstance(draft.get("pricing"), dict) and draft["pricing"].get("suggested_price")) else "pending",
        "precheck_status": ((product.get("publish_preview") or {}).get(platform) or {}).get("ok", "pending") if isinstance(product.get("publish_preview"), dict) else "pending",
        "publish_status": draft.get("publish_status") or "not_ready",
        "publish_queue_ready": bool(queue_platforms),
        "publish_queue_platforms": queue_platforms,
        "optimized": bool(has_copy or has_generated_image),
    }


def sanitize_products_index(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        record = dict(item)
        record["main_image"] = _display_image_ref(str(record.get("main_image") or ""))
        sanitized.append(record)
    return sanitized


def load_products_index() -> list[dict[str, Any]]:
    ensure_sqlite_store()
    return sanitize_products_index(erp_db.list_product_records(APP_DIR))


def load_drafts_index(scope: str = "active") -> list[dict[str, Any]]:
    ensure_sqlite_store()
    return sanitize_products_index(erp_db.list_draft_records(APP_DIR, scope=scope))


def delete_products_from_index(product_ids: list[Any]) -> dict[str, Any]:
    seen: set[str] = set()
    ids: list[str] = []
    for value in product_ids:
        product_id = str(value or "").strip()
        if product_id and product_id not in seen:
            ids.append(product_id)
            seen.add(product_id)
    if not ids:
        return {"ok": False, "error": "请先选择要删除的商品。", "deleted": 0, "deletedIds": [], "productsIndex": load_products_index()}

    ensure_sqlite_store()
    deleted_ids: list[str] = []
    missing_ids: list[str] = []
    for product_id in ids:
        deleted = erp_db.delete_product_model(APP_DIR, product_id)
        if deleted:
            deleted_ids.append(product_id)
        else:
            missing_ids.append(product_id)

    products_index = load_products_index()
    product = load_product()

    return {
        "ok": True,
        "deleted": len(deleted_ids),
        "deletedIds": deleted_ids,
        "missingIds": missing_ids,
        "productsIndex": products_index,
        "product": product,
        "imagePool": current_image_pool(product),
        "message": f"已删除 {len(deleted_ids)} 个商品。",
    }


def _normalize_delete_ids(value: Any) -> list[str]:
    raw_ids = value if isinstance(value, list) else [value]
    ids: list[str] = []
    seen: set[str] = set()
    for raw_id in raw_ids:
        normalized_id = str(raw_id or "").strip()
        if normalized_id and normalized_id not in seen:
            ids.append(normalized_id)
            seen.add(normalized_id)
    return ids


def delete_draft_from_index(draft_id: Any) -> dict[str, Any]:
    normalized_ids = _normalize_delete_ids(draft_id)
    if not normalized_ids:
        return {
            "ok": False,
            "error": "请先选择要删除的草稿。",
            "deleted": 0,
            "deletedDraftId": "",
            "deletedDraftIds": [],
            "deletedIds": [],
            "missingIds": [],
            "draftsIndex": load_drafts_index(),
        }

    ensure_sqlite_store()
    deleted_ids: list[str] = []
    missing_ids: list[str] = []
    affected_product_ids: list[str] = []
    for normalized_id in normalized_ids:
        draft = erp_db.load_draft_model(APP_DIR, normalized_id)
        product_id = str(draft.get("product_id") or "")
        deleted = erp_db.delete_draft_model(APP_DIR, normalized_id)
        if deleted:
            deleted_ids.append(normalized_id)
            if product_id and product_id not in affected_product_ids:
                affected_product_ids.append(product_id)
        else:
            missing_ids.append(normalized_id)

    product = load_product_from_index(affected_product_ids[0], "") if len(affected_product_ids) == 1 else load_product()
    deleted_count = len(deleted_ids)
    message = "草稿已删除。" if deleted_count == 1 else f"已删除 {deleted_count} 个草稿。"
    if not deleted_count:
        message = "草稿不存在或已被删除。"

    return {
        "ok": deleted_count > 0,
        "deleted": deleted_count,
        "deletedDraftId": deleted_ids[0] if deleted_count == 1 else "",
        "deletedDraftIds": deleted_ids,
        "deletedIds": deleted_ids,
        "missingIds": missing_ids,
        "affectedProductIds": affected_product_ids,
        "product": product,
        "productsIndex": load_products_index(),
        "draftsIndex": load_drafts_index(),
        "imagePool": current_image_pool(product),
        "message": message,
        "error": "" if deleted_count else "草稿不存在或已被删除。",
    }


def load_product_from_index(product_id: str = "", file_path: str = "") -> dict[str, Any]:
    product_id = str(product_id or "").strip()
    file_path = str(file_path or "").strip()
    ensure_sqlite_store()
    sqlite_product_id = product_id
    if not sqlite_product_id and file_path.startswith("sqlite://products/"):
        sqlite_product_id = file_path.rsplit("/", 1)[-1]
    if sqlite_product_id:
        loaded = erp_db.load_product_model(APP_DIR, sqlite_product_id)
        if loaded:
            return normalize_product_fields(loaded)
    return load_product()


def product_id_from_body(body: dict[str, Any]) -> str:
    return str(body.get("product_id") or "").strip()


def load_required_product_from_body(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    product_id = product_id_from_body(body)
    if not product_id:
        return {}, {"ok": False, "error": "product_id 不能为空"}, 400
    product = load_product_from_index(product_id, "")
    loaded_id = str(product.get("product_id") or product.get("id") or "").strip()
    if loaded_id != product_id:
        return {}, {"ok": False, "error": "商品不存在", "product_id": product_id}, 404
    return product, None, 200


def load_draft_from_index(draft_id: str) -> dict[str, Any]:
    draft_id = str(draft_id or "").strip()
    ensure_sqlite_store()
    if draft_id:
        loaded = erp_db.load_product_for_draft(APP_DIR, draft_id)
        if loaded:
            return normalize_product_fields(loaded)
    return load_product()


def draft_product_context(product: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_product_fields(product or {})
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else {}
    dimensions = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    return {
        "product_id": str(normalized.get("product_id") or normalized.get("id") or ""),
        "source_product_id": str(normalized.get("source_product_id") or normalized.get("product_id") or normalized.get("id") or ""),
        "title": str(normalized.get("name") or source.get("title") or ""),
        "source_title": str(source.get("title") or normalized.get("name") or ""),
        "source_platform": str(source.get("source_platform") or normalized.get("source_platform") or ""),
        "source_url": str(source.get("source_url") or normalized.get("source_url") or ""),
        "brand": str(normalized.get("brand") or source.get("brand") or ""),
        "model": str(normalized.get("model") or source.get("model") or ""),
        "sku": str(normalized.get("sku") or ""),
        "stock": str(normalized.get("stock") or ""),
        "cost": str(normalized.get("cost") or normalized.get("source_price_cny_for_cost") or source.get("price") or ""),
        "source_price": str(source.get("price") or ""),
        "currency": str(source.get("currency") or ""),
        "weight_kg": str(source.get("weight_kg") or normalized.get("weight_kg") or ""),
        "dimensions": {
            "length_cm": str(dimensions.get("length_cm") or dimensions.get("lengthCm") or ""),
            "width_cm": str(dimensions.get("width_cm") or dimensions.get("widthCm") or ""),
            "height_cm": str(dimensions.get("height_cm") or dimensions.get("heightCm") or ""),
        },
        "image_pool": current_image_pool(normalized),
        "raw": normalized,
    }


def _normalized_target_payload(target: dict[str, Any], platform: str, selected_site: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(target if isinstance(target, dict) else {})
    payload["platform"] = platform
    payload["site"] = selected_site["code"]
    payload["language"] = selected_site["language"]
    payload["currency"] = selected_site["currency"]
    for camel_key, snake_key in (
        ("categoryId", "category_id"),
        ("categoryPath", "category_path"),
        ("validationErrors", "validation_errors"),
        ("categoryPrecheck", "category_precheck"),
        ("publishStatus", "publish_status"),
        ("lastPrecheck", "last_precheck"),
        ("lastPrecheckTarget", "last_precheck_target"),
        ("publishLogs", "publish_logs"),
    ):
        if camel_key in payload and snake_key not in payload:
            payload[snake_key] = payload[camel_key]
    if not isinstance(payload.get("attributes"), dict):
        payload["attributes"] = {}
    if not isinstance(payload.get("validation_errors"), list):
        payload["validation_errors"] = []
    if not isinstance(payload.get("publish_logs"), list):
        payload["publish_logs"] = []
    return payload


def load_draft_detail_from_index(draft_id: str) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        return {}, {"ok": False, "error": "draft_id 不能为空"}, 400
    ensure_sqlite_store()
    draft = erp_db.load_draft_model(APP_DIR, draft_id)
    if not draft:
        return {}, {"ok": False, "error": "草稿不存在", "draft_id": draft_id}, 404
    product = erp_db.load_product_model(APP_DIR, str(draft.get("source_product_id") or draft.get("product_id") or ""))
    if not product:
        return {}, {"ok": False, "error": "草稿关联商品不存在", "draft_id": draft_id}, 404
    return {
        "ok": True,
        "draft": draft,
        "productContext": draft_product_context(product),
        "productsIndex": load_products_index(),
        "draftsIndex": load_drafts_index(),
    }, None, 200


def save_draft_detail(draft_payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    draft_id = str(draft_payload.get("draft_id") or draft_payload.get("draftId") or "").strip()
    if not draft_id:
        return {}, {"ok": False, "error": "draft_id 不能为空"}, 400
    ensure_sqlite_store()
    existing = erp_db.load_draft_model(APP_DIR, draft_id)
    if not existing:
        return {}, {"ok": False, "error": "草稿不存在", "draft_id": draft_id}, 404
    product_id = str(existing.get("product_id") or "").strip()
    source_product_id = str(existing.get("source_product_id") or product_id).strip()
    existing_platform = str(existing.get("platform") or "").strip().lower()
    requested_language = str(draft_payload.get("language") or existing.get("language") or "").strip()
    raw_targets = draft_payload.get("target_sites") if isinstance(draft_payload.get("target_sites"), list) else draft_payload.get("targetSites")
    targets: list[dict[str, str]] = []
    for raw_target in raw_targets if isinstance(raw_targets, list) else []:
        target = raw_target if isinstance(raw_target, dict) else {}
        target_platform = str(target.get("platform") or "").strip().lower()
        selected_site = marketplace_site(target_platform, str(target.get("site") or target.get("site_id") or ""))
        if target_platform not in PLATFORMS or not selected_site.get("code"):
            continue
        if requested_language and selected_site["language"].lower() != requested_language.lower():
            continue
        if not any(item["platform"] == target_platform and item["site"] == selected_site["code"] for item in targets):
            targets.append(_normalized_target_payload(target, target_platform, selected_site))
    if not targets:
        requested_platform = str(draft_payload.get("platform") or existing_platform).strip().lower()
        platform = requested_platform if requested_platform in PLATFORMS else existing_platform
        selected_site = marketplace_site(platform, str(draft_payload.get("site") or existing.get("site") or ""))
        if platform not in PLATFORMS or not selected_site.get("code"):
            return {}, {"ok": False, "error": "草稿站点不支持", "draft_id": draft_id}, 400
        fallback_target = {}
        existing_targets = existing.get("target_sites") if isinstance(existing.get("target_sites"), list) else []
        if existing_targets:
            fallback_target = existing_targets[0] if isinstance(existing_targets[0], dict) else {}
        targets = [_normalized_target_payload(fallback_target, platform, selected_site)]
    primary_target = targets[0]
    platform = primary_target["platform"]
    platforms = []
    for target in targets:
        target_platform = str(target.get("platform") or "").strip().lower()
        if target_platform in PLATFORMS and target_platform not in platforms:
            platforms.append(target_platform)
    merged = {
        **existing,
        **dict(draft_payload),
        "draft_id": draft_id,
        "product_id": product_id,
        "source_product_id": source_product_id,
        "platform": platform,
        "platforms": platforms or [platform],
        "site": primary_target["site"],
        "target_sites": targets,
        "language": primary_target["language"],
        "currency": primary_target["currency"],
    }
    merged["images"] = normalize_draft_image_refs(merged.get("images"))
    saved_draft_id = erp_db.upsert_draft_model(APP_DIR, product_id, platform, merged)
    draft = erp_db.load_draft_model(APP_DIR, saved_draft_id)
    product = erp_db.load_product_model(APP_DIR, source_product_id or product_id)
    return {
        "ok": True,
        "draft": draft,
        "productContext": draft_product_context(product),
        "productsIndex": load_products_index(),
        "draftsIndex": load_drafts_index(),
        "message": "草稿已保存。",
    }, None, 200


def apply_image_assets_to_draft(
    draft_id: str,
    created_items: list[dict[str, Any]],
    strategy: str = "append",
) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        return {}, {"ok": False, "error": "draft_id 不能为空"}, 400
    ensure_sqlite_store()
    existing = erp_db.load_draft_model(APP_DIR, draft_id)
    if not existing:
        return {}, {"ok": False, "error": "草稿不存在", "draft_id": draft_id}, 404
    product_id = str(existing.get("product_id") or "").strip()
    platform = str(existing.get("platform") or "").strip().lower()
    if not product_id or platform not in PLATFORMS:
        return {}, {"ok": False, "error": "草稿关联商品或平台无效", "draft_id": draft_id}, 400
    product = erp_db.load_product_model(APP_DIR, str(existing.get("source_product_id") or product_id))
    next_images = apply_created_image_refs_to_draft(existing.get("images"), created_items, strategy)
    merged = {**existing, "images": next_images}
    product_for_status = dict(product or {})
    drafts = product_for_status.get("drafts") if isinstance(product_for_status.get("drafts"), dict) else {}
    product_for_status["drafts"] = {**drafts, platform: merged}
    merged["status"] = draft_workflow_status(product_for_status, platform)
    saved_draft_id = erp_db.upsert_draft_model(APP_DIR, product_id, platform, merged)
    draft = erp_db.load_draft_model(APP_DIR, saved_draft_id)
    product = erp_db.load_product_model(APP_DIR, str(draft.get("source_product_id") or product_id))
    return {
        "ok": True,
        "draft": draft,
        "productContext": draft_product_context(product),
        "productsIndex": load_products_index(),
        "draftsIndex": load_drafts_index(),
        "message": "草稿图片已更新。",
    }, None, 200


def save_draft_copy_result(product: dict[str, Any], target_market: str, copy: dict[str, Any]) -> dict[str, Any]:
    product = normalize_product_fields(product or {})
    product_id = str(product.get("product_id") or product.get("id") or "").strip()
    target_key = str(target_market or "").strip().lower() or "mercadolibre"
    if not product_id:
        raise RuntimeError("product_id 不能为空")
    if target_key not in PLATFORMS:
        raise RuntimeError("不支持的平台")
    drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
    draft = dict(drafts.get(target_key) if isinstance(drafts.get(target_key), dict) else {})
    draft.update(
        {
            "title": copy.get("title", ""),
            "description": copy.get("description", ""),
            "bullets": normalize_list(copy.get("bullets")),
            "search_terms": normalize_list(copy.get("search_keywords")),
            "language": str(copy.get("language") or draft.get("language") or ""),
            "copy_source": "ai",
            "copy_result": copy,
            "copy_generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    draft.setdefault("platform", target_key)
    draft.setdefault("platforms", [target_key])
    product_for_status = dict(product)
    merged_drafts = dict(drafts)
    merged_drafts[target_key] = draft
    product_for_status["drafts"] = merged_drafts
    draft["status"] = draft_workflow_status(product_for_status, target_key)
    saved_draft_id = erp_db.upsert_draft_model(APP_DIR, product_id, target_key, draft)
    saved = load_product_from_index(product_id, "")
    saved["current_draft_id"] = saved_draft_id
    saved["current_draft_platform"] = target_key
    return saved


def load_app_config() -> dict[str, Any]:
    raw = read_json(APP_CONFIG_PATH, default_app_config())
    config = normalize_app_config(raw)
    if not APP_CONFIG_PATH.exists():
        write_json(APP_CONFIG_PATH, config)
    return config


def save_app_config(config: dict[str, Any]) -> None:
    config = normalize_app_config(config)
    write_json(APP_CONFIG_PATH, config)
    # Runtime secrets live only under config/ so they are never mirrored into
    # packaged web assets.


def load_store_config() -> dict[str, Any]:
    return normalize_store_config(publisher.load_store_config(STORE_CONFIG_PATH))


_STORE_SENSITIVE_FIELDS = {
    "app_id",
    "client_id",
    "app_secret",
    "client_secret",
    "code_verifier",
    "access_token",
    "refresh_token",
    "redirect_uri",
    "content_token",
    "prices_token",
    "marketplace_token",
    "stocks_token",
    "api_key",
}


def default_store_config() -> dict[str, Any]:
    return publisher.load_store_config(STORE_CONFIG_PATH.with_name("__default_store_config__.json"))


def _sync_mercadolibre_secret_aliases(store: dict[str, Any]) -> None:
    app_secret = str(store.get("app_secret") or "").strip()
    client_secret = str(store.get("client_secret") or "").strip()
    if client_secret and not app_secret:
        store["app_secret"] = client_secret
    if app_secret and not client_secret:
        store["client_secret"] = app_secret


def merge_store_config_fields(
    base: dict[str, Any] | None,
    updates: dict[str, Any] | None,
    *,
    preserve_empty_sensitive: bool = True,
) -> dict[str, Any]:
    merged = deepcopy(base if isinstance(base, dict) else default_store_config())
    updates = updates if isinstance(updates, dict) else {}
    for section_key, section_updates in updates.items():
        if not isinstance(section_updates, dict):
            merged[section_key] = deepcopy(section_updates)
            continue
        section = merged.setdefault(section_key, {})
        if not isinstance(section, dict):
            section = {}
            merged[section_key] = section
        for field, value in section_updates.items():
            if (
                preserve_empty_sensitive
                and field in _STORE_SENSITIVE_FIELDS
                and value in (None, "")
                and str(section.get(field) or "").strip()
            ):
                continue
            section[field] = deepcopy(value)
        if section_key == "mercadolibre":
            _sync_mercadolibre_secret_aliases(section)
    return merged


def normalize_store_config(config: dict[str, Any] | None) -> dict[str, Any]:
    normalized = merge_store_config_fields(default_store_config(), config, preserve_empty_sensitive=False)
    ml = normalized.get("mercadolibre") if isinstance(normalized.get("mercadolibre"), dict) else {}
    if isinstance(ml, dict) and not str(ml.get("code_verifier") or "").strip():
        ml.pop("code_verifier", None)
    return normalized


def update_store_config_fields(platform: str, fields: dict[str, Any], *, preserve_empty_sensitive: bool = True) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    config = load_store_config()
    updated = merge_store_config_fields(config, {platform: fields}, preserve_empty_sensitive=preserve_empty_sensitive)
    save_store_config(updated)
    return updated


def save_store_config(config: dict[str, Any], *, preserve_empty_sensitive: bool = True) -> None:
    STORE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if preserve_empty_sensitive:
        existing = publisher.load_store_config(STORE_CONFIG_PATH) if STORE_CONFIG_PATH.exists() else default_store_config()
        merged = merge_store_config_fields(existing, config, preserve_empty_sensitive=True)
    else:
        merged = normalize_store_config(config)
    publisher.save_store_config(STORE_CONFIG_PATH, merged)


def _auth_status_label(status: Any, store: dict[str, Any]) -> str:
    text = str(status or "").strip().lower()
    error_code = str(store.get("auth_error_code") or "").strip().lower()
    error_message = str(store.get("auth_error_message") or "").strip().lower()
    has_credentials = any(
        str(store.get(key) or "").strip()
        for key in (
            "access_token",
            "refresh_token",
            "app_id",
            "app_secret",
            "code_verifier",
            "content_token",
            "prices_token",
            "marketplace_token",
            "stocks_token",
            "client_id",
            "api_key",
        )
    )
    if text in {"ok", "success", "tested", "测试成功"}:
        return "测试成功"
    if text in {"failed", "error", "测试失败"}:
        if "429" in error_code or "429" in error_message or "rate" in error_code or "too many requests" in error_message:
            return "被限流"
        if "expired" in error_code or "expired" in error_message:
            return "Token 过期"
        if "permission" in error_code or "401" in error_message or "403" in error_message or "unauthorized" in error_message:
            return "权限不足"
        return "测试失败"
    if text in {"saved", "pending", "saved_not_tested", "已保存，未测试"}:
        return "已保存，未测试"
    if has_credentials:
        return "已保存，未测试"
    return "未配置"


def _auth_next_action(platform: str, status_label: str, error_code: str, error_message: str) -> str:
    platform = str(platform or "").strip().lower()
    error_code_l = str(error_code or "").strip().lower()
    error_message_l = str(error_message or "").strip().lower()
    if status_label == "测试成功":
        return "已可用于发布"
    if status_label == "被限流":
        return "等待一段时间后重新测试"
    if status_label == "Token 过期":
        if platform == "mercadolibre":
            return "使用刷新 token 更新 access token"
        return "重新生成并保存 token"
    if status_label == "权限不足":
        if platform == "mercadolibre":
            return "检查 App 权限和授权范围"
        return "检查 Token 权限是否包含当前接口"
    if "redirect_uri" in error_code_l or "redirect_uri" in error_message_l:
        return "检查 Redirect URI 是否与开发者后台完全一致"
    if "invalid_client" in error_code_l:
        return "检查 App ID / Client Secret 是否正确"
    if "invalid_grant" in error_code_l or "refresh token invalid" in error_message_l:
        return "重新生成授权链接并重新授权"
    if "callback" in error_code_l or "callback" in error_message_l:
        return "确认回调地址可访问且已正确注册"
    if "network" in error_code_l or "ssl" in error_message_l or "unexpected_eof" in error_message_l or "eof occurred" in error_message_l:
        return "检查本机网络、代理或防火墙后重试 Mercado Libre 授权接口"
    if platform == "mercadolibre":
        return "重新发起授权并检查回调地址"
    if platform == "yandex":
        return "确认 Yandex API Token 已保存且具备目标接口权限"
    if platform == "ozon":
        return "确认 Client ID 和 API Key 已保存且未过期"
    return "检查配置后重新测试"


def explain_mercadolibre_auth_error(error_code: str = "", error_message: str = "") -> dict[str, str]:
    from .publish_logs_runtime import _mercadolibre_test_error_code

    code = str(error_code or "").strip()
    message = str(error_message or "").strip()
    text = f"{code} {message}".lower()
    normalized = _mercadolibre_test_error_code(text) if code.lower() not in {
        "invalid_grant",
        "redirect_uri_mismatch",
        "code_verifier_missing",
        "token_expired",
        "refresh_token_invalid",
        "invalid_client",
    } else code.lower()
    if "code_verifier" in text:
        normalized = "code_verifier_missing"
    if "redirect_uri" in text and ("mismatch" in text or "different" in text or "does not match" in text):
        normalized = "redirect_uri_mismatch"
    if "expired" in text and "token" in text:
        normalized = "token_expired"
    if "ssl" in text or "unexpected_eof" in text or "eof occurred" in text or "urlopen error" in text:
        normalized = "network_tls_failed"
    if normalized == "invalid_grant":
        return {
            "platform": "mercadolibre",
            "code": "invalid_grant",
            "title": "授权 code 已失效或已被使用",
            "plain_message": "Mercado Libre 的 code 是一次性的，通常几分钟内有效；粘贴慢了、重复使用、或重新生成过授权链接都会导致这个错误。",
            "next_action": "重新生成授权链接，用已登录店铺主账号的浏览器打开，授权后立刻复制地址栏里的 code 回 ERP 换 token。",
        }
    if normalized == "redirect_uri_mismatch":
        return {
            "platform": "mercadolibre",
            "code": "redirect_uri_mismatch",
            "title": "Redirect URI 不一致",
            "plain_message": "ERP 里填写的 Redirect URI 必须和 Mercado Libre Developers 后台应用里保存的地址完全一致，包括 https、路径和末尾斜杠。",
            "next_action": "检查 ERP 和 Mercado Libre Developers 后台的 Redirect URI，保持完全一致后重新生成授权链接。",
        }
    if normalized == "code_verifier_missing":
        return {
            "platform": "mercadolibre",
            "code": "CODE_VERIFIER_MISSING",
            "title": "缺少本次授权链接对应的 code_verifier",
            "plain_message": "PKCE 授权要求“生成授权链接”和“用 code 换 token”必须来自同一次流程。重启 ERP、清空配置或直接粘旧 code 都可能缺这个值。",
            "next_action": "重新生成授权链接，不要复用旧 code；授权后直接回到当前 ERP 页面换 token。",
        }
    if normalized in {"token_expired", "refresh_token_invalid"}:
        return {
            "platform": "mercadolibre",
            "code": normalized,
            "title": "Token 已过期或 Refresh Token 不可用",
            "plain_message": "当前保存的 Mercado Libre token 不能继续调用接口，可能是过期、被后台撤销，或复制了不完整的 token。",
            "next_action": "先点击刷新 token；如果仍失败，重新生成授权链接并重新授权。",
        }
    if normalized == "invalid_client":
        return {
            "platform": "mercadolibre",
            "code": "invalid_client",
            "title": "App ID 或 Client Secret 不正确",
            "plain_message": "Mercado Libre 不认可当前应用信息，通常是 App ID、Client Secret 填错，或复制时多了空格。",
            "next_action": "回 Mercado Libre Developers 应用详情复制 App ID 和 Client Secret，保存后重新生成授权链接。",
        }
    if normalized in {"NETWORK_BLOCKED", "NETWORK_TIMEOUT", "network_tls_failed"}:
        return {
            "platform": "mercadolibre",
            "code": normalized,
            "title": "Mercado Libre 授权接口网络连接失败",
            "plain_message": "ERP 已请求 Mercado Libre token 接口，但 HTTPS/TLS 连接在读取响应时被提前断开，常见原因是代理、VPN、公司网络 TLS 拦截、防火墙或临时网络抖动。",
            "next_action": "确认当前电脑能稳定访问 https://api.mercadolibre.com，关闭会拦截 HTTPS 的代理/抓包工具后重试；如果必须走代理，请让 Python/系统网络也使用同一代理。",
        }
    return {
        "platform": "mercadolibre",
        "code": normalized or code or "mercadolibre_auth_failed",
        "title": "Mercado Libre 授权失败",
        "plain_message": message or "授权接口返回失败，但没有提供更具体的错误原因。",
        "next_action": _auth_next_action("mercadolibre", "测试失败", normalized or code, message),
    }


def mercadolibre_auth_checklist(config: dict[str, Any] | None = None) -> dict[str, Any]:
    ml = config if isinstance(config, dict) else load_store_config().get("mercadolibre", {})
    app_id = str(ml.get("app_id") or ml.get("client_id") or "").strip()
    app_secret = str(ml.get("app_secret") or ml.get("client_secret") or "").strip()
    redirect_uri = str(ml.get("redirect_uri") or "").strip()
    site_id = str(ml.get("site_id") or "CBT").strip() or "CBT"
    code_verifier = str(ml.get("code_verifier") or "").strip()
    access_token = str(ml.get("access_token") or "").strip()
    refresh_token = str(ml.get("refresh_token") or "").strip()
    missing: list[str] = []
    if not app_id:
        missing.append("APP_ID_MISSING")
    if not app_secret:
        missing.append("CLIENT_SECRET_MISSING")
    if not redirect_uri:
        missing.append("REDIRECT_URI_MISSING")
    elif not redirect_uri.lower().startswith("https://"):
        missing.append("REDIRECT_URI_MUST_BE_HTTPS")
    ready_for_auth_link = not any(code in missing for code in {"APP_ID_MISSING", "CLIENT_SECRET_MISSING", "REDIRECT_URI_MISSING", "REDIRECT_URI_MUST_BE_HTTPS"})
    token_ready = bool(access_token and refresh_token)
    if not ready_for_auth_link:
        if "APP_ID_MISSING" in missing:
            next_action = "填写 Mercado Libre Developers 里的 App ID / Client ID。"
        elif "CLIENT_SECRET_MISSING" in missing:
            next_action = "填写 Mercado Libre Developers 里的 Client Secret。"
        elif "REDIRECT_URI_MISSING" in missing:
            next_action = "填写 Redirect URI，默认可用 https://example.com/callback。"
        else:
            next_action = "Redirect URI 必须以 https:// 开头，并与 Developers 后台完全一致。"
    elif not token_ready:
        next_action = "生成授权链接，用店铺主账号浏览器打开，复制 code 回 ERP 换 token。"
    else:
        next_action = "授权配置已具备。到草稿的类目/属性页实时匹配 Mercado Libre 类目，并按选中类目读取必填属性。"
    fields = [
        {"key": "app_id", "label": "App ID / Client ID", "ok": bool(app_id), "value": mask_secret(app_id) if app_id else "缺失"},
        {"key": "app_secret", "label": "Client Secret", "ok": bool(app_secret), "value": mask_secret(app_secret) if app_secret else "缺失"},
        {"key": "redirect_uri", "label": "Redirect URI", "ok": bool(redirect_uri) and redirect_uri.lower().startswith("https://"), "value": redirect_uri or "缺失"},
        {"key": "site_id", "label": "Site", "ok": bool(site_id), "value": site_id},
        {"key": "code_verifier", "label": "code_verifier", "ok": bool(code_verifier), "value": "已生成，等待 code 换 token" if code_verifier else "未生成"},
        {"key": "access_token", "label": "Access Token", "ok": bool(access_token), "value": mask_secret(access_token) if access_token else "未保存"},
        {"key": "refresh_token", "label": "Refresh Token", "ok": bool(refresh_token), "value": mask_secret(refresh_token) if refresh_token else "未保存"},
    ]
    lines = ["Mercado Libre 授权配置检查清单"]
    lines.extend([f"- {item['label']}：{'OK' if item['ok'] else '缺失/需检查'}（{item['value']}）" for item in fields])
    lines.append(f"- 下一步：{next_action}")
    return {
        "platform": "mercadolibre",
        "ready_for_auth_link": ready_for_auth_link,
        "token_ready": token_ready,
        "missing_codes": missing,
        "fields": fields,
        "next_action": next_action,
        "copy_text": "\n".join(lines),
    }


def summarize_store_auth(platform: str, store: dict[str, Any]) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    store = store if isinstance(store, dict) else {}
    status_label = _auth_status_label(store.get("auth_status"), store)
    error_code = str(store.get("auth_error_code") or "").strip()
    error_message = str(store.get("auth_error_message") or "").strip()
    masked_account = str(store.get("auth_masked_account") or "").strip()
    if not masked_account:
        if platform == "mercadolibre":
            masked_account = str(store.get("shop_name") or store.get("user_id") or "").strip()
        elif platform == "yandex":
            masked_account = str(store.get("shop_name") or store.get("api_token") or "").strip()
        elif platform == "ozon":
            masked_account = str(store.get("shop_name") or store.get("client_id") or "").strip()
    if not masked_account:
        candidates = [
            store.get("access_token"),
            store.get("refresh_token"),
            store.get("api_token"),
            store.get("api_key"),
            store.get("app_secret"),
        ]
        for candidate in candidates:
            if str(candidate or "").strip():
                masked_account = mask_secret(candidate)
                break
    return {
        "platform": platform,
        "status": status_label,
        "checked_at": str(store.get("auth_checked_at") or "").strip(),
        "masked_account": masked_account,
        "error_code": error_code,
        "error_message": error_message,
        "next_action": str(store.get("auth_next_action") or _auth_next_action(platform, status_label, error_code, error_message)).strip(),
        "shop_name": str(store.get("shop_name") or "").strip(),
        "site_id": str(store.get("site_id") or store.get("country") or "").strip(),
        "bound": status_label in {"测试成功", "已绑定"},
    }


def summarize_store_auth_states(store_config: dict[str, Any]) -> dict[str, Any]:
    store_config = store_config if isinstance(store_config, dict) else {}
    return {
        platform: summarize_store_auth(platform, store_config.get(platform, {}))
        for platform in ("mercadolibre", "yandex", "ozon")
    }


def store_auth_failure_code(platform: str, message: str) -> str:
    text = str(message or "").lower()
    platform = str(platform or "").strip().lower()
    if platform == "mercadolibre":
        if "redirect_uri" in text and "mismatch" in text:
            return "redirect_uri_mismatch"
        if "invalid_client" in text or "client_id" in text and "invalid" in text:
            return "invalid_client"
        if "invalid_grant" in text:
            return "invalid_grant"
        if "refresh token" in text and "invalid" in text:
            return "refresh_token_invalid"
        if "expired" in text and "token" in text:
            return "token_expired"
        if "callback" in text:
            return "callback_not_received"
        return "mercadolibre_auth_failed"
    if platform == "yandex":
        if "429" in text or "too many requests" in text:
            return "rate_limited"
        if "401" in text or "403" in text or "unauthorized" in text:
            return "permission_denied"
        return "yandex_auth_failed"
    if platform == "ozon":
        if "429" in text or "too many requests" in text:
            return "rate_limited"
        if "401" in text or "403" in text or "unauthorized" in text:
            return "permission_denied"
        return "ozon_auth_failed"
    return "auth_failed"


def _store_auth_result_fields(
    platform: str,
    status: str,
    account: str = "",
    error_code: str = "",
    error_message: str = "",
    next_action: str = "",
) -> dict[str, str]:
    from .collect_helpers import collect_time_iso

    platform = str(platform or "").strip().lower()
    account_text = str(account or "").strip()
    error_code_text = str(error_code or "").strip()
    error_message_text = str(error_message or "").strip()
    next_action_text = str(next_action or "").strip()
    return {
        "auth_status": status,
        "auth_checked_at": collect_time_iso(),
        "auth_masked_account": account_text,
        "auth_error_code": error_code_text,
        "auth_error_message": error_message_text,
        "auth_next_action": next_action_text or _auth_next_action(platform, status, error_code_text, error_message_text),
    }


def _clear_store_auth_result() -> dict[str, str]:
    return {
        "auth_status": "",
        "auth_checked_at": "",
        "auth_masked_account": "",
        "auth_error_code": "",
        "auth_error_message": "",
        "auth_next_action": "",
    }



def default_app_config() -> dict[str, Any]:
    return app_config_runtime.default_app_config()


def normalize_app_config(config: dict[str, Any]) -> dict[str, Any]:
    return app_config_runtime.normalize_app_config(config)


__all__ = [
    "apply_image_assets_to_draft",
    "delete_draft_from_index",
    "delete_products_from_index",
    "explain_mercadolibre_auth_error",
    "load_app_config",
    "load_draft_detail_from_index",
    "load_draft_from_index",
    "load_drafts_index",
    "load_product",
    "load_product_from_index",
    "load_products_index",
    "load_store_config",
    "mercadolibre_auth_checklist",
    "merge_store_config_fields",
    "normalize_app_config",
    "normalize_product_fields",
    "product_id_from_body",
    "product_identity",
    "load_required_product_from_body",
    "publish_queue_platforms",
    "save_draft_detail",
    "save_app_config",
    "save_draft_copy_result",
    "save_product",
    "save_product_profile",
    "save_store_config",
    "summarize_store_auth_states",
    "sync_product_workflow_statuses",
]
