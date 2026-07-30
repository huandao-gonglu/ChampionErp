"""AI 任务执行标识与受限执行上下文。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict
from uuid import uuid4


class AiTraceIdentifiers(TypedDict, total=False):
    task_run_id: str
    attempt_id: str
    workflow_run_id: str | None
    parent_task_run_id: str | None


@dataclass(frozen=True)
class AiExecutionContext:
    """一次 AI Task attempt 的不可变执行边界。

    权限与审批信息只供确定性 Runtime 使用，不应序列化进模型消息。
    """

    task_run_id: str
    attempt_id: str
    deadline_at: datetime
    budget_profile: str
    actor_id: str = "local-user"
    permissions: frozenset[str] = frozenset()
    workflow_run_id: str | None = None
    parent_task_run_id: str | None = None
    approved_tool_call_ids: frozenset[str] = frozenset()
    allow_write: bool = False

    def __post_init__(self) -> None:
        if not self.task_run_id:
            raise ValueError("task_run_id 不能为空")
        if not self.attempt_id:
            raise ValueError("attempt_id 不能为空")
        if self.deadline_at.tzinfo is None:
            raise ValueError("deadline_at 必须包含时区")
        if not self.budget_profile:
            raise ValueError("budget_profile 不能为空")
        object.__setattr__(self, "permissions", frozenset(self.permissions))
        object.__setattr__(
            self,
            "approved_tool_call_ids",
            frozenset(self.approved_tool_call_ids),
        )

    @classmethod
    def create(
        cls,
        *,
        timeout_seconds: float,
        budget_profile: str,
        task_run_id: str = "",
        attempt_id: str = "",
        workflow_run_id: str | None = None,
        parent_task_run_id: str | None = None,
        actor_id: str = "local-user",
        permissions: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
        approved_tool_call_ids: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
        allow_write: bool = False,
        now: datetime | None = None,
    ) -> "AiExecutionContext":
        safe_timeout = float(timeout_seconds)
        if safe_timeout <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        started_at = now or datetime.now(timezone.utc)
        if started_at.tzinfo is None:
            raise ValueError("now 必须包含时区")
        return cls(
            task_run_id=task_run_id or f"task_{uuid4().hex}",
            attempt_id=attempt_id or f"attempt_{uuid4().hex}",
            workflow_run_id=workflow_run_id or None,
            parent_task_run_id=parent_task_run_id or None,
            actor_id=str(actor_id or "local-user"),
            permissions=frozenset(permissions),
            deadline_at=started_at + timedelta(seconds=safe_timeout),
            budget_profile=budget_profile,
            approved_tool_call_ids=frozenset(approved_tool_call_ids),
            allow_write=bool(allow_write),
        )

    def remaining_seconds(self, *, now: datetime | None = None) -> float:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("now 必须包含时区")
        return max(0.0, (self.deadline_at - current).total_seconds())

    def expired(self, *, now: datetime | None = None) -> bool:
        return self.remaining_seconds(now=now) <= 0

    def bounded_timeout_seconds(
        self,
        maximum_seconds: float | None = None,
        *,
        now: datetime | None = None,
    ) -> float:
        """返回受总 deadline 限制的 I/O timeout。

        同步 Python Runtime 无法安全杀死阻塞线程；executor 必须把这个值传给
        每一个网络、浏览器或外部进程调用，才能满足 cooperative deadline。
        """

        remaining = self.remaining_seconds(now=now)
        if remaining <= 0:
            raise TimeoutError("AI Task 总 deadline 已耗尽")
        if maximum_seconds is None:
            return remaining
        safe_maximum = float(maximum_seconds)
        if safe_maximum <= 0:
            raise ValueError("maximum_seconds 必须大于 0")
        return min(remaining, safe_maximum)

    def trace_payload(self) -> dict[str, Any]:
        return {
            "task_run_id": self.task_run_id,
            "attempt_id": self.attempt_id,
            "workflow_run_id": self.workflow_run_id,
            "parent_task_run_id": self.parent_task_run_id,
            "actor_id": self.actor_id,
            "deadline_at": self.deadline_at.isoformat(),
            "budget_profile": self.budget_profile,
        }


__all__ = ["AiExecutionContext", "AiTraceIdentifiers"]
