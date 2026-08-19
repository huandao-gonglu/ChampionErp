"""GlobalTaskController 测试：类型化计划、统一 Runtime 执行、审批与恢复。

Controller 不再有 Planner、手写 executor 或按 Capability 名称分支；
这些测试全部通过真实 ``@ai_tool`` 编译链与 ``AiToolRuntime`` 执行。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from erp_web.db import ErpDatabase
from erp_web.schemas.ai_tools import JobReferenceResult, TaskApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.global_tasks import (
    GlobalTaskApproveRequest,
    GlobalTaskIdRequest,
    GlobalTaskInputRequest,
    GlobalTaskRejectRequest,
    LocalGlobalTaskState,
)
from erp_web.services.ai_tool_catalog import AiToolBindingScope, AiToolCatalog
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputRequired,
)
from erp_web.services.global_task_controller import (
    GlobalTaskController,
    GlobalTaskControllerError,
)
from erp_web.services.task_approval import verify_execution_approval
from erp_web.stores.global_task_store import LocalGlobalTaskStore


NOW = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)
RECORDED: dict[str, list[dict[str, Any]]] = {}

# 测试用长任务 Job 类别；Job Status Reader 注册表按它解析读取器。
FAKE_JOB_TYPE = "fake"


def _record(name: str, **payload: Any) -> None:
    RECORDED.setdefault(name, []).append(payload)


def _snapshot_execution(execution: AiExecutionContext) -> dict[str, Any]:
    return {
        "business_scope": dict(execution.business_scope),
        "idempotency_context": dict(execution.idempotency_context),
        "approved_tool_call_ids": set(execution.approved_tool_call_ids),
        "allow_write": execution.allow_write,
        "budget_profile": execution.budget_profile,
        "task_run_id": execution.task_run_id,
        "approval_digest": execution.approval_digest,
        "approval_task_revision": execution.approval_task_revision,
    }


# -- 测试 Capability：全部经由 @ai_tool 编译链 ------------------------------


class FakeReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(default="", max_length=64)


class FakeReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    product_id: str = ""


@ai_tool(
    name="fake_read",
    description="读取测试商品。",
    permission="fake.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def fake_read(request: FakeReadRequest) -> FakeReadResult:
    _record("fake_read", product_id=request.product_id)
    return FakeReadResult(summary="已读取。", product_id=request.product_id)


class FakeInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clarification: str = Field(default="", max_length=200)


class FakeInputResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str


@ai_tool(
    name="fake_input",
    description="需要补充资料的测试能力。",
    permission="fake.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def fake_input(request: FakeInputRequest) -> FakeInputResult:
    if not request.clarification.strip():
        raise CapabilityInputRequired(
            "FAKE_CLARIFICATION_REQUIRED",
            "请补充说明后继续。",
            key="clarification",
            label="补充说明",
            reason="缺少说明。",
        )
    _record("fake_input", clarification=request.clarification)
    return FakeInputResult(summary=f"已补充：{request.clarification}")


class FakeWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(default="", max_length=120)


class FakeWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str


@ai_tool(
    name="fake_write_manual",
    description="不可安全重放的写能力。",
    permission="fake.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def fake_write_manual(
    request: FakeWriteRequest,
    execution: Annotated[AiExecutionContext, Injected()],
) -> FakeWriteResult:
    _record(
        "fake_write_manual",
        value=request.value,
        execution=_snapshot_execution(execution),
    )
    return FakeWriteResult(summary="写入完成。")


@ai_tool(
    name="fake_write_retry",
    description="可安全重放的写能力。",
    permission="fake.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="retry_safe",
    version="1",
)
def fake_write_retry(
    request: FakeWriteRequest,
    execution: Annotated[AiExecutionContext, Injected()],
) -> FakeWriteResult:
    _record(
        "fake_write_retry",
        value=request.value,
        execution=_snapshot_execution(execution),
    )
    return FakeWriteResult(summary="写入完成。")


@dataclass(frozen=True)
class FakeApprovalScope:
    """fake_approval 的可信边界占位；快照函数只需要 request。"""

    marker: str = "fake"


class FakeApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(min_length=1, max_length=64)
    price: str = Field(default="", max_length=64)


class FakeApprovalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str


def _fake_approval_snapshot(
    request: FakeApprovalRequest,
    scope: FakeApprovalScope,
) -> TaskApprovalSnapshot:
    del scope
    return TaskApprovalSnapshot(
        summary=f"发布摘要 {request.draft_id}",
        canonical_payload={
            "draft_id": request.draft_id,
            "price": request.price,
        },
    )


@ai_tool(
    name="fake_approval",
    description="需要显式审批的写能力。",
    permission="fake.write",
    side_effect="write",
    approval_required=True,
    approval_snapshot=_fake_approval_snapshot,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="idempotent",
    version="1",
)
def fake_approval(
    request: FakeApprovalRequest,
    scope: Annotated[FakeApprovalScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> FakeApprovalResult:
    verify_execution_approval(
        execution,
        snapshot=_fake_approval_snapshot(request, scope),
        capability_name="fake_approval",
        capability_version="1",
        stale_code="FAKE_APPROVAL_STALE",
    )
    _record(
        "fake_approval",
        draft_id=request.draft_id,
        execution=_snapshot_execution(execution),
    )
    return FakeApprovalResult(summary=f"已执行 {request.draft_id}")


@ai_tool(
    name="fake_job",
    description="提交长任务的测试能力。",
    permission="fake.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="idempotent",
    version="1",
    execution_mode="persistent_job",
)
def fake_job(
    request: FakeWriteRequest,
    execution: Annotated[AiExecutionContext, Injected()],
) -> JobReferenceResult:
    _record("fake_job", value=request.value, execution=_snapshot_execution(execution))
    return JobReferenceResult(
        job_id="job-1",
        job_type=FAKE_JOB_TYPE,
        status="queued",
        summary="已提交。",
    )


class FakeFailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FakeFailResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""


@ai_tool(
    name="fake_fail",
    description="总是失败的测试能力。",
    permission="fake.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def fake_fail(request: FakeFailRequest) -> FakeFailResult:
    raise BusinessCapabilityError("FAKE_FAILED", "稳定失败。", retryable=False)


@ai_tool(
    name="fake_outcome_unknown",
    description="副作用已发出但结果未知的测试能力。",
    permission="fake.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def fake_outcome_unknown(
    request: FakeFailRequest,
    execution: Annotated[AiExecutionContext, Injected()],
) -> FakeFailResult:
    del execution
    # 模拟外部请求已发出后超时：能力上报 outcome_unknown，禁止自动重试。
    raise BusinessCapabilityError(
        "FAKE_OUTCOME_UNKNOWN",
        "外部请求已发出，结果未知。",
        retryable=False,
        details={"outcome_unknown": True},
    )


FAKE_CAPABILITIES = (
    fake_read,
    fake_input,
    fake_write_manual,
    fake_write_retry,
    fake_approval,
    fake_job,
    fake_fail,
    fake_outcome_unknown,
)
FAKE_PERMISSIONS = frozenset({"fake.read", "fake.write"})


class _JobStatusReader:
    def __init__(self, *states: dict[str, Any]) -> None:
        self.states = deque(states)
        self.calls: list[str] = []

    def read_job_state(self, job_id: str) -> dict[str, Any]:
        self.calls.append(job_id)
        if self.states:
            return self.states.popleft()
        return {"status": "running"}


def _build_toolset(catalog: AiToolCatalog):
    return catalog.bind(
        toolset_id="global.task",
        allowed_tools=sorted(catalog.tools),
        scope=AiToolBindingScope({FakeApprovalScope: FakeApprovalScope()}),
        declared_permissions=FAKE_PERMISSIONS,
        allow_write=True,
    )


def _controller(
    tmp_path,
    *,
    catalog: AiToolCatalog | None = None,
    reader: _JobStatusReader | None = None,
) -> tuple[GlobalTaskController, LocalGlobalTaskStore]:
    RECORDED.clear()
    active_catalog = catalog or AiToolCatalog.compile(FAKE_CAPABILITIES)
    store = LocalGlobalTaskStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    controller = GlobalTaskController(
        store=store,
        catalog=active_catalog,
        task_toolset=_build_toolset(active_catalog),
        job_status_readers={FAKE_JOB_TYPE: reader or _JobStatusReader()},
    )
    return controller, store


def _start_request(*selections: Any, goal: str = "完成测试任务") -> Any:
    return SimpleNamespace(
        goal=goal,
        product_id="",
        platform="",
        steps=list(selections),
    )


class _Selection:
    """模拟 global_task_start union 分支的类型化形状。"""

    def __init__(self, capability_name: str, arguments: BaseModel) -> None:
        self.capability_name = capability_name
        self.arguments = arguments


def _seed_running_task_with_running_step(
    controller: GlobalTaskController,
    store: LocalGlobalTaskStore,
    capability_name: str,
    arguments: BaseModel,
) -> LocalGlobalTaskState:
    """模拟进程在业务步骤执行期间崩溃：步骤已持久化为 running 且无结果。"""

    task_id = "gtask_crashed"
    steps = controller._build_steps(
        task_id,
        [_Selection(capability_name, arguments)],
    )
    crashed_step = steps[0].model_copy(update={"status": "running"})
    task = LocalGlobalTaskState(
        task_id=task_id,
        goal="崩溃恢复测试",
        status="running",
        steps=[crashed_step],
        current_step_index=0,
        created_at=NOW,
        updated_at=NOW,
    )
    return store.create_task(task)


# -- 顺序执行 ----------------------------------------------------------------


def test_start_executes_typed_steps_strictly_in_order_via_runtime(tmp_path) -> None:
    controller, store = _controller(tmp_path)

    response = controller.start_task(
        _start_request(
            _Selection("fake_read", FakeReadRequest(product_id="p-1")),
            _Selection("fake_write_retry", FakeWriteRequest(value="v-1")),
        ),
        conversation_id="conversation-1",
        message_id="message-1",
    )
    task = response.task

    assert response.task_id == task.task_id
    assert task.status == "completed"
    assert task.current_step_index == 2
    assert [step.status for step in task.steps] == ["completed", "completed"]
    assert list(RECORDED["fake_read"]) == [{"product_id": "p-1"}]
    assert len(RECORDED["fake_write_retry"]) == 1
    assert store.require_task(task.task_id) == task
    # 每步 execution 携带可信业务上下文与 operation_key 幂等键。
    execution = RECORDED["fake_write_retry"][0]["execution"]
    step = task.steps[1]
    assert execution["business_scope"]["task_id"] == task.task_id
    assert execution["business_scope"]["step_id"] == step.step_id
    assert execution["business_scope"]["conversation_id"] == "conversation-1"
    assert execution["idempotency_context"]["operation_key"] == step.operation_key
    assert step.operation_key == f"global-task:{task.task_id}:step:{step.step_id}"
    assert execution["allow_write"] is True
    assert execution["budget_profile"] == "global.task"


def test_start_revalidates_arguments_with_capability_request_adapter(
    tmp_path,
) -> None:
    controller, store = _controller(tmp_path)

    class LooseArguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

        value: str = "ok"
        extra_field: str = "不受目标 Schema 允许"

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.start_task(
            _start_request(_Selection("fake_write_retry", LooseArguments()))
        )
    assert error.value.code == "GLOBAL_TASK_STEP_ARGUMENTS_INVALID"


def test_start_rejects_capability_missing_from_task_toolset(tmp_path) -> None:
    controller, _store = _controller(tmp_path)

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.start_task(
            _start_request(_Selection("not_a_capability", FakeReadRequest()))
        )
    assert error.value.code == "GLOBAL_TASK_CAPABILITY_UNAVAILABLE"


def test_failed_capability_stops_task_with_stable_code(tmp_path) -> None:
    controller, _store = _controller(tmp_path)

    response = controller.start_task(
        _start_request(
            _Selection("fake_fail", FakeFailRequest()),
            _Selection("fake_read", FakeReadRequest()),
        )
    )
    task = response.task

    assert task.status == "failed"
    assert task.error_code == "FAKE_FAILED"
    assert task.steps[0].status == "failed"
    assert task.steps[0].error is not None
    assert task.steps[0].error.code == "FAKE_FAILED"
    assert task.steps[1].status == "pending"
    assert "fake_read" not in RECORDED


def test_capability_reported_outcome_unknown_is_not_retryable(tmp_path) -> None:
    """能力在副作用发出后上报 outcome_unknown：Controller 必须按结果未知记录，
    禁止自动重试，而不是普通可重试失败。"""

    controller, _store = _controller(tmp_path)

    response = controller.start_task(
        _start_request(_Selection("fake_outcome_unknown", FakeFailRequest()))
    )
    task = response.task

    assert task.status == "failed"
    assert task.error_code == "FAKE_OUTCOME_UNKNOWN"
    step_error = task.steps[0].error
    assert step_error is not None
    assert step_error.retryable is False
    assert step_error.details.get("outcome_unknown") is True


def test_version_freeze_refuses_execution_after_capability_upgrade(
    tmp_path,
) -> None:
    controller, store = _controller(tmp_path)
    response = controller.start_task(
        _start_request(_Selection("fake_input", FakeInputRequest()))
    )
    task = response.task
    assert task.status == "needs_input"

    upgraded = tuple(
        capability for capability in FAKE_CAPABILITIES if capability is not fake_input
    )

    @ai_tool(
        name="fake_input",
        description="需要补充资料的测试能力。",
        permission="fake.read",
        side_effect="none",
        recovery_policy="retry_safe",
        version="2",
    )
    def fake_input_v2(request: FakeInputRequest) -> FakeInputResult:
        return FakeInputResult(summary="v2")

    upgraded_catalog = AiToolCatalog.compile((*upgraded, fake_input_v2))
    upgraded_controller = GlobalTaskController(
        store=store,
        catalog=upgraded_catalog,
        task_toolset=_build_toolset(upgraded_catalog),
        job_status_readers={FAKE_JOB_TYPE: _JobStatusReader()},
    )

    resumed = upgraded_controller.submit_input(
        GlobalTaskInputRequest(
            task_id=task.task_id,
            arguments={"clarification": "补充说明"},
        )
    )
    # 版本不一致必须在执行前被 Controller 拒绝，而不是静默重放。
    assert resumed.task.status == "failed"
    assert resumed.task.error_code == "GLOBAL_TASK_CAPABILITY_VERSION_MISMATCH"
    assert "fake_input" not in RECORDED


# -- 补充资料 ----------------------------------------------------------------


def test_needs_input_then_submit_input_merges_arguments_and_resumes(
    tmp_path,
) -> None:
    controller, _store = _controller(tmp_path)

    response = controller.start_task(
        _start_request(_Selection("fake_input", FakeInputRequest()))
    )
    task = response.task
    assert task.status == "needs_input"
    assert task.steps[0].status == "needs_input"
    # 待补字段必须来自 Capability 的类型化 details，而不是通用 fallback。
    assert [item.key for item in task.pending_inputs] == ["clarification"]
    assert task.pending_inputs[0].label == "补充说明"
    assert task.pending_inputs[0].reason == "缺少说明。"

    resumed = controller.submit_input(
        GlobalTaskInputRequest(
            task_id=task.task_id,
            arguments={"clarification": "需要红色包装"},
        )
    )

    assert resumed.task.status == "completed"
    assert resumed.task.pending_inputs == []
    assert RECORDED["fake_input"] == [{"clarification": "需要红色包装"}]


def test_submit_input_rejects_merged_arguments_that_fail_schema(
    tmp_path,
) -> None:
    controller, store = _controller(tmp_path)
    response = controller.start_task(
        _start_request(_Selection("fake_input", FakeInputRequest()))
    )
    before = store.require_task(response.task_id)

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.submit_input(
            GlobalTaskInputRequest(
                task_id=response.task_id,
                arguments={"clarification": "x" * 500},
            )
        )
    assert error.value.code == "GLOBAL_TASK_INPUT_SCHEMA_INVALID"
    assert store.require_task(response.task_id) == before


def test_submit_input_on_task_without_pending_input_is_rejected(tmp_path) -> None:
    controller, _store = _controller(tmp_path)
    response = controller.start_task(
        _start_request(_Selection("fake_read", FakeReadRequest()))
    )

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.submit_input(
            GlobalTaskInputRequest(
                task_id=response.task_id,
                arguments={"product_id": "p-2"},
            )
        )
    assert error.value.code == "GLOBAL_TASK_INPUT_NOT_EXPECTED"


# -- 审批 --------------------------------------------------------------------

APPROVER = "local-ui:test"


def _start_approval_task(controller) -> LocalGlobalTaskState:
    response = controller.start_task(
        _start_request(
            _Selection(
                "fake_approval",
                FakeApprovalRequest(draft_id="draft-9", price="199"),
            )
        ),
        conversation_id="conversation-9",
        message_id="message-9",
    )
    return response.task


def test_approval_gate_persists_digest_bound_to_step_and_revision(
    tmp_path,
) -> None:
    controller, _store = _controller(tmp_path)

    task = _start_approval_task(controller)

    assert task.status == "pending_approval"
    assert task.steps[0].status == "pending"
    assert "fake_approval" not in RECORDED
    approval = task.pending_approval
    assert approval is not None
    step = task.steps[0]
    assert approval.step_id == step.step_id
    assert approval.capability_name == "fake_approval"
    assert approval.capability_version == "1"
    assert approval.operation_key == step.operation_key
    # 审批请求基于最近一次已持久化 revision 构造，随后 pending_approval
    # 状态本身再 CAS 递增一次；绑定关系必须严格可预测。
    assert approval.task_revision == task.revision - 1
    # payload 是服务端快照：摘要 + 冻结参数，全部由服务端生成。
    assert approval.payload["summary"] == "发布摘要 draft-9"
    assert approval.payload["canonical_payload"] == {
        "draft_id": "draft-9",
        "price": "199",
    }
    assert len(approval.digest) == 64


def test_model_cannot_forge_approval_display_summary(tmp_path) -> None:
    controller, _store = _controller(tmp_path)

    # 审批展示摘要只能由服务端快照派生：模型在步骤参数里夹带的
    # summary/approval 字段不属于能力 request schema，必须在入口被拒绝。
    class ForgedApprovalArguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

        draft_id: str = "draft-9"
        price: str = "199"
        summary: str = "模型伪造的摘要"
        approval: dict = {"summary": "模型伪造的摘要"}

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.start_task(
            _start_request(
                _Selection("fake_approval", ForgedApprovalArguments())
            )
        )
    assert error.value.code == "GLOBAL_TASK_STEP_ARGUMENTS_INVALID"
    assert "fake_approval" not in RECORDED

    # 正常路径下，展示内容依然是服务端生成的快照，与伪造输入无关。
    task = _start_approval_task(controller)
    approval = task.pending_approval
    assert approval is not None
    assert approval.payload["summary"] == "发布摘要 draft-9"
    assert "伪造" not in approval.payload["summary"]


def test_approve_executes_with_approved_call_id_and_confirmed_at(
    tmp_path,
) -> None:
    controller, _store = _controller(tmp_path)
    task = _start_approval_task(controller)

    approved = controller.approve_task(
        GlobalTaskApproveRequest(task_id=task.task_id),
        approver=APPROVER,
        conversation_id="conversation-9",
        message_id="message-10",
    )

    assert approved.task.status == "completed"
    assert approved.task.pending_approval is None
    # 审批决定审计记录：审批人、决定、digest 与任务版本。
    record = approved.task.steps[0].approval
    assert record is not None
    assert record.approver == APPROVER
    assert record.decision == "approved"
    execution = RECORDED["fake_approval"][0]["execution"]
    # Runtime 审批闸门要求 call_id 在可信 approved_tool_call_ids 中，
    # call_id 由 task_id/step_id/execution_id 组成，模型无法伪造。
    approved_ids = execution["approved_tool_call_ids"]
    assert len(approved_ids) == 1
    call_id = next(iter(approved_ids))
    assert call_id.startswith(f"{task.task_id}:{task.steps[0].step_id}:")
    assert execution["business_scope"]["approval_confirmed_at"]
    assert execution["business_scope"]["approver"] == APPROVER
    assert execution["idempotency_context"]["operation_key"] == (
        task.steps[0].operation_key
    )
    # 执行上下文携带可信审批 digest 与任务版本，供 Capability 重核。
    approval = task.pending_approval
    assert approval is not None
    assert execution["approval_digest"] == approval.digest
    assert execution["approval_task_revision"] == approval.task_revision


def test_approve_without_identity_is_rejected(tmp_path) -> None:
    controller, store = _controller(tmp_path)
    task = _start_approval_task(controller)

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.approve_task(
            GlobalTaskApproveRequest(task_id=task.task_id),
            approver="",
        )
    assert error.value.code == "GLOBAL_TASK_APPROVAL_IDENTITY_REQUIRED"
    assert store.require_task(task.task_id).status == "pending_approval"
    assert "fake_approval" not in RECORDED


def test_approve_with_mismatched_step_id_is_rejected_without_execution(
    tmp_path,
) -> None:
    controller, store = _controller(tmp_path)
    task = _start_approval_task(controller)

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.approve_task(
            GlobalTaskApproveRequest(task_id=task.task_id, step_id="step_other"),
            approver=APPROVER,
        )
    assert error.value.code == "GLOBAL_TASK_APPROVAL_STEP_MISMATCH"
    assert store.require_task(task.task_id).status == "pending_approval"
    assert "fake_approval" not in RECORDED


def test_approve_after_task_modified_is_rejected_as_stale_revision(
    tmp_path,
) -> None:
    controller, store = _controller(tmp_path)
    task = _start_approval_task(controller)

    # 直接篡改已持久化步骤参数（模拟审批创建后的另一次写入）；
    # 任务 revision 随之前进，原审批版本立即过期。
    persisted = store.require_task(task.task_id)
    step = persisted.steps[0]
    tampered_arguments = dict(step.arguments)
    tampered_arguments["price"] = "1"
    tampered = persisted.model_copy(
        update={"steps": [step.model_copy(update={"arguments": tampered_arguments})]}
    )
    store.save_task(tampered)

    with pytest.raises(GlobalTaskControllerError) as stale:
        controller.approve_task(
            GlobalTaskApproveRequest(task_id=task.task_id),
            approver=APPROVER,
        )
    assert stale.value.code == "GLOBAL_TASK_APPROVAL_REVISION_STALE"
    assert "fake_approval" not in RECORDED


def test_reject_approval_fails_task_with_stable_reason(tmp_path) -> None:
    controller, _store = _controller(tmp_path)
    task = _start_approval_task(controller)

    rejected = controller.reject_task(
        GlobalTaskRejectRequest(task_id=task.task_id, reason="价格不对"),
        approver=APPROVER,
    )

    assert rejected.task.status == "failed"
    assert rejected.task.error_code == "GLOBAL_TASK_APPROVAL_REJECTED"
    assert rejected.task.error_message == "价格不对"
    assert rejected.task.pending_approval is None
    record = rejected.task.steps[0].approval
    assert record is not None
    assert record.approver == APPROVER
    assert record.decision == "rejected"
    assert record.reason == "价格不对"
    assert "fake_approval" not in RECORDED


def test_reject_without_identity_is_rejected(tmp_path) -> None:
    controller, store = _controller(tmp_path)
    task = _start_approval_task(controller)

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.reject_task(
            GlobalTaskRejectRequest(task_id=task.task_id, reason="x"),
            approver="",
        )
    assert error.value.code == "GLOBAL_TASK_APPROVAL_IDENTITY_REQUIRED"
    assert store.require_task(task.task_id).status == "pending_approval"


def test_approve_on_task_without_pending_approval_is_rejected(tmp_path) -> None:
    controller, _store = _controller(tmp_path)
    response = controller.start_task(
        _start_request(_Selection("fake_read", FakeReadRequest()))
    )

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.approve_task(
            GlobalTaskApproveRequest(task_id=response.task_id),
            approver=APPROVER,
        )
    assert error.value.code == "GLOBAL_TASK_APPROVAL_NOT_EXPECTED"


# -- 长任务 ------------------------------------------------------------------


def test_persistent_job_moves_task_to_in_progress_with_generic_active_job(
    tmp_path,
) -> None:
    controller, _store = _controller(tmp_path)

    response = controller.start_task(
        _start_request(_Selection("fake_job", FakeWriteRequest(value="go")))
    )
    task = response.task

    assert task.status == "in_progress"
    assert task.active_job is not None
    assert task.active_job.job_id == "job-1"
    assert task.active_job.capability_name == "fake_job"
    assert task.active_job.step_id == task.steps[0].step_id
    assert task.steps[0].status == "running"
    assert task.assistant_message == "已提交。"


def test_refresh_completes_task_when_job_succeeds_and_advances(
    tmp_path,
) -> None:
    reader = _JobStatusReader({"status": "running"}, {"status": "success"})
    controller, _store = _controller(tmp_path, reader=reader)
    response = controller.start_task(
        _start_request(
            _Selection("fake_job", FakeWriteRequest(value="go")),
            _Selection("fake_read", FakeReadRequest(product_id="p-9")),
        )
    )
    task = response.task
    assert task.status == "in_progress"

    unchanged = controller.refresh_task(task.task_id)
    assert unchanged.task.status == "in_progress"

    finished = controller.refresh_task(task.task_id)
    assert finished.task.status == "completed"
    assert finished.task.active_job is None
    assert finished.task.steps[0].status == "completed"
    assert finished.task.steps[0].result == {
        "job_id": "job-1",
        "job_status": "success",
    }
    assert finished.task.steps[1].status == "completed"
    assert reader.calls == ["job-1", "job-1"]


def test_refresh_fails_task_with_generic_job_error(tmp_path) -> None:
    reader = _JobStatusReader({"status": "failed", "error": "平台拒绝。"})
    controller, _store = _controller(tmp_path, reader=reader)
    response = controller.start_task(
        _start_request(_Selection("fake_job", FakeWriteRequest(value="go")))
    )

    finished = controller.refresh_task(response.task_id)

    assert finished.task.status == "failed"
    assert finished.task.error_code == "GLOBAL_TASK_JOB_FAILED"
    assert finished.task.error_message == "平台拒绝。"
    assert finished.task.active_job is None


def test_refresh_is_noop_for_non_in_progress_tasks(tmp_path) -> None:
    controller, _store = _controller(tmp_path)
    response = controller.start_task(
        _start_request(_Selection("fake_read", FakeReadRequest()))
    )

    refreshed = controller.refresh_task(response.task_id)

    assert refreshed.task == response.task


# -- 取消 --------------------------------------------------------------------


def test_cancel_is_idempotent_on_terminal_tasks(tmp_path) -> None:
    controller, _store = _controller(tmp_path)
    response = controller.start_task(
        _start_request(_Selection("fake_read", FakeReadRequest()))
    )

    cancelled = controller.cancel_task(
        GlobalTaskIdRequest(task_id=response.task_id)
    )

    assert cancelled.task == response.task


def test_cancel_refuses_in_progress_job_submission(tmp_path) -> None:
    controller, _store = _controller(tmp_path)
    response = controller.start_task(
        _start_request(_Selection("fake_job", FakeWriteRequest(value="go")))
    )

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.cancel_task(GlobalTaskIdRequest(task_id=response.task_id))
    assert error.value.code == "GLOBAL_TASK_JOB_ALREADY_SUBMITTED"


def test_cancel_needs_input_task(tmp_path) -> None:
    controller, _store = _controller(tmp_path)
    response = controller.start_task(
        _start_request(_Selection("fake_input", FakeInputRequest()))
    )
    assert response.task.status == "needs_input"

    cancelled = controller.cancel_task(
        GlobalTaskIdRequest(task_id=response.task_id)
    )

    assert cancelled.task.status == "cancelled"
    assert cancelled.task.pending_inputs == []


def test_cancel_refuses_when_running_step_outcome_unknown(tmp_path) -> None:
    controller, store = _controller(tmp_path)
    seeded = _seed_running_task_with_running_step(
        controller,
        store,
        "fake_write_manual",
        FakeWriteRequest(value="v"),
    )

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.cancel_task(GlobalTaskIdRequest(task_id=seeded.task_id))
    assert error.value.code == "GLOBAL_TASK_STEP_OUTCOME_UNKNOWN"


# -- 恢复 --------------------------------------------------------------------


def test_recovery_fails_manual_policy_step_without_replaying_side_effect(
    tmp_path,
) -> None:
    controller, store = _controller(tmp_path)
    seeded = _seed_running_task_with_running_step(
        controller,
        store,
        "fake_write_manual",
        FakeWriteRequest(value="v"),
    )

    resumed = controller.resume_task(seeded.task_id)

    assert resumed.status == "failed"
    assert resumed.error_code == "GLOBAL_TASK_STEP_RECOVERY_UNSAFE"
    # 结果不明确的写步骤绝不能在恢复时被静默重放。
    assert "fake_write_manual" not in RECORDED


def test_recovery_replays_retry_safe_step_with_same_operation_key(
    tmp_path,
) -> None:
    controller, store = _controller(tmp_path)
    seeded = _seed_running_task_with_running_step(
        controller,
        store,
        "fake_write_retry",
        FakeWriteRequest(value="v"),
    )
    original_operation_key = seeded.steps[0].operation_key

    resumed = controller.resume_task(seeded.task_id)

    assert resumed.status == "completed"
    execution = RECORDED["fake_write_retry"][0]["execution"]
    assert execution["idempotency_context"]["operation_key"] == (
        original_operation_key
    )
    # 恢复不依赖会话历史：business_scope 不包含 conversation/message。
    assert "conversation_id" not in execution["business_scope"]
    assert "message_id" not in execution["business_scope"]


def test_recovery_refreshes_in_progress_job_without_conversation(
    tmp_path,
) -> None:
    reader = _JobStatusReader({"status": "success"})
    controller, store = _controller(tmp_path, reader=reader)
    response = controller.start_task(
        _start_request(_Selection("fake_job", FakeWriteRequest(value="go")))
    )
    assert response.task.status == "in_progress"
    # 模拟重启后由恢复 worker 推进。
    store.require_task(response.task_id)

    resumed = controller.resume_task(response.task_id)

    assert resumed.status == "completed"
    assert resumed.active_job is None


def test_recover_unfinished_tasks_processes_recoverable_tasks(
    tmp_path,
) -> None:
    controller, store = _controller(tmp_path)
    crashed = _seed_running_task_with_running_step(
        controller,
        store,
        "fake_write_retry",
        FakeWriteRequest(value="v"),
    )

    recovered = controller.recover_unfinished_tasks()

    assert [task.task_id for task in recovered] == [crashed.task_id]
    assert recovered[0].status == "completed"
    # 第二次恢复不再返回已终结任务。
    assert controller.recover_unfinished_tasks() == []


# -- 读取 --------------------------------------------------------------------


def test_get_state_is_pure_read_without_execution(tmp_path) -> None:
    controller, _store = _controller(tmp_path)
    response = controller.start_task(
        _start_request(_Selection("fake_input", FakeInputRequest()))
    )
    RECORDED.clear()

    state = controller.get_state(response.task_id)
    via_tool = controller.get_task(GlobalTaskIdRequest(task_id=response.task_id))

    assert state == response.task
    assert via_tool.task == response.task
    assert RECORDED == {}


def test_missing_task_raises_stable_not_found(tmp_path) -> None:
    controller, _store = _controller(tmp_path)

    with pytest.raises(Exception) as error:
        controller.get_task(GlobalTaskIdRequest(task_id="gtask_missing"))
    assert getattr(error.value, "code", "") == "GLOBAL_TASK_NOT_FOUND"
