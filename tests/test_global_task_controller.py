from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import threading
from typing import Any

import pytest

from erp_web.db import ErpDatabase
from erp_web.schemas.draft_capabilities import (
    DraftQueryCriteria,
    DraftQuerySnapshot,
)
from erp_web.schemas.global_tasks import (
    CapabilityResult,
    GlobalPlanningDecision,
    GlobalTaskInputRequest,
    GlobalTaskPlanParameters,
    GlobalTaskPlanProposal,
    GlobalTaskStartRequest,
    GlobalTaskStepProposal,
    LocalGlobalTaskState,
    LocalTaskStep,
    RequiredInput,
    TrustedGlobalAnswer,
)
from erp_web.services.global_task_controller import (
    GlobalTaskController,
    GlobalTaskControllerError,
    GlobalTaskPlanningOutcome,
    declare_global_task_capability,
)
from erp_web.stores.global_task_store import LocalGlobalTaskStore


NOW = datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc)


def _answer_resolver(
    _task: LocalGlobalTaskState,
    decision: GlobalPlanningDecision,
) -> TrustedGlobalAnswer:
    return TrustedGlobalAnswer(
        answer_kind=decision.answer_kind or "active_draft_count",
        query_snapshot_id=decision.query_snapshot_id,
        message="当前共有 137 个活跃草稿。",
        facts={"active_draft_count": 137},
        evidence_refs=[f"draft_query_snapshot:{decision.query_snapshot_id}"],
    )


class _Planner:
    def __init__(self, *decisions: GlobalPlanningDecision) -> None:
        self.decisions = deque(decisions)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, task, supplement: str) -> GlobalTaskPlanningOutcome:
        self.calls.append((task.task_id, supplement))
        return GlobalTaskPlanningOutcome(decision=self.decisions.popleft())


class _PublishStatus:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {
            "platforms": {"ozon": {"status": "running"}}
        }
        self.calls: list[str] = []

    def __call__(self, job_id: str) -> dict[str, Any]:
        self.calls.append(job_id)
        return self.payload


def _plan(
    *capabilities: str,
    query_snapshot_id: str = "",
    draft_position: int | None = None,
    target_platform: str = "",
    parameters: GlobalTaskPlanParameters | None = None,
) -> GlobalPlanningDecision:
    return GlobalPlanningDecision(
        action="plan",
        plan=GlobalTaskPlanProposal(
            steps=[
                GlobalTaskStepProposal(
                    local_key=f"step-{index}",
                    capability=capability,
                    objective=f"执行 {capability}",
                )
                for index, capability in enumerate(capabilities, start=1)
            ],
            draft_position=draft_position,
            target_platform=target_platform,
            parameters=parameters or GlobalTaskPlanParameters(),
        ),
        query_snapshot_id=query_snapshot_id,
        explanation="按顺序执行计划。",
    )


def _snapshot(
    snapshot_id: str,
    draft_ids: list[str],
    *,
    total: int | None = None,
) -> DraftQuerySnapshot:
    return DraftQuerySnapshot(
        snapshot_id=snapshot_id,
        draft_ids=draft_ids,
        total=len(draft_ids) if total is None else total,
        count_by_platform={"ozon": len(draft_ids)},
        count_by_status={"ready_to_publish": len(draft_ids)},
        query=DraftQueryCriteria(scope="all", target_platform="ozon"),
        created_at=NOW,
    )


def _controller(
    tmp_path,
    *,
    planner: _Planner,
    capabilities: dict[str, Any],
    publish_status: _PublishStatus | None = None,
    answer_resolver: Any = _answer_resolver,
) -> tuple[GlobalTaskController, LocalGlobalTaskStore]:
    store = LocalGlobalTaskStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    controller = GlobalTaskController(
        store=store,
        planner=planner,
        capabilities=capabilities,
        publish_status_reader=publish_status or _PublishStatus(),
        answer_resolver=answer_resolver,
    )
    return controller, store


def _start(
    controller: GlobalTaskController,
    *,
    snapshot_id: str = "",
):
    return controller.create_task(
        GlobalTaskStartRequest(
            goal="完成当前商品处理",
            draft_query_snapshot_id=snapshot_id,
        ),
    )


def test_controller_executes_plan_strictly_in_order(tmp_path) -> None:
    calls: list[tuple[str, str]] = []

    def capability(task, step):
        calls.append((step.capability, step.status))
        return CapabilityResult(
            status="completed",
            summary=f"{step.capability} 已完成",
            result={"product_id": task.product_id or "product-1"},
        )

    names = ["product.read", "product.attributes.update", "product.images.prepare"]
    planner = _Planner(_plan(*names))
    controller, store = _controller(
        tmp_path,
        planner=planner,
        capabilities={name: capability for name in names},
    )

    task = _start(controller)

    assert [name for name, _status in calls] == names
    assert all(status == "running" for _name, status in calls)
    assert task.status == "completed"
    assert task.current_step_index == len(names)
    assert [step.status for step in task.steps] == ["completed"] * len(names)
    assert store.require_task(task.task_id) == task


