# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import urllib.parse

from routes import image_routes
from . import runtime as app
from .runtime import safe_json_body
from .http_route_units import (
    auth_config_routes,
    category_routes,
    collect_routes,
    copy_routes,
    get_routes,
    product_routes,
    publish_routes,
)
from .http_route_units.common import JsonRequestHandler


APP_MODULE = app
logger = logging.getLogger(__name__)

FRONTEND_PAGE_ROUTES = get_routes.FRONTEND_PAGE_ROUTES

GET_API_ROUTES = {
    "/api/ai-config",
    "/api/browser-debug/status",
    "/api/category-cache/refresh-status",
    "/api/mercadolibre/published-items",
    "/api/products-index",
    "/api/publish-bus/status",
    "/api/publish-logs",
    "/api/state",
}

POST_API_ROUTES = {
    "/api/ai-config/save",
    "/api/assign-upc",
    "/api/browser-debug/open-profile",
    "/api/calculate-price",
    "/api/category-ai-fill",
    "/api/category-ai-suggest",
    "/api/category-attrs",
    "/api/category-cache/refresh",
    "/api/category-cache/refresh-job",
    "/api/category-precheck",
    "/api/category-search",
    "/api/claim-products",
    "/api/collect-1688",
    "/api/collect-1688-clean",
    "/api/collect-batch",
    "/api/collect-extension-payload",
    "/api/collect-from-browser-tab",
    "/api/collect-source",
    "/api/delete-products",
    "/api/generate-copy",
    "/api/generate-copy-batch",
    "/api/generate-image-prompts",
    "/api/load-product",
    "/api/mercadolibre/auth-checklist",
    "/api/mercadolibre/auth-link",
    "/api/mercadolibre/close-item",
    "/api/mercadolibre/confirm-real-publish",
    "/api/mercadolibre/exchange-code",
    "/api/mercadolibre/real-auth-test",
    "/api/mercadolibre/refresh-token",
    "/api/open-1688-browser",
    "/api/open-auth-link",
    "/api/publish-bus/enqueue",
    "/api/publish-payload-preview",
    "/api/publish-precheck",
    "/api/publish-product",
    "/api/save-product",
    "/api/save-settings",
    "/api/test-ai-channel",
    "/api/test-store-auth",
}

__all__ = [
    "FRONTEND_PAGE_ROUTES",
    "GET_API_ROUTES",
    "POST_API_ROUTES",
    "handle_get",
    "handle_post",
    "safe_json_body",
]

def handle_get(handler: JsonRequestHandler) -> None:
    parsed = urllib.parse.urlparse(handler.path)
    if get_routes.handle_get(handler, parsed):
        return
    handler.send_response(404)
    handler.end_headers()


def handle_post(handler: JsonRequestHandler) -> None:
    parsed = urllib.parse.urlparse(handler.path)
    try:
        if image_routes.handle_post(handler, parsed.path, APP_MODULE):
            return
        for route_unit in (
            collect_routes,
            copy_routes,
            auth_config_routes,
            category_routes,
            product_routes,
            publish_routes,
        ):
            if route_unit.handle_post(handler, parsed):
                return
    except Exception as exc:
        logger.exception("Unhandled POST request failed: %s", handler.path)
        handler.send_json({"ok": False, "error": str(exc)}, 500)
        return
    handler.send_response(404)
    handler.end_headers()
