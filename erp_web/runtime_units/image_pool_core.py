# -*- coding: utf-8 -*-
from __future__ import annotations

import time
import urllib.parse
from pathlib import Path
from typing import Any

from erp_web.product_model import (
    SOURCE_COMPAT_IMAGE_ORIGINS,
    image_pool_legacy_views,
    normalize_image_pool,
)

from .browser_debug import file_url
from .runtime_common import APP_DIR, CHATGPT_DIR, COLLECT_DEBUG_DIR, SOURCE_DIR


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
    normalized = dict(product or {})
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else {}
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    if pool:
        source["image_pool"] = normalize_image_pool([enrich_image_pool_item_dimensions(item) for item in pool], [], "source")
        source["images"] = image_pool_legacy_views(source["image_pool"], SOURCE_COMPAT_IMAGE_ORIGINS)["images"]
        normalized["source"] = source
    return normalized


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
    for file_item in generated_files:
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
                "platforms": [],
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
    if platform not in {"mercadolibre", "yandex", "ozon"}:
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


def current_generated_images() -> list[dict[str, str]]:
    return image_files(CHATGPT_DIR, recursive=True)


def current_collect_debug_files() -> list[dict[str, str]]:
    return image_files(COLLECT_DEBUG_DIR, recursive=True)


__all__ = [
    "_display_image_ref",
    "_is_local_image_ref",
    "_is_web_image_ref",
    "_local_path_from_image_item",
    "_pool_display_item",
    "_read_image_dimensions_from_path",
    "_resolve_local_image_ref",
    "_source_only_pool_items",
    "_source_pool_items",
    "current_collect_debug_files",
    "current_generated_images",
    "current_image_pool",
    "current_source_images",
    "enrich_image_pool_item_dimensions",
    "enrich_product_image_dimensions",
    "image_files",
    "image_items_from_paths",
    "image_pool_refs_for_platform",
]
