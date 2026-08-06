# -*- coding: utf-8 -*-
from __future__ import annotations

import urllib.parse
from typing import Callable

from erp_web.context import get_context
from erp_web.services import config_service
from erp_web.schemas.api import API_SCHEMA_VERSION, validate_app_state_response
from erp_web.http_route_units import static_routes
from .common import JsonRequestHandler
from ..facades.get_facade import (
    browser_debug_status,
    current_generated_images,
    current_image_pool,
    current_source_images,
    exchange_mercadolibre_code_from_body,
    get_publishing_bus,
    html_page,
    load_app_config,
    load_drafts_index,
    load_mercadolibre_order_notifications,
    load_product,
    load_products_index,
    load_publish_logs,
    load_store_config,
    marketplace_options,
    mask_secret,
    mercadolibre_auth_checklist,
    mercadolibre_recent_orders,
    mercadolibre_remote_items,
    summarize_store_auth_states,
)
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
    "/aiWork": "ai-work",
}

GET_API_ROUTES = {
    "/api/ai-config",
    "/api/browser-debug/status",
    "/api/mercadolibre/published-items",
    "/api/mercadolibre/orders",
    "/api/drafts-index",
    "/api/products-index",
    "/api/publish-bus/jobs",
    "/api/publish-bus/status",
    "/api/publish-logs",
    "/api/state",
}

STATIC_ROUTES = {
    "/file",
    "/auth/mercadolibre",
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
    paths = get_context().paths
    prod = load_product()
    store_cfg = load_store_config()
    app_cfg = load_app_config()
    state = {
        "schemaVersion": API_SCHEMA_VERSION,
        "ok": True,
        "product": prod,
        "appConfig": config_service.public_app_config(paths.app_dir, app_cfg),
        "storeConfig": config_service.public_store_config(store_cfg),
        "storeAuthSummary": summarize_store_auth_states(store_cfg),
        "mercadolibreAuthChecklist": mercadolibre_auth_checklist(store_cfg.get("mercadolibre", {})),
        "imagePool": current_image_pool(prod),
        "sourceImages": current_source_images(prod),
        "generatedImages": current_generated_images(),
        "platformOptions": marketplace_options(),
        "outputDir": str(paths.output_dir),
    }
    handler.send_json(validate_app_state_response(state))


def handle_products_index(handler: JsonRequestHandler, parsed: object) -> None:
    handler.send_json({"ok": True, "items": load_products_index()})


def handle_drafts_index(handler: JsonRequestHandler, parsed: object) -> None:
    params = urllib.parse.parse_qs(parsed.query)
    scope = str((params.get("scope") or ["active"])[0] or "active")
    handler.send_json({"ok": True, "items": load_drafts_index(scope)})


def handle_browser_debug_status(handler: JsonRequestHandler, parsed: object) -> None:
    default_port = get_context().paths.browser_debug_port
    params = urllib.parse.parse_qs(parsed.query)
    port = int((params.get("port") or [str(default_port)])[0] or default_port)
    handler.send_json(browser_debug_status(port))


def handle_publish_logs(handler: JsonRequestHandler, parsed: object) -> None:
    params = urllib.parse.parse_qs(parsed.query)
    try:
        limit = int((params.get("limit") or ["200"])[0] or 200)
    except ValueError:
        limit = 200
    handler.send_json({"ok": True, "items": load_publish_logs(limit=limit)})


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
    app_dir = get_context().paths.app_dir
    handler.send_json(
        {
            "ok": True,
            "config": config_service.public_ai_config(app_dir, load_app_config()),
        }
    )


def handle_publish_bus_status(handler: JsonRequestHandler, parsed: object) -> None:
    params = urllib.parse.parse_qs(parsed.query)
    job_id = str((params.get("job_id") or [""])[0]).strip()
    if not job_id:
        handler.send_json({"ok": False, "error": "缺少 job_id"}, 400)
        return
    try:
        handler.send_json(
            {"ok": True, "job": get_publishing_bus().get_public_status(job_id)}
        )
    except Exception as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 404)


def handle_publish_bus_jobs(handler: JsonRequestHandler, parsed: object) -> None:
    params = urllib.parse.parse_qs(parsed.query)
    try:
        limit = int((params.get("limit") or ["50"])[0] or 50)
    except ValueError:
        limit = 50
    try:
        result = get_publishing_bus().list_jobs(
            limit=limit,
            cursor=str((params.get("cursor") or [""])[0]),
            status=str((params.get("status") or [""])[0]),
            platform=str((params.get("platform") or [""])[0]),
            product_id=str((params.get("product_id") or [""])[0]),
        )
        handler.send_json({"ok": True, **result})
    except Exception as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)


def handle_file(handler: JsonRequestHandler, parsed: object) -> None:
    paths = get_context().paths
    static_routes.serve_file(
        handler,
        parsed,
        (
            paths.images_dir,
            # 采集 HTML/TXT 也位于此目录，但 serve_file 会再按图片扩展名
            # 限制，因此这里只开放截图，不会暴露采集页面原文。
            paths.collect_debug_dir,
        ),
    )


def handle_assets(handler: JsonRequestHandler, parsed: object) -> None:
    static_routes.serve_frontend_asset(handler, parsed, get_context().paths.front_dist_dir)


def handle_mercadolibre_auth_page(handler: JsonRequestHandler, parsed: object) -> None:
    static_routes.serve_ml_auth_page(handler)


def handle_ozon_auth_page(handler: JsonRequestHandler, parsed: object) -> None:
    static_routes.serve_store_help_page(handler, "ozon")


def handle_mercadolibre_callback(handler: JsonRequestHandler, parsed: object) -> None:
    static_routes.handle_ml_callback(
        handler,
        parsed,
        exchange_code=exchange_mercadolibre_code_from_body,
        mask_secret=mask_secret,
    )


GET_HANDLERS: dict[str, GetHandler] = {
    "/api/state": handle_state,
    "/api/products-index": handle_products_index,
    "/api/drafts-index": handle_drafts_index,
    "/api/browser-debug/status": handle_browser_debug_status,
    "/api/publish-logs": handle_publish_logs,
    "/api/mercadolibre/published-items": handle_mercadolibre_published_items,
    "/api/mercadolibre/orders": handle_mercadolibre_orders,
    "/api/ai-config": handle_ai_config,
    "/api/publish-bus/jobs": handle_publish_bus_jobs,
    "/api/publish-bus/status": handle_publish_bus_status,
    "/file": handle_file,
    "/auth/mercadolibre": handle_mercadolibre_auth_page,
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
