"""发布、在线修改共用的任务视图与内置有限确认策略。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Literal, TypedDict


YANDEX_CONTENT_INTERVALS = (30, 60, 120, 300, 600, 1800)
FAST_CONFIRMATION_INTERVALS = (15, 30, 45, 60, 90, 120, 150, 180)
YANDEX_CONFIRM_BATCH_SIZE = 100


@dataclass(frozen=True)
class ConfirmationPolicy:
    first_delay: int
    interval: int
    max_checks: int
    lifetime: int
    max_requests: int
    round_timeout: int = 120
    cooldown: int = 30
    intervals: tuple[int, ...] = ()
    round_max_requests: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ConfirmationPolicy":
        interval = max(1, int(raw.get("interval") or 60))
        raw_intervals = raw.get("intervals")
        intervals = (
            tuple(int(value) for value in raw_intervals if int(value) > 0)
            if isinstance(raw_intervals, (list, tuple)) and raw_intervals
            else (interval,)
        )
        max_requests = max(1, int(raw["max_requests"]))
        round_max = int(raw.get("round_max_requests") or 0) or max_requests
        return cls(
            first_delay=max(0, int(raw["first_delay"])),
            interval=interval,
            max_checks=max(1, int(raw["max_checks"])),
            lifetime=max(1, int(raw["lifetime"])),
            max_requests=max_requests,
            round_timeout=max(1, int(raw.get("round_timeout") or 120)),
            cooldown=max(1, int(raw.get("cooldown") or 30)),
            intervals=intervals,
            round_max_requests=max(1, round_max),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["intervals"] = list(self.intervals or (self.interval,))
        data["round_max_requests"] = self.manual_round_requests()
        return data

    def next_interval(self, checks_used: int) -> int:
        """根据已完成的查询轮数返回渐进式等待间隔（秒）。"""
        schedule = self.intervals or (self.interval,)
        index = max(0, int(checks_used) - 1)
        return schedule[min(index, len(schedule) - 1)]

    def manual_round_requests(self) -> int:
        """自动确认停止后的显式单轮人工核验请求上限。"""
        return min(self.max_requests, self.round_max_requests) if self.round_max_requests > 0 else self.max_requests


def confirmation_policy(source: str, platform: str, operation: str, targets: int = 1) -> ConfirmationPolicy:
    """按平台异步处理特征和真实合批规模生成内置有限确认策略。"""
    count = max(1, int(targets or 1))
    if platform == "yandex" and (source == "publish" or operation == "content"):
        # Yandex 发布与内容确认按 100/200 SKU 合批，每批一轮最多约 4 个只读接口。
        batches = max(1, math.ceil(count / YANDEX_CONFIRM_BATCH_SIZE))
        max_checks = 52
        round_max = max(16, batches * 8)
        return ConfirmationPolicy(
            first_delay=30,
            interval=YANDEX_CONTENT_INTERVALS[-1],
            max_checks=max_checks,
            lifetime=86400,
            max_requests=max(120, batches * 4 * max_checks),
            round_timeout=120,
            cooldown=30,
            intervals=YANDEX_CONTENT_INTERVALS,
            round_max_requests=round_max,
        )
    if platform == "yandex" and operation == "price":
        return ConfirmationPolicy(
            first_delay=15,
            interval=FAST_CONFIRMATION_INTERVALS[-1],
            max_checks=10,
            lifetime=900,
            max_requests=40,
            round_timeout=120,
            cooldown=30,
            intervals=FAST_CONFIRMATION_INTERVALS,
            round_max_requests=12,
        )
    # Ozon / Mercado 异步确认及其他可定位的未知结果：15 分钟内渐进查询 10 轮。
    round_max = max(16, count * 4)
    return ConfirmationPolicy(
        first_delay=15,
        interval=FAST_CONFIRMATION_INTERVALS[-1],
        max_checks=10,
        lifetime=900,
        max_requests=max(80, count * 40),
        round_timeout=120,
        cooldown=30,
        intervals=FAST_CONFIRMATION_INTERVALS,
        round_max_requests=round_max,
    )


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
    can_check: bool
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
