# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sys
from copy import deepcopy
import subprocess
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import erp_db
import main as generator
import marketplace_publish as publisher
from erp_web import app_config as app_config_runtime
from erp_web import category_cache as category_cache_runtime
from services import collect_service, config_service, copy_service, html_extract_service as legacy, image_service, pricing_service
from product_model import (
    apply_ai_attribute_fill,
    apply_category_selection,
    default_collect_diagnostics,
    default_draft,
    default_product_model,
    category_cache_status,
    find_category_record,
    load_category_cache,
    PLATFORMS,
    image_pool_legacy_views,
    normalize_image_pool_item,
    normalize_platforms,
    merge_source_partial_result,
    normalize_image_pool,
    normalize_product_model,
    parse_dimensions_text,
    SOURCE_COMPAT_IMAGE_ORIGINS,
    search_category_cache,
    validate_category_precheck,
)
from publishing_bus import PublishingBus


APP_DIR = Path(__file__).resolve().parents[1]
DIST_DIR = APP_DIR / "dist"
DATA_DIR = APP_DIR / "data"
CONFIG_DIR = APP_DIR / "config"
CACHE_DIR = DATA_DIR / "cache"
LOGS_DIR = DATA_DIR / "logs"
IMAGES_DIR = DATA_DIR / "images"
EXPORTS_DIR = DATA_DIR / "exports"
OUTPUT_DIR = LOGS_DIR
STORE_CONFIG_PATH = CONFIG_DIR / "store_config.json"
APP_CONFIG_PATH = CONFIG_DIR / "app_config.json"
REMOVED_LEGACY_CONFIG_PATHS: tuple[Path, ...] = ()
LEGACY_STORE_CONFIG_PATHS = REMOVED_LEGACY_CONFIG_PATHS
LEGACY_APP_CONFIG_PATHS = REMOVED_LEGACY_CONFIG_PATHS
PUBLISH_LOG_PATH = OUTPUT_DIR / "publish_logs.json"
PUBLISHING_JOB_DIR = OUTPUT_DIR / "publishing_jobs"
TASK_DIR = OUTPUT_DIR / "codex_tasks"
CHATGPT_DIR = IMAGES_DIR / "chatgpt"
SOURCE_DIR = IMAGES_DIR / "source"
UPLOAD_DIR = IMAGES_DIR / "uploads"
COLLECT_DEBUG_DIR = CACHE_DIR / "collect_debug"
BROWSER_PROFILE_DIR = APP_DIR / "browser_profile" / "1688"
FRONT_DIR = APP_DIR / "front"
FRONT_DIST_DIR = APP_DIR / "backend" / "internal" / "web" / "dist"
FRONT_DIST_INDEX_PATH = FRONT_DIST_DIR / "index.html"
WEB_TEMPLATE_PATH = FRONT_DIR / "index.html"
WEB_PORT = int(os.environ.get("ERP_PORT", "5000"))
BROWSER_DEBUG_PORT = int(os.environ.get("ERP_BROWSER_DEBUG_PORT", "9222"))
DEFAULT_EXCHANGE_RATE_API_URL = "https://open.er-api.com/v6/latest/USD"
AI_TEXT_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("AI_TEXT_REQUEST_TIMEOUT_SECONDS", "60"))
AI_IMAGE_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("AI_IMAGE_REQUEST_TIMEOUT_SECONDS", "180"))
BROWSER_DEBUG_PROFILE_DIR = Path(os.environ.get("ERP_BROWSER_PROFILE_DIR", str(APP_DIR / "browser_profile" / "debug")))
DRAFT_WORKFLOW_STATUSES = (
    "collected",
    "claimed",
    "copy_ready",
    "images_ready",
    "ready_to_publish",
    "published",
)
EXCHANGE_RATE_CACHE: dict[str, Any] = {}

VERIFY_MARKERS = (
    "安全验证",
    "slide.1688.com",
    "请验证身份",
    "验证码",
    "captcha",
    "verify",
    "security verification",
)

_json_category_cache_status = category_cache_status
_json_find_category_record = find_category_record
_json_load_category_cache = load_category_cache
_json_search_category_cache = search_category_cache

AMAZON_VERIFY_MARKERS = (
    "robot check",
    "captcha",
    "enter the characters you see below",
    "validatecaptcha",
    "sorry, this page is not available",
    "this item is no longer available",
)


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_sqlite_store() -> None:
    erp_db.initialize_database(APP_DIR)


def _ensure_sqlite_category_cache(platform: str) -> dict[str, Any]:
    erp_db.initialize_database(APP_DIR)
    platform = str(platform or "mercadolibre").strip().lower()
    status = erp_db.category_cache_status(APP_DIR, platform)
    if status.get("records"):
        return status
    cache = _json_load_category_cache(platform)
    erp_db.import_category_cache(APP_DIR, cache if isinstance(cache, dict) else {})
    return erp_db.category_cache_status(APP_DIR, platform)


def load_category_cache(platform: str) -> dict[str, Any]:
    platform = str(platform or "mercadolibre").strip().lower()
    status = _ensure_sqlite_category_cache(platform)
    return {
        "platform": platform,
        "storage": "sqlite",
        "updated_at": status.get("updated_at") or "",
        "records": erp_db.search_category_records(APP_DIR, platform, limit=10000),
    }


def category_cache_status(platform: str) -> dict[str, Any]:
    platform = str(platform or "mercadolibre").strip().lower()
    status = _ensure_sqlite_category_cache(platform)
    json_status = _json_category_cache_status(platform)
    if isinstance(json_status, dict):
        status = {
            **json_status,
            **status,
            "records": status.get("records", 0),
            "sqlite_records": status.get("records", 0),
            "json_records": json_status.get("records", 0),
            "storage": "sqlite",
        }
    return status


def search_category_cache(platform: str, query: str = "", site: str = "", limit: int = 20) -> list[dict[str, Any]]:
    platform = str(platform or "mercadolibre").strip().lower()
    _ensure_sqlite_category_cache(platform)
    results = erp_db.search_category_records(APP_DIR, platform, query=query, site=site, limit=limit)
    if results:
        return results
    return _json_search_category_cache(platform, query=query, site=site, limit=limit)


def find_category_record(platform: str, category_id: str, site: str = "") -> dict[str, Any] | None:
    platform = str(platform or "mercadolibre").strip().lower()
    _ensure_sqlite_category_cache(platform)
    record = erp_db.find_category_record(APP_DIR, platform, category_id, site=site)
    return record or _json_find_category_record(platform, category_id, site=site)


_CATEGORY_AI_KEYWORD_MAP = {
    "风扇": ["ventilador", "fan"],
    "喷雾": ["niebla", "humidificador", "mist"],
    "无叶": ["sin aspas", "bladeless"],
    "耳机": ["audifonos", "auriculares", "headphones"],
    "瓶": ["botella", "bottle"],
    "水杯": ["vaso", "termo", "cup"],
    "项链": ["collar", "necklace"],
    "灯": ["lampara", "light"],
    "手机壳": ["funda", "case"],
}
_CATEGORY_AI_STOPWORDS = {
    "api", "stage", "collect", "product", "backend", "test", "manual", "imported",
    "the", "and", "for", "with", "from", "para", "con", "producto", "de", "del",
    "una", "uno", "los", "las", "por", "sin",
}


def _category_suggest_terms(product: dict[str, Any]) -> list[str]:
    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "mercadolibre")
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    chunks = [
        product.get("name"),
        product.get("category"),
        product.get("brand"),
        product.get("model"),
        source.get("title"),
        source.get("description"),
        draft.get("title"),
        draft.get("description"),
        draft.get("brand"),
        draft.get("model"),
    ]
    text = " ".join(str(item or "") for item in chunks).lower()
    raw_terms = [
        item.strip().lower()
        for item in re.split(r"[\s,，/|;；:：()（）\\-]+", text)
        if len(item.strip()) >= 2 and item.strip().lower() not in _CATEGORY_AI_STOPWORDS and not item.strip().isdigit()
    ]
    terms: list[str] = []
    for term in raw_terms[:80]:
        terms.append(term)
        for key, mapped in _CATEGORY_AI_KEYWORD_MAP.items():
            if key in term or key in text:
                terms.extend(mapped)
    return list(dict.fromkeys(item for item in terms if item))


def _category_suggest_query(product: dict[str, Any]) -> str:
    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "mercadolibre")
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    for value in (
        draft.get("title"),
        source.get("title"),
        product.get("name"),
        product.get("category"),
    ):
        text = str(value or "").strip()
        if text:
            return text[:120]
    return " ".join(_category_suggest_terms(product)[:8])


def _mercadolibre_domain_discovery_suggestions(product: dict[str, Any], site: str, limit: int) -> list[dict[str, Any]]:
    query = _category_suggest_query(product)
    if not query:
        return []
    token = str((load_store_config().get("mercadolibre") or {}).get("access_token") or "").strip()
    suggestions: list[dict[str, Any]] = []
    sites = [str(site or "").strip().upper() or "CBT"]
    if "MLM" not in sites:
        sites.append("MLM")
    for site_id in sites:
        try:
            data = http_json(
                f"https://api.mercadolibre.com/sites/{urllib.parse.quote(site_id)}/domain_discovery/search?q={urllib.parse.quote(query)}&limit={max(1, min(8, limit))}",
                token or None,
            )
        except Exception:
            continue
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            category_id = str(item.get("category_id") or "").strip()
            if not category_id:
                continue
            name = str(item.get("category_name") or item.get("domain_name") or category_id).strip()
            name_l = name.lower()
            score = 100 if site_id == sites[0] else 80
            if any(token in name_l for token in ("fan", "fans", "ventilador", "ventiladores")):
                score += 40
            if any(token in name_l for token in ("warmer", "calentador")) and any(term in query.lower() for term in ("fan", "ventilador", "风扇")):
                score -= 30
            suggestions.append(
                {
                    "id": category_id,
                    "category_id": category_id,
                    "name": name,
                    "path": name,
                    "site": site_id,
                    "score": score,
                    "matched_terms": [query],
                    "source": "mercadolibre_domain_discovery",
                    "raw": item,
                }
            )
    deduped: dict[str, dict[str, Any]] = {}
    for item in suggestions:
        deduped.setdefault(str(item.get("category_id") or ""), item)
    return sorted(deduped.values(), key=lambda item: int(item.get("score") or 0), reverse=True)[:limit]


def suggest_category_ids(product: dict[str, Any], platform: str = "mercadolibre", site: str = "", limit: int = 5) -> dict[str, Any]:
    platform = str(platform or "mercadolibre").strip().lower()
    site = str(site or load_store_config().get("mercadolibre", {}).get("site_id") or "").strip()
    terms = _category_suggest_terms(product)
    _ensure_sqlite_category_cache(platform)
    records = erp_db.search_category_records(APP_DIR, platform, query="", site=site, limit=10000)
    if not records and site:
        records = erp_db.search_category_records(APP_DIR, platform, query="", site="", limit=10000)
    live_suggestions = _mercadolibre_domain_discovery_suggestions(product, site, max(1, int(limit or 5))) if platform == "mercadolibre" else []
    scored: list[tuple[int, dict[str, Any], list[str]]] = []
    for record in records:
        haystack = " ".join(
            [
                str(record.get("category_id") or ""),
                str(record.get("name_original") or ""),
                str(record.get("name_cn") or ""),
                " ".join(map(str, record.get("path_original") or [])),
                " ".join(map(str, record.get("path_cn") or [])),
                " ".join(map(str, record.get("keywords") or [])),
            ]
        ).lower()
        matched = [term for term in terms if term and term in haystack]
        if not matched:
            continue
        score = sum(8 if " " in term else 3 for term in matched)
        category_id = str(record.get("category_id") or "")
        if category_id and category_id.lower() in haystack:
            score += 1
        if score >= 6:
            scored.append((score, record, matched[:8]))
    scored.sort(key=lambda item: item[0], reverse=True)
    suggestions = list(live_suggestions)
    seen_ids = {str(item.get("category_id") or item.get("id") or "") for item in suggestions}
    for score, record, matched in scored[: max(1, int(limit or 5))]:
        category_id = str(record.get("category_id") or record.get("id") or "")
        if category_id in seen_ids:
            continue
        suggestions.append(
            {
                "id": category_id,
                "category_id": category_id,
                "name": record.get("name_original") or record.get("name_cn") or record.get("category_id") or "",
                "path": " / ".join(str(item) for item in (record.get("path_original") or record.get("path_cn") or []) if str(item).strip()) or str(record.get("name_original") or ""),
                "site": record.get("site") or site,
                "score": score,
                "matched_terms": matched,
                "raw": record,
            }
        )
        seen_ids.add(category_id)
        if len(suggestions) >= max(1, int(limit or 5)):
            break
    return {
        "ok": True,
        "platform": platform,
        "site": site,
        "terms": terms[:30],
        "suggestions": suggestions,
        "cache_status": category_cache_status(platform),
    }


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
    images = normalize_list(draft.get("images"))
    if images:
        return True
    pool = current_image_pool(product)
    return any(
        isinstance(item, dict)
        and str(item.get("status") or "").strip().lower() != "empty"
        and str(item.get("origin") or "").strip().lower() in {"ai_generated", "chatgpt_import", "local_upload"}
        for item in pool
    )


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
        if draft_workflow_status(product, platform) == "ready_to_publish":
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
    return publisher.load_store_config(STORE_CONFIG_PATH)


def save_store_config(config: dict[str, Any]) -> None:
    STORE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    publisher.save_store_config(STORE_CONFIG_PATH, config)


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
    if platform == "wildberries":
        return "确认 Token 已复制完整且接口权限正确"
    if platform == "ozon":
        return "确认 Client ID 和 API Key 已保存且未过期"
    return "检查配置后重新测试"


