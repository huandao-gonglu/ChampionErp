# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

from erp_web import db as erp_db
from erp_web.product_model import (
    category_cache_status as _json_category_cache_status,
    find_category_record as _json_find_category_record,
    load_category_cache as _json_load_category_cache,
    search_category_cache as _json_search_category_cache,
)

from .runtime_common import APP_DIR

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
    from .product_store import normalize_product_fields
    from .publish_helpers import _draft_for_platform

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
    from .product_store import normalize_product_fields
    from .publish_helpers import _draft_for_platform

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
    from .category_refresh import http_json
    from .product_store import load_store_config

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
    from .product_store import load_store_config

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


__all__ = [
    "category_cache_status",
    "find_category_record",
    "import_partial_records",
    "load_category_cache",
    "read_json",
    "save_category_cache",
    "search_category_cache",
    "suggest_category_ids",
    "write_json",
]
