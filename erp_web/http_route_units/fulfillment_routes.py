"""跨境履约薄 HTTP 路由；所有写请求先经过公开契约校验。"""

from urllib.parse import parse_qs

from pydantic import ValidationError

from erp_web.facades import fulfillment_facade as facade
from erp_web.schemas.requests import validate_request_payload


def _command(action):
    def handle(handler):
        try:
            body = validate_request_payload(handler.read_body(), endpoint=handler.path)
            handler.send_json(facade.command(action, body))
        except ValidationError:
            handler.send_json({"ok": False, "error": "履约字段或类型无效，请检查必填资料。"}, 400)
        except ValueError as exc:
            handler.send_json({"ok": False, "error": str(exc)}, 400)
    return handle


POST_HANDLERS = {
    "/api/orders/fulfillment/upload-label": _command("upload-label"),
    **{f"/api/crossborderbus/{action}": _command(action) for action in ("authorize", "catalog", "save-rule", "delete-rule")},
    **{f"/api/orders/fulfillment/{action}": _command(action) for action in ("fetch-label", "label", "parcels", "plan", "pause", "resume", "submit", "sync", "retry", "cancel")},
}
HANDLED_PATHS = frozenset(POST_HANDLERS)
GET_HANDLERS = {"/api/crossborderbus/settings": facade.settings, "/api/crossborderbus/services": facade.services, "/api/orders/fulfillment": facade.detail}
GET_API_ROUTES = frozenset(GET_HANDLERS)


def handle_post(handler, parsed):
    action = POST_HANDLERS.get(parsed.path)
    if action is None:
        return False
    action(handler)
    return True


def handle_get(handler, parsed):
    action = GET_HANDLERS.get(parsed.path)
    if action is None:
        return False
    query = {k: values[0] for k, values in parse_qs(parsed.query).items()}
    try:
        if parsed.path == "/api/crossborderbus/settings":
            result = action()
        elif parsed.path == "/api/crossborderbus/services":
            result = action(section_id=query.get("section_id", ""), warehouse_id=query.get("warehouse_id", ""))
        else:
            result = action(order_id=query.get("order_id", ""))
        handler.send_json(result)
    except (ValueError, TypeError) as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 400)
    return True
