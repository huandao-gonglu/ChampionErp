"""Pydantic Agent deferred run 的版本化持久化边界。

本模块只保存恢复一次 Agent run 所需的公开消息模型、deferred 请求和安全的
ERP 引用。它不保存 Model/Provider 实例、凭据或第三方内部对象，也不承担工具
执行、审批授权或工作流编排。
"""

from __future__ import annotations

import json
import math
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Iterator, Literal, Mapping, Sequence, cast
from uuid import uuid4

from pydantic import TypeAdapter
from pydantic_ai import (
    DeferredToolRequests,
    ModelMessage,
    ModelMessagesTypeAdapter,
)


AI_AGENT_STATE_SCHEMA_VERSION = 1
AI_AGENT_STATE_RELATIVE_DIR = Path("data") / "ai_agent_state"
MAX_AI_AGENT_STATE_BYTES = 8 * 1024 * 1024
DEFAULT_RESUME_LEASE_SECONDS = 60.0
MAX_RESUME_LEASE_SECONDS = 300.0

AiAgentStateStatus = Literal[
    "pending",
    "resuming",
    "ready",
    "completed",
    "denied",
    "failed",
    "in_doubt",
]
AiAgentApprovalDecision = Literal["approved", "denied"]

_STATE_STATUSES = frozenset(
    {"pending", "resuming", "ready", "completed", "denied", "failed", "in_doubt"}
)
_APPROVAL_DECISIONS = frozenset({"approved", "denied"})
_SENSITIVE_MAPPING_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "password",
        "refresh_token",
        "secret",
        "token",
        "access_token",
    }
)
_DEFERRED_REQUESTS_ADAPTER = TypeAdapter(DeferredToolRequests)
_JSON_VALUE_ADAPTER = TypeAdapter(Any)
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