def explain_mercadolibre_auth_error(error_code: str = "", error_message: str = "") -> dict[str, str]:
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
    site_id = str(ml.get("site_id") or "MLM").strip() or "MLM"
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
        next_action = "授权配置已具备。可直接点击授权页里的“立即刷新类目缓存”，同步 Mercado Libre 官方类目和必填属性。"
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
        elif platform == "wildberries":
            masked_account = str(store.get("shop_name") or store.get("subject_id") or "").strip()
        elif platform == "ozon":
            masked_account = str(store.get("shop_name") or store.get("client_id") or "").strip()
    if not masked_account:
        candidates = [
            store.get("access_token"),
            store.get("refresh_token"),
            store.get("content_token"),
            store.get("prices_token"),
            store.get("marketplace_token"),
            store.get("stocks_token"),
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
        for platform in ("mercadolibre", "wildberries", "ozon")
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
    if platform == "wildberries":
        if "429" in text or "too many requests" in text:
            return "rate_limited"
        if "401" in text or "403" in text or "unauthorized" in text:
            return "permission_denied"
        return "wildberries_auth_failed"
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



AI_CONFIG_ALIAS_KEYS_TO_DROP = app_config_runtime.AI_CONFIG_ALIAS_KEYS_TO_DROP

def default_app_config() -> dict[str, Any]:
    return app_config_runtime.default_app_config()


def normalize_ai_section(section: Any, defaults: dict[str, str], include_quality: bool = False) -> dict[str, str]:
    return app_config_runtime.normalize_ai_section(section, defaults, include_quality=include_quality)


def normalize_app_config(config: dict[str, Any]) -> dict[str, Any]:
    return app_config_runtime.normalize_app_config(config)


def image_items_from_paths(paths: list[str]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        items.append(
            {
                "name": path.name,
                "path": str(path),
                "folder": str(path.parent),
                "url": f"/file?path={urllib.parse.quote(str(path), safe='')}",
                "size": f"{max(1, path.stat().st_size // 1024)} KB",
                "time": time.strftime("%m/%d %H:%M", time.localtime(path.stat().st_mtime)),
            }
        )
    return items


def image_files(folder: Path, recursive: bool = False) -> list[dict[str, str]]:
    if not folder.exists():
        return []
    paths = folder.rglob("*") if recursive else folder.iterdir()
    items: list[dict[str, str]] = []
    for path in paths:
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        items.append(
            {
                "name": path.name,
                "path": str(path),
                "folder": str(path.parent),
                "url": f"/file?path={urllib.parse.quote(str(path), safe='')}",
                "size": f"{max(1, path.stat().st_size // 1024)} KB",
                "time": time.strftime("%m/%d %H:%M", time.localtime(path.stat().st_mtime)),
            }
        )
    return sorted(items, key=lambda item: Path(item["path"]).stat().st_mtime, reverse=True)


def _is_web_image_ref(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith(("http://", "https://", "data:", "blob:", "/file?", "ml-id:"))


def _is_local_image_ref(value: str) -> bool:
    value = value.strip()
    if not value or _is_web_image_ref(value):
        return False
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme and len(parsed.scheme) > 1:
        return False
    return bool(Path(value).suffix or "\\" in value or "/" in value or Path(value).is_absolute())


def _resolve_local_image_ref(value: str) -> Path | None:
    value = value.strip()
    if not _is_local_image_ref(value):
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = APP_DIR / candidate
    return candidate


def _display_image_ref(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("/file?"):
        return value
    if value.lower().startswith(("http://", "https://", "data:", "blob:", "ml-id:")):
        return value
    candidate = _resolve_local_image_ref(value)
    if candidate and candidate.exists() and candidate.is_file():
        return file_url(candidate)
    return ""


def _pool_display_item(item: dict[str, Any]) -> dict[str, Any]:
    raw_preview = str(item.get("preview_url") or "").strip()
    path = str(item.get("path") or "").strip()
    url = str(item.get("url") or "").strip()
    display_ref = _display_image_ref(path) or _display_image_ref(raw_preview) or _display_image_ref(url)
    has_local_ref = any(_is_local_image_ref(value) for value in (path, raw_preview, url))
    status = str(item.get("status") or "ready")
    note = str(item.get("note") or "")
    if has_local_ref and not display_ref and status == "ready":
        status = "missing_file"
        note = note or "文件不存在或路径错误"
    return {
        "id": str(item.get("id") or ""),
        "path": path,
        "url": display_ref,
        "preview_url": display_ref,
        "origin": str(item.get("origin") or "source"),
        "usage": str(item.get("usage") or "detail"),
        "platforms": list(item.get("platforms") or []),
        "is_main": bool(item.get("is_main")),
        "selected": bool(item.get("selected")),
        "order": int(item.get("order") or 0),
        "status": status,
        "note": note,
        "width_px": item.get("width_px") or item.get("width"),
        "height_px": item.get("height_px") or item.get("height"),
        "size_label": str(item.get("size_label") or item.get("dimensions") or item.get("size") or ""),
    }


def _read_image_dimensions_from_path(path: Path | None) -> tuple[int | None, int | None, str]:
    if not path or not path.exists() or not path.is_file():
        return None, None, "local image file not found"
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
            return int(width), int(height), ""
    except Exception as exc:
        return None, None, f"image size unavailable: {exc}"


def enrich_image_pool_item_dimensions(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    if normalized.get("width_px") and normalized.get("height_px"):
        normalized["size_label"] = normalized.get("size_label") or f"{normalized.get('width_px')}x{normalized.get('height_px')}"
        return normalized
    path = _local_path_from_image_item(normalized)
    width, height, note = _read_image_dimensions_from_path(path)
    if width and height:
        normalized["width_px"] = width
        normalized["height_px"] = height
        normalized["size_label"] = f"{width}x{height}"
    else:
        normalized["size_label"] = normalized.get("size_label") or "unknown"
        if note:
            existing_note = str(normalized.get("note") or "")
            normalized["note"] = existing_note if note in existing_note else (f"{existing_note}; {note}".strip("; "))
    return normalized


def enrich_product_image_dimensions(product: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else {}
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    if pool:
        source["image_pool"] = normalize_image_pool([enrich_image_pool_item_dimensions(item) for item in pool], [], "source")
        source["images"] = image_pool_legacy_views(source["image_pool"], SOURCE_COMPAT_IMAGE_ORIGINS)["images"]
        normalized["source"] = source
    return normalize_product_fields(normalized)


def _source_pool_items(prod: dict[str, Any]) -> list[dict[str, Any]]:
    source = prod.get("source") if isinstance(prod.get("source"), dict) else {}
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    return normalize_image_pool(pool, [], "source")


def _source_only_pool_items(prod: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = {"source", "local_upload", "extension"}
    return [item for item in _source_pool_items(prod) if str(item.get("origin") or "").strip() in allowed]


def current_image_pool(prod: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = _source_pool_items(prod)
    generated_files = image_files(CHATGPT_DIR, recursive=True)
    existing_keys = {
        (str(item.get("path") or "") or str(item.get("url") or "") or str(item.get("preview_url") or ""))
        for item in normalized
    }
    for index, file_item in enumerate(generated_files):
        key = str(file_item.get("path") or file_item.get("url") or "")
        if key and key in existing_keys:
            continue
        normalized.append(
            {
                "id": f"gen_{len(normalized) + 1}",
                "path": str(file_item.get("path") or ""),
                "url": str(file_item.get("url") or ""),
                "preview_url": str(file_item.get("url") or file_item.get("path") or ""),
                "origin": "ai_generated",
                "usage": "scene",
                "platforms": list(PLATFORMS),
                "is_main": False,
                "selected": False,
                "order": len(normalized),
                "status": "ready",
                "note": "generated file sync",
            }
        )
    return [_pool_display_item(enrich_image_pool_item_dimensions(item)) for item in normalize_image_pool(normalized, [], "source")]


def current_source_images(prod: dict[str, Any]) -> list[dict[str, str]]:
    pool = [_pool_display_item(item) for item in _source_only_pool_items(prod)]
    return pool or image_files(SOURCE_DIR)


def image_pool_refs_for_platform(prod: dict[str, Any], platform: str) -> list[str]:
    platform = str(platform or "").strip().lower()
    pool = _source_pool_items(prod)
    if not pool:
        return []
    if platform not in {"mercadolibre", "wildberries", "ozon"}:
        return [str(item.get("url") or item.get("path") or item.get("preview_url") or "").strip() for item in pool if str(item.get("url") or item.get("path") or item.get("preview_url") or "").strip()]
    platform_items = [
        item
        for item in pool
        if platform in [str(value).strip().lower() for value in (item.get("platforms") or [])]
        and str(item.get("status") or "").strip().lower() != "empty"
    ]
    selected_items = [item for item in platform_items if bool(item.get("selected"))]
    items = selected_items or platform_items
    items = sorted(items, key=lambda item: (0 if item.get("is_main") else 1, int(item.get("order") or 0)))
    refs = [str(item.get("url") or item.get("path") or item.get("preview_url") or "").strip() for item in items]
    return [ref for ref in refs if ref]


def sync_generated_images_into_pool(product: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else default_source()
    pool = _source_pool_items(normalized)
    existing_keys = {
        str(item.get("path") or item.get("url") or item.get("preview_url") or item.get("id") or "").strip()
        for item in pool
        if str(item.get("path") or item.get("url") or item.get("preview_url") or item.get("id") or "").strip()
    }
    for file_item in image_files(CHATGPT_DIR, recursive=True):
        key = str(file_item.get("path") or file_item.get("url") or "").strip()
        if not key or key in existing_keys:
            continue
        pool.append(
            enrich_image_pool_item_dimensions(normalize_image_pool_item(
                {
                    "id": f"gen_{len(pool) + 1}",
                    "path": str(file_item.get("path") or ""),
                    "url": str(file_item.get("url") or ""),
                    "preview_url": str(file_item.get("url") or file_item.get("path") or ""),
                    "origin": "ai_generated",
                    "usage": "scene",
                    "platforms": list(PLATFORMS),
                    "is_main": False,
                    "selected": False,
                    "order": len(pool),
                    "status": "ready",
                    "note": "generated file sync",
                },
                order=len(pool),
                origin_hint="ai_generated",
            ))
        )
        existing_keys.add(key)
    source["image_pool"] = normalize_image_pool(pool, [], "source")
    source["images"] = image_pool_legacy_views(source["image_pool"], SOURCE_COMPAT_IMAGE_ORIGINS)["images"]
    normalized["source"] = source
    return sync_draft_images_from_pool(normalized)


def _uploaded_image_path(filename: str, suffix: str) -> Path:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename or "image").stem).strip("._") or "image"
    safe_suffix = suffix if suffix.startswith(".") else f".{suffix.lstrip('.')}" if suffix else ".png"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    rand = os.urandom(3).hex()
    return UPLOAD_DIR / f"{stamp}_{safe_name}_{rand}{safe_suffix}"


def _decode_data_url(data_url: str) -> tuple[bytes, str]:
    raw = str(data_url or "").strip()
    if not raw:
        return b"", ".png"
    if raw.startswith("data:") and "," in raw:
        header, body = raw.split(",", 1)
        match = re.match(r"data:([^;]+);base64", header, flags=re.I)
        mime = (match.group(1) if match else "image/png").lower()
        suffix = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }.get(mime, ".png")
        return base64.b64decode(body), suffix
    try:
        return base64.b64decode(raw), ".png"
    except Exception:
        return b"", ".png"


def _image_pool_item_from_path(path: Path, origin: str, usage: str, platforms: list[str], note: str, is_main: bool = False, selected: bool = False) -> dict[str, Any]:
    return enrich_image_pool_item_dimensions(normalize_image_pool_item(
        {
            "id": path.stem,
            "path": str(path),
            "url": file_url(path),
            "preview_url": file_url(path),
            "origin": origin,
            "usage": usage,
            "platforms": platforms or list(PLATFORMS),
            "is_main": is_main,
            "selected": selected,
            "order": 0,
            "status": "ready",
            "note": note,
        },
        order=0,
        origin_hint=origin,
    ))


def append_images_to_product_pool(product: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else default_source()
    existing = _source_pool_items(normalized)
    existing_keys = {
        str(item.get("path") or item.get("url") or item.get("preview_url") or item.get("id") or "").strip()
        for item in existing
        if str(item.get("path") or item.get("url") or item.get("preview_url") or item.get("id") or "").strip()
    }
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("path") or item.get("url") or item.get("preview_url") or item.get("id") or "").strip()
        if not key or key in existing_keys:
            continue
        existing.append(enrich_image_pool_item_dimensions(normalize_image_pool_item(item, order=len(existing), origin_hint=str(item.get("origin") or "source"))))
        existing_keys.add(key)
    source["image_pool"] = normalize_image_pool(existing, [], "source")
    source["images"] = image_pool_legacy_views(source["image_pool"], SOURCE_COMPAT_IMAGE_ORIGINS)["images"]
    normalized["source"] = source
    return sync_draft_images_from_pool(normalized)


def sync_draft_images_from_pool(product: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    for platform in PLATFORMS:
        draft = normalized.setdefault("drafts", {}).setdefault(platform, default_draft(platform))
        if isinstance(draft, dict):
            refs = image_pool_refs_for_platform(normalized, platform)
            if refs:
                draft["images"] = refs
    return sync_product_workflow_statuses(normalized)


def save_image_pool_for_product(product_id: str, image_pool: list[dict[str, Any]]) -> dict[str, Any]:
    product = load_product_from_index(product_id, "")
    if not product:
        return {"ok": False, "error": "商品不存在", "product_id": product_id}
    normalized = normalize_product_fields(product)
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else default_source()
    pool = normalize_image_pool(image_pool if isinstance(image_pool, list) else [], [], "source")
    source["image_pool"] = [enrich_image_pool_item_dimensions(item) for item in pool]
    source["images"] = image_pool_legacy_views(source["image_pool"], SOURCE_COMPAT_IMAGE_ORIGINS)["images"]
    normalized["source"] = source
    saved = save_product(sync_draft_images_from_pool(normalized))
    return {
        "ok": True,
        "product": saved,
        "imagePool": current_image_pool(saved),
        "productsIndex": load_products_index(),
    }


def apply_service_image_pool(product: dict[str, Any], image_pool: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else default_source()
    source["image_pool"] = image_service.normalize_pool(image_pool if isinstance(image_pool, list) else [], APP_DIR)
    source["images"] = image_pool_legacy_views(source["image_pool"], SOURCE_COMPAT_IMAGE_ORIGINS)["images"]
    normalized["source"] = source
    return sync_draft_images_from_pool(normalized)


def current_generated_images() -> list[dict[str, str]]:
    return image_files(CHATGPT_DIR, recursive=True)


def current_collect_debug_files() -> list[dict[str, str]]:
    return image_files(COLLECT_DEBUG_DIR, recursive=True)


def collect_time_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def normalize_collect_mode(mode: str, url: str = "") -> str:
    value = str(mode or "").strip().lower()
    if value in {"browser", "http", "manual"}:
        return value
    if value in {"playwright", "browser-session", "browser_session"}:
        return "browser"
    if value in {"fetch", "request", "requests"}:
        return "http"
    if "amazon." in str(url).lower():
        return "http"
    return "browser"


def detect_source_platform(url: str) -> str:
    lowered = str(url or "").lower()
    if "amazon." in lowered:
        return "amazon"
    if "1688.com" in lowered:
        return "1688"
    if "wildberries" in lowered:
        return "wildberries"
    if "ozon" in lowered:
        return "ozon"
    if "alibaba." in lowered:
        return "alibaba"
    return "unknown"


def collect_debug_path(kind: str, suffix: str) -> Path:
    COLLECT_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    rand = os.urandom(4).hex()
    safe_kind = re.sub(r"[^A-Za-z0-9._-]+", "_", kind or "collect").strip("_") or "collect"
    safe_suffix = suffix if suffix.startswith(".") else f".{suffix.lstrip('.')}"
    return COLLECT_DEBUG_DIR / f"{stamp}_{safe_kind}_{rand}{safe_suffix}"


def write_collect_debug_html(url: str, html: str, platform: str = "collect") -> str:
    path = collect_debug_path(platform, ".html")
    path.write_text(
        "\n".join(
            [
                "<!doctype html>",
                "<html><head><meta charset=\"utf-8\"><title>collect snapshot</title></head><body>",
                f"<pre>URL: {url}</pre>",
                "<hr>",
                html,
                "</body></html>",
            ]
        ),
        encoding="utf-8",
        errors="ignore",
    )
    return str(path)


def write_collect_debug_text(platform: str, text: str, suffix: str = ".txt") -> str:
    path = collect_debug_path(platform, suffix)
    path.write_text(text, encoding="utf-8", errors="ignore")
    return str(path)


def save_collect_snapshot_artifacts(
    platform: str,
    url: str,
    html: str = "",
    screenshot_base64: str = "",
    text: str = "",
) -> dict[str, str]:
    artifacts: dict[str, str] = {"html_snapshot_path": "", "screenshot_path": ""}
    if html:
        artifacts["html_snapshot_path"] = write_collect_debug_html(url, html, platform)
    elif text:
        artifacts["html_snapshot_path"] = write_collect_debug_text(platform, text, ".html.txt")
    if screenshot_base64:
        path = collect_debug_path(platform, ".png")
        try:
            path.write_bytes(base64.b64decode(screenshot_base64))
            artifacts["screenshot_path"] = str(path)
        except Exception:
            artifacts["screenshot_path"] = ""
    return artifacts


def collect_debug_file_url(path: str) -> str:
    if not path:
        return ""
    return file_url(Path(path))


def is_1688_login_page(url: str, html: str, text: str, title: str) -> bool:
    lowered = "\n".join([str(url or ""), html or "", text or "", title or ""]).lower()
    return "login.1688.com" in lowered or "请登录" in lowered or "登录" in lowered or "帐号密码登录" in lowered


def is_1688_security_check_page(html: str, text: str) -> bool:
    lowered = "\n".join([html or "", text or ""]).lower()
    return any(marker.lower() in lowered for marker in VERIFY_MARKERS) or "滑块" in lowered or "安全验证" in lowered


def is_amazon_robot_check_page(url: str, html: str, text: str, title: str) -> bool:
    lowered = "\n".join([str(url or ""), html or "", text or "", title or ""]).lower()
    return any(marker in lowered for marker in AMAZON_VERIFY_MARKERS) or "/errors/validatecaptcha" in lowered


def is_amazon_region_blocked_page(html: str, text: str) -> bool:
    lowered = "\n".join([html or "", text or ""]).lower()
    region_markers = (
        "cannot be shipped to your selected location",
        "not deliverable",
        "currently unavailable",
        "this item cannot be shipped",
        "not available in your region",
    )
    return any(marker in lowered for marker in region_markers)


def snapshot_field_flags(source: dict[str, Any]) -> dict[str, Any]:
    dimensions = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    return {
        "images_found_count": len(normalize_list(source.get("images"))),
        "title_found": bool(str(source.get("title") or "").strip()),
        "price_found": bool(str(source.get("price") or "").strip()),
        "bullets_found_count": len(normalize_list(source.get("bullets"))),
        "sku_found_count": len(normalize_list(source.get("skus"))),
        "dimensions_found": any(str(dimensions.get(part) or "").strip() for part in ["length_cm", "width_cm", "height_cm"]),
        "weight_found": bool(str(source.get("weight_kg") or "").strip()),
    }


def collect_field_summary(source: dict[str, Any]) -> dict[str, list[str]]:
    flags = snapshot_field_flags(source)
    collected: list[str] = []
    missing: list[str] = []
    checks = {
        "title": flags["title_found"],
        "price": flags["price_found"],
        "images": flags["images_found_count"] > 0,
        "bullets": flags["bullets_found_count"] > 0,
        "skus": flags["sku_found_count"] > 0,
        "dimensions": flags["dimensions_found"],
        "weight": flags["weight_found"],
        "description": bool(str(source.get("description") or "").strip()),
        "brand": bool(str(source.get("brand") or "").strip()),
    }
    for field, ok in checks.items():
        (collected if ok else missing).append(field)
    return {"collected_fields": collected, "missing_fields": missing}


def collect_next_action(platform: str, error_code: str) -> str:
    platform = (platform or "").lower()
    code = (error_code or "").upper()
    if not code:
        return "采集已完成，可进入商品库继续 AI 文案、生图和编辑。"
    if platform == "1688":
        if any(key in code for key in ["LOGIN", "CAPTCHA", "SECURITY", "SLIDER", "REMOTE_DEBUGGING"]):
            return "1688 触发验证，请手动打开浏览器完成验证，或使用手动导入。"
        return "请尝试浏览器会话采集；如果仍失败，保存商品详情页 HTML 后导入，或手动补充缺失字段。"
    if platform == "amazon":
        if any(key in code for key in ["ROBOT", "REGION", "LOGIN", "FORBIDDEN"]):
            return "请使用已登录且地区正确的浏览器会话重试；如果仍被拦截，请使用 HTML 导入 / 手动补充。"
        return "请尝试浏览器登录后采集；如果选择器失败，使用 HTML 导入或手动补充。"
    return "无法稳定自动解析该来源，请使用 HTML 导入或手动补充后继续后续流程。"


def finalize_collect_diagnostics(diagnostics: dict[str, Any], source: dict[str, Any], platform: str) -> dict[str, Any]:
    diagnostics.update(snapshot_field_flags(source))
    diagnostics.update(collect_field_summary(source))
    diagnostics["next_action"] = collect_next_action(platform, str(diagnostics.get("error_code") or ""))
    diagnostics["checked_at"] = collect_time_iso()
    return diagnostics


def collect_error_code(platform: str, mode: str, reason: str = "") -> str:
    platform = (platform or "").lower()
    reason = (reason or "").upper()
    if platform == "amazon":
        mapping = {
            "ROBOT": "AMAZON_ROBOT_CHECK",
            "REGION": "AMAZON_REGION_BLOCKED",
            "NO_IMAGES": "AMAZON_IMAGE_NOT_FOUND",
            "NO_TITLE": "AMAZON_TITLE_NOT_FOUND",
            "NO_BULLETS": "AMAZON_NO_BULLETS_FOUND",
            "NO_DIMENSIONS": "AMAZON_DIMENSIONS_NOT_FOUND",
            "NO_WEIGHT": "AMAZON_WEIGHT_NOT_FOUND",
            "SELECTOR": "AMAZON_SELECTOR_FAILED",
            "LOGIN": "AMAZON_LOGIN_REQUIRED",
            "NETWORK": "NETWORK_BLOCKED",
            "FORBIDDEN": "HTTP_FORBIDDEN",
        }
        return mapping.get(reason, "AMAZON_SELECTOR_FAILED")
    if platform == "1688":
        mapping = {
            "LOGIN": "1688_LOGIN_REQUIRED",
            "SECURITY": "1688_SECURITY_CHECK",
            "CAPTCHA": "1688_CAPTCHA_REQUIRED",
            "SLIDER": "1688_SLIDER_REQUIRED",
            "NO_IMAGES": "1688_IMAGE_NOT_FOUND",
            "NO_TITLE": "1688_TITLE_NOT_FOUND",
            "NO_DIMENSIONS": "1688_DIMENSIONS_NOT_FOUND",
            "SELECTOR": "1688_SELECTOR_FAILED",
            "PROFILE": "1688_BROWSER_PROFILE_NOT_FOUND",
            "REMOTE": "1688_REMOTE_DEBUGGING_NOT_CONNECTED",
            "NETWORK": "NETWORK_BLOCKED",
        }
        return mapping.get(reason, "1688_SELECTOR_FAILED")
    return "COLLECT_FAILED"


def current_browser_profile_name(platform: str) -> str:
    platform = (platform or "").lower()
    if platform == "amazon":
        return "amazon"
    if platform == "1688":
        return "1688"
    return platform or "collect"


def collect_image_origin(platform: str, mode: str = "") -> str:
    platform = (platform or "").strip().lower()
    mode = (mode or "").strip().lower()
    if mode in {"extension", "manual", "html_import", "browser"}:
        return mode
    if platform in {"amazon", "1688"}:
        return platform
    return "source"


def normalize_collect_source_images(source_updates: dict[str, Any], platform: str, mode: str = "", claim_platforms: list[str] | None = None) -> dict[str, Any]:
    source = deepcopy(source_updates if isinstance(source_updates, dict) else {})
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    refs: list[Any] = list(pool)
    if not refs:
        refs.extend(normalize_list(source.get("images")))
    if str(platform or "").strip().lower() == "1688":
        refs = refs[:5]
    origin = collect_image_origin(platform, mode)
    platforms = normalize_platforms(claim_platforms) or ["mercadolibre"]
    normalized_pool = image_service.materialize_image_values(
        APP_DIR,
        refs,
        str(source.get("source_url") or source.get("title") or "collected"),
        platforms,
        origin,
    )
    if normalized_pool:
        source["image_pool"] = normalize_image_pool(normalized_pool, [], origin)
        source["images"] = image_pool_legacy_views(source["image_pool"], SOURCE_COMPAT_IMAGE_ORIGINS)["images"]
        if not source.get("images"):
            fallback_refs: list[str] = []
            for raw in refs:
                if isinstance(raw, dict):
                    value = str(raw.get("url") or raw.get("preview_url") or raw.get("path") or "").strip()
                else:
                    value = str(raw or "").strip()
                if value:
                    fallback_refs.append(value)
            source["images"] = fallback_refs[: len(source["image_pool"])]
    return source


def parse_collect_urls(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.split(r"[\r\n,，\s]+", str(value or ""))
    urls: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        url = str(raw or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def apply_claimed_platform_drafts(product: dict[str, Any], claim_platforms: list[str] | None = None) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else {}
    platforms = normalize_platforms(claim_platforms) or ["mercadolibre"]
    dims = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    image_refs = image_pool_refs_for_platform(normalized, "mercadolibre") or productImages_from_source(normalized)
    placeholder_titles = {"", "-", "unknown", "draft title", "untitled", "未命名"}

    def use_existing(value: Any) -> bool:
        return str(value or "").strip().lower() not in placeholder_titles

    for platform in platforms:
        draft = normalized.setdefault("drafts", {}).setdefault(platform, default_draft(platform))
        if not isinstance(draft, dict):
            continue
        draft["enabled"] = True
        draft["title"] = draft.get("title") if use_existing(draft.get("title")) else source.get("title") or normalized.get("name") or ""
        draft["description"] = draft.get("description") if use_existing(draft.get("description")) else source.get("description") or ""
        draft["bullets"] = draft.get("bullets") or source.get("bullets") or []
        draft["images"] = draft.get("images") if draft.get("images") else image_refs
        draft["brand"] = draft.get("brand") or source.get("brand") or "Generic"
        draft["model"] = draft.get("model") or normalized.get("model") or "General"
        draft["status"] = "claimed"
        draft["package_dimensions"] = {
            **(draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}),
            "length_cm": (draft.get("package_dimensions") or {}).get("length_cm") or dims.get("length_cm") or "",
            "width_cm": (draft.get("package_dimensions") or {}).get("width_cm") or dims.get("width_cm") or "",
            "height_cm": (draft.get("package_dimensions") or {}).get("height_cm") or dims.get("height_cm") or "",
            "weight_kg": (draft.get("package_dimensions") or {}).get("weight_kg") or source.get("weight_kg") or "",
        }
    return sync_product_workflow_statuses(normalized)


def claim_products_to_platforms(product_ids: list[str], platforms: list[str]) -> dict[str, Any]:
    targets = normalize_platforms(platforms) or ["mercadolibre"]
    targets = [platform for platform in targets if platform in PLATFORMS]
    if not targets:
        return {"ok": False, "claimed_count": 0, "items": [], "error": "没有可认领的平台"}
    items: list[dict[str, Any]] = []
    for product_id in [str(item or "").strip() for item in product_ids if str(item or "").strip()]:
        product = load_product_from_index(product_id, "")
        if not product:
            items.append({"product_id": product_id, "ok": False, "error": "商品不存在"})
            continue
        product = apply_claimed_platform_drafts(product, targets)
        product = save_product(product)
        items.append(
            {
                "product_id": product.get("product_id") or product_id,
                "ok": True,
                "platforms": targets,
                "draft_statuses": (product.get("workflow_statuses") or {}),
            }
        )
    return {
        "ok": True,
        "claimed_count": sum(1 for item in items if item.get("ok")),
        "items": items,
        "productsIndex": load_products_index(),
    }


def productImages_from_source(product: dict[str, Any]) -> list[str]:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    refs = [str(item.get("url") or item.get("path") or item.get("preview_url") or "").strip() for item in pool if isinstance(item, dict)]
    return [item for item in refs if item] or normalize_list(source.get("images"))


def page_snapshot_from_html(url: str, html: str, text: str = "", title: str = "", image_urls: list[str] | None = None) -> dict[str, Any]:
    return {
        "url": url,
        "html": html,
        "text": text or legacy.html_to_text(html),
        "title": title or legacy.extract_page_title(html),
        "image_urls": image_urls or legacy.extract_product_image_urls(html, url, limit=20),
    }


def append_publish_log(entry: dict[str, Any]) -> None:
    logs = read_json(PUBLISH_LOG_PATH, [])
    if not isinstance(logs, list):
        logs = []
    logs.insert(0, entry)
    write_json(PUBLISH_LOG_PATH, logs[:200])


def load_publish_logs() -> list[dict[str, Any]]:
    logs = read_json(PUBLISH_LOG_PATH, [])
    return logs if isinstance(logs, list) else []


def publish_bus_terminal_status(status: str) -> str:
    value = str(status or "").strip().lower()
    if value == "success":
        return "published"
    if value in {"failed", "not_ready", "ready_for_real_publish", "skipped"}:
        return value
    return ""


def publish_bus_log_exists(job_id: str, platform: str) -> bool:
    for item in load_publish_logs():
        if str(item.get("job_id") or "") == str(job_id or "") and str(item.get("platform") or "") == str(platform or ""):
            return True
    return False


def apply_publish_bus_result_to_product(product: dict[str, Any], job_state: dict[str, Any], platform: str, item: dict[str, Any]) -> dict[str, Any]:
    product = normalize_product_fields(product or {})
    terminal_status = publish_bus_terminal_status(str(item.get("status") or ""))
    if not terminal_status:
        return product
    drafts = product.setdefault("drafts", {})
    draft = drafts.get(platform) if isinstance(drafts.get(platform), dict) else default_draft(platform)
    draft["publish_status"] = terminal_status
    if terminal_status == "published":
        draft["status"] = "published"
        draft["validation_errors"] = []
    elif str(item.get("error") or ""):
        draft["validation_errors"] = [
            precheck_item(
                "PUBLISH_BUS_FAILED",
                "publish",
                str(item.get("error") or ""),
                "error",
                "按字段提示修复后重试",
            )
        ]
    draft["last_publish_task"] = {
        "job_id": str(job_state.get("job_id") or ""),
        "status": terminal_status,
        "platform_status": str(item.get("status") or ""),
        "stage": str(item.get("stage") or ""),
        "error": str(item.get("error") or ""),
        "attempts": item.get("attempts", 0),
        "updated_at": str(item.get("updated_at") or job_state.get("updated_at") or collect_time_iso()),
    }
    drafts[platform] = draft
    product["drafts"] = drafts
    return product


def append_publish_bus_terminal_log(product: dict[str, Any], job_state: dict[str, Any], platform: str, item: dict[str, Any]) -> None:
    job_id = str(job_state.get("job_id") or "")
    if publish_bus_log_exists(job_id, platform):
        return
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    payload = {
        "job_id": job_id,
        "platform": platform,
        "product_id": str(product.get("product_id") or ""),
        "stage": item.get("stage") or "",
        "attempts": item.get("attempts", 0),
    }
    payload_path, response_path = _write_publish_artifacts(f"publish-bus-{platform}", payload, result or item)
    error_map = result.get("error_map") if isinstance(result.get("error_map"), dict) else {}
    field_errors = error_map.get("field_errors") if isinstance(error_map.get("field_errors"), dict) else {}
    terminal_status = publish_bus_terminal_status(str(item.get("status") or ""))
    append_publish_log(
        {
            "job_id": job_id,
            "product_id": str(product.get("product_id") or _product_id_for_log(product, platform)),
            "platform": platform,
            "draft_id": str(_draft_for_platform(product, platform).get("sku") or ""),
            "status": terminal_status or str(item.get("status") or ""),
            "started_at": str(item.get("created_at") or job_state.get("created_at") or ""),
            "finished_at": str(item.get("updated_at") or job_state.get("updated_at") or collect_time_iso()),
            "request_payload_path": payload_path,
            "response_body_path": response_path,
            "error_code": str(result.get("error_code") or result.get("status") or item.get("status") or ""),
            "error_message": str(item.get("error") or result.get("error") or ""),
            "field_errors": field_errors,
            "next_action": "按字段提示修复后重试" if terminal_status in {"failed", "not_ready"} else "",
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "shop": platform,
            "sku": str(_draft_for_platform(product, platform).get("sku") or ""),
            "error": str(item.get("error") or result.get("error") or ""),
            "image": normalize_list(product.get("source_image_urls"))[:1],
        }
    )


def persist_publish_bus_terminal_results(job_state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(job_state, dict):
        return {}
    product = job_state.get("product") if isinstance(job_state.get("product"), dict) else {}
    product_id = str(product.get("product_id") or "").strip()
    if product_id:
        loaded = load_product_from_index(product_id, "")
        if loaded:
            product = loaded
    changed = False
    platforms = job_state.get("platforms") if isinstance(job_state.get("platforms"), dict) else {}
    for platform, item in platforms.items():
        if not isinstance(item, dict):
            continue
        terminal_status = publish_bus_terminal_status(str(item.get("status") or ""))
        if not terminal_status:
            continue
        product = apply_publish_bus_result_to_product(product, job_state, str(platform), item)
        append_publish_bus_terminal_log(product, job_state, str(platform), item)
        changed = True
    if changed:
        saved = save_product(product)
        job_state["product"] = saved
    return job_state


def pick_web_port(preferred_port: int, attempts: int = 20) -> int:
    import socket

    for port in range(preferred_port, preferred_port + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"没有可用端口，已尝试从 {preferred_port} 开始的 {attempts} 个端口")


def file_url(path: Path) -> str:
    return f"/file?path={urllib.parse.quote(str(path), safe='')}"


def parse_cookie_header(cookie: str, url: str) -> list[dict[str, str]]:
    parsed = urllib.parse.urlparse(url)
    domain = parsed.hostname or ""
    cookies: list[dict[str, str]] = []
    for part in cookie.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookies.append({"name": name, "value": value, "domain": domain, "path": "/"})
    return cookies


class CdpWebSocket:
    def __init__(self, websocket_url: str) -> None:
        parsed = urllib.parse.urlparse(websocket_url)
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = self.sock.recv(4096)
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError("杩炴帴 Chrome DevTools WebSocket 澶辫触")
        self.next_id = 1

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass

    def _send_frame(self, payload: bytes) -> None:
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _recv_exact(self, length: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            chunk = self.sock.recv(length - len(chunks))
            if not chunk:
                raise RuntimeError("Chrome DevTools 连接已关闭")
            chunks.extend(chunk)
        return bytes(chunks)

    def _recv_frame(self) -> dict[str, Any]:
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            masked = bool(second & 0x80)
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length) if length else b""
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 1:
                return json.loads(payload.decode("utf-8"))
            if opcode == 8:
                raise RuntimeError("Chrome DevTools WebSocket 已关闭")
            if opcode == 9:
                continue

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 20) -> dict[str, Any]:
        message_id = self.next_id
        self.next_id += 1
        self._send_frame(json.dumps({"id": message_id, "method": method, "params": params or {}}).encode("utf-8"))
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self._recv_frame()
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return message.get("result", {})
        raise RuntimeError(f"{method} 超时")


def find_chrome_path() -> str:
    env_candidates = [
        os.environ.get("ERP_CHROME_PATH", ""),
        os.environ.get("CHROME_PATH", ""),
        os.environ.get("BROWSER_PATH", ""),
    ]
    candidates = [Path(value).expanduser() for value in env_candidates if value.strip()]
    command_candidates: list[str] = []

    if sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome",
                Path("/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"),
                Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
                Path.home() / "Applications" / "Chromium.app" / "Contents" / "MacOS" / "Chromium",
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                Path.home() / "Applications" / "Microsoft Edge.app" / "Contents" / "MacOS" / "Microsoft Edge",
            ]
        )
        command_candidates = ["google-chrome", "chromium", "chromium-browser", "microsoft-edge", "msedge"]
    elif os.name == "nt":
        candidates.extend(
            [
                Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ]
        )
        command_candidates = ["chrome", "chrome.exe", "msedge", "msedge.exe"]
    else:
        candidates.extend(
            [
                Path("/usr/bin/google-chrome"),
                Path("/usr/local/bin/google-chrome"),
                Path("/usr/bin/chromium"),
                Path("/usr/bin/chromium-browser"),
                Path("/usr/bin/microsoft-edge"),
                Path("/usr/bin/msedge"),
            ]
        )
        command_candidates = ["google-chrome", "chromium", "chromium-browser", "microsoft-edge", "msedge"]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    for command in command_candidates:
        found = shutil.which(command)
        if found:
            return found
    raise RuntimeError("没有找到 Chrome 或 Edge 浏览器；可设置 ERP_CHROME_PATH / CHROME_PATH 指向浏览器可执行文件。")


def find_named_browser_path(browser: str) -> str | None:
    name = str(browser or "").strip().lower()
    if name == "chrome":
        candidates = [
            Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
    elif name == "edge":
        candidates = [
            Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        ]
    else:
        return None
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def open_auth_link_in_browser(url: str, browser: str = "default") -> dict[str, Any]:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("授权链接无效，请先生成完整授权链接")
    browser_name = str(browser or "default").strip().lower()
    if browser_name in {"chrome", "edge"}:
        browser_path = find_named_browser_path(browser_name)
        if not browser_path:
            return {"ok": False, "opened": False, "browser": browser_name, "error": f"未检测到 {browser_name}，请复制链接到已登录店铺的浏览器手动打开"}
        subprocess.Popen([browser_path, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "opened": True, "browser": browser_name, "path": browser_path}
    webbrowser.open(url)
    return {"ok": True, "opened": True, "browser": "default"}


def browser_debug_commands(port: int = BROWSER_DEBUG_PORT) -> dict[str, str]:
    profile = str(BROWSER_DEBUG_PROFILE_DIR)
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    powershell_command = f'Start-Process "{chrome}" -ArgumentList \'--remote-debugging-port={port} --user-data-dir="{profile}"\''
    cmd_command = f'start chrome --remote-debugging-port={port} --user-data-dir="{profile}"'
    return {
        "profile_dir": profile,
        "powershell_command": powershell_command,
        "cmd_command": cmd_command,
        "start_command": powershell_command,
        "full_path_command": f'"{chrome}" --remote-debugging-port={port} --user-data-dir="{profile}"',
    }


def browser_debug_next_action() -> str:
    return "先关闭所有 Chrome；在 PowerShell 执行推荐命令；在新打开的 Chrome 里登录 1688 / Amazon；打开商品页后再点测试连接。"


def http_json(url: str, access_token: str | None = None) -> dict[str, Any] | list[Any]:
    return category_cache_runtime.http_json(url, access_token)


def _ml_attr_required(attr: dict[str, Any]) -> bool:
    return category_cache_runtime.ml_attr_required(attr)


def _normalize_ml_attribute(attr: dict[str, Any]) -> dict[str, Any]:
    return category_cache_runtime.normalize_ml_attribute(attr)


def _ml_attributes_for_category(category_id: str, access_token: str | None = None) -> dict[str, list[dict[str, Any]]]:
    return category_cache_runtime.ml_attributes_for_category(category_id, access_token=access_token, http_client=http_json)


def _ml_category_record(detail: dict[str, Any], site: str, attrs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return category_cache_runtime.ml_category_record(detail, site, attrs)


def build_mercadolibre_category_cache(site: str = "MLM", max_categories: int = 500, access_token: str | None = None) -> dict[str, Any]:
    return category_cache_runtime.build_mercadolibre_category_cache(
        site=site,
        max_categories=max_categories,
        access_token=access_token,
        http_client=http_json,
    )


def _refresh_mercadolibre_access_token_for_category_cache(config: dict[str, Any]) -> str:
    ml = config.setdefault("mercadolibre", {})
    app_id = str(ml.get("app_id") or ml.get("client_id") or "").strip()
    app_secret = _mercadolibre_app_secret(ml)
    refresh_token = str(ml.get("refresh_token") or "").strip()
    if not app_id or not app_secret or not refresh_token:
        return ""
    refreshed = publisher.refresh_mercadolibre_token(app_id, app_secret, refresh_token)
    token = str(refreshed.get("access_token") or "").strip()
    if not token:
        return ""
    ml["access_token"] = token
    ml["refresh_token"] = str(refreshed.get("refresh_token") or refresh_token).strip()
    ml.update(_store_auth_result_fields("mercadolibre", "测试成功", ml.get("shop_name") or token))
    ml["auth_error_code"] = ""
    ml["auth_error_message"] = ""
    save_store_config(config)
    return token


def refresh_official_category_cache(
    platform: str,
    site: str = "",
    max_categories: int = 500,
    progress_callback: category_cache_runtime.ProgressCallback | None = None,
) -> dict[str, Any]:
    config = load_store_config()
    ml = config.get("mercadolibre", {}) if isinstance(config.get("mercadolibre"), dict) else {}
    resolved_site = str(site or ml.get("site_id") or "MLM").strip().upper() or "MLM"
    result = category_cache_runtime.refresh_official_category_cache(
        APP_DIR,
        platform,
        config,
        site=resolved_site,
        max_categories=max_categories,
        http_client=http_json,
        progress_callback=progress_callback,
    )
    if (
        str(platform or "").strip().lower() == "mercadolibre"
        and not result.get("ok")
        and result.get("error_code") == "MERCADOLIBRE_CATEGORY_AUTH_REQUIRED"
        and str(ml.get("refresh_token") or "").strip()
    ):
        try:
            token = _refresh_mercadolibre_access_token_for_category_cache(config)
        except Exception as exc:
            token = ""
            result["refresh_error"] = str(exc)
        if token:
            if progress_callback:
                progress_callback({"stage": "token_refreshed", "site": resolved_site, "max_categories": max_categories})
            result = category_cache_runtime.refresh_official_category_cache(
                APP_DIR,
                platform,
                config,
                site=resolved_site,
                max_categories=max_categories,
                http_client=http_json,
                progress_callback=progress_callback,
            )
            if result.get("ok"):
                result["token_refreshed"] = True
    return result


def _category_refresh_job_update(job_id: str, updates: dict[str, Any]) -> None:
    with CATEGORY_REFRESH_LOCK:
        job = CATEGORY_REFRESH_JOBS.setdefault(job_id, {})
        job.update(updates)
        job["updated_at"] = collect_time_iso()


def _category_refresh_progress_percent(job: dict[str, Any]) -> int:
    status = str(job.get("status") or "")
    if status == "completed":
        return 100
    if status == "failed":
        return int(job.get("progress") or 100)
    max_categories = max(1, int(job.get("max_categories") or 500))
    visited = max(0, int(job.get("visited") or 0))
    return max(3, min(98, int(visited / max_categories * 100)))


def _category_refresh_job_snapshot(job_id: str) -> dict[str, Any]:
    with CATEGORY_REFRESH_LOCK:
        job = deepcopy(CATEGORY_REFRESH_JOBS.get(job_id) or {})
    if not job:
        raise KeyError(job_id)
    job["progress"] = _category_refresh_progress_percent(job)
    return job


def start_category_cache_refresh_job(platform: str, site: str = "", max_categories: int = 500) -> dict[str, Any]:
    job_id = f"cat-{uuid.uuid4().hex[:12]}"
    platform = str(platform or "mercadolibre").strip().lower()
    max_categories = max(1, int(max_categories or 500))
    _category_refresh_job_update(
        job_id,
        {
            "ok": True,
            "job_id": job_id,
            "platform": platform,
            "site": str(site or "").strip().upper(),
            "max_categories": max_categories,
            "status": "queued",
            "stage": "queued",
            "visited": 0,
            "records": 0,
            "queued": 0,
            "errors_count": 0,
            "started_at": collect_time_iso(),
        },
    )

    def run() -> None:
        partial_records: list[dict[str, Any]] = []
        partial_imported = 0

        def import_partial_records(force: bool = False) -> None:
            nonlocal partial_records, partial_imported
            if not partial_records or (not force and len(partial_records) < 25):
                return
            with CATEGORY_REFRESH_LOCK:
                job_site = str(CATEGORY_REFRESH_JOBS.get(job_id, {}).get("site") or site or "").strip().upper()
            cache = {
                "platform": "mercadolibre",
                "site": job_site or "MLM",
                "updated_at": collect_time_iso(),
                "records": list(partial_records),
            }
            imported = erp_db.import_category_cache(APP_DIR, cache)
            partial_imported += imported
            partial_records = []
            _category_refresh_job_update(job_id, {"partial_imported": partial_imported})

        def on_progress(progress: dict[str, Any]) -> None:
            record = progress.get("record")
            if isinstance(record, dict):
                partial_records.append(record)
                import_partial_records()
            _category_refresh_job_update(
                job_id,
                {
                    "status": "running",
                    "stage": progress.get("stage") or "running",
                    "site": progress.get("site") or "",
                    "category_id": progress.get("category_id") or "",
                    "visited": int(progress.get("visited") or 0),
                    "records": int(progress.get("records") or 0),
                    "queued": int(progress.get("queued") or 0),
                    "max_categories": int(progress.get("max_categories") or max_categories),
                    "errors_count": int(progress.get("errors") or 0),
                },
            )

        try:
            _category_refresh_job_update(job_id, {"status": "running", "stage": "starting"})
            result = refresh_official_category_cache(platform, site=site, max_categories=max_categories, progress_callback=on_progress)
            import_partial_records(force=True)
            terminal_status = "completed" if result.get("ok") else "failed"
            cache_status = result.get("cache_status") if isinstance(result.get("cache_status"), dict) else {}
            _category_refresh_job_update(
                job_id,
                {
                    "status": terminal_status,
                    "stage": terminal_status,
                    "ok": bool(result.get("ok")),
                    "site": result.get("site") or "",
                    "imported": int(result.get("imported") or 0),
                    "visited": int(result.get("visited") or 0),
                    "records": int(cache_status.get("records") or result.get("imported") or 0),
                    "errors_count": len(result.get("errors") or []),
                    "error": result.get("error") or "",
                    "error_code": result.get("error_code") or "",
                    "next_action": result.get("next_action") or "",
                    "result": {key: value for key, value in result.items() if key != "cache"},
                    "finished_at": collect_time_iso(),
                },
            )
        except Exception as exc:
            import_partial_records(force=True)
            _category_refresh_job_update(
                job_id,
                {
                    "status": "failed",
                    "stage": "failed",
                    "ok": False,
                    "error": str(exc),
                    "finished_at": collect_time_iso(),
                },
            )

    thread = threading.Thread(target=run, name=f"category-refresh-{job_id}", daemon=True)
    thread.start()
    return _category_refresh_job_snapshot(job_id)


def get_category_cache_refresh_job(job_id: str) -> dict[str, Any]:
    return _category_refresh_job_snapshot(str(job_id or "").strip())


def wait_for_cdp(port: int, timeout: int = 15) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            http_json(f"http://127.0.0.1:{port}/json/version")
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Chrome 调试端口启动超时")


def normalize_browser_tab(page: dict[str, Any]) -> dict[str, Any]:
    url = str(page.get("url") or "")
    title = str(page.get("title") or "")
    return {
        "title": title,
        "url": url,
        "platform_detected": detect_source_platform(url) or detect_source_platform(title) or "unknown",
        "type": page.get("type") or "",
        "id": page.get("id") or "",
    }


def browser_debug_status(port: int = BROWSER_DEBUG_PORT, tabs_override: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    commands = browser_debug_commands(port)
    try:
        raw_tabs = tabs_override if tabs_override is not None else http_json(f"http://127.0.0.1:{port}/json")
        if not isinstance(raw_tabs, list):
            raw_tabs = []
        page_tabs = [tab for tab in raw_tabs if isinstance(tab, dict) and str(tab.get("type") or "page") == "page"]
        tabs = [normalize_browser_tab(tab) for tab in page_tabs]
        product_tabs = [tab for tab in tabs if tab["platform_detected"] in {"amazon", "1688"}]
        error_code = "" if product_tabs else "NO_PRODUCT_TAB_FOUND"
        return {
            "ok": True,
            "connected": True,
            "port": port,
            "browser": "chrome",
            "tabs_count": len(tabs),
            "current_tabs": tabs,
            "error_code": error_code,
            "error_message": "" if product_tabs else "已连接 Chrome，但未发现 Amazon / 1688 商品页标签。",
            "next_action": "在专用 Chrome 当前标签页打开 Amazon / 1688 商品详情页后点击从当前页面采集。" if error_code else "已连接，可从当前标签页采集。",
            **commands,
        }
    except FileNotFoundError as exc:
        return {"ok": True, "connected": False, "port": port, "browser": "chrome", "tabs_count": 0, "current_tabs": [], "error_code": "CHROME_NOT_FOUND", "error_message": str(exc), "next_action": f"请先确认 Chrome 已安装。{browser_debug_next_action()}", **commands}
    except Exception as exc:
        message = str(exc)
        code = "DEBUG_PORT_BLOCKED" if "10013" in message or "permission" in message.lower() else "REMOTE_DEBUGGING_NOT_CONNECTED"
        return {"ok": True, "connected": False, "port": port, "browser": "chrome", "tabs_count": 0, "current_tabs": [], "error_code": code, "error_message": message or "未连接 Chrome remote debugging。", "next_action": browser_debug_next_action(), **commands}


def choose_browser_tab(raw_tabs: list[dict[str, Any]], tab_url: str = "", product_url: str = "", platform_hint: str = "") -> dict[str, Any] | None:
    pages = [tab for tab in raw_tabs if isinstance(tab, dict) and str(tab.get("type") or "page") == "page"]
    tab_url = str(tab_url or "").strip()
    product_url = str(product_url or "").strip()
    platform_hint = str(platform_hint or "").strip().lower()
    if tab_url:
        for tab in pages:
            if tab_url in str(tab.get("url") or ""):
                return tab
    if product_url:
        for tab in pages:
            if product_url in str(tab.get("url") or "") or str(tab.get("url") or "") in product_url:
                return tab
    if platform_hint in {"amazon", "1688"}:
        for tab in pages:
            if detect_source_platform(str(tab.get("url") or "")) == platform_hint:
                return tab
    for tab in pages:
        if detect_source_platform(str(tab.get("url") or "")) in {"amazon", "1688"}:
            return tab
    return pages[0] if pages else None


def open_browser_debug_session(url: str, port: int, profile_name: str) -> None:
    chrome = find_chrome_path()
    profile = BROWSER_PROFILE_DIR if profile_name.startswith("1688") else APP_DIR / "browser_profile" / profile_name
    profile.mkdir(parents=True, exist_ok=True)
    try:
        http_json(f"http://127.0.0.1:{port}/json/version")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe='')}", timeout=5)
        except Exception:
            pass
    except Exception:
        subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--disable-popup-blocking",
                "--start-maximized",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_for_cdp(port)


def cdp_target_for_url(port: int, url: str) -> dict[str, Any]:
    pages = http_json(f"http://127.0.0.1:{port}/json/list")
    for page in pages:
        page_url = page.get("url", "")
        if page.get("type") == "page" and (url in page_url or "1688.com" in page_url):
            return page
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe='')}",
            timeout=5,
        ):
            pass
    except Exception:
        pass
    time.sleep(2)
    pages = http_json(f"http://127.0.0.1:{port}/json/list")
    for page in pages:
        page_url = page.get("url", "")
        if page.get("type") == "page" and (url in page_url or "1688.com" in page_url):
            return page
    for page in pages:
        if page.get("type") == "page":
            return page
    raise RuntimeError("没有找到可用的 Chrome 页面")


def fetch_page_html_with_browser_session(url: str, port: int | None = None) -> str | None:
    snapshot = fetch_page_snapshot_with_browser_session(url, port=port, profile_name=detect_source_platform(url) or "1688")
    if snapshot:
        html = str(snapshot.get("html") or "")
        return html or None
    return None


def fetch_page_snapshot_with_browser_session(url: str, port: int | None = None, profile_name: str = "1688") -> dict[str, Any] | None:
    port = port or BROWSER_DEBUG_PORT
    try:
        open_browser_debug_session(url, port, profile_name)
        target = cdp_target_for_url(port, url)
        cdp = CdpWebSocket(target["webSocketDebuggerUrl"])
        try:
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            cdp.call("DOM.enable")
            cdp.call("Page.navigate", {"url": url}, timeout=20)
            deadline = time.time() + 25
            while time.time() < deadline:
                ready = cdp.call("Runtime.evaluate", {"expression": "document.readyState", "returnByValue": True}, timeout=10)
                state = ready.get("result", {}).get("value", "")
                if state == "complete":
                    break
                time.sleep(1)
            for _ in range(3):
                try:
                    cdp.call("Runtime.evaluate", {"expression": "window.scrollTo(0, document.body ? document.body.scrollHeight : 0)"}, timeout=10)
                    time.sleep(0.8)
                except Exception:
                    break
            values: dict[str, Any] = {}
            expressions = {
                "html": "document.documentElement.outerHTML",
                "text": "document.body ? document.body.innerText : ''",
                "title": "document.title || ''",
                "url": "location.href || ''",
                "images": "(() => [...new Set([...document.images].map(img => img.currentSrc || img.src || '').filter(Boolean))].slice(0, 40))()",
            }
            for key, expression in expressions.items():
                try:
                    result = cdp.call("Runtime.evaluate", {"expression": expression, "returnByValue": True}, timeout=30)
                    values[key] = result.get("result", {}).get("value", [] if key == "images" else "")
                except Exception:
                    values[key] = [] if key == "images" else ""
            screenshot_base64 = ""
            try:
                screenshot_result = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True}, timeout=30)
                screenshot_base64 = str(screenshot_result.get("data") or "")
            except Exception:
                screenshot_base64 = ""
            html = str(values.get("html") or "")
            if not html.strip():
                return None
            artifacts = save_collect_snapshot_artifacts(
                detect_source_platform(url) or profile_name,
                str(values.get("url") or url),
                html=html,
                screenshot_base64=screenshot_base64,
                text=str(values.get("text") or ""),
            )
            return {
                "url": values.get("url") or url,
                "html": html,
                "text": values.get("text") or "",
                "title": values.get("title") or "",
                "image_urls": values.get("images") or [],
                "html_snapshot_path": artifacts.get("html_snapshot_path", ""),
                "screenshot_path": artifacts.get("screenshot_path", ""),
                "final_url": values.get("url") or url,
                "page_title": values.get("title") or "",
            }
        finally:
            cdp.close()
    except Exception:
        return None


def snapshot_from_cdp_target(target: dict[str, Any], platform_hint: str = "") -> dict[str, Any]:
    if not isinstance(target, dict) or not target.get("webSocketDebuggerUrl"):
        raise RuntimeError("TAB_NOT_ACCESSIBLE")
    cdp = CdpWebSocket(str(target["webSocketDebuggerUrl"]))
    try:
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        for _ in range(3):
            try:
                cdp.call("Runtime.evaluate", {"expression": "window.scrollTo(0, document.body ? document.body.scrollHeight : 0)"}, timeout=10)
                time.sleep(0.6)
            except Exception:
                break
        values: dict[str, Any] = {}
        expressions = {
            "html": "document.documentElement ? document.documentElement.outerHTML : ''",
            "text": "document.body ? document.body.innerText : ''",
            "title": "document.title || ''",
            "url": "location.href || ''",
            "images": "(() => [...new Set([...document.images].map(img => img.currentSrc || img.src || '').filter(Boolean))].slice(0, 80))()",
        }
        for key, expression in expressions.items():
            result = cdp.call("Runtime.evaluate", {"expression": expression, "returnByValue": True}, timeout=30)
            values[key] = result.get("result", {}).get("value", [] if key == "images" else "")
        screenshot_base64 = ""
        try:
            screenshot_result = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True}, timeout=30)
            screenshot_base64 = str(screenshot_result.get("data") or "")
        except Exception:
            screenshot_base64 = ""
        url = str(values.get("url") or target.get("url") or "")
        html_text = str(values.get("html") or "")
        if not html_text:
            raise RuntimeError("TAB_NOT_ACCESSIBLE")
        platform = platform_hint or detect_source_platform(url) or "unknown"
        artifacts = save_collect_snapshot_artifacts(
            platform,
            url,
            html=html_text,
            screenshot_base64=screenshot_base64,
            text=str(values.get("text") or ""),
        )
        return {
            "url": url,
            "html": html_text,
            "text": str(values.get("text") or ""),
            "title": str(values.get("title") or target.get("title") or ""),
            "image_urls": values.get("images") if isinstance(values.get("images"), list) else [],
            "html_snapshot_path": artifacts.get("html_snapshot_path", ""),
            "screenshot_path": artifacts.get("screenshot_path", ""),
            "final_url": url,
            "page_title": str(values.get("title") or target.get("title") or ""),
        }
    finally:
        cdp.close()


def fetch_1688_page_snapshot_with_browser_session(url: str, port: int | None = None) -> dict[str, Any] | None:
    return fetch_page_snapshot_with_browser_session(url, port=port, profile_name="1688")


def fetch_page_html(url: str, cookie: str = "") -> str:
    try:
        return legacy.fetch_url_html(url, cookie)
    except TypeError:
        return legacy.fetch_url_html(url)


def fetch_page_html_with_status(url: str, cookie: str = "") -> tuple[str, int]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    if cookie.strip():
        headers["Cookie"] = cookie.strip()
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
        html = raw.decode("utf-8", errors="ignore")
        return html, int(getattr(response, "status", 200) or 200)


def maybe_fetch_page_html_with_playwright(url: str, cookie: str = "") -> str | None:
    if os.environ.get("ERP_USE_PLAYWRIGHT", "").strip() != "1":
        return None
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
                viewport={"width": 1440, "height": 1280},
            )
            if cookie.strip():
                context.add_cookies(parse_cookie_header(cookie, url))
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=45000)
            html = page.content()
            browser.close()
            return html
    except Exception:
        return None


