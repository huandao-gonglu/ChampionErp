"""Pydantic Model 请求错误到项目稳定错误的转换边界。"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError


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
    ) -> None:
        self.status_code = status_code
        self.reason = reason
        self.detail = detail
        self.model_id = model_id
        self.model_name = model_name
        self.api_style = api_style
        self.endpoint = endpoint
        model_label = model_id or model_name or "unknown"
        detail_text = f": {detail}" if detail else f": {reason}" if reason else ""
        super().__init__(
            f"AI 模型请求失败：{model_label} ({api_style}, {endpoint}) "
            f"HTTP {status_code}{detail_text}"
        )


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
        )
    if isinstance(exc, ModelAPIError):
        return AIModelRequestError(
            safe_model_error_text(exc.message)
            or f"{exc.__class__.__name__}: {model_id or model_name or 'unknown'}"
        )
    return exc


__all__ = [
    "AIHTTPError",
    "AIModelRequestError",
    "map_pydantic_model_error",
    "model_http_error_detail",
    "model_http_error_payload",
    "safe_model_error_text",
    "safe_provider_endpoint",
]
