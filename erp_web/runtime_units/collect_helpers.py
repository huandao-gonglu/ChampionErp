# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import os
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from erp_web.context import get_context
from erp_web.product_model import (
    PLATFORMS,
    SOURCE_IMAGE_ORIGINS,
    default_draft,
    draft_image_refs_from_pool,
    image_pool_refs,
    normalize_image_pool,
    normalize_draft_image_refs,
    normalize_platforms,
)
from erp_web.product_model.common import normalize_list
from erp_web.services import image_service
from erp_web.services.browser_debug_service import file_url
from erp_web.stores.product_store import normalize_product_fields

from .source_sites import detect_source_site, source_site

def collect_time_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def normalize_collect_mode(mode: str, url: str = "") -> str:
    value = str(mode or "").strip().lower()
    if value in {"browser", "http", "manual", "api"}:
        return value
    if value in {"playwright", "browser-session", "browser_session"}:
        return "browser"
    if value in {"fetch", "request", "requests"}:
        return "http"
    if "amazon." in str(url).lower():
        return "http"
    return "browser"


def detect_source_platform(url: str) -> str:
    return detect_source_site(url)


def _private_collect_descriptor(path: Path, flags: int) -> int:
    descriptor = os.open(path, flags, 0o600)
    if os.name != "nt":
        try:
            os.fchmod(descriptor, 0o600)
        except BaseException:
            os.close(descriptor)
            raise
    return descriptor


def _write_private_collect_text(path: Path, text: str) -> None:
    descriptor = _private_collect_descriptor(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
    )
    with os.fdopen(
        descriptor,
        "w",
        encoding="utf-8",
        errors="ignore",
    ) as output:
        output.write(text)


def _write_private_collect_bytes(path: Path, value: bytes) -> None:
    descriptor = _private_collect_descriptor(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_TRUNC
        | getattr(os, "O_BINARY", 0),
    )
    with os.fdopen(descriptor, "wb") as output:
        output.write(value)


def collect_debug_path(kind: str, suffix: str) -> Path:
    collect_debug_dir = get_context().paths.collect_debug_dir
    collect_debug_dir.mkdir(
        mode=0o700,
        parents=True,
        exist_ok=True,
    )
    if os.name != "nt":
        collect_debug_dir.chmod(0o700)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    rand = os.urandom(4).hex()
    safe_kind = re.sub(r"[^A-Za-z0-9._-]+", "_", kind or "collect").strip("_") or "collect"
    safe_suffix = suffix if suffix.startswith(".") else f".{suffix.lstrip('.')}"
    return collect_debug_dir / f"{stamp}_{safe_kind}_{rand}{safe_suffix}"


def write_collect_debug_html(url: str, html: str, platform: str = "collect") -> str:
    path = collect_debug_path(platform, ".html")
    _write_private_collect_text(
        path,
        "\n".join(
            [
                "<!doctype html>",
                "<html><head><meta charset=\"utf-8\"><title>collect snapshot</title></head><body>",
                f"<pre>URL: {url}</pre>",
                "<hr>",
                html,
                "</body></html>",
            ]
        ),
    )
    return str(path)


def write_collect_debug_text(platform: str, text: str, suffix: str = ".txt") -> str:
    path = collect_debug_path(platform, suffix)
    _write_private_collect_text(path, text)
    return str(path)


def save_collect_snapshot_artifacts(
    platform: str,
    url: str,
    html: str = "",
    screenshot_base64: str = "",
    text: str = "",
) -> dict[str, str]:
    artifacts: dict[str, str] = {"html_snapshot_path": "", "screenshot_path": ""}
    if html:
        artifacts["html_snapshot_path"] = write_collect_debug_html(url, html, platform)
    elif text:
        artifacts["html_snapshot_path"] = write_collect_debug_text(platform, text, ".html.txt")
    if screenshot_base64:
        path = collect_debug_path(platform, ".png")
        try:
            _write_private_collect_bytes(
                path,
                base64.b64decode(screenshot_base64),
            )
            artifacts["screenshot_path"] = str(path)
        except Exception:
            artifacts["screenshot_path"] = ""
    return artifacts


def collect_debug_file_url(path: str) -> str:
    if not path:
        return ""
    return file_url(Path(path))


def is_1688_login_page(url: str, html: str, text: str, title: str) -> bool:
    return source_site("1688").login_check(url, html, text, title)


def is_1688_security_check_page(html: str, text: str) -> bool:
    return source_site("1688").captcha_check("", html, text, "")


def is_amazon_robot_check_page(url: str, html: str, text: str, title: str) -> bool:
    return source_site("amazon").captcha_check(url, html, text, title)


def is_amazon_region_blocked_page(html: str, text: str) -> bool:
    return source_site("amazon").region_check("", html, text, "")


