"""全局任务控制 Capability 与自动投影的 ``global_task_start`` 请求 Schema。

``global.chat`` 通过这里声明四个任务控制工具；它们不包含任何业务领域逻辑，
只把类型化请求交给 Controller。``global_task_start`` 的 steps 是从 Task
ToolSet 各 Capability 的 Pydantic Request 机械投影的 discriminated union，
模型看到的是每个 Task Capability 的真实参数 Schema，而不是任意字典。

审批（approve/reject）不暴露给模型：批准与拒绝只能由携带可信审批凭据的
受信 UI/API 入口调用 Controller，避免同一决策主体自批高风险操作。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Collection, Literal, Protocol, Union, cast

from pydantic import BaseModel, ConfigDict, Field, create_model

from erp_web.ai_capability_composition import (
    APPLICATION_CAPABILITY_CATALOG,
    GLOBAL_TASK_CAPABILITIES,
)
from erp_web.schemas.ai_tools import AiToolExecutionError
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.global_tasks import (
    GLOBAL_TASK_MAX_STEPS,
    TASK_CONTROL_PERMISSION,
    GlobalTaskAcceptance,
    GlobalTaskIdRequest,
    GlobalTaskInputRequest,
    GlobalTaskResponse,
)
from erp_web.services.ai_tool_catalog import AiToolBindingScope, AiToolCatalog
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.ai_tool_registry import AiToolSet
from erp_web.services.capability_errors import BusinessCapabilityError


def _pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_") if part)


class _TaskStepBranchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def project_task_step_union(
    catalog: AiToolCatalog,
    capability_names: frozenset[str],
) -> Any:
    """从 Catalog 中 Task Capability 的 Request 类型机械投影 discriminated union。

    每个分支是 ``capability_name: Literal[名称] + arguments: 该 Capability 的
    真实 Request Model``；不允许逐 Capability 手写 step model。
    """

    branches: list[type[BaseModel]] = []
    for name in sorted(capability_names):
        tool = catalog.tools.get(name)
        if tool is None:
            raise ValueError(f"Task allowlist 引用了 Catalog 未收录能力：{name}")
        branch = create_model(
            f"GlobalTask{_pascal(name)}Step",
            __base__=_TaskStepBranchModel,
            capability_name=(Literal[name], ...),  # type: ignore[valid-type]
            arguments=(tool.request_type, ...),
        )
        branches.append(branch)
    if not branches:
        raise ValueError("Task Capability 集合为空，无法投影 task step union")
    return Annotated[
        Union[tuple(branches)],
        Field(discriminator="capability_name"),
    ]


TASK_STEP_SELECTION = project_task_step_union(
    APPLICATION_CAPABILITY_CATALOG,
    GLOBAL_TASK_CAPABILITIES,
)


class GlobalTaskStartControlRequest(BaseModel):
    """``global_task_start`` 的模型可见契约；steps 为类型化 Capability union。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: str = Field(min_length=1, max_length=4000)
    product_id: str = Field(default="", max_length=200)
    draft_id: str = Field(default="", max_length=200)
    platform: str = Field(default="", max_length=80)
    steps: list[TASK_STEP_SELECTION] = Field(
        min_length=1,
        max_length=GLOBAL_TASK_MAX_STEPS,
    )


class GlobalTaskControllerLike(Protocol):
    """控制 Capability 依赖的 Controller 最小可信接口。"""

    def accept_deferred_task(
        self,
        request: GlobalTaskStartControlRequest,
        *,
        conversation_id: str,
        request_run_id: str,
        tool_call_id: str,
        message_id: str,
    ) -> GlobalTaskAcceptance:
        ...

    def get_task(self, request: GlobalTaskIdRequest) -> GlobalTaskResponse:
        ...

    def submit_input(
        self,
        request: GlobalTaskInputRequest,
        *,
        conversation_id: str,
        message_id: str,
    ) -> GlobalTaskResponse:
        ...

    def cancel_task(
        self,
        request: GlobalTaskIdRequest,
        *,
        conversation_id: str,
        message_id: str,
    ) -> GlobalTaskResponse:
        ...


