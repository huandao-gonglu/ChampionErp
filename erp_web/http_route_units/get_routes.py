# -*- coding: utf-8 -*-
from __future__ import annotations

import urllib.parse
from typing import Callable

from erp_web.services import config_service
from erp_web.http_route_units import static_routes
from .common import JsonRequestHandler
from .. import runtime as app
from ..runtime_units.category_refresh import get_category_cache_refresh_job
from ..runtime_units.image_pool import current_generated_images, current_image_pool, current_source_images
from ..runtime_units.product_store import (
    load_drafts_index,
    load_app_config,
    load_product,
    load_products_index,
    load_store_config,
    mercadolibre_auth_checklist,
    summarize_store_auth_states,
)
from ..runtime_units.publish_bus import load_publish_logs, persist_publish_bus_terminal_results
from ..runtime_units.mercadolibre_orders import load_mercadolibre_order_notifications, mercadolibre_recent_orders
from ..runtime_units.publish_mercadolibre import mercadolibre_remote_items
from ..runtime_units.publish_adapter import PUBLISHING_BUS
from ..runtime_units.runtime_api import html_page
from ..runtime_units.runtime_common import APP_DIR, BROWSER_DEBUG_PORT, OUTPUT_DIR
from ..runtime_units.source_collect_browser import browser_debug_status
from ..marketplace_registry import marketplace_options

APP_MODULE = app
GetHandler = Callable[[JsonRequestHandler, object], None]

FRONTEND_PAGE_ROUTES = {
    "/": "workbench",
    "/research": "research",
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

GET_API_ROUTES = {
    "/api/ai-config",
    "/api/browser-debug/status",
    "/api/category-cache/refresh-status",
    "/api/mercadolibre/published-items",
    "/api/mercadolibre/orders",
    "/api/drafts-index",
    "/api/products-index",
    "/api/publish-bus/status",
    "/api/publish-logs",
    "/api/state",
}

STATIC_ROUTES = {
    "/file",
    "/auth/mercadolibre",
    "/auth/wildberries",
    "/auth/ozon",
    "/auth/mercadolibre/callback",
}

HANDLED_PATHS = frozenset(FRONTEND_PAGE_ROUTES) | GET_API_ROUTES | STATIC_ROUTES


def handle_frontend_page(handler: JsonRequestHandler, parsed: object) -> None:
    page = FRONTEND_PAGE_ROUTES.get(parsed.path, "workbench")
    raw = html_page(page).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def handle_state(handler: JsonRequestHandler, parsed: object) -> None:
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
            "mercadolibreOrderNotifications": load_mercadolibre_order_notifications(),
            "productsIndex": load_products_index(),
            "draftsIndex": load_drafts_index(),
            "platformOptions": marketplace_options(),
            "outputDir": str(OUTPUT_DIR),
        }
    )


def handle_products_index(handler: JsonRequestHandler, parsed: object) -> None:
    handler.send_json({"ok": True, "items": load_products_index()})


def handle_drafts_index(handler: JsonRequestHandler, parsed: object) -> None:
    params = urllib.parse.parse_qs(parsed.query)
    scope = str((params.get("scope") or ["active"])[0] or "active")
    handler.send_json({"ok": True, "items": load_drafts_index(scope)})


def handle_browser_debug_status(handler: JsonRequestHandler, parsed: object) -> None:
    params = urllib.parse.parse_qs(parsed.query)
    port = int((params.get("port") or [str(BROWSER_DEBUG_PORT)])[0] or BROWSER_DEBUG_PORT)
    handler.send_json(browser_debug_status(port))


def handle_publish_logs(handler: JsonRequestHandler, parsed: object) -> None:
    handler.send_json({"ok": True, "items": load_publish_logs()})


def handle_mercadolibre_published_items(handler: JsonRequestHandler, parsed: object) -> None:
    params = urllib.parse.parse_qs(parsed.query)
    status = str((params.get("status") or ["active"])[0] or "active")
    page = int((params.get("page") or ["1"])[0] or 1)
    per_page = int((params.get("per_page") or params.get("limit") or ["50"])[0] or 50)
    try:
        result = mercadolibre_remote_items(status=status, page=page, per_page=per_page)
        handler.send_json(result, 200 if result.get("ok") else 400)
    except Exception as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)


