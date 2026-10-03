"""选品研究 Job 的有界只读状态；不选择或推进 Agent 工具。"""

from __future__ import annotations
from datetime import datetime
from typing import Any, Callable, Mapping, cast
from erp_web.schemas.domain_jobs import (
    JobLifecycleStatus,
    JobStateActivity,
    JobStateSnapshot,
)
from erp_web.services import product_research_service

def _truncate(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit]


def _parse_datetime(value: Any) -> datetime | None:
    """把 ISO / 本地时间字符串解析为带时区 datetime；失败返回 None。"""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        # 无时区的持久化时间按本地时区解释。
        parsed = parsed.astimezone()
    return parsed


def _research_activities(run: Mapping[str, Any]) -> tuple[JobStateActivity, ...]:
    source_status = (
        run.get("source_status") if isinstance(run.get("source_status"), list) else []
    )
    activities: list[JobStateActivity] = []
    for item in source_status:
        if not isinstance(item, dict):
            continue
        code = str(item.get("source_id") or item.get("source") or "").strip()
        if not code:
            continue
        raw_status = str(item.get("status") or "").strip().lower()
        if raw_status in {"failed", "error"}:
            activity_status = "failed"
        else:
            activity_status = "completed"
        activities.append(
            JobStateActivity(
                code=_truncate(code, 80),
                label=_truncate(str(item.get("source") or code), 200),
                status=activity_status,
            )
        )
    return tuple(activities[:50])


class ResearchJobStatusReader:
    """把热门选品研究运行状态收敛为通用 job_id → 类型化快照。

    运行记录持久化在 SQLite（ProductResearchRunRegistry），进程重启后仍可
    读取终态；后台工具对账器只依赖这里注册的通用读取器。
    """

    def __init__(
        self,
        run_loader: Callable[[str], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self._run_loader = run_loader or product_research_service.get_hot_product_run

    def read_job_state(self, job_id: str) -> JobStateSnapshot:
        run = self._run_loader(str(job_id or "").strip())
        if run is None:
            return JobStateSnapshot(
                status="failed",
                error="选品研究运行不存在或已被清理。",
            )
        status = str(run.get("status") or "").strip().lower()
        error = ""
        if status == "completed":
            lifecycle: str = "success"
        elif status == "failed":
            lifecycle = "failed"
            error = str(run.get("error") or run.get("description") or "").strip()
        else:
            lifecycle = "running"

        summary = str(
            run.get("progress_description") or run.get("description") or ""
        ).strip()
        return JobStateSnapshot(
            status=cast(JobLifecycleStatus, lifecycle),
            error=_truncate(error, 2000),
            available=True,
            summary=_truncate(summary, 500),
            updated_at=_parse_datetime(
                run.get("completed_at") or run.get("created_at")
            ),
            activities=_research_activities(run),
        )