class AiAgentStateError(RuntimeError):
    """状态边界的稳定错误；消息不拼接文件内容或底层异常。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, repr=False)
class AiAgentStateSecurity:
    """恢复时必须重新验证的安全快照，而不是授权凭据。"""

    actor_id: str
    tenant_id: str
    required_permissions: frozenset[str] = frozenset()
    business_scope: Mapping[str, str] = field(default_factory=dict)
    idempotency_context: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor_id", _required_text(self.actor_id))
        object.__setattr__(self, "tenant_id", _required_text(self.tenant_id))
        object.__setattr__(
            self,
            "required_permissions",
            frozenset(_safe_permission(value) for value in self.required_permissions),
        )
        object.__setattr__(
            self,
            "business_scope",
            MappingProxyType(_safe_string_mapping(self.business_scope)),
        )
        object.__setattr__(
            self,
            "idempotency_context",
            MappingProxyType(_safe_string_mapping(self.idempotency_context)),
        )

    def __repr__(self) -> str:
        return (
            "AiAgentStateSecurity("
            f"actor_id={self.actor_id!r}, tenant_id={self.tenant_id!r}, "
            f"required_permissions={sorted(self.required_permissions)!r})"
        )


@dataclass(frozen=True)
class AiAgentApprovalRecord:
    """只保存审批决定和主体引用，不保存自由文本或凭据。"""

    tool_call_id: str
    decision: AiAgentApprovalDecision
    actor_id: str
    decided_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_call_id", _required_text(self.tool_call_id))
        object.__setattr__(self, "actor_id", _required_text(self.actor_id))
        if self.decision not in _APPROVAL_DECISIONS:
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        _require_aware_datetime(self.decided_at)


@dataclass(frozen=True)
class AiAgentResumeClaim:
    """一次恢复 attempt 的有限租约。"""

    claim_id: str
    claimed_at: datetime
    lease_expires_at: datetime
    tool_execution_started_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _safe_claim_id(self.claim_id))
        _require_aware_datetime(self.claimed_at)
        _require_aware_datetime(self.lease_expires_at)
        if self.lease_expires_at <= self.claimed_at:
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        if self.tool_execution_started_at is not None:
            _require_aware_datetime(self.tool_execution_started_at)
            if not (
                self.claimed_at
                <= self.tool_execution_started_at
                < self.lease_expires_at
            ):
                raise AiAgentStateError(
                    "AI_AGENT_STATE_INVALID",
                    "Agent 恢复状态参数无效。",
                )


@dataclass(frozen=True, repr=False)
class AiAgentResumeResult:
    """Agent 已完成恢复运行、等待业务终检消费的 durable 结果。"""

    message_history: tuple[ModelMessage, ...]
    output_payload: Any = field(repr=False)
    run_id: str
    attempt_id: str
    trace_id: str
    usage: Mapping[str, int | float]
    ready_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_history", tuple(self.message_history))
        object.__setattr__(self, "output_payload", _json_safe_payload(self.output_payload))
        object.__setattr__(self, "run_id", _required_text(self.run_id))
        object.__setattr__(self, "attempt_id", _required_text(self.attempt_id))
        object.__setattr__(self, "trace_id", _required_text(self.trace_id))
        object.__setattr__(self, "usage", MappingProxyType(_safe_usage(self.usage)))
        _require_aware_datetime(self.ready_at)

    def __repr__(self) -> str:
        return (
            "AiAgentResumeResult("
            f"run_id={self.run_id!r}, attempt_id={self.attempt_id!r}, "
            f"trace_id={self.trace_id!r}, ready_at={self.ready_at!r})"
        )


@dataclass(frozen=True, repr=False)
class AiAgentStateEnvelope:
    """进程内的 v1 envelope；第三方对象只存在于该持久化边界。"""

    state_id: str
    status: AiAgentStateStatus
    revision: int
    use_case_id: str
    profile_version: str
    toolset_id: str
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime
    security: AiAgentStateSecurity
    references: Mapping[str, str]
    message_history: tuple[ModelMessage, ...]
    deferred_requests: DeferredToolRequests
    approval_records: tuple[AiAgentApprovalRecord, ...] = ()
    resume_claim: AiAgentResumeClaim | None = None
    resume_result: AiAgentResumeResult | None = None
    schema_version: int = AI_AGENT_STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "state_id", _safe_state_id(self.state_id))
        if self.schema_version != AI_AGENT_STATE_SCHEMA_VERSION:
            raise AiAgentStateError(
                "AI_AGENT_STATE_VERSION_UNSUPPORTED",
                "Agent 恢复状态版本不受支持。",
            )
        if self.status not in _STATE_STATUSES:
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        if isinstance(self.revision, bool) or self.revision < 0:
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        object.__setattr__(self, "use_case_id", _required_text(self.use_case_id))
        object.__setattr__(self, "profile_version", _required_text(self.profile_version))
        object.__setattr__(self, "toolset_id", _required_text(self.toolset_id))
        _require_aware_datetime(self.created_at)
        _require_aware_datetime(self.updated_at)
        _require_aware_datetime(self.deadline_at)
        if self.updated_at < self.created_at:
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        object.__setattr__(
            self,
            "references",
            MappingProxyType(_safe_string_mapping(self.references)),
        )
        object.__setattr__(self, "message_history", tuple(self.message_history))
        object.__setattr__(self, "approval_records", tuple(self.approval_records))
        tool_call_ids = [record.tool_call_id for record in self.approval_records]
        if len(tool_call_ids) != len(set(tool_call_ids)):
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        if self.status == "ready" and (
            self.resume_claim is None or self.resume_result is None
        ):
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        if self.status == "ready" and (
            self.message_history != self.resume_result.message_history
        ):
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        if self.status in {"pending", "denied"} and (
            self.resume_claim is not None or self.resume_result is not None
        ):
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        if self.status == "resuming" and self.resume_result is not None:
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        if self.resume_claim is not None and (
            self.resume_claim.claimed_at < self.created_at
            or self.resume_claim.lease_expires_at > self.deadline_at
        ):
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        if self.resume_result is not None and self.resume_result.ready_at > self.updated_at:
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        if (
            self.resume_claim is not None
            and self.resume_result is not None
            and not (
                self.resume_claim.claimed_at
                <= self.resume_result.ready_at
                < self.resume_claim.lease_expires_at
            )
        ):
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )

    def __repr__(self) -> str:
        return (
            "AiAgentStateEnvelope("
            f"state_id={self.state_id!r}, status={self.status!r}, "
            f"revision={self.revision}, use_case_id={self.use_case_id!r}, "
            f"profile_version={self.profile_version!r}, toolset_id={self.toolset_id!r})"
        )


def _required_text(value: Any, *, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise AiAgentStateError(
            "AI_AGENT_STATE_INVALID",
            "Agent 恢复状态参数无效。",
        )
    text = value.strip()
    if not text or len(text) > maximum or any(ord(char) < 32 for char in text):
        raise AiAgentStateError(
            "AI_AGENT_STATE_INVALID",
            "Agent 恢复状态参数无效。",
        )
    return text


def _safe_state_id(value: Any) -> str:
    text = _required_text(value, maximum=160)
    safe = "".join(char for char in text if char.isalnum() or char in {"_", "-"})
    if text != safe:
        raise AiAgentStateError(
            "AI_AGENT_STATE_INVALID_ID",
            "Agent 恢复状态 ID 无效。",
        )
    return text


def _safe_claim_id(value: Any) -> str:
    text = _required_text(value, maximum=160)
    safe = "".join(char for char in text if char.isalnum() or char in {"_", "-"})
    if text != safe:
        raise AiAgentStateError(
            "AI_AGENT_STATE_INVALID_CLAIM",
            "Agent 恢复 claim 无效。",
        )
    return text


def _safe_permission(value: Any) -> str:
    return _required_text(value, maximum=160)


def _normalized_mapping_key(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _safe_string_mapping(value: Mapping[str, str] | Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise AiAgentStateError(
            "AI_AGENT_STATE_INVALID",
            "Agent 恢复状态参数无效。",
        )
    result: dict[str, str] = {}
    if len(value) > 64:
        raise AiAgentStateError(
            "AI_AGENT_STATE_INVALID",
            "Agent 恢复状态参数无效。",
        )
    for raw_key, raw_value in value.items():
        key = _required_text(raw_key, maximum=160)
        if _normalized_mapping_key(key) in _SENSITIVE_MAPPING_KEYS:
            raise AiAgentStateError(
                "AI_AGENT_STATE_SENSITIVE_DATA_REJECTED",
                "Agent 恢复状态包含禁止持久化的敏感字段。",
            )
        item = _required_text(raw_value, maximum=1024)
        result[key] = item
    return result


def _safe_usage(value: Mapping[str, int | float] | Any) -> dict[str, int | float]:
    if not isinstance(value, Mapping) or len(value) > 64:
        raise AiAgentStateError(
            "AI_AGENT_STATE_INVALID",
            "Agent 恢复状态参数无效。",
        )
    result: dict[str, int | float] = {}
    for raw_key, raw_value in value.items():
        key = _required_text(raw_key, maximum=160)
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        if raw_value < 0 or (
            isinstance(raw_value, float) and not math.isfinite(raw_value)
        ):
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        result[key] = raw_value
    return result


def _json_safe_payload(value: Any) -> Any:
    try:
        serialized = _JSON_VALUE_ADAPTER.dump_python(
            value,
            mode="json",
            warnings="error",
        )
        return json.loads(
            json.dumps(
                serialized,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        )
    except Exception:
        raise AiAgentStateError(
            "AI_AGENT_STATE_OUTPUT_INVALID",
            "Agent 恢复结果不是有效的 JSON payload。",
        ) from None


def _lease_seconds(value: Any) -> float:
    if isinstance(value, bool):
        raise AiAgentStateError(
            "AI_AGENT_STATE_LEASE_INVALID",
            "Agent 恢复 lease 必须是有限的正数。",
        )
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        raise AiAgentStateError(
            "AI_AGENT_STATE_LEASE_INVALID",
            "Agent 恢复 lease 必须是有限的正数。",
        ) from None
    if not math.isfinite(seconds) or seconds <= 0 or seconds > MAX_RESUME_LEASE_SECONDS:
        raise AiAgentStateError(
            "AI_AGENT_STATE_LEASE_INVALID",
            "Agent 恢复 lease 必须是有限的正数。",
        )
    return seconds


def _require_aware_datetime(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise AiAgentStateError(
            "AI_AGENT_STATE_INVALID",
            "Agent 恢复状态参数无效。",
        )
    return value


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError
    return parsed


def _as_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    keys = frozenset(value)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise ValueError


def _approval_payload(record: AiAgentApprovalRecord) -> dict[str, Any]:
    return {
        "tool_call_id": record.tool_call_id,
        "decision": record.decision,
        "actor_id": record.actor_id,
        "decided_at": record.decided_at.isoformat(),
    }


def _claim_payload(claim: AiAgentResumeClaim | None) -> dict[str, Any] | None:
    if claim is None:
        return None
    return {
        "claim_id": claim.claim_id,
        "claimed_at": claim.claimed_at.isoformat(),
        "lease_expires_at": claim.lease_expires_at.isoformat(),
        "tool_execution_started_at": (
            claim.tool_execution_started_at.isoformat()
            if claim.tool_execution_started_at is not None
            else None
        ),
    }


def _resume_result_payload(
    result: AiAgentResumeResult | None,
) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "output_payload": _json_safe_payload(result.output_payload),
        "run_id": result.run_id,
        "attempt_id": result.attempt_id,
        "trace_id": result.trace_id,
        "usage": dict(result.usage),
        "ready_at": result.ready_at.isoformat(),
    }


def _envelope_payload(envelope: AiAgentStateEnvelope) -> dict[str, Any]:
    try:
        messages = ModelMessagesTypeAdapter.dump_python(
            list(envelope.message_history),
            mode="json",
        )
        deferred = _DEFERRED_REQUESTS_ADAPTER.dump_python(
            envelope.deferred_requests,
            mode="json",
        )
        resume_result = _resume_result_payload(envelope.resume_result)
    except Exception:
        raise AiAgentStateError(
            "AI_AGENT_STATE_INVALID",
            "Agent 恢复状态参数无效。",
        ) from None
    return {
        "schema_version": AI_AGENT_STATE_SCHEMA_VERSION,
        "state_id": envelope.state_id,
        "status": envelope.status,
        "revision": envelope.revision,
        "profile": {
            "use_case_id": envelope.use_case_id,
            "profile_version": envelope.profile_version,
            "toolset_id": envelope.toolset_id,
        },
        "timestamps": {
            "created_at": envelope.created_at.isoformat(),
            "updated_at": envelope.updated_at.isoformat(),
            "deadline_at": envelope.deadline_at.isoformat(),
        },
        "security": {
            "actor_id": envelope.security.actor_id,
            "tenant_id": envelope.security.tenant_id,
            "required_permissions": sorted(envelope.security.required_permissions),
            "business_scope": dict(envelope.security.business_scope),
            "idempotency_context": dict(envelope.security.idempotency_context),
        },
        "references": dict(envelope.references),
        "message_history": messages,
        "deferred_requests": deferred,
        "approval_records": [
            _approval_payload(record) for record in envelope.approval_records
        ],
        "resume_claim": _claim_payload(envelope.resume_claim),
        "resume_result": resume_result,
    }


def _decode_resume_claim(value: Any) -> AiAgentResumeClaim | None:
    if value is None:
        return None
    claim = _as_mapping(value)
    _require_exact_keys(
        claim,
        required=frozenset(
            {
                "claim_id",
                "claimed_at",
                "lease_expires_at",
                "tool_execution_started_at",
            }
        ),
    )
    started = claim["tool_execution_started_at"]
    return AiAgentResumeClaim(
        claim_id=claim["claim_id"],
        claimed_at=_parse_datetime(claim["claimed_at"]),
        lease_expires_at=_parse_datetime(claim["lease_expires_at"]),
        tool_execution_started_at=(
            _parse_datetime(started) if started is not None else None
        ),
    )


def _decode_resume_result(
    value: Any,
    *,
    message_history: tuple[ModelMessage, ...],
) -> AiAgentResumeResult | None:
    if value is None:
        return None
    result = _as_mapping(value)
    _require_exact_keys(
        result,
        required=frozenset(
            {
                "output_payload",
                "run_id",
                "attempt_id",
                "trace_id",
                "usage",
                "ready_at",
            }
        ),
    )
    return AiAgentResumeResult(
        message_history=message_history,
        output_payload=result["output_payload"],
        run_id=result["run_id"],
        attempt_id=result["attempt_id"],
        trace_id=result["trace_id"],
        usage=result["usage"],
        ready_at=_parse_datetime(result["ready_at"]),
    )


def _decode_envelope(payload: Any) -> AiAgentStateEnvelope:
    try:
        root = _as_mapping(payload)
        version = root.get("schema_version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError
        if version != AI_AGENT_STATE_SCHEMA_VERSION:
            raise AiAgentStateError(
                "AI_AGENT_STATE_VERSION_UNSUPPORTED",
                "Agent 恢复状态版本不受支持。",
            )
        _require_exact_keys(
            root,
            required=frozenset(
                {
                    "schema_version",
                    "state_id",
                    "status",
                    "revision",
                    "profile",
                    "timestamps",
                    "security",
                    "references",
                    "message_history",
                    "deferred_requests",
                    "approval_records",
                    "resume_claim",
                    "resume_result",
                }
            ),
        )
        profile = _as_mapping(root["profile"])
        timestamps = _as_mapping(root["timestamps"])
        security = _as_mapping(root["security"])
        _require_exact_keys(
            profile,
            required=frozenset({"use_case_id", "profile_version", "toolset_id"}),
        )
        _require_exact_keys(
            timestamps,
            required=frozenset({"created_at", "updated_at", "deadline_at"}),
        )
        _require_exact_keys(
            security,
            required=frozenset(
                {
                    "actor_id",
                    "tenant_id",
                    "required_permissions",
                    "business_scope",
                    "idempotency_context",
                }
            ),
        )
        permissions = security["required_permissions"]
        if not isinstance(permissions, list):
            raise ValueError
        raw_approvals = root["approval_records"]
        if not isinstance(raw_approvals, list):
            raise ValueError
        approval_records: list[AiAgentApprovalRecord] = []
        for raw_record in raw_approvals:
            record = _as_mapping(raw_record)
            _require_exact_keys(
                record,
                required=frozenset(
                    {"tool_call_id", "decision", "actor_id", "decided_at"}
                ),
            )
            approval_records.append(
                AiAgentApprovalRecord(
                    tool_call_id=record["tool_call_id"],
                    decision=cast(AiAgentApprovalDecision, record["decision"]),
                    actor_id=record["actor_id"],
                    decided_at=_parse_datetime(record["decided_at"]),
                )
            )
        messages = tuple(
            ModelMessagesTypeAdapter.validate_python(root["message_history"])
        )
        deferred = _DEFERRED_REQUESTS_ADAPTER.validate_python(
            root["deferred_requests"]
        )
        resume_claim = _decode_resume_claim(root.get("resume_claim"))
        resume_result = _decode_resume_result(
            root.get("resume_result"),
            message_history=messages,
        )
        status = root["status"]
        if not isinstance(status, str):
            raise ValueError
        revision = root["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise ValueError
        return AiAgentStateEnvelope(
            schema_version=AI_AGENT_STATE_SCHEMA_VERSION,
            state_id=root["state_id"],
            status=cast(AiAgentStateStatus, status),
            revision=revision,
            use_case_id=profile["use_case_id"],
            profile_version=profile["profile_version"],
            toolset_id=profile["toolset_id"],
            created_at=_parse_datetime(timestamps["created_at"]),
            updated_at=_parse_datetime(timestamps["updated_at"]),
            deadline_at=_parse_datetime(timestamps["deadline_at"]),
            security=AiAgentStateSecurity(
                actor_id=security["actor_id"],
                tenant_id=security["tenant_id"],
                required_permissions=frozenset(permissions),
                business_scope=security["business_scope"],
                idempotency_context=security["idempotency_context"],
            ),
            references=root["references"],
            message_history=messages,
            deferred_requests=deferred,
            approval_records=tuple(approval_records),
            resume_claim=resume_claim,
            resume_result=resume_result,
        )
    except AiAgentStateError as exc:
        if exc.code == "AI_AGENT_STATE_VERSION_UNSUPPORTED":
            raise
        raise AiAgentStateError(
            "AI_AGENT_STATE_CORRUPT",
            "Agent 恢复状态损坏或格式无效。",
        ) from None
    except Exception:
        raise AiAgentStateError(
            "AI_AGENT_STATE_CORRUPT",
            "Agent 恢复状态损坏或格式无效。",
        ) from None


def _validate_security_context(
    envelope: AiAgentStateEnvelope,
    *,
    use_case_id: str,
    profile_version: str,
    toolset_id: str,
    tenant_id: str,
    business_scope: Mapping[str, str],
    idempotency_context: Mapping[str, str],
    permissions: Iterable[str],
    now: datetime | None = None,
    check_deadline: bool,
) -> None:
    if _required_text(use_case_id) != envelope.use_case_id:
        raise AiAgentStateError(
            "AI_AGENT_STATE_PROFILE_MISMATCH",
            "Agent 恢复配置与持久化状态不一致。",
        )
    if _required_text(profile_version) != envelope.profile_version:
        raise AiAgentStateError(
            "AI_AGENT_STATE_PROFILE_MISMATCH",
            "Agent 恢复配置与持久化状态不一致。",
        )
    if _required_text(toolset_id) != envelope.toolset_id:
        raise AiAgentStateError(
            "AI_AGENT_STATE_TOOLSET_MISMATCH",
            "Agent 恢复工具集与持久化状态不一致。",
        )
    if _required_text(tenant_id) != envelope.security.tenant_id:
        raise AiAgentStateError(
            "AI_AGENT_STATE_SCOPE_MISMATCH",
            "Agent 恢复业务作用域与持久化状态不一致。",
        )
    if _safe_string_mapping(business_scope) != dict(envelope.security.business_scope):
        raise AiAgentStateError(
            "AI_AGENT_STATE_SCOPE_MISMATCH",
            "Agent 恢复业务作用域与持久化状态不一致。",
        )
    if _safe_string_mapping(idempotency_context) != dict(
        envelope.security.idempotency_context
    ):
        raise AiAgentStateError(
            "AI_AGENT_STATE_IDEMPOTENCY_MISMATCH",
            "Agent 恢复幂等上下文与持久化状态不一致。",
        )
    current_permissions = frozenset(_safe_permission(value) for value in permissions)
    if not envelope.security.required_permissions.issubset(current_permissions):
        raise AiAgentStateError(
            "AI_AGENT_STATE_PERMISSION_DENIED",
            "当前主体无权恢复 Agent 状态。",
        )
    if check_deadline:
        current = now or datetime.now(timezone.utc)
        _require_aware_datetime(current)
    else:
        current = None
    if current is not None and current >= envelope.deadline_at:
        raise AiAgentStateError(
            "AI_AGENT_STATE_DEADLINE_EXCEEDED",
            "Agent 恢复状态已超过总 deadline。",
        )


def validate_resume_context(
    envelope: AiAgentStateEnvelope,
    *,
    use_case_id: str,
    profile_version: str,
    toolset_id: str,
    tenant_id: str,
    business_scope: Mapping[str, str],
    idempotency_context: Mapping[str, str],
    permissions: Iterable[str],
    now: datetime | None = None,
) -> None:
    """恢复执行前比较当前 ERP 上下文并强制原总 deadline。"""

    _validate_security_context(
        envelope,
        use_case_id=use_case_id,
        profile_version=profile_version,
        toolset_id=toolset_id,
        tenant_id=tenant_id,
        business_scope=business_scope,
        idempotency_context=idempotency_context,
        permissions=permissions,
        now=now,
        check_deadline=True,
    )


def validate_ready_context(
    envelope: AiAgentStateEnvelope,
    *,
    use_case_id: str,
    profile_version: str,
    toolset_id: str,
    tenant_id: str,
    business_scope: Mapping[str, str],
    idempotency_context: Mapping[str, str],
    permissions: Iterable[str],
) -> None:
    """校验 ready 结果的读取权限；重放不会执行 model/tool，故忽略旧 deadline。"""

    if envelope.status != "ready" or envelope.resume_result is None:
        raise AiAgentStateError(
            "AI_AGENT_STATE_NOT_READY",
            "Agent 恢复结果尚未 ready。",
        )
    _validate_security_context(
        envelope,
        use_case_id=use_case_id,
        profile_version=profile_version,
        toolset_id=toolset_id,
        tenant_id=tenant_id,
        business_scope=business_scope,
        idempotency_context=idempotency_context,
        permissions=permissions,
        check_deadline=False,
    )


def _process_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    """使用稳定 lock inode，在同进程和跨进程间串行化 CAS。"""

    process_lock = _process_lock(path)
    with process_lock:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            else:  # pragma: no cover - Windows CI 可验证该分支
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                if os.name != "nt":
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                else:  # pragma: no cover - Windows CI 可验证该分支
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


class AiAgentStateStore:
    """以原子 JSON 文件保存 Agent 恢复状态，并提供一次性领取语义。"""

    def __init__(self, app_dir: Path | str) -> None:
        self.root = Path(app_dir).resolve() / AI_AGENT_STATE_RELATIVE_DIR
        try:
            _ensure_private_directory(self.root)
        except OSError:
            raise AiAgentStateError(
                "AI_AGENT_STATE_STORAGE_UNAVAILABLE",
                "Agent 恢复状态存储不可用。",
            ) from None

    def state_path(self, state_id: str) -> Path:
        return self.root / f"{_safe_state_id(state_id)}.json"

    def _lock_path(self, state_id: str) -> Path:
        return self.root / f".{_safe_state_id(state_id)}.lock"

    @contextmanager
    def _locked(self, state_id: str) -> Iterator[None]:
        try:
            with _exclusive_file_lock(self._lock_path(state_id)):
                yield
        except AiAgentStateError:
            raise
        except OSError:
            raise AiAgentStateError(
                "AI_AGENT_STATE_STORAGE_UNAVAILABLE",
                "Agent 恢复状态存储不可用。",
            ) from None

    def _load_unlocked(self, state_id: str) -> AiAgentStateEnvelope:
        path = self.state_path(state_id)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raise AiAgentStateError(
                "AI_AGENT_STATE_NOT_FOUND",
                "Agent 恢复状态不存在。",
            ) from None
        except OSError:
            raise AiAgentStateError(
                "AI_AGENT_STATE_READ_FAILED",
                "无法读取 Agent 恢复状态。",
            ) from None
        if len(raw) > MAX_AI_AGENT_STATE_BYTES:
            raise AiAgentStateError(
                "AI_AGENT_STATE_CORRUPT",
                "Agent 恢复状态损坏或格式无效。",
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise AiAgentStateError(
                "AI_AGENT_STATE_CORRUPT",
                "Agent 恢复状态损坏或格式无效。",
            ) from None
        envelope = _decode_envelope(payload)
        if envelope.state_id != _safe_state_id(state_id):
            raise AiAgentStateError(
                "AI_AGENT_STATE_CORRUPT",
                "Agent 恢复状态损坏或格式无效。",
            )
        return envelope

    def _write_unlocked(self, envelope: AiAgentStateEnvelope) -> None:
        path = self.state_path(envelope.state_id)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        descriptor: int | None = None
        try:
            payload = _envelope_payload(envelope)
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ).encode("utf-8")
            if len(encoded) > MAX_AI_AGENT_STATE_BYTES:
                raise AiAgentStateError(
                    "AI_AGENT_STATE_TOO_LARGE",
                    "Agent 恢复状态超过允许大小。",
                )
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                path.chmod(0o600)
        except AiAgentStateError:
            raise
        except (OSError, TypeError, ValueError):
            raise AiAgentStateError(
                "AI_AGENT_STATE_WRITE_FAILED",
                "无法保存 Agent 恢复状态。",
            ) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def create_pending(
        self,
        *,
        use_case_id: str,
        profile_version: str,
        toolset_id: str,
        deadline_at: datetime,
        actor_id: str,
        tenant_id: str,
        required_permissions: Iterable[str],
        business_scope: Mapping[str, str],
        idempotency_context: Mapping[str, str],
        message_history: Sequence[ModelMessage],
        deferred_requests: DeferredToolRequests,
        references: Mapping[str, str] | None = None,
        state_id: str = "",
        now: datetime | None = None,
    ) -> AiAgentStateEnvelope:
        """创建唯一 pending 状态；同名状态绝不覆盖。"""

        safe_state_id = _safe_state_id(state_id or f"agent_state_{uuid4().hex}")
        current = now or datetime.now(timezone.utc)
        _require_aware_datetime(current)
        _require_aware_datetime(deadline_at)
        if deadline_at <= current:
            raise AiAgentStateError(
                "AI_AGENT_STATE_DEADLINE_EXCEEDED",
                "Agent 恢复状态已超过总 deadline。",
            )
        try:
            normalized_messages = tuple(
                ModelMessagesTypeAdapter.validate_python(
                    ModelMessagesTypeAdapter.dump_python(
                        list(message_history),
                        mode="json",
                    )
                )
            )
            normalized_deferred = _DEFERRED_REQUESTS_ADAPTER.validate_python(
                _DEFERRED_REQUESTS_ADAPTER.dump_python(
                    deferred_requests,
                    mode="json",
                )
            )
        except Exception:
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            ) from None
        if not normalized_deferred.calls and not normalized_deferred.approvals:
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        envelope = AiAgentStateEnvelope(
            state_id=safe_state_id,
            status="pending",
            revision=0,
            use_case_id=use_case_id,
            profile_version=profile_version,
            toolset_id=toolset_id,
            created_at=current,
            updated_at=current,
            deadline_at=deadline_at,
            security=AiAgentStateSecurity(
                actor_id=actor_id,
                tenant_id=tenant_id,
                required_permissions=frozenset(required_permissions),
                business_scope=business_scope,
                idempotency_context=idempotency_context,
            ),
            references=references or {},
            message_history=normalized_messages,
            deferred_requests=normalized_deferred,
        )
        with self._locked(safe_state_id):
            if self.state_path(safe_state_id).exists():
                raise AiAgentStateError(
                    "AI_AGENT_STATE_ALREADY_EXISTS",
                    "Agent 恢复状态已存在。",
                )
            self._write_unlocked(envelope)
        return envelope

    def load(self, state_id: str) -> AiAgentStateEnvelope:
        safe_state_id = _safe_state_id(state_id)
        with self._locked(safe_state_id):
            return self._load_unlocked(safe_state_id)

    def load_ready_for_replay(
        self,
        state_id: str,
        *,
        use_case_id: str,
        profile_version: str,
        toolset_id: str,
        tenant_id: str,
        business_scope: Mapping[str, str],
        idempotency_context: Mapping[str, str],
        permissions: Iterable[str],
    ) -> AiAgentStateEnvelope:
        """安全读取 ready 结果；纯重放不受原执行 deadline 限制。"""

        safe_state_id = _safe_state_id(state_id)
        with self._locked(safe_state_id):
            envelope = self._load_unlocked(safe_state_id)
            validate_ready_context(
                envelope,
                use_case_id=use_case_id,
                profile_version=profile_version,
                toolset_id=toolset_id,
                tenant_id=tenant_id,
                business_scope=business_scope,
                idempotency_context=idempotency_context,
                permissions=permissions,
            )
            return envelope

    def claim_for_resume(
        self,
        state_id: str,
        *,
        use_case_id: str,
        profile_version: str,
        toolset_id: str,
        tenant_id: str,
        business_scope: Mapping[str, str],
        idempotency_context: Mapping[str, str],
        permissions: Iterable[str],
        approval_records: Sequence[AiAgentApprovalRecord] = (),
        lease_seconds: float = DEFAULT_RESUME_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> AiAgentStateEnvelope:
        """校验当前上下文并原子领取 pending 状态，防止重复恢复副作用。"""

        safe_state_id = _safe_state_id(state_id)
        current = now or datetime.now(timezone.utc)
        _require_aware_datetime(current)
        safe_lease_seconds = _lease_seconds(lease_seconds)
        with self._locked(safe_state_id):
            envelope = self._load_unlocked(safe_state_id)
            validate_resume_context(
                envelope,
                use_case_id=use_case_id,
                profile_version=profile_version,
                toolset_id=toolset_id,
                tenant_id=tenant_id,
                business_scope=business_scope,
                idempotency_context=idempotency_context,
                permissions=permissions,
                now=current,
            )
            if envelope.status != "pending":
                raise AiAgentStateError(
                    "AI_AGENT_STATE_ALREADY_CLAIMED",
                    "Agent 恢复状态已被领取或已经结束。",
                )
            _validate_resume_approvals(envelope, approval_records)
            lease_expires_at = min(
                current + timedelta(seconds=safe_lease_seconds),
                envelope.deadline_at,
            )
            next_envelope = replace(
                envelope,
                status="resuming",
                revision=envelope.revision + 1,
                updated_at=current,
                resume_claim=AiAgentResumeClaim(
                    claim_id=f"resume_claim_{uuid4().hex}",
                    claimed_at=current,
                    lease_expires_at=lease_expires_at,
                ),
                resume_result=None,
                approval_records=_merge_approval_records(
                    envelope.approval_records,
                    approval_records,
                ),
            )
            self._write_unlocked(next_envelope)
            return next_envelope

    def mark_tool_execution_started(
        self,
        state_id: str,
        *,
        claim_id: str,
        now: datetime | None = None,
    ) -> AiAgentStateEnvelope:
        """在任何恢复工具真正执行前持久化不可自动重试边界。"""

        safe_state_id = _safe_state_id(state_id)
        current = now or datetime.now(timezone.utc)
        _require_aware_datetime(current)
        with self._locked(safe_state_id):
            envelope = self._load_unlocked(safe_state_id)
            if envelope.status != "resuming":
                raise AiAgentStateError(
                    "AI_AGENT_STATE_STATUS_CONFLICT",
                    "Agent 恢复状态已发生变化。",
                )
            claim = _require_claim(
                envelope,
                claim_id=claim_id,
                now=current,
                require_live=True,
            )
            if claim.tool_execution_started_at is not None:
                return envelope
            next_envelope = replace(
                envelope,
                revision=envelope.revision + 1,
                updated_at=current,
                resume_claim=replace(
                    claim,
                    tool_execution_started_at=current,
                ),
            )
            self._write_unlocked(next_envelope)
            return next_envelope

    def mark_resume_ready(
        self,
        state_id: str,
        *,
        claim_id: str,
        message_history: Sequence[ModelMessage],
        output_payload: Any,
        run_id: str,
        attempt_id: str,
        trace_id: str,
        usage: Mapping[str, int | float],
        now: datetime | None = None,
    ) -> AiAgentStateEnvelope:
        """持久化完整恢复结果后进入 ready；业务终检不得早于该 CAS。"""

        if isinstance(output_payload, DeferredToolRequests):
            raise AiAgentStateError(
                "AI_AGENT_STATE_OUTPUT_INVALID",
                "Agent 恢复结果不是有效的 JSON payload。",
            )
        safe_state_id = _safe_state_id(state_id)
        current = now or datetime.now(timezone.utc)
        _require_aware_datetime(current)
        normalized_messages = _normalize_messages(message_history)
        resume_result = AiAgentResumeResult(
            message_history=normalized_messages,
            output_payload=output_payload,
            run_id=run_id,
            attempt_id=attempt_id,
            trace_id=trace_id,
            usage=usage,
            ready_at=current,
        )
        with self._locked(safe_state_id):
            envelope = self._load_unlocked(safe_state_id)
            if envelope.status != "resuming":
                raise AiAgentStateError(
                    "AI_AGENT_STATE_STATUS_CONFLICT",
                    "Agent 恢复状态已发生变化。",
                )
            _require_claim(
                envelope,
                claim_id=claim_id,
                now=current,
                require_live=True,
            )
            next_envelope = replace(
                envelope,
                status="ready",
                revision=envelope.revision + 1,
                updated_at=current,
                message_history=normalized_messages,
                resume_result=resume_result,
            )
            self._write_unlocked(next_envelope)
            return next_envelope

    def mark_completed(
        self,
        state_id: str,
        *,
        claim_id: str,
        now: datetime | None = None,
    ) -> AiAgentStateEnvelope:
        """业务终检消费 ready 结果后写入 completed 终态。"""

        return self._finish_ready(
            state_id,
            claim_id=claim_id,
            status="completed",
            now=now,
        )

    def replace_pending_after_resume(
        self,
        state_id: str,
        *,
        claim_id: str,
        message_history: Sequence[ModelMessage],
        deferred_requests: DeferredToolRequests,
        now: datetime | None = None,
    ) -> AiAgentStateEnvelope:
        """恢复后再次 deferred 时原子更新同一 envelope，并等待下一次审批。"""

        safe_state_id = _safe_state_id(state_id)
        current = now or datetime.now(timezone.utc)
        _require_aware_datetime(current)
        try:
            normalized_messages = tuple(
                ModelMessagesTypeAdapter.validate_python(
                    ModelMessagesTypeAdapter.dump_python(
                        list(message_history), mode="json"
                    )
                )
            )
            normalized_deferred = _DEFERRED_REQUESTS_ADAPTER.validate_python(
                _DEFERRED_REQUESTS_ADAPTER.dump_python(
                    deferred_requests, mode="json"
                )
            )
        except Exception:
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            ) from None
        if not normalized_deferred.calls and not normalized_deferred.approvals:
            raise AiAgentStateError(
                "AI_AGENT_STATE_INVALID",
                "Agent 恢复状态参数无效。",
            )
        with self._locked(safe_state_id):
            envelope = self._load_unlocked(safe_state_id)
            if envelope.status != "resuming":
                raise AiAgentStateError(
                    "AI_AGENT_STATE_STATUS_CONFLICT",
                    "Agent 恢复状态已发生变化。",
                )
            _require_claim(
                envelope,
                claim_id=claim_id,
                now=current,
                require_live=True,
            )
            next_envelope = replace(
                envelope,
                status="pending",
                revision=envelope.revision + 1,
                updated_at=current,
                message_history=normalized_messages,
                deferred_requests=normalized_deferred,
                resume_claim=None,
                resume_result=None,
            )
            self._write_unlocked(next_envelope)
            return next_envelope

    def release_claim_for_retry(
        self,
        state_id: str,
        *,
        claim_id: str,
        now: datetime | None = None,
    ) -> AiAgentStateEnvelope:
        """释放尚未开始工具的 claim；有执行风险时永久转为 in_doubt。"""

        safe_state_id = _safe_state_id(state_id)
        current = now or datetime.now(timezone.utc)
        _require_aware_datetime(current)
        with self._locked(safe_state_id):
            envelope = self._load_unlocked(safe_state_id)
            if envelope.status != "resuming":
                raise AiAgentStateError(
                    "AI_AGENT_STATE_STATUS_CONFLICT",
                    "Agent 恢复状态已发生变化。",
                )
            claim = _require_claim(
                envelope,
                claim_id=claim_id,
                now=current,
                require_live=False,
            )
            unsafe = claim.tool_execution_started_at is not None
            next_status: AiAgentStateStatus = (
                "in_doubt"
                if unsafe
                else "failed"
                if current >= envelope.deadline_at
                else "pending"
            )
            next_envelope = replace(
                envelope,
                status=next_status,
                revision=envelope.revision + 1,
                updated_at=current,
                resume_claim=claim if next_status != "pending" else None,
                resume_result=None,
                approval_records=(
                    envelope.approval_records
                    if next_status != "pending"
                    else _without_current_approval_records(envelope)
                ),
            )
            self._write_unlocked(next_envelope)
            return next_envelope

    def recover_expired_claim(
        self,
        state_id: str,
        *,
        now: datetime | None = None,
    ) -> AiAgentStateEnvelope:
        """回收过期 lease；未知或已开始的工具执行绝不自动重跑。"""

        safe_state_id = _safe_state_id(state_id)
        current = now or datetime.now(timezone.utc)
        _require_aware_datetime(current)
        with self._locked(safe_state_id):
            envelope = self._load_unlocked(safe_state_id)
            if envelope.status != "resuming":
                raise AiAgentStateError(
                    "AI_AGENT_STATE_STATUS_CONFLICT",
                    "Agent 恢复状态已发生变化。",
                )
            claim = envelope.resume_claim
            if claim is not None and current < claim.lease_expires_at:
                raise AiAgentStateError(
                    "AI_AGENT_STATE_LEASE_ACTIVE",
                    "Agent 恢复 claim 的 lease 仍然有效。",
                )
            unsafe = claim is None or claim.tool_execution_started_at is not None
            next_status: AiAgentStateStatus = (
                "in_doubt"
                if unsafe
                else "failed"
                if current >= envelope.deadline_at
                else "pending"
            )
            next_envelope = replace(
                envelope,
                status=next_status,
                revision=envelope.revision + 1,
                updated_at=current,
                resume_claim=claim if next_status != "pending" else None,
                resume_result=None,
                approval_records=(
                    envelope.approval_records
                    if next_status != "pending"
                    else _without_current_approval_records(envelope)
                ),
            )
            self._write_unlocked(next_envelope)
            return next_envelope

    def mark_denied(
        self,
        state_id: str,
        *,
        use_case_id: str,
        profile_version: str,
        toolset_id: str,
        tenant_id: str,
        business_scope: Mapping[str, str],
        idempotency_context: Mapping[str, str],
        permissions: Iterable[str],
        approval_records: Sequence[AiAgentApprovalRecord],
        now: datetime | None = None,
    ) -> AiAgentStateEnvelope:
        safe_state_id = _safe_state_id(state_id)
        current = now or datetime.now(timezone.utc)
        _require_aware_datetime(current)
        with self._locked(safe_state_id):
            envelope = self._load_unlocked(safe_state_id)
            validate_resume_context(
                envelope,
                use_case_id=use_case_id,
                profile_version=profile_version,
                toolset_id=toolset_id,
                tenant_id=tenant_id,
                business_scope=business_scope,
                idempotency_context=idempotency_context,
                permissions=permissions,
                now=current,
            )
            if envelope.status != "pending":
                raise AiAgentStateError(
                    "AI_AGENT_STATE_ALREADY_CLAIMED",
                    "Agent 恢复状态已被领取或已经结束。",
                )
            _validate_denial_records(envelope, approval_records)
            next_envelope = replace(
                envelope,
                status="denied",
                revision=envelope.revision + 1,
                updated_at=current,
                approval_records=_merge_approval_records(
                    envelope.approval_records,
                    approval_records,
                ),
            )
            self._write_unlocked(next_envelope)
            return next_envelope

    def _finish_ready(
        self,
        state_id: str,
        *,
        claim_id: str,
        status: Literal["completed", "failed"],
        now: datetime | None = None,
    ) -> AiAgentStateEnvelope:
        safe_state_id = _safe_state_id(state_id)
        current = now or datetime.now(timezone.utc)
        _require_aware_datetime(current)
        with self._locked(safe_state_id):
            envelope = self._load_unlocked(safe_state_id)
            if envelope.status != "ready":
                raise AiAgentStateError(
                    "AI_AGENT_STATE_STATUS_CONFLICT",
                    "Agent 恢复状态已发生变化。",
                )
            _require_claim(
                envelope,
                claim_id=claim_id,
                now=current,
                require_live=False,
            )
            next_envelope = replace(
                envelope,
                status=status,
                revision=envelope.revision + 1,
                updated_at=current,
            )
            self._write_unlocked(next_envelope)
            return next_envelope

    def mark_failed(
        self,
        state_id: str,
        *,
        claim_id: str = "",
        now: datetime | None = None,
    ) -> AiAgentStateEnvelope:
        """终结 pending/resuming/ready；不确定副作用自动收敛为 in_doubt。"""

        safe_state_id = _safe_state_id(state_id)
        current = now or datetime.now(timezone.utc)
        _require_aware_datetime(current)
        with self._locked(safe_state_id):
            envelope = self._load_unlocked(safe_state_id)
            next_status: AiAgentStateStatus
            if envelope.status == "pending":
                if claim_id:
                    raise AiAgentStateError(
                        "AI_AGENT_STATE_CLAIM_MISMATCH",
                        "Agent 恢复 claim 与当前状态不一致。",
                    )
                next_status = "failed"
            elif envelope.status == "ready":
                _require_claim(
                    envelope,
                    claim_id=claim_id,
                    now=current,
                    require_live=False,
                )
                next_status = "failed"
            elif envelope.status == "resuming":
                claim = envelope.resume_claim
                if claim is None:
                    if claim_id:
                        raise AiAgentStateError(
                            "AI_AGENT_STATE_CLAIM_MISMATCH",
                            "Agent 恢复 claim 与当前状态不一致。",
                        )
                    next_status = "in_doubt"
                else:
                    _require_claim(
                        envelope,
                        claim_id=claim_id,
                        now=current,
                        require_live=False,
                    )
                    next_status = (
                        "in_doubt"
                        if claim.tool_execution_started_at is not None
                        else "failed"
                    )
            else:
                raise AiAgentStateError(
                    "AI_AGENT_STATE_STATUS_CONFLICT",
                    "Agent 恢复状态已发生变化。",
                )
            next_envelope = replace(
                envelope,
                status=next_status,
                revision=envelope.revision + 1,
                updated_at=current,
            )
            self._write_unlocked(next_envelope)
            return next_envelope


def _normalize_messages(
    message_history: Sequence[ModelMessage],
) -> tuple[ModelMessage, ...]:
    try:
        return tuple(
            ModelMessagesTypeAdapter.validate_python(
                ModelMessagesTypeAdapter.dump_python(
                    list(message_history),
                    mode="json",
                )
            )
        )
    except Exception:
        raise AiAgentStateError(
            "AI_AGENT_STATE_INVALID",
            "Agent 恢复状态参数无效。",
        ) from None


def _require_claim(
    envelope: AiAgentStateEnvelope,
    *,
    claim_id: str,
    now: datetime,
    require_live: bool,
) -> AiAgentResumeClaim:
    claim = envelope.resume_claim
    try:
        safe_claim_id = _safe_claim_id(claim_id)
    except AiAgentStateError:
        safe_claim_id = ""
    if claim is None or claim.claim_id != safe_claim_id:
        raise AiAgentStateError(
            "AI_AGENT_STATE_CLAIM_MISMATCH",
            "Agent 恢复 claim 与当前状态不一致。",
        )
    if require_live and now >= claim.lease_expires_at:
        raise AiAgentStateError(
            "AI_AGENT_STATE_LEASE_EXPIRED",
            "Agent 恢复 claim 的 lease 已过期。",
        )
    return claim


def _deferred_approval_ids(envelope: AiAgentStateEnvelope) -> frozenset[str]:
    call_ids = [
        _required_text(part.tool_call_id)
        for part in envelope.deferred_requests.approvals
    ]
    if len(call_ids) != len(set(call_ids)):
        raise AiAgentStateError(
            "AI_AGENT_STATE_APPROVAL_CONFLICT",
            "Agent 工具审批状态已发生变化。",
        )
    return frozenset(call_ids)


def _without_current_approval_records(
    envelope: AiAgentStateEnvelope,
) -> tuple[AiAgentApprovalRecord, ...]:
    current_ids = _deferred_approval_ids(envelope)
    return tuple(
        record
        for record in envelope.approval_records
        if record.tool_call_id not in current_ids
    )


def _validate_resume_approvals(
    envelope: AiAgentStateEnvelope,
    records: Sequence[AiAgentApprovalRecord],
) -> None:
    expected_ids = _deferred_approval_ids(envelope)
    provided_ids = frozenset(record.tool_call_id for record in records)
    if provided_ids != expected_ids or any(
        record.decision != "approved" for record in records
    ):
        raise AiAgentStateError(
            "AI_AGENT_STATE_APPROVAL_REQUIRED",
            "Agent 恢复所需的工具审批尚未全部通过。",
        )


def _validate_denial_records(
    envelope: AiAgentStateEnvelope,
    records: Sequence[AiAgentApprovalRecord],
) -> None:
    expected_ids = _deferred_approval_ids(envelope)
    provided_ids = frozenset(record.tool_call_id for record in records)
    if (
        not records
        or provided_ids != expected_ids
        or not any(record.decision == "denied" for record in records)
    ):
        raise AiAgentStateError(
            "AI_AGENT_STATE_APPROVAL_INVALID",
            "Agent 工具审批决定无效。",
        )


def _merge_approval_records(
    current: Sequence[AiAgentApprovalRecord],
    incoming: Sequence[AiAgentApprovalRecord],
) -> tuple[AiAgentApprovalRecord, ...]:
    merged = tuple(current) + tuple(incoming)
    call_ids = [record.tool_call_id for record in merged]
    if len(call_ids) != len(set(call_ids)):
        raise AiAgentStateError(
            "AI_AGENT_STATE_APPROVAL_CONFLICT",
            "Agent 工具审批状态已发生变化。",
        )
    return merged


__all__ = [
    "AI_AGENT_STATE_RELATIVE_DIR",
    "AI_AGENT_STATE_SCHEMA_VERSION",
    "DEFAULT_RESUME_LEASE_SECONDS",
    "MAX_RESUME_LEASE_SECONDS",
    "AiAgentApprovalDecision",
    "AiAgentApprovalRecord",
    "AiAgentResumeClaim",
    "AiAgentResumeResult",
    "AiAgentStateEnvelope",
    "AiAgentStateError",
    "AiAgentStateSecurity",
    "AiAgentStateStatus",
    "AiAgentStateStore",
    "validate_ready_context",
    "validate_resume_context",
]
