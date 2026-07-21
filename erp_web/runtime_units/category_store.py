# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

from .category_refresh import (
    http_json,
    mercadolibre_category_attributes,
    mercadolibre_category_detail,
    mercadolibre_category_record,
)
from .ozon_category_api import fetch_ozon_category_record, search_ozon_categories


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
    from erp_web import db as erp_db
    from .runtime_common import APP_DIR

    erp_db.initialize_database(APP_DIR)


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


def _require_mercadolibre(platform: str) -> str:
    platform = str(platform or "mercadolibre").strip().lower()
    if platform != "mercadolibre":
        raise RuntimeError("当前只支持 Mercado Libre 实时类目搜索；其他平台未接入官方实时类目接口。")
    return platform


def _require_supported_category_platform(platform: str) -> str:
    platform = str(platform or "mercadolibre").strip().lower()
    if platform not in {"mercadolibre", "ozon"}:
        raise RuntimeError("当前只支持 Mercado Libre 和 Ozon 的实时类目搜索；该平台尚未接入官方实时类目接口。")
    return platform


def _mercadolibre_token() -> str:
    from .product_store import load_store_config

    return str((load_store_config().get("mercadolibre") or {}).get("access_token") or "").strip()


def _mercadolibre_site(site: str = "") -> str:
    from .product_store import load_store_config

    configured = str((load_store_config().get("mercadolibre") or {}).get("site_id") or "").strip()
    return str(site or configured or "MLM").strip().upper()


def _category_suggest_terms(product: dict[str, Any], platform: str = "mercadolibre") -> list[str]:
    from .product_store import normalize_product_fields
    from .publish_helpers import _draft_for_platform

    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, platform)
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


def _category_suggest_query(product: dict[str, Any], platform: str = "mercadolibre") -> str:
    from .product_store import normalize_product_fields
    from .publish_helpers import _draft_for_platform

    product = normalize_product_fields(product)
    draft = _draft_for_platform(product, platform)
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
    return " ".join(_category_suggest_terms(product, platform)[:8])


def _domain_discovery_url(site: str, query: str, limit: int) -> str:
    site = _mercadolibre_site(site)
    quoted_query = urllib.parse.quote(query)
    safe_limit = max(1, min(8, int(limit or 5)))
    if site == "CBT":
        return f"https://api.mercadolibre.com/marketplace/domain_discovery/search?q={quoted_query}&limit={safe_limit}"
    return f"https://api.mercadolibre.com/sites/{urllib.parse.quote(site)}/domain_discovery/search?q={quoted_query}&limit={safe_limit}"


def _path_text(record: dict[str, Any]) -> str:
    path = record.get("path_original") if isinstance(record.get("path_original"), list) else []
    if path:
        return " / ".join(str(item).strip() for item in path if str(item).strip())
    return str(record.get("category_path") or record.get("name_original") or record.get("category_id") or "").strip()


def fetch_category_record(platform: str, category_id: str, site: str = "", include_attributes: bool = False) -> dict[str, Any]:
    platform = _require_supported_category_platform(platform)
    if platform == "ozon":
        return fetch_ozon_category_record(category_id, include_attributes=include_attributes)
    category_id = str(category_id or "").strip()
    if not category_id:
        raise RuntimeError("缺少 Mercado Libre 类目 ID。")
    token = _mercadolibre_token()
    resolved_site = _mercadolibre_site(site)
    detail = mercadolibre_category_detail(category_id, token or None, http_client=http_json)
    attrs = (
        mercadolibre_category_attributes(category_id, token or None, http_client=http_json)
        if include_attributes
        else {"required": [], "optional": []}
    )
    return mercadolibre_category_record(detail, resolved_site, attrs)


def fetch_category_attributes(platform: str, category_id: str, site: str = "") -> dict[str, Any]:
    platform = _require_supported_category_platform(platform)
    record = fetch_category_record(platform, category_id, site=site, include_attributes=True)
    attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    required = list(attrs.get("required") or [])
    optional = list(attrs.get("optional") or [])
    return {
        "ok": True,
        "platform": platform,
        "site": record.get("site") or (_mercadolibre_site(site) if platform == "mercadolibre" else "global"),
        "source": f"{platform}_live",
        "category": record,
        "required": required,
        "optional": optional,
        "attributes": required + optional,
        "category_id": record.get("category_id") or category_id,
        "category_path": _path_text(record),
        "path": _path_text(record),
    }


def search_categories_live(platform: str, query: str, site: str = "", limit: int = 5) -> list[dict[str, Any]]:
    platform = _require_supported_category_platform(platform)
    query = str(query or "").strip()
    if not query:
        return []
    if platform == "ozon":
        return search_ozon_categories(query, limit=limit)
    token = _mercadolibre_token()
    resolved_site = _mercadolibre_site(site)
    data = http_json(_domain_discovery_url(resolved_site, query, limit), token or None)
    suggestions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(data if isinstance(data, list) else []):
        if not isinstance(item, dict):
            continue
        category_id = str(item.get("category_id") or "").strip()
        if not category_id or category_id in seen_ids:
            continue
        record = fetch_category_record("mercadolibre", category_id, site=resolved_site, include_attributes=False)
        name = str(item.get("category_name") or item.get("domain_name") or record.get("name_original") or category_id).strip()
        path = _path_text(record) or name
        suggestions.append(
            {
                "id": category_id,
                "category_id": category_id,
                "name": name,
                "path": path,
                "category_path": path,
                "path_ids": record.get("path_ids") if isinstance(record.get("path_ids"), list) else [],
                "site": resolved_site,
                "score": max(1, 100 - index * 5),
                "matched_terms": [query],
                "source": "mercadolibre_domain_discovery",
                "raw": {
                    "domain_discovery": item,
                    "category": record.get("raw") if isinstance(record.get("raw"), dict) else {},
                    "path_ids": record.get("path_ids") if isinstance(record.get("path_ids"), list) else [],
                },
            }
        )
        seen_ids.add(category_id)
        if len(suggestions) >= max(1, int(limit or 5)):
            break
    return suggestions


def suggest_category_ids(product: dict[str, Any], platform: str = "mercadolibre", site: str = "", limit: int = 5) -> dict[str, Any]:
    platform = _require_supported_category_platform(platform)
    resolved_site = _mercadolibre_site(site) if platform == "mercadolibre" else "global"
    query = _category_suggest_query(product, platform)
    suggestions = search_categories_live(platform, query, site=resolved_site, limit=max(1, int(limit or 5))) if query else []
    return {
        "ok": True,
        "platform": platform,
        "site": resolved_site,
        "query": query,
        "terms": _category_suggest_terms(product, platform)[:30],
        "suggestions": suggestions,
        "source": f"{platform}_live",
    }


__all__ = [
    "ensure_sqlite_store",
    "fetch_category_attributes",
    "fetch_category_record",
    "read_json",
    "search_categories_live",
    "suggest_category_ids",
    "write_json",
]
