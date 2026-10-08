"""订单通知薄路由；平台回调仅校验和落库，不执行远端读取。"""

import sqlite3
from pydantic import ValidationError
from urllib.parse import parse_qs, urlsplit

from erp_web.facades import order_notification_facade as facade
from erp_web.facades import order_procurement_facade as procurement
from erp_web.facades import alibaba_self_purchase_facade
from erp_web.schemas.requests import validate_request_payload
from erp_web.facades import order_address_note_facade as address_notes
from erp_web.schemas.order_address_notes import AddressNoteConflict
from erp_web.schemas.external_requests import ExternalRequestBlocked


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


def handle_purchase_query(handler):
    try:
        body = validate_request_payload(handler.read_body(), endpoint=handler.path)
        handler.send_json(procurement.query_purchase(body))
    except ExternalRequestBlocked:
        handler.send_json({"ok": False, "error": "1688 请求已中断，请到授权配置的「中断与恢复」查看原因并处理"}, 409)
    except (ValueError, TypeError) as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)


def handle_purchase_sync(handler):
    try:
        body = validate_request_payload(handler.read_body(), endpoint=handler.path)
        handler.send_json(procurement.sync_purchase(body))
    except (ValueError, TypeError) as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)


def handle_address_note(handler):
    try:
        body = validate_request_payload(handler.read_body(), endpoint=handler.path)
        handler.send_json(address_notes.save(body))
    except AddressNoteConflict as exc:
        handler.send_json({"ok": False, "error": str(exc), "code": "ADDRESS_NOTE_CONFLICT",
                           "current": exc.current.model_dump(mode="json")}, 409)
    except ValidationError:
        handler.send_json({"ok": False, "error": "备注格式无效，最多可填写 4000 字"}, 400)
    except (ValueError, TypeError) as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)


def _self_purchase(handler, action):
    try:
        body = validate_request_payload(handler.read_body(), endpoint=handler.path)
        handler.send_json(alibaba_self_purchase_facade.command(action, body))
    except ExternalRequestBlocked:
        handler.send_json({"ok": False, "error": "1688 请求已中断，请到授权配置的中断与恢复查看"}, 409)
    except ValidationError:
        handler.send_json({"ok": False, "error": "采购参数无效，请核对数量和订单号"}, 400)
    except (ValueError, TypeError) as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)


def handle_self_purchase_preview(handler):
    _self_purchase(handler, "preview")


def handle_self_purchase_create(handler):
    _self_purchase(handler, "create")


def handle_self_purchase_reconcile(handler):
    _self_purchase(handler, "reconcile")


def handle_self_purchase_cashier(handler):
    _self_purchase(handler, "cashier")


POST_HANDLERS = {
    "/api/orders/alibaba-purchase/preview": handle_self_purchase_preview,
    "/api/orders/alibaba-purchase/create": handle_self_purchase_create,
    "/api/orders/alibaba-purchase/reconcile": handle_self_purchase_reconcile,
    "/api/orders/alibaba-purchase/cashier": handle_self_purchase_cashier,

    "/api/orders/purchase-sync": handle_purchase_sync,
    "/api/orders/purchase-query": handle_purchase_query,
    "/api/orders/address-note": handle_address_note,
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
    "/api/orders/alibaba-purchase": alibaba_self_purchase_facade.options,
    "/api/orders/address-note": address_notes.read,
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
            else callback(order_id=query.get("order_id", ""), shipment_id=query.get("shipment_id", ""), address=query.get("address", ""))
            if parsed.path == "/api/orders/address-note"
            else callback(order_id=query.get("order_id", ""))
            if parsed.path == "/api/orders/detail"
            else callback(query)
            if parsed.path == "/api/orders/alibaba-purchase"
            else callback()
        )
    except (ValueError, TypeError) as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)
    return True
