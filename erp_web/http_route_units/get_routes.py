# -*- coding: utf-8 -*-
from __future__ import annotations

import urllib.parse
from typing import Callable

from erp_web.context import get_context
from erp_web.services import config_service
from erp_web.schemas.api import API_SCHEMA_VERSION, validate_app_state_response
from erp_web.http_route_units import static_routes
from erp_web.facades.auth_config_facade import user_agents_payload
from .common import JsonRequestHandler
from ..facades.get_facade import (
    current_generated_images,
    current_image_pool,
    current_source_images,
    exchange_mercadolibre_callback,
    html_page,
    load_app_config,
    load_product,
    load_store_config,
    marketplace_options,
    mask_secret,
    mercadolibre_auth_checklist,
    summarize_store_auth_states,
)
GetHandler = Callable[[JsonRequestHandler, object], None]

FRONTEND_PAGE_ROUTES = {
    "/login": "login",
    "/register": "register",
    "/account": "account",
    "/admin/users": "admin-users",
    "/": "workbench",
    "/research": "research",
    "/collect": "collect",
    "/library": "library",
    "/drafts": "drafts",
    "/online-products": "online-products",
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
    "/api/system-settings/user-agents",
    "/api/ai-config",
    "/api/state",
}

STATIC_ROUTES = {
    "/licenses/THIRD-PARTY-NOTICES.txt",
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
    context = get_context()
    paths = context.paths
    store_cfg = load_store_config()
    app_cfg = load_app_config()
    state = {
        "schemaVersion": API_SCHEMA_VERSION,
        "ok": True,
        "appConfig": config_service.public_app_config(paths.app_dir, app_cfg),
        "storeConfig": config_service.public_store_config(store_cfg),
        "storeAuthSummary": summarize_store_auth_states(store_cfg),
        "mercadolibreAuthChecklist": mercadolibre_auth_checklist(store_cfg.get("mercadolibre", {})),
        "platformOptions": marketplace_options(),
        "outputDir": str(paths.output_dir),
        # 审批凭据只在这里下发给受信 UI；模型工具结果与对话上下文永远拿不到它。
        "approvalToken": context.approval_session.token,
    }
    if urllib.parse.parse_qs(parsed.query).get("view") == ["settings"]:
        handler.send_json(state)
        return
    prod = load_product()
    state.update(product=prod, imagePool=current_image_pool(prod), sourceImages=current_source_images(prod),
                 generatedImages=current_generated_images())
    handler.send_json(validate_app_state_response(state))


def handle_ai_config(handler: JsonRequestHandler, parsed: object) -> None:
    app_dir = get_context().paths.app_dir
    handler.send_json(
        {
            "ok": True,
            "config": config_service.public_ai_config(app_dir, load_app_config()),
        }
    )


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
        exchange_code=exchange_mercadolibre_callback,
        mask_secret=mask_secret,
    )


GET_HANDLERS: dict[str, GetHandler] = {
    "/api/system-settings/user-agents": lambda handler, parsed: handler.send_json(*user_agents_payload()),
    "/licenses/THIRD-PARTY-NOTICES.txt": handle_assets,
    "/api/state": handle_state,
    "/api/ai-config": handle_ai_config,
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
