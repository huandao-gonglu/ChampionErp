"""GlobalTaskController 测试：Deferred 受理、worker 唯一执行、审批与恢复。

新生命周期：``accept_deferred_task`` 同事务创建 Task 与 provisional link，
首次 Deferred history 原子提交（link ready）之后，recovery worker 通过既有
execution lease 独占推进全部步骤；补资料/批准/拒绝/取消只改变业务状态。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import ModelRequest, UserPromptPart

from erp_web.db import ErpDatabase
from erp_web.schemas.ai_tools import JobReferenceResult, TaskApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.global_tasks import (
    GlobalTaskAcceptance,
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
from erp_web.stores.pydantic_deferred_task_link_store import (
    PydanticDeferredTaskLinkStore,
)


NOW = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)
RECORDED: dict[str, list[dict[str, Any]]] = {}

# 测试用长任务 Job 类别；Job Status Reader 注册表按它解析读取器。
FAKE_JOB_TYPE = "fake"

CONVERSATION = "conversation_global_chat_" + "c" * 32


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


class FakeImageInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_ids: list[str] = Field(default_factory=list, max_length=10)


class FakeImageInputResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    asset_ids: list[str]


@ai_tool(
    name="fake_image_input",
    description="需要 string_list 图片资产的测试能力。",
    permission="fake.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def fake_image_input(request: FakeImageInputRequest) -> FakeImageInputResult:
    if not request.asset_ids:
        raise CapabilityInputRequired(
            "FAKE_ASSETS_REQUIRED",
            "请选择要用于发布的图片资产。",
            key="asset_ids",
            label="图片资产",
            reason="请从已就绪的图片池中选择。",
            input_type="string_list",
        )
    _record("fake_image_input", asset_ids=list(request.asset_ids))
    return FakeImageInputResult(
        summary=f"已选择 {len(request.asset_ids)} 张图片。",
        asset_ids=list(request.asset_ids),
    )


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


class FakeProfilePatch(BaseModel):
    """模拟商品主档补丁：多字段默认空值，必须以部分补丁执行。"""

    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(default="", max_length=64)
    stock: str = Field(default="", max_length=64)
    brand: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=120)
    cost: str = Field(default="", max_length=64)
    weight_kg: str = Field(default="", max_length=64)


class FakeProfilePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: FakeProfilePatch


class FakeProfilePatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = ""
    changed_fields: tuple[str, ...] = ()


@ai_tool(
    name="fake_profile_patch",
    description="多字段部分补丁测试能力。",
    permission="fake.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="retry_safe",
    version="1",
)
def fake_profile_patch(
    request: FakeProfilePatchRequest,
    execution: Annotated[AiExecutionContext, Injected()],
) -> FakeProfilePatchResult:
    del execution
    # 与 product_save 一致：写能力按 exclude_unset 应用部分补丁。
    patch = request.product.model_dump(mode="json", exclude_unset=True)
    _record("fake_profile_patch", patch=patch)
    return FakeProfilePatchResult(
        product_id=request.product.product_id,
        changed_fields=tuple(
            sorted(key for key in patch if key != "product_id")
        ),
    )


class FakeNestedInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(default="", max_length=64)
    provided_attributes: dict[str, str] = Field(default_factory=dict)


class FakeNestedInputResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    provided_attributes: dict[str, str] = Field(default_factory=dict)


@ai_tool(
    name="fake_nested_input",
    description="需要嵌套 provided_attributes 补资料的测试能力。",
    permission="fake.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def fake_nested_input(request: FakeNestedInputRequest) -> FakeNestedInputResult:
    if not request.provided_attributes:
        raise CapabilityInputRequired(
            "FAKE_ATTRIBUTES_REQUIRED",
            "请补充属性后继续。",
            key="85",
            label="品牌属性",
            reason="缺少必填属性。",
            input_owner="provided_attributes",
        )
    _record(
        "fake_nested_input",
        provided_attributes=dict(request.provided_attributes),
    )
    return FakeNestedInputResult(
        summary="已补充属性。",
        provided_attributes=dict(request.provided_attributes),
    )


FAKE_CAPABILITIES = (
    fake_read,
    fake_input,
    fake_image_input,
    fake_write_manual,
    fake_write_retry,
    fake_approval,
    fake_job,
    fake_fail,
    fake_outcome_unknown,
    fake_profile_patch,
    fake_nested_input,
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
    approval_mode: str = "ask",
) -> tuple[GlobalTaskController, LocalGlobalTaskStore]:
    RECORDED.clear()
    active_catalog = catalog or AiToolCatalog.compile(FAKE_CAPABILITIES)
    db = ErpDatabase(tmp_path / "erp.sqlite3")
    store = LocalGlobalTaskStore(db)
    links = PydanticDeferredTaskLinkStore(db)
    controller = GlobalTaskController(
        store=store,
        catalog=active_catalog,
        task_toolset=_build_toolset(active_catalog),
        job_status_readers={FAKE_JOB_TYPE: reader or _JobStatusReader()},
        deferred_links=links,
        approval_mode_loader=lambda: approval_mode,  # type: ignore[return-value]
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


_CALL_COUNTER = {"value": 0}


def _next_call_id() -> str:
    _CALL_COUNTER["value"] += 1
    return f"tool-call-{_CALL_COUNTER['value']}"


def _accept_task(
    controller: GlobalTaskController,
    request: Any,
    *,
    conversation_id: str = CONVERSATION,
) -> GlobalTaskAcceptance:
    """Deferred 握手：只创建 Task + provisional link，不执行任何步骤。"""

    return controller.accept_deferred_task(
        request,
        conversation_id=conversation_id,
        request_run_id="run-1",
        tool_call_id=_next_call_id(),
        message_id="message-1",
    )


def _commit_first_history(
    controller: GlobalTaskController,
    acceptance: GlobalTaskAcceptance,
) -> None:
    """模拟协议层在同一事务提交首次 Deferred history 并置 link ready。"""

    assert controller.deferred_links is not None
    controller.deferred_links.commit_initial_deferred_history(
        acceptance.conversation_id,
        [ModelRequest(parts=[UserPromptPart("创建任务")])],
        link_id=acceptance.link_id,
        request_run_id="run-1",
        encoded_chunks=[],
    )


def _start_and_run(
    controller: GlobalTaskController,
    request: Any,
    *,
    conversation_id: str = CONVERSATION,
) -> LocalGlobalTaskState:
    """完整生命周期：受理 → 首次 history 提交 → worker 推进。"""

    acceptance = _accept_task(
        controller,
        request,
        conversation_id=conversation_id,
    )
    _commit_first_history(controller, acceptance)
    return controller.resume_task(acceptance.task_id)


def _seed_running_task_with_running_step(
    controller: GlobalTaskController,
    store: LocalGlobalTaskStore,
    capability_name: str,
    arguments: BaseModel,
) -> LocalGlobalTaskState:
    """模拟进程在业务步骤执行期间崩溃：步骤已持久化为 running 且无结果。

    报告 R-07：夹具使用正式 Task+link 创建流程（accept_deferred_task →
    首次 history 提交置 link ready），再改写步骤状态模拟崩溃现场；不再直接
    创建无 Deferred link 的任务。
    """

    acceptance = _accept_task(
        controller,
        _start_request(
            _Selection(capability_name, arguments),
            goal="崩溃恢复测试",
        ),
    )
    _commit_first_history(controller, acceptance)
    task = store.require_task(acceptance.task_id)
    steps = list(task.steps)
    steps[0] = steps[0].model_copy(update={"status": "running"})
    return store.save_task(task.model_copy(update={"steps": steps}))


# -- Deferred 受理与 ready 屏障 ----------------------------------------------


def test_accept_creates_task_and_provisional_link_without_execution(
    tmp_path,
) -> None:
    controller, store = _controller(tmp_path)

    acceptance = _accept_task(
        controller,
        _start_request(_Selection("fake_read", FakeReadRequest(product_id="p-1"))),
    )

    assert acceptance.ok is True
    assert acceptance.conversation_id == CONVERSATION
    task = store.require_task(acceptance.task_id)
    assert task.status == "running"
    assert task.current_step_index == 0
    # 受理阶段不执行任何步骤。
    assert RECORDED == {}
    assert controller.deferred_links is not None
    link = controller.deferred_links.get(acceptance.link_id)
    assert link is not None
    assert link.link_status == "awaiting_history"
    assert link.ready_at == ""
    assert link.task_id == acceptance.task_id
    assert link.tool_call_id == acceptance.tool_call_id


def test_ready_barrier_blocks_worker_until_first_history_committed(
    tmp_path,
) -> None:
    controller, store = _controller(tmp_path)
    acceptance = _accept_task(
        controller,
        _start_request(_Selection("fake_read", FakeReadRequest(product_id="p-1"))),
    )

    # provisional link：worker 不得领取执行。
    blocked = controller.resume_task(acceptance.task_id)
    assert blocked.status == "running"
    assert RECORDED == {}
    assert controller.recover_unfinished_tasks() == []
    assert RECORDED == {}

    _commit_first_history(controller, acceptance)
    resumed = controller.resume_task(acceptance.task_id)
    assert resumed.status == "completed"
    assert list(RECORDED["fake_read"]) == [{"product_id": "p-1"}]
    assert store.require_task(acceptance.task_id) == resumed


def test_second_deferred_task_on_same_conversation_is_rejected(
    tmp_path,
) -> None:
    controller, store = _controller(tmp_path)
    request = _start_request(_Selection("fake_read", FakeReadRequest()))

    first = _accept_task(controller, request)
    with pytest.raises(GlobalTaskControllerError) as error:
        controller.accept_deferred_task(
            request,
            conversation_id=CONVERSATION,
            request_run_id="run-1",
            tool_call_id=_next_call_id(),
        )
    assert error.value.code == "GLOBAL_TASK_DEFERRED_ALREADY_PENDING"

    # 拒绝不得留下孤儿 Task 或 link。
    assert store.list_unfinished_tasks()[0].task_id == first.task_id
    assert len(store.list_unfinished_tasks()) == 1

    # 另一个 conversation 不受影响。
    other = controller.accept_deferred_task(
        request,
        conversation_id="conversation_global_chat_" + "d" * 32,
        request_run_id="run-2",
        tool_call_id=_next_call_id(),
    )
    assert other.task_id != first.task_id

    # 首个任务终结后，同一 conversation 允许新的 Deferred。
    _commit_first_history(controller, first)
    controller.resume_task(first.task_id)
    assert store.require_task(first.task_id).status == "completed"
    resolved_link = controller.deferred_links.get(first.link_id)
    assert resolved_link is not None
    # 任务终结但 continuation 尚未提交：link 仍是 active，仍拒绝新任务。
    with pytest.raises(GlobalTaskControllerError):
        controller.accept_deferred_task(
            request,
            conversation_id=CONVERSATION,
            request_run_id="run-3",
            tool_call_id=_next_call_id(),
        )


def test_accept_missing_context_is_rejected(tmp_path) -> None:
    controller, _store = _controller(tmp_path)

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.accept_deferred_task(
            _start_request(_Selection("fake_read", FakeReadRequest())),
            conversation_id="",
            request_run_id="run-1",
            tool_call_id="call-x",
        )
    assert error.value.code == "TASK_CONTROL_CONTEXT_MISSING"


# -- 顺序执行（worker 唯一执行路径） ----------------------------------------


def test_worker_executes_typed_steps_strictly_in_order_via_runtime(
    tmp_path,
) -> None:
    controller, store = _controller(tmp_path)

    task = _start_and_run(
        controller,
        _start_request(
            _Selection("fake_read", FakeReadRequest(product_id="p-1")),
            _Selection("fake_write_retry", FakeWriteRequest(value="v-1")),
        ),
    )

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
    assert execution["business_scope"]["conversation_id"] == CONVERSATION
    assert execution["idempotency_context"]["operation_key"] == step.operation_key
    assert step.operation_key == f"global-task:{task.task_id}:step:{step.step_id}"
    assert execution["allow_write"] is True
    assert execution["budget_profile"] == "global.task"


def test_worker_advances_all_steps_in_single_recovery_pass(tmp_path) -> None:
    controller, store = _controller(tmp_path)
    acceptance = _accept_task(
        controller,
        _start_request(
            _Selection("fake_read", FakeReadRequest(product_id="p-1")),
            _Selection("fake_read", FakeReadRequest(product_id="p-2")),
        ),
    )
    _commit_first_history(controller, acceptance)

    resumed = controller.resume_task(acceptance.task_id)

    assert resumed.status == "completed"
    assert resumed.current_step_index == 2
    assert list(RECORDED["fake_read"]) == [
        {"product_id": "p-1"},
        {"product_id": "p-2"},
    ]
    assert store.require_task(acceptance.task_id) == resumed


def test_accept_revalidates_arguments_with_capability_request_adapter(
    tmp_path,
) -> None:
    controller, store = _controller(tmp_path)

    class LooseArguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

        value: str = "ok"
        extra_field: str = "不受目标 Schema 允许"

    # 部分补丁语义下，未显式设置的字段不会进入持久化参数；
    # 显式设置的不允许字段仍必须被目标 request adapter 拒绝。
    with pytest.raises(GlobalTaskControllerError) as error:
        _accept_task(
            controller,
            _start_request(
                _Selection(
                    "fake_write_retry",
                    LooseArguments(extra_field="不受目标 Schema 允许"),
                )
            ),
        )
    assert error.value.code == "GLOBAL_TASK_STEP_ARGUMENTS_INVALID"
    # 校验失败不得留下任何 Task 或 link。
    assert store.list_unfinished_tasks() == []


def test_partial_patch_arguments_persist_without_default_expansion(
    tmp_path,
) -> None:
    """只提供 product_id + stock 时，未提供字段不得展开成显式空值。

    覆盖：创建时持久化参数、Task store round-trip、worker 重新校验执行。
    """

    controller, store = _controller(tmp_path)

    acceptance = _accept_task(
        controller,
        _start_request(
            _Selection(
                "fake_profile_patch",
                FakeProfilePatchRequest(
                    product={"product_id": "p-1", "stock": "200"}
                ),
            ),
        ),
    )
    task = store.require_task(acceptance.task_id)
    # 持久化参数只包含实际提供的字段（嵌套 model 的 fields_set 保留）。
    assert task.steps[0].arguments == {
        "product": {"product_id": "p-1", "stock": "200"}
    }

    _commit_first_history(controller, acceptance)
    resumed = controller.resume_task(acceptance.task_id)
    assert resumed.status == "completed"
    # worker 重新校验不会重新引入默认空字段；执行的就是部分补丁。
    assert RECORDED["fake_profile_patch"][0]["patch"] == {
        "product_id": "p-1",
        "stock": "200",
    }
    assert resumed.steps[0].result is not None
    assert resumed.steps[0].result["changed_fields"] == ["stock"]

    # Task store round-trip 后仍只包含实际提供字段。
    reloaded = store.require_task(acceptance.task_id)
    assert reloaded.steps[0].arguments == {
        "product": {"product_id": "p-1", "stock": "200"}
    }


def test_partial_patch_does_not_overwrite_unprovided_fields(tmp_path) -> None:
    """更新库存不得把 brand/model/cost/weight 等未提供字段变成空值。

    模拟商品主档已有完整资料；部分补丁执行后，已有字段保持原值。
    """

    controller, store = _controller(tmp_path)
    existing_profile = {
        "product_id": "p-existing",
        "stock": "5",
        "brand": "金诚海蓝",
        "model": "bxt-cq2",
        "cost": "9",
        "weight_kg": "0.04",
    }

    acceptance = _accept_task(
        controller,
        _start_request(
            _Selection(
                "fake_profile_patch",
                FakeProfilePatchRequest(
                    product={"product_id": "p-existing", "stock": "200"}
                ),
            ),
        ),
    )
    _commit_first_history(controller, acceptance)
    resumed = controller.resume_task(acceptance.task_id)
    assert resumed.status == "completed"

    patch = RECORDED["fake_profile_patch"][0]["patch"]
    # 执行收到的补丁只有 stock（和定位键），不含任何空默认字段。
    assert set(patch) == {"product_id", "stock"}
    merged = {**existing_profile, **patch}
    assert merged["stock"] == "200"
    assert merged["brand"] == "金诚海蓝"
    assert merged["model"] == "bxt-cq2"
    assert merged["cost"] == "9"
    assert merged["weight_kg"] == "0.04"


def test_accept_rejects_capability_missing_from_task_toolset(tmp_path) -> None:
    controller, _store = _controller(tmp_path)

    with pytest.raises(GlobalTaskControllerError) as error:
        _accept_task(
            controller,
            _start_request(_Selection("not_a_capability", FakeReadRequest())),
        )
    assert error.value.code == "GLOBAL_TASK_CAPABILITY_UNAVAILABLE"


def test_failed_capability_stops_task_with_stable_code(tmp_path) -> None:
    controller, _store = _controller(tmp_path)

    task = _start_and_run(
        controller,
        _start_request(
            _Selection("fake_fail", FakeFailRequest()),
            _Selection("fake_read", FakeReadRequest()),
        ),
    )

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

    task = _start_and_run(
        controller,
        _start_request(_Selection("fake_outcome_unknown", FakeFailRequest())),
    )

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
    acceptance = _accept_task(
        controller,
        _start_request(_Selection("fake_input", FakeInputRequest())),
    )
    _commit_first_history(controller, acceptance)
    task = controller.resume_task(acceptance.task_id)
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
        deferred_links=controller.deferred_links,
    )

    resumed_state = upgraded_controller.submit_input(
        GlobalTaskInputRequest(
            task_id=task.task_id,
            arguments={"clarification": "补充说明"},
        )
    ).task
    # submit_input 只做状态变更，执行在 worker。
    assert resumed_state.status == "running"
    resumed = upgraded_controller.resume_task(task.task_id)
    # 版本不一致必须在执行前被 Controller 拒绝，而不是静默重放。
    assert resumed.status == "failed"
    assert resumed.error_code == "GLOBAL_TASK_CAPABILITY_VERSION_MISMATCH"
    assert "fake_input" not in RECORDED


# -- 补充资料 ----------------------------------------------------------------


def test_needs_input_then_submit_input_merges_arguments_and_worker_resumes(
    tmp_path,
) -> None:
    controller, _store = _controller(tmp_path)

    task = _start_and_run(
        controller,
        _start_request(_Selection("fake_input", FakeInputRequest())),
    )
    assert task.status == "needs_input"
    assert task.steps[0].status == "needs_input"
    # 待补字段必须来自 Capability 的类型化 details，而不是通用 fallback。
    assert [item.key for item in task.pending_inputs] == ["clarification"]
    assert task.pending_inputs[0].label == "补充说明"
    assert task.pending_inputs[0].reason == "缺少说明。"

    submitted = controller.submit_input(
        GlobalTaskInputRequest(
            task_id=task.task_id,
            arguments={"clarification": "需要红色包装"},
        )
    )
    # 补资料只改变业务状态：任务回到 running，等待 worker 领取。
    assert submitted.task.status == "running"
    assert submitted.task.pending_inputs == []
    assert RECORDED.get("fake_input") is None

    resumed = controller.resume_task(task.task_id)
    assert resumed.status == "completed"
    assert RECORDED["fake_input"] == [{"clarification": "需要红色包装"}]


def test_needs_input_string_list_accepts_list_and_rejects_plain_string(
    tmp_path,
) -> None:
    """报告 A-08（纵向）：string_list 待补字段必须按列表提交。

    真实能力（如 prepare_product_images）以 ``input_type="string_list"`` 要求
    ``asset_ids``。前端旧实现把所有类型按字符串提交，会在这里触发 Pydantic
    list_type 校验失败。本测试固化任务级契约：待补字段声明为 string_list，
    提交列表可合并并继续执行；提交普通字符串被 schema 拒绝。
    """

    controller, store = _controller(tmp_path)
    task = _start_and_run(
        controller,
        _start_request(_Selection("fake_image_input", FakeImageInputRequest())),
    )
    assert task.status == "needs_input"
    assert [item.key for item in task.pending_inputs] == ["asset_ids"]
    assert task.pending_inputs[0].input_type == "string_list"

    # 旧前端的字符串提交：合并后不满足 list[str] schema，被确定性拒绝。
    with pytest.raises(GlobalTaskControllerError) as string_error:
        controller.submit_input(
            GlobalTaskInputRequest(
                task_id=task.task_id,
                arguments={"asset_ids": "image-1, image-2"},
            )
        )
    assert string_error.value.code == "GLOBAL_TASK_INPUT_SCHEMA_INVALID"
    assert store.require_task(task.task_id).status == "needs_input"

    # 类型化列表提交：合并通过，任务回到 running 并由 worker 继续。
    submitted = controller.submit_input(
        GlobalTaskInputRequest(
            task_id=task.task_id,
            arguments={"asset_ids": ["image-1", "image-2"]},
        )
    )
    assert submitted.task.status == "running"

    resumed = controller.resume_task(task.task_id)
    assert resumed.status == "completed"
    assert RECORDED["fake_image_input"] == [
        {"asset_ids": ["image-1", "image-2"]}
    ]
    # 成功步骤的业务结果（含所选资产）随任务状态持久化。
    assert resumed.steps[0].result is not None
    assert resumed.steps[0].result["asset_ids"] == ["image-1", "image-2"]


def test_needs_input_nested_owner_merges_into_provided_attributes(
    tmp_path,
) -> None:
    """嵌套补资料：input_owner 决定提交字段合并到 provided_attributes 路径。

    旧实现把属性 ID 作为顶层参数提交、Controller 只做顶层浅合并，导致
    GLOBAL_TASK_INPUT_SCHEMA_INVALID。修复后 pending input 携带 input_owner，
    Controller 按路径合并，同一任务可恢复执行。
    """

    controller, store = _controller(tmp_path)
    task = _start_and_run(
        controller,
        _start_request(
            _Selection("fake_nested_input", FakeNestedInputRequest(draft_id="d-1"))
        ),
    )
    assert task.status == "needs_input"
    assert [item.key for item in task.pending_inputs] == ["85"]
    # 待补字段携带稳定 input_owner，供受信 UI 与 Controller 按路径合并。
    assert task.pending_inputs[0].input_owner == "provided_attributes"

    # UI 提交属性值；Controller 按 input_owner 合并进嵌套 provided_attributes。
    submitted = controller.submit_input(
        GlobalTaskInputRequest(
            task_id=task.task_id,
            arguments={"85": "Нет бренда"},
        )
    )
    assert submitted.task.status == "running"
    step_arguments = submitted.task.steps[0].arguments
    assert step_arguments.get("provided_attributes") == {"85": "Нет бренда"}
    # 原有顶层参数保留，且未把属性键泄漏到顶层。
    assert step_arguments.get("draft_id") == "d-1"
    assert "85" not in step_arguments

    resumed = controller.resume_task(task.task_id)
    assert resumed.status == "completed"
    assert RECORDED["fake_nested_input"] == [
        {"provided_attributes": {"85": "Нет бренда"}}
    ]
    assert store.require_task(task.task_id).status == "completed"


def test_submit_input_rejects_merged_arguments_that_fail_schema(
    tmp_path,
) -> None:
    controller, store = _controller(tmp_path)
    task = _start_and_run(
        controller,
        _start_request(_Selection("fake_input", FakeInputRequest())),
    )
    before = store.require_task(task.task_id)

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.submit_input(
            GlobalTaskInputRequest(
                task_id=task.task_id,
                arguments={"clarification": "x" * 500},
            )
        )
    assert error.value.code == "GLOBAL_TASK_INPUT_SCHEMA_INVALID"
    assert store.require_task(task.task_id) == before


def test_submit_input_on_task_without_pending_input_is_rejected(tmp_path) -> None:
    controller, _store = _controller(tmp_path)
    task = _start_and_run(
        controller,
        _start_request(_Selection("fake_read", FakeReadRequest())),
    )

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.submit_input(
            GlobalTaskInputRequest(
                task_id=task.task_id,
                arguments={"product_id": "p-2"},
            )
        )
    assert error.value.code == "GLOBAL_TASK_INPUT_NOT_EXPECTED"


# -- 审批 --------------------------------------------------------------------

APPROVER = "local-ui:test"


def _accept_approval_task(
    controller: GlobalTaskController,
    *,
    conversation_id: str = "conversation_global_chat_" + "9" * 32,
) -> GlobalTaskAcceptance:
    return _accept_task(
        controller,
        _start_request(
            _Selection(
                "fake_approval",
                FakeApprovalRequest(draft_id="draft-9", price="199"),
            )
        ),
        conversation_id=conversation_id,
    )


def _pending_approval_task(controller: GlobalTaskController) -> LocalGlobalTaskState:
    acceptance = _accept_approval_task(controller)
    _commit_first_history(controller, acceptance)
    return controller.resume_task(acceptance.task_id)


def test_approval_gate_persists_digest_bound_to_step_and_revision(
    tmp_path,
) -> None:
    controller, _store = _controller(tmp_path)

    task = _pending_approval_task(controller)

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
        _accept_task(
            controller,
            _start_request(
                _Selection("fake_approval", ForgedApprovalArguments())
            ),
        )
    assert error.value.code == "GLOBAL_TASK_STEP_ARGUMENTS_INVALID"
    assert "fake_approval" not in RECORDED

    # 正常路径下，展示内容依然是服务端生成的快照，与伪造输入无关。
    task = _pending_approval_task(controller)
    approval = task.pending_approval
    assert approval is not None
    assert approval.payload["summary"] == "发布摘要 draft-9"
    assert "伪造" not in approval.payload["summary"]


def test_approve_then_worker_executes_with_persisted_approval_record(
    tmp_path,
) -> None:
    controller, _store = _controller(tmp_path)
    task = _pending_approval_task(controller)

    approved = controller.approve_task(
        GlobalTaskApproveRequest(task_id=task.task_id),
        approver=APPROVER,
        conversation_id="conversation-9",
        message_id="message-10",
    )
    # 批准只改变业务状态：任务回到 running，执行在 worker。
    assert approved.task.status == "running"
    assert approved.task.pending_approval is None
    record = approved.task.steps[0].approval
    assert record is not None
    assert record.approver == APPROVER
    assert record.decision == "approved"
    assert "fake_approval" not in RECORDED

    resumed = controller.resume_task(task.task_id)
    assert resumed.status == "completed"

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


def test_full_approval_mode_preauthorizes_and_worker_executes_without_gate(
    tmp_path,
) -> None:
    controller, store = _controller(tmp_path, approval_mode="full")

    acceptance = _accept_approval_task(controller)
    _commit_first_history(controller, acceptance)
    task = controller.resume_task(acceptance.task_id)

    assert task.status == "completed"
    assert task.pending_approval is None
    assert store.require_task(task.task_id) == task
    record = task.steps[0].approval
    assert record is not None
    assert record.approver == "local-settings:full"
    assert record.decision == "approved"
    assert record.reason == "用户已选择完全授权"
    execution = RECORDED["fake_approval"][0]["execution"]
    assert execution["business_scope"]["approver"] == "local-settings:full"
    assert execution["business_scope"]["approval_confirmed_at"]
    assert execution["approval_digest"] == record.digest
    assert execution["approval_task_revision"] == record.task_revision
    assert len(execution["approved_tool_call_ids"]) == 1


def test_invalid_approval_mode_fails_closed_to_pending_approval(tmp_path) -> None:
    controller, _store = _controller(tmp_path, approval_mode="invalid")

    task = _pending_approval_task(controller)

    assert task.status == "pending_approval"
    assert "fake_approval" not in RECORDED


def test_approve_without_identity_is_rejected(tmp_path) -> None:
    controller, store = _controller(tmp_path)
    task = _pending_approval_task(controller)

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
    task = _pending_approval_task(controller)

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
    task = _pending_approval_task(controller)

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
    task = _pending_approval_task(controller)

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
    task = _pending_approval_task(controller)

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.reject_task(
            GlobalTaskRejectRequest(task_id=task.task_id, reason="x"),
            approver="",
        )
    assert error.value.code == "GLOBAL_TASK_APPROVAL_IDENTITY_REQUIRED"
    assert store.require_task(task.task_id).status == "pending_approval"


def test_approve_on_task_without_pending_approval_is_rejected(tmp_path) -> None:
    controller, _store = _controller(tmp_path)
    task = _start_and_run(
        controller,
        _start_request(_Selection("fake_read", FakeReadRequest())),
    )

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.approve_task(
            GlobalTaskApproveRequest(task_id=task.task_id),
            approver=APPROVER,
        )
    assert error.value.code == "GLOBAL_TASK_APPROVAL_NOT_EXPECTED"


# -- 长任务 ------------------------------------------------------------------


def test_persistent_job_moves_task_to_in_progress_with_generic_active_job(
    tmp_path,
) -> None:
    controller, _store = _controller(tmp_path)

    task = _start_and_run(
        controller,
        _start_request(_Selection("fake_job", FakeWriteRequest(value="go"))),
    )

    assert task.status == "in_progress"
    assert task.active_job is not None
    assert task.active_job.job_id == "job-1"
    assert task.active_job.capability_name == "fake_job"
    assert task.active_job.step_id == task.steps[0].step_id
    assert task.steps[0].status == "running"
    assert task.assistant_message == "已提交。"


def test_worker_polls_job_until_success_and_advances(tmp_path) -> None:
    reader = _JobStatusReader({"status": "running"}, {"status": "success"})
    controller, _store = _controller(tmp_path, reader=reader)
    task = _start_and_run(
        controller,
        _start_request(
            _Selection("fake_job", FakeWriteRequest(value="go")),
            _Selection("fake_read", FakeReadRequest(product_id="p-9")),
        ),
    )
    assert task.status == "in_progress"

    unchanged = controller.resume_task(task.task_id)
    assert unchanged.status == "in_progress"

    finished = controller.resume_task(task.task_id)
    assert finished.status == "completed"
    assert finished.active_job is None
    assert finished.steps[0].status == "completed"
    assert finished.steps[0].result == {
        "job_id": "job-1",
        "job_status": "success",
    }
    assert finished.steps[1].status == "completed"
    assert reader.calls == ["job-1", "job-1"]


def test_worker_fails_task_with_generic_job_error(tmp_path) -> None:
    reader = _JobStatusReader({"status": "failed", "error": "平台拒绝。"})
    controller, _store = _controller(tmp_path, reader=reader)
    task = _start_and_run(
        controller,
        _start_request(_Selection("fake_job", FakeWriteRequest(value="go"))),
    )

    finished = controller.resume_task(task.task_id)

    assert finished.status == "failed"
    assert finished.error_code == "GLOBAL_TASK_JOB_FAILED"
    assert finished.error_message == "平台拒绝。"
    assert finished.active_job is None


# -- 取消 --------------------------------------------------------------------


def test_cancel_is_idempotent_on_terminal_tasks(tmp_path) -> None:
    controller, _store = _controller(tmp_path)
    task = _start_and_run(
        controller,
        _start_request(_Selection("fake_read", FakeReadRequest())),
    )

    cancelled = controller.cancel_task(
        GlobalTaskIdRequest(task_id=task.task_id)
    )

    assert cancelled.task == task


def test_cancel_refuses_in_progress_job_submission(tmp_path) -> None:
    controller, _store = _controller(tmp_path)
    task = _start_and_run(
        controller,
        _start_request(_Selection("fake_job", FakeWriteRequest(value="go"))),
    )

    with pytest.raises(GlobalTaskControllerError) as error:
        controller.cancel_task(GlobalTaskIdRequest(task_id=task.task_id))
    assert error.value.code == "GLOBAL_TASK_JOB_ALREADY_SUBMITTED"


def test_cancel_needs_input_task(tmp_path) -> None:
    controller, _store = _controller(tmp_path)
    task = _start_and_run(
        controller,
        _start_request(_Selection("fake_input", FakeInputRequest())),
    )
    assert task.status == "needs_input"

    cancelled = controller.cancel_task(
        GlobalTaskIdRequest(task_id=task.task_id),
        canceller="local-ui:test",
        reason="用户主动取消",
    )

    assert cancelled.task.status == "cancelled"
    assert cancelled.task.pending_inputs == []
    # 取消审计：保留取消者、时间、原因、取消前状态与最后一个 blocker 摘要。
    audit = cancelled.task
    assert audit.cancelled_by == "local-ui:test"
    assert audit.cancelled_at is not None
    assert audit.cancel_reason == "用户主动取消"
    assert audit.previous_status == "needs_input"
    assert "clarification" in audit.last_blocker_summary


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
    # 正式 Task+link 流程下，business_scope 携带 link 的 conversation 关联；
    # 恢复不依赖回合消息：message_id 不进入 business_scope。
    assert execution["business_scope"]["conversation_id"] == CONVERSATION
    assert "message_id" not in execution["business_scope"]


def test_recovery_refreshes_in_progress_job_without_conversation(
    tmp_path,
) -> None:
    reader = _JobStatusReader({"status": "success"})
    controller, _store = _controller(tmp_path, reader=reader)
    task = _start_and_run(
        controller,
        _start_request(_Selection("fake_job", FakeWriteRequest(value="go"))),
    )
    assert task.status == "in_progress"

    resumed = controller.resume_task(task.task_id)

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


def test_unlinked_recoverable_task_is_quarantined_not_executed(
    tmp_path,
) -> None:
    """报告 R-07：接线 ledger 后，无 Deferred link 任务不再有执行 fallback。

    迁移前孤儿任务（直接落库、无 link）在 resume_task 与
    recover_unfinished_tasks 两个入口都必须被确定性隔离取消，绝不执行步骤。
    """

    controller, store = _controller(tmp_path)
    steps = controller._build_steps(
        "gtask_orphan",
        [_Selection("fake_write_retry", FakeWriteRequest(value="v"))],
    )
    orphan = LocalGlobalTaskState(
        task_id="gtask_orphan",
        goal="迁移前孤儿任务",
        status="running",
        steps=steps,
        current_step_index=0,
        created_at=NOW,
        updated_at=NOW,
    )
    store.create_task(orphan)

    quarantined = controller.resume_task(orphan.task_id)

    assert quarantined.status == "cancelled"
    assert quarantined.error_code == "GLOBAL_TASK_ORPHAN_QUARANTINED"
    assert quarantined.pending_inputs == []
    assert quarantined.pending_approval is None
    # 隔离不得执行任何步骤。
    assert RECORDED == {}

    # recovery worker 入口同样隔离，且二次恢复幂等（终态不再处理）。
    recovered = controller.recover_unfinished_tasks()
    assert recovered == []
    assert store.require_task(orphan.task_id).status == "cancelled"
    assert RECORDED == {}


# -- 读取 --------------------------------------------------------------------


def test_get_state_is_pure_read_without_execution(tmp_path) -> None:
    controller, _store = _controller(tmp_path)
    task = _start_and_run(
        controller,
        _start_request(_Selection("fake_input", FakeInputRequest())),
    )
    RECORDED.clear()

    state = controller.get_state(task.task_id)
    via_tool = controller.get_task(GlobalTaskIdRequest(task_id=task.task_id))

    assert state == task
    assert via_tool.task == task
    assert RECORDED == {}


def test_missing_task_raises_stable_not_found(tmp_path) -> None:
    controller, _store = _controller(tmp_path)

    with pytest.raises(Exception) as error:
        controller.get_task(GlobalTaskIdRequest(task_id="gtask_missing"))
    assert getattr(error.value, "code", "") == "GLOBAL_TASK_NOT_FOUND"