def extract_text_pattern(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.M)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return ""


def infer_list_from_text(value: str) -> list[str]:
    cleaned = re.sub(r"[，;；/]+", "、", value)
    return [item.strip() for item in cleaned.split("、") if item.strip()]


def extract_1688_attributes(text: str, html: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for line in [line.strip() for line in text.splitlines() if line.strip()]:
        match = re.match(r"^([A-Za-z0-9_\-\u4e00-\u9fff]{1,24})\s*[:：]\s*(.+)$", line)
        if not match:
            continue
        key = normalize_space(match.group(1))
        value = normalize_space(match.group(2))
        if not key or not value or len(value) > 160:
            continue
        attrs.setdefault(key, value)

    targeted = {
        "品牌": [r"品牌[:：]\s*([^\n]+)", r"厂牌[:：]\s*([^\n]+)"],
        "货号": [r"货号[:：]\s*([^\n]+)", r"产品货号[:：]\s*([^\n]+)", r"款号[:：]\s*([^\n]+)"],
        "SKU": [r"SKU[:：]\s*([^\n]+)", r"sku[:：]\s*([^\n]+)"],
        "型号": [r"型号[:：]\s*([^\n]+)", r"规格型号[:：]\s*([^\n]+)"],
        "材质": [r"材质[:：]\s*([^\n]+)", r"材料[:：]\s*([^\n]+)", r"面料[:：]\s*([^\n]+)"],
        "规格": [r"规格[:：]\s*([^\n]+)", r"尺寸[:：]\s*([^\n]+)", r"产品尺寸[:：]\s*([^\n]+)"],
        "重量": [r"重量[:：]\s*([^\n]+)", r"净重[:：]\s*([^\n]+)", r"毛重[:：]\s*([^\n]+)"],
        "包装清单": [r"包装清单[:：]\s*([^\n]+)", r"包装内容[:：]\s*([^\n]+)", r"包装说明[:：]\s*([^\n]+)"],
    }
    for key, patterns in targeted.items():
        if attrs.get(key):
            continue
        value = extract_text_pattern(text, patterns)
        if value:
            attrs[key] = value

    if not attrs:
        matches = re.findall(r"([A-Za-z0-9_\-\u4e00-\u9fff]{2,24})\s*[:：]\s*([^\n]{1,160})", html)
        for key, value in matches[:40]:
            key = normalize_space(key)
            value = normalize_space(value)
            if key and value and key not in attrs:
                attrs[key] = value
    return attrs


def extract_1688_sku(page_url: str, text: str, html: str, attrs: dict[str, str]) -> str:
    candidates = [
        attrs.get("SKU", ""),
        attrs.get("货号", ""),
        attrs.get("型号", ""),
        attrs.get("款号", ""),
        attrs.get("产品编号", ""),
        attrs.get("商品编号", ""),
    ]
    match = re.search(r"/offer/(\d+)\.html", page_url)
    if match:
        candidates.append(match.group(1))
    for pattern in [
        r'"offerId"\s*[:=]\s*"?(\d+)"?',
        r'"itemId"\s*[:=]\s*"?(\d+)"?',
        r'"productId"\s*[:=]\s*"?(\d+)"?',
        r'货号[:：]\s*([A-Za-z0-9\-_]+)',
        r'SKU[:：]\s*([A-Za-z0-9\-_]+)',
    ]:
        found = re.search(pattern, f"{text}\n{html}", flags=re.I)
        if found:
            candidates.append(found.group(1))
    for value in candidates:
        value = normalize_space(str(value))
        if value:
            return value[:80]
    return ""


def populate_source_from_legacy_product(product: dict[str, Any], platform: str, page_url: str = "") -> dict[str, Any]:
    product = deepcopy(product if isinstance(product, dict) else {})
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    image_refs = []
    image_refs.extend(normalize_list(product.get("source_images")))
    image_refs.extend(normalize_list(product.get("source_image_urls")))
    image_refs.extend(normalize_list(product.get("detail_images")))
    image_refs.extend(normalize_list(product.get("detail_image_urls")))
    dims = product.get("dimensions")
    source.update(
        {
            "source_url": str(source.get("source_url") or product.get("source_url") or page_url or "").strip(),
            "source_platform": str(source.get("source_platform") or platform or product.get("source_platform") or "").strip().lower(),
            "title": str(source.get("title") or product.get("name") or "").strip(),
            "price": str(source.get("price") or product.get("detected_price") or product.get("cost") or "").strip(),
            "currency": str(source.get("currency") or product.get("detected_currency") or "").strip(),
            "bullets": normalize_list(source.get("bullets") or product.get("selling_points")),
            "description": str(source.get("description") or product.get("description") or "").strip(),
            "images": normalize_list(source.get("images") or image_refs),
            "dimensions": source.get("dimensions") if isinstance(source.get("dimensions"), dict) and any(source.get("dimensions").values()) else parse_dimensions_text(dims),
            "weight_kg": str(source.get("weight_kg") or product.get("weight_kg") or "").strip(),
            "material": str(source.get("material") or (normalize_list(product.get("materials")) or [""])[0] or "").strip(),
            "package_contents": normalize_list(source.get("package_contents") or product.get("package_includes")),
            "skus": deepcopy(source.get("skus") or product.get("sku_items") or []),
        }
    )
    if isinstance(product.get("attributes"), dict) and product.get("attributes"):
        source["attributes"] = deepcopy(product["attributes"])
    product["source"] = source
    return product


def collect_product_image_urls(html: str, page_url: str, snapshot_image_urls: list[Any] | None = None, limit: int = 20) -> list[str]:
    """Prefer product-image candidates from HTML over raw DOM image order.

    1688 pages contain many UI icons before the real product gallery in
    ``document.images``.  The HTML extractor finds product-specific fields such
    as og:image/mainUrl/imageUrl first, then we append DOM image URLs as a
    fallback.
    """

    candidates: list[Any] = []
    try:
        candidates.extend(legacy.extract_product_image_urls(html, page_url, limit=max(limit * 2, 20)))
    except Exception:
        pass
    candidates.extend(snapshot_image_urls or [])

    clean: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        raw = str(item or "").strip()
        if not raw:
            continue
        try:
            url = legacy.normalize_image_url(raw, page_url)
        except Exception:
            url = raw.replace("\\/", "/").strip()
        lowered = url.lower()
        parsed_path = urllib.parse.urlparse(url).path.lower()
        if not url or url in seen:
            continue
        if parsed_path.endswith(".svg") or any(skip in lowered for skip in ["sprite", "logo", "avatar", "icon"]):
            continue
        seen.add(url)
        clean.append(url)
        if len(clean) >= limit:
            break
    return clean


def parse_1688_product(raw_data: str | dict[str, Any], page_url: str = "") -> dict[str, Any]:
    if isinstance(raw_data, dict):
        html = str(raw_data.get("html", "") or "")
        text = str(raw_data.get("text") or "")
        page_title = str(raw_data.get("title") or "")
        page_url = str(raw_data.get("url") or page_url or "")
        image_urls = list(raw_data.get("image_urls") or [])
    else:
        html = str(raw_data or "")
        text = ""
        page_title = ""
        image_urls = []
    if not text:
        text = legacy.html_to_text(html)
    product = default_product_model()
    product["source_url"] = page_url
    product["source_text"] = text

    attrs = extract_1688_attributes(text, html)
    title = page_title or legacy.extract_page_title(html) or extract_text_pattern(text, [r"产品名称[:：]\s*([^\n]+)", r"商品名称[:：]\s*([^\n]+)", r"标题[:：]\s*([^\n]+)"])
    if title:
        product["name"] = title.strip()
        product.update({key: value for key, value in legacy.infer_product_from_title(title).items() if value})

    brand = extract_text_pattern(text, [r"品牌[:：]\s*([^\n]+)", r"厂牌[:：]\s*([^\n]+)"])
    if brand:
        product["brand"] = brand

    category = extract_text_pattern(text, [r"类目[:：]\s*([^\n]+)", r"品类[:：]\s*([^\n]+)"])
    if category:
        product["category"] = category

    target_customer = extract_text_pattern(text, [r"目标买家[:：]\s*([^\n]+)"])
    if target_customer:
        product["target_customer"] = target_customer

    dimensions = extract_text_pattern(text, [r"(?:规格|尺寸|长宽高)[:：]\s*([^\n]+)"])
    if dimensions:
        product["dimensions"] = dimensions

    weight = extract_text_pattern(text, [r"重量[:：]\s*([^\n]+)"])
    if weight:
        product["weight_kg"] = re.sub(r"[^\d.,]", "", weight)

    materials = extract_text_pattern(text, [r"(?:材质|材料|面料)[:：]\s*([^\n]+)"])
    if materials:
        product["materials"] = infer_list_from_text(materials)

    package = extract_text_pattern(text, [r"(?:包装清单|包装内容|包装说明)[:：]\s*([^\n]+)"])
    if package:
        product["package_includes"] = infer_list_from_text(package)

    if attrs:
        product["attributes"] = attrs
    sku = extract_1688_sku(page_url, text, html, attrs)
    if sku:
        product["sku"] = sku
    model = attrs.get("型号") or attrs.get("货号") or attrs.get("SKU") or ""
    if model:
        product["model"] = model[:80]

    bullets: list[str] = []
    for line in text.splitlines():
        value = line.strip(" -•\t")
        if 6 <= len(value) <= 80 and any(ch.isalpha() or "\u4e00" <= ch <= "\u9fff" for ch in value):
            if not value.startswith(("品牌", "型号", "规格", "尺寸", "包装", "材质", "价格")):
                bullets.append(value)
    product["selling_points"] = bullets[:6]

    price, currency = legacy.extract_price_currency(html)
    if price:
        product["detected_price"] = price
        product["detected_currency"] = currency
        product["detected_price_display"] = f"{price} {currency}".strip()

    dims, parsed_weight = legacy.extract_measurements(html)
    if dims and not product.get("dimensions"):
        product["dimensions"] = dims
    if parsed_weight and not product.get("weight_kg"):
        product["weight_kg"] = parsed_weight

    source_dir = SOURCE_DIR
    source_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[str] = []
    extracted_image_urls = collect_product_image_urls(html, page_url, image_urls, limit=20)
    if extracted_image_urls:
        try:
            image_paths = legacy.download_images(extracted_image_urls, source_dir)
        except Exception:
            image_paths = []

    product["source_image_urls"] = extracted_image_urls[:7]
    product["detail_image_urls"] = extracted_image_urls[7:20]
    product["source_images"] = image_paths[:7]
    product["detail_images"] = image_paths[7:20]
    return normalize_product_fields(populate_source_from_legacy_product(product, "1688", page_url))


def parse_amazon_product(raw_data: str | dict[str, Any], page_url: str = "") -> dict[str, Any]:
    if isinstance(raw_data, dict):
        html = str(raw_data.get("html", "") or "")
        text = str(raw_data.get("text") or "")
        page_title = str(raw_data.get("title") or "")
        page_url = str(raw_data.get("url") or page_url or "")
        image_urls = list(raw_data.get("image_urls") or [])
    else:
        html = str(raw_data or "")
        text = ""
        page_title = ""
        image_urls = []
    if not text:
        text = legacy.html_to_text(html)

    product = default_product_model()
    product["source_url"] = page_url
    product["source_platform"] = "Amazon"
    product["source_text"] = text

    title = page_title or legacy.extract_page_title(html)
    if title:
        product["name"] = title.strip()
        product.update({key: value for key, value in legacy.infer_product_from_title(title).items() if value})

    bullets = []
    try:
        bullets = legacy.extract_amazon_bullets(html)
    except Exception:
        bullets = []
    if bullets:
        product["selling_points"] = bullets[:10]

    price, currency = legacy.extract_price_currency(html)
    if price:
        product["detected_price"] = price
        product["detected_currency"] = currency
        product["detected_price_display"] = f"{price} {currency}".strip()

    dims, parsed_weight = legacy.extract_measurements(html)
    if dims:
        product["dimensions"] = dims
    if parsed_weight:
        product["weight_kg"] = parsed_weight

    source_dir = SOURCE_DIR
    source_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[str] = []
    extracted_image_urls = collect_product_image_urls(html, page_url, image_urls, limit=20)
    if extracted_image_urls:
        try:
            image_paths = legacy.download_images(extracted_image_urls, source_dir)
        except Exception:
            image_paths = []

    product["source_image_urls"] = extracted_image_urls[:7]
    product["detail_image_urls"] = extracted_image_urls[7:20]
    product["source_images"] = image_paths[:7]
    product["detail_images"] = image_paths[7:20]
    return normalize_product_fields(populate_source_from_legacy_product(product, "amazon", page_url))

def parse_amazon_product(raw_data: str | dict[str, Any], page_url: str = "") -> dict[str, Any]:
    if isinstance(raw_data, dict):
        html = str(raw_data.get("html", "") or "")
        text = str(raw_data.get("text") or "")
        page_title = str(raw_data.get("title") or "")
        page_url = str(raw_data.get("url") or page_url or "")
        image_urls = list(raw_data.get("image_urls") or [])
    else:
        html = str(raw_data or "")
        text = ""
        page_title = ""
        image_urls = []
    if not text:
        text = legacy.html_to_text(html)

    product = default_product_model()
    product["source_url"] = page_url
    product["source_platform"] = "Amazon"
    product["source_text"] = text

    title = page_title or legacy.extract_page_title(html)
    if title:
        product["name"] = title.strip()
        product.update({key: value for key, value in legacy.infer_product_from_title(title).items() if value})

    bullets = []
    try:
        bullets = legacy.extract_amazon_bullets(html)
    except Exception:
        bullets = []
    if bullets:
        product["selling_points"] = bullets[:10]

    price, currency = legacy.extract_price_currency(html)
    if price:
        product["detected_price"] = price
        product["detected_currency"] = currency
        product["detected_price_display"] = f"{price} {currency}".strip()

    dims, parsed_weight = legacy.extract_measurements(html)
    if dims:
        product["dimensions"] = dims
    if parsed_weight:
        product["weight_kg"] = parsed_weight

    source_dir = SOURCE_DIR
    source_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[str] = []
    extracted_image_urls = collect_product_image_urls(html, page_url, image_urls, limit=20)
    if extracted_image_urls:
        try:
            image_paths = legacy.download_images(extracted_image_urls, source_dir)
        except Exception:
            image_paths = []

    product["source_image_urls"] = extracted_image_urls[:7]
    product["detail_image_urls"] = extracted_image_urls[7:20]
    product["source_images"] = image_paths[:7]
    product["detail_images"] = image_paths[7:20]
    return normalize_product_fields(populate_source_from_legacy_product(product, "amazon", page_url))


def parse_generic_product(raw_data: str | dict[str, Any], page_url: str = "") -> dict[str, Any]:
    if isinstance(raw_data, dict):
        html = str(raw_data.get("html", "") or "")
        text = str(raw_data.get("text") or "")
        page_title = str(raw_data.get("title") or "")
        page_url = str(raw_data.get("url") or page_url or "")
        image_urls = list(raw_data.get("image_urls") or [])
    else:
        html = str(raw_data or "")
        text = ""
        page_title = ""
        image_urls = []
    if not text:
        text = legacy.html_to_text(html)
    product = default_product_model()
    product["source_url"] = page_url
    product["source_platform"] = "unknown"
    product["source_text"] = text
    title = page_title or legacy.extract_page_title(html) or extract_text_pattern(text, [r"(?:title|标题|商品名称)[:：]\s*([^\n]+)"])
    if title:
        product["name"] = title.strip()
    brand = extract_text_pattern(text, [r"(?:brand|品牌)[:：]\s*([^\n]+)"])
    if brand:
        product["brand"] = brand.strip()
    bullets = [line.strip(" -•\t") for line in text.splitlines() if 8 <= len(line.strip()) <= 120][:8]
    if bullets:
        product["selling_points"] = bullets
    price, currency = legacy.extract_price_currency(html)
    if price:
        product["detected_price"] = price
        product["detected_currency"] = currency
    dims, parsed_weight = legacy.extract_measurements(html)
    if dims:
        product["dimensions"] = dims
    if parsed_weight:
        product["weight_kg"] = parsed_weight
    if not image_urls:
        image_urls = legacy.extract_product_image_urls(html, page_url, limit=20)
    image_urls = list(dict.fromkeys([str(item).strip() for item in image_urls if str(item).strip()]))[:20]
    product["source_image_urls"] = image_urls[:7]
    product["detail_image_urls"] = image_urls[7:20]
    return normalize_product_fields(populate_source_from_legacy_product(product, "unknown", page_url))

def collect_source_product(
    url: str,
    mode: str = "browser",
    cookie: str | None = None,
    platform: str | None = None,
    claim_platforms: list[str] | None = None,
) -> dict[str, Any]:
    url = str(url or "").strip()
    if not url:
        raise RuntimeError("璇峰厛杈撳叆鍟嗗搧閾炬帴銆?")

    platform_detected = (platform or detect_source_platform(url)).lower() or "unknown"
    collect_mode = normalize_collect_mode(mode, url)
    cookie = (cookie or "").strip()
    diagnostics = default_collect_diagnostics()
    diagnostics.update(
        {
            "collect_mode": collect_mode,
            "source_url": url,
            "normalized_url": url,
            "platform_detected": platform_detected,
            "started_at": collect_time_iso(),
        }
    )
    partial_product = load_product()
    snapshot: dict[str, Any] | None = None
    html = ""
    text = ""
    title = ""
    final_url = url
    http_status = ""
    error_reason = ""

    try:
        if collect_mode == "manual":
            raise RuntimeError("MANUAL_MODE")

        if collect_mode == "browser":
            snapshot = fetch_page_snapshot_with_browser_session(url, profile_name=current_browser_profile_name(platform_detected))
            if not snapshot and platform_detected == "1688":
                html = maybe_fetch_page_html_with_playwright(url, cookie) or ""
                if html:
                    snapshot = page_snapshot_from_html(url, html, legacy.html_to_text(html), legacy.extract_page_title(html), legacy.extract_product_image_urls(html, url, limit=20))
        if not snapshot:
            html, http_status = fetch_page_html_with_status(url, cookie)
            if html:
                snapshot = page_snapshot_from_html(url, html, legacy.html_to_text(html), legacy.extract_page_title(html), legacy.extract_product_image_urls(html, url, limit=20))
        if not snapshot:
            raise RuntimeError("NO_SNAPSHOT")

        html = str(snapshot.get("html") or "")
        text = str(snapshot.get("text") or "")
        title = str(snapshot.get("title") or "")
        final_url = str(snapshot.get("final_url") or snapshot.get("url") or url)
        diagnostics["html_snapshot_path"] = str(snapshot.get("html_snapshot_path") or "")
        diagnostics["screenshot_path"] = str(snapshot.get("screenshot_path") or "")
        if not diagnostics["html_snapshot_path"] and html:
            diagnostics["html_snapshot_path"] = write_collect_debug_html(final_url, html, platform_detected)
        diagnostics["final_url"] = final_url
        diagnostics["page_title"] = title or legacy.extract_page_title(html)
        diagnostics["http_status"] = http_status

        if platform_detected == "1688":
            diagnostics["is_login_page"] = is_1688_login_page(final_url, html, text, title)
            diagnostics["is_captcha_page"] = is_1688_security_check_page(html, text)
            diagnostics["is_security_check_page"] = diagnostics["is_captcha_page"]
            if diagnostics["is_login_page"]:
                error_reason = "LOGIN"
            elif diagnostics["is_captcha_page"]:
                error_reason = "CAPTCHA"
        elif platform_detected == "amazon":
            diagnostics["is_login_page"] = "signin" in final_url.lower() or "sign in" in f"{title} {text}".lower()
            diagnostics["is_captcha_page"] = is_amazon_robot_check_page(final_url, html, text, title)
            diagnostics["is_security_check_page"] = diagnostics["is_captcha_page"]
            if diagnostics["is_captcha_page"]:
                error_reason = "ROBOT"
            elif is_amazon_region_blocked_page(html, text):
                error_reason = "REGION"

        if platform_detected == "amazon":
            parsed_product = parse_amazon_product(snapshot, final_url)
        elif platform_detected == "unknown":
            parsed_product = parse_generic_product(snapshot, final_url)
        else:
            parsed_product = parse_1688_product(snapshot, final_url)

        source_updates = parsed_product.get("source") if isinstance(parsed_product.get("source"), dict) else {}
        source_updates = normalize_collect_source_images(source_updates, platform_detected, collect_mode, claim_platforms)
        diagnostics.update(snapshot_field_flags(source_updates))
        if not diagnostics["title_found"]:
            error_reason = error_reason or "NO_TITLE"
        if diagnostics["images_found_count"] <= 0:
            error_reason = error_reason or "NO_IMAGES"
        if platform_detected == "amazon" and diagnostics["bullets_found_count"] <= 0:
            error_reason = error_reason or "NO_BULLETS"
        if platform_detected == "amazon" and not diagnostics["dimensions_found"]:
            error_reason = error_reason or "NO_DIMENSIONS"
        if platform_detected == "amazon" and not diagnostics["weight_found"]:
            error_reason = error_reason or "NO_WEIGHT"
        if platform_detected == "1688" and diagnostics["images_found_count"] <= 0:
            error_reason = error_reason or "NO_IMAGES"
        if platform_detected == "1688" and not diagnostics["dimensions_found"]:
            error_reason = error_reason or "NO_DIMENSIONS"

        diagnostics["error_code"] = collect_error_code(platform_detected, collect_mode, error_reason) if error_reason else ""
        diagnostics["error_message"] = "采集成功" if not diagnostics["error_code"] else diagnostics["error_code"]
        diagnostics["partial_success"] = any(
            [
                diagnostics["title_found"],
                diagnostics["images_found_count"],
                diagnostics["bullets_found_count"],
                diagnostics["dimensions_found"],
                diagnostics["weight_found"],
            ]
        )
        diagnostics["success"] = bool(diagnostics["title_found"] and not diagnostics["error_code"])
        diagnostics["finished_at"] = collect_time_iso()
        diagnostics = finalize_collect_diagnostics(diagnostics, source_updates, platform_detected)

        merged = merge_source_partial_result(partial_product, source_updates, diagnostics)
        merged["source"]["source_url"] = url
        merged["source"]["source_platform"] = str(merged["source"].get("source_platform") or platform_detected or "").strip()
        merged["source"]["collect_status"] = "success" if diagnostics["success"] else ("partial" if diagnostics["partial_success"] else "failed")
        merged["source"]["collect_logs"] = list(merged["source"].get("collect_logs") or [])
        merged["source"]["collect_logs"].append(
            {
                "started_at": diagnostics["started_at"],
                "finished_at": diagnostics["finished_at"],
                "mode": collect_mode,
                "platform": platform_detected,
                "success": diagnostics["success"],
                "partial_success": diagnostics["partial_success"],
                "error_code": diagnostics["error_code"],
                "error_message": diagnostics["error_message"],
            }
        )
        merged["source"]["collect_diagnostics"] = diagnostics
        merged["collect_status"] = merged["source"]["collect_status"]
        merged["collect_logs"] = merged["source"]["collect_logs"]
        merged["source_url"] = url
        merged["source_platform"] = merged["source"]["source_platform"]
        original_url = str((partial_product.get("source") or {}).get("source_url") or "").strip()
        if url and url != original_url:
            merged.pop("product_id", None)
            merged.pop("id", None)
        merged = apply_claimed_platform_drafts(merged, claim_platforms)
        saved = save_product(merged)
        return {
            "ok": diagnostics["success"],
            "product": saved,
            "imagePool": current_image_pool(saved),
            "sourceImages": current_source_images(saved),
            "diagnostics": diagnostics,
            "productsIndex": load_products_index(),
            "error": diagnostics["error_message"] if not diagnostics["success"] else "",
            "next_action": diagnostics.get("next_action", ""),
        }
    except Exception as exc:
        error_message = str(exc)
        if error_message == "MANUAL_MODE":
            error_message = "手动模式请走插件/手动导入接口"
        if not diagnostics.get("error_code"):
            if error_message == "NO_SNAPSHOT":
                reason = "REMOTE" if collect_mode == "browser" else "SELECTOR"
            elif "403" in error_message or "forbidden" in error_message.lower():
                reason = "FORBIDDEN"
            elif "winerror 10013" in error_message.lower() or "访问套接字" in error_message or "socket" in error_message.lower():
                reason = "REMOTE" if platform_detected == "1688" else "NETWORK"
            else:
                reason = "REMOTE" if "remote" in error_message.lower() or "connect" in error_message.lower() else "SELECTOR"
            if platform_detected == "1688" and "profile" in error_message.lower():
                reason = "PROFILE"
            diagnostics["error_code"] = collect_error_code(platform_detected, collect_mode, reason)
        diagnostics["error_message"] = error_message
        diagnostics["finished_at"] = collect_time_iso()
        diagnostics["success"] = False
        diagnostics["partial_success"] = bool(snapshot or html or text)
        if snapshot or html or text:
            fallback_snapshot = snapshot or page_snapshot_from_html(url, html, text, title)
            if platform_detected == "amazon":
                parsed_product = parse_amazon_product(fallback_snapshot, final_url)
            elif platform_detected == "unknown":
                parsed_product = parse_generic_product(fallback_snapshot, final_url)
            else:
                parsed_product = parse_1688_product(fallback_snapshot, final_url)
            source_updates = parsed_product.get("source") if isinstance(parsed_product.get("source"), dict) else {}
            source_updates = normalize_collect_source_images(source_updates, platform_detected, collect_mode, claim_platforms)
            diagnostics.update(snapshot_field_flags(source_updates))
            merged = merge_source_partial_result(partial_product, source_updates, diagnostics)
        else:
            merged = merge_source_partial_result(partial_product, {}, diagnostics)
        diagnostics = finalize_collect_diagnostics(diagnostics, merged.get("source") if isinstance(merged.get("source"), dict) else {}, platform_detected)
        original_url = str((partial_product.get("source") or {}).get("source_url") or "").strip()
        if url and url != original_url:
            merged = default_product_model() if not diagnostics["partial_success"] else merged
            merged.pop("product_id", None)
            merged.pop("id", None)
            merged["source"]["source_url"] = url
            merged["source"]["source_platform"] = platform_detected
        if diagnostics["partial_success"]:
            merged["source"]["source_url"] = url
            merged["source"]["source_platform"] = str(merged["source"].get("source_platform") or platform_detected or "").strip()
        merged["source"]["collect_status"] = "partial" if diagnostics["partial_success"] else "failed"
        merged["source"]["collect_logs"] = list(merged["source"].get("collect_logs") or [])
        merged["source"]["collect_logs"].append(
            {
                "started_at": diagnostics["started_at"],
                "finished_at": diagnostics["finished_at"],
                "mode": collect_mode,
                "platform": platform_detected,
                "success": False,
                "partial_success": diagnostics["partial_success"],
                "error_code": diagnostics["error_code"],
                "error_message": error_message,
            }
        )
        merged["source"]["collect_diagnostics"] = diagnostics
        merged = apply_claimed_platform_drafts(merged, claim_platforms)
        saved = save_product(merged)
        return {
            "ok": False,
            "product": saved,
            "imagePool": current_image_pool(saved),
            "sourceImages": current_source_images(saved),
            "productsIndex": load_products_index(),
            "diagnostics": diagnostics,
            "error": error_message or diagnostics["error_code"],
            "next_action": diagnostics.get("next_action", ""),
        }


def collect_batch_products(
    urls: Any,
    mode: str = "browser",
    cookie: str | None = None,
    platform: str | None = None,
    platforms: list[str] | None = None,
) -> dict[str, Any]:
    parsed_urls = parse_collect_urls(urls)
    items: list[dict[str, Any]] = []
    for url in parsed_urls:
        detected = (platform or detect_source_platform(url)).lower() or "unknown"
        row = {
            "url": url,
            "platform": detected,
            "status": "pending",
            "ok": False,
            "title": "",
            "image": "",
            "error": "",
            "error_code": "",
            "next_action": "",
            "product_id": "",
            "product": None,
        }
        try:
            result = collect_source_product(url, mode, cookie, detected, platforms)
            product = result.get("product") if isinstance(result.get("product"), dict) else {}
            source = product.get("source") if isinstance(product.get("source"), dict) else {}
            image_pool = current_image_pool(product)
            diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
            row.update(
                {
                    "status": "success" if result.get("ok") else ("partial" if diagnostics.get("partial_success") else "failed"),
                    "ok": bool(result.get("ok")),
                    "title": str(source.get("title") or product.get("name") or ""),
                    "image": str((image_pool[0] if image_pool else {}).get("preview_url") or (image_pool[0] if image_pool else {}).get("url") or ""),
                    "error": str(result.get("error") or ""),
                    "error_code": str(diagnostics.get("error_code") or ""),
                    "next_action": str(result.get("next_action") or diagnostics.get("next_action") or ""),
                    "product_id": str(product.get("product_id") or product.get("id") or ""),
                    "product": product,
                }
            )
        except Exception as exc:
            row.update(
                {
                    "status": "failed",
                    "error": str(exc),
                    "error_code": "COLLECT_FAILED",
                    "next_action": "请检查链接、登录状态或改用手动导入。",
                }
            )
        items.append(row)
    return {
        "ok": True,
        "total": len(items),
        "success_count": sum(1 for item in items if item["status"] == "success"),
        "partial_count": sum(1 for item in items if item["status"] == "partial"),
        "failed_count": sum(1 for item in items if item["status"] == "failed"),
        "items": items,
        "productsIndex": load_products_index(),
    }


def collect_from_browser_tab(
    tab_url: str = "",
    platform_hint: str = "",
    product_url: str = "",
    port: int = BROWSER_DEBUG_PORT,
    claim_platforms: list[str] | None = None,
    save_only: bool = False,
    mock_tabs: list[dict[str, Any]] | None = None,
    mock_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    original_product = load_product()
    started_at = collect_time_iso()
    status = browser_debug_status(port, mock_tabs)
    if not status.get("connected") and mock_snapshot is None:
        diagnostics = default_collect_diagnostics()
        diagnostics.update(
            {
                "collect_mode": "browser_debugging",
                "started_at": started_at,
                "finished_at": collect_time_iso(),
                "success": False,
                "partial_success": False,
                "error_code": status.get("error_code") or "REMOTE_DEBUGGING_NOT_CONNECTED",
                "error_message": status.get("error_message") or "未连接 Chrome remote debugging",
                "browser_connected": False,
                "debug_port": port,
                "next_action": status.get("next_action") or "请启动专用 Chrome 后重试。",
                "checked_at": collect_time_iso(),
            }
        )
        merged = merge_source_partial_result(original_product, {}, diagnostics)
        merged["source"]["collect_diagnostics"] = diagnostics
        saved = save_product(merged)
        return {"ok": False, "product": saved, "imagePool": current_image_pool(saved), "productsIndex": load_products_index(), "diagnostics": diagnostics, "browserStatus": status, "error": diagnostics["error_message"], "next_action": diagnostics["next_action"], "real_publish_called": False}
    try:
        if mock_snapshot is not None:
            snapshot = deepcopy(mock_snapshot)
            snapshot.setdefault("text", legacy.html_to_text(str(snapshot.get("html") or "")))
            snapshot.setdefault("title", legacy.extract_page_title(str(snapshot.get("html") or "")))
            snapshot.setdefault("image_urls", legacy.extract_product_image_urls(str(snapshot.get("html") or ""), str(snapshot.get("url") or product_url or tab_url), limit=80))
        else:
            raw_tabs = http_json(f"http://127.0.0.1:{port}/json")
            raw_tabs = raw_tabs if isinstance(raw_tabs, list) else []
            target = choose_browser_tab(raw_tabs, tab_url, product_url, platform_hint)
            if not target:
                raise RuntimeError("NO_PRODUCT_TAB_FOUND")
            snapshot = snapshot_from_cdp_target(target, platform_hint)
        final_url = str(snapshot.get("final_url") or snapshot.get("url") or product_url or tab_url or "")
        html_text = str(snapshot.get("html") or "")
        text = str(snapshot.get("text") or "")
        title = str(snapshot.get("title") or snapshot.get("page_title") or "")
        platform_detected = (platform_hint or detect_source_platform(final_url) or detect_source_platform(title) or "unknown").lower()
        diagnostics = default_collect_diagnostics()
        diagnostics.update(
            {
                "collect_mode": "browser_debugging",
                "source_url": product_url or final_url,
                "normalized_url": final_url,
                "platform_detected": platform_detected,
                "started_at": started_at,
                "finished_at": collect_time_iso(),
                "final_url": final_url,
                "page_title": title,
                "html_snapshot_path": str(snapshot.get("html_snapshot_path") or ""),
                "screenshot_path": str(snapshot.get("screenshot_path") or ""),
                "browser_connected": True,
                "debug_port": port,
                "tab_url": final_url,
                "tab_title": title,
            }
        )
        if not diagnostics["html_snapshot_path"] and html_text:
            diagnostics["html_snapshot_path"] = write_collect_debug_html(final_url, html_text, platform_detected)
        error_reason = ""
        if platform_detected == "1688":
            diagnostics["is_login_page"] = is_1688_login_page(final_url, html_text, text, title)
            diagnostics["is_captcha_page"] = is_1688_security_check_page(html_text, text)
            diagnostics["is_security_check_page"] = diagnostics["is_captcha_page"]
            if diagnostics["is_login_page"]:
                error_reason = "LOGIN"
            elif "滑块" in text or "slider" in text.lower():
                error_reason = "SLIDER"
            elif diagnostics["is_captcha_page"]:
                error_reason = "CAPTCHA"
        elif platform_detected == "amazon":
            diagnostics["is_login_page"] = "signin" in final_url.lower() or "sign in" in f"{title} {text}".lower()
            diagnostics["is_captcha_page"] = is_amazon_robot_check_page(final_url, html_text, text, title)
            diagnostics["is_security_check_page"] = diagnostics["is_captcha_page"]
            if diagnostics["is_captcha_page"]:
                error_reason = "ROBOT"
            elif diagnostics["is_login_page"]:
                error_reason = "LOGIN"
            elif is_amazon_region_blocked_page(html_text, text):
                error_reason = "REGION"
        parsed = parse_amazon_product(snapshot, final_url) if platform_detected == "amazon" else parse_1688_product(snapshot, final_url) if platform_detected == "1688" else parse_generic_product(snapshot, final_url)
        source_updates = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
        source_updates = normalize_collect_source_images(source_updates, platform_detected, "browser", claim_platforms)
        flags = snapshot_field_flags(source_updates)
        if not flags["title_found"]:
            error_reason = error_reason or "NO_TITLE"
        if flags["images_found_count"] <= 0:
            error_reason = error_reason or "NO_IMAGES"
        diagnostics["error_code"] = collect_error_code(platform_detected, "browser", error_reason) if error_reason else ""
        diagnostics["error_message"] = "浏览器采集成功" if not diagnostics["error_code"] else diagnostics["error_code"]
        diagnostics["partial_success"] = any([flags["title_found"], flags["images_found_count"], flags["bullets_found_count"], flags["dimensions_found"], flags["weight_found"]])
        diagnostics["success"] = bool(flags["title_found"] and not diagnostics["error_code"])
        diagnostics = finalize_collect_diagnostics(diagnostics, source_updates, platform_detected)
        if save_only:
            return {"ok": True, "saved_only": True, "diagnostics": diagnostics, "browserStatus": status, "html": html_text, "real_publish_called": False}
        merged = merge_source_partial_result(original_product, source_updates, diagnostics)
        if diagnostics["partial_success"]:
            merged["source"]["source_url"] = final_url
            merged["source"]["source_platform"] = platform_detected
            original_url = str((original_product.get("source") or {}).get("source_url") or "").strip()
            if final_url and final_url != original_url:
                merged.pop("product_id", None)
                merged.pop("id", None)
        merged["source"]["collect_status"] = "success" if diagnostics["success"] else ("partial" if diagnostics["partial_success"] else "failed")
        merged["source"]["collect_logs"] = list(merged["source"].get("collect_logs") or [])
        merged["source"]["collect_logs"].append({"started_at": started_at, "finished_at": diagnostics["finished_at"], "mode": "browser_debugging", "platform": platform_detected, "success": diagnostics["success"], "partial_success": diagnostics["partial_success"], "error_code": diagnostics["error_code"], "error_message": diagnostics["error_message"]})
        merged["source"]["collect_diagnostics"] = diagnostics
        merged = apply_claimed_platform_drafts(merged, claim_platforms)
        saved = save_product(merged)
        return {"ok": diagnostics["success"], "product": saved, "imagePool": current_image_pool(saved), "sourceImages": current_source_images(saved), "productsIndex": load_products_index(), "diagnostics": diagnostics, "browserStatus": status, "error": "" if diagnostics["success"] else diagnostics["error_message"], "next_action": diagnostics.get("next_action", ""), "real_publish_called": False}
    except Exception as exc:
        message = str(exc)
        code = "NO_PRODUCT_TAB_FOUND" if "NO_PRODUCT_TAB_FOUND" in message else "TAB_NOT_ACCESSIBLE" if "TAB_NOT_ACCESSIBLE" in message else "REMOTE_DEBUGGING_NOT_CONNECTED"
        diagnostics = default_collect_diagnostics()
        diagnostics.update({"collect_mode": "browser_debugging", "started_at": started_at, "finished_at": collect_time_iso(), "success": False, "partial_success": False, "error_code": code, "error_message": message, "browser_connected": bool(status.get("connected")), "debug_port": port, "next_action": "请确认专用 Chrome 已打开商品页；如果仍失败，点击保存 HTML 快照或使用 HTML 导入 / 手动补充。", "checked_at": collect_time_iso()})
        merged = merge_source_partial_result(original_product, {}, diagnostics)
        merged["source"]["collect_diagnostics"] = diagnostics
        saved = save_product(merged)
        return {"ok": False, "product": saved, "imagePool": current_image_pool(saved), "productsIndex": load_products_index(), "diagnostics": diagnostics, "browserStatus": status, "error": message, "next_action": diagnostics["next_action"], "real_publish_called": False}


def collect_1688_product(url: str, cookie: str | None = None) -> dict[str, Any]:
    if not url.strip():
        raise RuntimeError("请先输入商品链接。")
    cookie = (cookie or "").strip()
    snapshot = fetch_1688_page_snapshot_with_browser_session(url)
    if not snapshot:
        html = maybe_fetch_page_html_with_playwright(url, cookie)
        if html:
            snapshot = {
                "url": url,
                "html": html,
                "text": legacy.html_to_text(html),
                "title": legacy.extract_page_title(html),
                "image_urls": legacy.extract_product_image_urls(html, url, limit=20),
            }
    if not snapshot:
        html = fetch_page_html(url, cookie)
        if html:
            snapshot = {
                "url": url,
                "html": html,
                "text": legacy.html_to_text(html),
                "title": legacy.extract_page_title(html),
                "image_urls": legacy.extract_product_image_urls(html, url, limit=20),
            }
    if not snapshot:
        raise RuntimeError("采集失败：可能需要登录 1688 或完成验证码。请点击“打开 1688 浏览器会话”，登录后重试。")
    html = str(snapshot.get("html") or "")
    text = str(snapshot.get("text") or "")
    if not html.strip() or any(marker in html for marker in VERIFY_MARKERS) or any(marker in text for marker in VERIFY_MARKERS):
        raise RuntimeError("采集失败：1688 返回了登录、验证码或安全验证页面。请在打开的 1688 浏览器中完成验证后重试。")
    product = parse_1688_product(snapshot, url)
    if not product.get("name"):
        raise RuntimeError("采集失败：没有识别到商品标题。请确认链接是商品详情页，或登录 1688 后重试。")
    save_product(product)
    return product


def collect_extension_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    original_product = load_product()
    source_url = str(payload.get("source_url") or "").strip()
    platform = str(payload.get("platform") or detect_source_platform(source_url) or "unknown").strip().lower()
    raw_html = str(payload.get("raw_html_optional") or payload.get("raw_text") or payload.get("text") or "").strip()
    claim_platforms = normalize_platforms(payload.get("platforms")) or ["mercadolibre"]
    explicit_collect_mode = "collect_mode" in payload
    collect_mode = str(payload.get("collect_mode") or "manual").strip().lower()
    image_origin_mode = collect_mode if explicit_collect_mode else "extension"
    if platform in {"manual", ""} and source_url:
        platform = detect_source_platform(source_url) or "unknown"
    parsed_source: dict[str, Any] = {}
    if raw_html:
        html_platform = detect_source_platform(source_url) or platform or "unknown"
        snapshot = page_snapshot_from_html(source_url or "manual://html-import", raw_html)
        parsed_product = parse_amazon_product(snapshot, source_url) if html_platform == "amazon" else parse_1688_product(snapshot, source_url) if html_platform == "1688" else parse_generic_product(snapshot, source_url)
        parsed_source = parsed_product.get("source") if isinstance(parsed_product.get("source"), dict) else {}
        platform = html_platform
    image_values = normalize_list(payload.get("images"))
    manual_image_pool = image_service.materialize_image_values(
        APP_DIR,
        image_values,
        source_url or str(payload.get("title") or "manual-import"),
        claim_platforms,
        collect_image_origin(platform, image_origin_mode),
    )
    manual_updates = {
        "source_url": source_url,
        "source_platform": platform,
        "title": str(payload.get("title") or "").strip(),
        "price": str(payload.get("price") or "").strip(),
        "currency": str(payload.get("currency") or "").strip(),
        "bullets": normalize_list(payload.get("bullets")),
        "description": str(payload.get("description") or "").strip(),
        "images": image_values,
        "image_pool": manual_image_pool,
        "dimensions": payload.get("dimensions") if isinstance(payload.get("dimensions"), dict) else parse_dimensions_text(payload.get("dimensions")),
        "weight_kg": str(payload.get("weight") or "").strip(),
        "material": str(payload.get("material") or "").strip(),
        "package_contents": normalize_list(payload.get("package_contents")),
        "skus": deepcopy(payload.get("sku_options") or []),
        "collect_status": "manual_completed",
    }
    if raw_html:
        try:
            cleaned = collect_service.clean_1688_text(raw_html, source_url)
            if isinstance(cleaned, dict):
                manual_updates["source_price_cny_for_cost"] = cleaned.get("source_price_cny_for_cost") or manual_updates.get("price")
                manual_updates["source_weight_kg"] = cleaned.get("source_weight_kg") or manual_updates.get("weight_kg")
                manual_updates["source_material"] = cleaned.get("source_material") or manual_updates.get("material")
                manual_updates["materials"] = cleaned.get("materials") or ([manual_updates["material"]] if manual_updates.get("material") else [])
                manual_updates["package_includes"] = cleaned.get("package_includes") or manual_updates.get("package_contents")
                manual_updates["source_attributes"] = cleaned.get("source_attributes") or {}
                manual_updates["clean_source_text"] = cleaned.get("clean_source_text") or raw_html[:3000]
                manual_updates["source_text"] = cleaned.get("source_text") or manual_updates["clean_source_text"]
                if cleaned.get("dimensions") and not manual_updates.get("dimensions"):
                    manual_updates["dimensions"] = parse_dimensions_text(cleaned.get("dimensions"))
        except Exception:
            manual_updates["source_text"] = raw_html[:3000]
    source_updates = deepcopy(parsed_source)
    for key, value in manual_updates.items():
        if value not in (None, "", [], {}):
            source_updates[key] = value
    source_updates = normalize_collect_source_images(source_updates, platform, image_origin_mode, claim_platforms)
    diagnostics = default_collect_diagnostics()
    diagnostics.update(
        {
            "collect_mode": collect_mode,
            "source_url": source_url,
            "normalized_url": source_url,
            "platform_detected": platform,
            "started_at": collect_time_iso(),
            "finished_at": collect_time_iso(),
            "success": True,
            "partial_success": True,
            "error_code": "",
            "error_message": "HTML 导入" if raw_html else "手动补充",
            "page_title": str(payload.get("title") or parsed_source.get("title") or "").strip(),
            "final_url": source_url,
            "html_snapshot_path": "",
            "screenshot_path": "",
            "parser_version": "collect-v2",
        }
    )
    if raw_html:
        diagnostics["html_snapshot_path"] = write_collect_debug_html(source_url or "manual", raw_html, "manual")
    diagnostics = finalize_collect_diagnostics(diagnostics, source_updates, platform)
    merged = merge_source_partial_result(original_product, source_updates, diagnostics)
    merged["source"]["collect_status"] = "manual_completed"
    merged["source"]["collect_logs"] = list(merged["source"].get("collect_logs") or [])
    merged["source"]["collect_logs"].append(
        {
            "started_at": diagnostics["started_at"],
            "finished_at": diagnostics["finished_at"],
            "mode": collect_mode,
            "platform": platform,
            "success": True,
            "partial_success": True,
            "error_code": "",
            "error_message": diagnostics["error_message"],
        }
    )
    merged["source"]["collect_diagnostics"] = diagnostics
    original_url = str((original_product.get("source") or {}).get("source_url") or "").strip()
    if source_url and source_url != original_url:
        merged.pop("product_id", None)
        merged.pop("id", None)
    merged = apply_claimed_platform_drafts(merged, claim_platforms)
    saved = save_product(merged)
    return {
        "ok": True,
        "product": saved,
        "imagePool": current_image_pool(saved),
        "sourceImages": current_source_images(saved),
        "productsIndex": load_products_index(),
        "diagnostics": diagnostics,
        "error": "",
    }


def list_presets() -> dict[str, Any]:
    return generator.load_json(APP_DIR / "presets" / "platforms.json")


def platform_to_preset_key(platform: str) -> str:
    platform = (platform or "").lower()
    if platform == "mercadolibre":
        return "mercadolibre"
    if platform in {"wildberries", "ozon"}:
        return "wildberries"
    return "mercadolibre"


def build_plan_for_platform(product: dict[str, Any], platform: str) -> dict[str, Any]:
    presets = list_presets()
    preset_key = platform_to_preset_key(platform)
    keys = [preset_key]
    platforms = [generator.PlatformPlan(key=key, preset=presets[key]) for key in keys if key in presets]
    if not platforms:
        raise RuntimeError("平台预设缺失，无法生成计划")
    plan = generator.build_plan(product, platforms)
    overrides = product.get("listing_overrides", {})
    if isinstance(overrides, dict):
        for platform_key, override in overrides.items():
            if platform_key not in plan.get("platforms", {}):
                continue
            if not isinstance(override, dict):
                continue
            listing = plan["platforms"][platform_key].get("listing", {})
            if not isinstance(listing, dict):
                continue
            for field in ["title", "description", "alt_titles", "search_keywords", "language"]:
                value = override.get(field)
                if value:
                    listing[field] = value
    return apply_product_drafts_to_plan(product, plan)


def build_copy_preview(product: dict[str, Any], platform: str, app_cfg: dict[str, Any]) -> dict[str, Any]:
    plan = apply_product_drafts_to_plan(product, build_plan_for_platform(product, platform))
    ai_cfg = config_service.ai_config_from_sources(APP_DIR, app_cfg).get("text_ai", {})
    provider_name = str(ai_cfg.get("platform") or "DeepSeek").lower()
    provider = "deepseek" if "deepseek" in provider_name else "openai"
    model = str(ai_cfg.get("model") or ("deepseek-chat" if provider == "deepseek" else "gpt-4.1-mini"))
    warning = ""
    old_env = {
        "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY"),
        "DEEPSEEK_BASE_URL": os.environ.get("DEEPSEEK_BASE_URL"),
        "DEEPSEEK_MODEL": os.environ.get("DEEPSEEK_MODEL"),
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
        "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL"),
    }
    try:
        if not str(ai_cfg.get("api_key") or "").strip():
            warning = f"当前未配置 {ai_cfg.get('platform') or '文本 AI'} API Key，已返回基础草稿。"
        else:
            if provider == "deepseek":
                os.environ["DEEPSEEK_API_KEY"] = str(ai_cfg.get("api_key") or "").strip()
                os.environ["DEEPSEEK_BASE_URL"] = str(ai_cfg.get("base_url") or "https://api.deepseek.com").strip()
                os.environ["DEEPSEEK_MODEL"] = model
            else:
                os.environ["OPENAI_API_KEY"] = str(ai_cfg.get("api_key") or "").strip()
                if str(ai_cfg.get("base_url") or "").strip():
                    os.environ["OPENAI_BASE_URL"] = str(ai_cfg.get("base_url") or "").strip()
            generator.refine_listing_copy(
                plan,
                model=model,
                provider=provider,
                deepseek_model=model,
            )
    except Exception as exc:
        warning = str(exc)
    finally:
        for key_name, value in old_env.items():
            if value is None:
                os.environ.pop(key_name, None)
            else:
                os.environ[key_name] = value
    key = platform_to_preset_key(platform)
    listing = plan.get("platforms", {}).get(key, {}).get("listing", {})
    if platform == "mercadolibre":
        listing["title"] = str(listing.get("title") or product.get("name") or "")[:60]
    if platform == "ozon" and not listing.get("description"):
        listing["description"] = "Ozon 俄语文案草稿。"
    return {"plan": plan, "listing": listing, "warning": warning}


