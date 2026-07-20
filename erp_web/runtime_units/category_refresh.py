# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Callable

JsonClient = Callable[[str, str | None], dict[str, Any] | list[Any]]


def http_json(url: str, access_token: str | None = None) -> dict[str, Any] | list[Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 ERPCategoryLive/1.0",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=8) as response:
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
        "raw": attr,
    }


def mercadolibre_category_attributes(
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


def mercadolibre_category_detail(
    category_id: str,
    access_token: str | None = None,
    http_client: JsonClient = http_json,
) -> dict[str, Any]:
    data = http_client(
        f"https://api.mercadolibre.com/categories/{urllib.parse.quote(category_id)}",
        access_token,
    )
    if not isinstance(data, dict):
        raise RuntimeError(f"Mercado Libre 类目详情响应不是对象：{category_id}")
    return data


def mercadolibre_category_record(
    detail: dict[str, Any],
    site: str,
    attrs: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    path_items = detail.get("path_from_root") if isinstance(detail.get("path_from_root"), list) else []
    path = [str(item.get("name") or "").strip() for item in path_items if isinstance(item, dict) and str(item.get("name") or "").strip()]
    path_ids = [str(item.get("id") or "").strip() for item in path_items if isinstance(item, dict) and str(item.get("id") or "").strip()]
    category_id = str(detail.get("id") or "").strip()
    name = str(detail.get("name") or category_id).strip()
    attrs = attrs if isinstance(attrs, dict) else {"required": [], "optional": []}
    return {
        "platform": "mercadolibre",
        "site": str(site or "").strip().upper(),
        "category_id": category_id,
        "subject_id": category_id,
        "type_id": "",
        "name_original": name,
        "name_cn": "",
        "category_path": " / ".join(path or [name]),
        "path_original": path or [name],
        "path_ids": path_ids or [category_id],
        "path_cn": [],
        "parent_id": path_ids[-2] if len(path_ids) > 1 else "",
        "level": len(path or [name]),
        "keywords": [name, category_id],
        "attributes": {
            "required": list(attrs.get("required") or []),
            "optional": list(attrs.get("optional") or []),
        },
        "raw": detail,
    }


__all__ = [
    "JsonClient",
    "http_json",
    "mercadolibre_category_attributes",
    "mercadolibre_category_detail",
    "mercadolibre_category_record",
    "ml_attr_required",
    "normalize_ml_attribute",
]
