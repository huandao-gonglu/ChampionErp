"""通用 Capability 执行协议下的本地全局任务顺序状态机。

Controller 不包含任何具体 Capability 名称分支：

- 任务计划来自类型化 ``global_task_start`` 请求；持久化前用目标 Capability 的
  ``request_adapter`` 再次校验 arguments，并冻结 ``definition.version``；
- ``accept_deferred_task`` 在同一事务创建 Task 与 provisional Deferred link，
  不执行任何步骤；Agent 侧由 Bridge 抛出官方 ``CallDeferred`` 暂停，Controller
  不 import 任何 Pydantic Agent 生命周期类型；
- 任务步骤只能由 recovery worker 在 link ready 屏障之后通过既有 execution
  lease 领取并执行；受信写命令（补资料/批准/拒绝/取消）只改变业务状态；
- 每次 attempt 构造可信 ``AiExecutionContext`` 与独立 ``AiToolRuntime``，
  直接调用现有 ``AiToolRuntime.execute()``；
- 统一 ``AiToolResult`` 按标准协议映射为 completed / needs_input /
  pending_approval / in_progress / failed。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
import logging
from typing import Any, Callable, Iterator, Mapping, Protocol, cast
from uuid import uuid4

from erp_web.schemas.ai_tools import (
    AiToolCommand,
    AiToolResult,
    JobReferenceResult,
    TOOL_APPROVAL_REQUIRED,
    TOOL_INPUT_REQUIRED,
)
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.global_tasks import (
    CapabilityError,
    GLOBAL_TASK_MAX_STEPS,
    GlobalTaskAcceptance,
    GlobalTaskApproveRequest,
    GlobalTaskIdRequest,
    GlobalTaskInputRequest,
    GlobalTaskRejectRequest,
    GlobalTaskResponse,
    LocalGlobalTaskState,
    LocalTaskStep,
    RECOVERABLE_GLOBAL_TASK_STATUSES,
    RequiredInput,
    TERMINAL_GLOBAL_TASK_STATUSES,
    TaskActiveJob,
    TaskApprovalRequest,
    TaskStepApprovalRecord,
)
from erp_web.schemas.task_approval import (
    TASK_APPROVAL_MODE_ASK,
    TASK_APPROVAL_MODE_FULL,
    TASK_APPROVAL_MODES,
    TaskApprovalMode,
)
from erp_web.services.ai_tool_catalog import AiToolCatalog
from erp_web.services.ai_tool_registry import AiToolSet
from erp_web.services.ai_tool_runtime import AiToolRuntime
from erp_web.services.task_approval import approval_binding_digest
from erp_web.stores.global_task_store import LocalGlobalTaskStore


logger = logging.getLogger(__name__)


class GlobalTaskControllerError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        self.code = str(code or "GLOBAL_TASK_INVALID")
        self.status_code = int(status_code)
        super().__init__(message)


class JobStatusReader(Protocol):
    """通用长任务 Job 终态读取；只返回 job_id → 通用状态。"""

    def read_job_state(self, job_id: str) -> Mapping[str, Any]:
        """返回 ``{"status": ..., "error": str}``。

        status 取值：queued / pending / running / retrying（活跃）或
        success / failed（终态）。
        """
        ...


class TaskStepSelectionLike(Protocol):
    """``global_task_start`` 类型化步骤的结构契约（由 union 分支满足）。"""

    capability_name: str
    arguments: Any


class GlobalTaskStartRequestLike(Protocol):
    """``global_task_start`` 请求的结构契约；真实类型在控制工具层投影。"""

    goal: str
    product_id: str
    draft_id: str
    platform: str
    steps: list[TaskStepSelectionLike]


_ACTIVE_JOB_STATUSES = frozenset({"", "queued", "pending", "running", "retrying"})
# deadline 耗尽后的副作用结果未知错误码（Runtime 对 TimeoutError 的稳定映射）。
TASK_DEADLINE_EXCEEDED = "TASK_DEADLINE_EXCEEDED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _plain_json(value: Any) -> Any:
    """把 AiToolResult 冻结结构（mappingproxy/tuple）还原为可持久化 JSON。"""

    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


class DeferredLinkReader(Protocol):
    """Controller 只读访问 Deferred link ledger 的最小边界。"""

    def get_by_task(self, task_id: str) -> Any | None:
        ...

    def has_active(self, conversation_id: str) -> bool:
        ...


class GlobalTaskController:
    """严格顺序推进 Task Capability；不实现 model/tool loop。"""

    def __init__(
        self,
        *,
        store: LocalGlobalTaskStore,
        catalog: AiToolCatalog,
        task_toolset: AiToolSet,
        job_status_readers: Mapping[str, JobStatusReader],
        deferred_links: DeferredLinkReader,
        approval_mode_loader: Callable[[], TaskApprovalMode] | None = None,
        execution_timeout_seconds: float = 600.0,
        execution_lease_seconds: float = 30.0,
    ) -> None:
        if not task_toolset.bindings:
            raise ValueError("Task ToolSet 不能为空")
        readers = {
            str(job_type or "").strip(): reader
            for job_type, reader in dict(job_status_readers).items()
        }
        if not readers or any(not job_type for job_type in readers):
            raise ValueError("Job Status Reader 注册表不能为空，job_type 不能为空")
        self.store = store
        self.catalog = catalog
        self.task_toolset = task_toolset
        self.job_status_readers: Mapping[str, JobStatusReader] = readers
        self.deferred_links = deferred_links
        self.approval_mode_loader = approval_mode_loader
        self.execution_timeout_seconds = max(1.0, float(execution_timeout_seconds))
        self.execution_lease_seconds = max(1.0, float(execution_lease_seconds))
        self._permissions = frozenset(
            definition.required_permission
            for definition in task_toolset.definitions
        )

    def _approval_mode(self) -> TaskApprovalMode:
        """读取当前设置；任何无效或读取失败都安全回落为询问审批。"""

        if self.approval_mode_loader is None:
            return TASK_APPROVAL_MODE_ASK
        try:
            mode = str(self.approval_mode_loader() or "").strip().lower()
        except Exception:
            logger.exception("读取全局任务审批等级失败，已回落为询问审批")
            return TASK_APPROVAL_MODE_ASK
        if mode not in TASK_APPROVAL_MODES:
            logger.error("未知全局任务审批等级 %r，已回落为询问审批", mode)
            return TASK_APPROVAL_MODE_ASK
        return cast(TaskApprovalMode, mode)

    # -- 持久化辅助 ---------------------------------------------------------

    def _save(self, task: LocalGlobalTaskState) -> LocalGlobalTaskState:
        return self.store.save_task(task)

    @staticmethod
    def _response(task: LocalGlobalTaskState) -> GlobalTaskResponse:
        return GlobalTaskResponse(task=task, task_id=task.task_id)

    @contextmanager
    def _optional_mutation_claim(
        self,
        task_id: str,
        *,
        allowed_statuses: frozenset[str],
    ) -> Iterator[LocalGlobalTaskState | None]:
        """领取可选写权；状态不匹配与 lease busy 均以 None 交给调用方判定。"""

        with self.store.task_lock(task_id):
            with self.store.execution_claim(
                task_id,
                lease_seconds=self.execution_lease_seconds,
                allowed_statuses=allowed_statuses,
            ) as task:
                yield task

    def _busy_or_status_error(
        self,
        task_id: str,
        *,
        expected_status: str,
        not_expected_code: str,
        not_expected_message: str,
    ) -> GlobalTaskControllerError:
        current = self.store.require_task(task_id)
        if current.status != expected_status:
            if current.status in TERMINAL_GLOBAL_TASK_STATUSES:
                return GlobalTaskControllerError(
                    not_expected_code,
                    not_expected_message,
                    status_code=409,
                )
            return GlobalTaskControllerError(
                not_expected_code,
                not_expected_message,
                status_code=409,
            )
        return GlobalTaskControllerError(
            "GLOBAL_TASK_EXECUTION_BUSY",
            "任务正在由另一个执行者处理，请稍后重试。",
            status_code=409,
        )

    # -- 创建 ---------------------------------------------------------------

    def _build_steps(
        self,
        task_id: str,
        selections: list[Any],
    ) -> list[LocalTaskStep]:
        if not 1 <= len(selections) <= GLOBAL_TASK_MAX_STEPS:
            raise GlobalTaskControllerError(
                "GLOBAL_TASK_PLAN_STEP_COUNT_INVALID",
                f"顺序计划必须包含 1 到 {GLOBAL_TASK_MAX_STEPS} 个步骤。",
            )
        steps: list[LocalTaskStep] = []
        for index, selection in enumerate(selections):
            name = str(selection.capability_name)
            binding = self.task_toolset.get(name)
            tool = self.catalog.tools.get(name)
            if binding is None or tool is None:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_CAPABILITY_UNAVAILABLE",
                    f"计划引用了 Task ToolSet 未收录的 Capability：{name}",
                )
            # 保留部分补丁的 fields_set：未提供字段不得展开成显式空值，
            # 否则执行时会把空默认当成真实 patch 覆盖已有业务数据。
            normalized = selection.arguments.model_dump(
                mode="json",
                exclude_unset=True,
            )
            try:
                validated = tool.request_adapter.validate_python(normalized)
            except Exception as exc:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_STEP_ARGUMENTS_INVALID",
                    f"步骤 {index + 1}（{name}）的参数未通过类型校验：{exc}",
                ) from None
            dumped = tool.request_adapter.dump_python(
                validated,
                mode="json",
                exclude_unset=True,
            )
            step_id = f"step_{index + 1}_{uuid4().hex[:12]}"
            steps.append(
                LocalTaskStep(
                    step_id=step_id,
                    capability_name=name,
                    capability_version=binding.definition.version,
                    arguments=dumped if isinstance(dumped, dict) else {},
                    operation_key=f"global-task:{task_id}:step:{step_id}",
                )
            )
        return steps

    def accept_deferred_task(
        self,
        request: GlobalTaskStartRequestLike,
        *,
        conversation_id: str,
        request_run_id: str,
        tool_call_id: str,
        message_id: str = "",
    ) -> GlobalTaskAcceptance:
        """类型化创建 Task 与 provisional Deferred link（同一 SQLite 事务）。

        本方法不执行任何 Task step：步骤只能由 recovery worker 在首次
        Deferred history 提交（link ready）之后领取执行。conversation 已有
        未解决 link 时稳定拒绝，供同一 run 内的第二次调用安全闭合。
        """

        del message_id
        normalized_conversation = str(conversation_id or "").strip()
        normalized_run_id = str(request_run_id or "").strip()
        normalized_call_id = str(tool_call_id or "").strip()
        if not normalized_conversation or not normalized_call_id:
            raise GlobalTaskControllerError(
                "TASK_CONTROL_CONTEXT_MISSING",
                "Deferred 任务创建缺少可信 conversation/tool_call 上下文。",
            )
        if self.deferred_links.has_active(
            normalized_conversation
        ):
            raise GlobalTaskControllerError(
                "GLOBAL_TASK_DEFERRED_ALREADY_PENDING",
                "该会话已有未解决的全局任务，请等待任务终结后再提交。",
                status_code=409,
            )
        task_id = f"gtask_{uuid4().hex}"
        link_id = f"dlink_{uuid4().hex}"
        steps = self._build_steps(task_id, list(request.steps))
        now = _now()
        task = LocalGlobalTaskState(
            task_id=task_id,
            goal=request.goal.strip(),
            product_id=request.product_id.strip(),
            draft_id=str(getattr(request, "draft_id", "") or "").strip(),
            platform=request.platform.strip().lower(),
            status="running",
            steps=steps,
            current_step_index=0,
            assistant_message="任务计划已创建，等待首次对话历史提交后执行。",
            created_at=now,
            updated_at=now,
        )
        try:
            created, _link_row = self.store.create_task_with_deferred_link(
                task,
                link_id=link_id,
                conversation_id=normalized_conversation,
                request_run_id=normalized_run_id,
                tool_call_id=normalized_call_id,
            )
        except sqlite3.IntegrityError:
            # partial unique index 兜底：同一 conversation 不能同时存在两个
            # 未解决 Deferred；同一 task 也不能绑定两个 link。
            raise GlobalTaskControllerError(
                "GLOBAL_TASK_DEFERRED_ALREADY_PENDING",
                "该会话已有未解决的全局任务，请等待任务终结后再提交。",
                status_code=409,
            ) from None
        return GlobalTaskAcceptance(
            task_id=created.task_id,
            conversation_id=normalized_conversation,
            tool_call_id=normalized_call_id,
            link_id=link_id,
        )

    def get_task(self, request: GlobalTaskIdRequest) -> GlobalTaskResponse:
        return self._response(self.store.require_task(request.task_id))

    def get_state(self, task_id: str) -> LocalGlobalTaskState:
        """纯读已持久化快照；禁止隐式执行或状态刷新。"""

        return self.store.require_task(task_id)

    # -- 执行 ---------------------------------------------------------------

    def _step_call_id(self, task: LocalGlobalTaskState, step: LocalTaskStep) -> str:
        return f"{task.task_id}:{step.step_id}:{task.execution_id or 'exec'}"

    def _execute_step(
        self,
        task: LocalGlobalTaskState,
        step: LocalTaskStep,
        *,
        conversation_id: str,
        message_id: str,
        step_timeout_seconds: float,
        approved: bool = False,
        approval_confirmed_at: str = "",
        approval_digest: str = "",
        approval_task_revision: int = 0,
        approver: str = "",
    ) -> AiToolResult:
        call_id = self._step_call_id(task, step)
        business_scope: dict[str, str] = {
            "task_id": task.task_id,
            "step_id": step.step_id,
        }
        if conversation_id:
            business_scope["conversation_id"] = conversation_id
        if message_id:
            business_scope["message_id"] = message_id
        if approval_confirmed_at:
            business_scope["approval_confirmed_at"] = approval_confirmed_at
        if approver:
            business_scope["approver"] = approver
        idempotency_context: dict[str, str] = {
            "operation_key": step.operation_key,
        }
        if conversation_id:
            idempotency_context["conversation_id"] = conversation_id
        if message_id:
            idempotency_context["message_id"] = message_id
        execution = AiExecutionContext.create(
            timeout_seconds=step_timeout_seconds,
            budget_profile="global.task",
            task_run_id=task.task_id,
            attempt_id=f"{task.execution_id or 'exec'}:{step.step_id}",
            permissions=self._permissions,
            business_scope=business_scope,
            idempotency_context=idempotency_context,
            approved_tool_call_ids=frozenset({call_id}) if approved else frozenset(),
            allow_write=True,
            approval_digest=approval_digest if approved else "",
            approval_task_revision=approval_task_revision if approved else 0,
        )
        runtime = AiToolRuntime(
            toolset=self.task_toolset,
            execution_context=execution,
            max_tool_calls=1,
        )
        command = AiToolCommand(
            call_id=call_id,
            tool_name=step.capability_name,
            tool_version=step.capability_version,
            arguments=step.arguments,
            round=1,
        )
        return runtime.execute(command)

    def _prepare_step_approval(
        self,
        task: LocalGlobalTaskState,
        step: LocalTaskStep,
    ) -> tuple[str, dict[str, Any]]:
        """服务端生成审批快照并派生绑定 digest；模型不参与摘要构造。"""

        binding = self.task_toolset.get(step.capability_name)
        if binding is None or binding.approval_preparer is None:
            raise GlobalTaskControllerError(
                "GLOBAL_TASK_APPROVAL_PREPARER_MISSING",
                f"审批步骤 {step.step_id} 缺少服务端审批准备器。",
            )
        try:
            snapshot = binding.approval_preparer(dict(step.arguments))
        except GlobalTaskControllerError:
            raise
        except Exception as exc:
            stable_code = str(getattr(exc, "code", "") or "").strip()
            raise GlobalTaskControllerError(
                stable_code or "GLOBAL_TASK_APPROVAL_SNAPSHOT_FAILED",
                str(exc) or "审批快照生成失败。",
            ) from exc
        digest = approval_binding_digest(
            snapshot=snapshot,
            capability_name=step.capability_name,
            capability_version=step.capability_version,
            operation_key=step.operation_key,
            step_id=step.step_id,
            task_revision=task.revision,
        )
        return digest, snapshot.model_dump(mode="json")

    def _record_automatic_approval(
        self,
        task: LocalGlobalTaskState,
        index: int,
    ) -> tuple[LocalGlobalTaskState, str, int, str, str]:
        """按“完全授权”设置预授权当前步骤，并在副作用前持久化审计记录。"""

        step = task.steps[index]
        digest, _payload = self._prepare_step_approval(task, step)
        approved_revision = task.revision
        confirmed_at = _now()
        approver = "local-settings:full"
        steps = list(task.steps)
        steps[index] = step.model_copy(
            update={
                "approval": TaskStepApprovalRecord(
                    approver=approver,
                    decision="approved",
                    decided_at=confirmed_at,
                    digest=digest,
                    task_revision=approved_revision,
                    reason="用户已选择完全授权",
                )
            }
        )
        saved = self._save(
            task.model_copy(
                update={
                    "steps": steps,
                    "assistant_message": "已按完全授权设置预授权，继续执行。",
                }
            )
        )
        return (
            saved,
            confirmed_at.isoformat(),
            approved_revision,
            digest,
            approver,
        )

    def _fail_approval_preparation(
        self,
        task: LocalGlobalTaskState,
        index: int,
        error: GlobalTaskControllerError,
    ) -> LocalGlobalTaskState:
        step = task.steps[index]
        steps = list(task.steps)
        steps[index] = step.model_copy(
            update={
                "status": "failed",
                "error": CapabilityError(code=error.code, message=str(error)),
            }
        )
        return task.model_copy(
            update={
                "steps": steps,
                "status": "failed",
                "pending_inputs": [],
                "pending_approval": None,
                "error_code": error.code,
                "error_message": str(error),
                "assistant_message": str(error),
            }
        )

    def _apply_result(
        self,
        task: LocalGlobalTaskState,
        index: int,
        result: AiToolResult,
    ) -> LocalGlobalTaskState:
        step = task.steps[index]
        steps = list(task.steps)
        binding = self.task_toolset.get(step.capability_name)
        if result.ok:
            output = (
                _plain_json(result.output)
                if isinstance(result.output, Mapping)
                else {}
            )
            if (
                binding is not None
                and binding.definition.execution_mode == "persistent_job"
            ):
                try:
                    job = JobReferenceResult.model_validate(dict(output))
                except Exception:
                    job = None
                if job is None:
                    steps[index] = step.model_copy(
                        update={
                            "status": "failed",
                            "error": CapabilityError(
                                code="GLOBAL_TASK_JOB_REFERENCE_INVALID",
                                message="persistent_job 步骤未返回类型化 Job 引用。",
                            ),
                        }
                    )
                    return task.model_copy(
                        update={
                            "steps": steps,
                            "status": "failed",
                            "pending_inputs": [],
                            "pending_approval": None,
                            "error_code": "GLOBAL_TASK_JOB_REFERENCE_INVALID",
                            "error_message": "persistent_job 步骤未返回类型化 Job 引用。",
                            "assistant_message": "长任务步骤返回了无效结果。",
                        }
                    )
                steps[index] = step.model_copy(update={"status": "running"})
                return task.model_copy(
                    update={
                        "steps": steps,
                        "status": "in_progress",
                        "pending_inputs": [],
                        "pending_approval": None,
                        "active_job": TaskActiveJob(
                            step_id=step.step_id,
                            capability_name=step.capability_name,
                            job_id=job.job_id,
                            job_type=job.job_type,
                            started_at=_now(),
                        ),
                        "assistant_message": (
                            job.summary or "长任务已提交，正在等待平台真实终态。"
                        ),
                    }
                )
            steps[index] = step.model_copy(
                update={"status": "completed", "result": dict(output)}
            )
            return task.model_copy(
                update={
                    "steps": steps,
                    "current_step_index": index + 1,
                    "pending_inputs": [],
                    "pending_approval": None,
                    "assistant_message": f"步骤 {index + 1}/{len(steps)} 已完成。",
                }
            )

        error = result.error if isinstance(result.error, Mapping) else {}
        code = str(error.get("code") or "TOOL_EXECUTION_FAILED")
        message = str(error.get("message") or "步骤执行失败。")
        details = (
            _plain_json(error.get("details"))
            if isinstance(error.get("details"), Mapping)
            else {}
        )

        if code == TOOL_INPUT_REQUIRED:
            required_raw = details.get("required_inputs")
            pending: list[RequiredInput] = []
            if isinstance(required_raw, (list, tuple)):
                for item in required_raw:
                    try:
                        pending.append(RequiredInput.model_validate(item))
                    except Exception:
                        continue
            if not pending:
                pending = [
                    RequiredInput(
                        key="clarification",
                        label="补充说明",
                        reason=message,
                    )
                ]
            steps[index] = step.model_copy(update={"status": "needs_input"})
            return task.model_copy(
                update={
                    "steps": steps,
                    "status": "needs_input",
                    "pending_inputs": pending,
                    "pending_approval": None,
                    "assistant_message": message,
                }
            )

        if code == TOOL_APPROVAL_REQUIRED:
            try:
                digest, approval_payload = self._prepare_step_approval(task, step)
            except GlobalTaskControllerError as exc:
                return self._fail_approval_preparation(task, index, exc)
            steps[index] = step.model_copy(update={"status": "pending"})
            return task.model_copy(
                update={
                    "steps": steps,
                    "status": "pending_approval",
                    "pending_inputs": [],
                    "pending_approval": TaskApprovalRequest(
                        step_id=step.step_id,
                        capability_name=step.capability_name,
                        capability_version=step.capability_version,
                        operation_key=step.operation_key,
                        task_revision=task.revision,
                        digest=digest,
                        payload=approval_payload,
                        requested_at=_now(),
                    ),
                    "assistant_message": message,
                }
            )

        # 截止时间耗尽，或能力在副作用已发出后无法确认平台结果（details
        # 携带 outcome_unknown）时，步骤必须记录为结果未知：禁止自动重试，
        # 避免重复产生外部副作用。
        outcome_unknown = code == TASK_DEADLINE_EXCEEDED or bool(
            details.get("outcome_unknown")
        )
        steps[index] = step.model_copy(
            update={
                "status": "failed",
                "error": CapabilityError(
                    code=code,
                    message=message,
                    retryable=False if outcome_unknown else bool(error.get("retryable")),
                    details=(
                        {**details, "outcome_unknown": True}
                        if outcome_unknown
                        else details
                    ),
                ),
            }
        )
        return task.model_copy(
            update={
                "steps": steps,
                "status": "failed",
                "pending_inputs": [],
                "pending_approval": None,
                "error_code": code,
                "error_message": message,
                "assistant_message": (
                    "步骤执行超过 deadline，业务结果尚未确认；"
                    "系统不会自动重试，请先人工核对业务终态。"
                    if code == TASK_DEADLINE_EXCEEDED
                    else message
                ),
            }
        )

    def _advance(
        self,
        task: LocalGlobalTaskState,
        *,
        conversation_id: str = "",
        message_id: str = "",
        step_timeout_seconds: float | None = None,
        max_step_executions: int | None = None,
    ) -> LocalGlobalTaskState:
        resolved_timeout = (
            float(step_timeout_seconds)
            if step_timeout_seconds is not None
            else self.execution_timeout_seconds
        )
        executed_steps = 0
        while (
            task.status == "running"
            and task.current_step_index < len(task.steps)
        ):
            if (
                max_step_executions is not None
                and executed_steps >= max_step_executions
            ):
                break
            index = task.current_step_index
            step = task.steps[index]
            if step.status == "completed":
                task = self._save(
                    task.model_copy(update={"current_step_index": index + 1})
                )
                continue
            binding = self.task_toolset.get(step.capability_name)
            if (
                binding is None
                or binding.definition.version != step.capability_version
            ):
                return self._fail_version_mismatch(task, index)
            if (
                binding.definition.approval_required
                and step.approval is None
                and self._approval_mode() == TASK_APPROVAL_MODE_FULL
            ):
                # 完全授权：先持久化预授权审计记录，再从记录派生执行凭据。
                try:
                    task, *_ = self._record_automatic_approval(task, index)
                    step = task.steps[index]
                except GlobalTaskControllerError as exc:
                    failed = self._fail_approval_preparation(task, index, exc)
                    return self._save(failed)
            if step.status != "running":
                steps = list(task.steps)
                steps[index] = step.model_copy(update={"status": "running"})
                task = self._save(task.model_copy(update={"steps": steps}))
                step = steps[index]
            # 审批授权只能来自已持久化的步骤审批记录（受信批准或完全授权
            # 预授权）；执行侧据此重建 digest/revision/approver 凭据。
            step_approval = step.approval
            execution_approved = bool(
                step_approval is not None
                and step_approval.decision == "approved"
            )
            result = self._execute_step(
                task,
                step,
                conversation_id=conversation_id,
                message_id=message_id,
                step_timeout_seconds=resolved_timeout,
                approved=execution_approved,
                approval_confirmed_at=(
                    step_approval.decided_at.isoformat()
                    if execution_approved and step_approval is not None
                    else ""
                ),
                approval_digest=(
                    step_approval.digest
                    if execution_approved and step_approval is not None
                    else ""
                ),
                approval_task_revision=(
                    step_approval.task_revision
                    if execution_approved and step_approval is not None
                    else 0
                ),
                approver=(
                    step_approval.approver
                    if execution_approved and step_approval is not None
                    else ""
                ),
            )
            executed_steps += 1
            task = self._apply_result(task, index, result)
            task = self._save(task)
        if (
            task.status == "running"
            and task.current_step_index >= len(task.steps)
        ):
            task = self._save(
                task.model_copy(
                    update={
                        "status": "completed",
                        "assistant_message": "任务已完成。",
                    }
                )
            )
        return task

    def _fail_version_mismatch(
        self,
        task: LocalGlobalTaskState,
        index: int,
    ) -> LocalGlobalTaskState:
        step = task.steps[index]
        steps = list(task.steps)
        steps[index] = step.model_copy(
            update={
                "status": "failed",
                "error": CapabilityError(
                    code="GLOBAL_TASK_CAPABILITY_VERSION_MISMATCH",
                    message=(
                        f"Capability {step.capability_name} 的已冻结版本 "
                        f"{step.capability_version} 与当前 Catalog 定义不一致。"
                    ),
                ),
            }
        )
        return self._save(
            task.model_copy(
                update={
                    "steps": steps,
                    "status": "failed",
                    "pending_inputs": [],
                    "pending_approval": None,
                    "error_code": "GLOBAL_TASK_CAPABILITY_VERSION_MISMATCH",
                    "error_message": (
                        "任务步骤绑定的 Capability 版本已变化，"
                        "请基于新版本重新创建任务。"
                    ),
                    "assistant_message": (
                        "任务步骤绑定的 Capability 版本已变化，不能静默重放。"
                    ),
                }
            )
        )

    # -- 补充资料 -----------------------------------------------------------

    @staticmethod
    def _merge_submitted_arguments(
        step_arguments: Mapping[str, Any],
        submitted: Mapping[str, Any],
        pending_inputs: Any,
    ) -> dict[str, Any]:
        """按 ``input_owner`` 把 UI 提交字段合并到正确的嵌套路径。

        ``step`` 顶层合并；``provided_attributes`` / ``pricing_input`` 合并进
        对应嵌套对象，避免把属性 ID 等当成顶层参数造成 Schema 校验失败。
        """

        owner = "step"
        try:
            first = pending_inputs[0]
            candidate = str(getattr(first, "input_owner", "") or "").strip().lower()
            if candidate in {"step", "provided_attributes", "pricing_input"}:
                owner = candidate
        except (IndexError, TypeError):
            owner = "step"
        base = dict(step_arguments)
        submitted = dict(submitted)
        if owner == "step":
            return {**base, **submitted}
        if owner in submitted:
            # 提交方已按嵌套路径组织（例如 provided_attributes={...}）：
            # 深合并该嵌套对象，其余字段照常落顶层。
            existing = base.get(owner)
            nested = dict(existing) if isinstance(existing, Mapping) else {}
            incoming = submitted.get(owner)
            base[owner] = (
                {**nested, **dict(incoming)}
                if isinstance(incoming, Mapping)
                else incoming
            )
            for key, value in submitted.items():
                if key != owner:
                    base[key] = value
            return base
        # 扁平提交（如前端直接提交属性 ID）：按 input_owner 合并进嵌套路径，
        # 避免把属性键当成顶层参数造成 Schema 校验失败。
        existing = base.get(owner)
        nested = dict(existing) if isinstance(existing, Mapping) else {}
        base[owner] = {**nested, **submitted}
        return base

    def submit_input(
        self,
        request: GlobalTaskInputRequest,
        *,
        conversation_id: str = "",
        message_id: str = "",
    ) -> GlobalTaskResponse:
        del conversation_id, message_id
        allowed_statuses = frozenset({"needs_input"})
        with self._optional_mutation_claim(
            request.task_id,
            allowed_statuses=allowed_statuses,
        ) as task:
            if task is None:
                raise self._busy_or_status_error(
                    request.task_id,
                    expected_status="needs_input",
                    not_expected_code="GLOBAL_TASK_INPUT_NOT_EXPECTED",
                    not_expected_message="当前任务不在等待补充资料。",
                )
            index = task.current_step_index
            if index >= len(task.steps):
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_STEP_MISSING",
                    "等待资料的任务没有对应步骤。",
                    status_code=409,
                )
            step = task.steps[index]
            tool = self.catalog.tools.get(step.capability_name)
            binding = self.task_toolset.get(step.capability_name)
            if tool is None or binding is None:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_CAPABILITY_UNAVAILABLE",
                    f"步骤引用了未收录的 Capability：{step.capability_name}",
                )
            merged = self._merge_submitted_arguments(
                step.arguments,
                dict(request.arguments),
                task.pending_inputs,
            )
            try:
                validated = tool.request_adapter.validate_python(merged)
            except Exception as exc:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_INPUT_SCHEMA_INVALID",
                    f"补充资料合并后未通过参数校验：{exc}",
                ) from None
            # 与创建路径一致：补充资料合并后仍保留部分补丁语义，
            # 不得把未提供字段展开成显式空值覆盖已有数据。
            dumped = tool.request_adapter.dump_python(
                validated,
                mode="json",
                exclude_unset=True,
            )
            steps = list(task.steps)
            steps[index] = step.model_copy(
                update={
                    "arguments": dumped if isinstance(dumped, dict) else {},
                    "status": "pending",
                }
            )
            # 只改变业务状态：合并后的步骤回到 pending，任务回到 running，
            # 由 recovery worker 在既有 execution lease 下领取并继续执行。
            task = task.model_copy(
                update={
                    "steps": steps,
                    "status": "running",
                    "pending_inputs": [],
                    "assistant_message": "资料已收到，将继续执行。",
                }
            )
            return self._response(self._save(task))

    # -- 审批 ---------------------------------------------------------------

    def approve_task(
        self,
        request: GlobalTaskApproveRequest,
        *,
        approver: str,
        conversation_id: str = "",
        message_id: str = "",
    ) -> GlobalTaskResponse:
        del conversation_id, message_id
        safe_approver = str(approver or "").strip()
        if not safe_approver:
            raise GlobalTaskControllerError(
                "GLOBAL_TASK_APPROVAL_IDENTITY_REQUIRED",
                "批准请求必须由可信执行上下文提供审批身份。",
                status_code=403,
            )
        allowed_statuses = frozenset({"pending_approval"})
        with self._optional_mutation_claim(
            request.task_id,
            allowed_statuses=allowed_statuses,
        ) as task:
            if task is None:
                raise self._busy_or_status_error(
                    request.task_id,
                    expected_status="pending_approval",
                    not_expected_code="GLOBAL_TASK_APPROVAL_NOT_EXPECTED",
                    not_expected_message="当前任务不在等待审批。",
                )
            approval = task.pending_approval
            if approval is None:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_APPROVAL_NOT_EXPECTED",
                    "当前任务不在等待审批。",
                    status_code=409,
                )
            if request.step_id and request.step_id != approval.step_id:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_APPROVAL_STEP_MISMATCH",
                    "审批请求与当前待审批步骤不一致。",
                    status_code=409,
                )
            # 任务版本绑定：审批只对它被创建时的那一版任务有效；
            # pending_approval 持久化时 revision 会 +1，期间任何其他写入
            # 都会让这里的等值检查失败。
            if task.revision != approval.task_revision + 1:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_APPROVAL_REVISION_STALE",
                    "任务在审批请求创建后已被修改，原审批已过期。",
                    status_code=409,
                )
            index = task.current_step_index
            if index >= len(task.steps):
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_STEP_MISSING",
                    "待审批任务没有对应步骤。",
                    status_code=409,
                )
            step = task.steps[index]
            if step.step_id != approval.step_id:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_APPROVAL_STEP_MISMATCH",
                    "审批请求与当前步骤不一致。",
                    status_code=409,
                )
            try:
                recomputed_digest, _payload = self._prepare_step_approval(
                    task.model_copy(update={"revision": approval.task_revision}),
                    step,
                )
            except GlobalTaskControllerError as exc:
                steps = list(task.steps)
                steps[index] = step.model_copy(
                    update={
                        "status": "failed",
                        "error": CapabilityError(code=exc.code, message=str(exc)),
                    }
                )
                failed = task.model_copy(
                    update={
                        "steps": steps,
                        "status": "failed",
                        "pending_inputs": [],
                        "pending_approval": None,
                        "error_code": exc.code,
                        "error_message": str(exc),
                        "assistant_message": str(exc),
                    }
                )
                return self._response(self._save(failed))
            if recomputed_digest != approval.digest:
                steps = list(task.steps)
                steps[index] = step.model_copy(
                    update={
                        "status": "failed",
                        "error": CapabilityError(
                            code="GLOBAL_TASK_APPROVAL_DIGEST_MISMATCH",
                            message="审批内容与请求参数已不一致，不能确认。",
                        ),
                    }
                )
                failed = task.model_copy(
                    update={
                        "steps": steps,
                        "status": "failed",
                        "pending_inputs": [],
                        "pending_approval": None,
                        "error_code": "GLOBAL_TASK_APPROVAL_DIGEST_MISMATCH",
                        "error_message": "审批内容与请求参数已不一致，不能确认。",
                        "assistant_message": "审批内容已变化，请重新发起该步骤。",
                    }
                )
                return self._response(self._save(failed))
            confirmed_at = _now()
            steps = list(task.steps)
            steps[index] = step.model_copy(
                update={
                    "approval": TaskStepApprovalRecord(
                        approver=safe_approver,
                        decision="approved",
                        decided_at=confirmed_at,
                        digest=approval.digest,
                        task_revision=approval.task_revision,
                    ),
                }
            )
            # 只改变业务状态：审批决定随步骤持久化，任务回到 running；
            # recovery worker 领取后按已持久化的审批记录执行该步骤。
            task = task.model_copy(
                update={
                    "steps": steps,
                    "status": "running",
                    "pending_approval": None,
                    "assistant_message": "已确认审批，将继续执行。",
                }
            )
            return self._response(self._save(task))

    def reject_task(
        self,
        request: GlobalTaskRejectRequest,
        *,
        approver: str,
        conversation_id: str = "",
        message_id: str = "",
    ) -> GlobalTaskResponse:
        del conversation_id, message_id
        safe_approver = str(approver or "").strip()
        if not safe_approver:
            raise GlobalTaskControllerError(
                "GLOBAL_TASK_APPROVAL_IDENTITY_REQUIRED",
                "拒绝请求必须由可信执行上下文提供审批身份。",
                status_code=403,
            )
        allowed_statuses = frozenset({"pending_approval"})
        with self._optional_mutation_claim(
            request.task_id,
            allowed_statuses=allowed_statuses,
        ) as task:
            if task is None:
                raise self._busy_or_status_error(
                    request.task_id,
                    expected_status="pending_approval",
                    not_expected_code="GLOBAL_TASK_APPROVAL_NOT_EXPECTED",
                    not_expected_message="当前任务不在等待审批。",
                )
            approval = task.pending_approval
            if approval is None:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_APPROVAL_NOT_EXPECTED",
                    "当前任务不在等待审批。",
                    status_code=409,
                )
            if request.step_id and request.step_id != approval.step_id:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_APPROVAL_STEP_MISMATCH",
                    "拒绝请求与当前待审批步骤不一致。",
                    status_code=409,
                )
            if task.revision != approval.task_revision + 1:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_APPROVAL_REVISION_STALE",
                    "任务在审批请求创建后已被修改，原审批已过期。",
                    status_code=409,
                )
            index = task.current_step_index
            steps = list(task.steps)
            if index >= len(steps) or steps[index].step_id != approval.step_id:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_APPROVAL_STEP_MISMATCH",
                    "拒绝请求与当前步骤不一致。",
                    status_code=409,
                )
            recomputed_digest, _payload = self._prepare_step_approval(
                task.model_copy(update={"revision": approval.task_revision}),
                steps[index],
            )
            if recomputed_digest != approval.digest:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_APPROVAL_DIGEST_MISMATCH",
                    "审批内容与请求参数已不一致，不能拒绝旧快照。",
                    status_code=409,
                )
            steps[index] = steps[index].model_copy(
                update={
                    "status": "failed",
                    "error": CapabilityError(
                        code="GLOBAL_TASK_APPROVAL_REJECTED",
                        message=request.reason.strip(),
                    ),
                    "approval": TaskStepApprovalRecord(
                        approver=safe_approver,
                        decision="rejected",
                        decided_at=_now(),
                        digest=approval.digest,
                        task_revision=approval.task_revision,
                        reason=request.reason.strip(),
                    ),
                }
            )
            rejected = task.model_copy(
                update={
                    "steps": steps,
                    "status": "failed",
                    "pending_inputs": [],
                    "pending_approval": None,
                    "error_code": "GLOBAL_TASK_APPROVAL_REJECTED",
                    "error_message": request.reason.strip(),
                    "assistant_message": "审批已拒绝，该步骤不会执行。",
                }
            )
            return self._response(self._save(rejected))

    # -- 取消 ---------------------------------------------------------------

    def cancel_task(
        self,
        request: GlobalTaskIdRequest,
        *,
        conversation_id: str = "",
        message_id: str = "",
        canceller: str = "",
        reason: str = "",
    ) -> GlobalTaskResponse:
        del conversation_id, message_id
        persisted = self.store.require_task(request.task_id)
        # 终态不可再变化，幂等取消无需改写 execution_id 或 revision。
        if persisted.status in TERMINAL_GLOBAL_TASK_STATUSES:
            return self._response(persisted)
        if persisted.status == "in_progress":
            raise GlobalTaskControllerError(
                "GLOBAL_TASK_JOB_ALREADY_SUBMITTED",
                "长任务已经提交外部系统，不能通过取消任务撤回。",
                status_code=409,
            )
        cancellable = frozenset({"running", "needs_input", "pending_approval"})
        with self._optional_mutation_claim(
            request.task_id,
            allowed_statuses=cancellable,
        ) as task:
            if task is None:
                current = self.store.require_task(request.task_id)
                if current.status in TERMINAL_GLOBAL_TASK_STATUSES:
                    return self._response(current)
                if current.status == "in_progress":
                    raise GlobalTaskControllerError(
                        "GLOBAL_TASK_JOB_ALREADY_SUBMITTED",
                        "长任务已经提交外部系统，不能通过取消任务撤回。",
                        status_code=409,
                    )
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_EXECUTION_BUSY",
                    "任务正在由另一个执行者处理，请稍后重试。",
                    status_code=409,
                )
            if (
                task.status == "running"
                and task.current_step_index < len(task.steps)
                and task.steps[task.current_step_index].status == "running"
            ):
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_STEP_OUTCOME_UNKNOWN",
                    "当前业务步骤的执行结果尚不明确，不能直接取消；"
                    "请先恢复任务或人工核对业务结果。",
                    status_code=409,
                )
            cancelled = task.model_copy(
                update={
                    "status": "cancelled",
                    "pending_inputs": [],
                    "pending_approval": None,
                    "assistant_message": "任务已取消，未再执行后续步骤。",
                    # 取消审计：保留取消者、时间、原因、取消前状态与最后 blocker。
                    "cancelled_by": str(canceller or "local-ui").strip()
                    or "local-ui",
                    "cancelled_at": _now(),
                    "cancel_reason": str(reason or "").strip()[:2000],
                    "previous_status": task.status,
                    "last_blocker_summary": self._last_blocker_summary(task),
                }
            )
            return self._response(self._save(cancelled))

    @staticmethod
    def _last_blocker_summary(task: LocalGlobalTaskState) -> str:
        """取消前最后一个 blocker（待补资料/待审批/错误）的可审计摘要。"""

        if task.pending_inputs:
            keys = "、".join(item.key for item in task.pending_inputs[:5])
            return f"等待补充资料：{keys}"
        if task.pending_approval is not None:
            return (
                f"等待审批：{task.pending_approval.capability_name}"
                f"（{task.pending_approval.step_id}）"
            )
        step = (
            task.steps[task.current_step_index]
            if task.current_step_index < len(task.steps)
            else None
        )
        if step is not None and step.error is not None:
            return f"步骤错误：{step.error.code} {step.error.message}"[:2000]
        return str(task.assistant_message or task.error_message or "")[:2000]

    # -- 恢复与 Job 刷新 ----------------------------------------------------

    def _fail_unsafe_running_step(
        self,
        task: LocalGlobalTaskState,
        index: int,
    ) -> LocalGlobalTaskState:
        step = task.steps[index]
        steps = list(task.steps)
        steps[index] = step.model_copy(
            update={
                "status": "failed",
                "error": CapabilityError(
                    code="GLOBAL_TASK_STEP_RECOVERY_UNSAFE",
                    message=(
                        f"步骤 {step.step_id} 的上一次副作用结果不明确，"
                        "且 Capability 未声明可安全重放。"
                    ),
                ),
            }
        )
        return self._save(
            task.model_copy(
                update={
                    "steps": steps,
                    "status": "failed",
                    "pending_inputs": [],
                    "pending_approval": None,
                    "error_code": "GLOBAL_TASK_STEP_RECOVERY_UNSAFE",
                    "error_message": (
                        f"步骤 {step.step_id} 的上一次副作用结果不明确，"
                        "且 Capability 未声明可安全重放。"
                    ),
                    "assistant_message": (
                        "任务在业务步骤执行期间中断。为避免重复副作用，"
                        "系统未自动重放，请人工核对业务结果。"
                    ),
                }
            )
        )

    def _refresh_claimed(
        self,
        task: LocalGlobalTaskState,
        *,
        conversation_id: str = "",
    ) -> LocalGlobalTaskState:
        if task.status != "in_progress" or task.active_job is None:
            return task
        active_job = task.active_job
        reader = self.job_status_readers.get(active_job.job_type)
        if reader is None:
            index = task.current_step_index
            steps = list(task.steps)
            if index < len(steps):
                steps[index] = steps[index].model_copy(
                    update={
                        "status": "failed",
                        "error": CapabilityError(
                            code="GLOBAL_TASK_JOB_READER_MISSING",
                            message=(
                                f"Job 类型 {active_job.job_type} "
                                "没有注册可信状态读取器。"
                            ),
                        ),
                    }
                )
            return self._save(
                task.model_copy(
                    update={
                        "steps": steps,
                        "status": "failed",
                        "active_job": None,
                        "pending_inputs": [],
                        "pending_approval": None,
                        "error_code": "GLOBAL_TASK_JOB_READER_MISSING",
                        "error_message": (
                            f"Job 类型 {active_job.job_type} 没有注册可信状态读取器。"
                        ),
                        "assistant_message": "长任务状态读取器缺失，无法继续。",
                    }
                )
            )
        try:
            state = reader.read_job_state(active_job.job_id)
        except Exception as exc:
            logger.warning(
                "读取长任务 %s 状态失败：%s",
                active_job.job_id,
                exc,
            )
            return task
        status = str(state.get("status") or "").strip().lower()
        if status in _ACTIVE_JOB_STATUSES:
            return task
        index = task.current_step_index
        steps = list(task.steps)
        if status == "success":
            if index < len(steps):
                steps[index] = steps[index].model_copy(
                    update={
                        "status": "completed",
                        "result": {
                            "job_id": active_job.job_id,
                            "job_status": "success",
                        },
                    }
                )
            task = self._save(
                task.model_copy(
                    update={
                        "steps": steps,
                        "current_step_index": min(index + 1, len(steps)),
                        "status": "running",
                        "active_job": None,
                        "assistant_message": "长任务已成功，继续执行。",
                    }
                )
            )
            return self._advance(task, conversation_id=conversation_id)
        error_message = str(state.get("error") or "").strip() or "长任务执行失败。"
        if index < len(steps):
            steps[index] = steps[index].model_copy(
                update={
                    "status": "failed",
                    "error": CapabilityError(
                        code="GLOBAL_TASK_JOB_FAILED",
                        message=error_message,
                    ),
                }
            )
        return self._save(
            task.model_copy(
                update={
                    "steps": steps,
                    "status": "failed",
                    "active_job": None,
                    "pending_inputs": [],
                    "pending_approval": None,
                    "error_code": "GLOBAL_TASK_JOB_FAILED",
                    "error_message": error_message,
                    "assistant_message": error_message,
                }
            )
        )

    def _resume_claimed(
        self,
        task: LocalGlobalTaskState,
        *,
        conversation_id: str = "",
        max_step_executions: int | None = None,
    ) -> LocalGlobalTaskState:
        if task.status == "running":
            if task.current_step_index < len(task.steps):
                current = task.steps[task.current_step_index]
                if current.status == "running":
                    binding = self.task_toolset.get(current.capability_name)
                    policy = (
                        binding.definition.recovery_policy
                        if binding is not None
                        else "manual"
                    )
                    if binding is None or policy == "manual":
                        return self._fail_unsafe_running_step(
                            task,
                            task.current_step_index,
                        )
                    steps = list(task.steps)
                    steps[task.current_step_index] = current.model_copy(
                        update={"status": "pending"}
                    )
                    task = self._save(task.model_copy(update={"steps": steps}))
            return self._advance(
                task,
                conversation_id=conversation_id,
                max_step_executions=max_step_executions,
            )
        if task.status == "in_progress":
            return self._refresh_claimed(task, conversation_id=conversation_id)
        return task

    def _deferred_link_for_task(self, task_id: str) -> Any | None:
        # 报告 A-09：Deferred ledger 是必需依赖；生产装配永远接线。
        return self.deferred_links.get_by_task(task_id)

    def _quarantine_unlinked_task(
        self,
        task: LocalGlobalTaskState,
    ) -> LocalGlobalTaskState:
        """报告 R-07：隔离取消无 Deferred link 的 recoverable 任务。

        这类任务没有 Agent suspension、conversation 与最终回复关联，执行步骤
        只会产生无法对账的副作用。确定性进入 cancelled 终态（明确的人工处理
        入口），recovery 不得再领取执行。
        """

        logger.warning(
            "recovery 隔离无 Deferred link 的 Global Task：task=%s status=%s",
            task.task_id,
            task.status,
        )
        quarantined = task.model_copy(
            update={
                "status": "cancelled",
                "pending_inputs": [],
                "pending_approval": None,
                "error_code": "GLOBAL_TASK_ORPHAN_QUARANTINED",
                "error_message": "任务缺少 Deferred 会话关联，已被恢复流程隔离。",
                "assistant_message": (
                    "该任务缺少 Deferred 会话关联，已被隔离，未再执行后续步骤；"
                    "如需继续，请重新发起任务。"
                ),
            }
        )
        return self._save(quarantined)

    def _resume_task(
        self,
        task_id: str,
        *,
        wait_for_local_lock: bool,
    ) -> LocalGlobalTaskState:
        # Deferred ready 屏障：只要存在 link，就必须等 ``ready_at`` 落定后
        # worker 才能领取执行。
        link = self._deferred_link_for_task(task_id)
        link_conversation_id = (
            str(link.conversation_id or "") if link is not None else ""
        )
        with self.store.task_lock(
            task_id,
            blocking=wait_for_local_lock,
        ) as acquired:
            if not acquired:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_EXECUTION_BUSY",
                    "任务正在由另一个执行者处理，请稍后重试。",
                    status_code=409,
                )
            persisted = self.store.require_task(task_id)
            if persisted.status not in RECOVERABLE_GLOBAL_TASK_STATUSES:
                return persisted
            if link is None:
                # 报告 R-07/A-09：正式迁移后生产创建入口只允许 Task 与
                # provisional link 原子创建；Deferred ledger 是必需依赖，无
                # link 的 recoverable 任务（迁移前孤儿记录等）不存在 Agent
                # suspension、conversation 与最终回复关联，不保留任何执行
                # fallback——确定性隔离取消，绝不执行步骤。
                return self._quarantine_unlinked_task(persisted)
            if not link.ready_at:
                # provisional link：首次 Deferred history 尚未原子提交；
                # 直接返回当前快照，不领取执行权，也不改状态。
                return persisted
            with self.store.execution_claim(
                task_id,
                lease_seconds=self.execution_lease_seconds,
                allowed_statuses=RECOVERABLE_GLOBAL_TASK_STATUSES,
            ) as task:
                if task is None:
                    current = self.store.require_task(task_id)
                    if current.status not in RECOVERABLE_GLOBAL_TASK_STATUSES:
                        return current
                    raise GlobalTaskControllerError(
                        "GLOBAL_TASK_EXECUTION_BUSY",
                        "任务正在由另一个执行者处理，请稍后重试。",
                        status_code=409,
                    )
                return self._resume_claimed(
                    task,
                    conversation_id=link_conversation_id,
                )

    def resume_task(self, task_id: str) -> LocalGlobalTaskState:
        """显式恢复单个任务；这是唯一允许重启后继续执行的入口。"""

        return self._resume_task(task_id, wait_for_local_lock=True)

    def recover_unfinished_tasks(
        self,
        *,
        limit: int = 100,
    ) -> list[LocalGlobalTaskState]:
        """供应用启动/可信后台任务显式调用的有界恢复机制。"""

        recovered: list[LocalGlobalTaskState] = []
        for task in self.store.list_recoverable_tasks(limit=limit):
            link = self._deferred_link_for_task(task.task_id)
            if link is not None and not link.ready_at:
                # provisional link 的任务在首次 history 提交前不进入恢复。
                continue
            try:
                recovered.append(
                    self._resume_task(
                        task.task_id,
                        wait_for_local_lock=False,
                    )
                )
            except GlobalTaskControllerError as exc:
                if exc.code != "GLOBAL_TASK_EXECUTION_BUSY":
                    raise
        return recovered


__all__ = [
    "DeferredLinkReader",
    "GlobalTaskController",
    "GlobalTaskControllerError",
    "JobStatusReader",
]