@dataclass(frozen=True)
class GlobalTaskControlScope:
    """任务控制 Capability 的可信 Controller 边界。"""

    controller: GlobalTaskControllerLike


def _chat_idempotency_ids(
    execution: AiExecutionContext,
) -> tuple[str, str]:
    conversation_id = str(
        execution.idempotency_context.get("conversation_id") or ""
    ).strip()
    message_id = str(
        execution.idempotency_context.get("message_id") or ""
    ).strip()
    if not conversation_id or not message_id:
        raise AiToolExecutionError(
            "TASK_CONTROL_CONTEXT_MISSING",
            "任务控制调用缺少可信 conversation/message 上下文。",
        )
    return conversation_id, message_id


def _deferred_handshake_context(
    execution: AiExecutionContext,
) -> tuple[str, str, str]:
    """Deferred 握手需要的可信上下文：conversation、request_run、tool_call。

    ``tool_call_id`` 由 ``AiToolRuntime`` 在 agent_deferred 调用时从 Bridge 的
    ``AiToolCommand.call_id`` 注入本次 business scope；缺失即握手上下文不完整。
    """

    conversation_id = str(
        execution.idempotency_context.get("conversation_id") or ""
    ).strip()
    tool_call_id = str(execution.business_scope.get("tool_call_id") or "").strip()
    request_run_id = str(execution.attempt_id or "").strip()
    if not conversation_id or not tool_call_id or not request_run_id:
        raise AiToolExecutionError(
            "TASK_CONTROL_CONTEXT_MISSING",
            "Deferred 任务握手缺少可信 conversation/run/tool_call 上下文。",
        )
    return conversation_id, request_run_id, tool_call_id


def _run_control(action: Any) -> Any:
    try:
        return action()
    except AiToolExecutionError:
        raise
    except BusinessCapabilityError as exc:
        raise AiToolExecutionError(
            exc.code,
            str(exc),
            retryable=exc.retryable,
        ) from None
    except Exception as exc:
        stable_code = str(getattr(exc, "code", "") or "").strip()
        if stable_code:
            raise AiToolExecutionError(
                stable_code,
                str(exc) or "任务控制操作失败。",
            ) from exc
        raise AiToolExecutionError(
            "GLOBAL_TASK_CONTROL_FAILED",
            f"任务控制操作失败：{exc}",
            retryable=True,
        ) from exc


GLOBAL_TASK_START_TOOL = "global_task_start"
GLOBAL_TASK_GET_TOOL = "global_task_get"
GLOBAL_TASK_SUBMIT_INPUT_TOOL = "global_task_submit_input"
GLOBAL_TASK_CANCEL_TOOL = "global_task_cancel"


