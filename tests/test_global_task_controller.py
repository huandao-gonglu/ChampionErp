from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
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
)
from erp_web.services.global_task_controller import (
    GlobalTaskController,
    GlobalTaskPlanningOutcome,
)
from erp_web.stores.global_task_store import LocalGlobalTaskStore


NOW = datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc)


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
        query=DraftQueryCriteria(scope="all", platform="ozon"),
        created_at=NOW,
    )


def _controller(
    tmp_path,
    *,
    planner: _Planner,
    capabilities: dict[str, Any],
    publish_status: _PublishStatus | None = None,
    projection_writer: Any | None = None,
) -> tuple[GlobalTaskController, LocalGlobalTaskStore]:
    store = LocalGlobalTaskStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    controller = GlobalTaskController(
        store=store,
        planner=planner,
        capabilities=capabilities,
        publish_status_reader=publish_status or _PublishStatus(),
        projection_writer=projection_writer,
    )
    return controller, store


def _start(
    controller: GlobalTaskController,
    *,
    conversation_id: str = "conversation-1",
    snapshot_id: str = "",
):
    return controller.create_task(
        GlobalTaskStartRequest(
            goal="完成当前商品处理",
            draft_query_snapshot_id=snapshot_id,
        ),
        ai_work_conversation_id=conversation_id,
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


def test_get_state_resumes_persisted_planning_task_after_restart(tmp_path) -> None:
    database_path = tmp_path / "erp.sqlite3"
    initial_store = LocalGlobalTaskStore(ErpDatabase(database_path))
    initial_store.create_task(
        LocalGlobalTaskState(
            task_id="task-planning-restart",
            task_kind="global.agent.chat",
            goal="读取商品",
            status="planning",
            ai_work_conversation_id="conversation-planning-restart",
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
    )

    completed = restarted.get_state("task-planning-restart")

    assert completed.status == "completed"
    assert planner.calls == [("task-planning-restart", "")]
    assert calls == ["step_1_step-1"]
    assert restarted.store.require_task(completed.task_id) == completed


def test_get_state_resumes_running_task_without_replaying_completed_prefix(
    tmp_path,
) -> None:
    database_path = tmp_path / "erp.sqlite3"
    initial_store = LocalGlobalTaskStore(ErpDatabase(database_path))
    initial_store.create_task(
        LocalGlobalTaskState(
            task_id="task-running-restart",
            task_kind="global.agent.chat",
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
            ai_work_conversation_id="conversation-running-restart",
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
    )

    completed = restarted.get_state("task-running-restart")

    assert completed.status == "completed"
    assert completed.current_step_index == 3
    assert [step.status for step in completed.steps] == [
        "completed",
        "completed",
        "completed",
    ]
    assert calls == ["product.attributes.update", "product.images.prepare"]
    assert planner.calls == []


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


def test_planning_and_focused_capability_execution_links_are_deduplicated(
    tmp_path,
) -> None:
    projections: list[tuple[str, str, dict[str, Any]]] = []

    class PlannerWithExecutionLink:
        calls = 0

        def __call__(self, _task, _supplement):
            self.calls += 1
            return GlobalTaskPlanningOutcome(
                decision=_plan("draft.prepare_for_market"),
                execution_conversation_id="planning-execution-1",
            )

    planner = PlannerWithExecutionLink()

    def focused_capability(_task, _step):
        return CapabilityResult(
            status="completed",
            summary="目标市场准备完成。",
            result={
                "draft_id": "draft-1",
            },
            agent_execution_conversation_ids=[
                "focused-category-1",
                "focused-attributes-1",
                "planning-execution-1",
                "focused-category-1",
            ],
        )

    controller, store = _controller(
        tmp_path,
        planner=planner,  # type: ignore[arg-type]
        capabilities={"draft.prepare_for_market": focused_capability},
        projection_writer=lambda conversation_id, name, value: projections.append(
            (conversation_id, name, value)
        ),
    )

    completed = _start(controller)

    assert completed.status == "completed"
    assert completed.agent_execution_conversation_ids == [
        "planning-execution-1",
        "focused-category-1",
        "focused-attributes-1",
    ]
    assert store.require_task(completed.task_id) == completed
    linked_ids = [
        value["conversation_id"]
        for _conversation_id, name, value in projections
        if name == "global.agent_execution_link"
    ]
    assert linked_ids == [
        "planning-execution-1",
        "focused-category-1",
        "focused-attributes-1",
    ]


@pytest.mark.parametrize(
    ("result", "expected_status"),
    [
        (
            CapabilityResult(
                status="completed",
                summary="完成。",
                result={"product_id": "product-1"},
                agent_execution_conversation_ids=["focused-status-1"],
            ),
            "completed",
        ),
        (
            CapabilityResult(
                status="needs_input",
                summary="需要输入。",
                required_inputs=[
                    RequiredInput(key="brand", label="品牌", reason="缺少品牌")
                ],
                agent_execution_conversation_ids=["focused-status-1"],
            ),
            "needs_input",
        ),
        (
            CapabilityResult(
                status="in_progress",
                summary="处理中。",
                job_id="job-focused-status",
                agent_execution_conversation_ids=["focused-status-1"],
            ),
            "waiting_publish_result",
        ),
        (
            CapabilityResult(
                status="failed",
                summary="失败。",
                error={"code": "FOCUSED_FAILED", "message": "focused 失败"},
                agent_execution_conversation_ids=["focused-status-1"],
            ),
            "failed",
        ),
    ],
)
def test_every_capability_status_persists_execution_link_before_projection(
    tmp_path,
    result: CapabilityResult[Any],
    expected_status: str,
) -> None:
    store_holder: dict[str, LocalGlobalTaskStore] = {}

    def project(_conversation_id: str, name: str, value: dict[str, Any]) -> None:
        if name != "global.agent_execution_link":
            return
        persisted = store_holder["store"].require_task(value["task_id"])
        assert value["conversation_id"] in (
            persisted.agent_execution_conversation_ids
        )

    controller, store = _controller(
        tmp_path,
        planner=_Planner(_plan("product.read")),
        capabilities={"product.read": lambda _task, _step: result},
        projection_writer=project,
    )
    store_holder["store"] = store

    task = _start(controller)

    assert task.status == expected_status
    assert task.agent_execution_conversation_ids == ["focused-status-1"]
    assert store.require_task(task.task_id).agent_execution_conversation_ids == [
        "focused-status-1"
    ]


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


def test_submitted_fields_are_projected_as_bounded_user_message(tmp_path) -> None:
    projections: list[tuple[str, str, dict[str, Any]]] = []

    def capability(_task, step):
        if "brand" not in step.inputs:
            return CapabilityResult(
                status="needs_input",
                summary="请补充品牌。",
                required_inputs=[
                    RequiredInput(
                        key="brand",
                        label="品牌",
                        reason="发布需要品牌。",
                    )
                ],
            )
        return CapabilityResult(
            status="completed",
            summary="品牌已保存。",
            result={"draft_id": "draft-1"},
        )

    controller, _store = _controller(
        tmp_path,
        planner=_Planner(_plan("product.attributes.update")),
        capabilities={"product.attributes.update": capability},
        projection_writer=lambda conversation_id, name, value: projections.append(
            (conversation_id, name, value)
        ),
    )
    paused = _start(controller)
    projections.clear()

    controller.submit_input(
        GlobalTaskInputRequest(
            task_id=paused.task_id,
            inputs={"brand": "Acme"},
        )
    )

    assert projections[0] == (
        "conversation-1",
        "global.user_message",
        {"task_id": paused.task_id, "message": "已补充字段：brand"},
    )


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
        answer="",
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
    assert status.calls == ["publish-job-1"]
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

    terminal = controller.get_state(waiting.task_id)

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
    assert store.find_active_task(task.ai_work_conversation_id) is None


def test_answer_uses_snapshot_total_and_ignores_model_generated_number(
    tmp_path,
) -> None:
    planner = _Planner(
        GlobalPlanningDecision(
            action="answer",
            query_snapshot_id="snapshot-answer",
            answer="模型声称当前有 999 个草稿。",
            explanation="读取最近一次查询结果。",
        )
    )
    controller, store = _controller(
        tmp_path,
        planner=planner,
        capabilities={},
    )
    store.save_draft_query_snapshot(
        _snapshot("snapshot-answer", ["draft-1", "draft-2"], total=137)
    )

    task = _start(controller)

    assert task.status == "completed"
    assert task.assistant_message == "当前查询匹配 137 个草稿。"
    assert "999" not in task.assistant_message


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
