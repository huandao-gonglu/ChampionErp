# -*- coding: utf-8 -*-
from __future__ import annotations

from .runtime_common import *

from .source_collect_browser import *
from .source_collect_parsers import *

def collect_source_product(
    url: str,
    mode: str = "browser",
    cookie: str | None = None,
    platform: str | None = None,
    claim_platforms: list[str] | None = None,
) -> dict[str, Any]:
    url = str(url or "").strip()
    if not url:
        raise RuntimeError("璇峰厛杈撳叆鍟嗗搧閾炬帴銆?")

    platform_detected = (platform or detect_source_platform(url)).lower() or "unknown"
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
    partial_product = load_product()
    snapshot: dict[str, Any] | None = None
    html = ""
    text = ""
    title = ""
    final_url = url
    http_status = ""
    error_reason = ""

    try:
        if collect_mode == "manual":
            raise RuntimeError("MANUAL_MODE")

        if collect_mode == "browser":
            snapshot = fetch_page_snapshot_with_browser_session(url, profile_name=current_browser_profile_name(platform_detected))
            if not snapshot and platform_detected == "1688":
                html = maybe_fetch_page_html_with_playwright(url, cookie) or ""
                if html:
                    snapshot = page_snapshot_from_html(url, html, legacy.html_to_text(html), legacy.extract_page_title(html), legacy.extract_product_image_urls(html, url, limit=20))
        if not snapshot:
            html, http_status = fetch_page_html_with_status(url, cookie)
            if html:
                snapshot = page_snapshot_from_html(url, html, legacy.html_to_text(html), legacy.extract_page_title(html), legacy.extract_product_image_urls(html, url, limit=20))
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
        diagnostics["page_title"] = title or legacy.extract_page_title(html)
        diagnostics["http_status"] = http_status

        if platform_detected == "1688":
            diagnostics["is_login_page"] = is_1688_login_page(final_url, html, text, title)
            diagnostics["is_captcha_page"] = is_1688_security_check_page(html, text)
            diagnostics["is_security_check_page"] = diagnostics["is_captcha_page"]
            if diagnostics["is_login_page"]:
                error_reason = "LOGIN"
            elif diagnostics["is_captcha_page"]:
                error_reason = "CAPTCHA"
        elif platform_detected == "amazon":
            diagnostics["is_login_page"] = "signin" in final_url.lower() or "sign in" in f"{title} {text}".lower()
            diagnostics["is_captcha_page"] = is_amazon_robot_check_page(final_url, html, text, title)
            diagnostics["is_security_check_page"] = diagnostics["is_captcha_page"]
            if diagnostics["is_captcha_page"]:
                error_reason = "ROBOT"
            elif is_amazon_region_blocked_page(html, text):
                error_reason = "REGION"

        if platform_detected == "amazon":
            parsed_product = parse_amazon_product(snapshot, final_url)
        elif platform_detected == "unknown":
            parsed_product = parse_generic_product(snapshot, final_url)
        else:
            parsed_product = parse_1688_product(snapshot, final_url)

        source_updates = parsed_product.get("source") if isinstance(parsed_product.get("source"), dict) else {}
        source_updates = normalize_collect_source_images(source_updates, platform_detected, collect_mode, claim_platforms)
        diagnostics.update(snapshot_field_flags(source_updates))
        if not diagnostics["title_found"]:
            error_reason = error_reason or "NO_TITLE"
        if diagnostics["images_found_count"] <= 0:
            error_reason = error_reason or "NO_IMAGES"
        if platform_detected == "amazon" and diagnostics["bullets_found_count"] <= 0:
            error_reason = error_reason or "NO_BULLETS"
        if platform_detected == "amazon" and not diagnostics["dimensions_found"]:
            error_reason = error_reason or "NO_DIMENSIONS"
        if platform_detected == "amazon" and not diagnostics["weight_found"]:
            error_reason = error_reason or "NO_WEIGHT"
        if platform_detected == "1688" and diagnostics["images_found_count"] <= 0:
            error_reason = error_reason or "NO_IMAGES"
        if platform_detected == "1688" and not diagnostics["dimensions_found"]:
            error_reason = error_reason or "NO_DIMENSIONS"

        diagnostics["error_code"] = collect_error_code(platform_detected, collect_mode, error_reason) if error_reason else ""
        diagnostics["error_message"] = "采集成功" if not diagnostics["error_code"] else diagnostics["error_code"]
        diagnostics["partial_success"] = any(
            [
                diagnostics["title_found"],
                diagnostics["images_found_count"],
                diagnostics["bullets_found_count"],
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
        merged["source_url"] = url
        merged["source_platform"] = merged["source"]["source_platform"]
        original_url = str((partial_product.get("source") or {}).get("source_url") or "").strip()
        if url and url != original_url:
            merged.pop("product_id", None)
            merged.pop("id", None)
        merged = apply_claimed_platform_drafts(merged, claim_platforms)
        saved = save_product(merged)
        return {
            "ok": diagnostics["success"],
            "product": saved,
            "imagePool": current_image_pool(saved),
            "sourceImages": current_source_images(saved),
            "diagnostics": diagnostics,
            "productsIndex": load_products_index(),
            "error": diagnostics["error_message"] if not diagnostics["success"] else "",
            "next_action": diagnostics.get("next_action", ""),
        }
    except Exception as exc:
        error_message = str(exc)
        if error_message == "MANUAL_MODE":
            error_message = "手动模式请走插件/手动导入接口"
        if not diagnostics.get("error_code"):
            if error_message == "NO_SNAPSHOT":
                reason = "REMOTE" if collect_mode == "browser" else "SELECTOR"
            elif "403" in error_message or "forbidden" in error_message.lower():
                reason = "FORBIDDEN"
            elif "winerror 10013" in error_message.lower() or "访问套接字" in error_message or "socket" in error_message.lower():
                reason = "REMOTE" if platform_detected == "1688" else "NETWORK"
            else:
                reason = "REMOTE" if "remote" in error_message.lower() or "connect" in error_message.lower() else "SELECTOR"
            if platform_detected == "1688" and "profile" in error_message.lower():
                reason = "PROFILE"
            diagnostics["error_code"] = collect_error_code(platform_detected, collect_mode, reason)
        diagnostics["error_message"] = error_message
        diagnostics["finished_at"] = collect_time_iso()
        diagnostics["success"] = False
        diagnostics["partial_success"] = bool(snapshot or html or text)
        if snapshot or html or text:
            fallback_snapshot = snapshot or page_snapshot_from_html(url, html, text, title)
            if platform_detected == "amazon":
                parsed_product = parse_amazon_product(fallback_snapshot, final_url)
            elif platform_detected == "unknown":
                parsed_product = parse_generic_product(fallback_snapshot, final_url)
            else:
                parsed_product = parse_1688_product(fallback_snapshot, final_url)
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
            merged.pop("id", None)
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
        saved = save_product(merged)
        return {
            "ok": False,
            "product": saved,
            "imagePool": current_image_pool(saved),
            "sourceImages": current_source_images(saved),
            "productsIndex": load_products_index(),
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
) -> dict[str, Any]:
    parsed_urls = parse_collect_urls(urls)
    items: list[dict[str, Any]] = []
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
        try:
            result = collect_source_product(url, mode, cookie, detected, platforms)
            product = result.get("product") if isinstance(result.get("product"), dict) else {}
            source = product.get("source") if isinstance(product.get("source"), dict) else {}
            image_pool = current_image_pool(product)
            diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
            row.update(
                {
                    "status": "success" if result.get("ok") else ("partial" if diagnostics.get("partial_success") else "failed"),
                    "ok": bool(result.get("ok")),
                    "title": str(source.get("title") or product.get("name") or ""),
                    "image": str((image_pool[0] if image_pool else {}).get("preview_url") or (image_pool[0] if image_pool else {}).get("url") or ""),
                    "error": str(result.get("error") or ""),
                    "error_code": str(diagnostics.get("error_code") or ""),
                    "next_action": str(result.get("next_action") or diagnostics.get("next_action") or ""),
                    "product_id": str(product.get("product_id") or product.get("id") or ""),
                    "product": product,
                }
            )
        except Exception as exc:
            row.update(
                {
                    "status": "failed",
                    "error": str(exc),
                    "error_code": "COLLECT_FAILED",
                    "next_action": "请检查链接、登录状态或改用手动导入。",
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
        "productsIndex": load_products_index(),
    }


def collect_from_browser_tab(
    tab_url: str = "",
    platform_hint: str = "",
    product_url: str = "",
    port: int = BROWSER_DEBUG_PORT,
    claim_platforms: list[str] | None = None,
    save_only: bool = False,
    mock_tabs: list[dict[str, Any]] | None = None,
    mock_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    original_product = load_product()
    started_at = collect_time_iso()
    status = browser_debug_status(port, mock_tabs)
    if not status.get("connected") and mock_snapshot is None:
        diagnostics = default_collect_diagnostics()
        diagnostics.update(
            {
                "collect_mode": "browser_debugging",
                "started_at": started_at,
                "finished_at": collect_time_iso(),
                "success": False,
                "partial_success": False,
                "error_code": status.get("error_code") or "REMOTE_DEBUGGING_NOT_CONNECTED",
                "error_message": status.get("error_message") or "未连接 Chrome remote debugging",
                "browser_connected": False,
                "debug_port": port,
                "next_action": status.get("next_action") or "请启动专用 Chrome 后重试。",
                "checked_at": collect_time_iso(),
            }
        )
        merged = merge_source_partial_result(original_product, {}, diagnostics)
        merged["source"]["collect_diagnostics"] = diagnostics
        saved = save_product(merged)
        return {"ok": False, "product": saved, "imagePool": current_image_pool(saved), "productsIndex": load_products_index(), "diagnostics": diagnostics, "browserStatus": status, "error": diagnostics["error_message"], "next_action": diagnostics["next_action"], "real_publish_called": False}
    try:
        if mock_snapshot is not None:
            snapshot = deepcopy(mock_snapshot)
            snapshot.setdefault("text", legacy.html_to_text(str(snapshot.get("html") or "")))
            snapshot.setdefault("title", legacy.extract_page_title(str(snapshot.get("html") or "")))
            snapshot.setdefault("image_urls", legacy.extract_product_image_urls(str(snapshot.get("html") or ""), str(snapshot.get("url") or product_url or tab_url), limit=80))
        else:
            raw_tabs = http_json(f"http://127.0.0.1:{port}/json")
            raw_tabs = raw_tabs if isinstance(raw_tabs, list) else []
            target = choose_browser_tab(raw_tabs, tab_url, product_url, platform_hint)
            if not target:
                raise RuntimeError("NO_PRODUCT_TAB_FOUND")
            snapshot = snapshot_from_cdp_target(target, platform_hint)
        final_url = str(snapshot.get("final_url") or snapshot.get("url") or product_url or tab_url or "")
        html_text = str(snapshot.get("html") or "")
        text = str(snapshot.get("text") or "")
        title = str(snapshot.get("title") or snapshot.get("page_title") or "")
        platform_detected = (platform_hint or detect_source_platform(final_url) or detect_source_platform(title) or "unknown").lower()
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
                "debug_port": port,
                "tab_url": final_url,
                "tab_title": title,
            }
        )
        if not diagnostics["html_snapshot_path"] and html_text:
            diagnostics["html_snapshot_path"] = write_collect_debug_html(final_url, html_text, platform_detected)
        error_reason = ""
        if platform_detected == "1688":
            diagnostics["is_login_page"] = is_1688_login_page(final_url, html_text, text, title)
            diagnostics["is_captcha_page"] = is_1688_security_check_page(html_text, text)
            diagnostics["is_security_check_page"] = diagnostics["is_captcha_page"]
            if diagnostics["is_login_page"]:
                error_reason = "LOGIN"
            elif "滑块" in text or "slider" in text.lower():
                error_reason = "SLIDER"
            elif diagnostics["is_captcha_page"]:
                error_reason = "CAPTCHA"
        elif platform_detected == "amazon":
            diagnostics["is_login_page"] = "signin" in final_url.lower() or "sign in" in f"{title} {text}".lower()
            diagnostics["is_captcha_page"] = is_amazon_robot_check_page(final_url, html_text, text, title)
            diagnostics["is_security_check_page"] = diagnostics["is_captcha_page"]
            if diagnostics["is_captcha_page"]:
                error_reason = "ROBOT"
            elif diagnostics["is_login_page"]:
                error_reason = "LOGIN"
            elif is_amazon_region_blocked_page(html_text, text):
                error_reason = "REGION"
        parsed = parse_amazon_product(snapshot, final_url) if platform_detected == "amazon" else parse_1688_product(snapshot, final_url) if platform_detected == "1688" else parse_generic_product(snapshot, final_url)
        source_updates = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
        source_updates = normalize_collect_source_images(source_updates, platform_detected, "browser", claim_platforms)
        flags = snapshot_field_flags(source_updates)
        if not flags["title_found"]:
            error_reason = error_reason or "NO_TITLE"
        if flags["images_found_count"] <= 0:
            error_reason = error_reason or "NO_IMAGES"
        diagnostics["error_code"] = collect_error_code(platform_detected, "browser", error_reason) if error_reason else ""
        diagnostics["error_message"] = "浏览器采集成功" if not diagnostics["error_code"] else diagnostics["error_code"]
        diagnostics["partial_success"] = any([flags["title_found"], flags["images_found_count"], flags["bullets_found_count"], flags["dimensions_found"], flags["weight_found"]])
        diagnostics["success"] = bool(flags["title_found"] and not diagnostics["error_code"])
        diagnostics = finalize_collect_diagnostics(diagnostics, source_updates, platform_detected)
        if save_only:
            return {"ok": True, "saved_only": True, "diagnostics": diagnostics, "browserStatus": status, "html": html_text, "real_publish_called": False}
        merged = merge_source_partial_result(original_product, source_updates, diagnostics)
        if diagnostics["partial_success"]:
            merged["source"]["source_url"] = final_url
            merged["source"]["source_platform"] = platform_detected
            original_url = str((original_product.get("source") or {}).get("source_url") or "").strip()
            if final_url and final_url != original_url:
                merged.pop("product_id", None)
                merged.pop("id", None)
        merged["source"]["collect_status"] = "success" if diagnostics["success"] else ("partial" if diagnostics["partial_success"] else "failed")
        merged["source"]["collect_logs"] = list(merged["source"].get("collect_logs") or [])
        merged["source"]["collect_logs"].append({"started_at": started_at, "finished_at": diagnostics["finished_at"], "mode": "browser_debugging", "platform": platform_detected, "success": diagnostics["success"], "partial_success": diagnostics["partial_success"], "error_code": diagnostics["error_code"], "error_message": diagnostics["error_message"]})
        merged["source"]["collect_diagnostics"] = diagnostics
        merged = apply_claimed_platform_drafts(merged, claim_platforms)
        saved = save_product(merged)
        return {"ok": diagnostics["success"], "product": saved, "imagePool": current_image_pool(saved), "sourceImages": current_source_images(saved), "productsIndex": load_products_index(), "diagnostics": diagnostics, "browserStatus": status, "error": "" if diagnostics["success"] else diagnostics["error_message"], "next_action": diagnostics.get("next_action", ""), "real_publish_called": False}
    except Exception as exc:
        message = str(exc)
        code = "NO_PRODUCT_TAB_FOUND" if "NO_PRODUCT_TAB_FOUND" in message else "TAB_NOT_ACCESSIBLE" if "TAB_NOT_ACCESSIBLE" in message else "REMOTE_DEBUGGING_NOT_CONNECTED"
        diagnostics = default_collect_diagnostics()
        diagnostics.update({"collect_mode": "browser_debugging", "started_at": started_at, "finished_at": collect_time_iso(), "success": False, "partial_success": False, "error_code": code, "error_message": message, "browser_connected": bool(status.get("connected")), "debug_port": port, "next_action": "请确认专用 Chrome 已打开商品页；如果仍失败，点击保存 HTML 快照或使用 HTML 导入 / 手动补充。", "checked_at": collect_time_iso()})
        merged = merge_source_partial_result(original_product, {}, diagnostics)
        merged["source"]["collect_diagnostics"] = diagnostics
        saved = save_product(merged)
        return {"ok": False, "product": saved, "imagePool": current_image_pool(saved), "productsIndex": load_products_index(), "diagnostics": diagnostics, "browserStatus": status, "error": message, "next_action": diagnostics["next_action"], "real_publish_called": False}


def collect_1688_product(url: str, cookie: str | None = None) -> dict[str, Any]:
    if not url.strip():
        raise RuntimeError("请先输入商品链接。")
    cookie = (cookie or "").strip()
    snapshot = fetch_1688_page_snapshot_with_browser_session(url)
    if not snapshot:
        html = maybe_fetch_page_html_with_playwright(url, cookie)
        if html:
            snapshot = {
                "url": url,
                "html": html,
                "text": legacy.html_to_text(html),
                "title": legacy.extract_page_title(html),
                "image_urls": legacy.extract_product_image_urls(html, url, limit=20),
            }
    if not snapshot:
        html = fetch_page_html(url, cookie)
        if html:
            snapshot = {
                "url": url,
                "html": html,
                "text": legacy.html_to_text(html),
                "title": legacy.extract_page_title(html),
                "image_urls": legacy.extract_product_image_urls(html, url, limit=20),
            }
    if not snapshot:
        raise RuntimeError("采集失败：可能需要登录 1688 或完成验证码。请点击“打开 1688 浏览器会话”，登录后重试。")
    html = str(snapshot.get("html") or "")
    text = str(snapshot.get("text") or "")
    if not html.strip() or any(marker in html for marker in VERIFY_MARKERS) or any(marker in text for marker in VERIFY_MARKERS):
        raise RuntimeError("采集失败：1688 返回了登录、验证码或安全验证页面。请在打开的 1688 浏览器中完成验证后重试。")
    product = parse_1688_product(snapshot, url)
    if not product.get("name"):
        raise RuntimeError("采集失败：没有识别到商品标题。请确认链接是商品详情页，或登录 1688 后重试。")
    save_product(product)
    return product


def collect_extension_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    original_product = load_product()
    source_url = str(payload.get("source_url") or "").strip()
    platform = str(payload.get("platform") or detect_source_platform(source_url) or "unknown").strip().lower()
    raw_html = str(payload.get("raw_html_optional") or payload.get("raw_text") or payload.get("text") or "").strip()
    claim_platforms = normalize_platforms(payload.get("platforms")) or ["mercadolibre"]
    explicit_collect_mode = "collect_mode" in payload
    collect_mode = str(payload.get("collect_mode") or "manual").strip().lower()
    image_origin_mode = collect_mode if explicit_collect_mode else "extension"
    if platform in {"manual", ""} and source_url:
        platform = detect_source_platform(source_url) or "unknown"
    parsed_source: dict[str, Any] = {}
    if raw_html:
        html_platform = detect_source_platform(source_url) or platform or "unknown"
        snapshot = page_snapshot_from_html(source_url or "manual://html-import", raw_html)
        parsed_product = parse_amazon_product(snapshot, source_url) if html_platform == "amazon" else parse_1688_product(snapshot, source_url) if html_platform == "1688" else parse_generic_product(snapshot, source_url)
        parsed_source = parsed_product.get("source") if isinstance(parsed_product.get("source"), dict) else {}
        platform = html_platform
    image_values = normalize_list(payload.get("images"))
    manual_image_pool = image_service.materialize_image_values(
        APP_DIR,
        image_values,
        source_url or str(payload.get("title") or "manual-import"),
        claim_platforms,
        collect_image_origin(platform, image_origin_mode),
    )
    manual_updates = {
        "source_url": source_url,
        "source_platform": platform,
        "title": str(payload.get("title") or "").strip(),
        "price": str(payload.get("price") or "").strip(),
        "currency": str(payload.get("currency") or "").strip(),
        "bullets": normalize_list(payload.get("bullets")),
        "description": str(payload.get("description") or "").strip(),
        "images": image_values,
        "image_pool": manual_image_pool,
        "dimensions": payload.get("dimensions") if isinstance(payload.get("dimensions"), dict) else parse_dimensions_text(payload.get("dimensions")),
        "weight_kg": str(payload.get("weight") or "").strip(),
        "material": str(payload.get("material") or "").strip(),
        "package_contents": normalize_list(payload.get("package_contents")),
        "skus": deepcopy(payload.get("sku_options") or []),
        "collect_status": "manual_completed",
    }
    if raw_html:
        try:
            cleaned = collect_service.clean_1688_text(raw_html, source_url)
            if isinstance(cleaned, dict):
                manual_updates["source_price_cny_for_cost"] = cleaned.get("source_price_cny_for_cost") or manual_updates.get("price")
                manual_updates["source_weight_kg"] = cleaned.get("source_weight_kg") or manual_updates.get("weight_kg")
                manual_updates["source_material"] = cleaned.get("source_material") or manual_updates.get("material")
                manual_updates["materials"] = cleaned.get("materials") or ([manual_updates["material"]] if manual_updates.get("material") else [])
                manual_updates["package_includes"] = cleaned.get("package_includes") or manual_updates.get("package_contents")
                manual_updates["source_attributes"] = cleaned.get("source_attributes") or {}
                manual_updates["clean_source_text"] = cleaned.get("clean_source_text") or raw_html[:3000]
                manual_updates["source_text"] = cleaned.get("source_text") or manual_updates["clean_source_text"]
                if cleaned.get("dimensions") and not manual_updates.get("dimensions"):
                    manual_updates["dimensions"] = parse_dimensions_text(cleaned.get("dimensions"))
        except Exception:
            manual_updates["source_text"] = raw_html[:3000]
    source_updates = deepcopy(parsed_source)
    for key, value in manual_updates.items():
        if value not in (None, "", [], {}):
            source_updates[key] = value
    source_updates = normalize_collect_source_images(source_updates, platform, image_origin_mode, claim_platforms)
    diagnostics = default_collect_diagnostics()
    diagnostics.update(
        {
            "collect_mode": collect_mode,
            "source_url": source_url,
            "normalized_url": source_url,
            "platform_detected": platform,
            "started_at": collect_time_iso(),
            "finished_at": collect_time_iso(),
            "success": True,
            "partial_success": True,
            "error_code": "",
            "error_message": "HTML 导入" if raw_html else "手动补充",
            "page_title": str(payload.get("title") or parsed_source.get("title") or "").strip(),
            "final_url": source_url,
            "html_snapshot_path": "",
            "screenshot_path": "",
            "parser_version": "collect-v2",
        }
    )
    if raw_html:
        diagnostics["html_snapshot_path"] = write_collect_debug_html(source_url or "manual", raw_html, "manual")
    diagnostics = finalize_collect_diagnostics(diagnostics, source_updates, platform)
    merged = merge_source_partial_result(original_product, source_updates, diagnostics)
    merged["source"]["collect_status"] = "manual_completed"
    merged["source"]["collect_logs"] = list(merged["source"].get("collect_logs") or [])
    merged["source"]["collect_logs"].append(
        {
            "started_at": diagnostics["started_at"],
            "finished_at": diagnostics["finished_at"],
            "mode": collect_mode,
            "platform": platform,
            "success": True,
            "partial_success": True,
            "error_code": "",
            "error_message": diagnostics["error_message"],
        }
    )
    merged["source"]["collect_diagnostics"] = diagnostics
    original_url = str((original_product.get("source") or {}).get("source_url") or "").strip()
    if source_url and source_url != original_url:
        merged.pop("product_id", None)
        merged.pop("id", None)
    merged = apply_claimed_platform_drafts(merged, claim_platforms)
    saved = save_product(merged)
    return {
        "ok": True,
        "product": saved,
        "imagePool": current_image_pool(saved),
        "sourceImages": current_source_images(saved),
        "productsIndex": load_products_index(),
        "diagnostics": diagnostics,
        "error": "",
    }


__all__ = [
    "collect_1688_product",
    "collect_batch_products",
    "collect_extension_payload",
    "collect_from_browser_tab",
    "collect_source_product",
]
