# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import urllib.parse

from erp_web.http_route_units import image_routes
from . import runtime as app
from .http_route_units import (
    auth_config_routes,
    category_routes,
    collect_routes,
    copy_routes,
    get_routes,
    logistics_routes,
    mercadolibre_routes,
    product_routes,
    product_research_routes,
    publish_routes,
)
from .http_route_units.common import JsonRequestHandler
from .runtime_units.runtime_api import safe_json_body


APP_MODULE = app
logger = logging.getLogger(__name__)

FRONTEND_PAGE_ROUTES = get_routes.FRONTEND_PAGE_ROUTES
GET_ROUTE_UNITS = (
    get_routes,
    product_research_routes,
)
GET_API_ROUTES = frozenset(
    path
    for route_unit in GET_ROUTE_UNITS
    for path in getattr(route_unit, "GET_API_ROUTES", frozenset())
)
POST_ROUTE_UNITS = (
    collect_routes,
    copy_routes,
    auth_config_routes,
    category_routes,
    product_routes,
    product_research_routes,
    logistics_routes,
    mercadolibre_routes,
    publish_routes,
)
POST_ROUTE_UNITS_BY_PATH = {
    path: route_unit
    for route_unit in POST_ROUTE_UNITS
    for path in route_unit.HANDLED_PATHS
}
POST_API_ROUTES = frozenset(POST_ROUTE_UNITS_BY_PATH)

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
    for route_unit in GET_ROUTE_UNITS:
        if route_unit.handle_get(handler, parsed):
            return
    handler.send_response(404)
    handler.end_headers()


def handle_post(handler: JsonRequestHandler) -> None:
    parsed = urllib.parse.urlparse(handler.path)
    try:
        if image_routes.handle_post(handler, parsed.path, APP_MODULE):
            return
        route_unit = POST_ROUTE_UNITS_BY_PATH.get(parsed.path)
        if route_unit and route_unit.handle_post(handler, parsed):
            return
    except Exception as exc:
        logger.exception("Unhandled POST request failed: %s", handler.path)
        handler.send_json({"ok": False, "error": str(exc)}, 500)
        return
    handler.send_response(404)
    handler.end_headers()
