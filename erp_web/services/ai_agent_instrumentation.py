"""Pydantic Agent 的独立技术观测边界。

该模块只保存排障和性能分析需要的 span，不承担 AI Work 的业务状态、审批或恢复。
Pydantic AI 的内容采集在源头关闭；Exporter 再做一层递归脱敏，防止异常文本或后续新增
attribute 把 prompt、工具参数、凭据和商品敏感字段写入技术日志。
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Span, Tracer, format_span_id, format_trace_id
from pydantic_ai import InstrumentationSettings


TRACE_ENVELOPE_VERSION = "ai_technical_trace.v1"
DEFAULT_TRACE_SERVICE_NAME = "champion-erp-ai-agent"
REDACTED_VALUE = "[REDACTED]"
TRUNCATED_VALUE = "[TRUNCATED]"

_MAX_TEXT_LENGTH = 2_048
_MAX_COLLECTION_ITEMS = 100
_MAX_NESTING_DEPTH = 8
_ENTITY_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}_id$")
_SENSITIVE_KEY_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "body",
        "bullets",
        "content",
        "cookie",
        "credential",
        "credentials",
        "description",
        "input",
        "message",
        "messages",
        "output",
        "password",
        "passwd",
        "prompt",
        "request_body",
        "response_body",
        "result",
        "secret",
        "stacktrace",
        "statement",
        "title",
        "tool_arguments",
        "tool_result",
    }
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|authorization|"
    r"password|passwd|secret|credential)\b\s*[:=]\s*([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_OPENAI_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_URL_CREDENTIAL_PATTERN = re.compile(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@")

SpanWriter = Callable[[Path, str], None]


def _normalized_key_parts(key: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    parts = {part for part in normalized.split("_") if part}
    if normalized:
        parts.add(normalized)
    return parts


def _is_usage_measurement(key: str, value: Any) -> bool:
    """保留 token 数量和 cost 数值；它们不是认证 token 或支付凭据。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    lowered = key.casefold()
    return "usage" in lowered or "cost" in lowered or "duration" in lowered


def _is_sensitive_key(key: str, value: Any) -> bool:
    if _is_usage_measurement(key, value):
        return False
    lowered = key.casefold()
    if lowered in {"exception.message", "exception.stacktrace"}:
        return True
    parts = _normalized_key_parts(key)
    if parts & _SENSITIVE_KEY_PARTS:
        return True
    return any(
        marker in lowered
        for marker in (
            "api_key",
            "access_token",
            "refresh_token",
            "tool.arguments",
            "tool.result",
            "input.messages",
            "output.messages",
            "all_messages",
        )
    )


def _scrub_string(value: str) -> str:
    scrubbed = _SECRET_ASSIGNMENT_PATTERN.sub(r"\1=" + REDACTED_VALUE, value)
    scrubbed = _BEARER_PATTERN.sub("Bearer " + REDACTED_VALUE, scrubbed)
    scrubbed = _OPENAI_KEY_PATTERN.sub(REDACTED_VALUE, scrubbed)
    scrubbed = _URL_CREDENTIAL_PATTERN.sub(r"\1" + REDACTED_VALUE + "@", scrubbed)
    if "-----BEGIN PRIVATE KEY-----" in scrubbed:
        return REDACTED_VALUE
    if len(scrubbed) > _MAX_TEXT_LENGTH:
        return scrubbed[:_MAX_TEXT_LENGTH] + TRUNCATED_VALUE
    return scrubbed


