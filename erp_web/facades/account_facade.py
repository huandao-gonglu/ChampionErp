"""账号 HTTP 编排及管理员对个人授权的管理。"""
from http.cookies import SimpleCookie
import os

from pydantic import ValidationError

from erp_web.schemas.alibaba_authorization import OneBoundUserBindingRequest
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.schemas.accounts import AccountError, SESSION_COOKIE, SESSION_SECONDS, public_user
from erp_web.schemas.newapi import NewAPIConnectionInfo, NewAPIAuthorizationPreview, NewAPILaunch
from erp_web.services import registration_verification
from erp_web.services.account_request import account_runtime, client_ip
from erp_web.services.alibaba_authorization_service import AlibabaAuthorizationService


def _require_session(handler, *, admin=False):
    session = getattr(handler, "account_session", None)
    if session is None:
        raise AccountError("请先登录。", status_code=401, error_code="UNAUTHENTICATED")
    if admin and session.user["role"] != "admin":
        raise AccountError("需要管理员权限。", status_code=403, error_code="FORBIDDEN")
    return session


def _cookie(handler, token):
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE] = token
    value = cookie[SESSION_COOKIE]
    value["httponly"] = True
    value["samesite"] = "Lax"
    value["path"] = "/"
    value["max-age"] = SESSION_SECONDS if token else 0
    if os.environ.get("ERP_PUBLIC_URL", "").startswith("https://"):
        value["secure"] = True
    handler.account_set_cookie = value.OutputString()


def session(handler, parsed=None):
    value = getattr(handler, "account_session", None)
    return {"ok": True, "user": public_user(value.user) if value else None,
            "csrf_token": value.csrf_token if value else "",
            "initialized": account_runtime(handler).store.admin() is not None}


def registration_config(handler, parsed=None):
    return {"ok": True, "site_key": os.environ.get("ERP_TURNSTILE_SITE_KEY", "")}


def registration_code(handler, body):
    return registration_verification.issue_code(account_runtime(handler).service, body, client_ip(handler))


def register(handler, body):
    runtime = account_runtime(handler)
    runtime.service.limit("register", body["email"], client_ip(handler))
    digest = registration_verification.verified_digest(body["email"], body["email_code"])
    return {"ok": True, "user": runtime.service.create(body, registration_digest=digest)}


def login(handler, body):
    runtime = account_runtime(handler)
    runtime.service.limit("login", body["email"], client_ip(handler))
    token, value = runtime.service.login(body)
    previous = getattr(handler, "account_session", None)
    if previous:
        try:
            runtime.newapi.logout(previous)
        except Exception:
            # 新登录尚未交给客户端；保留旧登录并收回未使用的凭据。
            runtime.store.revoke_session(value.token_hash)
            raise
        runtime.disconnect(previous.user["id"], previous.token_hash)
    _cookie(handler, token)
    return {"ok": True, "user": public_user(value.user), "csrf_token": value.csrf_token}


def logout(handler, body):
    value = _require_session(handler)
    runtime = account_runtime(handler)
    runtime.newapi.logout(value)
    runtime.disconnect(value.user["id"], value.token_hash)
    _cookie(handler, "")
    return {"ok": True, "newapi_sync_pending": False}


def password(handler, body):
    value = _require_session(handler)
    runtime = account_runtime(handler)
    runtime.service.change_password(value, body)
    runtime.disconnect(value.user["id"])
    _cookie(handler, "")
    return {"ok": True, "newapi_sync_pending": runtime.newapi.sync(value.user["id"])}


def users(handler, query):
    _require_session(handler, admin=True)
    try:
        page = int((query.get("page") or ["1"])[0])
        limit = int((query.get("limit") or ["30"])[0])
        if page < 1 or not 1 <= limit <= 100:
            raise ValueError
    except ValueError as exc:
        raise AccountError("分页参数无效。") from exc
    rows = [public_user(u) for u in account_runtime(handler).store.users()]
    search = (query.get("q") or [""])[0].strip().casefold()
    if search:
        rows = [u for u in rows if search in u["email"] or search in u["display_name"].casefold()]
    return {"ok": True, "items": rows[(page - 1)*limit:page*limit], "page": page, "limit": limit, "total": len(rows)}


