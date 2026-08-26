"""全局 Agent 顺序任务与 HTTP 边界的类型化契约。

Task 协议与具体业务能力解耦：步骤只保存 capability 名称/版本、规范化
arguments、operation_key、状态、结果与错误；任务顶层使用通用
``pending_approval`` 与 ``active_job``。Controller 不解析领域参数。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from erp_web.schemas.ai_tools import AiToolRequiredInput


GlobalTaskStatus = Literal[
    "running",
    "needs_input",
    "pending_approval",
    "in_progress",
    "completed",
    "failed",
    "cancelled",
]
TERMINAL_GLOBAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})
RECOVERABLE_GLOBAL_TASK_STATUSES = frozenset({"running", "in_progress"})
GLOBAL_TASK_MAX_STEPS = 12
# 主 Agent 调用任务控制 Capability 所需的稳定权限标识。
TASK_CONTROL_PERMISSION = "task.control"

TaskStepStatus = Literal[
    "pending",
    "running",
    "needs_input",
    "completed",
    "failed",
]


class StrictTaskModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


RequiredInput = AiToolRequiredInput


class CapabilityError(StrictTaskModel):
    code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool = False
    details: dict[str, JsonValue] = Field(default_factory=dict, max_length=50)


class TaskStepApprovalRecord(StrictTaskModel):
    """已做出的审批决定审计记录：审批人、时间、任务版本与冻结 digest。"""

    approver: str = Field(min_length=1, max_length=200)
    decision: Literal["approved", "rejected"]
    decided_at: datetime
    digest: str = Field(min_length=1, max_length=128)
    task_revision: int = Field(ge=1)
    reason: str = Field(default="", max_length=2000)


class LocalTaskStep(StrictTaskModel):
    step_id: str = Field(min_length=1, max_length=160)
    capability_name: str = Field(min_length=1, max_length=64)
    capability_version: str = Field(min_length=1, max_length=40)
    # arguments 在任务持久化前已经由目标 Capability 的 request_adapter 校验，
    # 是规范化 JSON；补充资料也以整份 arguments 合并后重新校验。
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    # 仅由受信 submit_input 入口写入；Capability 用它区分用户明确补充的值与
    # 模型在初始计划中主动生成的同名参数。
    user_input_keys: tuple[str, ...] = Field(default=(), max_length=100)
    # operation_key 在计划创建时生成，任何重试都必须复用同一个值。
    operation_key: str = Field(min_length=1, max_length=320)
    status: TaskStepStatus = "pending"
    result: dict[str, JsonValue] | None = None
    error: CapabilityError | None = None
    # 审批决定审计记录；只在受信 UI/API 做出批准或拒绝后写入。
    approval: TaskStepApprovalRecord | None = None

    @model_validator(mode="after")
    def validate_step_shape(self) -> "LocalTaskStep":
        if self.status == "completed" and self.result is None:
            raise ValueError("completed 步骤必须保存结果")
        if self.status == "failed" and self.error is None:
            raise ValueError("failed 步骤必须保存稳定错误")
        if self.status in {"pending", "running", "needs_input"} and (
            self.result is not None or self.error is not None
        ):
            raise ValueError("未终结步骤不得保留结果或错误")
        return self


class TaskApprovalRequest(StrictTaskModel):
    """通用审批请求；绑定任务 revision、步骤、Capability 版本与 digest。"""

    step_id: str = Field(min_length=1, max_length=160)
    capability_name: str = Field(min_length=1, max_length=64)
    capability_version: str = Field(min_length=1, max_length=40)
    operation_key: str = Field(min_length=1, max_length=320)
    task_revision: int = Field(ge=1)
    # digest 由服务端审批快照（summary + canonical_payload）与 Capability
    # 名称/版本、operation_key、step_id、task_revision 规范化派生；
    # 批准时与执行时都必须重新核对。
    digest: str = Field(min_length=1, max_length=128)
    # payload 是服务端生成的审批快照（summary + canonical_payload），
    # 模型不能提交或改写；审批页面只展示它。
    payload: dict[str, JsonValue] = Field(default_factory=dict, max_length=100)
    requested_at: datetime


class TaskActiveJob(StrictTaskModel):
    """通用长任务 Job 引用；不含业务专用字段。"""

    step_id: str = Field(min_length=1, max_length=160)
    capability_name: str = Field(min_length=1, max_length=64)
    job_id: str = Field(min_length=1, max_length=200)
    # 领域无关 Job 类别；Controller 按它解析注册的 Job Status Reader。
    job_type: str = Field(min_length=1, max_length=80)
    started_at: datetime


class LocalGlobalTaskState(StrictTaskModel):
    schema_version: Literal[2] = 2
    # 每次持久化状态变更都会由 SQLite CAS 原子递增；调用方不得自行跳号。
    revision: int = Field(default=1, ge=1)
    # 一次显式执行领取的稳定 ID。进程崩溃后重新领取会生成新 ID。
    execution_id: str = Field(default="", max_length=200)
    task_id: str = Field(min_length=1, max_length=160)
    goal: str = Field(min_length=1, max_length=4000)
    product_id: str = Field(default="", max_length=200)
    draft_id: str = Field(default="", max_length=200)
    platform: str = Field(default="", max_length=80)
    status: GlobalTaskStatus
    steps: list[LocalTaskStep] = Field(default_factory=list, max_length=GLOBAL_TASK_MAX_STEPS)
    current_step_index: int = Field(default=0, ge=0, le=GLOBAL_TASK_MAX_STEPS)
    pending_inputs: list[RequiredInput] = Field(default_factory=list, max_length=100)
    pending_approval: TaskApprovalRequest | None = None
    active_job: TaskActiveJob | None = None
    assistant_message: str = Field(default="", max_length=4000)
    error_code: str = Field(default="", max_length=120)
    error_message: str = Field(default="", max_length=2000)
    # 取消审计（修复计划第 7 节）：cancelled 终态仍保留可追溯的取消者与取消前
    # 状态、原因及最后一个 blocker 摘要；默认空值兼容既有持久化任务。
    cancelled_by: str = Field(default="", max_length=200)
    cancelled_at: datetime | None = None
    cancel_reason: str = Field(default="", max_length=2000)
    previous_status: str = Field(default="", max_length=40)
    last_blocker_summary: str = Field(default="", max_length=2000)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_task_shape(self) -> "LocalGlobalTaskState":
        if self.steps and self.current_step_index > len(self.steps):
            raise ValueError("current_step_index 超出步骤范围")
        if self.status == "needs_input":
            if not self.pending_inputs:
                raise ValueError("needs_input 任务必须保存明确待补字段")
            current = (
                self.steps[self.current_step_index]
                if self.current_step_index < len(self.steps)
                else None
            )
            if current is None or current.status != "needs_input":
                raise ValueError("needs_input 任务的当前步骤必须处于 needs_input")
        elif self.pending_inputs:
            raise ValueError("非 needs_input 任务不得保留待补字段")
        if self.status == "pending_approval":
            if self.pending_approval is None:
                raise ValueError("pending_approval 任务必须保存审批请求")
            current = (
                self.steps[self.current_step_index]
                if self.current_step_index < len(self.steps)
                else None
            )
            if (
                current is None
                or current.step_id != self.pending_approval.step_id
                or current.capability_name != self.pending_approval.capability_name
            ):
                raise ValueError("审批请求必须绑定当前步骤")
        elif self.pending_approval is not None:
            raise ValueError("非 pending_approval 任务不得保留审批请求")
        if self.status == "in_progress":
            if self.active_job is None:
                raise ValueError("in_progress 任务必须保存 active_job")
        elif self.active_job is not None:
            raise ValueError("非 in_progress 任务不得保留 active_job")
        return self


class GlobalTaskStepCreate(StrictTaskModel):
    """可信内部形状：控制 Capability 解析后交给 Controller 的单步计划。"""

    capability_name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class GlobalTaskIdRequest(StrictTaskModel):
    task_id: str = Field(min_length=1, max_length=160)


class GlobalTaskInputRequest(GlobalTaskIdRequest):
    """补充资料：把字段合并进当前步骤 arguments 后重新校验并继续执行。"""

    arguments: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_user_input(self) -> "GlobalTaskInputRequest":
        if not self.arguments:
            raise ValueError("请提交至少一个补充字段")
        return self


class GlobalTaskApproveRequest(GlobalTaskIdRequest):
    """用户确认当前待审批步骤；digest 与 payload 来自已持久化审批请求。"""

    step_id: str = Field(default="", max_length=160)


class GlobalTaskRejectRequest(GlobalTaskIdRequest):
    """用户拒绝当前待审批步骤；任务进入 failed 并保留稳定原因。"""

    step_id: str = Field(default="", max_length=160)
    reason: str = Field(min_length=1, max_length=2000)


class GlobalTaskResponse(StrictTaskModel):
    """全局任务受信端点共用的成功响应。"""

    ok: Literal[True] = True
    task: LocalGlobalTaskState
    task_id: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_references(self) -> "GlobalTaskResponse":
        if self.task_id != self.task.task_id:
            raise ValueError("响应 task_id 与任务状态不一致")
        return self


# -- 执行进度只读视图 -------------------------------------------------------
#
# 进度是从当前任务与领域 Job 状态即时投影的计算型视图：不写回
# ``LocalGlobalTaskState``，不进入 ``task_json`` 持久化，GET 读取也不触发
# CAS/revision 递增。Reader 没有详细信息时只填充生命周期字段，任务卡必须
# 安全降级；所有用户可见文本在此处限制长度，禁止透传原始平台对象。

#: 领域 Job 生命周期状态；Controller 只消费它判定任务推进。
JobLifecycleStatus = Literal[
    "queued",
    "pending",
    "running",
    "retrying",
    "success",
    "failed",
]

#: 任务卡展示用进度状态；由投影服务从生命周期状态派生。
GlobalTaskProgressStatus = Literal[
    "queued",
    "running",
    "waiting",
    "retrying",
    "completed",
    "failed",
]

JobActivityStatus = Literal[
    "queued",
    "running",
    "waiting",
    "retrying",
    "completed",
    "failed",
]


class JobStateActivity(StrictTaskModel):
    """领域 Job 内部子步骤；code/label 已由 Reader 白名单映射。"""

    code: str = Field(min_length=1, max_length=80)
    label: str = Field(default="", max_length=200)
    status: JobActivityStatus
    completed_at: datetime | None = None


class JobStateSnapshot(StrictTaskModel):
    """``JobStatusReader`` 的类型化通用快照。

    ``status`` 与 ``error`` 是生命周期字段，Controller 只消费它们；
    其余为可选展示字段，进度投影服务消费，缺失时保持默认空值。
    """

    status: JobLifecycleStatus
    error: str = Field(default="", max_length=2000)
    # Job 记录是否真实存在；缺失时生命周期仍按 running 处理（不误判终态），
    # 任务卡展示降级为“暂时无法读取后台任务进度”。
    available: bool = True
    stage_code: str = Field(default="", max_length=120)
    stage_label: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=500)
    updated_at: datetime | None = None
    attempt: int | None = Field(default=None, ge=1)
    retry_count: int | None = Field(default=None, ge=0)
    next_check_at: datetime | None = None
    last_external_status: str = Field(default="", max_length=80)
    phase_started_at: datetime | None = None
    activities: tuple[JobStateActivity, ...] = Field(
        default_factory=tuple,
        max_length=50,
    )


class GlobalTaskProgressActivity(StrictTaskModel):
    """任务卡展示的 Job 内部活动；与顶层步骤分层渲染。"""

    code: str = Field(min_length=1, max_length=80)
    label: str = Field(default="", max_length=200)
    status: GlobalTaskProgressStatus
    completed_at: datetime | None = None


class GlobalTaskCurrentStepProgress(StrictTaskModel):
    """当前顶层步骤投影；label 是用户可读名称，缺省回落 capability 名。"""

    index: int = Field(ge=0)
    ordinal: int = Field(ge=1)
    total: int = Field(ge=1)
    capability_name: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=200)
    status: TaskStepStatus


class GlobalTaskActiveJobProgress(StrictTaskModel):
    """活跃领域 Job 的通用进度投影；不含平台专用字段。"""

    job_id: str = Field(min_length=1, max_length=200)
    job_type: str = Field(min_length=1, max_length=80)
    status: GlobalTaskProgressStatus
    stage_code: str = Field(default="", max_length=120)
    stage_label: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=500)
    started_at: datetime
    updated_at: datetime | None = None
    elapsed_seconds: int = Field(default=0, ge=0)
    phase_started_at: datetime | None = None
    phase_elapsed_seconds: int | None = Field(default=None, ge=0)
    attempt: int | None = Field(default=None, ge=1)
    retry_count: int | None = Field(default=None, ge=0)
    next_check_at: datetime | None = None
    last_external_status: str = Field(default="", max_length=80)


class GlobalTaskExecutionProgress(StrictTaskModel):
    """计算型只读进度视图；以 ``observed_at`` 作为前端计时锚点。"""

    observed_at: datetime
    task_elapsed_seconds: int = Field(default=0, ge=0)
    current_step: GlobalTaskCurrentStepProgress | None = None
    active_job: GlobalTaskActiveJobProgress | None = None
    activities: list[GlobalTaskProgressActivity] = Field(
        default_factory=list,
        max_length=50,
    )


class GlobalTaskViewResponse(StrictTaskModel):
    """HTTP/UI 专用读模型：持久化任务 + 计算型进度视图。

    Pydantic 控制 Tool 继续使用原领域响应 ``GlobalTaskResponse``，
    不引入这里的 UI 字段。
    """

    ok: Literal[True] = True
    task_id: str = Field(min_length=1, max_length=160)
    task: LocalGlobalTaskState
    execution_progress: GlobalTaskExecutionProgress | None = None

    @model_validator(mode="after")
    def validate_references(self) -> "GlobalTaskViewResponse":
        if self.task_id != self.task.task_id:
            raise ValueError("响应 task_id 与任务状态不一致")
        return self


class GlobalTaskAcceptance(StrictTaskModel):
    """``global_task_start`` Deferred 握手成功后的类型化受理结果。

    Task 与 provisional link 已在同一事务创建；此时任务尚未执行任何步骤，
    Bridge 用它携带的 task_id 抛出官方 ``CallDeferred`` 暂停 Agent。
    """

    ok: Literal[True] = True
    task_id: str = Field(min_length=1, max_length=160)
    conversation_id: str = Field(min_length=1, max_length=200)
    tool_call_id: str = Field(min_length=1, max_length=320)
    link_id: str = Field(min_length=1, max_length=160)


__all__ = [
    "CapabilityError",
    "GLOBAL_TASK_MAX_STEPS",
    "GlobalTaskAcceptance",
    "GlobalTaskActiveJobProgress",
    "GlobalTaskApproveRequest",
    "GlobalTaskCurrentStepProgress",
    "GlobalTaskExecutionProgress",
    "GlobalTaskIdRequest",
    "GlobalTaskInputRequest",
    "GlobalTaskProgressActivity",
    "GlobalTaskProgressStatus",
    "GlobalTaskRejectRequest",
    "GlobalTaskResponse",
    "GlobalTaskStatus",
    "GlobalTaskStepCreate",
    "GlobalTaskViewResponse",
    "JobActivityStatus",
    "JobLifecycleStatus",
    "JobStateActivity",
    "JobStateSnapshot",
    "LocalGlobalTaskState",
    "LocalTaskStep",
    "RECOVERABLE_GLOBAL_TASK_STATUSES",
    "RequiredInput",
    "TASK_CONTROL_PERMISSION",
    "TERMINAL_GLOBAL_TASK_STATUSES",
    "TaskActiveJob",
    "TaskApprovalRequest",
    "TaskStepApprovalRecord",
    "TaskStepStatus",
]
