from __future__ import annotations

from urllib.parse import parse_qs
from erp_web.facades import online_product_facade
from erp_web.schemas.requests import validate_request_payload


def handle_sync(handler):
    result, status = online_product_facade.mutate("sync", validate_request_payload(handler.read_body(), endpoint=handler.path))
    handler.send_json(result, status)


def handle_refresh_status(handler):
    result, status = online_product_facade.mutate("refresh-status", validate_request_payload(handler.read_body(), endpoint=handler.path))
    handler.send_json(result, status)


def handle_change(handler):
    result, status = online_product_facade.mutate("change", validate_request_payload(handler.read_body(), endpoint=handler.path))
    handler.send_json(result, status)


def handle_reconcile(handler):
    result, status = online_product_facade.mutate("reconcile", validate_request_payload(handler.read_body(), endpoint=handler.path))
    handler.send_json(result, status)


def handle_retry(handler):
    result, status = online_product_facade.mutate("retry", validate_request_payload(handler.read_body(), endpoint=handler.path))
    handler.send_json(result, status)


POST_HANDLERS = {
    "/api/online-products/refresh-status": handle_refresh_status,
    "/api/online-products/sync": handle_sync,
    "/api/online-products/change": handle_change,
    "/api/online-products/reconcile": handle_reconcile,
    "/api/online-products/retry": handle_retry,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)
GET_API_ROUTES = frozenset({"/api/online-products"})


def handle_post(handler, parsed):
    callback = POST_HANDLERS.get(parsed.path)
    if callback is None:
        return False
    callback(handler)
    return True


def handle_get(handler, parsed):
    if parsed.path not in GET_API_ROUTES:
        return False
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    try:
        handler.send_json(online_product_facade.read(query))
    except ValueError as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)
    return True
