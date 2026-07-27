from __future__ import annotations

import os
from typing import Any

from erp_web.services import collect_service

from erp_web.runtime_units.collect_helpers import claim_products_to_platforms
from erp_web.runtime_units.product_store import load_products_index
from erp_web.runtime_units.runtime_common import BROWSER_DEBUG_PORT, BROWSER_DEBUG_PROFILE_DIR
from erp_web.runtime_units.source_collect_browser import open_browser_debug_session
from erp_web.runtime_units.source_collect_workflows import (
    collect_1688_payload_service,
    collect_batch_products,
    collect_extension_payload,
    collect_from_browser_tab,
    collect_source_product,
)
from erp_web.schemas.api import ApiResponse

ResponseWithStatus = tuple[ApiResponse, int]


def collect_source_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        result = collect_source_product(
            body.get("url", ""),
            body.get("mode", "browser"),
            body.get("cookie", ""),
            body.get("platform", ""),
            body.get("platforms") if isinstance(body.get("platforms"), list) else None,
            body.get("1688_api") if isinstance(body.get("1688_api"), dict) else None,
        )
        result["productsIndex"] = load_products_index()
        return result, 200
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400


def collect_batch_payload(body: dict[str, Any]) -> ApiResponse:
    return collect_batch_products(
        body.get("urls") if body.get("urls") is not None else body.get("url", ""),
        body.get("mode", "browser"),
        body.get("cookie", ""),
        body.get("platform", ""),
        body.get("platforms") if isinstance(body.get("platforms"), list) else None,
        body.get("1688_api") if isinstance(body.get("1688_api"), dict) else None,
    )


def claim_products_payload(body: dict[str, Any]) -> ResponseWithStatus:
    platforms = body.get("platforms") if isinstance(body.get("platforms"), list) else None
    if platforms is None and body.get("platform"):
        platforms = [body.get("platform")]
    result = claim_products_to_platforms(
        body.get("product_ids") if isinstance(body.get("product_ids"), list) else [],
        platforms,
    )
    return result, 200 if result.get("ok") else 400


def collect_1688_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        result = collect_1688_payload_service(body)
        status = 200 if result.get("ok") or (result.get("diagnostics") or {}).get("partial_success") else 400
        return result, status
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400


def clean_1688_payload(body: dict[str, Any]) -> ResponseWithStatus:
    cleaned = collect_service.clean_1688_text(str(body.get("text") or body.get("html") or ""), str(body.get("url") or ""))
    return cleaned, 200 if cleaned.get("ok") else 400


def collect_from_browser_tab_payload(body: dict[str, Any]) -> ApiResponse:
    return collect_from_browser_tab(
        tab_url=str(body.get("tab_url") or ""),
        platform_hint=str(body.get("platform_hint") or ""),
        product_url=str(body.get("product_url") or body.get("url") or ""),
        port=int(body.get("port") or BROWSER_DEBUG_PORT),
        claim_platforms=body.get("platforms") if isinstance(body.get("platforms"), list) else None,
        save_only=bool(body.get("save_only")),
        mock_tabs=body.get("mock_tabs") if isinstance(body.get("mock_tabs"), list) else None,
        mock_snapshot=body.get("mock_snapshot") if isinstance(body.get("mock_snapshot"), dict) else None,
    )


def open_browser_profile_payload() -> ResponseWithStatus:
    BROWSER_DEBUG_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(str(BROWSER_DEBUG_PROFILE_DIR))  # type: ignore[attr-defined]
        return {"ok": True, "profile_dir": str(BROWSER_DEBUG_PROFILE_DIR)}, 200
    except Exception as exc:
        return {"ok": False, "error": str(exc), "profile_dir": str(BROWSER_DEBUG_PROFILE_DIR)}, 400


def open_1688_browser_payload() -> ResponseWithStatus:
    try:
        open_browser_debug_session("https://www.1688.com/", BROWSER_DEBUG_PORT, "1688")
        return {
            "ok": True,
            "message": f"已用调试端口 {BROWSER_DEBUG_PORT} 打开 1688 浏览器会话，请先登录后再采集。",
            "port": BROWSER_DEBUG_PORT,
        }, 200
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400


def collect_extension_payload_response(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        result = collect_extension_payload(body)
        return result, 200 if result.get("ok") else 400
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 400


__all__ = [
    "claim_products_payload",
    "clean_1688_payload",
    "collect_1688_payload",
    "collect_batch_payload",
    "collect_extension_payload_response",
    "collect_from_browser_tab_payload",
    "collect_source_payload",
    "open_1688_browser_payload",
    "open_browser_profile_payload",
]
