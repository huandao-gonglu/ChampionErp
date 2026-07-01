# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import subprocess
import time
import urllib.parse
import urllib.request
from typing import Any

from erp_web.services import html_extract_service as legacy

from .browser_debug import (
    CdpWebSocket,
    browser_debug_commands,
    browser_debug_next_action,
    find_chrome_path,
    parse_cookie_header,
)
from .category_refresh import http_json
from .collect_helpers import detect_source_platform, save_collect_snapshot_artifacts
from .runtime_common import APP_DIR, BROWSER_DEBUG_PORT, BROWSER_PROFILE_DIR

def wait_for_cdp(port: int, timeout: int = 15) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            http_json(f"http://127.0.0.1:{port}/json/version")
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Chrome 调试端口启动超时")


def normalize_browser_tab(page: dict[str, Any]) -> dict[str, Any]:
    url = str(page.get("url") or "")
    title = str(page.get("title") or "")
    return {
        "title": title,
        "url": url,
        "platform_detected": detect_source_platform(url) or detect_source_platform(title) or "unknown",
        "type": page.get("type") or "",
        "id": page.get("id") or "",
    }


def browser_debug_status(port: int = BROWSER_DEBUG_PORT, tabs_override: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    commands = browser_debug_commands(port)
    try:
        raw_tabs = tabs_override if tabs_override is not None else http_json(f"http://127.0.0.1:{port}/json")
        if not isinstance(raw_tabs, list):
            raw_tabs = []
        page_tabs = [tab for tab in raw_tabs if isinstance(tab, dict) and str(tab.get("type") or "page") == "page"]
        tabs = [normalize_browser_tab(tab) for tab in page_tabs]
        product_tabs = [tab for tab in tabs if tab["platform_detected"] in {"amazon", "1688"}]
        error_code = "" if product_tabs else "NO_PRODUCT_TAB_FOUND"
        return {
            "ok": True,
            "connected": True,
            "port": port,
            "browser": "chrome",
            "tabs_count": len(tabs),
            "current_tabs": tabs,
            "error_code": error_code,
            "error_message": "" if product_tabs else "已连接 Chrome，但未发现 Amazon / 1688 商品页标签。",
            "next_action": "在专用 Chrome 当前标签页打开 Amazon / 1688 商品详情页后点击从当前页面采集。" if error_code else "已连接，可从当前标签页采集。",
            **commands,
        }
    except FileNotFoundError as exc:
        return {"ok": True, "connected": False, "port": port, "browser": "chrome", "tabs_count": 0, "current_tabs": [], "error_code": "CHROME_NOT_FOUND", "error_message": str(exc), "next_action": f"请先确认 Chrome 已安装。{browser_debug_next_action()}", **commands}
    except Exception as exc:
        message = str(exc)
        code = "DEBUG_PORT_BLOCKED" if "10013" in message or "permission" in message.lower() else "REMOTE_DEBUGGING_NOT_CONNECTED"
        return {"ok": True, "connected": False, "port": port, "browser": "chrome", "tabs_count": 0, "current_tabs": [], "error_code": code, "error_message": message or "未连接 Chrome remote debugging。", "next_action": browser_debug_next_action(), **commands}


def choose_browser_tab(raw_tabs: list[dict[str, Any]], tab_url: str = "", product_url: str = "", platform_hint: str = "") -> dict[str, Any] | None:
    pages = [tab for tab in raw_tabs if isinstance(tab, dict) and str(tab.get("type") or "page") == "page"]
    tab_url = str(tab_url or "").strip()
    product_url = str(product_url or "").strip()
    platform_hint = str(platform_hint or "").strip().lower()
    if tab_url:
        for tab in pages:
            if tab_url in str(tab.get("url") or ""):
                return tab
    if product_url:
        for tab in pages:
            if product_url in str(tab.get("url") or "") or str(tab.get("url") or "") in product_url:
                return tab
    if platform_hint in {"amazon", "1688"}:
        for tab in pages:
            if detect_source_platform(str(tab.get("url") or "")) == platform_hint:
                return tab
    for tab in pages:
        if detect_source_platform(str(tab.get("url") or "")) in {"amazon", "1688"}:
            return tab
    return pages[0] if pages else None


def open_browser_debug_session(url: str, port: int, profile_name: str) -> None:
    chrome = find_chrome_path()
    profile = BROWSER_PROFILE_DIR if profile_name.startswith("1688") else APP_DIR / "browser_profile" / profile_name
    profile.mkdir(parents=True, exist_ok=True)
    try:
        http_json(f"http://127.0.0.1:{port}/json/version")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe='')}", timeout=5)
        except Exception:
            pass
    except Exception:
        subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--disable-popup-blocking",
                "--start-maximized",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_for_cdp(port)


