"""按平台响应契约识别请求拒绝；业务状态不代表接口不可用。"""
from __future__ import annotations

from email.utils import parsedate_to_datetime
import json
import re
import time

from erp_web.schemas.external_requests import RequestFailure

_ACCOUNT_DISABLED_MARKERS = ("api access disabled", "account suspended", "account blocked")


def _header(headers, name: str) -> str:
    if not headers:
        return ""
    value = headers.get(name)
    if value is not None:
        return str(value).strip()
    target = name.lower()
    for key, raw in headers.items():
        if str(key).lower() == target:
            return str(raw or "").strip()
    return ""


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
            allowed = allowed or re.fullmatch(r"(?:\d{1,8}_\d{1,8}|gw\.[A-Za-z][A-Za-z0-9]{1,60}|order(?:\.[a-z]{1,24}){1,3})", code)
        if allowed and code not in codes:
            codes.append(code)
    return codes[:8]


def _parse_retry_value(value: str, *, now: float) -> float | None:
    value = str(value or "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
        return now + seconds if 0 < seconds < 365 * 86400 else None
    except ValueError:
        try:
            at = parsedate_to_datetime(value).timestamp()
            return at if now < at < now + 365 * 86400 else None
        except (ValueError, TypeError, OverflowError):
            return None


def retry_after(headers, *, now=None):
    now = time.time() if now is None else now
    return _parse_retry_value(_header(headers, "Retry-After"), now=now)


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


def _classify_ozon(status: int, headers, codes: list[str], text: str, detail: str, interface: str) -> RequestFailure:
    """Ozon 优先按 HTTP 状态码与 gRPC 业务 code 分类，不依赖英文错误文案。"""
    if status == 429 or "8" in codes or any(v in text for v in ("too_many_requests", "rate_limit_exceeded", "rate limit exceeded")):
        return RequestFailure("OZON_RATE_LIMITED", "Ozon 接口限流，将自动退避后重试", "account", retry_after(headers), status or 429)
    if any(marker in text for marker in _ACCOUNT_DISABLED_MARKERS):
        return RequestFailure("OZON_ACCOUNT_DISABLED", "Ozon 已停用该账号的 API，请在授权配置核对并重新测试连接", "account", status=status or 403)
    if status == 401 or "16" in codes:
        return RequestFailure("OZON_AUTH_FAILED", "Ozon 凭据无效或已失效，请在授权配置重新核对并测试连接", "credential", status=401)
    if status == 403 or "7" in codes:
        if interface == "/v1/seller/info":
            return RequestFailure("OZON_ACCOUNT_DISABLED", "Ozon 拒绝账号基础权限访问，请在授权配置核对 Key 或账号状态后重新测试连接", "account", status=403)
        return RequestFailure("OZON_AUTH_FAILED", "Ozon 当前 API Key 缺少该接口权限，请核对角色权限", "interface", status=403)
    if status >= 500 or status == 408 or (status >= 400 and any(code in {"4", "13", "14"} for code in codes)):
        return RequestFailure("OZON_SERVER_ERROR", "Ozon 服务暂时不可用", status=status or 503, retryable=True)
    if status == 400 or "3" in codes:
        return RequestFailure("OZON_REQUEST_INVALID", f"平台明确拒绝请求{detail}，请检查参数与业务回执", "request", status=status or 400)
    if any(v in text for v in ("forbidden", "access_denied", "permission_denied")):
        return RequestFailure("OZON_AUTH_FAILED", "Ozon 接口访问被拒绝，请核对权限", "interface", status=status or 403)
    return RequestFailure("OZON_REQUEST_INVALID", f"平台明确拒绝请求{detail}，请检查参数与业务回执", "request", status=status)


def classify_response(platform, status, headers, raw, *, method="", interface=""):
    """HTTP 状态优先；正常业务状态仅按平台和具体接口契约识别。"""
    # S3 HeadObject 的 404 是“对象尚不存在”的查询事实，上传前会正常出现。
    # SDK 仍接收原始 404；仅限流与审计不将它当成接口拒绝。
    if platform == "image_hosting:s3" and method.upper() == "HEAD" and status == 404:
        return None
    try:
        body = json.loads(raw) if raw else {}
    except (ValueError, UnicodeError):
        body = {}
    body = body if isinstance(body, dict) else {}
    if (status == 200 and platform == "gw.open.1688.com"
            and interface == "alibaba.trade.getLogisticsInfos.buyerView"
            and body.get("errorCode") == "500_2"
            and not (body.get("error") or body.get("errors") or body.get("error_code"))):
        # 与 1688 客户端一致：未发货是空物流结果，不计失败，也不阻止原请求。
        return None
    failed = status >= 400 or _failed_envelope(platform, body)
    if not failed:
        return None
    parts = [body.get(k) for k in ("error", "errors", "message", "msg", "code", "error_code", "errorCode") if body.get(k)]
    text = json.dumps(parts, ensure_ascii=False).casefold()
    # 非 JSON 错误页只用于判别，不进入审计日志。
    if status >= 400 and not parts:
        text = raw[:4096].decode("utf-8", errors="replace").casefold()
    if platform == "image_hosting:public" and status in (401, 403):
        code = "IMAGE_HOSTING_PUBLIC_AUTH_REQUIRED" if status == 401 else "IMAGE_HOSTING_PUBLIC_ACCESS_DENIED"
        return RequestFailure(code, "图片地址拒绝匿名读取，请检查桶公开权限和公开地址", "request", status=status)
    prefix = re.sub(r"[^A-Z0-9_]", "_", platform.upper())
    codes = platform_error_codes(raw, platform=platform)
    detail = "（错误码：" + "、".join(codes) + "）" if codes else ""
    if platform == "ozon":
        return _classify_ozon(status, headers, codes, text, detail, interface)
    if platform == "yandex" and (status == 400 or (status == 200 and "BAD_REQUEST" in codes)):
        # 参数互斥错误也会写 Forbidden，不能据此阻断整个费用接口。
        return RequestFailure(prefix + "_REQUEST_INVALID", f"平台明确拒绝请求{detail}，请检查参数与业务回执", "request", status=status)
    if platform.startswith("ai:") and status == 402:
        return RequestFailure(prefix + "_PAYMENT_REQUIRED", "AI Provider 余额不足或计费配置不可用，请检查额度与计费状态", "request", status=status)
    if (platform == "gw.open.1688.com" and status < 500
            and status not in (401, 420, 429)
            and (body.get("errorCode") or body.get("error_code")) == "gw.APIACLDecline"):
        return RequestFailure(prefix + "_AUTH_FAILED", "1688 应用未获该接口权限（gw.APIACLDecline），请核对应用解决方案与接口授权", "interface", status=status)
    if any(marker in text for marker in _ACCOUNT_DISABLED_MARKERS):
        return RequestFailure(prefix + "_ACCOUNT_DISABLED", "平台已停用该账号的 API，请在授权配置核对并重新测试连接", "account", status=status)
    if "revoked" in text:
        return RequestFailure(prefix + "_CREDENTIAL_REVOKED", "平台凭据已撤销，请在授权配置重新授权", "credential", status=status)
    if status in (420, 429) or any(v in text for v in ("too_many_requests", "rate_limit_exceeded", "rate limit exceeded")):
        scope = "account"
        resume_at = retry_after(headers)
        if platform == "yandex":
            until_header = _header(headers, "X-RateLimit-Resource-Until")
            if until_header or "resource" in text:
                scope = "interface"
                resume_at = resume_at or _parse_retry_value(until_header, now=time.time())
            elif "parallel requests" in text:
                scope = "quota"
        return RequestFailure(prefix + "_RATE_LIMITED", "平台限流，将等待冷却后自动恢复", scope, resume_at, status or 429)
    if status == 401 or any(v in text for v in ("invalid_token", "token_expired", "invalid access token", "token expired")):
        return RequestFailure(prefix + "_AUTH_FAILED", "当前凭据失效，请在授权配置刷新或重新授权", "credential", status=401)
    if status == 403 or any(v in text for v in ("forbidden", "access_denied", "permission_denied")):
        return RequestFailure(prefix + "_AUTH_FAILED", "接口访问被拒绝，请核对该接口权限", "interface", status=403)
    if status >= 500 or status == 408:
        return RequestFailure(prefix + "_SERVER_ERROR", "平台服务暂时不可用", status=status, retryable=True)
    return RequestFailure(prefix + "_REQUEST_INVALID", f"平台明确拒绝请求{detail}，请检查参数与业务回执", "request", status=status)


def response_quota_pause(platform, headers, *, now=None):
    now = time.time() if now is None else now
    if platform == "yandex" and _header(headers, "X-RateLimit-Resource-Remaining") == "0":
        return RequestFailure(
            "YANDEX_RESOURCE_LIMIT",
            "该接口资源配额已用完，将等待平台恢复时间",
            "interface",
            _parse_retry_value(_header(headers, "X-RateLimit-Resource-Until"), now=now),
            420,
        )
    return None


def response_rate_pause(platform, headers, *, now=None) -> float | None:
    """Ozon 响应头 Ratelimit-Remaining: 0 时仅暂停进程内存令牌桶，不落阻断表。"""
    now = time.time() if now is None else now
    if platform == "ozon" and _header(headers, "Ratelimit-Remaining") == "0":
        reset_raw = _header(headers, "Ratelimit-Reset") or _header(headers, "X-RateLimit-Reset")
        if reset_raw:
            try:
                value = float(reset_raw)
                # 兼容秒数相对值与 Unix 时间戳。
                if value > 1_000_000_000:
                    return max(now + 0.1, min(value, now + 60))
                if 0 < value <= 60:
                    return now + value
            except ValueError:
                parsed = _parse_retry_value(reset_raw, now=now)
                if parsed is not None:
                    return min(parsed, now + 60)
        return now + 1.0
    return None