def create_user(handler, body):
    _require_session(handler, admin=True)
    return {"ok": True, "user": account_runtime(handler).service.create(body)}


def status(handler, body):
    _require_session(handler, admin=True)
    runtime = account_runtime(handler)
    runtime.store.set_status(body["user_id"], body["status"])
    if body["status"] == "disabled":
        runtime.disconnect(body["user_id"])
    return {"ok": True, "newapi_sync_pending": runtime.newapi.sync(body["user_id"])}


def reset_password(handler, body):
    _require_session(handler, admin=True)
    runtime = account_runtime(handler)
    runtime.service.reset_password(body)
    runtime.disconnect(body["user_id"])
    return {"ok": True, "newapi_sync_pending": runtime.newapi.sync(body["user_id"])}


def _user_onebound_service(handler, user_id):
    _require_session(handler, admin=True)
    runtime = account_runtime(handler)
    user = next((item for item in runtime.store.users() if item['id'] == user_id), None)
    if user is None:
        raise AccountError('用户不存在。', status_code=404, error_code='USER_NOT_FOUND')
    return AlibabaAuthorizationService(runtime.context_for(user).config, owner_id=user_id)


def onebound_binding(handler, query):
    values = query.get('user_id', [])
    if len(values) != 1 or not values[0]:
        raise AccountError('请选择要管理的用户。')
    return {'ok': True, 'binding': _user_onebound_service(handler, values[0]).onebound_binding()}


def bind_onebound_user(handler, body):
    try:
        request = OneBoundUserBindingRequest.model_validate(body)
    except ValidationError as exc:
        raise AccountError('请选择有效的用户和万邦授权记录。') from exc
    service = _user_onebound_service(handler, request.user_id)
    try:
        binding = service.bind_onebound_directory(binding_id=request.binding_id, page=request.page)
    except ExternalRequestBlocked as exc:
        raise AccountError('万邦核验请求已中断，请在系统设置中查看。', status_code=409) from exc
    except ValueError as exc:
        raise AccountError(str(exc)) from exc
    return {'ok': True, 'binding': binding}


def newapi_info(handler, query) -> NewAPIConnectionInfo:
    value = _require_session(handler)
    runtime = account_runtime(handler)
    bridge = runtime.newapi
    return {"ok": True, "configured": bridge.configured,
            "url": bridge.public_url + "/sign-in?force_erp=true" if bridge.configured else "",
            "sync_pending": bridge.configured and bool(runtime.store.newapi_pending(value.user["id"]))}


def newapi_preview(handler, body) -> NewAPIAuthorizationPreview:
    value = _require_session(handler)
    runtime = account_runtime(handler)
    runtime.service.limit("newapi-preview", value.user["email"], client_ip(handler))
    flow = runtime.newapi.flow(body["code"])
    return {"ok": True, "target": flow["target"], "expires_at": flow["expires_at"], "code": body["code"]}


def newapi_authorize(handler, body):
    return account_runtime(handler).newapi.authorize(_require_session(handler), body, client_ip(handler))


def newapi_launch(handler, body) -> NewAPILaunch:
    origin = handler.headers.get("Origin") or os.environ.get("ERP_PUBLIC_URL") or "http://" + handler.headers["Host"]
    return account_runtime(handler).newapi.launch(_require_session(handler), body, origin.rstrip("/"), client_ip(handler))


def newapi_launch_exchange(handler, body):
    return account_runtime(handler).store.exchange_newapi_launch(body)


def newapi_exchange(handler, body):
    return account_runtime(handler).store.exchange_newapi_ticket(body)


def newapi_session(handler, body):
    return {"active": account_runtime(handler).newapi.session_active(body["user_id"], body["session_ref"])}


def newapi_user(handler, body):
    return {"active": account_runtime(handler).store.newapi_user_active(body["user_id"])}
