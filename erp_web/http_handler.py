# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler
from typing import Any

from routes import image_routes, static_routes
from . import runtime as app
from .runtime import *  # noqa: F403 - route methods intentionally mirror runtime globals.

APP_MODULE = app

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, data: Any, status: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_body(self) -> dict[str, Any]:
        return safe_json_body(self)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"/", "/collect", "/library", "/edit", "/media", "/pricing", "/publish", "/settings", "/auth", "/logs", "/pending"}:
            page = {
                "/": "workbench",
                "/collect": "collect",
                "/library": "library",
                "/edit": "edit",
                "/media": "media",
                "/pricing": "pricing",
                "/publish": "publish",
                "/pending": "pending",
                "/settings": "settings",
                "/auth": "auth",
                "/logs": "logs",
            }.get(parsed.path, "workbench")
            raw = html_page(page).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if parsed.path == "/api/state":
            prod = load_product()
            store_cfg = load_store_config()
            self.send_json(
                {
                    "ok": True,
                    "product": prod,
                    "appConfig": load_app_config(),
                    "storeConfig": store_cfg,
                    "storeAuthSummary": summarize_store_auth_states(store_cfg),
                    "mercadolibreAuthChecklist": mercadolibre_auth_checklist(store_cfg.get("mercadolibre", {})),
                    "imagePool": current_image_pool(prod),
                    "sourceImages": current_source_images(prod),
                    "generatedImages": current_generated_images(),
                    "publishLogs": load_publish_logs(),
                    "productsIndex": load_products_index(),
                    "outputDir": str(OUTPUT_DIR),
                }
            )
            return
        if parsed.path == "/api/products-index":
            self.send_json({"ok": True, "items": load_products_index()})
            return
        if parsed.path == "/api/browser-debug/status":
            params = urllib.parse.parse_qs(parsed.query)
            port = int((params.get("port") or [str(BROWSER_DEBUG_PORT)])[0] or BROWSER_DEBUG_PORT)
            self.send_json(browser_debug_status(port))
            return
        if parsed.path == "/api/publish-logs":
            self.send_json({"ok": True, "items": load_publish_logs()})
            return
        if parsed.path == "/api/ai-config":
            config_service.write_env_template(APP_DIR)
            self.send_json({"ok": True, "config": config_service.public_ai_config(APP_DIR, load_app_config())})
            return
        if parsed.path == "/api/publish-bus/status":
            params = urllib.parse.parse_qs(parsed.query)
            job_id = str((params.get("job_id") or [""])[0]).strip()
            if not job_id:
                self.send_json({"ok": False, "error": "缺少 job_id"}, 400)
                return
            try:
                self.send_json({"ok": True, "job": persist_publish_bus_terminal_results(PUBLISHING_BUS.get_status(job_id))})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 404)
            return
        if parsed.path == "/api/category-cache/refresh-status":
            params = urllib.parse.parse_qs(parsed.query)
            job_id = str((params.get("job_id") or [""])[0]).strip()
            if not job_id:
                self.send_json({"ok": False, "error": "缺少 job_id"}, 400)
                return
            try:
                self.send_json({"ok": True, "job": get_category_cache_refresh_job(job_id)})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 404)
            return
        if parsed.path == "/file":
            static_routes.serve_file(self, parsed, APP_MODULE)
            return
        if parsed.path.startswith("/assets/"):
            static_routes.serve_frontend_asset(self, parsed, APP_MODULE)
            return
        if parsed.path == "/auth/mercadolibre":
            static_routes.serve_ml_auth_page(self)
            return
        if parsed.path == "/auth/wildberries":
            static_routes.serve_store_help_page(self, "wildberries")
            return
        if parsed.path == "/auth/ozon":
            static_routes.serve_store_help_page(self, "ozon")
            return
        if parsed.path == "/auth/mercadolibre/callback":
            static_routes.handle_ml_callback(self, parsed, APP_MODULE)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if image_routes.handle_post(self, parsed.path, APP_MODULE):
                return
            if parsed.path == "/api/collect-source":
                body = self.read_body()
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
                    self.send_json(result, status)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            if parsed.path == "/api/collect-batch":
                body = self.read_body()
                result = collect_batch_products(
                    body.get("urls") if body.get("urls") is not None else body.get("url", ""),
                    body.get("mode", "browser"),
                    body.get("cookie", ""),
                    body.get("platform", ""),
                    body.get("platforms") if isinstance(body.get("platforms"), list) else None,
                )
                self.send_json(result, 200)
                return
            if parsed.path == "/api/claim-products":
                body = self.read_body()
                result = claim_products_to_platforms(
                    body.get("product_ids") if isinstance(body.get("product_ids"), list) else [],
                    body.get("platforms") if isinstance(body.get("platforms"), list) else None,
                )
                self.send_json(result, 200 if result.get("ok") else 400)
                return
            if parsed.path == "/api/collect-1688":
                body = self.read_body()
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
                        self.send_json(cleaned, 200 if cleaned.get("ok") else 400)
                        return
                    result = collect_source_product(body.get("url", ""), body.get("mode", "browser"), body.get("cookie", ""), "1688", body.get("platforms") if isinstance(body.get("platforms"), list) else None)
                    status = 200 if result.get("ok") or (result.get("diagnostics") or {}).get("partial_success") else 400
                    result["productsIndex"] = load_products_index()
                    self.send_json(result, status)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            if parsed.path == "/api/collect-1688-clean":
                body = self.read_body()
                cleaned = collect_service.clean_1688_text(str(body.get("text") or body.get("html") or ""), str(body.get("url") or ""))
                self.send_json(cleaned, 200 if cleaned.get("ok") else 400)
                return
            if parsed.path == "/api/collect-from-browser-tab":
                body = self.read_body()
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
                self.send_json(result, 200)
                return
            if parsed.path == "/api/browser-debug/open-profile":
                BROWSER_DEBUG_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
                try:
                    os.startfile(str(BROWSER_DEBUG_PROFILE_DIR))  # type: ignore[attr-defined]
                    self.send_json({"ok": True, "profile_dir": str(BROWSER_DEBUG_PROFILE_DIR)})
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc), "profile_dir": str(BROWSER_DEBUG_PROFILE_DIR)}, 400)
                return
            if parsed.path == "/api/open-1688-browser":
                try:
                    open_browser_debug_session("https://www.1688.com/", BROWSER_DEBUG_PORT, "1688")
                    self.send_json({"ok": True, "message": f"已用调试端口 {BROWSER_DEBUG_PORT} 打开 1688 浏览器会话，请先登录后再采集。", "port": BROWSER_DEBUG_PORT})
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            if parsed.path == "/api/generate-copy":
                body = self.read_body()
                product = normalize_product_fields(body.get("product") or load_product())
                platform = str(body.get("platform", "mercadolibre") or "mercadolibre")
                target_market = str(body.get("target_market") or platform or "mercadolibre")
                language = str(body.get("language") or ("Spanish (Mexico)" if target_market.strip().lower() == "mercadolibre" else "English"))
                mode = str(body.get("mode") or "rewrite")
                result = generate_ai_copy_bundle(product, platform, target_market, language, mode, load_app_config())
                product = save_copy_result(product, result["target_market"], {**result["copy"], "language": result["language"], "source_platform": result["source_platform"], "mode": result["mode"]})
                plan = apply_product_drafts_to_plan(product, build_plan_for_platform(product, platform))
                listing = plan.get("platforms", {}).get(platform_to_preset_key(platform), {}).get("listing", {})
                self.send_json({"ok": True, **result, "product": product, "plan": plan, "listing": listing, "productsIndex": load_products_index()})
                return
            if parsed.path == "/api/generate-copy-batch":
                body = self.read_body()
                result = batch_generate_copy_for_products(
                    body.get("product_ids") if isinstance(body.get("product_ids"), list) else [],
                    str(body.get("platform") or "mercadolibre"),
                    str(body.get("language") or ""),
                    str(body.get("mode") or "rewrite"),
                )
                self.send_json(result, 200 if result.get("ok") else 400)
                return
            if parsed.path == "/api/generate-image-prompts":
                body = self.read_body()
                product = normalize_product_fields(body.get("product") or load_product())
                prompt = build_image_prompt_pack(
                    product,
                    body.get("platform", "mercadolibre"),
                    body.get("selected_image_ids") if isinstance(body.get("selected_image_ids"), list) else [],
                    bool(body.get("include_bullets", True)),
                    bool(body.get("include_description", True)),
                    str(body.get("target_language") or body.get("language") or ""),
                )
                self.send_json({"ok": True, "prompt": prompt, "selected_image_ids": body.get("selected_image_ids") or []})
                return
            if parsed.path == "/api/test-ai-channel":
                body = self.read_body()
                try:
                    result = test_ai_channel(body.get("channel", "text"), body.get("config") or {})
                    self.send_json(result)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            if parsed.path == "/api/ai-config/save":
                body = self.read_body()
                config_service.write_env_template(APP_DIR)
                app_cfg = config_service.merge_ai_config(APP_DIR, load_app_config(), body.get("config") if isinstance(body.get("config"), dict) else body)
                save_app_config(app_cfg)
                config_service.save_config_snapshot(APP_DIR, app_cfg)
                self.send_json({"ok": True, "config": config_service.public_ai_config(APP_DIR, load_app_config())})
                return
            if parsed.path == "/api/mercadolibre/auth-link":
                body = self.read_body()
                try:
                    result = build_mercadolibre_auth_link(str(body.get("app_id") or ""), str(body.get("redirect_uri") or ""))
                    self.send_json({"ok": True, **result})
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            if parsed.path == "/api/mercadolibre/auth-checklist":
                self.send_json({"ok": True, "checklist": mercadolibre_auth_checklist()})
                return
            if parsed.path == "/api/open-auth-link":
                body = self.read_body()
                try:
                    result = open_auth_link_in_browser(str(body.get("url") or ""), str(body.get("browser") or "default"))
                    status = 200 if result.get("ok") else 400
                    self.send_json(result, status)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            if parsed.path == "/api/mercadolibre/exchange-code":
                body = self.read_body()
                try:
                    result = exchange_mercadolibre_code_from_body(body)
                    self.send_json({"ok": True, **result})
                except Exception as exc:
                    message = str(exc)
                    code = _mercadolibre_test_error_code(message)
                    append_ml_auth_test_log(
                        "exchange_code",
                        "failed",
                        {"redirect_uri": body.get("redirect_uri") or "", "code_present": bool(body.get("code_or_url") or body.get("code"))},
                        {"ok": False, "error_code": code, "error_message": message},
                        code,
                        message,
                        _auth_next_action("mercadolibre", "测试失败", code, message),
                    )
                    explanation = explain_mercadolibre_auth_error(code, message)
                    self.send_json({"ok": False, "error": message, "error_code": explanation["code"], "next_action": explanation["next_action"], "auth_explanation": explanation}, 400)
                return
            if parsed.path == "/api/mercadolibre/refresh-token":
                body = self.read_body()
                try:
                    result = refresh_mercadolibre_token_from_body(body)
                    self.send_json({"ok": True, **result})
                except Exception as exc:
                    message = str(exc)
                    code = _mercadolibre_test_error_code(message)
                    explanation = explain_mercadolibre_auth_error(code, message)
                    self.send_json({"ok": False, "error": message, "error_code": explanation["code"], "next_action": explanation["next_action"], "auth_explanation": explanation}, 400)
                return
            if parsed.path == "/api/mercadolibre/real-auth-test":
                body = self.read_body()
                product = normalize_product_fields(body.get("product") or load_product())
                result = run_mercadolibre_07d_test(str(body.get("mode") or "auth_link"), product, str(body.get("category_id") or ""))
                self.send_json(result)
                return
            if parsed.path == "/api/test-store-auth":
                body = self.read_body()
                try:
                    self.send_json(test_store_auth(str(body.get("platform") or ""), str(body.get("scope") or "")))
                except Exception as exc:
                    platform = str(body.get("platform") or "").strip().lower()
                    message = str(exc)
                    if platform == "mercadolibre":
                        code = _mercadolibre_test_error_code(message)
                        explanation = explain_mercadolibre_auth_error(code, message)
                        self.send_json({"ok": False, "error": message, "error_code": explanation["code"], "next_action": explanation["next_action"], "auth_explanation": explanation}, 400)
                    else:
                        self.send_json({"ok": False, "error": message}, 400)
                return
            if parsed.path == "/api/calculate-price":
                self.send_json(calculate_price(self.read_body()))
                return
            if parsed.path == "/api/category-attrs":
                body = self.read_body()
                platform = body.get("platform", "mercadolibre")
                category_id = str(body.get("category_id", "")).strip()
                if platform == "mercadolibre" and category_id:
                    token = load_store_config().get("mercadolibre", {}).get("access_token", "")
                    if token:
                        try:
                            attrs = publisher.mercadolibre_category_attributes(category_id, token)
                            self.send_json({"ok": True, "source": "live", "required": attrs})
                            return
                        except Exception as exc:
                            self.send_json({"source": "mock", "warning": str(exc), **mock_category_attrs(platform, category_id)})
                            return
                self.send_json(mock_category_attrs(platform, category_id))
                return
            if parsed.path == "/api/category-search":
                body = self.read_body()
                platform = str(body.get("platform") or "mercadolibre").strip().lower()
                site = str(body.get("site") or body.get("country") or "").strip()
                query = str(body.get("query") or body.get("keyword") or "").strip()
                limit = int(body.get("limit") or 20)
                results = search_category_cache(platform, query=query, site=site, limit=limit)
                self.send_json(
                    {
                        "ok": True,
                        "platform": platform,
                        "site": site,
                        "query": query,
                        "cache_status": category_cache_status(platform),
                        "results": results,
                    }
                )
                return
            if parsed.path == "/api/category-ai-suggest":
                body = self.read_body()
                platform = str(body.get("platform") or "mercadolibre").strip().lower()
                site = str(body.get("site") or body.get("country") or "").strip()
                product = normalize_product_fields(body.get("product") or load_product())
                limit = int(body.get("limit") or 5)
                self.send_json(suggest_category_ids(product, platform=platform, site=site, limit=limit))
                return
            if parsed.path == "/api/category-cache/refresh":
                body = self.read_body()
                platform = str(body.get("platform") or "mercadolibre").strip().lower()
                site = str(body.get("site") or body.get("country") or "").strip().upper()
                max_categories = int(body.get("max_categories") or 500)
                if platform == "mercadolibre":
                    self.send_json(refresh_official_category_cache(platform, site=site, max_categories=max_categories))
                else:
                    cache = load_category_cache(platform)
                    self.send_json(
                        {
                            "ok": True,
                            "platform": platform,
                            "cache_status": category_cache_status(platform),
                            "cache": cache,
                            "warning": "当前平台暂未接入官方类目刷新，仅读取本地缓存。",
                        }
                    )
                return
            if parsed.path == "/api/category-cache/refresh-job":
                body = self.read_body()
                platform = str(body.get("platform") or "mercadolibre").strip().lower()
                site = str(body.get("site") or body.get("country") or "").strip().upper()
                max_categories = int(body.get("max_categories") or 500)
                self.send_json({"ok": True, "job": start_category_cache_refresh_job(platform, site=site, max_categories=max_categories)})
                return
            if parsed.path == "/api/category-ai-fill":
                body = self.read_body()
                platform = str(body.get("platform") or "mercadolibre").strip().lower()
                category_id = str(body.get("category_id") or "").strip()
                product = normalize_product_fields(body.get("product") or load_product())
                record = find_category_record(platform, category_id) or body.get("category_record")
                updated = apply_ai_attribute_fill(product, platform, record if isinstance(record, dict) else None)
                saved = save_product(updated)
                self.send_json(
                    {
                        "ok": True,
                        "product": saved,
                        "draft": saved.get("drafts", {}).get(platform, {}),
                        "attributes": saved.get("drafts", {}).get(platform, {}).get("attributes", {}),
                        "need_review": saved.get("drafts", {}).get(platform, {}).get("validation_errors", []),
                        "cache_status": category_cache_status(platform),
                    }
                )
                return
            if parsed.path == "/api/category-precheck":
                body = self.read_body()
                platform = str(body.get("platform") or "mercadolibre").strip().lower()
                category_id = str(body.get("category_id") or "").strip()
                product = normalize_product_fields(body.get("product") or load_product())
                record = find_category_record(platform, category_id) or body.get("category_record")
                errors = validate_category_precheck(product, platform, record if isinstance(record, dict) else None)
                self.send_json(
                    {
                        "ok": True,
                        "platform": platform,
                        "errors": errors,
                        "missing_fields": errors,
                        "cache_status": category_cache_status(platform),
                    }
                )
                return
            if parsed.path == "/api/assign-upc":
                self.send_json(assign_upc())
                return
            if parsed.path == "/api/save-product":
                body = self.read_body()
                product = save_product(body.get("product", {}))
                self.send_json({"ok": True, "product": product, "productsIndex": load_products_index(), "imagePool": current_image_pool(product)})
                return
            if parsed.path == "/api/load-product":
                body = self.read_body()
                product = load_product_from_index(body.get("product_id", ""), body.get("product_file_path", ""))
                saved = save_product(product)
                self.send_json({"ok": True, "product": saved, "productsIndex": load_products_index(), "imagePool": current_image_pool(saved), "sourceImages": current_source_images(saved)})
                return
            if parsed.path == "/api/delete-products":
                body = self.read_body()
                result = delete_products_from_index(body.get("product_ids") if isinstance(body.get("product_ids"), list) else [])
                self.send_json(result, 200 if result.get("ok") else 400)
                return
            if parsed.path == "/api/collect-extension-payload":
                body = self.read_body()
                try:
                    result = collect_extension_payload(body)
                    self.send_json(result, 200 if result.get("ok") else 400)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            if parsed.path == "/api/save-settings":
                body = self.read_body()
                if body.get("appConfig"):
                    app_cfg = load_app_config()
                    app_cfg.update(body["appConfig"])
                    save_app_config(app_cfg)
                if body.get("storeConfig"):
                    store_cfg = load_store_config()
                    for key, value in body["storeConfig"].items():
                        if isinstance(value, dict):
                            if key == "mercadolibre":
                                if value.get("client_secret") and not value.get("app_secret"):
                                    value["app_secret"] = value.get("client_secret")
                                if value.get("app_secret") and not value.get("client_secret"):
                                    value["client_secret"] = value.get("app_secret")
                            store_cfg.setdefault(key, {}).update(value)
                    save_store_config(store_cfg)
                store_cfg = load_store_config()
                self.send_json({"ok": True, "appConfig": load_app_config(), "storeConfig": store_cfg, "storeAuthSummary": summarize_store_auth_states(store_cfg)})
                return
            if parsed.path == "/api/publish-precheck":
                body = self.read_body()
                product = normalize_product_fields(body.get("product") or load_product())
                config = load_store_config()
                platforms = body.get("platforms") or []
                if isinstance(platforms, str):
                    platforms = [platforms]
                platforms = [str(item).strip().lower() for item in platforms if str(item).strip()]
                if not platforms:
                    platforms = [str(body.get("platform") or "mercadolibre").strip().lower()]
                results: dict[str, Any] = {}
                updated = product
                for platform in platforms:
                    result = validate_platform_draft(updated, platform, config)
                    updated = apply_precheck_to_product(updated, platform, result, status="ready" if result.get("ok") else "not_ready")
                    results[platform] = result
                saved = save_product(updated)
                self.send_json({"ok": True, "platform": platforms[0] if len(platforms) == 1 else "", "platforms": results, "product": saved, "productsIndex": load_products_index()})
                return
            if parsed.path == "/api/publish-payload-preview":
                body = self.read_body()
                platform = str(body.get("platform") or "mercadolibre").strip().lower()
                product = normalize_product_fields(body.get("product") or load_product())
                if platform not in PLATFORMS:
                    self.send_json({"ok": False, "error": "不支持的平台"}, 400)
                    return
                if platform != "mercadolibre":
                    self.send_json(
                        {
                            "ok": True,
                            "platform": platform,
                            "status": "pending_real_interface",
                            "message": "payload 待真实接口完善",
                            "payload": {"platform": platform, "message": "payload 待真实接口完善"},
                        }
                    )
                    return
                try:
                    payload = app._sanitize_for_log(build_mercadolibre_payload_preview(product, load_store_config()))
                    path = OUTPUT_DIR / "last_mercadolibre_payload.json"
                    write_json(path, payload)
                    append_ml_publish_log(product, "payload_preview", collect_time_iso(), payload, {"ok": True, "status": "payload_preview", "path": str(path)}, "", "", {}, "仅预览 payload，未调用真实发布")
                    self.send_json({"ok": True, "platform": platform, "status": "preview_only", "payload": payload, "path": str(path)})
                except Exception as exc:
                    path = OUTPUT_DIR / "last_mercadolibre_payload.json"
                    if path.exists():
                        self.send_json({"ok": True, "platform": platform, "status": "file_fallback", "payload": read_json(path, {}), "path": str(path), "warning": str(exc)})
                    else:
                        self.send_json({"ok": False, "platform": platform, "error": str(exc)}, 400)
                return
            if parsed.path == "/api/publish-product":
                body = self.read_body()
                platform = body.get("platform", "mercadolibre")
                product = normalize_product_fields(body.get("product") or load_product())
                config = load_store_config()
                try:
                    result = publish_product(product, platform, config)
                    status = 200 if result.get("ok") else 400
                    self.send_json(result, status)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            if parsed.path == "/api/mercadolibre/confirm-real-publish":
                body = self.read_body()
                product = normalize_product_fields(body.get("product") or load_product())
                confirm = bool(body.get("confirm_real_publish") or body.get("confirm"))
                try:
                    result = mercadolibre_real_publish(product, confirm)
                    self.send_json(result, 200 if result.get("ok") else 400)
                except Exception as exc:
                    self.send_json({"ok": False, "status": "real_publish_failed", "error": str(exc)}, 400)
                return
            if parsed.path == "/api/publish-bus/enqueue":
                body = self.read_body()
                product = normalize_product_fields(body.get("product") or load_product())
                platforms = body.get("platforms") or []
                if isinstance(platforms, str):
                    platforms = [platforms]
                platforms = [str(item).strip() for item in platforms if str(item).strip()]
                try:
                    eligible_platforms = publish_queue_platforms(product, platforms)
                    rejected_platforms = [platform for platform in platforms if platform not in eligible_platforms]
                    if not eligible_platforms:
                        self.send_json(
                            {
                                "ok": False,
                                "error": "当前商品未通过发布队列准入：请先把草稿推进到“校验通过”。",
                                "error_code": "PUBLISH_QUEUE_NOT_READY",
                                "eligible_platforms": [],
                                "rejected_platforms": rejected_platforms,
                                "workflow_statuses": (sync_product_workflow_statuses(product).get("workflow_statuses") or {}),
                            },
                            400,
                        )
                        return
                    result = PUBLISHING_BUS.enqueue(product, eligible_platforms, load_store_config())
                    result["eligible_platforms"] = eligible_platforms
                    result["rejected_platforms"] = rejected_platforms
                    self.send_json(result)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, 400)
                return
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        self.send_response(404)
        self.end_headers()
