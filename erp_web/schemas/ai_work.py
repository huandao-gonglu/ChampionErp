"""AI Work API 数据形状。"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


AiWorkEventType = Literal[
    "RUN_STARTED",
    "RUN_FINISHED",
    "RUN_ERROR",
    "RUN_DEFERRED",
    "RUN_RESUMED",
    "STEP_STARTED",
    "STEP_FINISHED",
    "REASONING_MESSAGE_START",
    "REASONING_MESSAGE_CONTENT",
    "REASONING_MESSAGE_END",
    "TEXT_MESSAGE_START",
    "TEXT_MESSAGE_CONTENT",
    "TEXT_MESSAGE_END",
    "CUSTOM",
    "RAW",
]


class AiWorkEvent(TypedDict, total=False):
    schema_version: int
    seq: int
    timestamp: int
    occurred_at: str
    type: AiWorkEventType
    threadId: str
    runId: str
    conversation_id: str
    messageId: str
    role: str
    delta: str
    name: str
    value: Any
    message: str
    code: str
    result: Any
    rawEvent: Any
    task_run_id: str
    attempt_id: str
    workflow_run_id: str | None
    parent_task_run_id: str | None
    model_call_id: str
    tool_call_id: str


class AiWorkConversationSummary(TypedDict, total=False):
    conversation_id: str
    use_case_id: str
    capability: str
    provider_id: str
    provider: str
    model_id: str
    model: str
    stream: bool
    required_capabilities: list[str]
    timeout_seconds: int | None
    status: str
    created_at: str
    updated_at: str
    last_seq: int
    event_count: int
    error: str


__all__ = ["AiWorkConversationSummary", "AiWorkEvent", "AiWorkEventType"]
