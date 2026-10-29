"""微信连接的受信设置接口，沿用公共 Host/Origin 检查。"""
from erp_web.facades import wechat_facade
from erp_web.schemas.requests import validate_request_payload


def handle_login(handler):
    payload, status = wechat_facade.command_payload("login", validate_request_payload(handler.read_body(), endpoint=handler.path))
    handler.send_json(payload, status)


def handle_verify(handler):
    payload, status = wechat_facade.command_payload("verify", validate_request_payload(handler.read_body(), endpoint=handler.path))
    handler.send_json(payload, status)


def handle_unbind(handler):
    payload, status = wechat_facade.command_payload("unbind", validate_request_payload(handler.read_body(), endpoint=handler.path))
    handler.send_json(payload, status)


def handle_settings(handler):
    payload, status = wechat_facade.command_payload("settings", validate_request_payload(handler.read_body(), endpoint=handler.path))
    handler.send_json(payload, status)


POST_HANDLERS = {"/api/wechat/login": handle_login, "/api/wechat/verify": handle_verify,
                 "/api/wechat/unbind": handle_unbind, "/api/wechat/settings": handle_settings}
HANDLED_PATHS = frozenset(POST_HANDLERS)
GET_API_ROUTES = frozenset({"/api/wechat/status"})


def handle_post(handler, parsed):
    callback = POST_HANDLERS.get(parsed.path)
    if callback is None:
        return False
    callback(handler)
    return True


def handle_get(handler, parsed):
    if parsed.path not in GET_API_ROUTES:
        return False
    handler.send_json(wechat_facade.status_payload())
    return True


__all__ = ["POST_HANDLERS", "HANDLED_PATHS", "GET_API_ROUTES", "handle_post", "handle_get"]
