"""Pydantic Agent 的请求级 ERP dependencies。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from erp_web.schemas.ai_trace import AiExecutionContext

from .ai_tool_runtime import AiToolRuntime


@dataclass(frozen=True, repr=False)
class AiAgentDependencies:
    """把一次 Agent run 绑定到唯一安全 Runtime 和业务上下文。"""

    use_case_id: str
    execution_context: AiExecutionContext
    tool_runtime: AiToolRuntime
    use_case_state: Any = None
    invocation_id: str = ""

    def __post_init__(self) -> None:
        use_case_id = str(self.use_case_id or "").strip()
        if not use_case_id:
            raise ValueError("use_case_id 不能为空")
        if self.tool_runtime.execution_context is not self.execution_context:
            raise ValueError("Agent dependencies 与 Tool Runtime 的 execution context 不一致")
        invocation_id = str(self.invocation_id or self.execution_context.attempt_id).strip()
        if not invocation_id:
            raise ValueError("invocation_id 不能为空")
        object.__setattr__(self, "use_case_id", use_case_id)
        object.__setattr__(self, "invocation_id", invocation_id)

    @property
    def user_id(self) -> str:
        return self.execution_context.actor_id

    @property
    def tenant_id(self) -> str:
        return self.execution_context.tenant_id

    @property
    def permissions(self) -> frozenset[str]:
        return self.execution_context.permissions

    @property
    def business_scope(self) -> Mapping[str, str]:
        return self.execution_context.business_scope

    @property
    def deadline_at(self) -> datetime:
        return self.execution_context.deadline_at

    @property
    def approved_tool_call_ids(self) -> frozenset[str]:
        return self.execution_context.approved_tool_call_ids

    @property
    def idempotency_context(self) -> Mapping[str, str]:
        return self.execution_context.idempotency_context

    def __repr__(self) -> str:
        return (
            "AiAgentDependencies("
            f"use_case_id={self.use_case_id!r}, "
            f"invocation_id={self.invocation_id!r}, "
            f"user_id={self.user_id!r}, "
            f"tenant_id={self.tenant_id!r})"
        )


__all__ = ["AiAgentDependencies"]
