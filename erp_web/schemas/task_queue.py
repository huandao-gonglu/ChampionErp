"""发布、在线修改共用的任务视图与有限确认策略。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, TypedDict


@dataclass(frozen=True)
class ConfirmationPolicy:
    first_delay: int
    interval: int
    max_checks: int
    lifetime: int
    max_requests: int
    round_timeout: int = 120

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def confirmation_policy(source: str, platform: str, operation: str, targets: int = 1) -> ConfirmationPolicy:
    # 平台没有规定统一轮询周期；这些是 ERP 的有限查询预算，不是平台 SLA。
    if platform == "yandex" and (source == "publish" or operation == "content"):
        return ConfirmationPolicy(60, 1800, 24, 86400, max(120, targets * 120))
    if platform == "yandex" and operation == "price":
        return ConfirmationPolicy(30, 60, 10, 900, 40)
    return ConfirmationPolicy(30, 60, 10, 900, max(80, targets * 40))


class TaskConfirmationSummary(TypedDict):
    state: str
    checks_used: int
    checks_remaining: int
    requests_used: int
    requests_remaining: int
    last_checked_at: float
    next_check_at: float
    deadline_at: float
    error: str


class QueueTask(TypedDict):
    id: str
    job_id: str
    source: Literal["publish", "online"]
    origin: str
    platform: str
    account_id: str
    operation: str
    title: str
    target_id: str
    status: str
    business_status: str
    created_at: str
    updated_at: str
    error: str
    protection: str
    confirmation: TaskConfirmationSummary | None
    can_retry: bool


class TaskQueueResponse(TypedDict):
    ok: bool
    items: list[QueueTask]
    total: int
    page: int
    accounts: list[str]


class TaskQueueDetail(TypedDict):
    ok: bool
    task: QueueTask
    detail: dict[str, Any]


__all__ = ["ConfirmationPolicy", "confirmation_policy", "QueueTask", "TaskConfirmationSummary", "TaskQueueResponse", "TaskQueueDetail"]
