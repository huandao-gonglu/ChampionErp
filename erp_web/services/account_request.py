"""所有真实 HTTP 请求的身份边界；公开例外按具体端点声明。"""
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from http.cookies import SimpleCookie, CookieError
from threading import Lock
from urllib.parse import parse_qs, urlsplit
import secrets
import ipaddress
import os

from erp_web.context import bind_context, get_context
from erp_web.http_request import validate_request_metadata
from erp_web.schemas.accounts import AccountError, SESSION_COOKIE
from erp_web.schemas.requests import RequestValidationError

_session = ContextVar("erp_account_session", default=None)
_runtime_lock = Lock()
PUBLIC_POST_PATHS = frozenset({"/api/account/login", "/api/account/register", "/api/account/registration-code"})
CALLBACK_POST_PATHS = frozenset({f"/api/{p}/notifications" for p in ("mercadolibre", "ozon", "yandex")})
CALLBACK_GET_PATHS = frozenset({"/auth/alibaba/callback", "/auth/mercadolibre/callback"})
INTERNAL_NEWAPI_PATHS = frozenset({"/api/internal/newapi/exchange", "/api/internal/newapi/launch", "/api/internal/newapi/session", "/api/internal/newapi/user"})


def current_session():
    return _session.get()


def client_ip(handler):
    peer = handler.client_address[0]
    # 公网反代必须覆盖 X-Real-IP；仅信任回环代理提供的实际客户端地址。
    if os.environ.get("ERP_PUBLIC_URL", "").startswith("https://") and ipaddress.ip_address(peer).is_loopback:
        value = handler.headers.get("X-Real-IP", "").strip()
        if value:
            try:
                return str(ipaddress.ip_address(value))
            except ValueError:
                pass
    return peer


def account_runtime(handler):
    with _runtime_lock:
        if not hasattr(handler.server, "account_runtime"):
            from erp_web.account_runtime import AccountRuntime
            handler.server.account_runtime = AccountRuntime(get_context())
        return handler.server.account_runtime


def session_cookie(headers):
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("Cookie", ""))
        value = cookie.get(SESSION_COOKIE)
        return value.value if value else ""
    except CookieError:
        return ""


def _callback_user(runtime, parsed):
    query = parse_qs(parsed.query)
    # 新回调只把 owner 当作路由信息；原 token/state 仍由领域服务验证。
    owner = (query.get("owner") or [""])[0]
    if parsed.path in CALLBACK_GET_PATHS:
        state = (query.get("state") or [""])[0]
        parts = state.split(".")
        if len(parts) >= 3 and parts[0] == "account":
            owner = parts[1]
    if owner:
        user = next((u for u in runtime.store.users() if u["id"] == owner), None)
    else:
        user = runtime.store.admin()
    if user is None:
        raise AccountError("回调所属账号不存在。", status_code=404, error_code="CALLBACK_OWNER_NOT_FOUND")
    return user


def _authenticate(handler, stack, public_pages):
    validate_request_metadata(handler)
    runtime = account_runtime(handler)
    parsed = urlsplit(handler.path)
    method = handler.command
    if method == "POST" and parsed.path in INTERNAL_NEWAPI_PATHS:
        secret = runtime.newapi.secret
        if (len(secret) < 32 or not secrets.compare_digest(handler.headers.get("Authorization", "").encode(), ("Bearer " + secret).encode())
                or handler.headers.get("Origin") or handler.headers.get("Cookie")):
            raise AccountError("内部服务身份无效。", status_code=401, error_code="SERVICE_UNAUTHORIZED")
        handler.account_session = None
        handler.desktop_scope_allowed = False
        stack.enter_context(bind_context(runtime.root))
        return
    session = runtime.store.session(session_cookie(handler.headers))
    handler.account_session = session
    _session.set(session)
    public = method == "GET" and (
        parsed.path in public_pages
        or parsed.path in {"/api/account/session", "/api/account/registration-config", "/licenses/THIRD-PARTY-NOTICES.txt"}
        or parsed.path.startswith("/assets/")
    )
    account_endpoint = parsed.path.startswith(("/api/account/", "/api/admin/"))
    callback = (method == "POST" and parsed.path in CALLBACK_POST_PATHS) or (method == "GET" and parsed.path in CALLBACK_GET_PATHS)
    handler.desktop_scope_allowed = not (public or account_endpoint or callback)
    if not callback and session is not None and method not in {"GET", "HEAD", "OPTIONS"}:
        csrf = handler.headers.get("X-CSRF-Token", "")
        if not csrf or not secrets.compare_digest(csrf.encode("utf-8"), session.csrf_token.encode("ascii")):
            raise AccountError("请求校验失败，请刷新页面后重试。", status_code=403, error_code="CSRF_INVALID")
    if callback:
        user = _callback_user(runtime, parsed)
    elif public or (method == "POST" and parsed.path in PUBLIC_POST_PATHS):
        user = session.user if session else None
    else:
        if session is None:
            raise AccountError("请先登录。", status_code=401, error_code="UNAUTHENTICATED")
        user = session.user
    admin_only = parsed.path.startswith("/api/admin/") or (method == "POST" and parsed.path in {
        "/api/system-settings/forbidden-categories/import", "/api/alibaba/authorization/complete",
    })
    if admin_only and (session is None or session.user["role"] != "admin"):
        raise AccountError("需要管理员权限。", status_code=403, error_code="FORBIDDEN")
    # 页面资源和账号接口不装配个人业务服务，业务身份只来自服务端会话。
    context = runtime.context_for(user) if user and not (public or account_endpoint) else runtime.root
    stack.enter_context(bind_context(context))


@contextmanager
def authenticated_request(handler, *, public_pages=()):
    token = _session.set(None)
    try:
        with ExitStack() as stack:
            allowed = False
            try:
                _authenticate(handler, stack, public_pages)
                allowed = True
            except RequestValidationError as exc:
                handler.send_json({"ok": False, "error": str(exc), "error_code": exc.error_code}, exc.status_code)
            yield allowed
    finally:
        _session.reset(token)