@ai_tool(
    name=GLOBAL_TASK_START_TOOL,
    description=(
        "创建一个全局任务：steps 必须是已选择好的 Task Capability 步骤，"
        "每一步都携带该 Capability 的类型化参数；不会触发第二次计划模型调用。"
        "调用成功后本轮对话即挂起，任务由后台执行，终结时系统自动恢复并给出"
        "最终回复；不要轮询任务状态。需要审批的步骤会进入待审批状态，由人工"
        "在受信界面确认，模型不能自批。"
    ),
    permission=TASK_CONTROL_PERMISSION,
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("conversation_id", "message_id"),
    recovery_policy="idempotent",
    version="2",
    agent_deferred=True,
)
def global_task_start(
    request: GlobalTaskStartControlRequest,
    scope: Annotated[GlobalTaskControlScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> GlobalTaskAcceptance:
    conversation_id, request_run_id, tool_call_id = _deferred_handshake_context(
        execution
    )
    message_id = str(
        execution.idempotency_context.get("message_id") or ""
    ).strip()
    return cast(
        GlobalTaskAcceptance,
        _run_control(
            lambda: scope.controller.accept_deferred_task(
                request,
                conversation_id=conversation_id,
                request_run_id=request_run_id,
                tool_call_id=tool_call_id,
                message_id=message_id,
            )
        ),
    )


@ai_tool(
    name=GLOBAL_TASK_GET_TOOL,
    description="读取指定全局任务的当前状态、步骤结果与待办（补资料/审批/长任务）。",
    permission=TASK_CONTROL_PERMISSION,
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def global_task_get(
    request: GlobalTaskIdRequest,
    scope: Annotated[GlobalTaskControlScope, Injected()],
) -> GlobalTaskResponse:
    return _run_control(lambda: scope.controller.get_task(request))


@ai_tool(
    name=GLOBAL_TASK_SUBMIT_INPUT_TOOL,
    description=(
        "为处于待补资料状态的任务提交补充字段；字段会合并进当前步骤参数并"
        "重新校验后继续执行。"
    ),
    permission=TASK_CONTROL_PERMISSION,
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("conversation_id", "message_id"),
    recovery_policy="idempotent",
    version="1",
)
def global_task_submit_input(
    request: GlobalTaskInputRequest,
    scope: Annotated[GlobalTaskControlScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> GlobalTaskResponse:
    conversation_id, message_id = _chat_idempotency_ids(execution)
    return _run_control(
        lambda: scope.controller.submit_input(
            request,
            conversation_id=conversation_id,
            message_id=message_id,
        )
    )


@ai_tool(
    name=GLOBAL_TASK_CANCEL_TOOL,
    description="取消一个尚未终结的全局任务。",
    permission=TASK_CONTROL_PERMISSION,
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("conversation_id", "message_id"),
    recovery_policy="idempotent",
    version="1",
)
def global_task_cancel(
    request: GlobalTaskIdRequest,
    scope: Annotated[GlobalTaskControlScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> GlobalTaskResponse:
    conversation_id, message_id = _chat_idempotency_ids(execution)
    return _run_control(
        lambda: scope.controller.cancel_task(
            request,
            conversation_id=conversation_id,
            message_id=message_id,
        )
    )


GLOBAL_TASK_CONTROL_CAPABILITIES = (
    global_task_start,
    global_task_get,
    global_task_submit_input,
    global_task_cancel,
)

GLOBAL_TASK_CONTROL_TOOLSET_ID = "global.chat.task_control"

GLOBAL_TASK_CONTROL_CATALOG = AiToolCatalog.compile(
    GLOBAL_TASK_CONTROL_CAPABILITIES
)


def bind_global_task_control_toolset(
    *,
    scope: GlobalTaskControlScope,
    declared_permissions: Collection[str],
) -> AiToolSet:
    """为主 Agent 绑定任务控制 ToolSet。"""

    return GLOBAL_TASK_CONTROL_CATALOG.bind(
        toolset_id=GLOBAL_TASK_CONTROL_TOOLSET_ID,
        allowed_tools=sorted(GLOBAL_TASK_CONTROL_CATALOG.tools),
        scope=AiToolBindingScope({GlobalTaskControlScope: scope}),
        declared_permissions=declared_permissions,
        allow_write=True,
    )


__all__ = [
    "GLOBAL_TASK_CANCEL_TOOL",
    "GLOBAL_TASK_CONTROL_CATALOG",
    "TASK_CONTROL_PERMISSION",
    "GLOBAL_TASK_CONTROL_CAPABILITIES",
    "GLOBAL_TASK_CONTROL_TOOLSET_ID",
    "GLOBAL_TASK_GET_TOOL",
    "GLOBAL_TASK_START_TOOL",
    "GLOBAL_TASK_SUBMIT_INPUT_TOOL",
    "GlobalTaskControlScope",
    "GlobalTaskControllerLike",
    "GlobalTaskStartControlRequest",
    "TASK_STEP_SELECTION",
    "bind_global_task_control_toolset",
    "global_task_cancel",
    "global_task_get",
    "global_task_start",
    "global_task_submit_input",
    "project_task_step_union",
]
