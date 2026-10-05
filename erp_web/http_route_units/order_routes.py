"""订单通知薄路由；平台回调仅校验和落库，不执行远端读取。"""

import sqlite3
from urllib.parse import parse_qs, urlsplit

from erp_web.facades import order_notification_facade as facade
from erp_web.facades import order_procurement_facade as procurement
from erp_web.schemas.requests import validate_request_payload


def _receive(handler, platform):
    query = parse_qs(urlsplit(handler.path).query)
    try:
        if int(handler.headers.get("Content-Length", "0")) > 256 * 1024:
            handler.send_json({"ok": False, "error": "订单回调请求体过大"}, 413)
            return
        result = facade.receive_notification(
            platform,
            (query.get("token") or [""])[0],
            validate_request_payload(handler.read_body(), endpoint=handler.path),
        )
        handler.send_json(result, 200)
    except sqlite3.OperationalError:
        handler.send_json({"ok": False, "error": "通知尚未保存，请稍后重投"}, 503)
    except PermissionError as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 403)
    except (ValueError, TypeError) as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)


def handle_mercadolibre(handler):
    _receive(handler, "mercadolibre")


def handle_ozon(handler):
    _receive(handler, "ozon")


def handle_yandex(handler):
    _receive(handler, "yandex")


def _command(handler, action):
    try:
        handler.send_json(
            facade.command(
                action,
                validate_request_payload(handler.read_body(), endpoint=handler.path),
            )
        )
    except (ValueError, TypeError) as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)


def handle_sync(handler):
    _command(handler, "sync")


def handle_retry(handler):
    _command(handler, "retry")


def handle_acknowledge(handler):
    _command(handler, "acknowledge")


def handle_configure(handler):
    _command(handler, "configure")


def _procurement_command(handler, action):
    try:
        body = validate_request_payload(handler.read_body(), endpoint=handler.path)
        handler.send_json(procurement.command(action, body))
    except (ValueError, TypeError) as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)


def handle_source(handler):
    _procurement_command(handler, "select-source")


def handle_purchase(handler):
    _procurement_command(handler, "record-purchase")


def handle_cancel_purchase(handler):
    _procurement_command(handler, "cancel-purchase")


POST_HANDLERS = {
    "/api/mercadolibre/notifications": handle_mercadolibre,
    "/api/ozon/notifications": handle_ozon,
    "/api/yandex/notifications": handle_yandex,
    "/api/orders/sync": handle_sync,
    "/api/orders/retry": handle_retry,
    "/api/orders/acknowledge": handle_acknowledge,
    "/api/orders/configure": handle_configure,
    "/api/orders/select-source": handle_source,
    "/api/orders/record-purchase": handle_purchase,
    "/api/orders/cancel-purchase": handle_cancel_purchase,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)
GET_HANDLERS = {
    "/api/orders": facade.read_orders,
    "/api/orders/integrations": facade.integrations,
    "/api/orders/summary": facade.summary,
    "/api/orders/detail": procurement.detail,
}
GET_API_ROUTES = frozenset(GET_HANDLERS)


def handle_post(handler, parsed):
    callback = POST_HANDLERS.get(parsed.path)
    if callback is None:
        return False
    callback(handler)
    return True


def handle_get(handler, parsed):
    callback = GET_HANDLERS.get(parsed.path)
    if callback is None:
        return False
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    try:
        allowed = {
            key: value
            for key, value in query.items()
            if key in {"platform", "state", "limit", "offset", "q"}
        }
        handler.send_json(
            callback(**allowed)
            if parsed.path == "/api/orders"
            else callback(order_id=query.get("order_id", ""))
            if parsed.path == "/api/orders/detail"
            else callback()
        )
    except (ValueError, TypeError) as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)
    return True
