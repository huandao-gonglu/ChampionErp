# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import os
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from product_model import (
    PLATFORMS,
    SOURCE_COMPAT_IMAGE_ORIGINS,
    default_draft,
    image_pool_legacy_views,
    normalize_image_pool,
    normalize_platforms,
)
from services import image_service

from .browser_debug import file_url
from .image_pool_core import image_pool_refs_for_platform
from .product_store import (
    load_drafts_index,
    load_product_from_index,
    load_products_index,
    normalize_list,
    normalize_product_fields,
    save_product,
    sync_product_workflow_statuses,
)
from .runtime_common import AMAZON_VERIFY_MARKERS, APP_DIR, COLLECT_DEBUG_DIR, VERIFY_MARKERS

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
    lowered = str(url or "").lower()
    if "amazon." in lowered:
        return "amazon"
    if "1688.com" in lowered:
        return "1688"
    if "wildberries" in lowered:
        return "wildberries"
    if "ozon" in lowered:
        return "ozon"
    if "alibaba." in lowered:
        return "alibaba"
    return "unknown"


def collect_debug_path(kind: str, suffix: str) -> Path:
    COLLECT_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    rand = os.urandom(4).hex()
    safe_kind = re.sub(r"[^A-Za-z0-9._-]+", "_", kind or "collect").strip("_") or "collect"
    safe_suffix = suffix if suffix.startswith(".") else f".{suffix.lstrip('.')}"
    return COLLECT_DEBUG_DIR / f"{stamp}_{safe_kind}_{rand}{safe_suffix}"


def write_collect_debug_html(url: str, html: str, platform: str = "collect") -> str:
    path = collect_debug_path(platform, ".html")
    path.write_text(
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
        encoding="utf-8",
        errors="ignore",
    )
    return str(path)


def write_collect_debug_text(platform: str, text: str, suffix: str = ".txt") -> str:
    path = collect_debug_path(platform, suffix)
    path.write_text(text, encoding="utf-8", errors="ignore")
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
            path.write_bytes(base64.b64decode(screenshot_base64))
            artifacts["screenshot_path"] = str(path)
        except Exception:
            artifacts["screenshot_path"] = ""
    return artifacts


def collect_debug_file_url(path: str) -> str:
    if not path:
        return ""
    return file_url(Path(path))


def is_1688_login_page(url: str, html: str, text: str, title: str) -> bool:
    lowered = "\n".join([str(url or ""), html or "", text or "", title or ""]).lower()
    return "login.1688.com" in lowered or "请登录" in lowered or "登录" in lowered or "帐号密码登录" in lowered


def is_1688_security_check_page(html: str, text: str) -> bool:
    lowered = "\n".join([html or "", text or ""]).lower()
    return any(marker.lower() in lowered for marker in VERIFY_MARKERS) or "滑块" in lowered or "安全验证" in lowered


def is_amazon_robot_check_page(url: str, html: str, text: str, title: str) -> bool:
    lowered = "\n".join([str(url or ""), html or "", text or "", title or ""]).lower()
    return any(marker in lowered for marker in AMAZON_VERIFY_MARKERS) or "/errors/validatecaptcha" in lowered


def is_amazon_region_blocked_page(html: str, text: str) -> bool:
    lowered = "\n".join([html or "", text or ""]).lower()
    region_markers = (
        "cannot be shipped to your selected location",
        "not deliverable",
        "currently unavailable",
        "this item cannot be shipped",
        "not available in your region",
    )
    return any(marker in lowered for marker in region_markers)


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
    platform = (platform or "").lower()
    code = (error_code or "").upper()
    if not code:
        return "采集已完成，可进入商品库继续 AI 文案、生图和编辑。"
    if platform == "1688":
        if "API" in code:
            return "请检查 1688 官方 API 凭证、接口权限和商品详情接口地址；未开通权限时可切回浏览器采集。"
        if any(key in code for key in ["LOGIN", "CAPTCHA", "SECURITY", "SLIDER", "REMOTE_DEBUGGING"]):
            return "1688 触发验证，请手动打开浏览器完成验证，或使用手动导入。"
        return "请尝试浏览器会话采集；如果仍失败，保存商品详情页 HTML 后导入，或手动补充缺失字段。"
    if platform == "amazon":
        if any(key in code for key in ["ROBOT", "REGION", "LOGIN", "FORBIDDEN"]):
            return "请使用已登录且地区正确的浏览器会话重试；如果仍被拦截，请使用 HTML 导入 / 手动补充。"
        return "请尝试浏览器登录后采集；如果选择器失败，使用 HTML 导入或手动补充。"
    return "无法稳定自动解析该来源，请使用 HTML 导入或手动补充后继续后续流程。"


def finalize_collect_diagnostics(diagnostics: dict[str, Any], source: dict[str, Any], platform: str) -> dict[str, Any]:
    diagnostics.update(snapshot_field_flags(source))
    diagnostics.update(collect_field_summary(source))
    diagnostics["next_action"] = collect_next_action(platform, str(diagnostics.get("error_code") or ""))
    diagnostics["checked_at"] = collect_time_iso()
    return diagnostics


