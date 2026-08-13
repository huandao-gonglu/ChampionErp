"""全局 Agent 的顺序任务、Capability 与 HTTP 边界类型。"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


CapabilityStatus = Literal["completed", "needs_input", "in_progress", "failed"]
TaskStepStatus = Literal[
    "pending",
    "running",
    "needs_input",
    "completed",
    "failed",
]
GlobalTaskStatus = Literal[
    "planning",
    "running",
    "needs_input",
    "waiting_publish_confirmation",
    "waiting_publish_result",
    "completed",
    "failed",
    "cancelled",
]
TERMINAL_GLOBAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})


class StrictTaskModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequiredInput(StrictTaskModel):
    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)
    input_type: Literal[
        "text",
        "select",
        "json_object",
        "string_list",
    ] = "text"
    options: list[str] = Field(default_factory=list, max_length=100)
    input_owner: Literal[
        "step",
        "provided_attributes",
        "pricing_input",
    ] = "step"


class CapabilityError(StrictTaskModel):
    code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool = False


CapabilityResultT = TypeVar("CapabilityResultT")


class CapabilityResult(StrictTaskModel, Generic[CapabilityResultT]):
    """Controller 可消费的封闭 Capability 结果。"""

    status: CapabilityStatus
    summary: str = Field(min_length=1, max_length=2000)
    result: CapabilityResultT | None = None
    required_inputs: list[RequiredInput] = Field(default_factory=list, max_length=100)
    job_id: str | None = Field(default=None, max_length=200)
    error: CapabilityError | None = None
    agent_execution_conversation_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
    )

    @field_validator("agent_execution_conversation_ids", mode="before")
    @classmethod
    def deduplicate_execution_conversation_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, (list, tuple)):
            raise ValueError("agent_execution_conversation_ids 必须是列表")
        return list(
            dict.fromkeys(
                str(item or "").strip()
                for item in value
                if str(item or "").strip()
            )
        )

    @model_validator(mode="after")
    def validate_status_shape(self) -> "CapabilityResult[CapabilityResultT]":
        if self.status == "completed":
            if self.result is None:
                raise ValueError("completed Capability 必须返回可验证 result")
            if self.required_inputs or self.job_id is not None or self.error is not None:
                raise ValueError("completed Capability 包含不属于该状态的字段")
        elif self.status == "needs_input":
            if not self.required_inputs:
                raise ValueError("needs_input Capability 必须返回明确字段")
            if self.result is not None or self.job_id is not None or self.error is not None:
                raise ValueError("needs_input Capability 包含不属于该状态的字段")
        elif self.status == "in_progress":
            if not self.job_id:
                raise ValueError("in_progress Capability 必须返回 PublishingBus job_id")
            if self.result is not None or self.required_inputs or self.error is not None:
                raise ValueError("in_progress Capability 包含不属于该状态的字段")
        else:
            if self.error is None:
                raise ValueError("failed Capability 必须返回稳定错误")
            if self.result is not None or self.required_inputs or self.job_id is not None:
                raise ValueError("failed Capability 包含不属于该状态的字段")
        return self


class GlobalTaskStepProposal(StrictTaskModel):
    local_key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    capability: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=500)


class GlobalTaskPlanParameters(StrictTaskModel):
    """模型可从用户目标结构化、且仍会由领域 Capability 复核的业务值。

    商品、草稿、店铺、平台与图片资产等稳定身份不在这里，继续由可信上下文
    或查询快照注入。
    """

    attribute_updates: dict[str, JsonValue] = Field(
        default_factory=dict,
        max_length=200,
    )
    provided_attributes: dict[str, JsonValue] = Field(
        default_factory=dict,
        max_length=200,
    )
    pricing_input: dict[str, JsonValue] = Field(default_factory=dict)
    regenerate_copy: bool = False


class GlobalTaskPlanProposal(StrictTaskModel):
    steps: list[GlobalTaskStepProposal] = Field(min_length=1, max_length=12)
    # 文档原示例没有结构化目标引用，Controller 因而无法安全地把“第二个”
    # 传给 Capability。模型只返回一基序号，Controller 再从快照解析稳定 ID。
    draft_position: int | None = Field(default=None, ge=1, le=100)
    target_platform: str = Field(default="", max_length=80)
    parameters: GlobalTaskPlanParameters = Field(
        default_factory=GlobalTaskPlanParameters
    )

    @model_validator(mode="after")
    def validate_local_keys(self) -> "GlobalTaskPlanProposal":
        keys = [step.local_key for step in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError("计划步骤 local_key 不得重复")
        return self


class GlobalPlanningDecision(StrictTaskModel):
    action: Literal["plan", "answer", "ask_user"]
    plan: GlobalTaskPlanProposal | None = None
    query_snapshot_id: str = Field(default="", max_length=160)
    answer: str = Field(default="", max_length=4000)
    question: str = Field(default="", max_length=1000)
    explanation: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_action_shape(self) -> "GlobalPlanningDecision":
        if self.action == "plan":
            if self.plan is None:
                raise ValueError("action=plan 时必须返回 plan")
            if self.answer or self.question:
                raise ValueError("action=plan 时不得返回 answer/question")
        elif self.action == "answer":
            if not self.query_snapshot_id:
                raise ValueError("action=answer 时必须引用 query_snapshot_id")
            if not self.answer:
                raise ValueError("action=answer 时必须说明回答意图")
            if self.plan is not None or self.question:
                raise ValueError("action=answer 时不得返回 plan/question")
        else:
            if not self.question:
                raise ValueError("action=ask_user 时必须返回具体问题")
            if self.plan is not None or self.answer or self.query_snapshot_id:
                raise ValueError("action=ask_user 时不得返回计划、答案或查询快照")
        return self


class LocalTaskStep(StrictTaskModel):
    step_id: str = Field(min_length=1, max_length=160)
    capability: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=500)
    status: TaskStepStatus = "pending"
    # 文档示例漏掉了补充资料的 durable owner；字段必须随步骤持久化，重启后才能继续。
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    result_summary: str = Field(default="", max_length=2000)
    result_ref: str = Field(default="", max_length=500)
    error_code: str = Field(default="", max_length=120)


class PublishConfirmation(StrictTaskModel):
    status: Literal["none", "pending", "confirmed"] = "none"
    validation_digest: str = Field(default="", max_length=128)
    summary: dict[str, JsonValue] = Field(default_factory=dict)
    confirmed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_confirmation_shape(self) -> "PublishConfirmation":
        if self.status == "none":
            if self.validation_digest or self.summary or self.confirmed_at is not None:
                raise ValueError("未进入确认时不得保留发布确认内容")
        elif self.status == "pending":
            if not self.validation_digest or not self.summary:
                raise ValueError("待确认发布必须包含摘要和 validation_digest")
            if self.confirmed_at is not None:
                raise ValueError("待确认发布不能预先写入 confirmed_at")
        else:
            if not self.validation_digest or not self.summary or self.confirmed_at is None:
                raise ValueError("已确认发布必须保留摘要、digest 和确认时间")
        return self


class LocalGlobalTaskState(StrictTaskModel):
    schema_version: Literal[1] = 1
    task_id: str = Field(min_length=1, max_length=160)
    task_kind: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=4000)
    product_id: str = Field(default="", max_length=200)
    platform: str = Field(default="", max_length=80)
    status: GlobalTaskStatus
    steps: list[LocalTaskStep] = Field(default_factory=list, max_length=12)
    current_step_index: int = Field(default=0, ge=0, le=12)
    pending_inputs: list[RequiredInput] = Field(default_factory=list, max_length=100)
    pending_input_owner: Literal["none", "planning", "capability"] = "none"
    publish_confirmation: PublishConfirmation = Field(default_factory=PublishConfirmation)
    publish_idempotency_key: str = Field(default="", max_length=320)
    publish_job_id: str = Field(default="", max_length=200)
    draft_query_snapshot_id: str = Field(default="", max_length=160)
    ai_work_conversation_id: str = Field(default="", max_length=200)
    agent_execution_conversation_ids: list[str] = Field(default_factory=list, max_length=100)
    assistant_message: str = Field(default="", max_length=4000)
    plan_explanation: str = Field(default="", max_length=2000)
    error_code: str = Field(default="", max_length=120)
    error_message: str = Field(default="", max_length=2000)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_task_shape(self) -> "LocalGlobalTaskState":
        if self.steps and self.current_step_index > len(self.steps):
            raise ValueError("current_step_index 超出步骤范围")
        if self.status == "needs_input" and not self.pending_inputs:
            raise ValueError("needs_input 任务必须保存明确待补字段")
        if self.status == "needs_input" and self.pending_input_owner == "none":
            raise ValueError("needs_input 任务必须保存待补资料 owner")
        if self.status != "needs_input" and self.pending_input_owner != "none":
            raise ValueError("非 needs_input 任务不得保留待补资料 owner")
        if self.status == "waiting_publish_confirmation":
            if self.publish_confirmation.status != "pending":
                raise ValueError("待发布确认任务必须保存 pending confirmation")
        if self.status == "waiting_publish_result" and not self.publish_job_id:
            raise ValueError("等待发布终态的任务必须保存 PublishingBus job_id")
        return self


class PublishConfirmationContext(StrictTaskModel):
    task_id: str = Field(min_length=1, max_length=160)
    step_id: str = Field(min_length=1, max_length=160)
    validation_digest: str = Field(min_length=1, max_length=128)
    confirmed_at: datetime


class GlobalTaskStartRequest(StrictTaskModel):
    goal: str = Field(min_length=1, max_length=4000)
    ai_work_conversation_id: str = Field(default="", max_length=200)
    task_kind: Literal["global.agent.chat"] = "global.agent.chat"
    product_id: str = Field(default="", max_length=200)
    platform: str = Field(default="", max_length=80)
    draft_query_snapshot_id: str = Field(default="", max_length=160)


class GlobalTaskIdRequest(StrictTaskModel):
    task_id: str = Field(min_length=1, max_length=160)


class GlobalTaskInputRequest(GlobalTaskIdRequest):
    message: str = Field(default="", max_length=4000)
    inputs: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_user_input(self) -> "GlobalTaskInputRequest":
        if not self.message.strip() and not self.inputs:
            raise ValueError("请提交字段化资料或补充说明")
        return self


class GlobalTaskResponse(StrictTaskModel):
    """五个全局任务端点共用的成功响应。"""

    ok: Literal[True] = True
    task: LocalGlobalTaskState
    task_id: str = Field(min_length=1, max_length=160)
    ai_work_conversation_id: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_references(self) -> "GlobalTaskResponse":
        if self.task_id != self.task.task_id:
            raise ValueError("响应 task_id 与任务状态不一致")
        if self.ai_work_conversation_id != self.task.ai_work_conversation_id:
            raise ValueError("响应对话 ID 与任务状态不一致")
        return self


__all__ = [
    "CapabilityError",
    "CapabilityResult",
    "CapabilityStatus",
    "GlobalPlanningDecision",
    "GlobalTaskIdRequest",
    "GlobalTaskInputRequest",
    "GlobalTaskPlanProposal",
    "GlobalTaskPlanParameters",
    "GlobalTaskResponse",
    "GlobalTaskStartRequest",
    "GlobalTaskStatus",
    "GlobalTaskStepProposal",
    "JsonValue",
    "LocalGlobalTaskState",
    "LocalTaskStep",
    "PublishConfirmation",
    "PublishConfirmationContext",
    "RequiredInput",
    "TERMINAL_GLOBAL_TASK_STATUSES",
    "TaskStepStatus",
]
