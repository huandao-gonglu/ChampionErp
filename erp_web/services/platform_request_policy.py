"""按平台响应契约识别请求拒绝；业务状态不代表接口不可用。"""
from __future__ import annotations

import json
import re
import time
from email.utils import parsedate_to_datetime

from erp_web.schemas.external_requests import RequestFailure


def platform_error_codes(raw, *, platform=""):
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
        code = str(row.get("code") or row.get("error_code") or
                   (row.get("errorCode") if platform == "gw.open.1688.com" else "") or "")
        allowed = re.fullmatch(r"(?:-?\d{1,8}|[A-Z][A-Z_0-9.]{1,63})", code)
        if platform == "gw.open.1688.com":
            allowed = allowed or re.fullmatch(r"(?:\d{1,8}_\d{1,8}|gw\.[A-Za-z][A-Za-z0-9]{1,60})", code)
        if allowed and code not in codes:
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


def _failed_envelope(platform, body):
    """只解释已接入平台的响应信封，不把任意 JSON 的状态当作协议错误。"""
    error = body.get("error") or body.get("errors")
    if platform == "gw.open.1688.com":
        if body.get("success") is True or body.get("success") == "true":
            return False
        return (body.get("success") is False or body.get("success") == "false") or bool(
            body.get("errorCode") or body.get("error_code") or error
        )
    if platform == "yandex":
        return body.get("status") == "ERROR"
    if platform == "ozon":
        return body.get("code") not in (None, 0, "0", "") or bool(error)
    if platform == "mercadolibre" or platform.startswith("ai:"):
        return bool(body.get("error"))
    # 未声明的业务协议交给原适配器；HTTP 拒绝仍由统一入口处理。
    return False


def classify_response(platform, status, headers, raw, *, method="", interface=""):
    """HTTP 状态优先；正常业务状态仅按平台和具体接口契约识别。"""
    # S3 HeadObject 的 404 是“对象尚不存在”的查询事实，上传前会正常出现。
    # SDK 仍接收原始 404；仅阻断与失败计数不将它当成接口拒绝。
    if platform == "image_hosting:s3" and method.upper() == "HEAD" and status == 404:
        return None
    try:
        body = json.loads(raw) if raw else {}
    except (ValueError, UnicodeError):
        body = {}
    body = body if isinstance(body,dict) else {}
    if (status == 200 and platform == "gw.open.1688.com"
            and interface == "alibaba.trade.getLogisticsInfos.buyerView"
            and body.get("errorCode") == "500_2"
            and not (body.get("error") or body.get("errors") or body.get("error_code"))):
        # 与 1688 客户端一致：未发货是空物流结果，不计失败，也不阻止原请求。
        return None
    failed = status >= 400 or _failed_envelope(platform, body)
    if not failed:
        return None
    parts = [body.get(k) for k in ("error","errors","message","msg","code","error_code","errorCode") if body.get(k)]
    text = json.dumps(parts,ensure_ascii=False).casefold()
    # 非 JSON 错误页只用于判别，不进入审计日志。
    if status >= 400 and not parts:
        text = raw[:4096].decode("utf-8",errors="replace").casefold()
    if platform == "image_hosting:public" and status in (401, 403):
        # 匿名读取没有可刷新凭据；公开权限修正后的新测试应能重新请求。
        code = "IMAGE_HOSTING_PUBLIC_AUTH_REQUIRED" if status == 401 else "IMAGE_HOSTING_PUBLIC_ACCESS_DENIED"
        return RequestFailure(code, "图片地址拒绝匿名读取，请检查桶公开权限和公开地址", "request", status=status)
    prefix = re.sub(r"[^A-Z0-9_]", "_", platform.upper())
    if platform.startswith("ai:") and status == 402:
        return RequestFailure(prefix+"_PAYMENT_REQUIRED", "AI Provider 余额不足或计费配置不可用，请检查额度与计费状态", "request", status=status)
    if (platform == "gw.open.1688.com" and status < 500
            and status not in (401, 420, 429)
            and (body.get("errorCode") or body.get("error_code")) == "gw.APIACLDecline"):
        return RequestFailure(prefix+"_AUTH_FAILED", "1688 应用未获该接口权限（gw.APIACLDecline），请核对应用解决方案与接口授权", "interface", status=status)
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
    codes = platform_error_codes(raw, platform=platform)
    detail = "（错误码：" + "、".join(codes) + "）" if codes else ""
    return RequestFailure(prefix+"_REQUEST_INVALID", f"平台明确拒绝请求{detail}，请检查参数与业务回执", "request", status=status)


def response_quota_pause(platform, headers):
    if platform == "yandex" and str(headers.get("X-RateLimit-Resource-Remaining")) == "0":
        return RequestFailure("YANDEX_RESOURCE_LIMIT", "该接口资源配额已用完，请等待平台恢复时间", "interface",
            retry_after({"Retry-After":headers.get("X-RateLimit-Resource-Until", "")}),420)
    return None
