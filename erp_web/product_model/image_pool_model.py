from __future__ import annotations

from copy import deepcopy
from typing import Any

from erp_web.schemas.image import ImageItem

from .common import IMAGE_ORIGINS, IMAGE_USAGES, PLATFORMS, normalize_list, text_or_empty


_CANONICAL_IMAGE_FIELDS = frozenset(ImageItem.__annotations__)


def default_image_pool_item() -> dict[str, Any]:
    return {
        "id": "",
        "url": "",
        "path": "",
        "origin": "source",
        "usage": "detail",
        "platforms": [],
        "is_main": False,
        "selected": False,
        "order": 0,
        "status": "ready",
        "preview_url": "",
        "storage_key": "",
        "content_sha256": "",
        "delivery_provider": "",
        "delivery_error": "",
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
        normalized["platforms"] = []
        normalized["is_main"] = order == 0
        normalized["order"] = order
        normalized["status"] = "ready" if normalized["preview_url"] else "empty"
        return normalized

    item = item if isinstance(item, dict) else {}
    normalized["id"] = text_or_empty(
        item.get("id")
        or item.get("asset_id")
    ) or f"img_{order + 1}"
    normalized["url"] = text_or_empty(item.get("url"))
    normalized["path"] = text_or_empty(
        item.get("path")
        or item.get("local_path")
    )
    normalized["origin"] = text_or_empty(
        item.get("origin")
        or item.get("source_kind")
    ) or (origin_hint if origin_hint in IMAGE_ORIGINS else "source")
    usage = text_or_empty(
        item.get("usage")
        or item.get("asset_type")
        or item.get("type")
    ) or ("main" if order == 0 else "detail")
    normalized["usage"] = usage if usage in IMAGE_USAGES else "other"
    platforms = normalize_platforms(
        item.get("platforms")
        or item.get("platforms_json")
    )
    normalized["platforms"] = platforms
    normalized["is_main"] = bool(
        item.get("is_main")
        or item.get("is_primary")
        or (
            order == 0
            and normalized["usage"] == "main"
        )
    )
    normalized["selected"] = bool(item.get("selected", False))
    try:
        normalized["order"] = int(
            item.get("order")
            if item.get("order") not in (None, "")
            else item.get("sort_order")
            if item.get("sort_order") not in (None, "")
            else order
        )
    except Exception:
        normalized["order"] = order
    normalized["status"] = text_or_empty(item.get("status")) or ("ready" if (normalized["url"] or normalized["path"]) else "empty")
    normalized["preview_url"] = text_or_empty(item.get("preview_url")) or normalized["url"] or normalized["path"]
    normalized["note"] = text_or_empty(item.get("note"))
    for key, aliases in (
        ("width", ("width", "width_px")),
        ("height", ("height", "height_px")),
    ):
        raw_value = next(
            (
                item.get(alias)
                for alias in aliases
                if item.get(alias) not in (None, "")
            ),
            None,
        )
        if raw_value is not None:
            try:
                normalized[key] = int(float(raw_value))
            except (TypeError, ValueError):
                pass
    for key, aliases in (
        ("size_label", ("size_label", "dimensions", "size")),
        ("sku", ("sku", "sku_id")),
        ("derived_from_id", ("derived_from_id",)),
        ("source_asset_id", ("source_asset_id",)),
        ("target_language", ("target_language",)),
        ("provider", ("provider",)),
        ("translate_job_id", ("translate_job_id",)),
        ("storage_key", ("storage_key",)),
        ("content_sha256", ("content_sha256",)),
        ("delivery_provider", ("delivery_provider",)),
        ("delivery_error", ("delivery_error",)),
    ):
        value = next(
            (
                item.get(alias)
                for alias in aliases
                if item.get(alias) not in (None, "")
            ),
            None,
        )
        if value is not None:
            normalized[key] = text_or_empty(value)
    if item.get("is_sku") or item.get("sku_image"):
        normalized["is_sku"] = True
    if isinstance(item.get("raw"), dict):
        normalized["raw"] = deepcopy(item["raw"])
    return {
        key: value
        for key, value in normalized.items()
        if key in _CANONICAL_IMAGE_FIELDS
    }


def normalize_image_pool(
    items: Any,
    origin_hint: str = "source",
) -> list[dict[str, Any]]:
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

    if isinstance(items, list):
        for index, raw_item in enumerate(items):
            append_item(raw_item, index, origin_hint)

    if normalized and not any(item.get("is_main") for item in normalized):
        normalized[0]["is_main"] = True
        normalized[0]["usage"] = "main"

    for index, item in enumerate(normalized):
        item["order"] = index
    return normalized


def image_pool_refs(
    image_pool: list[dict[str, Any]],
    allowed_origins: set[str] | None = None,
) -> list[str]:
    ordered = sorted(
        [item for item in image_pool if isinstance(item, dict)],
        key=lambda item: int(item.get("order") or 0),
    )
    if allowed_origins:
        ordered = [item for item in ordered if text_or_empty(item.get("origin")) in allowed_origins]

    def as_ref(item: dict[str, Any]) -> str:
        return text_or_empty(item.get("path") or item.get("url") or item.get("preview_url"))

    return [as_ref(item) for item in ordered if as_ref(item)]