def snapshot_field_flags(source: dict[str, Any]) -> dict[str, Any]:
    dimensions = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    return {
        "images_found_count": len(normalize_list(source.get("images"))),
        "title_found": bool(str(source.get("title") or "").strip()),
        "price_found": bool(str(source.get("price") or "").strip()),
        "bullets_found_count": len(normalize_list(source.get("bullets"))),
        "sku_found_count": len(normalize_list(source.get("skus"))),
        "dimensions_found": any(str(dimensions.get(part) or "").strip() for part in ["length_cm", "width_cm", "height_cm"]),
        "weight_found": bool(str(source.get("weight_kg") or "").strip()),
    }


def collect_field_summary(source: dict[str, Any]) -> dict[str, list[str]]:
    flags = snapshot_field_flags(source)
    collected: list[str] = []
    missing: list[str] = []
    checks = {
        "title": flags["title_found"],
        "price": flags["price_found"],
        "images": flags["images_found_count"] > 0,
        "bullets": flags["bullets_found_count"] > 0,
        "skus": flags["sku_found_count"] > 0,
        "dimensions": flags["dimensions_found"],
        "weight": flags["weight_found"],
        "description": bool(str(source.get("description") or "").strip()),
        "brand": bool(str(source.get("brand") or "").strip()),
    }
    for field, ok in checks.items():
        (collected if ok else missing).append(field)
    return {"collected_fields": collected, "missing_fields": missing}


def collect_next_action(platform: str, error_code: str) -> str:
    return source_site(platform).next_action(error_code)


def finalize_collect_diagnostics(diagnostics: dict[str, Any], source: dict[str, Any], platform: str) -> dict[str, Any]:
    diagnostics.update(snapshot_field_flags(source))
    diagnostics.update(collect_field_summary(source))
    diagnostics["next_action"] = collect_next_action(platform, str(diagnostics.get("error_code") or ""))
    diagnostics["checked_at"] = collect_time_iso()
    return diagnostics


def collect_error_code(platform: str, mode: str, reason: str = "") -> str:
    del mode  # 错误命名由采集源拥有；模式不再改变同一原因的语义。
    return source_site(platform).error_code(reason)


def current_browser_profile_name(platform: str) -> str:
    return source_site(platform).browser_profile


def collect_image_origin(platform: str, mode: str = "") -> str:
    platform = (platform or "").strip().lower()
    mode = (mode or "").strip().lower()
    if mode in {"extension", "manual", "html_import", "browser"}:
        return mode
    site = source_site(platform)
    if site.key != "generic":
        return site.key
    return "source"


def normalize_collect_source_images(source_updates: dict[str, Any], platform: str, mode: str = "", claim_platforms: list[str] | None = None) -> dict[str, Any]:
    source = deepcopy(source_updates if isinstance(source_updates, dict) else {})
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    refs: list[Any] = list(pool)
    if not refs:
        refs.extend(normalize_list(source.get("images")))
    image_limit = source_site(platform).image_limit
    if image_limit is not None:
        refs = refs[:image_limit]
    origin = collect_image_origin(platform, mode)
    platforms = normalize_platforms(claim_platforms)
    normalized_pool = image_service.materialize_image_values(
        get_context().paths.app_dir,
        refs,
        str(source.get("source_url") or source.get("title") or "collected"),
        platforms,
        origin,
    )
    if normalized_pool:
        source["image_pool"] = normalize_image_pool(normalized_pool, origin)
        source["images"] = image_pool_refs(
            source["image_pool"],
            SOURCE_IMAGE_ORIGINS,
        )
        if not source.get("images"):
            fallback_refs: list[str] = []
            for raw in refs:
                if isinstance(raw, dict):
                    value = str(raw.get("url") or raw.get("preview_url") or raw.get("path") or "").strip()
                else:
                    value = str(raw or "").strip()
                if value:
                    fallback_refs.append(value)
            source["images"] = fallback_refs[: len(source["image_pool"])]
    return source


