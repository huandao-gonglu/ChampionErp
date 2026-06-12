# -*- coding: utf-8 -*-
from __future__ import annotations

import os

from .common import JsonRequestHandler
from .. import runtime as app
from ..runtime import *  # noqa: F403 - route units mirror legacy runtime globals.


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    if parsed.path == "/api/collect-source":
        body = handler.read_body()
        try:
            result = collect_source_product(
                body.get("url", ""),
                body.get("mode", "browser"),
                body.get("cookie", ""),
                body.get("platform", ""),
                body.get("platforms") if isinstance(body.get("platforms"), list) else None,
            )
            status = 200
            result["productsIndex"] = load_products_index()
            handler.send_json(result, status)
        except Exception as exc:
            handler.send_json({"ok": False, "error": str(exc)}, 400)
        return True
    if parsed.path == "/api/collect-batch":
        body = handler.read_body()
        result = collect_batch_products(
            body.get("urls") if body.get("urls") is not None else body.get("url", ""),
            body.get("mode", "browser"),
            body.get("cookie", ""),
            body.get("platform", ""),
            body.get("platforms") if isinstance(body.get("platforms"), list) else None,
        )
        handler.send_json(result, 200)
        return True
    if parsed.path == "/api/claim-products":
        body = handler.read_body()
        result = claim_products_to_platforms(
            body.get("product_ids") if isinstance(body.get("product_ids"), list) else [],
            body.get("platforms") if isinstance(body.get("platforms"), list) else None,
        )
        handler.send_json(result, 200 if result.get("ok") else 400)
        return True
    if parsed.path == "/api/collect-1688":
        body = handler.read_body()
        try:
            pasted = str(body.get("text") or body.get("html") or body.get("source_text") or "").strip()
            if pasted:
                cleaned = collect_service.clean_1688_text(pasted, str(body.get("url") or body.get("source_url") or ""))
                if body.get("save"):
                    product = normalize_product_fields(body.get("product") or load_product())
                    product.update(
                        {
                            "source_platform": "1688",
                            "source_url": cleaned.get("source_url") or product.get("source_url") or "",
                            "source_price_cny": cleaned.get("source_price_cny", ""),
                            "source_price_cny_for_cost": cleaned.get("source_price_cny_for_cost", ""),
                            "source_material": cleaned.get("source_material", ""),
                            "source_weight_kg": cleaned.get("source_weight_kg", ""),
                            "materials": cleaned.get("materials") or product.get("materials", []),
                            "dimensions": cleaned.get("dimensions") or product.get("dimensions", ""),
                            "colors": cleaned.get("colors") or product.get("colors", []),
                            "package_includes": cleaned.get("package_includes") or product.get("package_includes", []),
                            "target_customer": cleaned.get("target_customer") or product.get("target_customer", ""),
                            "source_text": cleaned.get("source_text", ""),
                            "supplemental_info": cleaned.get("supplemental_info", ""),
                        }
                    )
                    source = product.get("source") if isinstance(product.get("source"), dict) else default_source()
                    source.update(
                        {
                            "source_platform": "1688",
                            "source_url": cleaned.get("source_url") or source.get("source_url") or "",
                            "price": cleaned.get("source_price_cny") or source.get("price") or "",
                            "currency": "CNY" if cleaned.get("source_price_cny") else source.get("currency", ""),
                            "description": cleaned.get("clean_source_text") or source.get("description") or "",
                            "attributes": cleaned.get("source_attributes") or {},
                        }
                    )
                    product["source"] = source
                    saved = save_product(product)
                    cleaned["product"] = saved
                    cleaned["productsIndex"] = load_products_index()
                handler.send_json(cleaned, 200 if cleaned.get("ok") else 400)
                return True
            result = collect_source_product(body.get("url", ""), body.get("mode", "browser"), body.get("cookie", ""), "1688", body.get("platforms") if isinstance(body.get("platforms"), list) else None)
            status = 200 if result.get("ok") or (result.get("diagnostics") or {}).get("partial_success") else 400
            result["productsIndex"] = load_products_index()
            handler.send_json(result, status)
        except Exception as exc:
            handler.send_json({"ok": False, "error": str(exc)}, 400)
        return True
    if parsed.path == "/api/collect-1688-clean":
        body = handler.read_body()
        cleaned = collect_service.clean_1688_text(str(body.get("text") or body.get("html") or ""), str(body.get("url") or ""))
        handler.send_json(cleaned, 200 if cleaned.get("ok") else 400)
        return True
    if parsed.path == "/api/collect-from-browser-tab":
        body = handler.read_body()
        result = collect_from_browser_tab(
            tab_url=str(body.get("tab_url") or ""),
            platform_hint=str(body.get("platform_hint") or ""),
            product_url=str(body.get("product_url") or body.get("url") or ""),
            port=int(body.get("port") or BROWSER_DEBUG_PORT),
            claim_platforms=body.get("platforms") if isinstance(body.get("platforms"), list) else None,
            save_only=bool(body.get("save_only")),
            mock_tabs=body.get("mock_tabs") if isinstance(body.get("mock_tabs"), list) else None,
            mock_snapshot=body.get("mock_snapshot") if isinstance(body.get("mock_snapshot"), dict) else None,
        )
        handler.send_json(result, 200)
        return True
    if parsed.path == "/api/browser-debug/open-profile":
        BROWSER_DEBUG_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(BROWSER_DEBUG_PROFILE_DIR))  # type: ignore[attr-defined]
            handler.send_json({"ok": True, "profile_dir": str(BROWSER_DEBUG_PROFILE_DIR)})
        except Exception as exc:
            handler.send_json({"ok": False, "error": str(exc), "profile_dir": str(BROWSER_DEBUG_PROFILE_DIR)}, 400)
        return True
    if parsed.path == "/api/open-1688-browser":
        try:
            open_browser_debug_session("https://www.1688.com/", BROWSER_DEBUG_PORT, "1688")
            handler.send_json({"ok": True, "message": f"已用调试端口 {BROWSER_DEBUG_PORT} 打开 1688 浏览器会话，请先登录后再采集。", "port": BROWSER_DEBUG_PORT})
        except Exception as exc:
            handler.send_json({"ok": False, "error": str(exc)}, 400)
        return True
    if parsed.path == "/api/collect-extension-payload":
        body = handler.read_body()
        try:
            result = collect_extension_payload(body)
            handler.send_json(result, 200 if result.get("ok") else 400)
        except Exception as exc:
            handler.send_json({"ok": False, "error": str(exc)}, 400)
        return True
    return False
