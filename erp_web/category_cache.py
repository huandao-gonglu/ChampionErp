# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

import erp_db

JsonClient = Callable[[str, str | None], dict[str, Any] | list[Any]]


def http_json(url: str, access_token: str | None = None) -> dict[str, Any] | list[Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 ERPCategoryCache/1.0",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def ml_attr_required(attr: dict[str, Any]) -> bool:
    tags = attr.get("tags") if isinstance(attr.get("tags"), dict) else {}
    return bool(attr.get("required") or tags.get("required") or tags.get("catalog_required"))


def normalize_ml_attribute(attr: dict[str, Any]) -> dict[str, Any]:
    values = attr.get("values") if isinstance(attr.get("values"), list) else []
    units = attr.get("allowed_units") if isinstance(attr.get("allowed_units"), list) else []
    return {
        "id": str(attr.get("id") or "").strip(),
        "name": str(attr.get("name") or attr.get("id") or "").strip(),
        "required": ml_attr_required(attr),
        "value_type": str(attr.get("value_type") or "string").strip() or "string",
        "unit": str((units[0] or {}).get("id") or "") if units and isinstance(units[0], dict) else "",
        "options": [str(item.get("name") or item.get("id") or "").strip() for item in values if isinstance(item, dict)],
        "description": str(attr.get("tooltip") or attr.get("hint") or "").strip(),
    }


def ml_attributes_for_category(
    category_id: str,
    access_token: str | None = None,
    http_client: JsonClient = http_json,
) -> dict[str, list[dict[str, Any]]]:
    raw = http_client(
        f"https://api.mercadolibre.com/categories/{urllib.parse.quote(category_id)}/attributes",
        access_token,
    )
    attrs = [normalize_ml_attribute(item) for item in (raw if isinstance(raw, list) else []) if isinstance(item, dict)]
    return {
        "required": [item for item in attrs if item.get("required")],
        "optional": [item for item in attrs if not item.get("required")],
    }


def ml_category_record(detail: dict[str, Any], site: str, attrs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    path_items = detail.get("path_from_root") if isinstance(detail.get("path_from_root"), list) else []
    path = [str(item.get("name") or "").strip() for item in path_items if isinstance(item, dict) and str(item.get("name") or "").strip()]
    category_id = str(detail.get("id") or "").strip()
    name = str(detail.get("name") or category_id).strip()
    return {
        "platform": "mercadolibre",
        "site": site,
        "country": "MX" if site == "MLM" else site,
        "category_id": category_id,
        "subject_id": category_id,
        "type_id": "",
        "name_original": name,
        "name_cn": "",
        "path_original": path or [name],
        "path_cn": [],
        "parent_id": str((path_items[-2] or {}).get("id") or "") if len(path_items) > 1 and isinstance(path_items[-2], dict) else "",
        "level": len(path or [name]),
        "keywords": [name, category_id],
        "attributes_cache": attrs,
        "updated_at": erp_db.utc_now(),
    }


def build_mercadolibre_category_cache(
    site: str = "MLM",
    max_categories: int = 500,
    access_token: str | None = None,
    http_client: JsonClient = http_json,
) -> dict[str, Any]:
    site = str(site or "MLM").strip().upper()
    max_categories = max(1, int(max_categories or 500))
    roots = http_client(
        f"https://api.mercadolibre.com/sites/{urllib.parse.quote(site)}/categories",
        access_token,
    )
    queue = [str(item.get("id") or "").strip() for item in (roots if isinstance(roots, list) else []) if isinstance(item, dict) and str(item.get("id") or "").strip()]
    records: list[dict[str, Any]] = []
    visited: set[str] = set()
    errors: list[str] = []
    while queue and len(visited) < max_categories:
        category_id = queue.pop(0)
        if category_id in visited:
            continue
        visited.add(category_id)
        try:
            detail = http_client(
                f"https://api.mercadolibre.com/categories/{urllib.parse.quote(category_id)}",
                access_token,
            )
        except Exception as exc:
            errors.append(f"{category_id}: {exc}")
            continue
        if not isinstance(detail, dict):
            continue
        children = detail.get("children_categories") if isinstance(detail.get("children_categories"), list) else []
        child_ids = [str(item.get("id") or "").strip() for item in children if isinstance(item, dict) and str(item.get("id") or "").strip()]
        if child_ids:
            queue.extend(child_id for child_id in child_ids if child_id not in visited)
            continue
        try:
            attrs = ml_attributes_for_category(category_id, access_token=access_token, http_client=http_client)
        except Exception as exc:
            errors.append(f"{category_id}/attributes: {exc}")
            attrs = {"required": [], "optional": []}
        records.append(ml_category_record(detail, site, attrs))
    return {
        "platform": "mercadolibre",
        "site": site,
        "updated_at": erp_db.utc_now(),
        "records": records,
        "source": "mercadolibre_official_api",
        "visited": len(visited),
        "errors": errors,
    }


def refresh_official_category_cache(
    app_dir: Path,
    platform: str,
    store_config: dict[str, Any],
    site: str = "MLM",
    max_categories: int = 500,
    http_client: JsonClient = http_json,
) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    if platform != "mercadolibre":
        return {"ok": False, "platform": platform, "error": "当前只接入 Mercado Libre 官方类目刷新"}
    erp_db.initialize_database(app_dir)
    token = str((store_config.get("mercadolibre") or {}).get("access_token") or "").strip()
    try:
        cache = build_mercadolibre_category_cache(
            site=site,
            max_categories=max_categories,
            access_token=token or None,
            http_client=http_client,
        )
    except urllib.error.HTTPError as exc:
        status = erp_db.category_cache_status(app_dir, platform)
        code = int(getattr(exc, "code", 0) or 0)
        if code in {401, 403}:
            return {
                "ok": False,
                "platform": platform,
                "site": site,
                "error_code": "MERCADOLIBRE_CATEGORY_AUTH_REQUIRED",
                "error": "Mercado Libre 官方类目接口拒绝匿名访问，请先完成店铺授权或刷新 access_token 后再更新类目缓存。",
                "next_action": "前往授权页完成 Mercado Libre 授权，然后回到发布预检页刷新类目缓存。",
                "http_status": code,
                "cache_status": status,
            }
        raise
    imported = erp_db.import_category_cache(app_dir, cache)
    status = erp_db.category_cache_status(app_dir, platform)
    return {
        "ok": True,
        "platform": platform,
        "site": site,
        "imported": imported,
        "visited": cache.get("visited", 0),
        "errors": cache.get("errors", []),
        "cache_status": status,
        "cache": cache,
    }
