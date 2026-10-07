"""业务操作关联与传输上下文。仅读取本地绑定，不发身份预检查请求。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
import hashlib
import re
import urllib.parse
from uuid import uuid4

from erp_web.context import get_context
from erp_web.schemas.external_requests import RequestContext

_operation = ContextVar("external_operation", default=None)
_rejections = ContextVar("external_request_rejections", default=None)


@contextmanager
def request_operation(source, *, operation_id="", trigger="manual", cancel=None, deadline_at=None, cancellation_check=None):
    value = {"source": source, "operation_id": operation_id or uuid4().hex, "trigger": trigger, "cancel": cancel, "deadline_at":deadline_at,"cancellation_check":cancellation_check}
    token = _operation.set(value)
    try:
        yield
    finally:
        _operation.reset(token)


def credential_fingerprint(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:24] if value else "public"


def request_context(url, headers=None, *, data=None, method="GET", timeout=30, source="http_client", platform="", account_id="", semantics=None):
    parsed = urllib.parse.urlsplit(url)
    headers = {k.lower():v for k,v in (headers or {}).items()}
    host = parsed.hostname or "external"
    platform = platform or ({"api-seller.ozon.ru":"ozon", "api.mercadolibre.com":"mercadolibre", "api.partner.market.yandex.ru":"yandex"}.get(host)) or host
    secret = headers.get("api-key") or headers.get("authorization") or headers.get("token") or ""
    credential_id = credential_fingerprint(secret)
    if platform == "ozon":
        account_id = account_id or headers.get("client-id", "")
    elif platform in {"mercadolibre","yandex"}:
        store = get_context().config.load_store_config().get(platform,{})
        saved = str(store.get("access_token" if platform == "mercadolibre" else "api_token") or "")
        actual = secret.removeprefix("Bearer ")
        if actual and actual == saved:
            account_id = account_id or str(store.get("user_id" if platform == "mercadolibre" else "business_id") or "")
        if platform == "mercadolibre" and parsed.path == "/oauth/token":
            form = urllib.parse.parse_qs((data or b"").decode("utf-8"))
            credential_id = credential_fingerprint(form.get("refresh_token") or form.get("code"))
            account_id = account_id or str(store.get("user_id") or (form.get("client_id") or [""])[0])
    account_id = account_id or ("credential:"+credential_id)
    # POST 查询由平台契约列举；未声明的请求保守按写入处理。
    read_post = platform == "ozon" and any(part in parsed.path for part in ("/info", "/list", "/tree", "/attribute", "/seller/info", "/posting/fbs/get", "/posting/fbo/get")) and not any(part in parsed.path for part in ("/update", "/import", "/delete"))
    if platform == "yandex":
        read_post = method.upper() == "POST" and not any(part in parsed.path for part in ("/update","/delete","/create")) and not parsed.path.endswith("/hidden-offers")
    # Yandex 的发货批次搜索虽使用 PUT，但官方契约是只读；其他 PUT 保持写入语义。
    read_put = platform == "yandex" and method.upper() == "PUT" and bool(re.fullmatch(r"/v2/campaigns/[1-9][0-9]*/first-mile/shipments", parsed.path))
    # 接口标识去掉 URL 参数与动态数字 ID，避免敏感查询进入日志。
    interface = re.sub(r"/[0-9]+(?=/|$)", "/:id", (parsed.path or "/"))[:240]
    known_ai_path = any(parsed.path.endswith(path) for path in ("/chat/completions", "/responses", "/embeddings", "/images/generations", "/images/edits"))
    if platform not in {"ozon","mercadolibre","yandex"} and not (platform.startswith("ai:") and known_ai_path):
        interface = "/resource/"+credential_fingerprint(parsed.path or "/")
    elif platform.startswith("ai:"):
        interface = next(path for path in ("/chat/completions", "/responses", "/embeddings", "/images/generations", "/images/edits") if parsed.path.endswith(path))
    quota_match = re.search(r"/(campaigns|businesses)/([0-9]+)", parsed.path)
    quota_key = (quota_match.group(1)+":"+quota_match.group(2)) if platform == "yandex" and quota_match else "business:"+account_id
    ctx = RequestContext(platform=platform, account_id=account_id[:128],credential_id=credential_id,
        interface=interface,source=source,timeout=timeout,quota_key=quota_key,
        fingerprint=hashlib.sha256(method.encode()+url.encode()+(data or b"")).hexdigest(),
        semantics=semantics or ("read" if method.upper() in {"GET","HEAD"} or read_post or read_put else "write"))
    operation = _operation.get()
    return replace(ctx,**{**operation,"source":operation["source"]+":"+source}) if operation else ctx


@contextmanager
def capture_request_rejections():
    notices = []
    token = _rejections.set(notices)
    try:
        yield notices
    finally:
        _rejections.reset(token)


def note_request_rejection(failure):
    notices = _rejections.get()
    if notices is not None and failure.scope:
        notices.append(failure.code)


def current_operation_id():
    return (_operation.get() or {}).get("operation_id", "")
