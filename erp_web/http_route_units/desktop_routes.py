"""桌面主动等待与回执路由；不持有业务或 Agent 生命周期。"""
from erp_web.facades import desktop_facade
from erp_web.schemas.requests import validate_request_payload, RequestValidationError
from erp_web.services.desktop_connection_service import DesktopUnavailable

POST_HANDLERS = {
    '/api/desktop/connect': desktop_facade.connect,
    '/api/desktop/poll': desktop_facade.poll,
    '/api/desktop/result': desktop_facade.complete,
    '/api/desktop/asset': desktop_facade.upload_asset,
    '/api/desktop/disconnect': desktop_facade.disconnect,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler, parsed):
    action = POST_HANDLERS.get(parsed.path)
    if action is None:
        return False
    body = validate_request_payload(handler.read_body(), endpoint=handler.path)
    try:
        handler.send_json(action(body))
    except DesktopUnavailable as exc:
        handler.send_json({'ok': False, 'error': str(exc), 'error_code': exc.code}, 409)
    except (ValueError, KeyError) as exc:
        raise RequestValidationError(str(exc)) from exc
    return True