def openai_client_from_config(app_cfg: dict[str, Any]):
    text_ai = config_service.ai_config_from_sources(APP_DIR, app_cfg).get("text_ai", {})
    api_key = str(text_ai.get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("请先在系统设置的“生文案通道”填写 API Key。")
    base_url = str(text_ai.get("base_url") or "").strip().rstrip("/")
    kwargs: dict[str, Any] = {"api_key": api_key, "timeout": AI_TEXT_REQUEST_TIMEOUT_SECONDS}
    if base_url:
        kwargs["base_url"] = base_url
    from openai import OpenAI

    return OpenAI(**kwargs)


def target_market_label(target_market: str) -> str:
    mapping = {
        "amazon": "Amazon",
        "mercadolibre": "Mercado Libre",
        "wildberries": "Wildberries",
        "ozon": "Ozon",
    }
    return mapping.get((target_market or "").lower(), (target_market or "marketplace").title())


def build_web_copy_prompt(
    product: dict[str, Any],
    source_listing: dict[str, Any],
    source_platform: str,
    target_market: str,
    language: str,
    mode: str,
) -> str:
    target_label = target_market_label(target_market)
    source_label = target_market_label(source_platform)
    language = (language or "English").strip() or "English"
    title_limit = 180 if target_market.lower() == "amazon" else 60 if target_market.lower() == "mercadolibre" else 120
    return f"""You are an ecommerce copywriter.

Return only valid JSON with these keys:
title: string
description: string
bullets: array of 5 short strings
alt_titles: array of 2-3 strings
search_keywords: array of 10-20 strings

Requirements:
- Write in {language}.
- Target market: {target_label}.
- Mode: {mode}.
- Keep the title under {title_limit} characters if possible.
- Do not invent certifications, accessories, or specs not supported by the product data.
- Make the copy conversion-oriented but truthful.
- If the target is Amazon, use Amazon-style bullets and a concise product description.
- If the target is Mercado Libre, keep the title and description natural for that marketplace.

Source draft from {source_label}:
Title: {source_listing.get("title", "")}
Description: {source_listing.get("description", "")}

Product summary:
{generator.product_summary(product)}
"""


def generate_ai_copy_bundle(
    product: dict[str, Any],
    source_platform: str,
    target_market: str,
    language: str,
    mode: str,
    app_cfg: dict[str, Any],
) -> dict[str, Any]:
    source_key = platform_to_preset_key(source_platform)
    result = copy_service.generate_copy(
        str(APP_DIR),
        product,
        app_cfg,
        target_market=(target_market or source_key),
        language=language,
        mode=mode,
    )
    result["source_platform"] = source_key
    return result


def save_copy_result(
    product: dict[str, Any],
    target_market: str,
    copy: dict[str, Any],
) -> dict[str, Any]:
    product = normalize_product_fields(product)
    target_key = (target_market or "").strip().lower() or "mercadolibre"
    copy_results = product.setdefault("copy_results", {})
    if isinstance(copy_results, dict):
        copy_results[target_key] = copy
    drafts = product.setdefault("drafts", {})
    if isinstance(drafts, dict):
        draft = drafts.setdefault(target_key, {})
        if isinstance(draft, dict):
            draft.update(
                {
                    "title": copy.get("title", ""),
                    "description": copy.get("description", ""),
                    "bullets": copy.get("bullets", []),
                    "search_terms": copy.get("search_keywords", []),
                    "language": copy.get("language", draft.get("language", "")),
                    "copy_source": "ai",
                    "copy_generated_at": collect_time_iso(),
                }
            )
            draft["status"] = draft_workflow_status(product, target_key)
    if target_key == "mercadolibre":
        overrides = product.setdefault("listing_overrides", {})
        if isinstance(overrides, dict):
            overrides["mercadolibre"] = {
                "title": copy.get("title", ""),
                "description": copy.get("description", ""),
                "alt_titles": copy.get("alt_titles", []),
                "search_keywords": copy.get("search_keywords", []),
                "language": copy.get("language", "English"),
            }
    return save_product(product)


def batch_generate_copy_for_products(
    product_ids: list[str],
    platform: str = "mercadolibre",
    language: str = "",
    mode: str = "rewrite",
) -> dict[str, Any]:
    target_platform = str(platform or "mercadolibre").strip().lower() or "mercadolibre"
    if target_platform not in PLATFORMS:
        return {"ok": False, "success_count": 0, "failed_count": 0, "items": [], "error": "不支持的平台"}
    language = str(language or ("Spanish" if target_platform == "mercadolibre" else "Russian")).strip()
    app_cfg = load_app_config()
    items: list[dict[str, Any]] = []
    for product_id in [str(item or "").strip() for item in product_ids if str(item or "").strip()]:
        row = {
            "product_id": product_id,
            "platform": target_platform,
            "ok": False,
            "status": "failed",
            "title": "",
            "warning": "",
            "error": "",
        }
        try:
            product = load_product_from_index(product_id, "")
            if not product:
                raise RuntimeError("商品不存在")
            source_platform = str((product.get("source") or {}).get("source_platform") or product.get("source_platform") or target_platform)
            result = generate_ai_copy_bundle(product, source_platform, target_platform, language, mode, app_cfg)
            if result.get("warning"):
                raise RuntimeError(str(result.get("warning")))
            copy_payload = {**(result.get("copy") or {}), "language": result.get("language", language), "source_platform": result.get("source_platform", source_platform), "mode": result.get("mode", mode)}
            saved = save_copy_result(product, result.get("target_market") or target_platform, copy_payload)
            draft = ((saved.get("drafts") or {}).get(target_platform) or {}) if isinstance(saved.get("drafts"), dict) else {}
            row.update(
                {
                    "ok": True,
                    "status": draft.get("status") or "copy_ready",
                    "title": draft.get("title") or "",
                    "warning": result.get("warning") or "",
                    "product": saved,
                }
            )
        except Exception as exc:
            row["error"] = str(exc)
        items.append(row)
    return {
        "ok": True,
        "platform": target_platform,
        "language": language,
        "total": len(items),
        "success_count": sum(1 for item in items if item.get("ok")),
        "failed_count": sum(1 for item in items if not item.get("ok")),
        "items": items,
        "message": f"成功 {sum(1 for item in items if item.get('ok'))}/{len(items)}，失败 {sum(1 for item in items if not item.get('ok'))}。",
        "productsIndex": load_products_index(),
    }


def apply_product_drafts_to_plan(product: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    overrides = product.get("listing_overrides", {}) if isinstance(product.get("listing_overrides"), dict) else {}
    drafts = product.get("drafts", {}) if isinstance(product.get("drafts"), dict) else {}
    for platform_key, platform_state in plan.get("platforms", {}).items():
        listing = platform_state.get("listing", {})
        if not isinstance(listing, dict):
            continue
        draft = drafts.get(platform_key) if isinstance(drafts.get(platform_key), dict) else {}
        override = overrides.get(platform_key) if isinstance(overrides.get(platform_key), dict) else {}
        for field in ["title", "description", "language"]:
            value = draft.get(field) or override.get(field)
            if value:
                listing[field] = value
        if draft.get("bullets"):
            listing["bullets"] = draft.get("bullets")
        if draft.get("search_terms"):
            listing["search_keywords"] = draft.get("search_terms")
            listing["attribute_keywords"] = draft.get("search_terms")
    return plan


def build_image_prompt_pack(
    product: dict[str, Any],
    platform: str,
    selected_image_ids: list[str] | None = None,
    include_bullets: bool = True,
    include_description: bool = True,
    target_language: str = "",
) -> str:
    copy = build_copy_preview(product, platform, load_app_config())
    listing = copy.get("listing", {})
    pool = _source_pool_items(product)
    selected_ids = {str(item).strip() for item in (selected_image_ids or []) if str(item).strip()}
    images = [item for item in pool if str(item.get("id") or "").strip() in selected_ids] if selected_ids else pool
    if not images:
        images = _source_only_pool_items(product)
    bullets = normalize_list(product.get("selling_points")) if include_bullets else []
    description = str(listing.get("description") or product.get("description") or "").strip() if include_description else ""
    language = str(target_language or listing.get("language") or "").strip()
    lines = [
        "ChatGPT 生图提示词包",
        f"产品名: {product.get('name', '')}",
        f"品牌: {product.get('brand', '')}",
        f"品类: {product.get('category', '')}",
        f"目标语言: {language or '按目标平台'}",
        f"核心卖点: {'，'.join(bullets[:6])}",
        f"平台文案: {listing.get('title', '')}",
        f"平台描述: {description}",
        "原图:",
    ]
    for item in images[:8]:
        lines.append(
            "- "
            + " | ".join(
                [
                    str(item.get("id") or ""),
                    str(item.get("origin") or ""),
                    str(item.get("usage") or ""),
                    str(item.get("path") or item.get("url") or item.get("preview_url") or ""),
                ]
            )
        )
    lines.extend(
        [
            "",
            "目标：生成适合海外电商平台的本地化商品图。",
            "要求：保持真实外观、材质、比例和核心卖点，不要加入原图没有的配件或虚假认证。",
        ]
    )
    if copy.get("warning"):
        lines.append(f"提示: {copy['warning']}")
    return "\n".join(lines)


def test_ai_channel(channel: str, channel_config: dict[str, Any]) -> dict[str, Any]:
    channel = (channel or "text").strip().lower()
    if isinstance(channel_config.get("text_ai"), dict) or isinstance(channel_config.get("image_ai"), dict):
        section = "image_ai" if channel == "image" else "text_ai"
        channel_config = channel_config.get(section) if isinstance(channel_config.get(section), dict) else {}
    api_key = str(channel_config.get("api_key") or "").strip()
    base_url = str(channel_config.get("base_url") or "").strip().rstrip("/")
    model = str(channel_config.get("model") or "").strip()
    platform = str(channel_config.get("platform") or "OpenAI").strip()
    if not api_key:
        raise RuntimeError("请先填写 API Key。")
    from openai import OpenAI

    timeout_seconds = AI_IMAGE_REQUEST_TIMEOUT_SECONDS if channel == "image" else AI_TEXT_REQUEST_TIMEOUT_SECONDS
    kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout_seconds}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    client.models.list()
    return {
        "ok": True,
        "channel": channel,
        "platform": platform,
        "model": model,
        "masked_key": mask_secret(api_key),
        "message": f"{platform or channel} 测试成功：接口可以连接。",
        "next_action": "可以保存配置并继续使用 AI 生成功能。",
    }


def build_mercadolibre_auth_link(app_id: str, redirect_uri: str) -> dict[str, str]:
    if not app_id or not redirect_uri:
        raise RuntimeError("请先填写 Mercado Libre 的 client_id 和 redirect_uri。")
    parsed = urllib.parse.urlparse(str(redirect_uri or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise RuntimeError("REDIRECT_URI_MUST_BE_HTTPS：Mercado Libre Developers 要求 Redirect URI 使用 https://")
    verifier, challenge = publisher.generate_pkce_pair()
    url = (
        "https://global-selling.mercadolibre.com/authorization?"
        f"response_type=code&client_id={urllib.parse.quote(app_id)}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
        f"&code_challenge={urllib.parse.quote(challenge)}&code_challenge_method=S256"
    )
    config = load_store_config()
    config.setdefault("mercadolibre", {})["code_verifier"] = verifier
    config["mercadolibre"]["redirect_uri"] = redirect_uri
    config["mercadolibre"]["app_id"] = app_id
    save_store_config(config)
    return {"url": url, "code_verifier": verifier}


def preview_mercadolibre_auth_link(app_id: str, redirect_uri: str) -> str:
    if not app_id or not redirect_uri:
        raise RuntimeError("请先填写 Mercado Libre 的 client_id 和 redirect_uri。")
    _, challenge = publisher.generate_pkce_pair()
    return (
        "https://global-selling.mercadolibre.com/authorization?"
        f"response_type=code&client_id={urllib.parse.quote(app_id)}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
        f"&code_challenge={urllib.parse.quote(challenge)}&code_challenge_method=S256"
    )


def _mercadolibre_app_secret(store: dict[str, Any]) -> str:
    return str(store.get("app_secret") or store.get("client_secret") or "").strip()


def _update_store_auth_state(config: dict[str, Any], platform: str, updates: dict[str, Any]) -> dict[str, Any]:
    config = config if isinstance(config, dict) else {}
    platform = str(platform or "").strip().lower()
    platform_cfg = config.setdefault(platform, {})
    if isinstance(platform_cfg, dict):
        platform_cfg.update({key: value for key, value in updates.items() if value is not None})
    return config


def exchange_mercadolibre_code_from_body(body: dict[str, Any]) -> dict[str, Any]:
    config = load_store_config()
    ml = config.setdefault("mercadolibre", {})
    app_id = str(body.get("app_id") or ml.get("app_id") or "").strip()
    app_secret = str(body.get("app_secret") or body.get("client_secret") or _mercadolibre_app_secret(ml)).strip()
    redirect_uri = str(body.get("redirect_uri") or ml.get("redirect_uri") or "").strip()
    code_verifier = str(body.get("code_verifier") or ml.get("code_verifier") or "").strip()
    code_or_url = str(body.get("code_or_url") or body.get("code") or "").strip()
    if not code_or_url:
        raise RuntimeError("请先粘贴包含 code= 的回调地址，或直接粘贴授权 code。")
    if not code_verifier:
        raise RuntimeError("CODE_VERIFIER_MISSING：请重新生成授权链接后再换 token。")
    try:
        result = publisher.exchange_mercadolibre_code(app_id, app_secret, redirect_uri, code_or_url, code_verifier)
        token = str(result.get("access_token") or "").strip()
        shop_name = ""
        if token:
            try:
                shop_name = publisher.fetch_mercadolibre_shop_name(token)
            except Exception:
                shop_name = ""
        ml["app_id"] = app_id
        ml["app_secret"] = app_secret
        ml["client_secret"] = ml.get("client_secret") or app_secret
        ml["redirect_uri"] = redirect_uri
        ml["access_token"] = token
        if result.get("refresh_token"):
            ml["refresh_token"] = str(result.get("refresh_token") or "").strip()
        ml["shop_name"] = shop_name or str(result.get("user_id") or "").strip() or ml.get("shop_name", "")
        ml["user_id"] = str(result.get("user_id") or ml.get("user_id") or "").strip()
        ml["site_id"] = str(body.get("site_id") or ml.get("site_id") or "MLM").strip() or "MLM"
        ml.update(_store_auth_result_fields("mercadolibre", "测试成功", ml.get("shop_name") or ml.get("user_id") or token))
        ml["auth_error_code"] = ""
        ml["auth_error_message"] = ""
        append_ml_auth_test_log(
            "exchange_code",
            "success",
            {"redirect_uri": redirect_uri, "code_present": bool(code_or_url)},
            {"status": "success", "masked_account": ml.get("auth_masked_account") or "", "checked_at": ml.get("auth_checked_at") or ""},
            next_action="code 已使用，不要长期保存。继续测试 user_info 和 refresh token。",
        )
        return {
            "platform": "mercadolibre",
            "status": "测试成功",
            "shop_name": ml.get("shop_name") or "",
            "user_info_checked": True,
            "user_info": {
                "user_id": ml.get("user_id") or "",
                "shop_name": ml.get("shop_name") or "",
                "site_id": ml.get("site_id") or "",
            },
            "masked_account": ml.get("auth_masked_account") or "",
            "checked_at": ml.get("auth_checked_at") or "",
            "storeAuthSummary": summarize_store_auth_states(config),
            "message": "Mercado Libre 授权成功，已自动读取用户信息。",
            "next_action": "授权成功。下一步可直接在授权页点击“立即刷新类目缓存”，同步 Mercado Libre 官方类目和必填属性。",
        }
    finally:
        if "code_verifier" in ml:
            ml.pop("code_verifier", None)
        save_store_config(config)


def refresh_mercadolibre_token_from_body(body: dict[str, Any]) -> dict[str, Any]:
    config = load_store_config()
    ml = config.setdefault("mercadolibre", {})
    app_id = str(body.get("app_id") or ml.get("app_id") or "").strip()
    app_secret = str(body.get("app_secret") or body.get("client_secret") or _mercadolibre_app_secret(ml)).strip()
    refresh_token = str(body.get("refresh_token") or ml.get("refresh_token") or "").strip()
    if not app_id or not app_secret or not refresh_token:
        raise RuntimeError("请先填写 App ID、App Secret 和 Refresh Token。")
    result = publisher.refresh_mercadolibre_token(app_id, app_secret, refresh_token)
    token = str(result.get("access_token") or "").strip()
    shop_name = ""
    if token:
        try:
            shop_name = publisher.fetch_mercadolibre_shop_name(token)
        except Exception:
            shop_name = ""
    ml["app_id"] = app_id
    ml["app_secret"] = app_secret
    ml["client_secret"] = ml.get("client_secret") or app_secret
    ml["refresh_token"] = str(result.get("refresh_token") or refresh_token).strip()
    ml["access_token"] = token
    ml["shop_name"] = shop_name or ml.get("shop_name", "")
    ml.update(_store_auth_result_fields("mercadolibre", "测试成功", ml.get("shop_name") or token))
    ml["auth_error_code"] = ""
    ml["auth_error_message"] = ""
    save_store_config(config)
    return {
        "platform": "mercadolibre",
        "status": "测试成功",
        "shop_name": ml.get("shop_name") or "",
        "masked_account": ml.get("auth_masked_account") or "",
        "checked_at": ml.get("auth_checked_at") or "",
        "storeAuthSummary": summarize_store_auth_states(config),
        "message": "Mercado Libre token 已刷新。",
        "next_action": "Token 已刷新。下一步可直接在授权页点击“立即刷新类目缓存”，同步 Mercado Libre 官方类目和必填属性。",
    }


def test_store_auth(platform: str, scope: str = "") -> dict[str, Any]:
    platform = (platform or "").strip().lower()
    scope = (scope or "").strip().lower()
    config = load_store_config()
    try:
        if platform == "mercadolibre":
            token = str(config.get("mercadolibre", {}).get("access_token") or "").strip()
            if not token:
                raise RuntimeError("请先填写 Mercado Libre access token，或通过授权链接换取 token。")
            name = publisher.fetch_mercadolibre_shop_name(token)
            store = config.setdefault("mercadolibre", {})
            store["shop_name"] = name or store.get("shop_name", "")
            store.update(_store_auth_result_fields("mercadolibre", "测试成功", name or token))
            store["auth_error_code"] = ""
            store["auth_error_message"] = ""
        elif platform == "wildberries":
            wb = config.setdefault("wildberries", {})
            token_key = {
                "content": "content_token",
                "prices": "prices_token",
                "stocks": "stocks_token",
                "marketplace": "marketplace_token",
            }.get(scope, "content_token")
            token = str(wb.get(token_key) or wb.get("content_token") or "").strip()
            if not token:
                raise RuntimeError("请先填写 Wildberries Token。")
            name = publisher.fetch_wildberries_shop_name(token)
            wb["shop_name"] = name or wb.get("shop_name", "")
            wb.update(_store_auth_result_fields("wildberries", "测试成功", name or mask_secret(token)))
            wb["auth_error_code"] = ""
            wb["auth_error_message"] = ""
        elif platform == "ozon":
            ozon = config.get("ozon", {})
            client_id = str(ozon.get("client_id") or "").strip()
            api_key = str(ozon.get("api_key") or "").strip()
            if not client_id or not api_key:
                raise RuntimeError("请先填写 Ozon Client ID 和 API Key。")
            if scope == "category" and not str(ozon.get("category_id") or "").strip():
                raise RuntimeError("请先填写 Ozon category_id，再测试类目读取。")
            name = publisher.fetch_ozon_shop_name(client_id, api_key)
            config.setdefault("ozon", {})["shop_name"] = name or config.get("ozon", {}).get("shop_name", "")
            config["ozon"].update(_store_auth_result_fields("ozon", "测试成功", name or client_id))
            config["ozon"]["auth_error_code"] = ""
            config["ozon"]["auth_error_message"] = ""
        else:
            raise RuntimeError("不支持的平台。")
    except Exception as exc:
        error_message = str(exc)
        error_code = store_auth_failure_code(platform, error_message)
        next_action = _auth_next_action(platform, "测试失败", error_code, error_message)
        store = config.setdefault(platform, {})
        store.update(_store_auth_result_fields(platform, "测试失败", store.get("auth_masked_account") or store.get("shop_name") or "", error_code, error_message, next_action))
        save_store_config(config)
        raise RuntimeError(f"测试失败：{error_message}") from exc
    save_store_config(config)
    return {
        "ok": True,
        "platform": platform,
        "scope": scope,
        "shop_name": str(config.get(platform, {}).get("shop_name") or "已授权店铺"),
        "masked_account": str(config.get(platform, {}).get("auth_masked_account") or ""),
        "checked_at": str(config.get(platform, {}).get("auth_checked_at") or ""),
        "status": str(config.get(platform, {}).get("auth_status") or "ok"),
        "message": "测试成功：授权可用。",
        "storeAuthSummary": summarize_store_auth_states(config),
    }


def _pricing_exchange_rate_config() -> dict[str, Any]:
    pricing = load_app_config().get("pricing_defaults")
    cfg = pricing if isinstance(pricing, dict) else {}
    default_cfg = default_app_config()["pricing_defaults"]
    return {
        "api_url": str(cfg.get("exchange_rate_api_url") or default_cfg["exchange_rate_api_url"]).strip(),
        "timeout_seconds": max(1, min(30, int(pricing_service.number_value(cfg.get("exchange_rate_timeout_seconds"), 5) or 5))),
        "cache_ttl_seconds": max(0, int(pricing_service.number_value(cfg.get("exchange_rate_cache_ttl_seconds"), 3600) or 3600)),
    }


def _extract_usd_rates(payload: Any) -> dict[str, float]:
    rates: dict[str, float] = {}
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote") or item.get("currency") or "").upper()
            rate = pricing_service.number_value(item.get("rate"))
            if quote and rate > 0:
                rates[quote] = rate
    elif isinstance(payload, dict):
        raw_rates = payload.get("rates")
        if isinstance(raw_rates, dict):
            rates.update({str(key).upper(): pricing_service.number_value(value) for key, value in raw_rates.items() if pricing_service.number_value(value) > 0})
        elif isinstance(payload.get("conversion_rates"), dict):
            raw_conversion_rates = payload.get("conversion_rates")
            rates.update({str(key).upper(): pricing_service.number_value(value) for key, value in raw_conversion_rates.items() if pricing_service.number_value(value) > 0})
        elif isinstance(payload.get("data"), list):
            rates.update(_extract_usd_rates(payload.get("data")))
        quote = str(payload.get("quote") or "").upper()
        rate = pricing_service.number_value(payload.get("rate"))
        if quote and rate > 0:
            rates[quote] = rate
    return rates


def fetch_pricing_exchange_rates(force_refresh: bool = False) -> dict[str, Any]:
    cfg = _pricing_exchange_rate_config()
    api_url = cfg["api_url"]
    if not api_url:
        return {"ok": False, "error": "汇率 API URL 未配置，请在系统设置里填写。", "source": "config"}
    now = time.time()
    cache_key = api_url
    cached = EXCHANGE_RATE_CACHE.get(cache_key)
    if not force_refresh and isinstance(cached, dict) and cfg["cache_ttl_seconds"] > 0:
        if now - float(cached.get("fetched_at_ts") or 0) < cfg["cache_ttl_seconds"]:
            return {**cached["result"], "cached": True}
    try:
        request = urllib.request.Request(api_url, headers={"Accept": "application/json", "User-Agent": "ChampionERP/1.0"})
        with urllib.request.urlopen(request, timeout=cfg["timeout_seconds"]) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"实时汇率获取失败：{exc}", "source": api_url}
    rates = _extract_usd_rates(payload)
    usd_cny = rates.get("CNY")
    mxn_usd = rates.get("MXN")
    rub_usd = rates.get("RUB")
    if not usd_cny or not mxn_usd:
        return {"ok": False, "error": "实时汇率响应缺少 CNY 或 MXN 汇率。", "source": api_url, "raw": payload}
    rub_cny = (float(rub_usd) / float(usd_cny)) if rub_usd and usd_cny else 0.0
    result = {
        "ok": True,
        "source": api_url,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "cached": False,
        "rates": {
            "usd_cny_rate": round(float(usd_cny), 6),
            "mxn_usd_rate": round(float(mxn_usd), 6),
            "rub_usd_rate": round(float(rub_usd or 0), 6),
            "rub_cny_rate": round(float(rub_cny), 6),
        },
        "raw": payload,
    }
    EXCHANGE_RATE_CACHE[cache_key] = {"fetched_at_ts": now, "result": result}
    return result


def calculate_price(input_data: dict[str, Any]) -> dict[str, Any]:
    source = dict(input_data) if isinstance(input_data, dict) else {}
    has_manual_rates = source.get("usd_cny_rate") not in (None, "") and source.get("mxn_usd_rate") not in (None, "")
    exchange_mode = str(source.get("exchange_rate_mode") or ("manual" if has_manual_rates else "live")).strip().lower()
    exchange_rates: dict[str, Any] | None = None
    if exchange_mode != "manual":
        exchange_rates = fetch_pricing_exchange_rates(bool(source.get("force_exchange_rate_refresh")))
        if not exchange_rates.get("ok"):
            return {"ok": False, "error": exchange_rates.get("error") or "实时汇率获取失败", "exchange_rates": exchange_rates}
        rates = exchange_rates.get("rates") if isinstance(exchange_rates.get("rates"), dict) else {}
        source["usd_cny_rate"] = rates.get("usd_cny_rate")
        source["mxn_usd_rate"] = rates.get("mxn_usd_rate")
        source["rub_cny_rate"] = rates.get("rub_cny_rate")
    result = pricing_service.pricing_result(source)
    if exchange_rates:
        result["exchange_rates"] = exchange_rates
        result["exchange_rate_mode"] = "live"
    else:
        result["exchange_rates"] = {
            "ok": True,
            "source": "manual",
            "rates": {
                "usd_cny_rate": source.get("usd_cny_rate"),
                "mxn_usd_rate": source.get("mxn_usd_rate"),
                "rub_cny_rate": source.get("rub_cny_rate"),
            },
        }
        result["exchange_rate_mode"] = "manual"
    result.setdefault("suggested_price", result.get("suggested_price_mxn", 0))
    result.setdefault("reverse_price", result.get("reverse_price_mxn", 0))
    result.setdefault("profit", result.get("profit_cny", 0))
    result.setdefault("profit_rate", result.get("profit_percent", 0))
    result.setdefault("foreign_price", result.get("sale_price_usd", 0))
    result.setdefault("expected_profit", result.get("profit_cny", 0))
    result.setdefault("net_profit", result.get("profit_cny", 0))
    return result

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


def validate_mercadolibre_draft(product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "mercadolibre")
    store = config.get("mercadolibre", {}) if isinstance(config.get("mercadolibre"), dict) else {}
    summary = _required_attribute_summary(product, "mercadolibre")
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    auth_status, auth_next = _masked_auth_status("mercadolibre", config)
    title_limit = int(load_app_config().get("mercadolibre_title_limit") or 60)
    title = str(draft.get("title") or "").strip()
    description = str(draft.get("description") or "").strip()
    category_id = str(draft.get("category_id") or "").strip()
    category_path = str(draft.get("category_path") or "").strip()
    attrs = draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {}
    pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
    images = _draft_images(product, "mercadolibre", draft)
    if auth_status in {"未配置", "已保存，未测试", "测试失败", "Token 过期", "权限不足", "被限流"}:
        code = "AUTH_TOKEN_EXPIRED" if auth_status == "Token 过期" else "AUTH_NOT_CONFIGURED"
        errors.append(precheck_item(code, "auth", f"Mercado Libre 授权状态：{auth_status}", "error", auth_next or "前往授权页测试授权"))
    if not title:
        errors.append(precheck_item("TITLE_MISSING", "title", "缺少标题", "error", "前往商品编辑页补齐标题"))
    elif len(title) > title_limit:
        errors.append(precheck_item("TITLE_TOO_LONG", "title", f"标题长度超过 {title_limit} 字符限制", "error", "压缩 Mercado Libre 标题长度"))
    if not description:
        errors.append(precheck_item("DESCRIPTION_MISSING", "description", "缺少描述", "error", "前往商品编辑页补齐描述"))
    if not category_id:
        errors.append(precheck_item("CATEGORY_MISSING", "category_id", "缺少 Mercado Libre 类目 ID", "error", "前往类目属性页选择类目"))
    elif not category_path:
        warnings.append(precheck_item("CATEGORY_PATH_MISSING", "category_path", "类目路径为空，建议重新选择本地类目缓存", "warning", "前往类目属性页重新选择类目"))
    if summary["missing"]:
        for field in summary["missing"]:
            attr_id = str(field).split(".", 1)[-1]
            errors.append(precheck_item("REQUIRED_ATTRIBUTE_MISSING", field, f"缺少必填属性：{attr_id}", "error", "前往类目属性页补齐必填属性"))
    if not str(draft.get("brand") or "").strip():
        errors.append(precheck_item("BRAND_MISSING", "brand", "Brand 为空", "error", "前往类目属性页确认 Brand"))
    if not str(draft.get("model") or "").strip():
        errors.append(precheck_item("MODEL_MISSING", "model", "Model 为空", "error", "前往类目属性页确认 Model"))
    if not str(draft.get("sku") or product.get("sku") or "").strip():
        errors.append(precheck_item("SKU_MISSING", "sku", "SKU 为空", "error", "前往商品编辑页填写 SKU"))
    try:
        if float(str(draft.get("price") or "0").strip() or "0") <= 0:
            raise ValueError
    except Exception:
        errors.append(precheck_item("PRICE_MISSING", "price", "价格缺失或无效", "error", "前往核价页计算并应用售价"))
    try:
        if int(float(str(draft.get("stock") or "0").strip() or "0")) <= 0:
            raise ValueError
    except Exception:
        errors.append(precheck_item("STOCK_MISSING", "stock", "库存缺失或无效", "error", "前往商品编辑页填写库存"))
    if not images:
        errors.append(precheck_item("IMAGE_MISSING", "images", "缺少商品图片", "error", "前往图片池导入并勾选图片"))
    if images and not _has_main_image(product, "mercadolibre", draft):
        errors.append(precheck_item("MAIN_IMAGE_MISSING", "images", "缺少主图", "error", "前往图片池设置主图"))
    if images and any(not str(image).startswith(("http://", "https://", "/file?path=")) for image in images):
        warnings.append(precheck_item("IMAGE_NOT_UPLOADED", "images", "存在尚未上传的平台图片引用", "warning", "真实发布前确认图片可访问或已上传平台"))
    for field in ("length_cm", "width_cm", "height_cm"):
        if not str(pkg.get(field) or "").strip():
            errors.append(precheck_item("PACKAGE_DIMENSIONS_MISSING", f"package_dimensions.{field}", f"{field} 缺失", "error", "前往核价页或类目属性页补齐尺寸"))
    if not str(pkg.get("weight_kg") or "").strip():
        errors.append(precheck_item("WEIGHT_MISSING", "package_dimensions.weight_kg", "重量缺失", "error", "前往核价页或类目属性页补齐重量"))
    if not str(pricing.get("suggested_price") or "").strip() and not str(draft.get("price") or "").strip():
        errors.append(precheck_item("PRICING_NOT_APPLIED", "pricing", "尚未应用核价结果", "error", "前往核价页应用售价"))
    elif not str(pricing.get("suggested_price") or "").strip():
        warnings.append(precheck_item("PRICING_NOT_APPLIED", "pricing", "当前使用草稿售价，建议回核价页确认已应用最新核价结果", "warning", "前往核价页复核售价"))

    def review_item_resolved(item: str) -> bool:
        field = str(item or "").strip()
        attr_id = field.split(".", 1)[-1] if field.startswith("attributes.") else field
        package_map = {
            "PACKAGE_LENGTH": "length_cm",
            "PACKAGE_WIDTH": "width_cm",
            "PACKAGE_HEIGHT": "height_cm",
            "PACKAGE_WEIGHT": "weight_kg",
        }
        if attr_id in package_map and str(pkg.get(package_map[attr_id]) or "").strip():
            return True
        return bool(attr_id and str(attrs.get(attr_id) or "").strip())

    need_review: list[str] = []
    for item in draft.get("validation_errors") or []:
        if isinstance(item, dict):
            if str(item.get("code") or "") != "NEED_REVIEW_ATTRIBUTES":
                continue
            raw_field = str(item.get("field") or "").strip()
        else:
            raw_field = str(item or "").strip()
        if raw_field and not review_item_resolved(raw_field):
            need_review.append(raw_field)
    if need_review:
        errors.append(precheck_item("NEED_REVIEW_ATTRIBUTES", "attributes", f"仍有 {len(need_review)} 个属性待复核", "error", "前往类目属性页确认 need_review 字段"))
    if not str(draft.get("upc") or draft.get("gtin") or draft.get("barcode") or product.get("upc") or product.get("gtin") or product.get("barcode") or "").strip():
        allow_gtin_exemption = bool(draft.get("allow_gtin_exemption") or draft.get("gtin_exempt") or config.get("listing", {}).get("allow_gtin_exemption"))
        if allow_gtin_exemption:
            warnings.append(precheck_item("UPC_MISSING", "upc", "UPC / GTIN 为空，已按配置允许豁免", "warning", "确认 Mercado Libre 类目允许 EMPTY_GTIN_REASON"))
        else:
            errors.append(precheck_item("UPC_MISSING", "upc", "UPC / GTIN 为空，且未确认允许豁免", "error", "前往商品编辑页分配 UPC 或显式确认豁免"))
    terms_raw = product.get("marketplace_terms", {}).get("mercadolibre") if isinstance(product.get("marketplace_terms"), dict) else {}
    sale_terms: Any = []
    if isinstance(terms_raw, dict):
        sale_terms = terms_raw.get("sale_terms") or terms_raw.get("warranty") or []
    elif isinstance(terms_raw, list):
        sale_terms = terms_raw
    if not sale_terms:
        sale_terms = draft.get("sale_terms") or draft.get("warranty") or []
    if not sale_terms:
        sale_terms = config.get("listing", {}).get("mercadolibre_sale_terms") if isinstance(config.get("listing"), dict) else []
    if not sale_terms:
        errors.append(precheck_item("SALE_TERMS_MISSING", "sale_terms", "sale_terms / warranty 尚未配置完整", "error", "前往平台属性页补齐售后条款"))
    draft_shipping = draft.get("shipping") if isinstance(draft.get("shipping"), dict) else {}
    logistic_type = str(draft_shipping.get("logistic_type") or draft_shipping.get("mode") or config.get("listing", {}).get("mercadolibre_logistic_type") or "").strip()
    if not logistic_type:
        errors.append(precheck_item("LOGISTIC_MODE_MISSING", "logistic_type", "未读取 shipping / logistics mode", "error", "发布前在店铺后台确认物流模式，不要自动修改后台模式"))
    return {"platform": "mercadolibre", "ok": not errors, "errors": errors, "warnings": warnings, "checked_at": collect_time_iso()}


def validate_wildberries_draft(product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "wildberries")
    store = config.get("wildberries", {}) if isinstance(config.get("wildberries"), dict) else {}
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    auth_status, auth_next = _masked_auth_status("wildberries", config)
    if auth_status in {"未配置", "测试失败", "Token 过期", "权限不足", "被限流"}:
        errors.append(precheck_item("AUTH_NOT_CONFIGURED", "auth", f"Wildberries 授权状态：{auth_status}", "error", auth_next or "前往授权页测试 Token"))
    if not str(draft.get("title") or "").strip():
        errors.append(precheck_item("TITLE_MISSING", "title", "缺少标题", "error", "前往商品编辑页补齐标题"))
    if not str(draft.get("description") or "").strip():
        errors.append(precheck_item("DESCRIPTION_MISSING", "description", "缺少描述", "error", "前往商品编辑页补齐描述"))
    if not str(draft.get("category_id") or store.get("subject_id") or "").strip():
        errors.append(precheck_item("CATEGORY_MISSING", "category_id", "缺少 Wildberries Subject ID", "error", "前往类目属性页选择类目"))
    if not str(draft.get("brand") or "").strip():
        errors.append(precheck_item("BRAND_MISSING", "brand", "品牌为空", "error", "前往类目属性页确认 Brand"))
    if not str(draft.get("model") or "").strip():
        errors.append(precheck_item("MODEL_MISSING", "model", "型号为空", "error", "前往类目属性页确认 Model"))
    if not str(draft.get("sku") or product.get("sku") or "").strip():
        errors.append(precheck_item("SKU_MISSING", "sku", "SKU 为空", "error", "前往商品编辑页填写 SKU"))
    if not str(draft.get("price") or "").strip():
        errors.append(precheck_item("PRICE_MISSING", "price", "价格缺失", "error", "前往核价页应用 Wildberries 价格"))
    if not str(draft.get("stock") or "").strip():
        errors.append(precheck_item("STOCK_MISSING", "stock", "库存缺失", "error", "前往商品编辑页填写库存"))
    images = _draft_images(product, "wildberries", draft)
    if not images:
        errors.append(precheck_item("IMAGE_MISSING", "images", "缺少图片", "error", "前往图片池导入图片"))
    elif len(images) < 1:
        errors.append(precheck_item("IMAGE_MISSING", "images", "图片数量不足", "error", "前往图片池补图"))
    pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    for field in ("length_cm", "width_cm", "height_cm"):
        if not str(pkg.get(field) or "").strip():
            errors.append(precheck_item("PACKAGE_DIMENSIONS_MISSING", f"package_dimensions.{field}", f"{field} 缺失", "error", "前往核价页补齐尺寸"))
    if not str(pkg.get("weight_kg") or "").strip():
        errors.append(precheck_item("WEIGHT_MISSING", "package_dimensions.weight_kg", "重量缺失", "error", "前往核价页补齐重量"))
    pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
    if not str(pricing.get("suggested_price") or "").strip():
        errors.append(precheck_item("PRICING_NOT_APPLIED", "pricing", "尚未应用核价结果", "error", "前往核价页应用 Wildberries 价格"))
    need_review = [str(item) for item in draft.get("validation_errors") or [] if str(item).strip()]
    if need_review:
        warnings.append(precheck_item("NEED_REVIEW_ATTRIBUTES", "attributes", f"仍有 {len(need_review)} 个属性待复核", "warning", "前往类目属性页确认属性"))
    if not str(draft.get("language") or "").strip():
        warnings.append(precheck_item("LANGUAGE_MISSING", "language", "俄语标题/描述尚未确认", "warning", "发布前确认 Wildberries 文案语言"))
    return {"platform": "wildberries", "ok": not errors, "errors": errors, "warnings": warnings, "checked_at": collect_time_iso()}


def validate_ozon_draft(product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, "ozon")
    store = config.get("ozon", {}) if isinstance(config.get("ozon"), dict) else {}
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    auth_status, auth_next = _masked_auth_status("ozon", config)
    if auth_status in {"未配置", "测试失败", "Token 过期", "权限不足", "被限流"}:
        errors.append(precheck_item("AUTH_NOT_CONFIGURED", "auth", f"Ozon 授权状态：{auth_status}", "error", auth_next or "前往授权页测试授权"))
    if not str(draft.get("title") or "").strip():
        errors.append(precheck_item("TITLE_MISSING", "title", "缺少标题", "error", "前往商品编辑页补齐标题"))
    if not str(draft.get("description") or "").strip():
        errors.append(precheck_item("DESCRIPTION_MISSING", "description", "缺少描述", "error", "前往商品编辑页补齐描述"))
    if not str(draft.get("category_id") or store.get("category_id") or "").strip():
        errors.append(precheck_item("CATEGORY_MISSING", "category_id", "缺少 Ozon Category / Type ID", "error", "前往类目属性页选择类目"))
    if not str(draft.get("brand") or "").strip():
        errors.append(precheck_item("BRAND_MISSING", "brand", "品牌为空", "error", "前往类目属性页确认 Brand"))
    if not str(draft.get("model") or "").strip():
        errors.append(precheck_item("MODEL_MISSING", "model", "型号为空", "error", "前往类目属性页确认 Model"))
    if not str(draft.get("sku") or product.get("sku") or "").strip():
        errors.append(precheck_item("SKU_MISSING", "sku", "SKU 为空", "error", "前往商品编辑页填写 SKU"))
    if not str(draft.get("price") or "").strip():
        errors.append(precheck_item("PRICE_MISSING", "price", "价格缺失", "error", "前往核价页应用 Ozon 价格"))
    if not str(draft.get("stock") or "").strip():
        errors.append(precheck_item("STOCK_MISSING", "stock", "库存缺失", "error", "前往商品编辑页填写库存"))
    images = _draft_images(product, "ozon", draft)
    if not images:
        errors.append(precheck_item("IMAGE_MISSING", "images", "缺少图片", "error", "前往图片池导入图片"))
    pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    for field in ("length_cm", "width_cm", "height_cm"):
        if not str(pkg.get(field) or "").strip():
            errors.append(precheck_item("PACKAGE_DIMENSIONS_MISSING", f"package_dimensions.{field}", f"{field} 缺失", "error", "前往核价页补齐尺寸"))
    if not str(pkg.get("weight_kg") or "").strip():
        errors.append(precheck_item("WEIGHT_MISSING", "package_dimensions.weight_kg", "重量缺失", "error", "前往核价页补齐重量"))
    pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
    if not str(pricing.get("suggested_price") or "").strip():
        errors.append(precheck_item("PRICING_NOT_APPLIED", "pricing", "尚未应用核价结果", "error", "前往核价页应用 Ozon 价格"))
    need_review = [str(item) for item in draft.get("validation_errors") or [] if str(item).strip()]
    if need_review:
        warnings.append(precheck_item("NEED_REVIEW_ATTRIBUTES", "attributes", f"仍有 {len(need_review)} 个属性待复核", "warning", "前往类目属性页确认属性"))
    return {"platform": "ozon", "ok": not errors, "errors": errors, "warnings": warnings, "checked_at": collect_time_iso()}


def validate_platform_draft(product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    if platform == "mercadolibre":
        return validate_mercadolibre_draft(product, config)
    if platform == "wildberries":
        return validate_wildberries_draft(product, config)
    if platform == "ozon":
        return validate_ozon_draft(product, config)
    return {
        "platform": platform,
        "ok": False,
        "errors": [precheck_item("UNSUPPORTED_PLATFORM", "platform", "不支持的平台", "error", "切换到受支持的平台")],
        "warnings": [],
        "checked_at": collect_time_iso(),
    }


def apply_precheck_to_product(product: dict[str, Any], platform: str, precheck: dict[str, Any], status: str = "") -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    draft = deepcopy(_draft_for_platform(normalized, platform))
    combined = list(precheck.get("errors") or []) + list(precheck.get("warnings") or [])
    draft["validation_errors"] = combined
    draft["publish_status"] = status or ("ready" if precheck.get("ok") else "not_ready")
    publish_logs = draft.get("publish_logs") if isinstance(draft.get("publish_logs"), list) else []
    publish_logs.insert(
        0,
        {
            "time": collect_time_iso(),
            "status": draft["publish_status"],
            "error_count": len(precheck.get("errors") or []),
            "warning_count": len(precheck.get("warnings") or []),
        },
    )
    draft["publish_logs"] = publish_logs[:20]
    normalized.setdefault("drafts", {})[platform] = draft
    normalized["publish_preview"] = {
        **(normalized.get("publish_preview") if isinstance(normalized.get("publish_preview"), dict) else {}),
        platform: precheck,
    }
    return normalize_product_fields(normalized)


def _sanitize_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_l = str(key).lower()
            if any(token in key_l for token in ("token", "secret", "api_key", "apikey", "authorization")):
                sanitized[key] = mask_secret(item)
            else:
                sanitized[key] = _sanitize_for_log(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_log(item) for item in value]
    return value


def _publish_artifact_paths(platform: str) -> tuple[Path, Path]:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    token = f"{stamp}-{platform}-{os.getpid()}"
    payload_path = OUTPUT_DIR / "publish_artifacts" / f"{token}-payload.json"
    response_path = OUTPUT_DIR / "publish_artifacts" / f"{token}-response.json"
    return payload_path, response_path


def _write_publish_artifacts(platform: str, payload: Any, response: Any) -> tuple[str, str]:
    payload_path, response_path = _publish_artifact_paths(platform)
    write_json(payload_path, _sanitize_for_log(payload))
    write_json(response_path, _sanitize_for_log(response))
    return str(payload_path), str(response_path)


def _product_id_for_log(product: dict[str, Any], platform: str) -> str:
    draft = _draft_for_platform(product, platform)
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    return str(source.get("source_url") or product.get("source_url") or draft.get("sku") or product.get("sku") or product.get("name") or "").strip()


def append_ml_publish_log(
    product: dict[str, Any],
    status: str,
    started_at: str,
    payload: Any,
    response: Any,
    error_code: str = "",
    error_message: str = "",
    field_errors: dict[str, Any] | None = None,
    next_action: str = "",
) -> tuple[str, str]:
    payload_path, response_path = _write_publish_artifacts("mercadolibre", payload, response)
    draft = _draft_for_platform(product, "mercadolibre")
    append_publish_log(
        {
            "product_id": _product_id_for_log(product, "mercadolibre"),
            "platform": "mercadolibre",
            "draft_id": str(draft.get("sku") or ""),
            "status": status,
            "started_at": started_at,
            "finished_at": collect_time_iso(),
            "request_payload_path": payload_path,
            "response_body_path": response_path,
            "error_code": error_code,
            "error_message": error_message,
            "field_errors": field_errors or {},
            "next_action": next_action,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "shop": "mercadolibre",
            "sku": str(draft.get("sku") or ""),
            "error": error_message,
            "image": normalize_list(product.get("source_image_urls"))[:1],
        }
    )
    return payload_path, response_path


def _mercadolibre_test_error_code(message: str) -> str:
    text = str(message or "").lower()
    if "ssl" in text or "unexpected_eof" in text or "eof occurred" in text:
        return "network_tls_failed"
    if "winerror 10013" in text or "urlopen error" in text and "socket" in text:
        return "NETWORK_BLOCKED"
    if "timed out" in text or "timeout" in text:
        return "NETWORK_TIMEOUT"
    if "invalid access token" in text or "invalid_token" in text:
        return "INVALID_ACCESS_TOKEN"
    if "expired" in text and "token" in text:
        return "TOKEN_EXPIRED"
    if "invalid_grant" in text:
        return "INVALID_GRANT"
    if "real_category_required" in text or "mock/seed" in text or "测试类目" in text or "category_id 为空" in text:
        return "REAL_CATEGORY_REQUIRED"
    if "403" in text or "permission" in text or "forbidden" in text:
        return "PERMISSION_DENIED"
    return store_auth_failure_code("mercadolibre", message).upper()


def append_ml_auth_test_log(
    test_type: str,
    status: str,
    request_payload: Any | None = None,
    response_body: Any | None = None,
    error_code: str = "",
    error_message: str = "",
    next_action: str = "",
) -> tuple[str, str]:
    payload_path, response_path = _write_publish_artifacts(
        "mercadolibre-07d",
        request_payload or {"test_type": test_type},
        response_body or {},
    )
    append_publish_log(
        {
            "platform": "mercadolibre",
            "test_type": test_type,
            "status": status,
            "checked_at": collect_time_iso(),
            "started_at": collect_time_iso(),
            "finished_at": collect_time_iso(),
            "request_payload_path": payload_path,
            "response_body_path": response_path,
            "error_code": error_code,
            "error_message": error_message,
            "field_errors": {},
            "next_action": next_action,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "shop": "mercadolibre",
        }
    )
    return payload_path, response_path


def _mercadolibre_category_id_from_product(product: dict[str, Any]) -> str:
    draft = _draft_for_platform(product, "mercadolibre")
    return str(draft.get("category_id") or product.get("category_id") or "").strip()


def _is_mock_mercadolibre_category_id(category_id: str) -> bool:
    value = str(category_id or "").strip().lower()
    return value in {"mock", "mock_test", "seed_test"} or value.startswith("mock_") or value.startswith("seed_")


def _mercadolibre_required_attr_ids(attrs: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for attr in attrs if isinstance(attrs, list) else []:
        if isinstance(attr, dict) and attr.get("required"):
            attr_id = str(attr.get("id") or "").strip()
            if attr_id:
                ids.append(attr_id)
    return ids


def run_mercadolibre_07d_test(mode: str, product: dict[str, Any] | None = None, category_id_override: str = "") -> dict[str, Any]:
    mode = str(mode or "auth_link").strip().lower()
    product = normalize_product_fields(product or load_product())
    config = load_store_config()
    ml = config.setdefault("mercadolibre", {})
    token = str(ml.get("access_token") or "").strip()
    refresh_token = str(ml.get("refresh_token") or "").strip()
    app_id = str(ml.get("app_id") or ml.get("client_id") or "").strip()
    app_secret = _mercadolibre_app_secret(ml)
    redirect_uri = str(ml.get("redirect_uri") or "").strip()
    result: dict[str, Any] = {
        "ok": True,
        "platform": "mercadolibre",
        "mode": mode,
        "checked_at": collect_time_iso(),
        "real_publish_called": False,
        "message": "当前仍未真实发布。",
    }

    try:
        if mode == "auth_link":
            url = preview_mercadolibre_auth_link(app_id, redirect_uri)
            parsed = urllib.parse.urlparse(url)
            query = urllib.parse.parse_qs(parsed.query)
            result.update(
                {
                    "auth_url": url,
                    "redirect_uri": query.get("redirect_uri", [""])[0],
                    "redirect_uri_matches_config": query.get("redirect_uri", [""])[0] == redirect_uri,
                    "client_id_present": bool(query.get("client_id", [""])[0]),
                }
            )
            append_ml_auth_test_log("auth_link", "success", {"redirect_uri": redirect_uri}, result, next_action="打开授权链接并完成回调，或手动粘贴 code。")
            return result

        if mode == "user_info":
            if not token:
                raise RuntimeError("Mercado Libre access_token 为空。")
            data = publisher.request_json("GET", "https://api.mercadolibre.com/users/me", token)
            if not isinstance(data, dict):
                raise RuntimeError(f"Mercado Libre users/me 返回异常: {data}")
            ml["user_id"] = str(data.get("id") or ml.get("user_id") or "").strip()
            ml["seller_id"] = str(data.get("id") or ml.get("seller_id") or "").strip()
            ml["nickname"] = str(data.get("nickname") or ml.get("nickname") or "").strip()
            ml["site_id"] = str(data.get("site_id") or ml.get("site_id") or "MLM").strip() or "MLM"
            ml["shop_name"] = ml.get("nickname") or ml.get("shop_name") or ml.get("user_id") or ""
            ml.update(_store_auth_result_fields("mercadolibre", "测试成功", ml.get("shop_name") or token))
            ml["auth_error_code"] = ""
            ml["auth_error_message"] = ""
            save_store_config(config)
            result.update(
                {
                    "status": "success",
                    "user_id_present": bool(ml.get("user_id")),
                    "seller_id_present": bool(ml.get("seller_id")),
                    "nickname_present": bool(ml.get("nickname")),
                    "site_id": ml.get("site_id") or "",
                    "storeAuthSummary": summarize_store_auth_states(config),
                }
            )
            append_ml_auth_test_log("user_info", "success", {"endpoint": "users/me"}, result, next_action="授权可用于后续类目、图片和 payload 测试。")
            return result

        if mode == "refresh_token":
            if not app_id or not app_secret or not refresh_token:
                raise RuntimeError("App ID、Client Secret 或 Refresh Token 缺失。")
            refreshed = refresh_mercadolibre_token_from_body({})
            result.update({"status": "success", **_sanitize_for_log(refreshed)})
            append_ml_auth_test_log("refresh_token", "success", {"grant_type": "refresh_token"}, result, next_action="刷新成功后重新测试用户信息。")
            return result

        if mode == "category_attrs":
            category_id = str(category_id_override or "").strip() or _mercadolibre_category_id_from_product(product)
            if not category_id:
                raise RuntimeError("drafts.mercadolibre.category_id 为空。")
            if _is_mock_mercadolibre_category_id(category_id):
                raise RuntimeError("REAL_CATEGORY_REQUIRED: 当前 category_id 是 mock/seed 测试类目，请先选择真实 Mercado Libre 类目，或手动输入真实 category_id。")
            path = publisher.mercadolibre_category_path(category_id, token)
            attrs = publisher.mercadolibre_category_attributes(category_id, token)
            required_ids = _mercadolibre_required_attr_ids(attrs)
            draft_attrs = _draft_for_platform(product, "mercadolibre").get("attributes")
            draft_attrs = draft_attrs if isinstance(draft_attrs, dict) else {}
            missing = [attr_id for attr_id in required_ids if not str(draft_attrs.get(attr_id) or "").strip()]
            result.update(
                {
                    "status": "success",
                    "category_id": category_id,
                    "category_path": path,
                    "required_count": len(required_ids),
                    "missing_required": missing,
                    "field_errors": [
                        precheck_item("REQUIRED_ATTRIBUTE_MISSING", f"attributes.{attr_id}", f"真实类目缺少必填属性：{attr_id}", "error", "前往类目属性页补齐")
                        for attr_id in missing
                    ],
                    "required_attributes": attrs[:80],
                }
            )
            append_ml_auth_test_log("category_attrs", "success" if not missing else "failed", {"category_id": category_id}, result, error_code="REQUIRED_ATTRIBUTE_MISSING" if missing else "", error_message=f"缺少 {len(missing)} 个真实必填属性" if missing else "", next_action="前往类目属性页补齐缺失属性" if missing else "真实类目属性读取成功。")
            return result

        if mode == "image_upload":
            candidates = _mercadolibre_image_candidates(product)
            if not candidates:
                error = precheck_item("IMAGE_NOT_FOUND", "images", "Mercado Libre 没有可用图片", "error", "在 07D 向导上传一张测试主图")
                result.update({"ok": False, "status": "failed", "error_code": error["code"], "error_message": error["message"], "next_action": error["next_action"], "errors": [error], "product": product})
                append_ml_auth_test_log("image_upload", "failed", {"image_count": 0}, result, error["code"], error["message"], error["next_action"])
                return result
            has_uploadable = any(_mercadolibre_picture_id(item) or _local_path_from_image_item(item) for item in candidates)
            if not has_uploadable:
                error = precheck_item("IMAGE_UNAVAILABLE", "images", "Mercado Libre 图片不是本地文件，无法执行真实图片上传测试", "error", "请使用 07D 上传测试主图入口上传一张本地图片")
                result.update({"ok": False, "status": "failed", "error_code": error["code"], "error_message": error["message"], "next_action": error["next_action"], "errors": [error], "product": product})
                append_ml_auth_test_log("image_upload", "failed", {"image_count": len(candidates)}, result, error["code"], error["message"], error["next_action"])
                return result
            auth = ensure_mercadolibre_auth_ready(config)
            if not auth.get("ok"):
                raise RuntimeError(auth.get("message") or "Mercado Libre 授权不可用。")
            upload = ensure_mercadolibre_pictures_uploaded(product, str(auth.get("token") or ""))
            if not upload.get("ok"):
                first = (upload.get("errors") or [{}])[0]
                result.update({"ok": False, "status": "failed", "errors": upload.get("errors") or [], "product": upload.get("product") or product})
                append_ml_auth_test_log("image_upload", "failed", {"image_count": len(_mercadolibre_image_candidates(product))}, result, str(first.get("code") or "IMAGE_UPLOAD_FAILED"), str(first.get("message") or "图片上传失败"), str(first.get("next_action") or "前往图片池修复图片"))
                return result
            result.update({"status": "success", "picture_refs": upload.get("picture_refs") or [], "product": upload.get("product") or product})
            append_ml_auth_test_log("image_upload", "success", {"image_count": len(_mercadolibre_image_candidates(product))}, result, next_action="图片上传测试成功，仍未真实发布。")
            return result

        if mode == "payload_generate":
            payload = build_mercadolibre_payload_preview(product, config)
            path = OUTPUT_DIR / "last_mercadolibre_payload.json"
            write_json(path, _sanitize_for_log(payload))
            draft = _draft_for_platform(product, "mercadolibre")
            draft_category_id = str(draft.get("category_id") or "").strip()
            sites_to_sell = payload.get("sites_to_sell") if isinstance(payload.get("sites_to_sell"), list) else []
            attributes = payload.get("attributes") if isinstance(payload.get("attributes"), list) else []
            picture_items = payload.get("pictures") if isinstance(payload.get("pictures"), list) else []
            if not picture_items:
                for site in sites_to_sell:
                    if isinstance(site, dict) and isinstance(site.get("pictures"), list):
                        picture_items = site.get("pictures") or []
                        break
            condition_present = bool(payload.get("condition")) or any(str(attr.get("id") or "") in {"ITEM_CONDITION", "CONDITION"} for attr in attributes if isinstance(attr, dict))
            pictures_present = bool(picture_items)
            pictures_use_ml_id = bool(picture_items) and all(isinstance(pic, dict) and bool(pic.get("id")) and not pic.get("source") for pic in picture_items)
            shipping_present = bool(payload.get("shipping")) or any(str(site.get("logistic_type") or "").strip() for site in sites_to_sell if isinstance(site, dict))
            required_checks = {
                "title": bool(payload.get("title")),
                "category_id": bool(payload.get("category_id")),
                "category_id_from_draft": bool(draft_category_id) and str(payload.get("category_id") or "").strip() == draft_category_id,
                "price": "price" in payload,
                "currency_id": bool(payload.get("currency_id")),
                "available_quantity": "available_quantity" in payload,
                "buying_mode": bool(payload.get("buying_mode")),
                "listing_type_id": bool(payload.get("listing_type_id")),
                "condition": condition_present,
                "pictures": pictures_present,
                "pictures_with_mercadolibre_id": pictures_use_ml_id,
                "attributes": bool(attributes),
                "sale_terms": bool(payload.get("sale_terms")),
                "shipping_or_logistics": shipping_present,
            }
            missing_keys = [key for key, present in required_checks.items() if not present]
            result.update({"ok": not missing_keys, "status": "success" if not missing_keys else "failed", "payload": _sanitize_for_log(payload), "path": str(path), "missing_keys": missing_keys})
            append_ml_auth_test_log("payload_generate", "success" if not missing_keys else "failed", {"platform": "mercadolibre"}, {"path": str(path), "missing_keys": missing_keys, "payload": _sanitize_for_log(payload)}, error_code="PAYLOAD_FIELD_MISSING" if missing_keys else "", error_message=", ".join(missing_keys), next_action="补齐 payload 缺失字段" if missing_keys else "payload 已生成，仍未真实发布。")
            return result

        if mode == "all":
            outputs = []
            for sub_mode in ("auth_link", "user_info", "category_attrs", "payload_generate"):
                try:
                    outputs.append(run_mercadolibre_07d_test(sub_mode, product))
                except Exception as exc:
                    outputs.append({"ok": False, "mode": sub_mode, "error": str(exc), "error_code": _mercadolibre_test_error_code(str(exc))})
            result["tests"] = outputs
            result["ok"] = all(item.get("ok", True) and item.get("status") != "failed" for item in outputs)
            return result

        raise RuntimeError(f"不支持的 07D 测试模式：{mode}")
    except Exception as exc:
        message = str(exc)
        code = _mercadolibre_test_error_code(message)
        status = "failed"
        if code == "NETWORK_BLOCKED":
            next_action = "当前环境无法访问 Mercado Libre，请换到允许外网 socket 的本机环境重试。"
        elif code == "REAL_CATEGORY_REQUIRED":
            next_action = "请先选择真实 Mercado Libre 类目，或在 07D 向导里手动输入真实 category_id。"
        else:
            next_action = _auth_next_action("mercadolibre", "测试失败", code, message)
        if mode in {"user_info", "refresh_token"} and code != "NETWORK_BLOCKED":
            ml.update(_store_auth_result_fields("mercadolibre", "测试失败", ml.get("shop_name") or ml.get("user_id") or "", code, message, next_action))
            save_store_config(config)
        response = {"ok": False, "platform": "mercadolibre", "mode": mode, "status": status, "error_code": code, "error_message": message, "next_action": next_action, "real_publish_called": False}
        append_ml_auth_test_log(mode, status, {"mode": mode}, response, code, message, next_action)
        return response


def _local_path_from_image_item(item: dict[str, Any]) -> Path | None:
    for key in ("path", "url", "preview_url"):
        value = str(item.get(key) or "").strip()
        if not value:
            continue
        if value.startswith("/file?"):
            parsed = urllib.parse.urlparse(value)
            path = urllib.parse.parse_qs(parsed.query).get("path", [""])[0]
            if path:
                candidate = Path(path)
                if candidate.exists():
                    return candidate
        if value.startswith("file:"):
            candidate = Path(urllib.parse.urlparse(value).path)
            if candidate.exists():
                return candidate
        if not value.startswith(("http://", "https://", "ml-id:")):
            candidate = Path(value)
            if candidate.exists():
                return candidate
    return None


def _mercadolibre_picture_id(item: dict[str, Any]) -> str:
    for key in ("platform_picture_id", "mercadolibre_picture_id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    uploads = item.get("platform_uploads") if isinstance(item.get("platform_uploads"), dict) else {}
    ml_upload = uploads.get("mercadolibre") if isinstance(uploads.get("mercadolibre"), dict) else {}
    return str(ml_upload.get("picture_id") or ml_upload.get("id") or "").strip()


def _mercadolibre_image_candidates(product: dict[str, Any]) -> list[dict[str, Any]]:
    pool = _source_pool_items(product)
    candidates = []
    for item in pool:
        platforms = [str(value).strip().lower() for value in (item.get("platforms") or [])]
        if platforms and "mercadolibre" not in platforms:
            continue
        if str(item.get("status") or "").strip().lower() == "empty":
            continue
        if item.get("selected") or item.get("is_main"):
            candidates.append(item)
    if not candidates:
        candidates = [
            item for item in pool
            if (not item.get("platforms") or "mercadolibre" in [str(value).strip().lower() for value in (item.get("platforms") or [])])
            and str(item.get("status") or "").strip().lower() != "empty"
        ]
    return sorted(candidates, key=lambda item: (0 if item.get("is_main") else 1, int(item.get("order") or 0)))


def ensure_mercadolibre_pictures_uploaded(product: dict[str, Any], token: str) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else {}
    pool = _source_pool_items(normalized)
    selected_ids = {str(item.get("id") or "") for item in _mercadolibre_image_candidates(normalized)}
    errors: list[dict[str, str]] = []
    picture_refs: list[str] = []
    if not selected_ids:
        errors.append(precheck_item("IMAGE_NOT_FOUND", "images", "Mercado Libre 没有可用图片", "error", "前往图片池选择主图或勾选 Mercado Libre 图片"))
        return {"ok": False, "product": normalized, "picture_refs": [], "errors": errors}
    updated_pool: list[dict[str, Any]] = []
    for item in pool:
        item = dict(item)
        if str(item.get("id") or "") not in selected_ids:
            updated_pool.append(item)
            continue
        picture_id = _mercadolibre_picture_id(item)
        if not picture_id and str(item.get("url") or "").startswith("ml-id:"):
            picture_id = str(item.get("url") or "").split(":", 1)[1].strip()
        if picture_id:
            item["platform_picture_id"] = picture_id
            item["mercadolibre_picture_id"] = picture_id
            item["upload_status"] = "uploaded"
            item.setdefault("uploaded_at", collect_time_iso())
            item["platform_uploads"] = {**(item.get("platform_uploads") if isinstance(item.get("platform_uploads"), dict) else {}), "mercadolibre": {"picture_id": picture_id, "upload_status": "uploaded", "uploaded_at": item.get("uploaded_at")}}
            picture_refs.append(f"ml-id:{picture_id}")
            updated_pool.append(item)
            continue
        local_path = _local_path_from_image_item(item)
        if not local_path:
            item["upload_status"] = "failed"
            item["upload_error"] = "图片不是本地文件，无法在真实发布前上传 Mercado Libre。"
            errors.append(precheck_item("IMAGE_UNAVAILABLE", "images", f"图片不可上传或不可访问：{item.get('id') or item.get('url') or item.get('path')}", "error", "前往图片池替换为本地可上传图片"))
            updated_pool.append(item)
            continue
        try:
            upload = publisher.upload_mercadolibre_picture(local_path, token)
            picture_id = str(upload.get("id") or upload.get("secure_url") or upload.get("url") or "").strip()
            if not picture_id:
                raise RuntimeError(f"Mercado Libre 图片上传未返回 picture id: {upload}")
            item["platform_picture_id"] = picture_id
            item["mercadolibre_picture_id"] = picture_id
            item["upload_status"] = "uploaded"
            item["uploaded_at"] = collect_time_iso()
            item["platform_uploads"] = {**(item.get("platform_uploads") if isinstance(item.get("platform_uploads"), dict) else {}), "mercadolibre": {"picture_id": picture_id, "upload_status": "uploaded", "uploaded_at": item["uploaded_at"]}}
            picture_refs.append(f"ml-id:{picture_id}")
        except Exception as exc:
            item["upload_status"] = "failed"
            item["upload_error"] = str(exc)
            errors.append(precheck_item("IMAGE_UPLOAD_FAILED", "images", mercadolibre_picture_upload_error_message(exc), "error", "检查图片文件格式/尺寸后重试"))
        updated_pool.append(item)
    source["image_pool"] = updated_pool
    normalized["source"] = source
    normalized.setdefault("drafts", {}).setdefault("mercadolibre", default_draft("mercadolibre"))["images"] = picture_refs
    normalized["source_image_urls"] = picture_refs
    saved = save_product(normalized)
    errors = compact_precheck_items(errors)
    return {"ok": not errors, "product": saved, "picture_refs": picture_refs, "errors": errors}


def ensure_mercadolibre_auth_ready(config: dict[str, Any]) -> dict[str, Any]:
    store = config.setdefault("mercadolibre", {})
    token = str(store.get("access_token") or "").strip()
    if not token:
        return {"ok": False, "error_code": "AUTH_NOT_CONFIGURED", "message": "Mercado Libre Access Token 为空", "next_action": "请先完成授权测试"}
    try:
        name = publisher.fetch_mercadolibre_shop_name(token)
        store["shop_name"] = name or store.get("shop_name", "")
        store.update(_store_auth_result_fields("mercadolibre", "测试成功", name or token))
        store["auth_error_code"] = ""
        store["auth_error_message"] = ""
        save_store_config(config)
        return {"ok": True, "token": token, "seller": name or store.get("user_id") or ""}
    except Exception as exc:
        message = str(exc)
        if publisher.is_mercadolibre_auth_error(exc) and str(store.get("refresh_token") or "").strip():
            try:
                refreshed = publisher.refresh_mercadolibre_token(str(store.get("app_id") or ""), str(store.get("app_secret") or ""), str(store.get("refresh_token") or ""))
                token = str(refreshed.get("access_token") or "").strip()
                store["access_token"] = token
                store["refresh_token"] = str(refreshed.get("refresh_token") or store.get("refresh_token") or "").strip()
                name = publisher.fetch_mercadolibre_shop_name(token)
                store["shop_name"] = name or store.get("shop_name", "")
                store.update(_store_auth_result_fields("mercadolibre", "测试成功", name or token))
                store["auth_error_code"] = ""
                store["auth_error_message"] = ""
                save_store_config(config)
                return {"ok": True, "token": token, "seller": name or store.get("user_id") or "", "refreshed": True}
            except Exception as refresh_exc:
                message = str(refresh_exc)
        code = store_auth_failure_code("mercadolibre", message)
        store.update(_store_auth_result_fields("mercadolibre", "测试失败", token))
        store["auth_error_code"] = code
        store["auth_error_message"] = message
        save_store_config(config)
        return {"ok": False, "error_code": "AUTH_TOKEN_EXPIRED" if "expired" in code.lower() or "expired" in message.lower() else "AUTH_INVALID", "message": message, "next_action": "请先完成授权测试或刷新 token"}


def mercadolibre_product_for_payload(product: dict[str, Any], picture_refs: list[str]) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    draft = _draft_for_platform(normalized, "mercadolibre")
    pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    normalized["category_id"] = str(draft.get("category_id") or "").strip()
    normalized["attributes"] = draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {}
    normalized["brand"] = str(draft.get("brand") or normalized.get("brand") or "Generic").strip()
    normalized["model"] = str(draft.get("model") or normalized.get("model") or "General").strip()
    normalized["sku"] = str(draft.get("sku") or normalized.get("sku") or "").strip()
    normalized["upc"] = str(draft.get("upc") or draft.get("gtin") or draft.get("barcode") or normalized.get("upc") or "").strip()
    normalized["name"] = str(draft.get("title") or normalized.get("name") or "").strip()
    normalized["weight_kg"] = str(pkg.get("weight_kg") or normalized.get("weight_kg") or "").strip()
    normalized["dimensions"] = " x ".join(str(pkg.get(key) or "").strip() for key in ("length_cm", "width_cm", "height_cm") if str(pkg.get(key) or "").strip())
    normalized["source_image_urls"] = picture_refs
    return normalized


def mercadolibre_config_for_payload(config: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
    cfg = deepcopy(config)
    draft = _draft_for_platform(product, "mercadolibre")
    pkg = draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}
    cfg.setdefault("mercadolibre", {})["category_id"] = str(draft.get("category_id") or "").strip()
    listing = cfg.setdefault("listing", {})
    for key, value in {
        "mercadolibre_price": draft.get("price"),
        "price": draft.get("price"),
        "stock": draft.get("stock"),
        "sku": draft.get("sku"),
        "upc": draft.get("upc") or draft.get("gtin") or draft.get("barcode"),
        "model": draft.get("model"),
        "mercadolibre_title": draft.get("title"),
        "package_length_cm": pkg.get("length_cm"),
        "package_width_cm": pkg.get("width_cm"),
        "package_height_cm": pkg.get("height_cm"),
        "package_weight_kg": pkg.get("weight_kg"),
        "mercadolibre_attributes": draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {},
    }.items():
        if value not in (None, ""):
            listing[key] = value
    if isinstance(draft.get("sale_terms"), list) and draft.get("sale_terms"):
        listing["mercadolibre_sale_terms"] = draft.get("sale_terms")
    shipping = draft.get("shipping") if isinstance(draft.get("shipping"), dict) else {}
    logistic_type = str(shipping.get("logistic_type") or shipping.get("mode") or "").strip()
    if logistic_type:
        listing["mercadolibre_logistic_type"] = logistic_type
    return cfg


def build_mercadolibre_payload_preview(product: dict[str, Any], config: dict[str, Any], picture_refs: list[str] | None = None) -> dict[str, Any]:
    refs = picture_refs if picture_refs is not None else image_pool_refs_for_platform(product, "mercadolibre")
    payload_product = mercadolibre_product_for_payload(product, refs)
    payload_config = mercadolibre_config_for_payload(config, payload_product)
    return build_publish_payload(payload_product, "mercadolibre", payload_config)


def mercadolibre_real_publish(product: dict[str, Any], confirm: bool) -> dict[str, Any]:
    started_at = collect_time_iso()
    if not confirm:
        return {"ok": False, "status": "confirmation_required", "error": "真实发布需要二次确认。"}
    product = normalize_product_fields(product)
    config = load_store_config()
    auth = ensure_mercadolibre_auth_ready(config)
    if not auth.get("ok"):
        error = precheck_item(auth.get("error_code") or "AUTH_INVALID", "auth", auth.get("message") or "Mercado Libre 授权不可用", "error", auth.get("next_action") or "请先完成授权测试")
        precheck = {"platform": "mercadolibre", "ok": False, "errors": [error], "warnings": [], "checked_at": collect_time_iso()}
        updated = apply_precheck_to_product(product, "mercadolibre", precheck, status="not_ready")
        append_ml_publish_log(updated, "not_ready", started_at, {"precheck": precheck}, {"ok": False, "status": "not_ready"}, error["code"], error["message"], _field_error_map([error]), error["next_action"])
        saved = save_product(updated)
        return compact_publish_failure_response("not_ready", error["message"], saved, precheck=precheck, next_action=error["next_action"])
    precheck = validate_mercadolibre_draft(product, config)
    if not precheck.get("ok"):
        updated = apply_precheck_to_product(product, "mercadolibre", precheck, status="not_ready")
        first = (precheck.get("errors") or [{}])[0]
        append_ml_publish_log(updated, "not_ready", started_at, {"precheck": precheck}, {"ok": False, "status": "not_ready"}, str(first.get("code") or ""), "；".join(str(item.get("message") or "") for item in precheck.get("errors") or [] if isinstance(item, dict)), _field_error_map(list(precheck.get("errors") or []) + list(precheck.get("warnings") or [])), str(first.get("next_action") or ""))
        saved = save_product(updated)
        return compact_publish_failure_response("not_ready", "发布前预检未通过", saved, precheck=precheck, next_action=str(first.get("next_action") or ""))
    upload = ensure_mercadolibre_pictures_uploaded(product, str(auth.get("token") or ""))
    product = upload.get("product") or product
    if not upload.get("ok"):
        precheck = {"platform": "mercadolibre", "ok": False, "errors": upload.get("errors") or [], "warnings": [], "checked_at": collect_time_iso()}
        updated = apply_precheck_to_product(product, "mercadolibre", precheck, status="not_ready")
        first = (precheck.get("errors") or [{}])[0]
        append_ml_publish_log(updated, "not_ready", started_at, {"precheck": precheck}, {"ok": False, "status": "image_upload_failed"}, str(first.get("code") or "IMAGE_UPLOAD_FAILED"), "；".join(str(item.get("message") or "") for item in precheck.get("errors") or [] if isinstance(item, dict)), _field_error_map(precheck.get("errors") or []), str(first.get("next_action") or ""))
        saved = save_product(updated)
        return compact_publish_failure_response("not_ready", "图片上传失败，已禁止真实发布", saved, precheck=precheck, next_action=str(first.get("next_action") or "前往图片池替换或重新上传图片"))
    payload = build_mercadolibre_payload_preview(product, config, upload.get("picture_refs") or [])
    payload_path = OUTPUT_DIR / "last_mercadolibre_payload.json"
    write_json(payload_path, _sanitize_for_log(payload))
    payload_errors = validate_publish_payload("mercadolibre", payload, config)
    if payload_errors:
        errors = [precheck_item("PAYLOAD_INVALID", "payload", message, "error", "前往对应页面补齐字段") for message in payload_errors]
        precheck = {"platform": "mercadolibre", "ok": False, "errors": errors, "warnings": [], "checked_at": collect_time_iso()}
        updated = apply_precheck_to_product(product, "mercadolibre", precheck, status="not_ready")
        append_ml_publish_log(updated, "not_ready", started_at, payload, {"ok": False, "errors": payload_errors}, "PAYLOAD_INVALID", "，".join(payload_errors), {"payload": payload_errors}, "前往对应页面补齐字段")
        saved = save_product(updated)
        return compact_publish_failure_response("not_ready", "，".join(payload_errors), saved, payload_path=str(payload_path), next_action="前往对应页面补齐字段")
    try:
        result = publisher.publish_mercadolibre(payload, str(auth.get("token") or ""))
        ok = isinstance(result, dict) and bool(result.get("id") or result.get("ok") or result.get("success"))
        status = "real_publish_success" if ok else "real_publish_failed"
        updated = apply_precheck_to_product(product, "mercadolibre", precheck, status=status)
        append_ml_publish_log(updated, status, started_at, payload, result, "" if ok else "REAL_PUBLISH_FAILED", "" if ok else "Mercado Libre 未返回成功状态", {}, "" if ok else "查看响应后重试")
        saved = save_product(updated)
        return {"ok": ok, "status": status, "result": _sanitize_for_log(result), "payload": _sanitize_for_log(payload), "payload_path": str(payload_path), "product": saved}
    except Exception as exc:
        parsed = publisher.parse_mercadolibre_error(exc)
        mapped = map_mercadolibre_publish_error(parsed)
        errors = [
            precheck_item(str(parsed.get("error") or "REAL_PUBLISH_FAILED"), field, str(values[0] if isinstance(values, list) and values else mapped["summary"]), "error", "前往对应字段修复后重试")
            for field, values in mapped["field_errors"].items()
        ] or [precheck_item(str(parsed.get("error") or "REAL_PUBLISH_FAILED"), "publish", mapped["summary"], "error", "查看字段映射并重试")]
        updated = apply_precheck_to_product(product, "mercadolibre", {"platform": "mercadolibre", "ok": False, "errors": errors, "warnings": [], "checked_at": collect_time_iso()}, status="real_publish_failed")
        append_ml_publish_log(updated, "real_publish_failed", started_at, payload, mapped, str(parsed.get("error") or "REAL_PUBLISH_FAILED"), mapped["summary"], mapped["field_errors"], "按字段提示修复后重试")
        saved = save_product(updated)
        return compact_publish_failure_response("real_publish_failed", mapped["summary"], saved, error_map=mapped, payload_path=str(payload_path), next_action="按字段提示修复后重试")


def map_mercadolibre_publish_error(parsed: dict[str, Any]) -> dict[str, Any]:
    field_errors: dict[str, Any] = {}
    for field in parsed.get("missing_fields") or []:
        field = publisher.normalize_mercadolibre_error_field(str(field))
        if field:
            field_errors.setdefault(field, [])
    missing_attrs = [str(item) for item in parsed.get("missing_attributes") or [] if str(item).strip()]
    if missing_attrs:
        field_errors["attributes"] = missing_attrs

    guidance = {
        "auth": "Mercado Libre 授权无效或已过期，请前往授权页刷新 token。",
        "logistic_type": "当前类目不支持店铺后台的 remote/me1 发货模式，请换一个可发墨西哥的类目，不要随意改物流方式。",
        "attributes": "请在平台属性区域补齐缺失属性后重试。",
        "pictures": "请重新检查图片上传结果，优先使用已导入并可访问的商品图片。",
        "title": "请把 Mercado Libre 标题控制在 60 个字符以内。",
        "sale_terms": "请检查 Warranty type / Warranty time 等售后条款。",
        "category_id": "请填写或更换 Mercado Libre 类目 ID。",
        "price": "请先完成核价并填写发布价格。",
        "stock": "请检查库存 available_quantity 是否为有效正数。",
    }
    hints = [guidance[key] for key in field_errors if key in guidance]
    summary = "；".join(hints) or str(parsed.get("message") or parsed.get("error") or "发布失败")
    return {
        "summary": summary,
        "field_errors": field_errors,
        "missing_attributes": missing_attrs,
        "missing_fields": list(field_errors.keys()),
        "parsed": parsed,
    }


class ProjectPublishingAdapter:
    def resolve_category(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        product = normalize_product_fields(product)
        local_categories = product.get("local_platform_categories") if isinstance(product.get("local_platform_categories"), dict) else {}
        platform_category = local_categories.get(platform) if isinstance(local_categories, dict) else None
        if isinstance(platform_category, dict):
            category_id = str(platform_category.get("category_id") or platform_category.get("platform_category_id") or "").strip()
        else:
            category_id = str(platform_category or "").strip()
        if not category_id:
            if platform == "mercadolibre":
                category_id = str(product.get("category_id") or config.get("mercadolibre", {}).get("category_id") or "").strip()
            elif platform == "wildberries":
                category_id = str(product.get("wb_subject_id") or config.get("wildberries", {}).get("subject_id") or "").strip()
            elif platform == "ozon":
                category_id = str(product.get("ozon_category_id") or config.get("ozon", {}).get("category_id") or "").strip()
        if category_id:
            if platform == "mercadolibre":
                product["category_id"] = category_id
            elif platform == "wildberries":
                product["wb_subject_id"] = category_id
            elif platform == "ozon":
                product["ozon_category_id"] = category_id
        return product

    def validate_required_attributes(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> list[str]:
        return []

    def publish(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        return publish_product(product, platform, config)


PUBLISHING_BUS = PublishingBus(
    PUBLISHING_JOB_DIR,
    adapters={
        "mercadolibre": ProjectPublishingAdapter(),
        "wildberries": ProjectPublishingAdapter(),
        "ozon": ProjectPublishingAdapter(),
    },
)
CATEGORY_REFRESH_JOBS: dict[str, dict[str, Any]] = {}
CATEGORY_REFRESH_LOCK = threading.Lock()


def publish_product(product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
    product = normalize_product_fields(product)
    platform = str(platform or "").strip().lower()
    precheck = validate_platform_draft(product, platform, config)
    if not precheck.get("ok"):
        updated = apply_precheck_to_product(product, platform, precheck, status="not_ready")
        payload_path, response_path = _write_publish_artifacts(platform, {"precheck": precheck}, {"ok": False, "status": "not_ready"})
        log_entry = {
            "product_id": str(updated.get("source_url") or updated.get("sku") or updated.get("name") or ""),
            "platform": platform,
            "draft_id": str(_draft_for_platform(updated, platform).get("sku") or ""),
            "status": "not_ready",
            "started_at": precheck.get("checked_at") or collect_time_iso(),
            "finished_at": collect_time_iso(),
            "request_payload_path": payload_path,
            "response_body_path": response_path,
            "error_code": (precheck.get("errors") or [{}])[0].get("code", ""),
            "error_message": "；".join(str(item.get("message") or "") for item in precheck.get("errors") or [] if isinstance(item, dict)),
            "field_errors": _field_error_map(list(precheck.get("errors") or []) + list(precheck.get("warnings") or [])),
            "next_action": (precheck.get("errors") or [{}])[0].get("next_action", ""),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "shop": platform,
            "sku": config.get("listing", {}).get("sku", ""),
            "error": "；".join(str(item.get("message") or "") for item in precheck.get("errors") or [] if isinstance(item, dict)),
            "image": normalize_list(updated.get("source_image_urls"))[:1],
        }
        append_publish_log(log_entry)
        saved = save_product(updated)
        return {
            "ok": False,
            "status": "not_ready",
            "error": log_entry["error_message"] or "发布前预检未通过",
            "precheck": precheck,
            "error_map": {"summary": log_entry["error_message"] or "发布前预检未通过", "field_errors": log_entry["field_errors"]},
            "product": saved,
        }

    product = apply_precheck_to_product(product, platform, precheck, status="local_precheck_passed")
    payload = build_publish_payload(product, platform, config)
    errors = validate_publish_payload(platform, payload, config)
    if errors:
        updated = apply_precheck_to_product(
            product,
            platform,
            {
                "platform": platform,
                "ok": False,
                "errors": [precheck_item("PAYLOAD_INVALID", "payload", message, "error", "前往对应页面补齐字段") for message in errors],
                "warnings": [],
                "checked_at": collect_time_iso(),
            },
            status="not_ready",
        )
        payload_path, response_path = _write_publish_artifacts(platform, payload, {"ok": False, "errors": errors})
        append_publish_log(
            {
                "product_id": str(updated.get("source_url") or updated.get("sku") or updated.get("name") or ""),
                "platform": platform,
                "draft_id": str(_draft_for_platform(updated, platform).get("sku") or ""),
                "status": "not_ready",
                "started_at": collect_time_iso(),
                "finished_at": collect_time_iso(),
                "request_payload_path": payload_path,
                "response_body_path": response_path,
                "error_code": "PAYLOAD_INVALID",
                "error_message": "，".join(errors),
                "field_errors": {"payload": errors},
                "next_action": "前往对应页面补齐字段",
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "shop": platform,
                "sku": config.get("listing", {}).get("sku", ""),
                "error": "，".join(errors),
                "image": normalize_list(updated.get("source_image_urls"))[:1],
            }
        )
        saved = save_product(updated)
        return {"ok": False, "status": "not_ready", "error": "，".join(errors), "payload": payload, "product": saved}

    draft = _draft_for_platform(product, platform)
    started_at = collect_time_iso()
    result: Any
    status = "publishing"
    if platform == "mercadolibre":
        try:
            result = publisher.publish_mercadolibre(payload, config["mercadolibre"].get("access_token", ""))
            status = "real_publish_success" if isinstance(result, dict) and (result.get("id") or result.get("ok") or result.get("success")) else "real_publish_failed"
        except Exception as exc:
            parsed = publisher.parse_mercadolibre_error(exc)
            mapped = map_mercadolibre_publish_error(parsed)
            payload_path, response_path = _write_publish_artifacts(platform, payload, mapped)
            updated = apply_precheck_to_product(
                product,
                platform,
                {
                    "platform": platform,
                    "ok": False,
                    "errors": [
                        precheck_item("REAL_PUBLISH_FAILED", field, str(values[0] if isinstance(values, list) and values else mapped["summary"]), "error", "前往对应字段修复后重试")
                        for field, values in mapped["field_errors"].items()
                    ] or [precheck_item("REAL_PUBLISH_FAILED", "publish", mapped["summary"], "error", "查看字段映射并重试")],
                    "warnings": [],
                    "checked_at": collect_time_iso(),
                },
                status="real_publish_failed",
            )
            append_publish_log(
                {
                    "product_id": str(updated.get("source_url") or updated.get("sku") or updated.get("name") or ""),
                    "platform": platform,
                    "draft_id": str(draft.get("sku") or ""),
                    "status": "real_publish_failed",
                    "started_at": started_at,
                    "finished_at": collect_time_iso(),
                    "request_payload_path": payload_path,
                    "response_body_path": response_path,
                    "error_code": str(parsed.get("error") or "REAL_PUBLISH_FAILED"),
                    "error_message": mapped["summary"],
                    "field_errors": mapped["field_errors"],
                    "next_action": "按字段提示修复后重试",
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "shop": platform,
                    "sku": config.get("listing", {}).get("sku", ""),
                    "error": mapped["summary"],
                    "image": normalize_list(updated.get("source_image_urls"))[:1],
                }
            )
            saved = save_product(updated)
            return {"ok": False, "status": "real_publish_failed", "error": mapped["summary"], "error_map": mapped, "payload": payload, "product": saved}
    elif platform == "wildberries":
        result = {"ok": False, "status": "ready_for_real_publish", "message": "Wildberries 真实发布前，建议先确认授权与类目接口。当前保留本地预检与 payload。"}
        status = "ready_for_real_publish"
    else:
        result = {"ok": False, "status": "ready_for_real_publish", "message": "Ozon 真实发布接口仍需真实授权验证。当前保留本地预检与 payload。"}
        status = "ready_for_real_publish"

    ok = bool(result.get("id") or result.get("ok") or result.get("success")) if isinstance(result, dict) else True
    final_status = "real_publish_success" if ok and platform == "mercadolibre" else status if not ok else "mock_success"
    payload_path, response_path = _write_publish_artifacts(platform, payload, result)
    updated = apply_precheck_to_product(product, platform, precheck, status=final_status if ok else status)
    append_publish_log(
        {
            "product_id": str(updated.get("source_url") or updated.get("sku") or updated.get("name") or ""),
            "platform": platform,
            "draft_id": str(draft.get("sku") or ""),
            "status": final_status if ok else status,
            "started_at": started_at,
            "finished_at": collect_time_iso(),
            "request_payload_path": payload_path,
            "response_body_path": response_path,
            "error_code": "" if ok else str(result.get("error_code") or result.get("status") or ""),
            "error_message": "" if ok else str(result.get("error") or result.get("message") or json.dumps(result, ensure_ascii=False)),
            "field_errors": _field_error_map(updated["drafts"][platform].get("validation_errors") or []),
            "next_action": "" if ok else "查看 payload 与日志，再决定是否真实发布",
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "shop": platform,
            "sku": config.get("listing", {}).get("sku", ""),
            "error": "" if ok else str(result.get("error") or result.get("message") or json.dumps(result, ensure_ascii=False)),
            "image": normalize_list(updated.get("source_image_urls"))[:1],
        }
    )
    saved = save_product(updated)
    return {"ok": ok, "status": final_status if ok else status, "result": result, "payload": payload, "product": saved, "precheck": precheck}


def save_task_bundle(product: dict[str, Any], platform: str, count: int) -> dict[str, Any]:
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = TASK_DIR / stamp
    folder.mkdir(parents=True, exist_ok=True)
    image_paths = [Path(path) for path in normalize_list(product.get("source_images")) if Path(path).exists()][:5]
    prompt = generator.build_plan(product, [generator.PlatformPlan(key=platform_to_preset_key(platform), preset=list_presets()[platform_to_preset_key(platform)])])
    prompt_text = json.dumps(prompt, ensure_ascii=False, indent=2)
    prompt_file = folder / "task_prompt.json"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    metadata = {
        "productName": product.get("name", ""),
        "platform": platform,
        "count": count,
        "sourceCount": len(image_paths),
        "prompt": str(prompt_file),
    }
    write_json(folder / "metadata.json", metadata)
    return {"folder": str(folder), "prompt": str(prompt_file), "metadata": metadata}


def safe_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    raw = handler.rfile.read(length).decode("utf-8") if length else "{}"
    return json.loads(raw or "{}")


def html_page(active_page: str = "workbench") -> str:
    if FRONT_DIST_INDEX_PATH.exists():
        template = FRONT_DIST_INDEX_PATH.read_text(encoding="utf-8")
    elif WEB_TEMPLATE_PATH.exists():
        template = WEB_TEMPLATE_PATH.read_text(encoding="utf-8")
    else:
        template = HTML_TEMPLATE
    return template.replace("__ACTIVE_PAGE__", active_page)


HTML_TEMPLATE = ""
