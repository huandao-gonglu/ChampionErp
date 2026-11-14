"""账号 HTTP 编排；只读取共享账号库，不装配模型工具。"""
from http.cookies import SimpleCookie
import os

from erp_web.schemas.accounts import AccountError, SESSION_COOKIE, SESSION_SECONDS, public_user
from erp_web.schemas.newapi import NewAPIConnectionInfo, NewAPIAuthorizationPreview, NewAPILaunch
from erp_web.services import registration_verification
from erp_web.services.account_request import account_runtime, client_ip


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
    rows = [public_user(u) for u in account_runtime(handler).store.users() if u["role"] == "user"]
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
