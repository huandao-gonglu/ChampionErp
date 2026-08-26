"""GlobalTask 执行进度投影服务测试（进度计划 §11.1）。

验证计算型只读视图：当前步骤与耗时、领域 Job 快照投影、Reader 缺失/异常/
Job 缺失的安全降级、负时间差收敛，以及响应不透传凭据/原始对象。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from erp_web.schemas.global_tasks import (
    JobStateActivity,
    JobStateSnapshot,
    LocalGlobalTaskState,
    LocalTaskStep,
    TaskActiveJob,
)
from erp_web.services import global_task_progress_service as progress
from erp_web.services.global_task_progress_service import (
    GlobalTaskProgressProjector,
    elapsed_whole_seconds,
    task_elapsed_seconds,
)


CREATED = datetime(2026, 8, 24, 0, 3, 5, tzinfo=timezone.utc)
JOB_STARTED = datetime(2026, 8, 24, 0, 8, 18, tzinfo=timezone.utc)
OBSERVED = datetime(2026, 8, 24, 0, 13, 0, tzinfo=timezone.utc)


def _step(
    index: int,
    *,
    status: str = "pending",
    capability: str = "product_publish_request",
) -> LocalTaskStep:
    return LocalTaskStep(
        step_id=f"step_{index}",
        capability_name=capability,
        capability_version="1",
        operation_key=f"op:{index}",
        status=status,
        result={"ok": True} if status == "completed" else None,
    )


def _in_progress_task(
    *,
    created: datetime = CREATED,
    updated: datetime = CREATED,
    started: datetime = JOB_STARTED,
    job_type: str = "publish",
) -> LocalGlobalTaskState:
    steps = [
        _step(1, status="completed", capability="draft_prepare_for_market"),
        _step(2, status="running"),
        _step(3, status="pending", capability="draft_stock_update"),
    ]
    return LocalGlobalTaskState(
        task_id="gtask_progress",
        goal="发布两个草稿",
        status="in_progress",
        steps=steps,
        current_step_index=1,
        active_job=TaskActiveJob(
            step_id="step_2",
            capability_name="product_publish_request",
            job_id="job-1",
            job_type=job_type,
            started_at=started,
        ),
        created_at=created,
        updated_at=updated,
    )


def _terminal_task(
    *,
    created: datetime = CREATED,
    updated: datetime,
) -> LocalGlobalTaskState:
    steps = [
        _step(1, status="completed", capability="draft_prepare_for_market"),
        _step(2, status="completed"),
    ]
    return LocalGlobalTaskState(
        task_id="gtask_done",
        goal="发布草稿",
        status="completed",
        steps=steps,
        current_step_index=2,
        created_at=created,
        updated_at=updated,
    )


class _FakeReader:
    def __init__(self, snapshot: JobStateSnapshot) -> None:
        self._snapshot = snapshot
        self.calls: list[str] = []

    def read_job_state(self, job_id: str) -> JobStateSnapshot:
        self.calls.append(job_id)
        return self._snapshot


class _RaisingReader:
    def read_job_state(self, job_id: str) -> JobStateSnapshot:
        raise RuntimeError("读取失败")


@pytest.fixture(autouse=True)
def _freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(progress, "_now", lambda: OBSERVED)


def _projector(reader=None) -> GlobalTaskProgressProjector:
    readers = {"publish": reader} if reader is not None else {}
    return GlobalTaskProgressProjector(
        job_status_readers=readers,
        capability_label_loader=lambda name: {
            "product_publish_request": "提交商品发布",
        }.get(name, ""),
    )


def test_elapsed_whole_seconds_floors_and_clamps_negative() -> None:
    start = datetime(2026, 8, 24, 0, 0, 0, tzinfo=timezone.utc)
    assert elapsed_whole_seconds(start, start + timedelta(seconds=2.9)) == 2
    # 负时间差收敛为 0。
    assert elapsed_whole_seconds(start + timedelta(seconds=5), start) == 0


def test_task_elapsed_active_uses_observed_terminal_uses_updated() -> None:
    active = _in_progress_task()
    assert task_elapsed_seconds(active, OBSERVED) == int(
        (OBSERVED - CREATED).total_seconds()
    )
    terminal = _terminal_task(updated=CREATED + timedelta(seconds=295))
    # 终态耗时冻结在 updated_at，与观察时间无关。
    assert task_elapsed_seconds(terminal, OBSERVED) == 295


def test_active_task_projects_current_step_and_job_elapsed() -> None:
    snapshot = JobStateSnapshot(status="running", stage_label="等待平台确认")
    projector = _projector(_FakeReader(snapshot))

    view = projector.project(_in_progress_task())

    assert view.observed_at == OBSERVED
    assert view.task_elapsed_seconds == int((OBSERVED - CREATED).total_seconds())
    step = view.current_step
    assert step is not None
    assert step.ordinal == 2
    assert step.total == 3
    assert step.capability_name == "product_publish_request"
    assert step.label == "提交商品发布"
    assert step.status == "running"

    job = view.active_job
    assert job is not None
    assert job.job_id == "job-1"
    assert job.elapsed_seconds == int((OBSERVED - JOB_STARTED).total_seconds())


def test_snapshot_display_fields_flow_into_active_job() -> None:
    next_check = OBSERVED + timedelta(seconds=12)
    snapshot = JobStateSnapshot(
        status="running",
        stage_code="confirmation",
        stage_label="等待平台确认",
        summary="远端写入已完成，正在确认店铺商品状态",
        retry_count=7,
        attempt=1,
        next_check_at=next_check,
        last_external_status="CHECKING",
        phase_started_at=JOB_STARTED,
        activities=(
            JobStateActivity(code="offer_mapping", label="提交商品资料", status="completed"),
            JobStateActivity(code="confirmation", label="确认平台状态", status="running"),
        ),
    )
    projector = _projector(_FakeReader(snapshot))

    view = projector.project(_in_progress_task())
    job = view.active_job
    assert job is not None
    assert job.stage_code == "confirmation"
    assert job.retry_count == 7
    assert job.next_check_at == next_check
    assert job.last_external_status == "CHECKING"
    # running 且有下次检查时间 → 展示为正常等待。
    assert job.status == "waiting"
    assert job.phase_elapsed_seconds == int((OBSERVED - JOB_STARTED).total_seconds())

    codes = [activity.code for activity in view.activities]
    assert codes == ["offer_mapping", "confirmation"]


def test_missing_reader_degrades_to_generic_summary() -> None:
    projector = _projector()  # 不注册任何 reader

    view = projector.project(_in_progress_task())
    job = view.active_job
    assert job is not None
    assert job.summary == progress.JOB_PROGRESS_UNAVAILABLE_SUMMARY
    assert view.activities == []


def test_missing_job_degrades_to_unavailable_summary() -> None:
    projector = _projector(_FakeReader(JobStateSnapshot(status="running", available=False)))

    view = projector.project(_in_progress_task())
    job = view.active_job
    assert job is not None
    assert job.summary == progress.JOB_PROGRESS_MISSING_SUMMARY


def test_reader_exception_degrades_and_does_not_raise() -> None:
    projector = _projector(_RaisingReader())

    view = projector.project(_in_progress_task())
    job = view.active_job
    assert job is not None
    assert job.summary == progress.JOB_PROGRESS_MISSING_SUMMARY


def test_terminal_task_has_no_active_job_and_freezes_elapsed() -> None:
    projector = _projector(_FakeReader(JobStateSnapshot(status="success")))
    terminal = _terminal_task(updated=CREATED + timedelta(seconds=100))

    view = projector.project(terminal)
    assert view.active_job is None
    assert view.activities == []
    assert view.task_elapsed_seconds == 100
    # 所有步骤已完成，current_step_index 越界 → 无当前步骤。
    assert view.current_step is None


def test_view_response_wraps_task_and_progress() -> None:
    projector = _projector(_FakeReader(JobStateSnapshot(status="running")))
    task = _in_progress_task()

    response = projector.build_view_response(task)
    assert response.task_id == task.task_id
    assert response.task == task
    assert response.execution_progress is not None


def test_progress_view_contains_no_credentials_or_raw_payload() -> None:
    snapshot = JobStateSnapshot(
        status="running",
        summary="正常摘要",
        activities=(JobStateActivity(code="offer_mapping", status="running"),),
    )
    projector = _projector(_FakeReader(snapshot))

    dumped = projector.project(_in_progress_task()).model_dump(mode="json")
    text = repr(dumped)
    for forbidden in ("api_token", "access_token", "payload", "authorization"):
        assert forbidden not in text
