# -*- coding: utf-8 -*-
from __future__ import annotations

from routes import static_routes
from .common import JsonRequestHandler
from .. import runtime as app
from ..runtime import *  # noqa: F403 - route units mirror legacy runtime globals.

APP_MODULE = app
FRONTEND_PAGE_ROUTES = {
    "/": "workbench",
    "/collect": "collect",
    "/library": "library",
    "/drafts": "drafts",
    "/ml-items": "ml-items",
    "/edit": "edit",
    "/media": "media",
    "/pricing": "pricing",
    "/publish": "publish",
    "/pending": "pending",
    "/settings": "settings",
    "/auth": "auth",
    "/logs": "logs",
}


def handle_get(handler: JsonRequestHandler, parsed: object) -> bool:
    if parsed.path in FRONTEND_PAGE_ROUTES:
        page = FRONTEND_PAGE_ROUTES.get(parsed.path, "workbench")
        raw = html_page(page).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)
        return True
    if parsed.path == "/api/state":
        prod = load_product()
        store_cfg = load_store_config()
        handler.send_json(
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
        return True
    if parsed.path == "/api/products-index":
        handler.send_json({"ok": True, "items": load_products_index()})
        return True
    if parsed.path == "/api/browser-debug/status":
        params = urllib.parse.parse_qs(parsed.query)
        port = int((params.get("port") or [str(BROWSER_DEBUG_PORT)])[0] or BROWSER_DEBUG_PORT)
        handler.send_json(browser_debug_status(port))
        return True
    if parsed.path == "/api/publish-logs":
        handler.send_json({"ok": True, "items": load_publish_logs()})
        return True
    if parsed.path == "/api/mercadolibre/published-items":
        params = urllib.parse.parse_qs(parsed.query)
        status = str((params.get("status") or ["active"])[0] or "active")
        page = int((params.get("page") or ["1"])[0] or 1)
        per_page = int((params.get("per_page") or params.get("limit") or ["50"])[0] or 50)
        try:
            result = mercadolibre_remote_items(status=status, page=page, per_page=per_page)
            handler.send_json(result, 200 if result.get("ok") else 400)
        except Exception as exc:
            handler.send_json({"ok": False, "error": str(exc)}, 400)
        return True
    if parsed.path == "/api/ai-config":
        config_service.write_env_template(APP_DIR)
        handler.send_json({"ok": True, "config": config_service.public_ai_config(APP_DIR, load_app_config())})
        return True
    if parsed.path == "/api/publish-bus/status":
        params = urllib.parse.parse_qs(parsed.query)
        job_id = str((params.get("job_id") or [""])[0]).strip()
        if not job_id:
            handler.send_json({"ok": False, "error": "缺少 job_id"}, 400)
            return True
        try:
            handler.send_json({"ok": True, "job": persist_publish_bus_terminal_results(PUBLISHING_BUS.get_status(job_id))})
        except Exception as exc:
            handler.send_json({"ok": False, "error": str(exc)}, 404)
        return True
    if parsed.path == "/api/category-cache/refresh-status":
        params = urllib.parse.parse_qs(parsed.query)
        job_id = str((params.get("job_id") or [""])[0]).strip()
        if not job_id:
            handler.send_json({"ok": False, "error": "缺少 job_id"}, 400)
            return True
        try:
            handler.send_json({"ok": True, "job": get_category_cache_refresh_job(job_id)})
        except Exception as exc:
            handler.send_json({"ok": False, "error": str(exc)}, 404)
        return True
    if parsed.path == "/file":
        static_routes.serve_file(handler, parsed, APP_MODULE)
        return True
    if parsed.path.startswith("/assets/"):
        static_routes.serve_frontend_asset(handler, parsed, APP_MODULE)
        return True
    if parsed.path == "/auth/mercadolibre":
        static_routes.serve_ml_auth_page(handler)
        return True
    if parsed.path == "/auth/wildberries":
        static_routes.serve_store_help_page(handler, "wildberries")
        return True
    if parsed.path == "/auth/ozon":
        static_routes.serve_store_help_page(handler, "ozon")
        return True
    if parsed.path == "/auth/mercadolibre/callback":
        static_routes.handle_ml_callback(handler, parsed, APP_MODULE)
        return True
    handler.send_response(404)
    handler.end_headers()


    return False
