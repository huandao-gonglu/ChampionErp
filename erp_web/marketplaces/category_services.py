from __future__ import annotations

import urllib.parse
from typing import Any

from .config_http import request_json, request_ozon_json


def mercadolibre_category_path(category_id: str, token: str = "") -> str:
    if not category_id:
        return ""
    try:
        data = request_json("GET", f"https://api.mercadolibre.com/categories/{category_id}", token)
        path = data.get("path_from_root", []) if isinstance(data, dict) else []
        names = [str(item.get("name") or "").strip() for item in path if isinstance(item, dict)]
        return " / ".join(name for name in names if name)
    except Exception:
        return ""


def mercadolibre_category_allowed_currencies(category_id: str, token: str = "") -> list[str]:
    """读取类目级允许发布币种（``settings.currencies``）。"""

    category = str(category_id or "").strip()
    if not category:
        return []
    try:
        data = request_json(
            "GET",
            f"https://api.mercadolibre.com/categories/{urllib.parse.quote(category)}",
            token,
        )
    except Exception:
        return []
    settings = data.get("settings") if isinstance(data, dict) else None
    if not isinstance(settings, dict):
        return []
    currencies: list[str] = []
    raw = settings.get("currencies")
    if isinstance(raw, list):
        for item in raw:
            code = str(
                item.get("id") if isinstance(item, dict) else item or ""
            ).strip().upper()
            if len(code) == 3 and code.isalpha() and code not in currencies:
                currencies.append(code)
    return currencies


def fetch_ozon_seller_info(client_id: str, api_key: str) -> dict[str, Any]:
    """Return Ozon seller account metadata used by publishing capabilities."""

    data = request_ozon_json(
        "POST",
        "https://api-seller.ozon.ru/v1/seller/info",
        client_id,
        api_key,
        {},
    )
    if not isinstance(data, dict):
        raise RuntimeError("Ozon seller/info 返回异常")
    company = data.get("company") if isinstance(data.get("company"), dict) else {}
    currency = str(company.get("currency") or "").strip().upper()
    if not currency:
        raise RuntimeError("Ozon seller/info 未返回店铺合同币种")
    return data


def mercadolibre_category_attributes_for_publish(category_id: str, token: str) -> list[dict[str, Any]]:
    if not category_id:
        return []
    attrs = request_json(
        "GET",
        f"https://api.mercadolibre.com/categories/{category_id}/attributes",
        token,
    )
    normalized = []
    for attr in attrs if isinstance(attrs, list) else []:
        tags = attr.get("tags", {})
        normalized.append(
            {
                "id": attr.get("id"),
                "name": attr.get("name") or attr.get("id"),
                "value_type": attr.get("value_type"),
                "values": attr.get("values", [])[:50],
                "required": bool(
                    tags.get("required")
                    or tags.get("catalog_required")
                    or tags.get("conditional_required")
                    or tags.get("new_required")
                ),
            }
        )
    normalized.sort(key=lambda item: (not item.get("required"), str(item.get("name") or "")))
    return normalized


__all__ = [
    "fetch_ozon_seller_info",
    "mercadolibre_category_allowed_currencies",
    "mercadolibre_category_attributes_for_publish",
    "mercadolibre_category_path",
]
