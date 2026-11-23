"""1688 账号授权及官方 code/state 回调。"""
from html import escape
from urllib.parse import parse_qs

from erp_web.facades.alibaba_authorization_facade import authorization_payload, restore_onebound_key_payload
from erp_web.schemas.requests import validate_request_payload


def _start(handler):
    body = validate_request_payload(handler.read_body(), endpoint=handler.path)
    payload, status = authorization_payload('start', body)
    handler.send_json(payload, status)


def _complete(handler):
    body = validate_request_payload(handler.read_body(), endpoint=handler.path)
    payload, status = authorization_payload('complete', body)
    handler.send_json(payload, status)


def _complete_onebound(handler):
    body = validate_request_payload(handler.read_body(), endpoint=handler.path)
    payload, status = authorization_payload('complete-onebound', {'callback_url': body['callback_url']})
    handler.send_json(payload, status)


def _self_use_token(handler):
    body = validate_request_payload(handler.read_body(), endpoint=handler.path)
    payload, status = authorization_payload('self-use-token', body)
    handler.send_json(payload, status)


def _restore_onebound_key(handler):
    validate_request_payload(handler.read_body(), endpoint=handler.path)
    payload, status = restore_onebound_key_payload()
    handler.send_json(payload, status)


POST_HANDLERS = {
    '/api/alibaba/authorization/self-use-token': _self_use_token,
    '/api/alibaba/authorization/onebound/complete': _complete_onebound,
    '/api/alibaba/authorization/complete': _complete,
    '/api/alibaba/authorization/start': _start,
    '/api/alibaba/crossborder/authorization/start': _start,
    '/api/alibaba/onebound/key/restore': _restore_onebound_key,
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
        providers = parse_qs(parsed.query, keep_blank_values=True).get('provider', [])
        if len(providers) > 1 or providers == ['']:
            handler.send_json({'ok': False, 'error': '请指定唯一的 1688 接入方案'}, 400)
            return True
        payload, status = authorization_payload('status', {'provider': providers[0]} if providers else {})
        handler.send_json(payload, status)
        return True
    if parsed.path != '/auth/alibaba/callback':
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    if any(len(query.get(key, [])) > 1 for key in ('state', 'code')):
        payload, status = {'error': '1688 授权回调包含重复参数，请重新授权'}, 400
    else:
        body = {key: query[key][0] for key in ('code', 'state') if key in query}
        payload, status = authorization_payload('callback', body)
    message = '1688 账号已连接，可关闭此页面并返回 ERP。' if payload.get('ok') else payload['error']
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
