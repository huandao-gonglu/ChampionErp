"""GlobalTask 执行进度投影服务：计算型只读视图。

从当前 ``LocalGlobalTaskState`` 与领域 Job 已持久化状态即时投影出
``GlobalTaskExecutionProgress``：

- 不推进任务、不更新数据库、不调用模型、不直接依赖平台 API；
- 领域专用状态解析在按 ``job_type`` 注册的 ``JobStatusReader`` 内完成，
  这里只做领域无关的通用投影与安全降级；
- 进度读取失败只影响本视图，HTTP 仍返回任务主体。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Mapping

from erp_web.schemas.global_tasks import (
    TERMINAL_GLOBAL_TASK_STATUSES,
    GlobalTaskActiveJobProgress,
    GlobalTaskCurrentStepProgress,
    GlobalTaskExecutionProgress,
    GlobalTaskProgressActivity,
    GlobalTaskProgressStatus,
    GlobalTaskViewResponse,
    JobStateSnapshot,
    LocalGlobalTaskState,
    TaskActiveJob,
)
from erp_web.services.global_task_controller import JobStatusReader


logger = logging.getLogger(__name__)

#: Reader 未注册时的降级文案：不猜测业务含义。
JOB_PROGRESS_UNAVAILABLE_SUMMARY = "后台任务正在执行，暂无详细进度。"
#: Job 记录缺失或读取异常时的降级文案；任务生命周期不受影响。
JOB_PROGRESS_MISSING_SUMMARY = "暂时无法读取后台任务进度。"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime) -> datetime:
    """无时区时间按 UTC 处理；保证耗时比较安全。"""

    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def elapsed_whole_seconds(start: datetime, end: datetime) -> int:
    """向下取整的非负整数秒；负时间差收敛为 0。"""

    return max(0, int((ensure_aware(end) - ensure_aware(start)).total_seconds()))


def task_elapsed_seconds(task: LocalGlobalTaskState, observed_at: datetime) -> int:
    """任务总耗时：活跃任务用观察时间，终态任务冻结在 ``updated_at``。"""

    end = (
        task.updated_at
        if task.status in TERMINAL_GLOBAL_TASK_STATUSES
        else observed_at
    )
    return elapsed_whole_seconds(task.created_at, end)


def progress_status_from_snapshot(
    snapshot: JobStateSnapshot,
) -> GlobalTaskProgressStatus:
    """生命周期状态 → 展示状态；running 且有下次检查时间视为正常等待。"""

    status = str(snapshot.status or "").strip().lower()
    if status in {"queued", "pending"}:
        return "queued"
    if status == "retrying":
        return "retrying"
    if status == "success":
        return "completed"
    if status == "failed":
        return "failed"
    if snapshot.next_check_at is not None:
        return "waiting"
    return "running"


class GlobalTaskProgressProjector:
    """把任务与领域 Job 状态投影为进度视图；纯读、可安全降级。"""

    def __init__(
        self,
        *,
        job_status_readers: Mapping[str, JobStatusReader],
        capability_label_loader: Callable[[str], str] | None = None,
    ) -> None:
        self._readers: Mapping[str, JobStatusReader] = {
            str(job_type or "").strip(): reader
            for job_type, reader in dict(job_status_readers).items()
        }
        self._capability_label_loader = capability_label_loader

    def project(self, task: LocalGlobalTaskState) -> GlobalTaskExecutionProgress:
        observed_at = _now()
        job = task.active_job
        active_job, activities = self._project_active_job(job, observed_at)
        return GlobalTaskExecutionProgress(
            observed_at=observed_at,
            task_elapsed_seconds=task_elapsed_seconds(task, observed_at),
            current_step=self._project_current_step(task),
            active_job=active_job,
            activities=activities,
        )

    def build_view_response(
        self,
        task: LocalGlobalTaskState,
    ) -> GlobalTaskViewResponse:
        """任务主体 + 进度视图；进度投影失败不影响任务主体返回。"""

        try:
            progress: GlobalTaskExecutionProgress | None = self.project(task)
        except Exception:
            logger.exception("全局任务进度投影失败：%s", task.task_id)
            progress = None
        return GlobalTaskViewResponse(
            task=task,
            task_id=task.task_id,
            execution_progress=progress,
        )

    # -- 内部投影 -----------------------------------------------------------

    def _capability_label(self, capability_name: str) -> str:
        if self._capability_label_loader is None:
            return capability_name
        try:
            label = str(self._capability_label_loader(capability_name) or "").strip()
        except Exception:
            logger.exception("读取 Capability 展示名称失败：%s", capability_name)
            return capability_name
        return label or capability_name

    def _project_current_step(
        self,
        task: LocalGlobalTaskState,
    ) -> GlobalTaskCurrentStepProgress | None:
        if not task.steps:
            return None
        index = task.current_step_index
        if index >= len(task.steps):
            return None
        step = task.steps[index]
        return GlobalTaskCurrentStepProgress(
            index=index,
            ordinal=index + 1,
            total=len(task.steps),
            capability_name=step.capability_name,
            label=self._capability_label(step.capability_name),
            status=step.status,
        )

    def _degraded_job_progress(
        self,
        job: TaskActiveJob,
        observed_at: datetime,
        *,
        summary: str,
    ) -> GlobalTaskActiveJobProgress:
        return GlobalTaskActiveJobProgress(
            job_id=job.job_id,
            job_type=job.job_type,
            status="running",
            summary=summary,
            started_at=job.started_at,
            elapsed_seconds=elapsed_whole_seconds(job.started_at, observed_at),
        )

    def _project_active_job(
        self,
        job: TaskActiveJob | None,
        observed_at: datetime,
    ) -> tuple[
        GlobalTaskActiveJobProgress | None,
        list[GlobalTaskProgressActivity],
    ]:
        if job is None:
            return None, []
        reader = self._readers.get(job.job_type)
        if reader is None:
            return (
                self._degraded_job_progress(
                    job,
                    observed_at,
                    summary=JOB_PROGRESS_UNAVAILABLE_SUMMARY,
                ),
                [],
            )
        try:
            snapshot = reader.read_job_state(job.job_id)
        except Exception:
            logger.warning(
                "读取长任务 %s 进度失败，任务卡降级展示。",
                job.job_id,
                exc_info=True,
            )
            return (
                self._degraded_job_progress(
                    job,
                    observed_at,
                    summary=JOB_PROGRESS_MISSING_SUMMARY,
                ),
                [],
            )
        if not snapshot.available:
            return (
                self._degraded_job_progress(
                    job,
                    observed_at,
                    summary=JOB_PROGRESS_MISSING_SUMMARY,
                ),
                [],
            )
        elapsed = elapsed_whole_seconds(job.started_at, observed_at)
        phase_started_at = snapshot.phase_started_at
        # 阶段耗时优先使用 Reader 提供的 phase_started_at；缺失时回落 Job 耗时。
        phase_elapsed = (
            elapsed_whole_seconds(phase_started_at, observed_at)
            if phase_started_at is not None
            else elapsed
        )
        active_job = GlobalTaskActiveJobProgress(
            job_id=job.job_id,
            job_type=job.job_type,
            status=progress_status_from_snapshot(snapshot),
            stage_code=snapshot.stage_code,
            stage_label=snapshot.stage_label,
            summary=snapshot.summary,
            started_at=job.started_at,
            updated_at=snapshot.updated_at,
            elapsed_seconds=elapsed,
            phase_started_at=phase_started_at,
            phase_elapsed_seconds=phase_elapsed,
            attempt=snapshot.attempt,
            retry_count=snapshot.retry_count,
            next_check_at=snapshot.next_check_at,
            last_external_status=snapshot.last_external_status,
        )
        activities = [
            GlobalTaskProgressActivity(
                code=activity.code,
                label=activity.label or activity.code,
                status=activity.status,
                completed_at=activity.completed_at,
            )
            for activity in snapshot.activities
        ]
        return active_job, activities


__all__ = [
    "GlobalTaskProgressProjector",
    "JOB_PROGRESS_MISSING_SUMMARY",
    "JOB_PROGRESS_UNAVAILABLE_SUMMARY",
    "elapsed_whole_seconds",
    "ensure_aware",
    "progress_status_from_snapshot",
    "task_elapsed_seconds",
]
