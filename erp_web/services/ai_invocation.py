"""一次 AI 调用的解析结果、deadline 与 AI Work recorder 边界。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from erp_web.schemas.ai_trace import AiExecutionContext

from .ai_provider_contracts import AiProvider
from .ai_work_service import AiWorkConversation


class AiWorkRecorder(Protocol):
    """Task Runner 与 Provider Adapter 共用的记录器接口。"""

    @property
    def conversation_id(self) -> str:
        ...

    def record(self, event_type: str, **payload: Any) -> None:
        ...

    def emit(self, event_type: str, **payload: Any) -> Any:
        ...

    def emit_custom(self, name: str, value: Any) -> Any:
        ...

    def emit_text_delta(self, delta: str) -> None:
        ...

    def finish_assistant_message(self, raw_text: str = "") -> None:
        ...

    def finish(self, result: Any) -> None:
        ...

    def fail(self, error: Exception) -> None:
        ...


@dataclass(frozen=True)
class ConversationAiWorkRecorder:
    """把现有 AI Work conversation 适配成单次 invocation recorder。"""

    conversation: AiWorkConversation
    execution_context: AiExecutionContext

    @property
    def conversation_id(self) -> str:
        return self.conversation.conversation_id

    def record(self, event_type: str, **payload: Any) -> None:
        # PR 1 只建立 recorder seam；新事件 union 留到 AI Work 扩展阶段。
        self.emit_custom(event_type, payload)

    def emit(self, event_type: str, **payload: Any) -> Any:
        return self.conversation.emit(event_type, **payload)

    def emit_custom(self, name: str, value: Any) -> Any:
        return self.emit("CUSTOM", name=name, value=value)

    def emit_text_delta(self, delta: str) -> None:
        self.conversation.emit_text_delta(delta)

    def finish_assistant_message(self, raw_text: str = "") -> None:
        self.conversation.finish_assistant_message(raw_text)

    def finish(self, result: Any) -> None:
        self.emit("RUN_FINISHED", result=result)

    def fail(self, error: Exception) -> None:
        self.emit(
            "RUN_ERROR",
            message=str(error),
            code=getattr(error, "code", error.__class__.__name__),
        )


@dataclass(frozen=True)
class AiInvocation:
    """一次已解析 provider/model 且只绑定一个 recorder 的 AI 调用。"""

    use_case_id: str
    capability: str
    provider: AiProvider
    model: dict[str, Any]
    required_capabilities: tuple[str, ...]
    timeout_seconds: int
    execution_context: AiExecutionContext
    recorder: AiWorkRecorder

    def __post_init__(self) -> None:
        if not self.use_case_id:
            raise ValueError("AiInvocation.use_case_id 不能为空")
        if not self.capability:
            raise ValueError("AiInvocation.capability 不能为空")
        if self.timeout_seconds <= 0:
            raise ValueError("AiInvocation.timeout_seconds 必须大于 0")
        object.__setattr__(self, "model", dict(self.model))
        object.__setattr__(
            self,
            "required_capabilities",
            tuple(self.required_capabilities),
        )


__all__ = [
    "AiInvocation",
    "AiWorkRecorder",
    "ConversationAiWorkRecorder",
]
