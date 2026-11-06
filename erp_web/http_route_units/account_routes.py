"""个人账号与管理员用户管理的显式路由。"""
from erp_web.facades import account_facade
from erp_web.schemas.requests import validate_request_payload, RequestValidationError
from urllib.parse import parse_qs

POST_HANDLERS = {
    "/api/account/register": account_facade.register,
    "/api/account/login": account_facade.login,
    "/api/account/logout": account_facade.logout,
    "/api/account/password": account_facade.password,
    "/api/admin/users": account_facade.create_user,
    "/api/admin/users/status": account_facade.status,
    "/api/admin/users/password": account_facade.reset_password,
}
GET_HANDLERS = {
    "/api/account/session": account_facade.session,
    "/api/admin/users": account_facade.users,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)
GET_API_ROUTES = frozenset(GET_HANDLERS)


def handle_get(handler, parsed):
    action = GET_HANDLERS.get(parsed.path)
    if action is None:
        return False
    try:
        handler.send_json(action(handler, parse_qs(parsed.query)))
    except RequestValidationError as exc:
        handler.send_json({"ok": False, "error": str(exc), "error_code": exc.error_code}, exc.status_code)
    return True


def handle_post(handler, parsed):
    action = POST_HANDLERS.get(parsed.path)
    if action is None:
        return False
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError as exc:
        raise RequestValidationError("Content-Length 无效。") from exc
    if length > 8192:
        raise RequestValidationError("账号请求体过大。", status_code=413, error_code="REQUEST_BODY_TOO_LARGE")
    body = validate_request_payload(handler.read_body(), endpoint=handler.path)
    contract = {"/api/account/logout": set()}.get(parsed.path)
    if contract is None:
        from erp_web.schemas.requests import REQUEST_CONTRACTS
        contract = set(REQUEST_CONTRACTS[parsed.path].fields)
    if set(body) - contract:
        raise RequestValidationError("账号请求含有不支持的字段。")
    handler.send_json(action(handler, body))
    return True
