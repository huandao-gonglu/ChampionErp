"""1688 万邦个人直购薄路由；独立于 ERP 订单与采购流程。"""
from __future__ import annotations

from pydantic import ValidationError
from urllib.parse import parse_qs

from erp_web.facades import alibaba_direct_purchase_facade as facade
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.schemas.requests import validate_request_payload


def _run_post(handler, action: str) -> None:
    try:
        body = validate_request_payload(handler.read_body(), endpoint=handler.path)
        handler.send_json(facade.command(action, body))
    except ExternalRequestBlocked as exc:
        handler.send_json({"ok": False, "error": f"万邦 1688 请求已暂停：{exc}"}, 409)
    except ValidationError:
        handler.send_json({"ok": False, "error": "直购参数无效，请核对链接、SKU 数量及收货地址"}, 400)
    except (ValueError, TypeError) as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)


def _handle_resolve(handler):
    _run_post(handler, "resolve")


def _handle_parse_address(handler):
    _run_post(handler, "parse-address")


def _handle_preview(handler):
    _run_post(handler, "preview")


def _handle_create(handler):
    _run_post(handler, "create")


def _handle_reconcile(handler):
    _run_post(handler, "reconcile")


def _handle_cashier(handler):
    _run_post(handler, "cashier")


def _handle_payment_status(handler):
    _run_post(handler, "payment-status")


def _handle_pay(handler):
    _run_post(handler, "pay")


POST_HANDLERS = {
    "/api/alibaba/direct-purchase/resolve": _handle_resolve,
    "/api/alibaba/direct-purchase/parse-address": _handle_parse_address,
    "/api/alibaba/direct-purchase/preview": _handle_preview,
    "/api/alibaba/direct-purchase/create": _handle_create,
    "/api/alibaba/direct-purchase/reconcile": _handle_reconcile,
    "/api/alibaba/direct-purchase/cashier": _handle_cashier,
    "/api/alibaba/direct-purchase/payment-status": _handle_payment_status,
    "/api/alibaba/direct-purchase/pay": _handle_pay,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)

GET_HANDLERS = {
    "/api/alibaba/direct-purchase/state": facade.state,
    "/api/alibaba/direct-purchase/addresses": facade.addresses,
}
GET_API_ROUTES = frozenset(GET_HANDLERS)


def handle_post(handler, parsed) -> bool:
    callback = POST_HANDLERS.get(parsed.path)
    if callback is None:
        return False
    callback(handler)
    return True


def handle_get(handler, parsed) -> bool:
    callback = GET_HANDLERS.get(parsed.path)
    if callback is None:
        return False
    try:
        if parsed.path == "/api/alibaba/direct-purchase/addresses":
            query = parse_qs(parsed.query)
            refresh = str((query.get("refresh") or [""])[0]).strip().lower() in {"1", "true", "yes"}
            handler.send_json(callback(refresh=refresh))
        else:
            handler.send_json(callback())
    except ExternalRequestBlocked as exc:
        handler.send_json({"ok": False, "error": f"万邦 1688 请求已暂停：{exc}"}, 409)
    except (ValueError, TypeError) as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)
    return True


__all__ = [
    "GET_API_ROUTES",
    "GET_HANDLERS",
    "HANDLED_PATHS",
    "POST_HANDLERS",
    "handle_get",
    "handle_post",
]