def cdp_target_for_url(port: int, url: str) -> dict[str, Any]:
    pages = http_json(f"http://127.0.0.1:{port}/json/list")
    for page in pages:
        page_url = page.get("url", "")
        if page.get("type") == "page" and (url in page_url or "1688.com" in page_url):
            return page
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe='')}",
            timeout=5,
        ):
            pass
    except Exception:
        pass
    time.sleep(2)
    pages = http_json(f"http://127.0.0.1:{port}/json/list")
    for page in pages:
        page_url = page.get("url", "")
        if page.get("type") == "page" and (url in page_url or "1688.com" in page_url):
            return page
    for page in pages:
        if page.get("type") == "page":
            return page
    raise RuntimeError("没有找到可用的 Chrome 页面")


def fetch_page_html_with_browser_session(url: str, port: int | None = None) -> str | None:
    snapshot = fetch_page_snapshot_with_browser_session(url, port=port, profile_name=detect_source_platform(url) or "1688")
    if snapshot:
        html = str(snapshot.get("html") or "")
        return html or None
    return None


def fetch_page_snapshot_with_browser_session(url: str, port: int | None = None, profile_name: str = "1688") -> dict[str, Any] | None:
    port = port or BROWSER_DEBUG_PORT
    try:
        open_browser_debug_session(url, port, profile_name)
        target = cdp_target_for_url(port, url)
        cdp = CdpWebSocket(target["webSocketDebuggerUrl"])
        try:
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            cdp.call("DOM.enable")
            cdp.call("Page.navigate", {"url": url}, timeout=20)
            deadline = time.time() + 25
            while time.time() < deadline:
                ready = cdp.call("Runtime.evaluate", {"expression": "document.readyState", "returnByValue": True}, timeout=10)
                state = ready.get("result", {}).get("value", "")
                if state == "complete":
                    break
                time.sleep(1)
            for _ in range(3):
                try:
                    cdp.call("Runtime.evaluate", {"expression": "window.scrollTo(0, document.body ? document.body.scrollHeight : 0)"}, timeout=10)
                    time.sleep(0.8)
                except Exception:
                    break
            values: dict[str, Any] = {}
            expressions = {
                "html": "document.documentElement.outerHTML",
                "text": "document.body ? document.body.innerText : ''",
                "title": "document.title || ''",
                "url": "location.href || ''",
                "images": "(() => [...new Set([...document.images].map(img => img.currentSrc || img.src || '').filter(Boolean))].slice(0, 40))()",
            }
            for key, expression in expressions.items():
                try:
                    result = cdp.call("Runtime.evaluate", {"expression": expression, "returnByValue": True}, timeout=30)
                    values[key] = result.get("result", {}).get("value", [] if key == "images" else "")
                except Exception:
                    values[key] = [] if key == "images" else ""
            screenshot_base64 = ""
            try:
                screenshot_result = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True}, timeout=30)
                screenshot_base64 = str(screenshot_result.get("data") or "")
            except Exception:
                screenshot_base64 = ""
            html = str(values.get("html") or "")
            if not html.strip():
                return None
            artifacts = save_collect_snapshot_artifacts(
                detect_source_platform(url) or profile_name,
                str(values.get("url") or url),
                html=html,
                screenshot_base64=screenshot_base64,
                text=str(values.get("text") or ""),
            )
            return {
                "url": values.get("url") or url,
                "html": html,
                "text": values.get("text") or "",
                "title": values.get("title") or "",
                "image_urls": values.get("images") or [],
                "html_snapshot_path": artifacts.get("html_snapshot_path", ""),
                "screenshot_path": artifacts.get("screenshot_path", ""),
                "final_url": values.get("url") or url,
                "page_title": values.get("title") or "",
            }
        finally:
            cdp.close()
    except Exception:
        return None


