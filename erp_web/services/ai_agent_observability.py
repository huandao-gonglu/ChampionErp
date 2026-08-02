"""Pydantic Agent 在 AI Work 中的可读业务观测投影。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage

from .ai_tool_registry import AiToolSet


AI_WORK_REDACTED_VALUE = "[REDACTED]"
AI_WORK_TRUNCATED_VALUE = "[TRUNCATED]"
AGENT_REQUEST_EVENT = "agent.request"
AGENT_TRANSCRIPT_EVENT = "agent.transcript"
AGENT_TRANSCRIPT_VERSION = "agent.transcript.v1"

_MAX_TEXT_LENGTH = 32 * 1024
_MAX_COLLECTION_ITEMS = 200
_MAX_NESTING_DEPTH = 12
_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passwd",
        "private_key",
        "refresh_token",
        "secret",
        "token",
    }
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|authorization|"
    r"password|passwd|secret|credential)\b\s*[:=]\s*(?:bearer\s+)?([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_OPENAI_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_URL_CREDENTIAL_PATTERN = re.compile(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@")
_MODEL_MESSAGES_ADAPTER = TypeAdapter(list[ModelMessage])


def _normalized_key(value: Any) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value or ""))
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def _is_sensitive_key(value: Any) -> bool:
    key = _normalized_key(value)
    if key == "probe_token":
        return False
    return key in _SENSITIVE_KEYS or any(
        key.endswith(f"_{suffix}")
        for suffix in (
            "access_token",
            "api_key",
            "authorization",
            "client_secret",
            "cookie",
            "credential",
            "password",
            "private_key",
            "refresh_token",
            "secret",
        )
    )


def _scrub_string(value: str) -> str:
    scrubbed = _SECRET_ASSIGNMENT_PATTERN.sub(
        r"\1=" + AI_WORK_REDACTED_VALUE,
        value,
    )
    scrubbed = _BEARER_PATTERN.sub(
        "Bearer " + AI_WORK_REDACTED_VALUE,
        scrubbed,
    )
    scrubbed = _OPENAI_KEY_PATTERN.sub(AI_WORK_REDACTED_VALUE, scrubbed)
    scrubbed = _URL_CREDENTIAL_PATTERN.sub(
        r"\1" + AI_WORK_REDACTED_VALUE + "@",
        scrubbed,
    )
    if "-----BEGIN PRIVATE KEY-----" in scrubbed:
        return AI_WORK_REDACTED_VALUE
    if len(scrubbed) > _MAX_TEXT_LENGTH:
        return scrubbed[:_MAX_TEXT_LENGTH] + AI_WORK_TRUNCATED_VALUE
    return scrubbed


def sanitize_ai_work_value(
    value: Any,
    *,
    key: str = "",
    _depth: int = 0,
) -> Any:
    """生成有界、可 JSON 化的 AI Work 内容，并只屏蔽认证类敏感数据。"""

    if key and _is_sensitive_key(key):
        return AI_WORK_REDACTED_VALUE
    if _depth >= _MAX_NESTING_DEPTH:
        return AI_WORK_TRUNCATED_VALUE
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value)}
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for index, (nested_key, nested_value) in enumerate(value.items()):
            if index >= _MAX_COLLECTION_ITEMS:
                sanitized[AI_WORK_TRUNCATED_VALUE] = True
                break
            normalized_key = str(nested_key)[:256]
            sanitized[normalized_key] = sanitize_ai_work_value(
                nested_value,
                key=normalized_key,
                _depth=_depth + 1,
            )
        return sanitized
    if isinstance(value, Sequence):
        items = list(value)
        sanitized_items = [
            sanitize_ai_work_value(item, key=key, _depth=_depth + 1)
            for item in items[:_MAX_COLLECTION_ITEMS]
        ]
        if len(items) > _MAX_COLLECTION_ITEMS:
            sanitized_items.append(AI_WORK_TRUNCATED_VALUE)
        return sanitized_items
    return f"<{type(value).__name__}>"


def build_agent_request_observation(
    *,
    instructions: str,
    user_prompt: str | None,
    output_type: type[Any],
    toolset: AiToolSet,
    model_settings: Mapping[str, Any],
    max_model_requests: int,
    max_tool_calls: int,
    timeout_seconds: float,
    mode: str = "initial",
    message_history_count: int = 0,
) -> dict[str, Any]:
    """记录项目交给 Pydantic Agent 的真实输入和受控 execution profile。"""

    messages: list[dict[str, str]] = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    if user_prompt is not None:
        messages.append({"role": "user", "content": user_prompt})
    try:
        output_schema: Any = TypeAdapter(output_type).json_schema()
    except Exception:
        output_schema = {"type": getattr(output_type, "__name__", "unknown")}
    payload = {
        "mode": str(mode or "initial"),
        "messages": messages,
        "message_history_count": max(0, int(message_history_count)),
        "tools": [definition.to_dict() for definition in toolset.definitions],
        "output_schema": output_schema,
        "model_settings": dict(model_settings),
        "limits": {
            "max_model_requests": max(1, int(max_model_requests)),
            "max_tool_calls": max(1, int(max_tool_calls)),
            "timeout_seconds": max(0.001, float(timeout_seconds)),
        },
    }
    sanitized = sanitize_ai_work_value(payload)
    return sanitized if isinstance(sanitized, dict) else {}


def build_agent_transcript_observation(
    messages: Sequence[ModelMessage],
) -> dict[str, Any]:
    """投影每一轮 Pydantic model request/response，失败运行也可以使用。"""

    try:
        dumped = _MODEL_MESSAGES_ADAPTER.dump_python(list(messages), mode="json")
    except Exception:
        dumped = [
            {
                "kind": getattr(message, "kind", type(message).__name__),
                "state": getattr(message, "state", "unknown"),
                "parts": [
                    {
                        "part_kind": getattr(part, "part_kind", type(part).__name__),
                    }
                    for part in getattr(message, "parts", ())
                ],
            }
            for message in messages
        ]
    sanitized = sanitize_ai_work_value(dumped)
    return {
        "schema_version": AGENT_TRANSCRIPT_VERSION,
        "messages": sanitized if isinstance(sanitized, list) else [],
    }


__all__ = [
    "AGENT_REQUEST_EVENT",
    "AGENT_TRANSCRIPT_EVENT",
    "AGENT_TRANSCRIPT_VERSION",
    "AI_WORK_REDACTED_VALUE",
    "AI_WORK_TRUNCATED_VALUE",
    "build_agent_request_observation",
    "build_agent_transcript_observation",
    "sanitize_ai_work_value",
]
