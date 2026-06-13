from __future__ import annotations

from copy import deepcopy
from typing import Any

from .common import IMAGE_ORIGINS, IMAGE_USAGES, PLATFORMS, normalize_list, text_or_empty

def default_image_pool_item() -> dict[str, Any]:
    return {
        "id": "",
        "url": "",
        "path": "",
        "origin": "source",
        "usage": "detail",
        "platforms": list(PLATFORMS),
        "is_main": False,
        "selected": False,
        "order": 0,
        "status": "ready",
        "preview_url": "",
        "note": "",
    }


def normalize_platforms(value: Any) -> list[str]:
    items = normalize_list(value)
    if not items and isinstance(value, str):
        items = [value.strip()]
    return [item for item in items if item in PLATFORMS]


def normalize_image_pool_item(item: Any, order: int = 0, origin_hint: str = "source") -> dict[str, Any]:
    normalized = default_image_pool_item()
    if isinstance(item, str):
        text = text_or_empty(item)
        if text.startswith("http://") or text.startswith("https://") or text.startswith("ml-id:"):
            normalized["url"] = text
        else:
            normalized["path"] = text
        normalized["preview_url"] = normalized["url"] or normalized["path"]
        normalized["id"] = f"img_{order + 1}"
        normalized["origin"] = origin_hint if origin_hint in IMAGE_ORIGINS else "source"
        normalized["usage"] = "main" if order == 0 else "detail"
        normalized["platforms"] = list(PLATFORMS)
        normalized["is_main"] = order == 0
        normalized["order"] = order
        normalized["status"] = "ready" if normalized["preview_url"] else "empty"
        return normalized

    item = item if isinstance(item, dict) else {}
    normalized["id"] = text_or_empty(item.get("id")) or f"img_{order + 1}"
    normalized["url"] = text_or_empty(item.get("url"))
    normalized["path"] = text_or_empty(item.get("path"))
    normalized["origin"] = text_or_empty(item.get("origin")) or (origin_hint if origin_hint in IMAGE_ORIGINS else "source")
    usage = text_or_empty(item.get("usage")) or ("main" if order == 0 else "detail")
    normalized["usage"] = usage if usage in IMAGE_USAGES else "other"
    platforms = normalize_platforms(item.get("platforms"))
    normalized["platforms"] = platforms or list(PLATFORMS)
    normalized["is_main"] = bool(item.get("is_main", order == 0 and normalized["usage"] == "main"))
    normalized["selected"] = bool(item.get("selected", False))
    try:
        normalized["order"] = int(item.get("order", order))
    except Exception:
        normalized["order"] = order
    normalized["status"] = text_or_empty(item.get("status")) or ("ready" if (normalized["url"] or normalized["path"]) else "empty")
    normalized["preview_url"] = text_or_empty(item.get("preview_url")) or normalized["url"] or normalized["path"]
    normalized["note"] = text_or_empty(item.get("note"))
    for key, value in item.items():
        if key not in normalized:
            normalized[key] = deepcopy(value)
    return normalized


def normalize_image_pool(items: Any, legacy_images: list[Any] | None = None, origin_hint: str = "source") -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append_item(raw_item: Any, order: int, source_origin: str) -> None:
        item = normalize_image_pool_item(raw_item, order=order, origin_hint=source_origin)
        key = item.get("path") or item.get("url") or item.get("preview_url") or item.get("id")
        if key and key in seen:
            return
        if key:
            seen.add(str(key))
        normalized.append(item)

    if isinstance(items, list) and items:
        for index, raw_item in enumerate(items):
            append_item(raw_item, index, origin_hint)
    else:
        fallback = legacy_images if isinstance(legacy_images, list) else []
        for index, raw_item in enumerate(fallback):
            append_item(raw_item, index, origin_hint)

    if normalized and not any(item.get("is_main") for item in normalized):
        normalized[0]["is_main"] = True
        normalized[0]["usage"] = "main"

    for index, item in enumerate(normalized):
        item["order"] = index
    return normalized


def image_pool_legacy_views(image_pool: list[dict[str, Any]], allowed_origins: set[str] | None = None) -> dict[str, list[str]]:
    ordered = sorted(
        [item for item in image_pool if isinstance(item, dict)],
        key=lambda item: int(item.get("order") or 0),
    )
    if allowed_origins:
        ordered = [item for item in ordered if text_or_empty(item.get("origin")) in allowed_origins]
    source_items = ordered[:7]
    detail_items = ordered[7:]
    def as_ref(item: dict[str, Any]) -> str:
        return text_or_empty(item.get("path") or item.get("url") or item.get("preview_url"))
    return {
        "images": [as_ref(item) for item in ordered if as_ref(item)],
        "source_images": [as_ref(item) for item in source_items if as_ref(item)],
        "source_image_urls": [text_or_empty(item.get("url") or item.get("path") or item.get("preview_url")) for item in source_items if as_ref(item)],
        "detail_images": [as_ref(item) for item in detail_items if as_ref(item)],
        "detail_image_urls": [text_or_empty(item.get("url") or item.get("path") or item.get("preview_url")) for item in detail_items if as_ref(item)],
    }