def test_get_state_is_pure_read_and_explicit_recovery_resumes_planning_task(
    tmp_path,
) -> None:
    database_path = tmp_path / "erp.sqlite3"
    initial_store = LocalGlobalTaskStore(ErpDatabase(database_path))
    initial_store.create_task(
        LocalGlobalTaskState(
            task_id="task-planning-restart",
            goal="读取商品",
            status="planning",
            assistant_message="正在理解目标并制定计划。",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    planner = _Planner(_plan("product.read"))
    calls: list[str] = []

    def read_capability(_task, step):
        calls.append(step.step_id)
        return CapabilityResult(
            status="completed",
            summary="商品读取完成。",
            result={"product_id": "product-1"},
        )

    restarted = GlobalTaskController(
        store=LocalGlobalTaskStore(ErpDatabase(database_path)),
        planner=planner,
        capabilities={"product.read": read_capability},
        publish_status_reader=_PublishStatus(),
        answer_resolver=_answer_resolver,
    )

    snapshot = restarted.get_state("task-planning-restart")

    assert snapshot.status == "planning"
    assert planner.calls == []
    assert calls == []

    [completed] = restarted.recover_unfinished_tasks()

    assert completed.status == "completed"
    assert planner.calls == [("task-planning-restart", "")]
    assert calls == ["step_1_step-1"]
    assert restarted.store.require_task(completed.task_id) == completed


def test_recovery_limit_is_applied_after_skipping_user_waiting_tasks(
    tmp_path,
) -> None:
    database_path = tmp_path / "erp.sqlite3"
    store = LocalGlobalTaskStore(ErpDatabase(database_path))
    for index in range(100):
        created_at = NOW + timedelta(seconds=index)
        store.create_task(
            LocalGlobalTaskState(
                task_id=f"task-waiting-{index:03d}",
                goal="等待用户补充资料",
                status="needs_input",
                pending_inputs=[
                    RequiredInput(
                        key="clarification",
                        label="补充说明",
                        reason="请补充说明后继续。",
                    )
                ],
                pending_input_owner="planning",
                assistant_message="请补充说明后继续。",
                created_at=created_at,
                updated_at=created_at,
            )
        )
    planning_at = NOW + timedelta(seconds=101)
    store.create_task(
        LocalGlobalTaskState(
            task_id="task-recoverable-after-waiting",
            goal="读取商品",
            status="planning",
            assistant_message="正在规划。",
            created_at=planning_at,
            updated_at=planning_at,
        )
    )
    capability_calls: list[str] = []

    def read_capability(_task, step):
        capability_calls.append(step.step_id)
        return CapabilityResult(
            status="completed",
            summary="商品读取完成。",
            result={"product_id": "product-1"},
        )

    controller = GlobalTaskController(
        store=store,
        planner=_Planner(_plan("product.read")),
        capabilities={"product.read": read_capability},
        publish_status_reader=_PublishStatus(),
        answer_resolver=_answer_resolver,
    )

    [recovered] = controller.recover_unfinished_tasks(limit=1)

    assert recovered.task_id == "task-recoverable-after-waiting"
    assert recovered.status == "completed"
    assert capability_calls == ["step_1_step-1"]


def test_recovery_skips_locally_locked_leased_task_and_advances_next(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalGlobalTaskStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    first = store.create_task(
        LocalGlobalTaskState(
            task_id="task-locked-lease-1",
            goal="正在由前台执行",
            status="planning",
            assistant_message="正在规划。",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    second = store.create_task(
        LocalGlobalTaskState(
            task_id="task-locked-lease-2",
            goal="应由恢复 worker 推进",
            status="planning",
            assistant_message="正在规划。",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    holder_ready = threading.Event()
    release_holder = threading.Event()

    def hold_first_task() -> None:
        with store.task_lock(first.task_id):
            with store.execution_claim(first.task_id, lease_seconds=30) as claimed:
                assert claimed is not None
                holder_ready.set()
                assert release_holder.wait(timeout=5)

    holder = threading.Thread(target=hold_first_task)
    holder.start()
    assert holder_ready.wait(timeout=5)
    capability_calls: list[str] = []

    def read_capability(_task, step):
        capability_calls.append(step.step_id)
        return CapabilityResult(
            status="completed",
            summary="读取完成。",
            result={"product_id": "product-1"},
        )

    controller = GlobalTaskController(
        store=store,
        planner=_Planner(_plan("product.read")),
        capabilities={"product.read": read_capability},
        publish_status_reader=_PublishStatus(),
        answer_resolver=_answer_resolver,
    )
    # 模拟 SQL 列表返回后、resume 前才被另一线程领取的 TOCTOU；恢复 worker
    # 必须非阻塞跳过第一个本地锁，继续处理后续候选。
    monkeypatch.setattr(
        store,
        "list_recoverable_tasks",
        lambda *, limit=100: [first, second][:limit],
    )
    recovery_finished = threading.Event()
    recovered: list[LocalGlobalTaskState] = []

    def recover_once() -> None:
        recovered.extend(controller.recover_unfinished_tasks(limit=2))
        recovery_finished.set()

    recovery_thread = threading.Thread(target=recover_once)
    recovery_thread.start()
    try:
        assert recovery_finished.wait(timeout=1)
    finally:
        release_holder.set()
    recovery_thread.join(timeout=5)
    holder.join(timeout=5)

    assert [task.task_id for task in recovered] == [second.task_id]
    assert recovered[0].status == "completed"
    assert capability_calls == ["step_1_step-1"]


def test_resume_nonrecoverable_task_does_not_claim_or_mutate_snapshot(tmp_path) -> None:
    controller, store = _controller(
        tmp_path,
        planner=_Planner(_plan("product.read")),
        capabilities={},
    )
    created = store.create_task(
        LocalGlobalTaskState(
            task_id="task-needs-input-no-resume",
            goal="等待用户选择草稿",
            status="needs_input",
            pending_inputs=[
                RequiredInput(
                    key="draft_position",
                    label="草稿序号",
                    reason="请说明要处理第几个草稿。",
                )
            ],
            pending_input_owner="planning",
            assistant_message="请说明要处理第几个草稿。",
            created_at=NOW,
            updated_at=NOW,
        )
    )

    resumed = controller.resume_task(created.task_id)

    assert resumed == created
    assert resumed.execution_id == ""
    assert resumed.revision == created.revision


def test_explicit_resume_continues_running_task_without_replaying_completed_prefix(
    tmp_path,
) -> None:
    database_path = tmp_path / "erp.sqlite3"
    initial_store = LocalGlobalTaskStore(ErpDatabase(database_path))
    initial_store.create_task(
        LocalGlobalTaskState(
            task_id="task-running-restart",
            goal="继续执行中断任务",
            status="running",
            steps=[
                LocalTaskStep(
                    step_id="step_1_read",
                    capability="product.read",
                    objective="读取商品",
                    status="completed",
                    result_summary="商品读取完成。",
                    result_ref="product-1",
                ),
                LocalTaskStep(
                    step_id="step_2_attributes",
                    capability="product.attributes.update",
                    objective="保存属性",
                    status="running",
                    operation_key=(
                        "global-task:task-running-restart:"
                        "step:step_2_attributes"
                    ),
                    recovery_policy="retry_safe",
                    inputs={"updates": {"BRAND": "Acme"}},
                ),
                LocalTaskStep(
                    step_id="step_3_images",
                    capability="product.images.prepare",
                    objective="准备图片",
                    status="pending",
                ),
            ],
            current_step_index=1,
            assistant_message="正在保存属性。",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    calls: list[str] = []

    def capability(_task, step):
        calls.append(step.capability)
        return CapabilityResult(
            status="completed",
            summary=f"{step.capability} 已完成。",
            result={"draft_id": "draft-1"},
        )

    capability = declare_global_task_capability(
        capability,
        recovery_policy="retry_safe",
    )

    planner = _Planner()
    restarted = GlobalTaskController(
        store=LocalGlobalTaskStore(ErpDatabase(database_path)),
        planner=planner,
        capabilities={
            "product.read": capability,
            "product.attributes.update": capability,
            "product.images.prepare": capability,
        },
        publish_status_reader=_PublishStatus(),
        answer_resolver=_answer_resolver,
    )

    snapshot = restarted.get_state("task-running-restart")
    assert snapshot.status == "running"
    assert calls == []

    completed = restarted.resume_task("task-running-restart")

    assert completed.status == "completed"
    assert completed.current_step_index == 3
    assert [step.status for step in completed.steps] == [
        "completed",
        "completed",
        "completed",
    ]
    assert calls == ["product.attributes.update", "product.images.prepare"]
    assert planner.calls == []


def test_two_controllers_compete_for_planning_and_only_one_executes(tmp_path) -> None:
    database_path = tmp_path / "erp.sqlite3"
    seed = LocalGlobalTaskStore(ErpDatabase(database_path))
    task = seed.create_task(
        LocalGlobalTaskState(
            task_id="task-concurrent-resume",
            goal="并发恢复",
            status="planning",
            assistant_message="等待恢复。",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    planner_started = threading.Event()
    release_planner = threading.Event()
    planner_calls: list[str] = []

    def planner(current, _supplement):
        planner_calls.append(current.execution_id)
        planner_started.set()
        assert release_planner.wait(timeout=5)
        return GlobalTaskPlanningOutcome(decision=_plan("product.read"))

    capability_calls: list[str] = []

    def capability(_task, step):
        capability_calls.append(step.step_id)
        return CapabilityResult(
            status="completed",
            summary="读取完成。",
            result={"product_id": "product-1"},
        )

    controllers = [
        GlobalTaskController(
            store=LocalGlobalTaskStore(ErpDatabase(database_path)),
            planner=planner,
            capabilities={"product.read": capability},
            publish_status_reader=_PublishStatus(),
            answer_resolver=_answer_resolver,
        )
        for _ in range(2)
    ]
    outcomes: list[str] = []

    def resume(controller):
        try:
            outcomes.append(controller.resume_task(task.task_id).status)
        except Exception as exc:
            outcomes.append(str(getattr(exc, "code", type(exc).__name__)))

    first = threading.Thread(target=resume, args=(controllers[0],))
    second = threading.Thread(target=resume, args=(controllers[1],))
    first.start()
    assert planner_started.wait(timeout=5)
    second.start()
    second.join(timeout=5)
    release_planner.set()
    first.join(timeout=5)

    assert planner_calls and len(planner_calls) == 1
    assert capability_calls == ["step_1_step-1"]
    assert sorted(outcomes) == ["GLOBAL_TASK_EXECUTION_BUSY", "completed"]


def test_unsafe_running_step_is_failed_without_replaying_side_effect(
    tmp_path,
) -> None:
    store = LocalGlobalTaskStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    task = store.create_task(
        LocalGlobalTaskState(
            task_id="task-unsafe-recovery",
            goal="恢复未知结果的写步骤",
            status="running",
            steps=[
                LocalTaskStep(
                    step_id="step_1_write",
                    capability="product.attributes.update",
                    objective="保存属性",
                    status="running",
                    operation_key=(
                        "global-task:task-unsafe-recovery:step:step_1_write"
                    ),
                    recovery_policy="manual",
                    attempt_execution_id="gexec_crashed",
                )
            ],
            current_step_index=0,
            assistant_message="正在保存属性。",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    calls: list[str] = []

    def write_capability(_task, step):
        calls.append(step.operation_key)
        return CapabilityResult(
            status="completed",
            summary="保存完成。",
            result={"draft_id": "draft-1"},
        )

    controller = GlobalTaskController(
        store=LocalGlobalTaskStore(ErpDatabase(tmp_path / "erp.sqlite3")),
        planner=_Planner(),
        capabilities={"product.attributes.update": write_capability},
        publish_status_reader=_PublishStatus(),
        answer_resolver=_answer_resolver,
    )

    with pytest.raises(GlobalTaskControllerError) as cancel_error:
        controller.cancel(task.task_id)
    assert cancel_error.value.code == "GLOBAL_TASK_STEP_OUTCOME_UNKNOWN"
    assert controller.get_state(task.task_id) == task

    failed = controller.resume_task(task.task_id)

    assert calls == []
    assert failed.status == "failed"
    assert failed.error_code == "GLOBAL_TASK_STEP_RECOVERY_UNSAFE"
    assert failed.steps[0].status == "failed"
    assert failed.steps[0].error_code == "GLOBAL_TASK_STEP_RECOVERY_UNSAFE"


def test_invalid_input_after_status_claim_does_not_change_persisted_row(
    tmp_path,
) -> None:
    database = ErpDatabase(tmp_path / "erp.sqlite3")
    store = LocalGlobalTaskStore(database)
    task = store.create_task(
        LocalGlobalTaskState(
            task_id="task-invalid-input-claim",
            goal="等待品牌字段",
            status="needs_input",
            steps=[
                LocalTaskStep(
                    step_id="step_1_update",
                    capability="product.attributes.update",
                    objective="更新属性",
                    status="needs_input",
                    operation_key=(
                        "global-task:task-invalid-input-claim:step:step_1_update"
                    ),
                )
            ],
            current_step_index=0,
            pending_inputs=[
                RequiredInput(
                    key="brand",
                    label="品牌",
                    reason="请填写品牌。",
                )
            ],
            pending_input_owner="capability",
            assistant_message="请填写品牌。",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    controller = GlobalTaskController(
        store=store,
        planner=_Planner(),
        capabilities={},
        publish_status_reader=_PublishStatus(),
        answer_resolver=_answer_resolver,
    )
    with database._connect() as conn:
        before = dict(
            conn.execute(
                "SELECT * FROM global_tasks WHERE task_id = ?",
                (task.task_id,),
            ).fetchone()
        )

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.submit_input(
            GlobalTaskInputRequest(
                task_id=task.task_id,
                inputs={"unknown": "value"},
            )
        )
    assert error.value.code == "GLOBAL_TASK_INPUT_FIELD_UNKNOWN"

    with database._connect() as conn:
        after = dict(
            conn.execute(
                "SELECT * FROM global_tasks WHERE task_id = ?",
                (task.task_id,),
            ).fetchone()
        )
    assert after == before


def test_lease_takeover_reuses_operation_key_and_true_owner_effects_once(
    tmp_path,
) -> None:
    database_path = tmp_path / "erp.sqlite3"
    database = ErpDatabase(database_path)
    store = LocalGlobalTaskStore(database)
    operation_key = "global-task:task-fenced-effect:step:step_1_write"
    task = store.create_task(
        LocalGlobalTaskState(
            task_id="task-fenced-effect",
            goal="执行带幂等 owner 的写步骤",
            status="running",
            steps=[
                LocalTaskStep(
                    step_id="step_1_write",
                    capability="test.idempotent.write",
                    objective="写入一次",
                    operation_key=operation_key,
                    recovery_policy="idempotent",
                )
            ],
            current_step_index=0,
            assistant_message="准备写入。",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    owner_lock = threading.Lock()
    applied_keys: set[str] = set()
    effects: list[str] = []
    attempts: list[tuple[str, str]] = []
    first_entered = threading.Event()
    release_first = threading.Event()

    def idempotent_capability(current, step):
        with owner_lock:
            attempts.append((current.execution_id, step.operation_key))
            first_attempt = len(attempts) == 1
            if step.operation_key not in applied_keys:
                applied_keys.add(step.operation_key)
                effects.append(step.operation_key)
        if first_attempt:
            first_entered.set()
            assert release_first.wait(timeout=5)
        return CapabilityResult(
            status="completed",
            summary="幂等写入完成。",
            result={"draft_id": "draft-1"},
        )

    idempotent_capability = declare_global_task_capability(
        idempotent_capability,
        recovery_policy="idempotent",
    )
    controllers = [
        GlobalTaskController(
            store=LocalGlobalTaskStore(ErpDatabase(database_path)),
            planner=_Planner(),
            capabilities={"test.idempotent.write": idempotent_capability},
            publish_status_reader=_PublishStatus(),
            answer_resolver=_answer_resolver,
        )
        for _ in range(2)
    ]
    first_errors: list[str] = []

    def run_first() -> None:
        try:
            controllers[0].resume_task(task.task_id)
        except Exception as exc:
            first_errors.append(str(getattr(exc, "code", type(exc).__name__)))

    first_thread = threading.Thread(target=run_first)
    first_thread.start()
    assert first_entered.wait(timeout=5)
    # 模拟进程停止续租，但尚未从外部 owner 返回；第二个执行者随后接管。
    with database._connect() as conn:
        conn.execute(
            "UPDATE global_tasks SET execution_lease_expires_at = 0 "
            "WHERE task_id = ?",
            (task.task_id,),
        )
        conn.commit()

    completed = controllers[1].resume_task(task.task_id)
    release_first.set()
    first_thread.join(timeout=5)

    assert completed.status == "completed"
    assert effects == [operation_key]
    assert len(attempts) == 2
    assert {key for _execution_id, key in attempts} == {operation_key}
    assert len({execution_id for execution_id, _key in attempts}) == 2
    assert first_errors == ["GLOBAL_TASK_REVISION_CONFLICT"]


def test_validate_only_plan_completes_without_requesting_publish_confirmation(
    tmp_path,
) -> None:
    def validate(_task, _step):
        return CapabilityResult(
            status="completed",
            summary="发布校验通过。",
            result={
                "passed": True,
                "validation_digest": "digest-validate-only",
                "summary": {"platform": "ozon", "draft_id": "draft-1"},
            },
        )

    controller, store = _controller(
        tmp_path,
        planner=_Planner(_plan("product.publish.validate")),
        capabilities={"product.publish.validate": validate},
    )

    completed = _start(controller)

    assert completed.status == "completed"
    assert completed.current_step_index == 1
    assert completed.steps[0].status == "completed"
    assert completed.publish_confirmation.status == "none"
    assert completed.publish_job_id == ""
    assert store.require_task(completed.task_id) == completed


def test_needs_input_is_saved_merged_and_resumed_after_restart(tmp_path) -> None:
    seen_inputs: list[dict[str, Any]] = []

    def capability(_task, step):
        seen_inputs.append(dict(step.inputs))
        missing = [key for key in ("brand", "color") if key not in step.inputs]
        if missing:
            return CapabilityResult(
                status="needs_input",
                summary="请补充商品资料。",
                required_inputs=[
                    RequiredInput(
                        key=key,
                        label=key,
                        reason=f"缺少 {key}",
                    )
                    for key in missing
                ],
            )
        return CapabilityResult(
            status="completed",
            summary="商品资料已补齐。",
            result={"product_id": "product-1"},
        )

    planner = _Planner(_plan("product.attributes.update"))
    controller, store = _controller(
        tmp_path,
        planner=planner,
        capabilities={"product.attributes.update": capability},
    )
    paused = _start(controller)

    assert paused.status == "needs_input"
    assert [item.key for item in paused.pending_inputs] == ["brand", "color"]
    assert store.require_task(paused.task_id).steps[0].status == "needs_input"

    # 重建 Controller/Store 模拟进程重启，证明待补字段与已提交资料都有 durable owner。
    restarted, _ = _controller(
        tmp_path,
        planner=planner,
        capabilities={"product.attributes.update": capability},
    )
    partially_filled = restarted.submit_input(
        GlobalTaskInputRequest(task_id=paused.task_id, inputs={"brand": "Acme"})
    )

    assert partially_filled.status == "needs_input"
    assert [item.key for item in partially_filled.pending_inputs] == ["color"]
    assert partially_filled.steps[0].inputs == {"brand": "Acme"}
    assert len(seen_inputs) == 1

    completed = restarted.submit_input(
        GlobalTaskInputRequest(task_id=paused.task_id, inputs={"color": "black"})
    )

    assert completed.status == "completed"
    assert seen_inputs == [{}, {"brand": "Acme", "color": "black"}]
    assert completed.steps[0].inputs == {"brand": "Acme", "color": "black"}


def test_planning_clarification_inputs_replan_instead_of_requiring_a_step(
    tmp_path,
) -> None:
    planner = _Planner(
        GlobalPlanningDecision(
            action="ask_user",
            question="要发布到哪个平台？",
            explanation="目标平台不明确。",
        ),
        _plan("product.read", target_platform="ozon"),
    )

    def read_capability(_task, step):
        assert step.inputs["platform"] == "ozon"
        return CapabilityResult(
            status="completed",
            summary="商品读取完成。",
            result={"product_id": "product-1"},
        )

    controller, _store = _controller(
        tmp_path,
        planner=planner,
        capabilities={"product.read": read_capability},
    )
    paused = _start(controller)

    assert paused.status == "needs_input"
    assert paused.steps == []
    completed = controller.submit_input(
        GlobalTaskInputRequest(
            task_id=paused.task_id,
            inputs={"clarification": "Ozon"},
        )
    )

    assert completed.status == "completed"
    assert planner.calls[1][1] == "Ozon"


def test_controller_rejects_plan_overriding_explicit_task_platform(
    tmp_path,
) -> None:
    capability_calls: list[str] = []
    planner = _Planner(_plan("product.read", target_platform="mercadolibre"))

    def capability(_task, _step):
        capability_calls.append("called")
        return CapabilityResult(
            status="completed",
            summary="不应执行。",
            result={"product_id": "product-1"},
        )

    controller, _store = _controller(
        tmp_path,
        planner=planner,
        capabilities={"product.read": capability},
    )

    task = controller.create_task(
        GlobalTaskStartRequest(
            goal="读取商品",
            platform="ozon",
        ),
    )

    assert task.status == "failed"
    assert task.error_code == "GLOBAL_PLAN_PLATFORM_SCOPE_MISMATCH"
    assert capability_calls == []


def test_controller_allows_plan_platform_when_task_platform_is_unbound(
    tmp_path,
) -> None:
    captured_platforms: list[str] = []
    planner = _Planner(_plan("product.read", target_platform="ozon"))

    def capability(_task, step):
        captured_platforms.append(str(step.inputs.get("platform") or ""))
        return CapabilityResult(
            status="completed",
            summary="商品读取完成。",
            result={"product_id": "product-1"},
        )

    controller, _store = _controller(
        tmp_path,
        planner=planner,
        capabilities={"product.read": capability},
    )

    task = _start(controller)

    assert task.status == "completed"
    assert captured_platforms == ["ozon"]


def test_planning_and_capability_clarifications_follow_durable_owner(
    tmp_path,
) -> None:
    planner = _Planner(
        GlobalPlanningDecision(
            action="ask_user",
            question="要处理哪个平台？",
            explanation="目标平台不明确。",
        ),
        _plan("product.attributes.update", target_platform="ozon"),
    )
    capability_calls: list[dict[str, Any]] = []

    def capability(_task, step):
        capability_calls.append(dict(step.inputs))
        if "attributes" not in step.inputs:
            return CapabilityResult(
                status="needs_input",
                summary="请提供属性对象。",
                required_inputs=[
                    RequiredInput(
                        key="attributes",
                        label="属性",
                        reason="需要明确的属性和值。",
                        input_type="json_object",
                    )
                ],
            )
        return CapabilityResult(
            status="completed",
            summary="属性已保存。",
            result={"draft_id": "draft-1"},
        )

    controller, store = _controller(
        tmp_path,
        planner=planner,
        capabilities={"product.attributes.update": capability},
    )
    planning_pause = _start(controller)

    assert planning_pause.pending_input_owner == "planning"
    capability_pause = controller.submit_input(
        GlobalTaskInputRequest(
            task_id=planning_pause.task_id,
            inputs={"clarification": "Ozon"},
        )
    )
    assert capability_pause.status == "needs_input"
    assert capability_pause.pending_input_owner == "capability"
    assert len(planner.calls) == 2

    # 新 Controller 实例必须依持久化 owner 直接恢复 Capability，而非再调用模型。
    restarted = GlobalTaskController(
        store=LocalGlobalTaskStore(ErpDatabase(tmp_path / "erp.sqlite3")),
        planner=planner,
        capabilities={"product.attributes.update": capability},
        publish_status_reader=_PublishStatus(),
        answer_resolver=_answer_resolver,
    )
    completed = restarted.submit_input(
        GlobalTaskInputRequest(
            task_id=capability_pause.task_id,
            message="这是确定值，不需要重新规划。",
            inputs={"attributes": {"BRAND": "Acme"}},
        )
    )

    assert completed.status == "completed"
    assert len(planner.calls) == 2
    assert capability_calls == [
        {"platform": "ozon"},
        {
            "platform": "ozon",
            "attributes": {"BRAND": "Acme"},
            "additional_context": "这是确定值，不需要重新规划。",
        },
    ]
    assert store.require_task(completed.task_id).pending_input_owner == "none"


def test_submitted_capability_fields_merge_into_structured_input_owners(
    tmp_path,
) -> None:
    seen_inputs: list[dict[str, Any]] = []

    def capability(_task, step):
        seen_inputs.append(dict(step.inputs))
        if len(seen_inputs) == 1:
            return CapabilityResult(
                status="needs_input",
                summary="请补充属性与核价事实。",
                required_inputs=[
                    RequiredInput(
                        key="COLOR",
                        label="颜色",
                        reason="缺少颜色",
                        input_owner="provided_attributes",
                    ),
                    *[
                        RequiredInput(
                            key=key,
                            label=key,
                            reason=f"缺少 {key}",
                            input_owner="pricing_input",
                        )
                        for key in (
                            "shipping_quote_mode",
                            "domestic_freight",
                            "commission_percent",
                            "target_margin_percent",
                        )
                    ],
                    RequiredInput(
                        key="site",
                        label="站点",
                        reason="缺少站点",
                    ),
                ],
            )
        return CapabilityResult(
            status="completed",
            summary="资料已保存。",
            result={"draft_id": "draft-1"},
        )

    planner = _Planner(_plan("draft.prepare_for_market"))
    controller, _store = _controller(
        tmp_path,
        planner=planner,
        capabilities={"draft.prepare_for_market": capability},
    )
    paused = _start(controller)
    restarted, _ = _controller(
        tmp_path,
        planner=planner,
        capabilities={"draft.prepare_for_market": capability},
    )

    completed = restarted.submit_input(
        GlobalTaskInputRequest(
            task_id=paused.task_id,
            inputs={
                "COLOR": "Black",
                "shipping_quote_mode": "manual",
                "domestic_freight": "12.50",
                "commission_percent": "16",
                "target_margin_percent": "30",
                "site": "MLM",
            },
        )
    )

    assert completed.status == "completed"
    assert seen_inputs == [
        {},
        {
            "provided_attributes": {"COLOR": "Black"},
            "pricing_input": {
                "shipping_quote_mode": "manual",
                "domestic_freight": "12.50",
                "commission_percent": "16",
                "target_margin_percent": "30",
            },
            "site": "MLM",
        },
    ]


def test_plan_parameters_are_mapped_only_to_their_intended_capabilities(
    tmp_path,
) -> None:
    captured: dict[str, dict[str, Any]] = {}

    def capability(_task, step):
        captured[step.capability] = dict(step.inputs)
        return CapabilityResult(
            status="completed",
            summary=f"{step.capability} 已完成。",
            result={"draft_id": "draft-1"},
        )

    parameters = GlobalTaskPlanParameters(
        attribute_updates={"BRAND": "Acme"},
        provided_attributes={"COLOR": "Black"},
        pricing_input={"cost_cny": "12.50", "stock": "8"},
        regenerate_copy=True,
    )
    names = [
        "product.read",
        "product.attributes.update",
        "product.attributes.fill",
        "draft.prepare_for_market",
        "product.images.prepare",
    ]
    controller, _store = _controller(
        tmp_path,
        planner=_Planner(
            _plan(
                *names,
                query_snapshot_id="snapshot-parameters",
                draft_position=1,
                target_platform="ozon",
                parameters=parameters,
            )
        ),
        capabilities={name: capability for name in names},
    )
    controller.store.save_draft_query_snapshot(
        _snapshot("snapshot-parameters", ["draft-1"])
    )

    completed = _start(controller)

    assert completed.status == "completed"
    trusted = {
        "platform": "ozon",
        "draft_id": "draft-1",
        "draft_position": 1,
        "snapshot_id": "snapshot-parameters",
    }
    assert captured["product.read"] == trusted
    assert captured["product.attributes.update"] == {
        **trusted,
        "updates": {"BRAND": "Acme"},
    }
    assert captured["product.attributes.fill"] == {
        **trusted,
        "provided_attributes": {"COLOR": "Black"},
    }
    assert captured["draft.prepare_for_market"] == {
        **trusted,
        "provided_attributes": {"COLOR": "Black"},
        "pricing_input": {"cost_cny": "12.50", "stock": "8"},
        "regenerate_copy": True,
    }
    assert captured["product.images.prepare"] == trusted


def test_failed_publish_validation_never_enters_confirmation_or_submission(
    tmp_path,
) -> None:
    publish_calls: list[str] = []

    def validate(_task, _step):
        return CapabilityResult(
            status="completed",
            summary="发布校验失败。",
            result={
                "passed": False,
                "errors": [{"code": "TITLE_REQUIRED"}],
            },
        )

    def publish(_task, step):
        publish_calls.append(step.step_id)
        return CapabilityResult(
            status="in_progress",
            summary="已提交。",
            job_id="job-should-not-exist",
        )

    controller, _store = _controller(
        tmp_path,
        planner=_Planner(
            _plan("product.publish.validate", "product.publish.request")
        ),
        capabilities={
            "product.publish.validate": validate,
            "product.publish.request": publish,
        },
    )

    task = _start(controller)

    assert task.status == "failed"
    assert task.error_code == "PUBLISH_VALIDATION_FAILED"
    assert task.publish_confirmation.status == "none"
    assert publish_calls == []


@pytest.mark.parametrize(
    ("capabilities", "expected_code"),
    [
        (
            ("product.publish.request", "product.publish.validate"),
            "GLOBAL_TASK_PLAN_PUBLISH_ORDER_INVALID",
        ),
        (
            (
                "product.publish.validate",
                "product.publish.request",
                "product.publish.request",
            ),
            "GLOBAL_TASK_PLAN_PUBLISH_REQUEST_MULTIPLE",
        ),
    ],
)
def test_controller_rejects_unsafe_publish_plan_before_any_capability_runs(
    tmp_path,
    capabilities,
    expected_code,
) -> None:
    calls: list[str] = []

    def capability(_task, step):
        calls.append(step.capability)
        return CapabilityResult(
            status="completed",
            summary="不应执行。",
            result={"passed": True},
        )

    controller, _store = _controller(
        tmp_path,
        planner=_Planner(_plan(*capabilities)),
        capabilities={
            "product.publish.validate": capability,
            "product.publish.request": capability,
        },
    )

    task = _start(controller)

    assert task.status == "failed"
    assert task.error_code == expected_code
    assert calls == []


def test_controller_rejects_capability_outside_static_map(tmp_path) -> None:
    controller, _store = _controller(
        tmp_path,
        planner=_Planner(_plan("runtime.dynamic.import")),
        capabilities={},
    )

    task = _start(controller)

    assert task.status == "failed"
    assert task.error_code == "GLOBAL_TASK_CAPABILITY_UNAVAILABLE"


def test_controller_defensively_rejects_more_than_twelve_steps(tmp_path) -> None:
    # model_construct 模拟未来调用方跳过 planner 输出校验；Controller 仍不能执行。
    proposals = [
        GlobalTaskStepProposal(
            local_key=f"overflow-{index}",
            capability="product.read",
            objective="读取商品",
        )
        for index in range(13)
    ]
    oversized_plan = GlobalTaskPlanProposal.model_construct(
        steps=proposals,
        draft_position=None,
        target_platform="",
    )
    decision = GlobalPlanningDecision.model_construct(
        action="plan",
        plan=oversized_plan,
        query_snapshot_id="",
        answer_kind=None,
        answer_draft_position=None,
        question="",
        explanation="",
    )
    calls: list[str] = []

    def capability(_task, step):
        calls.append(step.step_id)
        return CapabilityResult(
            status="completed",
            summary="不应执行。",
            result={"product_id": "product-1"},
        )

    controller, _store = _controller(
        tmp_path,
        planner=_Planner(decision),
        capabilities={"product.read": capability},
    )

    task = _start(controller)

    assert task.status == "failed"
    assert task.error_code == "GLOBAL_TASK_PLAN_STEP_COUNT_INVALID"
    assert calls == []


def _waiting_publish_task(tmp_path, status_payload: dict[str, Any]):
    publish_calls: list[dict[str, Any]] = []

    def validate(_task, _step):
        return CapabilityResult(
            status="completed",
            summary="发布校验通过。",
            result={
                "passed": True,
                "validation_digest": "digest-1",
                "summary": {
                    "platform": "ozon",
                    "draft_id": "draft-1",
                    "price": "199.00 RUB",
                },
            },
        )

    def publish(task, step):
        publish_calls.append(
            {
                "step_id": step.step_id,
                "confirmation": task.publish_confirmation,
                "idempotency_key": task.publish_idempotency_key,
            }
        )
        return CapabilityResult(
            status="in_progress",
            summary="已提交 PublishingBus。",
            job_id="publish-job-1",
        )

    status = _PublishStatus(status_payload)
    controller, store = _controller(
        tmp_path,
        planner=_Planner(
            _plan("product.publish.validate", "product.publish.request")
        ),
        capabilities={
            "product.publish.validate": validate,
            "product.publish.request": publish,
        },
        publish_status=status,
    )
    task = _start(controller)
    assert task.status == "waiting_publish_confirmation"
    return controller, store, task, publish_calls, status


def test_confirmation_submits_once_and_duplicate_confirmation_only_refreshes(
    tmp_path,
) -> None:
    controller, store, task, publish_calls, status = _waiting_publish_task(
        tmp_path,
        {"platforms": {"ozon": {"status": "running"}}},
    )

    waiting = controller.confirm_publish(task.task_id)
    duplicate = controller.confirm_publish(task.task_id)

    assert waiting.status == "waiting_publish_result"
    assert duplicate.status == "waiting_publish_result"
    assert waiting.publish_job_id == "publish-job-1"
    assert len(publish_calls) == 1
    submitted = publish_calls[0]
    assert submitted["confirmation"].status == "confirmed"
    assert submitted["confirmation"].validation_digest == "digest-1"
    assert submitted["confirmation"].confirmed_at is not None
    assert submitted["idempotency_key"] == (
        f"global-task:{task.task_id}:step:step_2_step-2"
    )
    assert status.calls == []
    assert duplicate == waiting
    assert store.require_task(task.task_id).publish_job_id == "publish-job-1"


@pytest.mark.parametrize(
    ("platform_payload", "expected_status", "expected_step_status"),
    [
        (
            {"platforms": {"ozon": {"status": "success"}}},
            "completed",
            "completed",
        ),
        (
            {
                "platforms": {
                    "ozon": {"status": "failed", "error": "远端拒绝商品"}
                }
            },
            "failed",
            "failed",
        ),
    ],
)
def test_platform_terminal_status_controls_task_terminal_status(
    tmp_path,
    platform_payload,
    expected_status,
    expected_step_status,
) -> None:
    controller, store, task, publish_calls, _status = _waiting_publish_task(
        tmp_path,
        platform_payload,
    )
    waiting = controller.confirm_publish(task.task_id)

    # GET 只读；发布状态也只由显式恢复入口刷新。
    assert controller.get_state(waiting.task_id).status == "waiting_publish_result"
    terminal = controller.resume_task(waiting.task_id)

    assert len(publish_calls) == 1
    assert terminal.status == expected_status
    assert terminal.steps[1].status == expected_step_status
    assert store.require_task(task.task_id) == terminal
    if expected_status == "failed":
        assert terminal.error_code == "PUBLISH_PLATFORM_FAILED"
        assert terminal.error_message == "远端拒绝商品"


def test_cancel_before_confirmation_never_submits_publish(tmp_path) -> None:
    controller, store, task, publish_calls, _status = _waiting_publish_task(
        tmp_path,
        {"platforms": {"ozon": {"status": "running"}}},
    )

    cancelled = controller.cancel(task.task_id)
    repeated = controller.cancel(task.task_id)

    assert cancelled.status == "cancelled"
    assert repeated == cancelled
    assert publish_calls == []


def test_terminal_operations_keep_complete_persisted_snapshot_unchanged(
    tmp_path,
) -> None:
    controller, store = _controller(
        tmp_path,
        planner=_Planner(_plan("product.read")),
        capabilities={
            "product.read": lambda _task, _step: CapabilityResult(
                status="completed",
                summary="读取完成。",
                result={"product_id": "product-1"},
            )
        },
    )
    terminal = _start(controller)
    assert terminal.status == "completed"
    database = store._db
    with database._connect() as conn:
        row_before = dict(
            conn.execute(
                "SELECT * FROM global_tasks WHERE task_id = ?",
                (terminal.task_id,),
            ).fetchone()
        )

    assert controller.cancel(terminal.task_id) == terminal
    assert controller.confirm_publish(terminal.task_id) == terminal
    with pytest.raises(GlobalTaskControllerError) as error:
        controller.submit_input(
            GlobalTaskInputRequest(
                task_id=terminal.task_id,
                message="错误的终态补充",
            )
        )
    assert getattr(error.value, "code", "") == "GLOBAL_TASK_INPUT_NOT_EXPECTED"

    with database._connect() as conn:
        row_after = dict(
            conn.execute(
                "SELECT * FROM global_tasks WHERE task_id = ?",
                (terminal.task_id,),
            ).fetchone()
        )
    assert row_after == row_before
    assert store.require_task(terminal.task_id) == terminal


def test_answer_uses_injected_trusted_resolver_and_persists_canonical_result(
    tmp_path,
) -> None:
    def fresh_answer_resolver(
        _task: LocalGlobalTaskState,
        _decision: GlobalPlanningDecision,
    ) -> TrustedGlobalAnswer:
        return TrustedGlobalAnswer(
            answer_kind="active_draft_count",
            query_snapshot_id="snapshot-fresh",
            message="当前共有 2 个活跃草稿。",
            facts={"active_draft_count": 2},
            evidence_refs=["draft_query_snapshot:snapshot-fresh"],
        )

    planner = _Planner(
        GlobalPlanningDecision(
            action="answer",
            query_snapshot_id="snapshot-answer",
            answer_kind="active_draft_count",
            explanation="读取最近一次查询结果。",
        )
    )
    controller, store = _controller(
        tmp_path,
        planner=planner,
        capabilities={},
        answer_resolver=fresh_answer_resolver,
    )
    store.save_draft_query_snapshot(
        _snapshot("snapshot-answer", ["draft-1", "draft-2"], total=137)
    )

    task = _start(controller)

    assert task.status == "completed"
    assert task.assistant_message == "当前共有 2 个活跃草稿。"
    assert task.draft_query_snapshot_id == "snapshot-fresh"
    assert store.require_task(task.task_id) == task


@pytest.mark.parametrize(
    ("product_id", "platform", "expected_code"),
    [
        ("product-p1", "", "GLOBAL_ANSWER_PRODUCT_SCOPE_MISMATCH"),
        ("", "mercadolibre", "GLOBAL_ANSWER_PLATFORM_SCOPE_MISMATCH"),
    ],
)
def test_controller_rejects_precomputed_answer_outside_task_scope(
    tmp_path,
    product_id: str,
    platform: str,
    expected_code: str,
) -> None:
    decision = GlobalPlanningDecision(
        action="answer",
        query_snapshot_id="snapshot-p2",
        answer_kind="draft_market_context",
    )
    precomputed = TrustedGlobalAnswer(
        answer_kind="draft_market_context",
        query_snapshot_id="snapshot-p2",
        message="当前草稿的目标平台是 ozon。",
        facts={
            "product_id": "product-p2",
            "target_platform": "ozon",
        },
        evidence_refs=["draft_query_snapshot:snapshot-p2", "draft:p2"],
    )

    def planner(_task, _supplement: str) -> GlobalTaskPlanningOutcome:
        return GlobalTaskPlanningOutcome(
            decision=decision,
            trusted_answer=precomputed,
        )

    controller, _store = _controller(
        tmp_path,
        planner=planner,  # type: ignore[arg-type]
        capabilities={},
        answer_resolver=lambda _task, _decision: pytest.fail(
            "已有预解析答案时不应重复查询"
        ),
    )

    task = controller.create_task(
        GlobalTaskStartRequest(
            goal="查看当前草稿目标平台",
            product_id=product_id,
            platform=platform,
            draft_query_snapshot_id="snapshot-p2",
        ),
    )

    assert task.status == "failed"
    assert task.error_code == expected_code
    assert task.assistant_message == "无法验证只读回答，请重新查询。"


def test_draft_position_resolves_from_referenced_snapshot_not_newer_order(
    tmp_path,
) -> None:
    captured_inputs: list[dict[str, Any]] = []

    def capability(_task, step):
        captured_inputs.append(dict(step.inputs))
        return CapabilityResult(
            status="completed",
            summary="草稿读取完成。",
            result={"draft_id": step.inputs["draft_id"]},
        )

    planner = _Planner(
        _plan(
            "product.read",
            query_snapshot_id="snapshot-original",
            draft_position=2,
        )
    )
    controller, store = _controller(
        tmp_path,
        planner=planner,
        capabilities={"product.read": capability},
    )
    store.save_draft_query_snapshot(
        _snapshot("snapshot-original", ["draft-a", "draft-b"])
    )
    store.save_draft_query_snapshot(
        _snapshot("snapshot-newer", ["draft-new", "draft-a", "draft-b"])
    )

    task = _start(controller, snapshot_id="snapshot-newer")

    assert task.status == "completed"
    assert captured_inputs == [
        {
            "draft_id": "draft-b",
            "draft_position": 2,
            "snapshot_id": "snapshot-original",
        }
    ]
    assert task.draft_query_snapshot_id == "snapshot-original"
