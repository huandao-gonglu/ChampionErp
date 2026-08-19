"""通用 Capability 执行协议下的本地全局任务顺序状态机。

Controller 不包含任何具体 Capability 名称分支：

- 任务计划来自类型化 ``global_task_start`` 请求；持久化前用目标 Capability 的
  ``request_adapter`` 再次校验 arguments，并冻结 ``definition.version``；
- 每次 attempt 构造可信 ``AiExecutionContext`` 与独立 ``AiToolRuntime``，
  直接调用现有 ``AiToolRuntime.execute()``；
- 统一 ``AiToolResult`` 按标准协议映射为 completed / needs_input /
  pending_approval / in_progress / failed。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import logging
from typing import Any, Iterator, Mapping, Protocol
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
    platform: str
    steps: list[TaskStepSelectionLike]


_ACTIVE_JOB_STATUSES = frozenset({"", "queued", "pending", "running", "retrying"})
# 步骤执行超时的最小可行值；外层剩余时间低于它时直接拒绝执行。
_MINIMUM_STEP_TIMEOUT_SECONDS = 1.0
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


class GlobalTaskController:
    """严格顺序推进 Task Capability；不实现 model/tool loop。"""

    def __init__(
        self,
        *,
        store: LocalGlobalTaskStore,
        catalog: AiToolCatalog,
        task_toolset: AiToolSet,
        job_status_readers: Mapping[str, JobStatusReader],
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
        self.execution_timeout_seconds = max(1.0, float(execution_timeout_seconds))
        self.execution_lease_seconds = max(1.0, float(execution_lease_seconds))
        self._permissions = frozenset(
            definition.required_permission
            for definition in task_toolset.definitions
        )

    def _resolve_step_timeout(
        self,
        outer_remaining_seconds: float | None,
    ) -> float:
        """内层步骤 deadline 必须受外层剩余时间约束，不能自行扩展。"""

        if outer_remaining_seconds is None:
            return self.execution_timeout_seconds
        remaining = float(outer_remaining_seconds)
        if remaining < _MINIMUM_STEP_TIMEOUT_SECONDS:
            raise GlobalTaskControllerError(
                "GLOBAL_TASK_DEADLINE_EXHAUSTED",
                "外层执行剩余时间不足，不能安全启动任务步骤。",
                status_code=409,
            )
        return min(self.execution_timeout_seconds, remaining)

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
            normalized = selection.arguments.model_dump(mode="json")
            try:
                validated = tool.request_adapter.validate_python(normalized)
            except Exception as exc:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_STEP_ARGUMENTS_INVALID",
                    f"步骤 {index + 1}（{name}）的参数未通过类型校验：{exc}",
                ) from None
            dumped = tool.request_adapter.dump_python(validated, mode="json")
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

    def start_task(
        self,
        request: GlobalTaskStartRequestLike,
        *,
        conversation_id: str = "",
        message_id: str = "",
        outer_remaining_seconds: float | None = None,
    ) -> GlobalTaskResponse:
        step_timeout = self._resolve_step_timeout(outer_remaining_seconds)
        task_id = f"gtask_{uuid4().hex}"
        steps = self._build_steps(task_id, list(request.steps))
        now = _now()
        task = LocalGlobalTaskState(
            task_id=task_id,
            goal=request.goal.strip(),
            product_id=request.product_id.strip(),
            platform=request.platform.strip().lower(),
            status="running",
            steps=steps,
            current_step_index=0,
            assistant_message="任务计划已创建，开始执行。",
            created_at=now,
            updated_at=now,
        )
        with self.store.task_lock(task_id):
            with self.store.create_task_claimed(
                task,
                lease_seconds=self.execution_lease_seconds,
            ) as claimed:
                executed = self._advance(
                    claimed,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    step_timeout_seconds=step_timeout,
                )
        return self._response(executed)

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
                steps[index] = step.model_copy(
                    update={
                        "status": "failed",
                        "error": CapabilityError(code=exc.code, message=str(exc)),
                    }
                )
                return task.model_copy(
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
        approved: bool = False,
        approval_confirmed_at: str = "",
        approval_digest: str = "",
        approval_task_revision: int = 0,
        approver: str = "",
        step_timeout_seconds: float | None = None,
    ) -> LocalGlobalTaskState:
        resolved_timeout = (
            float(step_timeout_seconds)
            if step_timeout_seconds is not None
            else self.execution_timeout_seconds
        )
        while (
            task.status == "running"
            and task.current_step_index < len(task.steps)
        ):
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
            if step.status != "running":
                steps = list(task.steps)
                steps[index] = step.model_copy(update={"status": "running"})
                task = self._save(task.model_copy(update={"steps": steps}))
                step = steps[index]
            result = self._execute_step(
                task,
                step,
                conversation_id=conversation_id,
                message_id=message_id,
                step_timeout_seconds=resolved_timeout,
                approved=approved,
                approval_confirmed_at=approval_confirmed_at,
                approval_digest=approval_digest,
                approval_task_revision=approval_task_revision,
                approver=approver,
            )
            # 审批授权只对触发它的那一次执行有效。
            approved = False
            approval_confirmed_at = ""
            approval_digest = ""
            approval_task_revision = 0
            approver = ""
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

    def submit_input(
        self,
        request: GlobalTaskInputRequest,
        *,
        conversation_id: str = "",
        message_id: str = "",
        outer_remaining_seconds: float | None = None,
    ) -> GlobalTaskResponse:
        step_timeout = self._resolve_step_timeout(outer_remaining_seconds)
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
            merged = {**step.arguments, **dict(request.arguments)}
            try:
                validated = tool.request_adapter.validate_python(merged)
            except Exception as exc:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_INPUT_SCHEMA_INVALID",
                    f"补充资料合并后未通过参数校验：{exc}",
                ) from None
            dumped = tool.request_adapter.dump_python(validated, mode="json")
            steps = list(task.steps)
            steps[index] = step.model_copy(
                update={
                    "arguments": dumped if isinstance(dumped, dict) else {},
                    "status": "pending",
                }
            )
            task = task.model_copy(
                update={
                    "steps": steps,
                    "status": "running",
                    "pending_inputs": [],
                    "assistant_message": "资料已收到，继续执行。",
                }
            )
            task = self._save(task)
            executed = self._advance(
                task,
                conversation_id=conversation_id,
                message_id=message_id,
                step_timeout_seconds=step_timeout,
            )
            return self._response(executed)

    # -- 审批 ---------------------------------------------------------------

    def approve_task(
        self,
        request: GlobalTaskApproveRequest,
        *,
        approver: str,
        conversation_id: str = "",
        message_id: str = "",
        outer_remaining_seconds: float | None = None,
    ) -> GlobalTaskResponse:
        safe_approver = str(approver or "").strip()
        if not safe_approver:
            raise GlobalTaskControllerError(
                "GLOBAL_TASK_APPROVAL_IDENTITY_REQUIRED",
                "批准请求必须由可信执行上下文提供审批身份。",
                status_code=403,
            )
        step_timeout = self._resolve_step_timeout(outer_remaining_seconds)
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
            task = task.model_copy(
                update={
                    "steps": steps,
                    "status": "running",
                    "pending_approval": None,
                    "assistant_message": "已确认审批，继续执行。",
                }
            )
            task = self._save(task)
            executed = self._advance(
                task,
                conversation_id=conversation_id,
                message_id=message_id,
                approved=True,
                approval_confirmed_at=confirmed_at.isoformat(),
                approval_digest=approval.digest,
                approval_task_revision=approval.task_revision,
                approver=safe_approver,
                step_timeout_seconds=step_timeout,
            )
            return self._response(executed)

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
            index = task.current_step_index
            steps = list(task.steps)
            if index < len(steps):
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
                }
            )
            return self._response(self._save(cancelled))

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

    def _refresh_claimed(self, task: LocalGlobalTaskState) -> LocalGlobalTaskState:
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
            return self._advance(task)
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

    def refresh_task(self, task_id: str) -> GlobalTaskResponse:
        """受信 UI 轮询长任务终态；非 in_progress 任务原样返回。"""

        persisted = self.store.require_task(task_id)
        if persisted.status != "in_progress":
            return self._response(persisted)
        with self._optional_mutation_claim(
            task_id,
            allowed_statuses=frozenset({"in_progress"}),
        ) as task:
            if task is None:
                return self._response(self.store.require_task(task_id))
            return self._response(self._refresh_claimed(task))

    def _resume_claimed(self, task: LocalGlobalTaskState) -> LocalGlobalTaskState:
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
            return self._advance(task)
        if task.status == "in_progress":
            return self._refresh_claimed(task)
        return task

    def _resume_task(
        self,
        task_id: str,
        *,
        wait_for_local_lock: bool,
    ) -> LocalGlobalTaskState:
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
                return self._resume_claimed(task)

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
    "GlobalTaskController",
    "GlobalTaskControllerError",
    "JobStatusReader",
]
