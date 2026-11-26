"""Pydantic Model 请求错误到项目稳定错误的转换边界。"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError


AI_FATAL_MODEL_ERROR_CODES = frozenset(
    {
        "AI_PROVIDER_PAYMENT_REQUIRED",
        "AI_PROVIDER_AUTH_FAILED",
        "HTTP_401",
        "HTTP_403",
    }
)
AI_FATAL_MODEL_HTTP_STATUSES = frozenset({401, 402, 403})


def use_case_label_for(use_case_id: str) -> str:
    """按用例 ID 返回用户可读的功能名称，未知用例返回空字符串。"""

    key = str(use_case_id or "").strip()
    if not key:
        return ""
    from .ai_model_config import AI_USE_CASES

    spec = AI_USE_CASES.get(key)
    return str(spec.get("label") or "").strip() if isinstance(spec, dict) else ""


def model_error_attribution(
    *,
    use_case_id: str = "",
    use_case_label: str = "",
    model_id: str = "",
    model_name: str = "",
) -> dict[str, str]:
    """构造可安全透传到上层工具与前端的“功能 + 模型”归属字段。"""

    clean_use_case_id = safe_model_error_text(use_case_id)
    clean_use_case_label = safe_model_error_text(use_case_label) or use_case_label_for(
        clean_use_case_id
    )
    clean_model_id = safe_model_error_text(model_id)
    clean_model_name = safe_model_error_text(model_name)
    details: dict[str, str] = {}
    if clean_use_case_id:
        details["use_case_id"] = clean_use_case_id
    if clean_use_case_label:
        details["use_case_label"] = clean_use_case_label
    if clean_model_id:
        details["model_id"] = clean_model_id
    if clean_model_name:
        details["model_name"] = clean_model_name
    return details


def is_fatal_model_http_status(status_code: int | None) -> bool:
    return isinstance(status_code, int) and status_code in AI_FATAL_MODEL_HTTP_STATUSES


def format_model_http_error_message(
    *,
    status_code: int,
    raw_detail: str,
    use_case_id: str = "",
    use_case_label: str = "",
    model_id: str = "",
    model_name: str = "",
) -> str:
    """为 402 / 401 / 403 生成明确指认“功能 + 模型”的错误文案，同时保留脱敏后的原始明细。"""

    attribution = model_error_attribution(
        use_case_id=use_case_id,
        use_case_label=use_case_label,
        model_id=model_id,
        model_name=model_name,
    )
    label = attribution.get("use_case_label") or attribution.get("use_case_id") or ""
    model_display = attribution.get("model_name") or attribution.get("model_id") or ""
    if label and model_display:
        subject = f"「{label}」绑定的模型「{model_display}」"
    elif label:
        subject = f"「{label}」绑定的模型"
    elif model_display:
        subject = f"AI 模型「{model_display}」"
    else:
        subject = "AI Provider"

    detail = safe_model_error_text(raw_detail)
    suffix = f"（明细：{detail}）" if detail else ""
    if status_code == 402:
        return (
            f"{subject}余额不足或计费不可用（HTTP 402），"
            f"请充值或在「授权配置 → AI 模型」切换绑定。{suffix}"
        )
    if status_code in {401, 403}:
        return (
            f"{subject}鉴权失败或无访问权限（HTTP {status_code}），"
            f"请检查 API Key 或在「授权配置 → AI 模型」切换绑定。{suffix}"
        )
    return detail or f"{subject}请求失败（HTTP {status_code}）。"


class AIHTTPError(RuntimeError):
    """配置的 AI Model/Provider 返回 HTTP 错误。"""

    def __init__(
        self,
        *,
        status_code: int,
        reason: str,
        detail: str,
        model_id: str,
        model_name: str,
        api_style: str,
        endpoint: str,
        use_case_id: str = "",
        use_case_label: str = "",
        model_display_name: str = "",
    ) -> None:
        self.status_code = status_code
        self.reason = reason
        self.detail = detail
        self.model_id = model_id
        self.model_name = model_name
        self.api_style = api_style
        self.endpoint = endpoint
        self.use_case_id = str(use_case_id or "").strip()
        self.use_case_label = (
            str(use_case_label or "").strip() or use_case_label_for(self.use_case_id)
        )
        self.model_display_name = str(model_display_name or "").strip()
        model_label = self.model_display_name or model_id or model_name or "unknown"
        detail_text = f": {detail}" if detail else f": {reason}" if reason else ""
        raw_message = (
            f"AI 模型请求失败：{model_label} ({api_style}, {endpoint}) "
            f"HTTP {status_code}{detail_text}"
        )
        if status_code in AI_FATAL_MODEL_HTTP_STATUSES:
            message = format_model_http_error_message(
                status_code=status_code,
                raw_detail=raw_message,
                use_case_id=self.use_case_id,
                use_case_label=self.use_case_label,
                model_id=model_id,
                model_name=self.model_display_name or model_name or model_id,
            )
        else:
            message = raw_message
        super().__init__(message)


class AIModelRequestError(RuntimeError):
    """Pydantic Model 请求失败，但 Provider 没有返回可识别的 HTTP 状态。"""


def _detail_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, dict):
        error = value.get("error")
        if isinstance(error, dict):
            value = error.get("message") or error.get("detail") or error.get("code")
        else:
            value = value.get("message") or value.get("detail") or value.get("code")
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            value = str(value)
    text = re.sub(r"\s+", " ", value).strip()
    text = re.sub(
        r"Bearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer ***",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***", text)
    text = re.sub(
        r"(?i)(api[_-]?key|authorization|access[_-]?token)"
        r"(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2***",
        text,
    )
    text = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?***", text)
    return text[:1000]


def safe_model_error_text(value: Any) -> str:
    """只脱敏和收敛长度，不改变 Provider/Pydantic 的错误语义。"""

    return _detail_text(value)


def direct_model_error_payload(
    exc: Exception,
    *,
    use_case_id: str = "",
    model_id: str = "",
    model_name: str = "",
) -> dict[str, Any] | None:
    """让 Agent 和业务工具保留集中 Direct Model 边界的安全失败原因与模型归属。"""
    if not isinstance(exc, (AIHTTPError, AIModelRequestError)):
        return None
    if isinstance(exc, AIModelRequestError):
        message = safe_model_error_text(str(exc)) or "AI 模型请求失败。"
        return {
            "code": "AIModelRequestError",
            "message": message,
            "retryable": True,
            "details": model_error_attribution(
                use_case_id=use_case_id,
                model_id=model_id,
                model_name=model_name,
            ),
        }
    status_code = int(exc.status_code)
    effective_use_case_id = exc.use_case_id or str(use_case_id or "").strip()
    effective_use_case_label = exc.use_case_label or use_case_label_for(effective_use_case_id)
    effective_model_id = exc.model_id or str(model_id or "").strip()
    effective_model_name = (
        exc.model_display_name
        or exc.model_name
        or str(model_name or "").strip()
        or effective_model_id
    )
    raw_detail = safe_model_error_text(exc.detail or exc.reason or str(exc))
    if status_code in AI_FATAL_MODEL_HTTP_STATUSES:
        raw_message = (
            f"HTTP {status_code}: {raw_detail}"
            if raw_detail and f"HTTP {status_code}" not in raw_detail
            else (raw_detail or safe_model_error_text(str(exc)))
        )
        message = format_model_http_error_message(
            status_code=status_code,
            raw_detail=raw_message,
            use_case_id=effective_use_case_id,
            use_case_label=effective_use_case_label,
            model_id=effective_model_id,
            model_name=effective_model_name,
        )
    else:
        message = safe_model_error_text(str(exc)) or "AI 模型请求失败。"
    details: dict[str, Any] = {
        "status_code": status_code,
        **model_error_attribution(
            use_case_id=effective_use_case_id,
            use_case_label=effective_use_case_label,
            model_id=effective_model_id,
            model_name=effective_model_name,
        ),
    }
    return {
        "code": "AI_PROVIDER_PAYMENT_REQUIRED" if status_code == 402 else f"HTTP_{status_code}",
        "message": message,
        "retryable": status_code in {408, 425, 429} or status_code >= 500,
        "details": details,
    }


def model_http_error_payload(exc: ModelHTTPError) -> dict[str, Any]:
    """保留 Provider HTTP 错误的原始状态、代码、消息与 request ID。"""

    body = exc.body
    raw_error = body.get("error") if isinstance(body, dict) else None
    error = raw_error if isinstance(raw_error, dict) else {}
    code = (
        error.get("code")
        or (body.get("code") if isinstance(body, dict) else "")
    )
    message = (
        error.get("message")
        or error.get("detail")
        or (body.get("message") if isinstance(body, dict) else "")
        or (body.get("detail") if isinstance(body, dict) else "")
        or (raw_error if isinstance(raw_error, str) else "")
        or (body if not isinstance(body, dict) else "")
    )
    request_id = ""
    if isinstance(body, dict):
        request_id = next(
            (
                value
                for value in (
                    body.get("request_id"),
                    body.get("requestId"),
                    error.get("request_id"),
                    error.get("requestId"),
                )
                if value not in (None, "")
            ),
            "",
        )
    if not request_id and exc.headers:
        headers = {str(key).lower(): value for key, value in exc.headers.items()}
        request_id = next(
            (
                headers[key]
                for key in (
                    "x-request-id",
                    "request-id",
                    "x-dashscope-request-id",
                )
                if headers.get(key) not in (None, "")
            ),
            "",
        )

    status_code = int(exc.status_code)
    return {
        "status_code": status_code,
        "code": (_detail_text(code)[:160] or f"HTTP_{status_code}"),
        "message": (
            _detail_text(message)
            or f"Provider 返回 HTTP {status_code}，但没有提供错误消息。"
        ),
        "request_id": _detail_text(request_id)[:200],
    }


def model_http_error_detail(exc: ModelHTTPError) -> str:
    """提取可安全展示和记录的 Provider 错误码、消息与请求 ID。"""

    payload = model_http_error_payload(exc)
    parts = [
        f"code={payload['code']}",
        f"message={payload['message']}",
    ]
    if payload["request_id"]:
        parts.append(f"request_id={payload['request_id']}")
    return "; ".join(parts)[:1000]


def safe_provider_endpoint(base_url: str, api_style: str) -> str:
    """返回不含凭据和查询参数的 Provider 端点标签。"""

    parsed = urlparse(str(base_url or "").strip())
    host = parsed.netloc or parsed.path.split("/", 1)[0] or "configured-provider"
    return f"{host}/{api_style or 'model'}"


def map_pydantic_model_error(
    exc: Exception,
    *,
    model_id: str,
    model_name: str,
    api_style: str,
    base_url: str,
    use_case_id: str = "",
    model_display_name: str = "",
) -> Exception:
    """把 Pydantic 的公开异常转换为不泄密的项目错误。"""

    if isinstance(exc, AIHTTPError | AIModelRequestError):
        return exc
    if isinstance(exc, ModelHTTPError):
        return AIHTTPError(
            status_code=int(exc.status_code),
            reason="",
            detail=model_http_error_detail(exc),
            model_id=model_id,
            model_name=model_name or str(exc.model_name or ""),
            api_style=api_style,
            endpoint=safe_provider_endpoint(base_url, api_style),
            use_case_id=use_case_id,
            model_display_name=model_display_name,
        )
    if isinstance(exc, ModelAPIError):
        return AIModelRequestError(
            safe_model_error_text(exc.message)
            or f"{exc.__class__.__name__}: {model_display_name or model_id or model_name or 'unknown'}"
        )
    return exc


__all__ = [
    "AI_FATAL_MODEL_ERROR_CODES",
    "AI_FATAL_MODEL_HTTP_STATUSES",
    "AIHTTPError",
    "AIModelRequestError",
    "direct_model_error_payload",
    "format_model_http_error_message",
    "is_fatal_model_http_status",
    "map_pydantic_model_error",
    "model_error_attribution",
    "model_http_error_detail",
    "model_http_error_payload",
    "safe_model_error_text",
    "safe_provider_endpoint",
    "use_case_label_for",
]
