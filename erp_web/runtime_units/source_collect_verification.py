"""只读检查人工验证是否结束；不导航、不刷新、不操作验证码。"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from erp_web.context import get_context
from erp_web.schemas.collection import CollectionVerificationStatus
from erp_web.services.browser_debug_service import CdpWebSocket
from .category_refresh import http_json
from .collect_helpers import collect_error_code, collect_time_iso, detect_source_platform
from .source_sites import source_site

VERIFICATION_REASONS = frozenset({"LOGIN", "CAPTCHA", "SLIDER", "SECURITY", "ROBOT"})
WAIT_MESSAGE = "等待人工验证：请在采集浏览器的原商品页完成登录或验证码，完成后自动继续。"


def same_collection_page(expected_url: str, current_url: str) -> bool:
    expected, current = urlsplit(expected_url), urlsplit(current_url)
    if expected.scheme not in {"http", "https"} or not expected.hostname:
        return False
    if expected._replace(fragment="") == current._replace(fragment=""):
        return True
    # 1688 验证后的追踪参数不影响商品身份；其他 URL 的查询参数可能是商品 ID。
    return bool(
        expected.hostname == current.hostname == "detail.1688.com"
        and expected.path == current.path
        and re.fullmatch(r"/offer/\d+\.html", expected.path)
    )


def waiting_verification_result(snapshot: dict[str, Any], source_url: str, platform: str,
                                diagnostics: dict[str, Any], reason: str) -> dict[str, Any] | None:
    tab_id = str(snapshot.get("browser_tab_id") or "")
    if reason not in VERIFICATION_REASONS:
        return None
    diagnostics.update({"status": "waiting_verification", "success": False, "partial_success": False,
                        "error_code": collect_error_code(platform, "browser", reason),
                        "error_message": "", "next_action": WAIT_MESSAGE, "finished_at": ""})
    if not tab_id:
        message = "页面需要人工验证，但未连接到可继续的浏览器标签。请使用采集浏览器打开原商品页后采集。"
        diagnostics.update({"status": "failed", "error_message": message, "next_action": message, "finished_at": collect_time_iso()})
        return {"ok": False, "status": "failed", "diagnostics": diagnostics, "error": message, "next_action": message}
    return {"ok": False, "status": "waiting_verification", "diagnostics": diagnostics,
            "verification": {"browser_tab_id": tab_id, "source_url": source_url, "platform": platform},
            "next_action": WAIT_MESSAGE}


def inspect_collection_verification(browser_tab_id: str, source_url: str) -> CollectionVerificationStatus:
    try:
        port = get_context().paths.browser_debug_port
        tabs = http_json(f"http://127.0.0.1:{port}/json")
        target = next((tab for tab in tabs if tab.get("id") == browser_tab_id and tab.get("type") == "page"), None)
        if not target:
            return {"ok": True, "status": "unavailable", "message": "原商品标签页已关闭，请重新开始采集。"}
        cdp = CdpWebSocket(target["webSocketDebuggerUrl"])
        try:
            result = cdp.call("Runtime.evaluate", {
                "expression": "({url:location.href,title:document.title,text:document.body?.innerText||'',html:document.documentElement?.outerHTML||'',ready:document.readyState})",
                "returnByValue": True,
            }, timeout=10)
            page = result.get("result", {}).get("value") or {}
        finally:
            cdp.close()
        if not page or page.get("ready") != "complete":
            return {"ok": True, "status": "loading", "message": "正在等待原商品页加载完成…"}
        site = source_site(detect_source_platform(source_url) or "unknown")
        _, reason = site.diagnose(str(page.get("url") or ""), str(page.get("html") or ""),
                                  str(page.get("text") or ""), str(page.get("title") or ""), detect_slider=True)
        if reason in VERIFICATION_REASONS:
            return {"ok": True, "status": "waiting_verification", "message": WAIT_MESSAGE}
        if not same_collection_page(source_url, str(page.get("url") or "")):
            return {"ok": True, "status": "unavailable", "message": "原标签页已离开目标商品，请返回原商品页后重新采集。"}
        return {"ok": True, "status": "ready", "message": "验证已完成，继续采集原商品页。"}
    except Exception:
        return {"ok": True, "status": "unavailable", "message": "无法读取原商品页，请确认采集浏览器仍保持打开后重试。"}


__all__ = ["inspect_collection_verification", "same_collection_page", "waiting_verification_result"]
