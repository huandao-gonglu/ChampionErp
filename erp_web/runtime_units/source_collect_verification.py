"""浏览器采集等待结果及后端验证入口。"""
from typing import Any
from erp_shared.collection import same_collection_page, VERIFICATION_REASONS, WAIT_MESSAGE
from erp_web.schemas.collection import CollectionVerificationStatus
from erp_web.services.desktop_connection_service import call_desktop, DesktopUnavailable
from .collect_helpers import collect_error_code, collect_time_iso

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
        return call_desktop('collect.verify', {'browser_tab_id': browser_tab_id, 'source_url': source_url}, timeout=30)
    except DesktopUnavailable as exc:
        return {'ok': False, 'status': 'unavailable', 'message': str(exc)}


__all__ = ['inspect_collection_verification', 'same_collection_page', 'waiting_verification_result']