def handle_mercadolibre_orders(handler: JsonRequestHandler, parsed: object) -> None:
    params = urllib.parse.parse_qs(parsed.query)
    limit = int((params.get("limit") or ["10"])[0] or 10)
    offset = int((params.get("offset") or ["0"])[0] or 0)
    try:
        result = mercadolibre_recent_orders(limit=limit, offset=offset)
        handler.send_json(result, 200 if result.get("ok") else 400)
    except Exception as exc:
        handler.send_json({"ok": False, "error": str(exc), "items": [], "notifications": load_mercadolibre_order_notifications()}, 400)


def handle_ai_config(handler: JsonRequestHandler, parsed: object) -> None:
    config_service.write_env_template(APP_DIR)
    handler.send_json({"ok": True, "config": config_service.public_ai_config(APP_DIR, load_app_config())})


def handle_publish_bus_status(handler: JsonRequestHandler, parsed: object) -> None:
    params = urllib.parse.parse_qs(parsed.query)
    job_id = str((params.get("job_id") or [""])[0]).strip()
    if not job_id:
        handler.send_json({"ok": False, "error": "缺少 job_id"}, 400)
        return
    try:
        handler.send_json({"ok": True, "job": persist_publish_bus_terminal_results(PUBLISHING_BUS.get_status(job_id))})
    except Exception as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 404)


def handle_category_cache_refresh_status(handler: JsonRequestHandler, parsed: object) -> None:
    params = urllib.parse.parse_qs(parsed.query)
    job_id = str((params.get("job_id") or [""])[0]).strip()
    if not job_id:
        handler.send_json({"ok": False, "error": "缺少 job_id"}, 400)
        return
    try:
        handler.send_json({"ok": True, "job": get_category_cache_refresh_job(job_id)})
    except Exception as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 404)


def handle_file(handler: JsonRequestHandler, parsed: object) -> None:
    static_routes.serve_file(handler, parsed, APP_MODULE)


def handle_assets(handler: JsonRequestHandler, parsed: object) -> None:
    static_routes.serve_frontend_asset(handler, parsed, APP_MODULE)


def handle_mercadolibre_auth_page(handler: JsonRequestHandler, parsed: object) -> None:
    static_routes.serve_ml_auth_page(handler)


def handle_wildberries_auth_page(handler: JsonRequestHandler, parsed: object) -> None:
    static_routes.serve_store_help_page(handler, "wildberries")


def handle_ozon_auth_page(handler: JsonRequestHandler, parsed: object) -> None:
    static_routes.serve_store_help_page(handler, "ozon")


def handle_mercadolibre_callback(handler: JsonRequestHandler, parsed: object) -> None:
    static_routes.handle_ml_callback(handler, parsed, APP_MODULE)


GET_HANDLERS: dict[str, GetHandler] = {
    "/api/state": handle_state,
    "/api/products-index": handle_products_index,
    "/api/drafts-index": handle_drafts_index,
    "/api/browser-debug/status": handle_browser_debug_status,
    "/api/publish-logs": handle_publish_logs,
    "/api/mercadolibre/published-items": handle_mercadolibre_published_items,
    "/api/mercadolibre/orders": handle_mercadolibre_orders,
    "/api/ai-config": handle_ai_config,
    "/api/publish-bus/status": handle_publish_bus_status,
    "/api/category-cache/refresh-status": handle_category_cache_refresh_status,
    "/file": handle_file,
    "/auth/mercadolibre": handle_mercadolibre_auth_page,
    "/auth/wildberries": handle_wildberries_auth_page,
    "/auth/ozon": handle_ozon_auth_page,
    "/auth/mercadolibre/callback": handle_mercadolibre_callback,
}


def handle_get(handler: JsonRequestHandler, parsed: object) -> bool:
    if parsed.path in FRONTEND_PAGE_ROUTES:
        handle_frontend_page(handler, parsed)
        return True
    if parsed.path.startswith("/assets/"):
        handle_assets(handler, parsed)
        return True
    route_handler = GET_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler, parsed)
    return True
