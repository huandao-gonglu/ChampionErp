"""平台授权中的中断状态与显式恢复。"""
from urllib.parse import parse_qs

from erp_web.facades import external_request_facade as facade
from erp_web.schemas.requests import validate_request_payload


def handle_recover(handler):
    try:
        handler.send_json(facade.recover(validate_request_payload(handler.read_body(), endpoint=handler.path)))
    except ValueError as exc:
        handler.send_json({'ok': False, 'error': str(exc)}, 400)


POST_HANDLERS = {'/api/external-requests/recover': handle_recover}
HANDLED_PATHS = frozenset(POST_HANDLERS)
GET_API_ROUTES = frozenset({'/api/external-requests/status'})


def handle_post(handler, parsed):
    callback = POST_HANDLERS.get(parsed.path)
    if callback is None:
        return False
    callback(handler)
    return True


def handle_get(handler, parsed):
    if parsed.path not in GET_API_ROUTES:
        return False
    query = parse_qs(parsed.query)
    try:
        offset = max(0, int(query.get('offset', ['0'])[0]))
        operation_ids = query.get('operation_id', [])
        if len(operation_ids) > 50 or any(len(value) > 128 for value in operation_ids):
            raise ValueError('请求关联标识过多或过长')
        handler.send_json(facade.status(offset=offset, operation_ids=operation_ids))
    except ValueError as exc:
        handler.send_json({'ok': False, 'error': str(exc)}, 400)
    return True