def collect_error_code(platform: str, mode: str, reason: str = "") -> str:
    platform = (platform or "").lower()
    reason = (reason or "").upper()
    if platform == "amazon":
        mapping = {
            "ROBOT": "AMAZON_ROBOT_CHECK",
            "REGION": "AMAZON_REGION_BLOCKED",
            "NO_IMAGES": "AMAZON_IMAGE_NOT_FOUND",
            "NO_TITLE": "AMAZON_TITLE_NOT_FOUND",
            "NO_BULLETS": "AMAZON_NO_BULLETS_FOUND",
            "NO_DIMENSIONS": "AMAZON_DIMENSIONS_NOT_FOUND",
            "NO_WEIGHT": "AMAZON_WEIGHT_NOT_FOUND",
            "SELECTOR": "AMAZON_SELECTOR_FAILED",
            "LOGIN": "AMAZON_LOGIN_REQUIRED",
            "NETWORK": "NETWORK_BLOCKED",
            "FORBIDDEN": "HTTP_FORBIDDEN",
        }
        return mapping.get(reason, "AMAZON_SELECTOR_FAILED")
    if platform == "1688":
        mapping = {
            "LOGIN": "1688_LOGIN_REQUIRED",
            "SECURITY": "1688_SECURITY_CHECK",
            "CAPTCHA": "1688_CAPTCHA_REQUIRED",
            "SLIDER": "1688_SLIDER_REQUIRED",
            "NO_IMAGES": "1688_IMAGE_NOT_FOUND",
            "NO_TITLE": "1688_TITLE_NOT_FOUND",
            "NO_DIMENSIONS": "1688_DIMENSIONS_NOT_FOUND",
            "SELECTOR": "1688_SELECTOR_FAILED",
            "PROFILE": "1688_BROWSER_PROFILE_NOT_FOUND",
            "REMOTE": "1688_REMOTE_DEBUGGING_NOT_CONNECTED",
            "NETWORK": "NETWORK_BLOCKED",
            "API": "1688_API_FAILED",
        }
        return mapping.get(reason, "1688_SELECTOR_FAILED")
    return "COLLECT_FAILED"


def current_browser_profile_name(platform: str) -> str:
    platform = (platform or "").lower()
    if platform == "amazon":
        return "amazon"
    if platform == "1688":
        return "1688"
    return platform or "collect"


def collect_image_origin(platform: str, mode: str = "") -> str:
    platform = (platform or "").strip().lower()
    mode = (mode or "").strip().lower()
    if mode in {"extension", "manual", "html_import", "browser"}:
        return mode
    if platform in {"amazon", "1688"}:
        return platform
    return "source"


def normalize_collect_source_images(source_updates: dict[str, Any], platform: str, mode: str = "", claim_platforms: list[str] | None = None) -> dict[str, Any]:
    source = deepcopy(source_updates if isinstance(source_updates, dict) else {})
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    refs: list[Any] = list(pool)
    if not refs:
        refs.extend(normalize_list(source.get("images")))
    if str(platform or "").strip().lower() == "1688":
        refs = refs[:5]
    origin = collect_image_origin(platform, mode)
    platforms = normalize_platforms(claim_platforms) or ["mercadolibre"]
    normalized_pool = image_service.materialize_image_values(
        APP_DIR,
        refs,
        str(source.get("source_url") or source.get("title") or "collected"),
        platforms,
        origin,
    )
    if normalized_pool:
        source["image_pool"] = normalize_image_pool(normalized_pool, [], origin)
        source["images"] = image_pool_legacy_views(source["image_pool"], SOURCE_COMPAT_IMAGE_ORIGINS)["images"]
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
    platforms = normalize_platforms(claim_platforms) or ["mercadolibre"]
    dims = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    image_refs = image_pool_refs_for_platform(normalized, "mercadolibre") or productImages_from_source(normalized)
    placeholder_titles = {"", "-", "unknown", "draft title", "untitled", "未命名"}

    def use_existing(value: Any) -> bool:
        return str(value or "").strip().lower() not in placeholder_titles

    for platform in platforms:
        draft = default_draft(platform)
        normalized.setdefault("drafts", {})[platform] = draft
        if not isinstance(draft, dict):
            continue
        draft["enabled"] = True
        draft["title"] = draft.get("title") if use_existing(draft.get("title")) else source.get("title") or normalized.get("name") or ""
        draft["description"] = draft.get("description") if use_existing(draft.get("description")) else source.get("description") or ""
        draft["bullets"] = draft.get("bullets") or source.get("bullets") or []
        draft["images"] = draft.get("images") if draft.get("images") else image_refs
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
    return sync_product_workflow_statuses(normalized)


def claim_products_to_platforms(product_ids: list[str], platforms: list[str]) -> dict[str, Any]:
    targets = normalize_platforms(platforms) or ["mercadolibre"]
    targets = [platform for platform in targets if platform in PLATFORMS]
    if not targets:
        return {"ok": False, "claimed_count": 0, "items": [], "error": "没有可认领的平台"}
    items: list[dict[str, Any]] = []
    for product_id in [str(item or "").strip() for item in product_ids if str(item or "").strip()]:
        product = load_product_from_index(product_id, "")
        if not product:
            items.append({"product_id": product_id, "ok": False, "error": "商品不存在"})
            continue
        product = apply_claimed_platform_drafts(product, targets)
        product = save_product(product)
        items.append(
            {
                "product_id": product.get("product_id") or product_id,
                "ok": True,
                "platforms": targets,
                "draft_statuses": (product.get("workflow_statuses") or {}),
            }
        )
    return {
        "ok": True,
        "claimed_count": sum(1 for item in items if item.get("ok")),
        "items": items,
        "productsIndex": load_products_index(),
        "draftsIndex": load_drafts_index(),
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
