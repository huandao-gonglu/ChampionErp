# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

from .category_providers import require_category_provider


logger = logging.getLogger(__name__)


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # Do not fail the caller, but a corrupt/unreadable JSON store should never
        # be silently indistinguishable from an empty one.
        logger.warning("read_json fell back to default for %s", path, exc_info=True)
        return default
    return default


def write_json(path: Path, data: Any) -> None:
    """Atomically persist JSON: write a same-directory temp file, then os.replace.

    A crash mid-write must never leave a truncated/corrupt store behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if os.name != "nt":
            tmp_path.chmod(0o600)
        os.replace(tmp_path, path)
        if os.name != "nt":
            path.chmod(0o600)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


# SQLite 初始化已并入 ErpDatabase（构造期建 schema）；本模块只保留 JSON 文件
# 读写工具和类目实时检索。

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


def _category_suggest_terms(product: dict[str, Any], platform: str = "mercadolibre") -> list[str]:
    from erp_web.stores.product_store import normalize_product_fields
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
    from erp_web.stores.product_store import normalize_product_fields
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


def _path_text(record: dict[str, Any]) -> str:
    path = record.get("path_original") if isinstance(record.get("path_original"), list) else []
    if path:
        return " / ".join(str(item).strip() for item in path if str(item).strip())
    return str(record.get("category_path") or record.get("name_original") or record.get("category_id") or "").strip()


def fetch_category_record(platform: str, category_id: str, site: str = "", include_attributes: bool = False) -> dict[str, Any]:
    provider = require_category_provider(platform)
    return provider.detail(category_id, site=site, include_attributes=include_attributes)


def fetch_category_attributes(platform: str, category_id: str, site: str = "") -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    provider = require_category_provider(platform)
    record = fetch_category_record(platform, category_id, site=site, include_attributes=True)
    attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    required = list(attrs.get("required") or [])
    optional = list(attrs.get("optional") or [])
    return {
        "ok": True,
        "platform": platform,
        "site": record.get("site") or provider.resolve_site(site),
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
    provider = require_category_provider(platform)
    query = str(query or "").strip()
    if not query:
        return []
    return provider.search(query, site=site, limit=limit)


def suggest_category_ids(product: dict[str, Any], platform: str = "mercadolibre", site: str = "", limit: int = 5) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    provider = require_category_provider(platform)
    resolved_site = provider.resolve_site(site)
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
    "fetch_category_attributes",
    "fetch_category_record",
    "read_json",
    "search_categories_live",
    "suggest_category_ids",
    "write_json",
]