__all__ = [
    "browser_debug_status",
    "choose_browser_tab",
    "fetch_1688_page_snapshot_with_browser_session",
    "fetch_page_html",
    "fetch_page_html_with_browser_session",
    "fetch_page_html_with_status",
    "fetch_page_snapshot_with_browser_session",
    "maybe_fetch_page_html_with_playwright",
    "open_browser_debug_session",
    "snapshot_from_cdp_target",
]


def snapshot_from_cdp_target(target: dict[str, Any], platform_hint: str = "") -> dict[str, Any]:
    if not isinstance(target, dict) or not target.get("webSocketDebuggerUrl"):
        raise RuntimeError("TAB_NOT_ACCESSIBLE")
    cdp = CdpWebSocket(str(target["webSocketDebuggerUrl"]))
    try:
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        for _ in range(3):
            try:
                cdp.call("Runtime.evaluate", {"expression": "window.scrollTo(0, document.body ? document.body.scrollHeight : 0)"}, timeout=10)
                time.sleep(0.6)
            except Exception:
                break
        values: dict[str, Any] = {}
        expressions = {
            "html": "document.documentElement ? document.documentElement.outerHTML : ''",
            "text": "document.body ? document.body.innerText : ''",
            "title": "document.title || ''",
            "url": "location.href || ''",
            "images": "(() => [...new Set([...document.images].map(img => img.currentSrc || img.src || '').filter(Boolean))].slice(0, 80))()",
        }
        for key, expression in expressions.items():
            result = cdp.call("Runtime.evaluate", {"expression": expression, "returnByValue": True}, timeout=30)
            values[key] = result.get("result", {}).get("value", [] if key == "images" else "")
        screenshot_base64 = ""
        try:
            screenshot_result = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True}, timeout=30)
            screenshot_base64 = str(screenshot_result.get("data") or "")
        except Exception:
            screenshot_base64 = ""
        url = str(values.get("url") or target.get("url") or "")
        html_text = str(values.get("html") or "")
        if not html_text:
            raise RuntimeError("TAB_NOT_ACCESSIBLE")
        platform = platform_hint or detect_source_platform(url) or "unknown"
        artifacts = save_collect_snapshot_artifacts(
            platform,
            url,
            html=html_text,
            screenshot_base64=screenshot_base64,
            text=str(values.get("text") or ""),
        )
        return {
            "url": url,
            "html": html_text,
            "text": str(values.get("text") or ""),
            "title": str(values.get("title") or target.get("title") or ""),
            "image_urls": values.get("images") if isinstance(values.get("images"), list) else [],
            "html_snapshot_path": artifacts.get("html_snapshot_path", ""),
            "screenshot_path": artifacts.get("screenshot_path", ""),
            "final_url": url,
            "page_title": str(values.get("title") or target.get("title") or ""),
        }
    finally:
        cdp.close()


def fetch_1688_page_snapshot_with_browser_session(url: str, port: int | None = None) -> dict[str, Any] | None:
    return fetch_page_snapshot_with_browser_session(url, port=port, profile_name="1688")


def fetch_page_html(url: str, cookie: str = "") -> str:
    try:
        return legacy.fetch_url_html(url, cookie)
    except TypeError:
        return legacy.fetch_url_html(url)


def fetch_page_html_with_status(url: str, cookie: str = "") -> tuple[str, int]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    if cookie.strip():
        headers["Cookie"] = cookie.strip()
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
        html = raw.decode("utf-8", errors="ignore")
        return html, int(getattr(response, "status", 200) or 200)


def maybe_fetch_page_html_with_playwright(url: str, cookie: str = "") -> str | None:
    if os.environ.get("ERP_USE_PLAYWRIGHT", "").strip() != "1":
        return None
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
                viewport={"width": 1440, "height": 1280},
            )
            if cookie.strip():
                context.add_cookies(parse_cookie_header(cookie, url))
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=45000)
            html = page.content()
            browser.close()
            return html
    except Exception:
        return None
