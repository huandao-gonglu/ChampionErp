"""平台拒绝含义的纯解析；未知拒绝只阻止对应接口，不猜测账号封禁。"""
from __future__ import annotations

import json
import re
import time
from email.utils import parsedate_to_datetime

from erp_web.schemas.external_requests import RequestFailure


def platform_error_codes(raw):
    """仅保留错误码字段中的短枚举值，拒绝自然语言或任意响应内容。"""
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        return []
    if not isinstance(body, dict):
        return []
    rows = [body]
    for key in ("error", "errors"):
        value = body.get(key)
        rows.extend([value] if isinstance(value, dict) else value[:8] if isinstance(value, list) else [])
    codes = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or row.get("error_code") or "")
        if re.fullmatch(r"(?:-?\d{1,8}|[A-Z][A-Z_0-9.]{1,63})", code) and code not in codes:
            codes.append(code)
    return codes[:8]


def retry_after(headers, *, now=None):
    value = str(headers.get("Retry-After", "") or "").strip()
    now = time.time() if now is None else now
    try:
        seconds = float(value)
        return now + seconds if 0 < seconds < 365*86400 else None
    except ValueError:
        try:
            at = parsedate_to_datetime(value).timestamp()
            return at if now < at < now+365*86400 else None
        except (ValueError, TypeError, OverflowError):
            return None


def classify_response(platform, status, headers, raw, *, method=""):
    """只从错误字段识别业务失败；商品内容中的文字不得改变请求控制。"""
    # S3 HeadObject 的 404 是“对象尚不存在”的查询事实，上传前会正常出现。
    # SDK 仍接收原始 404；仅阻断与失败计数不将它当成接口拒绝。
    if platform == "image_hosting:s3" and method.upper() == "HEAD" and status == 404:
        return None
    try:
        body = json.loads(raw) if raw else {}
    except (ValueError, UnicodeError):
        body = {}
    body = body if isinstance(body,dict) else {}
    error = body.get("error") or body.get("errors")
    failed = status >= 400 or bool(error) or str(body.get("status", "")).upper() in {"ERROR","FAILED"} or body.get("success") is False
    if platform == "ozon" and body.get("code") not in (None, 0, "0", ""):
        failed = True
    if not failed:
        return None
    parts = [body.get(k) for k in ("error","errors","message","msg","code","error_code") if body.get(k)]
    text = json.dumps(parts,ensure_ascii=False).casefold()
    # 非 JSON 错误页只用于判别，不进入审计日志。
    if status >= 400 and not parts:
        text = raw[:4096].decode("utf-8",errors="replace").casefold()
    if platform == "image_hosting:public" and status in (401, 403):
        # 匿名读取没有可刷新凭据；公开权限修正后的新测试应能重新请求。
        code = "IMAGE_HOSTING_PUBLIC_AUTH_REQUIRED" if status == 401 else "IMAGE_HOSTING_PUBLIC_ACCESS_DENIED"
        return RequestFailure(code, "图片地址拒绝匿名读取，请检查桶公开权限和公开地址", "request", status=status)
    prefix = re.sub(r"[^A-Z0-9_]", "_", platform.upper())
    if "api access disabled" in text or "account suspended" in text or "account blocked" in text:
        return RequestFailure(prefix+"_ACCOUNT_DISABLED", "平台已停用该账号的 API；请完成平台恢复后明确解除阻断", "account", status=status)
    if "revoked" in text:
        return RequestFailure(prefix+"_CREDENTIAL_REVOKED", "平台凭据已撤销，请重新授权", "credential", status=status)
    if status in (420,429) or any(v in text for v in ('too_many_requests','rate_limit_exceeded','rate limit exceeded')):
        scope = "account"
        resume_at = retry_after(headers)
        if platform == "yandex":
            if headers.get("X-RateLimit-Resource-Until") or "resource" in text:
                scope = "interface"
                resume_at = resume_at or retry_after({"Retry-After":headers.get("X-RateLimit-Resource-Until", "")})
            elif "parallel requests" in text:
                scope = "quota"
        return RequestFailure(prefix+"_RATE_LIMITED", "平台限流；请等待恢复时间，未提供时间时需稍后明确恢复", scope, resume_at, status or 429)
    if status == 401 or any(v in text for v in ('invalid_token','token_expired','invalid access token','token expired')):
        return RequestFailure(prefix+"_AUTH_FAILED", "当前凭据失效；请按平台协议刷新或重新授权", "credential", status=401)
    if status == 403 or any(v in text for v in ('forbidden','access_denied','permission_denied')):
        return RequestFailure(prefix+"_AUTH_FAILED", "接口访问被拒绝；请核对该接口权限后明确恢复", "interface", status=403)
    if status >= 500 or status == 408:
        return RequestFailure(prefix+"_SERVER_ERROR", "平台服务暂时不可用", status=status,retryable=True)
    return RequestFailure(prefix+"_REQUEST_INVALID", "平台明确拒绝请求，请检查参数与业务回执", "request", status=status)


def response_quota_pause(platform, headers):
    if platform == "yandex" and str(headers.get("X-RateLimit-Resource-Remaining")) == "0":
        return RequestFailure("YANDEX_RESOURCE_LIMIT", "该接口资源配额已用完，请等待平台恢复时间", "interface",
            retry_after({"Retry-After":headers.get("X-RateLimit-Resource-Until", "")}),420)
    return None