def sanitize_trace_value(
    value: Any,
    *,
    key: str = "",
    _depth: int = 0,
) -> Any:
    """把任意 span 值转换成有界、可 JSON 化且不含已知敏感字段的结构。"""

    if _is_sensitive_key(key, value):
        return REDACTED_VALUE
    if _depth >= _MAX_NESTING_DEPTH:
        return TRUNCATED_VALUE
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, bytes):
        return REDACTED_VALUE
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for index, (nested_key, nested_value) in enumerate(value.items()):
            if index >= _MAX_COLLECTION_ITEMS:
                sanitized[TRUNCATED_VALUE] = True
                break
            normalized_key = str(nested_key)[:256]
            sanitized[normalized_key] = sanitize_trace_value(
                nested_value,
                key=normalized_key,
                _depth=_depth + 1,
            )
        return sanitized
    if isinstance(value, Sequence):
        return [
            sanitize_trace_value(item, key=key, _depth=_depth + 1)
            for item in value[:_MAX_COLLECTION_ITEMS]
        ]
    # 禁止通过任意对象的 __repr__ 把内部状态或凭据带入 trace。
    return f"<{type(value).__name__}>"


def build_safe_trace_attributes(
    *,
    use_case_id: str,
    conversation_id: str | None = None,
    invocation_id: str | None = None,
    agent_run_id: str | None = None,
    business_entity_ids: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """构造固定语义的关联属性；任意商品内容或非 ID 字段不会进入 trace。"""

    candidates: list[tuple[str, Any]] = [
        ("erp.ai.use_case_id", use_case_id),
        ("erp.ai.conversation_id", conversation_id),
        ("erp.ai.invocation_id", invocation_id),
        ("erp.ai.agent_run_id", agent_run_id),
    ]
    for entity_key, entity_id in (business_entity_ids or {}).items():
        normalized_key = str(entity_key).strip().casefold()
        if _ENTITY_KEY_PATTERN.fullmatch(normalized_key):
            candidates.append((f"erp.ai.entity.{normalized_key}", entity_id))

    attributes: dict[str, str] = {}
    for key, value in candidates:
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            continue
        cleaned = sanitize_trace_value(str(value), key=key)
        if not cleaned or cleaned == REDACTED_VALUE:
            continue
        attributes[key] = str(cleaned)[:256]
    return attributes


def current_trace_id() -> str | None:
    """返回当前有效 OpenTelemetry trace ID；无活动 span 时返回 ``None``。"""

    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return format_trace_id(span_context.trace_id)


def _append_private_jsonl(path: Path, line: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        # chmod 失败由后续 open/write 决定是否能写；Exporter 会统一隔离该故障。
        pass
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, (line + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _span_payload(span: ReadableSpan) -> dict[str, Any]:
    context = span.context
    parent = span.parent
    start_time = span.start_time
    end_time = span.end_time
    duration_ms = None
    if start_time is not None and end_time is not None:
        duration_ms = max(0.0, (end_time - start_time) / 1_000_000)

    events = []
    for event in span.events:
        event_attributes = sanitize_trace_value(dict(event.attributes or {}))
        events.append(
            {
                "name": _scrub_string(event.name),
                "timestamp_unix_nano": event.timestamp,
                "attributes": event_attributes,
            }
        )

    status_code = getattr(span.status.status_code, "name", str(span.status.status_code))
    scope = span.instrumentation_scope
    return {
        "schema_version": TRACE_ENVELOPE_VERSION,
        "trace_id": format_trace_id(context.trace_id),
        "span_id": format_span_id(context.span_id),
        "parent_span_id": format_span_id(parent.span_id) if parent is not None else None,
        "name": _scrub_string(span.name),
        "kind": getattr(span.kind, "name", str(span.kind)).casefold(),
        "status": str(status_code).casefold(),
        "start_time_unix_nano": start_time,
        "end_time_unix_nano": end_time,
        "duration_ms": duration_ms,
        "attributes": sanitize_trace_value(dict(span.attributes or {})),
        "events": events,
        "resource": sanitize_trace_value(dict(span.resource.attributes or {})),
        "instrumentation_scope": {
            "name": _scrub_string(scope.name),
            "version": _scrub_string(scope.version) if scope.version else None,
        },
    }


class SafeJsonlSpanExporter(SpanExporter):
    """把 span 以脱敏 JSONL 输出，并将所有后端写入故障隔离在观测边界内。"""

    def __init__(self, path: Path | str, *, writer: SpanWriter | None = None) -> None:
        self.path = Path(path)
        self._writer = writer or _append_private_jsonl
        self._lock = threading.Lock()
        self._closed = False
        self._exported_span_count = 0
        self._failed_export_count = 0

    @property
    def exported_span_count(self) -> int:
        with self._lock:
            return self._exported_span_count

    @property
    def failed_export_count(self) -> int:
        with self._lock:
            return self._failed_export_count

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            if self._closed:
                return SpanExportResult.SUCCESS
            try:
                lines = [
                    json.dumps(
                        _span_payload(span),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    for span in spans
                ]
                for line in lines:
                    self._writer(self.path, line)
                self._exported_span_count += len(lines)
            except Exception:
                # 技术观测不得改变 Agent 的业务结果或异常语义。
                self._failed_export_count += 1
            return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        del timeout_millis
        return True

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True


@dataclass(frozen=True)
class AiAgentTrace:
    """一次 Agent run 的技术 trace 关联句柄。"""

    trace_id: str
    span_id: str
    _span: Span = field(repr=False, compare=False)

    def set_agent_run_id(self, agent_run_id: str) -> None:
        attributes = build_safe_trace_attributes(
            use_case_id="",
            agent_run_id=agent_run_id,
        )
        value = attributes.get("erp.ai.agent_run_id")
        if value:
            self._span.set_attribute("erp.ai.agent_run_id", value)


class AiAgentInstrumentation:
    """独立的 Pydantic instrumentation 装配与 ERP run span owner。"""

    def __init__(
        self,
        path: Path | str,
        *,
        exporter: SafeJsonlSpanExporter | None = None,
        service_name: str = DEFAULT_TRACE_SERVICE_NAME,
    ) -> None:
        self.exporter = exporter or SafeJsonlSpanExporter(path)
        self.tracer_provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": _scrub_string(service_name),
                    "service.namespace": "champion-erp",
                }
            ),
            shutdown_on_exit=False,
        )
        self.tracer_provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer: Tracer = self.tracer_provider.get_tracer(
            "erp_web.services.ai_agent_instrumentation"
        )
        self.settings = InstrumentationSettings(
            tracer_provider=self.tracer_provider,
            include_content=False,
            include_binary_content=False,
            include_model_request_parameters=False,
            version=5,
        )

    @contextmanager
    def start_run_span(
        self,
        *,
        use_case_id: str,
        conversation_id: str | None = None,
        invocation_id: str | None = None,
        agent_run_id: str | None = None,
        business_entity_ids: Mapping[str, str] | None = None,
    ) -> Iterator[AiAgentTrace]:
        attributes = build_safe_trace_attributes(
            use_case_id=use_case_id,
            conversation_id=conversation_id,
            invocation_id=invocation_id,
            agent_run_id=agent_run_id,
            business_entity_ids=business_entity_ids,
        )
        with self.tracer.start_as_current_span(
            "erp.ai.agent.run",
            attributes=attributes,
        ) as span:
            span_context = span.get_span_context()
            trace_id = format_trace_id(span_context.trace_id)
            span_id = format_span_id(span_context.span_id)
            span.set_attribute("erp.ai.trace_id", trace_id)
            yield AiAgentTrace(trace_id=trace_id, span_id=span_id, _span=span)

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        try:
            return bool(self.tracer_provider.force_flush(timeout_millis))
        except Exception:
            return False

    def shutdown(self) -> None:
        try:
            self.tracer_provider.shutdown()
        except Exception:
            pass


__all__ = [
    "AiAgentInstrumentation",
    "AiAgentTrace",
    "DEFAULT_TRACE_SERVICE_NAME",
    "REDACTED_VALUE",
    "SafeJsonlSpanExporter",
    "TRACE_ENVELOPE_VERSION",
    "build_safe_trace_attributes",
    "current_trace_id",
    "sanitize_trace_value",
]
