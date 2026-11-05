"""1688 账号授权；官方回调只接收 code/state，不接收应用凭据。"""
from html import escape
from urllib.parse import parse_qs

from erp_web.facades.alibaba_authorization_facade import authorization_payload
from erp_web.schemas.requests import validate_request_payload


def _post(handler, action, *, crossborder=False):
    body = validate_request_payload(handler.read_body(), endpoint=handler.path)
    payload, status = authorization_payload(action, body, crossborder=crossborder)
    handler.send_json(payload, status)


POST_HANDLERS = {
    '/api/alibaba/authorization/start': lambda handler: _post(handler, 'start'),
    '/api/alibaba/authorization/complete': lambda handler: _post(handler, 'complete'),
    '/api/alibaba/crossborder/authorization/start': lambda handler: _post(handler, 'start', crossborder=True),
    '/api/alibaba/crossborder/authorization/complete': lambda handler: _post(handler, 'complete', crossborder=True),
    '/api/alibaba/crossborder/authorization/test-product': lambda handler: _post(handler, 'test_product', crossborder=True),
}
GET_API_ROUTES = frozenset({'/api/alibaba/authorization', '/api/alibaba/crossborder/authorization'})
GET_HANDLED_PATHS = GET_API_ROUTES | {'/auth/alibaba/callback'}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler, parsed):
    callback = POST_HANDLERS.get(parsed.path)
    if callback is None:
        return False
    callback(handler)
    return True


def handle_get(handler, parsed):
    if parsed.path in GET_API_ROUTES:
        payload, status = authorization_payload('status', crossborder=parsed.path == '/api/alibaba/crossborder/authorization')
        handler.send_json(payload, status)
        return True
    if parsed.path != '/auth/alibaba/callback':
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    body = {key: values[0] for key, values in query.items() if key in {'code', 'state'} and len(values) == 1}
    payload, status = authorization_payload('callback', body)
    message = '1688 账号已连接，请返回 ERP 刷新授权状态。' if payload.get('ok') else payload['error']
    raw = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>1688 账号授权</title>'
           '<meta name="referrer" content="no-referrer"><body><p>' + escape(message) + '</p></body></html>').encode()
    handler.send_response(status)
    handler.send_header('Content-Type', 'text/html; charset=utf-8')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")
    handler.send_header('Referrer-Policy', 'no-referrer')
    handler.send_header('Content-Length', str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)
    return True
