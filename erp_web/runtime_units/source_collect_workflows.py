# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from erp_web.context import get_context
from erp_web.product_model import (
    default_collect_diagnostics,
    default_product_model,
    merge_source_partial_result,
)
from erp_web.services import html_extract_service

from erp_web.services.desktop_connection_service import call_desktop, DesktopUnavailable
from erp_web.services.desktop_collection_service import apply_uploaded_assets
from erp_web.services.html_extract_service import fetch_url_html_with_status
from .publish_bus import page_snapshot_from_html
from .source_collect_1688_api import collect_1688_product_via_api
from .source_collect_verification import same_collection_page, waiting_verification_result
from .source_sites import parse_source_snapshot, source_site
from .collect_helpers import (
    apply_claimed_platform_drafts,
    collect_error_code,
    collect_time_iso,
    detect_source_platform,
    finalize_collect_diagnostics,
    normalize_collect_mode,
    normalize_collect_source_images,
    parse_collect_urls,
    snapshot_field_flags,
    write_collect_debug_html,
)
from .image_pool_core import current_image_pool, current_source_images


def collect_source_product(
    url: str,
    mode: str = "browser",
    cookie: str | None = None,
    platform: str | None = None,
    claim_platforms: list[str] | None = None,
    api_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = str(url or "").strip()
    if not url:
        raise RuntimeError("请先输入商品链接。")

    platform_detected = (platform or detect_source_platform(url)).lower() or "unknown"
    site = source_site(platform_detected)
    collect_mode = normalize_collect_mode(mode, url)
    cookie = (cookie or "").strip()
    diagnostics = default_collect_diagnostics()
    diagnostics.update(
        {
            "collect_mode": collect_mode,
            "source_url": url,
            "normalized_url": url,
            "platform_detected": platform_detected,
            "started_at": collect_time_iso(),
        }
    )
    partial_product = get_context().products.collection_product(url)
    snapshot: dict[str, Any] | None = None
    html = ""
    text = ""
    title = ""
    final_url = url
    http_status = ""
    error_reason = ""

    try:
        if collect_mode == "api":
            if not site.supports_api_collect:
                raise RuntimeError("API_MODE_ONLY_SUPPORTS_1688")
            return collect_1688_product_via_api(url, claim_platforms, api_config)

        if collect_mode == "browser":
            return collect_browser_url(product_url=url, platform_hint=platform_detected,
                                            claim_platforms=claim_platforms, open_url=True)
        html, http_status = fetch_url_html_with_status(url, cookie)
        if html:
            snapshot = page_snapshot_from_html(url, html, html_extract_service.html_to_text(html),
                html_extract_service.extract_page_title(html), html_extract_service.extract_product_image_urls(html, url, limit=20))
        if not snapshot:
            raise RuntimeError("NO_SNAPSHOT")

        html = str(snapshot.get("html") or "")
        text = str(snapshot.get("text") or "")
        title = str(snapshot.get("title") or "")
        final_url = str(snapshot.get("final_url") or snapshot.get("url") or url)
        diagnostics["html_snapshot_path"] = str(snapshot.get("html_snapshot_path") or "")
        diagnostics["screenshot_path"] = str(snapshot.get("screenshot_path") or "")
        if not diagnostics["html_snapshot_path"] and html:
            diagnostics["html_snapshot_path"] = write_collect_debug_html(final_url, html, platform_detected)
        diagnostics["final_url"] = final_url
        diagnostics["page_title"] = title or html_extract_service.extract_page_title(html)
        diagnostics["http_status"] = http_status

        page_diagnostics, error_reason = site.diagnose(final_url, html, text, title)
        diagnostics.update(page_diagnostics)
        waiting = waiting_verification_result(snapshot, url, platform_detected, diagnostics, error_reason)
        if waiting:
            return waiting
        parsed_product = parse_source_snapshot(site.key, snapshot, final_url)

        source_updates = parsed_product.get("source") if isinstance(parsed_product.get("source"), dict) else {}
        source_updates = normalize_collect_source_images(source_updates, platform_detected, collect_mode, claim_platforms)
        diagnostics.update(snapshot_field_flags(source_updates))
        error_reason = site.quality_reason(diagnostics, error_reason)

        diagnostics["error_code"] = collect_error_code(platform_detected, collect_mode, error_reason) if error_reason else ""
        diagnostics["error_message"] = "采集成功" if not diagnostics["error_code"] else diagnostics["error_code"]
        diagnostics["partial_success"] = any(
            [
                diagnostics["title_found"],
                diagnostics["images_found_count"],
                diagnostics["dimensions_found"],
                diagnostics["weight_found"],
            ]
        )
        diagnostics["success"] = bool(diagnostics["title_found"] and not diagnostics["error_code"])
        diagnostics["finished_at"] = collect_time_iso()
        diagnostics = finalize_collect_diagnostics(diagnostics, source_updates, platform_detected)

        merged = merge_source_partial_result(partial_product, source_updates, diagnostics)
        merged["source"]["source_url"] = url
        merged["source"]["source_platform"] = str(merged["source"].get("source_platform") or platform_detected or "").strip()
        merged["source"]["collect_status"] = "success" if diagnostics["success"] else ("partial" if diagnostics["partial_success"] else "failed")
        merged["source"]["collect_logs"] = list(merged["source"].get("collect_logs") or [])
        merged["source"]["collect_logs"].append(
            {
                "started_at": diagnostics["started_at"],
                "finished_at": diagnostics["finished_at"],
                "mode": collect_mode,
                "platform": platform_detected,
                "success": diagnostics["success"],
                "partial_success": diagnostics["partial_success"],
                "error_code": diagnostics["error_code"],
                "error_message": diagnostics["error_message"],
            }
        )
        merged["source"]["collect_diagnostics"] = diagnostics
        merged["collect_status"] = merged["source"]["collect_status"]
        merged["collect_logs"] = merged["source"]["collect_logs"]
        original_url = str((partial_product.get("source") or {}).get("source_url") or "").strip()
        if url and url != original_url:
            merged.pop("product_id", None)
        merged = apply_claimed_platform_drafts(merged, claim_platforms)
        saved = get_context().products.save_product(merged)
        return {
            "ok": diagnostics["success"],
            "product": saved,
            "imagePool": current_image_pool(saved),
            "sourceImages": current_source_images(saved),
            "diagnostics": diagnostics,
            "productsIndex": get_context().products.load_products_index(),
            "error": diagnostics["error_message"] if not diagnostics["success"] else "",
            "next_action": diagnostics.get("next_action", ""),
        }
    except Exception as exc:
        error_message = str(exc)
        if not diagnostics.get("error_code"):
            if error_message == "NO_SNAPSHOT":
                reason = "REMOTE" if collect_mode == "browser" else "SELECTOR"
            elif "403" in error_message or "forbidden" in error_message.lower():
                reason = "FORBIDDEN"
            elif "winerror 10013" in error_message.lower() or "访问套接字" in error_message or "socket" in error_message.lower():
                reason = "REMOTE" if site.supports_api_collect else "NETWORK"
            else:
                reason = "REMOTE" if "remote" in error_message.lower() or "connect" in error_message.lower() else "SELECTOR"
            if site.supports_api_collect and "profile" in error_message.lower():
                reason = "PROFILE"
            diagnostics["error_code"] = collect_error_code(platform_detected, collect_mode, reason)
        diagnostics["error_message"] = error_message
        diagnostics["finished_at"] = collect_time_iso()
        diagnostics["success"] = False
        diagnostics["partial_success"] = bool(snapshot or html or text)
        if snapshot or html or text:
            fallback_snapshot = snapshot or page_snapshot_from_html(url, html, text, title)
            parsed_product = parse_source_snapshot(site.key, fallback_snapshot, final_url)
            source_updates = parsed_product.get("source") if isinstance(parsed_product.get("source"), dict) else {}
            source_updates = normalize_collect_source_images(source_updates, platform_detected, collect_mode, claim_platforms)
            diagnostics.update(snapshot_field_flags(source_updates))
            merged = merge_source_partial_result(partial_product, source_updates, diagnostics)
        else:
            merged = merge_source_partial_result(partial_product, {}, diagnostics)
        diagnostics = finalize_collect_diagnostics(diagnostics, merged.get("source") if isinstance(merged.get("source"), dict) else {}, platform_detected)
        original_url = str((partial_product.get("source") or {}).get("source_url") or "").strip()
        if url and url != original_url:
            merged = default_product_model() if not diagnostics["partial_success"] else merged
            merged.pop("product_id", None)
            merged["source"]["source_url"] = url
            merged["source"]["source_platform"] = platform_detected
        if diagnostics["partial_success"]:
            merged["source"]["source_url"] = url
            merged["source"]["source_platform"] = str(merged["source"].get("source_platform") or platform_detected or "").strip()
        merged["source"]["collect_status"] = "partial" if diagnostics["partial_success"] else "failed"
        merged["source"]["collect_logs"] = list(merged["source"].get("collect_logs") or [])
        merged["source"]["collect_logs"].append(
            {
                "started_at": diagnostics["started_at"],
                "finished_at": diagnostics["finished_at"],
                "mode": collect_mode,
                "platform": platform_detected,
                "success": False,
                "partial_success": diagnostics["partial_success"],
                "error_code": diagnostics["error_code"],
                "error_message": error_message,
            }
        )
        merged["source"]["collect_diagnostics"] = diagnostics
        merged = apply_claimed_platform_drafts(merged, claim_platforms)
        saved = get_context().products.save_product(merged)
        return {
            "ok": False,
            "product": saved,
            "imagePool": current_image_pool(saved),
            "sourceImages": current_source_images(saved),
            "productsIndex": get_context().products.load_products_index(),
            "diagnostics": diagnostics,
            "error": error_message or diagnostics["error_code"],
            "next_action": diagnostics.get("next_action", ""),
        }


def collect_batch_products(
    urls: Any,
    mode: str = "browser",
    cookie: str | None = None,
    platform: str | None = None,
    platforms: list[str] | None = None,
    api_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parsed_urls = parse_collect_urls(urls)
    items: list[dict[str, Any]] = []
    waiting = False
    for url in parsed_urls:
        detected = (platform or detect_source_platform(url)).lower() or "unknown"
        row = {
            "url": url,
            "platform": detected,
            "status": "pending",
            "ok": False,
            "title": "",
            "image": "",
            "error": "",
            "error_code": "",
            "next_action": "",
            "product_id": "",
            "product": None,
        }
        if waiting:
            items.append(row)
            continue
        try:
            if api_config is not None:
                result = collect_source_product(url, mode, cookie, detected, platforms, api_config)
            else:
                result = collect_source_product(url, mode, cookie, detected, platforms)
            product = result.get("product") if isinstance(result.get("product"), dict) else {}
            source = product.get("source") if isinstance(product.get("source"), dict) else {}
            image_pool = current_image_pool(product)
            diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
            waiting = result.get("status") == "waiting_verification"
            if waiting:
                row["verification"] = result["verification"]
            row.update(
                {
                    "status": "waiting_verification" if waiting else "success" if result.get("ok") else ("partial" if diagnostics.get("partial_success") else "failed"),
                    "ok": bool(result.get("ok")),
                    "title": str(source.get("title") or product.get("name") or ""),
                    "image": str((image_pool[0] if image_pool else {}).get("preview_url") or (image_pool[0] if image_pool else {}).get("url") or ""),
                    "error": str(result.get("error") or ""),
                    "error_code": str(diagnostics.get("error_code") or ""),
                    "next_action": str(result.get("next_action") or diagnostics.get("next_action") or ""),
                    "product_id": str(product.get("product_id") or ""),
                    "product": product,
                }
            )
        except Exception as exc:
            row.update(
                {
                    "status": "failed",
                    "error": str(exc),
                    "error_code": "COLLECT_FAILED",
                    "next_action": "请检查商品链接及采集浏览器的登录状态后重试。",
                }
            )
        items.append(row)
    return {
        "ok": True,
        "total": len(items),
        "success_count": sum(1 for item in items if item["status"] == "success"),
        "partial_count": sum(1 for item in items if item["status"] == "partial"),
        "failed_count": sum(1 for item in items if item["status"] == "failed"),
        "items": items,
        "productsIndex": get_context().products.load_products_index(),
    }


def collect_browser_url(
    product_url: str, platform_hint: str = "",
    claim_platforms: list[str] | None = None,
    browser_tab_id: str = "", open_url: bool = False,
) -> dict[str, Any]:
    original_product = get_context().products.load_product()
    started_at = collect_time_iso()
    status = {}
    try:
        facts = call_desktop('collect.read', {
            'platform_hint': platform_hint, 'product_url': product_url,
            'browser_tab_id': browser_tab_id, 'open_url': open_url,
        }, timeout=900)
        snapshot = facts['snapshot']
        status = facts.get('browserStatus') or {}
        final_url = str(snapshot.get("final_url") or snapshot.get("url") or product_url or "")
        original_product = get_context().products.collection_product(product_url or final_url)
        html_text = str(snapshot.get("html") or "")
        text = str(snapshot.get("text") or "")
        title = str(snapshot.get("title") or snapshot.get("page_title") or "")
        platform_detected = (detect_source_platform(product_url or final_url) or platform_hint or detect_source_platform(title) or "unknown").lower()
        site = source_site(platform_detected)
        diagnostics = default_collect_diagnostics()
        diagnostics.update(
            {
                "collect_mode": "browser_debugging",
                "source_url": product_url or final_url,
                "normalized_url": final_url,
                "platform_detected": platform_detected,
                "started_at": started_at,
                "finished_at": collect_time_iso(),
                "final_url": final_url,
                "page_title": title,
                "html_snapshot_path": str(snapshot.get("html_snapshot_path") or ""),
                "screenshot_path": str(snapshot.get("screenshot_path") or ""),
                "browser_connected": True,
                "debug_port": status.get("port"),
                "tab_url": final_url,
                "tab_title": title,
            }
        )
        page_diagnostics, error_reason = site.diagnose(
            final_url,
            html_text,
            text,
            title,
            detect_slider=True,
        )
        diagnostics.update(page_diagnostics)
        waiting = waiting_verification_result(snapshot, product_url or final_url, platform_detected, diagnostics, error_reason)
        if waiting:
            return {**waiting, "browserStatus": status}
        if browser_tab_id and not same_collection_page(product_url, final_url):
            raise RuntimeError("TAB_NOT_ACCESSIBLE：原标签页已离开目标商品，请返回商品页后重试。")
        parsed = parse_source_snapshot(site.key, snapshot, final_url)
        source_updates = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
        source_updates = apply_uploaded_assets(source_updates, facts.get('uploaded_assets') or {})
        source_updates = normalize_collect_source_images(source_updates, platform_detected, "browser", claim_platforms)
        flags = snapshot_field_flags(source_updates)
        error_reason = site.quality_reason(flags, error_reason)
        diagnostics["error_code"] = collect_error_code(platform_detected, "browser", error_reason) if error_reason else ""
        diagnostics["error_message"] = "浏览器采集成功" if not diagnostics["error_code"] else diagnostics["error_code"]
        diagnostics["partial_success"] = any([flags["title_found"], flags["images_found_count"], flags["dimensions_found"], flags["weight_found"]])
        diagnostics["success"] = bool(flags["title_found"] and not diagnostics["error_code"])
        diagnostics = finalize_collect_diagnostics(diagnostics, source_updates, platform_detected)
        merged = merge_source_partial_result(original_product, source_updates, diagnostics)
        if diagnostics["partial_success"]:
            merged["source"]["source_url"] = product_url or final_url
            merged["source"]["source_platform"] = platform_detected
            original_url = str((original_product.get("source") or {}).get("source_url") or "").strip()
            if final_url and not same_collection_page(original_url, final_url):
                merged.pop("product_id", None)
                merged.pop("id", None)
        merged["source"]["collect_status"] = "success" if diagnostics["success"] else ("partial" if diagnostics["partial_success"] else "failed")
        merged["source"]["collect_logs"] = list(merged["source"].get("collect_logs") or [])
        merged["source"]["collect_logs"].append({"started_at": started_at, "finished_at": diagnostics["finished_at"], "mode": "browser_debugging", "platform": platform_detected, "success": diagnostics["success"], "partial_success": diagnostics["partial_success"], "error_code": diagnostics["error_code"], "error_message": diagnostics["error_message"]})
        merged["source"]["collect_diagnostics"] = diagnostics
        merged = apply_claimed_platform_drafts(merged, claim_platforms)
        saved = get_context().products.save_product(merged)
        return {"ok": diagnostics["success"], "product": saved, "imagePool": current_image_pool(saved), "sourceImages": current_source_images(saved), "productsIndex": get_context().products.load_products_index(), "diagnostics": diagnostics, "browserStatus": status, "error": "" if diagnostics["success"] else diagnostics["error_message"], "next_action": diagnostics.get("next_action", ""), "real_publish_called": False}
    except Exception as exc:
        message = str(exc)
        code = getattr(exc, "code", "") or ("NO_PRODUCT_TAB_FOUND" if "NO_PRODUCT_TAB_FOUND" in message else "TAB_NOT_ACCESSIBLE" if "TAB_NOT_ACCESSIBLE" in message else "REMOTE_DEBUGGING_NOT_CONNECTED")
        diagnostics = default_collect_diagnostics()
        diagnostics.update({"collect_mode": "browser_debugging", "started_at": started_at, "finished_at": collect_time_iso(), "success": False, "partial_success": False, "error_code": code, "error_message": message, "browser_connected": bool(status.get("connected")), "debug_port": status.get("port"), "next_action": "请确认采集浏览器中原商品页可访问，完成登录或验证码后重试该链接。", "checked_at": collect_time_iso()})
        # 目标页失效、快照失败等情况只返回诊断，不修改之前采集的商品。
        if isinstance(exc, DesktopUnavailable):
            diagnostics["next_action"] = message
        return {"ok": False, "product": original_product, "imagePool": current_image_pool(original_product), "productsIndex": get_context().products.load_products_index(), "diagnostics": diagnostics, "browserStatus": status, "error": message, "next_action": diagnostics["next_action"], "real_publish_called": False}


def collect_1688_payload_service(body: dict[str, Any]) -> dict[str, Any]:
    return collect_source_product(
        str(body.get('url') or ''),
        str(body.get('mode') or 'browser'),
        str(body.get('cookie') or ''),
        '1688',
        body.get('platforms'),
        body.get('1688_api'),
    )


__all__ = [
    "collect_1688_payload_service",
    "collect_batch_products",
    "collect_browser_url",
    "collect_source_product",
]
