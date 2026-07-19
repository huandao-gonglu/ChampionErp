"""Image pool operations for the Web ERP.

All files created here stay under ``data/images`` inside the project. The
service only handles pure image-pool operations; database persistence is kept in
the existing Web API layer.
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from erp_web.marketplace_registry import PLATFORMS

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_REMOTE_BYTES = 12 * 1024 * 1024
MAX_SAFE_SEGMENT_LENGTH = 80


def service_status() -> dict[str, str]:
    return {"service": "image", "status": "ready"}


def images_root(app_dir: Path | str) -> Path:
    root = Path(app_dir) / "data" / "images"
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_segment(value: str, fallback: str = "unassigned", max_length: int = MAX_SAFE_SEGMENT_LENGTH) -> str:
    """Return a filesystem-safe single path segment.

    Collection flows may pass a full source URL as ``product_id`` before the
    product has a stable id.  macOS rejects path components longer than 255
    bytes, so keep segments short and append a stable hash when truncating.
    """

    raw = str(value or "")
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")
    if not text:
        raw = str(fallback or "unassigned")
        text = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._") or "unassigned"

    max_length = max(16, int(max_length or MAX_SAFE_SEGMENT_LENGTH))
    if len(text) <= max_length:
        return text

    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:10]
    prefix_length = max(1, max_length - len(digest) - 1)
    prefix = text[:prefix_length].rstrip("._") or text[:prefix_length]
    return f"{prefix}_{digest}"[:max_length]


def safe_filename(filename: str, suffix: str = ".png") -> str:
    stem = safe_segment(Path(filename or "image").stem, "image")
    ext = Path(filename or "").suffix or suffix or ".png"
    if not ext.startswith("."):
        ext = f".{ext}"
    ext = ext.lower()
    if ext == ".jpe":
        ext = ".jpg"
    if ext not in IMAGE_SUFFIXES:
        ext = ".png"
    return f"{time.strftime('%Y%m%d_%H%M%S')}_{stem}_{os.urandom(3).hex()}{ext}"


def relative_to_app(path: Path, app_dir: Path | str) -> str:
    try:
        return str(path.resolve().relative_to(Path(app_dir).resolve()))
    except Exception:
        return str(path)


def file_url(path: Path) -> str:
    return f"/file?path={urllib.parse.quote(str(path), safe='')}"


def decode_upload(upload: dict[str, Any]) -> tuple[bytes, str]:
    raw = str(upload.get("data_url") or upload.get("dataUrl") or upload.get("base64") or "").strip()
    if not raw:
        return b"", ".png"
    suffix = ".png"
    if raw.startswith("data:") and "," in raw:
        header, raw = raw.split(",", 1)
        mime = header.split(";", 1)[0].replace("data:", "").lower()
        suffix = mimetypes.guess_extension(mime) or ".png"
    if suffix == ".jpe":
        suffix = ".jpg"
    try:
        return base64.b64decode(raw), suffix
    except Exception:
        return b"", suffix


def image_dimensions(path: Path) -> tuple[int, int]:
    if not Image or not path.exists():
        return 0, 0
    try:
        with Image.open(path) as img:
            return int(img.width), int(img.height)
    except Exception:
        return 0, 0


def normalize_platforms(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = re.split(r"[,，\s]+", value)
    elif isinstance(value, list):
        raw = value
    else:
        raw = []
    result: list[str] = []
    for item in raw:
        text = str(item or "").strip().lower()
        if text in PLATFORMS and text not in result:
            result.append(text)
    return result


def resolve_local_path(path: str, app_dir: Path | str | None) -> Path | None:
    if not path or not app_dir:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path(app_dir) / candidate
    return candidate


def preview_for_path(path: str, app_dir: Path | str | None) -> str:
    candidate = resolve_local_path(path, app_dir)
    if candidate and candidate.exists():
        return file_url(candidate)
    return ""


def normalize_item(item: dict[str, Any], index: int = 0, app_dir: Path | str | None = None) -> dict[str, Any]:
    data = dict(item or {})
    path = str(data.get("path") or data.get("local_path") or "").strip()
    url = str(data.get("url") or "").strip()
    preview_url = str(data.get("preview_url") or "").strip()
    width = int(float(data.get("width") or data.get("width_px") or 0))
    height = int(float(data.get("height") or data.get("height_px") or 0))
    status = str(data.get("status") or "ready").strip()
    note = str(data.get("note") or "").strip()

    local_preview = preview_for_path(path, app_dir)
    if local_preview:
        preview_url = local_preview
        candidate = resolve_local_path(path, app_dir)
        if candidate and (not width or not height):
            width, height = image_dimensions(candidate)
    elif path and status == "ready":
        status = "missing_file"
        note = note or "文件不存在"
    elif not preview_url and url:
        preview_url = url

    asset_id = str(data.get("id") or data.get("asset_id") or "").strip()
    if not asset_id:
        seed = str(url or preview_url or path or index)
        asset_id = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:16]

    platforms = normalize_platforms(data.get("platforms") or data.get("platforms_json"))
    normalized = {
        "id": asset_id,
        "asset_id": asset_id,
        "url": url,
        "path": path,
        "local_path": path,
        "preview_url": preview_url,
        "origin": str(data.get("origin") or data.get("source_kind") or "manual").strip(),
        "usage": str(data.get("usage") or data.get("asset_type") or "detail").strip(),
        "platforms": platforms,
        "is_main": bool(data.get("is_main") or data.get("is_primary")),
        "is_sku": bool(data.get("is_sku") or data.get("sku_image")),
        "sku": str(data.get("sku") or data.get("sku_id") or "").strip(),
        "selected": bool(data.get("selected", True)),
        "order": int(data.get("order") if str(data.get("order") or "").isdigit() else index),
        "status": status,
        "note": note,
        "width": width,
        "height": height,
        "width_px": width,
        "height_px": height,
        "size_label": data.get("size_label") or (f"{width}x{height}" if width and height else "unknown"),
        "raw": data.get("raw") if isinstance(data.get("raw"), dict) else {},
    }
    for key in ("derived_from_id", "source_asset_id", "target_language", "provider", "translate_job_id"):
        value = data.get(key)
        if value not in (None, ""):
            normalized[key] = str(value).strip()
    return normalized


def normalize_pool(pool: list[dict[str, Any]], app_dir: Path | str | None = None) -> list[dict[str, Any]]:
    items = [normalize_item(item, index, app_dir) for index, item in enumerate(pool or []) if isinstance(item, dict)]
    items.sort(key=lambda item: int(item.get("order") or 0))
    for index, item in enumerate(items):
        item["order"] = index
    ready_items = [item for item in items if item.get("status") == "ready"]
    if ready_items and not any(item.get("is_main") for item in items):
        ready_items[0]["is_main"] = True
        ready_items[0]["usage"] = "main"
    return items


def upload_images(app_dir: Path | str, uploads: list[dict[str, Any]], product_id: str = "") -> list[dict[str, Any]]:
    target_dir = images_root(app_dir) / safe_segment(product_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for index, upload in enumerate(uploads or []):
        if not isinstance(upload, dict):
            continue
        raw, suffix = decode_upload(upload)
        if raw:
            dest = target_dir / safe_filename(str(upload.get("filename") or upload.get("name") or f"upload_{index + 1}"), suffix)
            dest.write_bytes(raw)
        else:
            source_path = str(upload.get("path") or upload.get("local_path") or "").strip()
            if not source_path:
                continue
            src = Path(source_path)
            if not src.exists() or not src.is_file():
                continue
            dest = target_dir / safe_filename(str(upload.get("filename") or src.name), src.suffix or ".png")
            shutil.copy2(src, dest)
        width, height = image_dimensions(dest)
        rel_path = relative_to_app(dest, app_dir)
        items.append(
            normalize_item(
                {
                    "id": dest.stem,
                    "path": rel_path,
                    "local_path": rel_path,
                    "preview_url": file_url(dest),
                    "origin": upload.get("origin") or "local_upload",
                    "usage": upload.get("usage") or ("main" if index == 0 else "detail"),
                    "platforms": upload.get("platforms") or [],
                    "is_main": bool(upload.get("is_main", index == 0)),
                    "is_sku": bool(upload.get("is_sku")),
                    "sku": upload.get("sku") or upload.get("sku_id") or "",
                    "selected": bool(upload.get("selected", True)),
                    "width": width,
                    "height": height,
                    "status": "ready",
                    "note": "已保存到 data/images",
                },
                index,
                app_dir,
            )
        )
    return items


def _remote_suffix(url: str, content_type: str = "") -> str:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return suffix
    guessed = mimetypes.guess_extension(content_type.split(";")[0].strip().lower()) if content_type else ""
    if guessed == ".jpe":
        guessed = ".jpg"
    return guessed if guessed in IMAGE_SUFFIXES else ".jpg"


def download_remote_image(app_dir: Path | str, url: str, product_id: str = "", index: int = 0) -> dict[str, Any]:
    target_dir = images_root(app_dir) / safe_segment(product_id, "collected")
    target_dir.mkdir(parents=True, exist_ok=True)
    url = str(url or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {}
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": f"{parsed.scheme}://{parsed.netloc}/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            raw = response.read(MAX_REMOTE_BYTES + 1)
        if len(raw) > MAX_REMOTE_BYTES:
            raise RuntimeError("图片超过 12MB")
        if "image" not in content_type and not raw.startswith((b"\xff\xd8", b"\x89PNG", b"RIFF")):
            raise RuntimeError(f"图片格式不支持: {content_type or 'unknown'}")
        suffix = _remote_suffix(url, content_type)
        dest = target_dir / safe_filename(f"remote_{index + 1}{suffix}", suffix)
        dest.write_bytes(raw)
        width, height = image_dimensions(dest)
        rel_path = relative_to_app(dest, app_dir)
        return normalize_item(
            {
                "id": dest.stem,
                "url": url,
                "path": rel_path,
                "local_path": rel_path,
                "preview_url": file_url(dest),
                "origin": "remote_download",
                "usage": "main" if index == 0 else "detail",
                "selected": True,
                "is_main": index == 0,
                "width": width,
                "height": height,
                "status": "ready",
                "note": "远程图片已保存到 data/images",
            },
            index,
            app_dir,
        )
    except Exception as exc:
        return normalize_item(
            {
                "id": hashlib.sha1(url.encode("utf-8", errors="ignore")).hexdigest()[:16],
                "url": url,
                "preview_url": url,
                "origin": "remote_download",
                "usage": "main" if index == 0 else "detail",
                "selected": True,
                "is_main": index == 0,
                "status": "download_failed",
                "note": f"图片下载失败: {exc}",
            },
            index,
            app_dir,
        )


def materialize_image_values(
    app_dir: Path | str,
    image_values: list[Any],
    product_id: str = "",
    platforms: list[str] | None = None,
    origin: str = "manual",
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    platform_values = normalize_platforms(platforms) or list(PLATFORMS)
    for index, value in enumerate(image_values or []):
        raw_item = value if isinstance(value, dict) else {}
        text = str(raw_item.get("preview_url") or raw_item.get("url") or raw_item.get("path") or value or "").strip()
        if not text:
            continue
        if text.startswith(("http://", "https://")):
            item = download_remote_image(app_dir, text, product_id, index)
        else:
            src = Path(text)
            if not src.is_absolute():
                src = Path(app_dir) / src
            uploads = (
                upload_images(
                    app_dir,
                    [{"path": str(src), "filename": src.name, "origin": origin, "selected": True, "is_main": index == 0}],
                    product_id,
                )
                if src.exists() and src.is_file()
                else []
            )
            item = uploads[0] if uploads else normalize_item(
                {
                    "id": hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16],
                    "path": text,
                    "status": "missing_file",
                    "note": "文件不存在",
                    "selected": True,
                    "is_main": index == 0,
                },
                index,
                app_dir,
            )
        if item:
            item["origin"] = origin if item.get("status") == "ready" else item.get("origin") or origin
            item["platforms"] = platform_values
            item["order"] = index
            item["is_main"] = index == 0
            item["selected"] = True
            items.append(item)
    return normalize_pool(items, app_dir)


def add_images(pool: list[dict[str, Any]], items: list[dict[str, Any]], app_dir: Path | str | None = None) -> list[dict[str, Any]]:
    existing = normalize_pool(pool or [], app_dir)
    seen = {str(item.get("id") or item.get("path") or item.get("url")) for item in existing}
    for item in items or []:
        normalized = normalize_item(item, len(existing), app_dir)
        key = str(normalized.get("id") or normalized.get("path") or normalized.get("url"))
        if key in seen:
            continue
        existing.append(normalized)
        seen.add(key)
    return normalize_pool(existing, app_dir)


def sort_images(pool: list[dict[str, Any]], ordered_ids: list[str], app_dir: Path | str | None = None) -> list[dict[str, Any]]:
    items = normalize_pool(pool or [], app_dir)
    order = {str(item_id): index for index, item_id in enumerate(ordered_ids or [])}
    items.sort(key=lambda item: (order.get(str(item.get("id")), 10_000), int(item.get("order") or 0)))
    for index, item in enumerate(items):
        item["order"] = index
    return normalize_pool(items, app_dir)


def delete_images(pool: list[dict[str, Any]], image_ids: list[str], app_dir: Path | str | None = None, delete_files: bool = False) -> list[dict[str, Any]]:
    targets = {str(item).strip() for item in image_ids or [] if str(item).strip()}
    kept: list[dict[str, Any]] = []
    for item in normalize_pool(pool or [], app_dir):
        if str(item.get("id")) in targets:
            if delete_files and app_dir:
                full = resolve_local_path(str(item.get("path") or ""), app_dir)
                try:
                    root = images_root(app_dir).resolve()
                    if full and str(full.resolve()).startswith(str(root)) and full.exists():
                        full.unlink()
                except Exception:
                    pass
            continue
        kept.append(item)
    return normalize_pool(kept, app_dir)


def replace_image(pool: list[dict[str, Any]], image_id: str, replacement: dict[str, Any], app_dir: Path | str) -> list[dict[str, Any]]:
    items = normalize_pool(pool or [], app_dir)
    uploads = upload_images(app_dir, [replacement], str(replacement.get("product_id") or ""))
    if not uploads:
        return items
    new_item = uploads[0]
    for index, item in enumerate(items):
        if str(item.get("id")) == str(image_id):
            new_item["order"] = item.get("order", index)
            new_item["is_main"] = item.get("is_main", False)
            new_item["is_sku"] = item.get("is_sku", False)
            new_item["sku"] = item.get("sku", "")
            new_item["platforms"] = item.get("platforms") or new_item.get("platforms")
            items[index] = new_item
            return normalize_pool(items, app_dir)
    items.append(new_item)
    return normalize_pool(items, app_dir)


def set_main_image(pool: list[dict[str, Any]], image_id: str, app_dir: Path | str | None = None) -> list[dict[str, Any]]:
    items = normalize_pool(pool or [], app_dir)
    for item in items:
        is_target = str(item.get("id")) == str(image_id)
        item["is_main"] = is_target
        if is_target:
            item["usage"] = "main"
            item["selected"] = True
    return normalize_pool(items, app_dir)


def set_sku_image(pool: list[dict[str, Any]], image_id: str, sku: str, app_dir: Path | str | None = None) -> list[dict[str, Any]]:
    items = normalize_pool(pool or [], app_dir)
    for item in items:
        if str(item.get("id")) == str(image_id):
            item["is_sku"] = True
            item["sku"] = str(sku or "").strip()
            item["usage"] = "sku"
            item["selected"] = True
    return normalize_pool(items, app_dir)


def filter_images(pool: list[dict[str, Any]], platform: str = "", selected_only: bool = False, app_dir: Path | str | None = None) -> list[dict[str, Any]]:
    items = normalize_pool(pool or [], app_dir)
    platform = str(platform or "").strip().lower()
    if platform:
        items = [item for item in items if platform in [str(p).lower() for p in item.get("platforms", [])]]
    if selected_only:
        items = [item for item in items if item.get("selected")]
    return items


def apply_image_action(app_dir: Path | str, pool: list[dict[str, Any]], action: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    action = str(action or "").strip().lower()
    if action == "upload":
        return add_images(pool, upload_images(app_dir, body.get("uploads") or [], str(body.get("product_id") or "")), app_dir)
    if action == "sort":
        return sort_images(pool, body.get("ordered_ids") or body.get("image_ids") or [], app_dir)
    if action == "delete":
        return delete_images(pool, body.get("image_ids") or body.get("ids") or [], app_dir, bool(body.get("delete_files")))
    if action == "replace":
        return replace_image(pool, str(body.get("image_id") or ""), body.get("replacement") or {}, app_dir)
    if action == "set_main":
        return set_main_image(pool, str(body.get("image_id") or ""), app_dir)
    if action == "set_sku":
        return set_sku_image(pool, str(body.get("image_id") or ""), str(body.get("sku") or body.get("sku_id") or ""), app_dir)
    if action == "filter":
        return filter_images(pool, str(body.get("platform") or ""), bool(body.get("selected_only")), app_dir)
    raise ValueError(f"Unsupported image action: {action}")