def parse_collect_urls(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.split(r"[\r\n,，\s]+", str(value or ""))
    urls: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        url = str(raw or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def apply_claimed_platform_drafts(product: dict[str, Any], claim_platforms: list[str] | None = None) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else {}
    platforms = normalize_platforms(claim_platforms)
    if not platforms:
        return get_context().products.sync_product_workflow_statuses(
            normalized
        )
    dims = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    placeholder_titles = {"", "-", "unknown", "draft title", "untitled", "未命名"}

    def use_existing(value: Any) -> bool:
        return str(value or "").strip().lower() not in placeholder_titles

    for platform in platforms:
        draft = default_draft(platform)
        normalized.setdefault("drafts", {})[platform] = draft
        if not isinstance(draft, dict):
            continue
        image_refs = draft_image_refs_from_pool(normalized, platform)
        draft["enabled"] = True
        draft["title"] = draft.get("title") if use_existing(draft.get("title")) else source.get("title") or normalized.get("name") or ""
        draft["description"] = draft.get("description") if use_existing(draft.get("description")) else source.get("description") or ""
        draft["bullets"] = draft.get("bullets") or source.get("bullets") or []
        draft["images"] = normalize_draft_image_refs(draft.get("images")) or image_refs
        draft["brand"] = draft.get("brand") or source.get("brand") or "Generic"
        draft["model"] = draft.get("model") or normalized.get("model") or "General"
        draft["status"] = "claimed"
        draft["package_dimensions"] = {
            **(draft.get("package_dimensions") if isinstance(draft.get("package_dimensions"), dict) else {}),
            "length_cm": (draft.get("package_dimensions") or {}).get("length_cm") or dims.get("length_cm") or "",
            "width_cm": (draft.get("package_dimensions") or {}).get("width_cm") or dims.get("width_cm") or "",
            "height_cm": (draft.get("package_dimensions") or {}).get("height_cm") or dims.get("height_cm") or "",
            "weight_kg": (draft.get("package_dimensions") or {}).get("weight_kg") or source.get("weight_kg") or "",
        }
    return get_context().products.sync_product_workflow_statuses(normalized)


def draft_copy_from_product(product: dict[str, Any], platform: str) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else {}
    dims = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    product_id = str(normalized.get("product_id") or "").strip()
    draft = default_draft(platform)
    draft.update(
        {
            "enabled": True,
            "platform": platform,
            "platforms": [platform],
            "source_product_id": product_id,
            "title": str(source.get("title") or normalized.get("name") or ""),
            "description": str(source.get("description") or normalized.get("description") or ""),
            "bullets": normalize_list(source.get("bullets") or normalized.get("selling_points")),
            "images": draft_image_refs_from_pool(normalized, platform),
            "brand": str(normalized.get("brand") or source.get("brand") or "Generic"),
            "model": str(normalized.get("model") or source.get("model") or "General"),
            # 新草稿代表新的平台刊登；持久化时按 draft_id 生成唯一 SKU。
            "sku": "",
            "stock": str(normalized.get("stock") or ""),
            "status": "claimed",
            "package_dimensions": {
                "length_cm": str(dims.get("length_cm") or dims.get("lengthCm") or ""),
                "width_cm": str(dims.get("width_cm") or dims.get("widthCm") or ""),
                "height_cm": str(dims.get("height_cm") or dims.get("heightCm") or ""),
                "weight_kg": str(source.get("weight_kg") or normalized.get("weight_kg") or ""),
            },
        }
    )
    return draft


def claim_products_to_platforms(product_ids: list[str], platforms: list[str] | None = None) -> dict[str, Any]:
    targets = normalize_platforms(platforms) or ["mercadolibre"]
    targets = [platform for platform in targets if platform in PLATFORMS]
    if not targets:
        return {"ok": False, "claimed_count": 0, "items": [], "error": "没有可用的草稿目标"}
    items: list[dict[str, Any]] = []
    for product_id in [str(item or "").strip() for item in product_ids if str(item or "").strip()]:
        product = get_context().products.load_product_from_index(
            product_id,
            "",
        )
        loaded_id = str(product.get("product_id") or "").strip() if isinstance(product, dict) else ""
        if not product or loaded_id != product_id:
            items.append({"product_id": product_id, "ok": False, "error": "商品不存在"})
            continue
        draft_ids: list[str] = []
        for platform in targets:
            draft = draft_copy_from_product(product, platform)
            draft_id = get_context().db.upsert_draft_model(product_id, platform, draft)
            if draft_id:
                draft_ids.append(draft_id)
        items.append(
            {
                "product_id": product_id,
                "source_product_id": product_id,
                "ok": bool(draft_ids),
                "platforms": targets,
                "draft_ids": draft_ids,
                "draft_statuses": {platform: "claimed" for platform in targets},
            }
        )
    return {
        "ok": True,
        "claimed_count": sum(1 for item in items if item.get("ok")),
        "items": items,
        "productsIndex": get_context().products.load_products_index(),
        "draftsIndex": get_context().products.load_drafts_index(),
    }


def productImages_from_source(product: dict[str, Any]) -> list[str]:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    refs = [str(item.get("url") or item.get("path") or item.get("preview_url") or "").strip() for item in pool if isinstance(item, dict)]
    return [item for item in refs if item] or normalize_list(source.get("images"))


__all__ = [
    "apply_claimed_platform_drafts",
    "claim_products_to_platforms",
    "collect_error_code",
    "collect_field_summary",
    "collect_image_origin",
    "collect_next_action",
    "collect_time_iso",
    "detect_source_platform",
    "finalize_collect_diagnostics",
    "normalize_collect_mode",
    "normalize_collect_source_images",
    "parse_collect_urls",
    "productImages_from_source",
    "snapshot_field_flags",
]
