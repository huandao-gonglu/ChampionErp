"""单进程、本地全局任务的顺序状态机。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from erp_web.schemas.global_tasks import (
    CapabilityError,
    CapabilityResult,
    GlobalPlanningDecision,
    GlobalTaskInputRequest,
    GlobalTaskStartRequest,
    LocalGlobalTaskState,
    LocalTaskStep,
    PublishConfirmation,
    RequiredInput,
    TERMINAL_GLOBAL_TASK_STATUSES,
)
from erp_web.stores.global_task_store import (
    GlobalTaskStoreError,
    LocalGlobalTaskStore,
)


logger = logging.getLogger(__name__)


class GlobalTaskControllerError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        self.code = str(code or "GLOBAL_TASK_INVALID")
        self.status_code = int(status_code)
        super().__init__(message)


@dataclass(frozen=True)
class GlobalTaskPlanningOutcome:
    decision: GlobalPlanningDecision
    execution_conversation_id: str = ""
    finish: Callable[[], None] | None = None


class GlobalTaskPlanner(Protocol):
    def __call__(
        self,
        task: LocalGlobalTaskState,
        supplement: str,
    ) -> GlobalTaskPlanningOutcome:
        ...


class GlobalTaskCapability(Protocol):
    def __call__(
        self,
        task: LocalGlobalTaskState,
        step: LocalTaskStep,
    ) -> CapabilityResult[Any]:
        ...


class PublishStatusReader(Protocol):
    def __call__(self, job_id: str) -> dict[str, Any]:
        ...


ProjectionWriter = Callable[[str, str, dict[str, Any]], None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _result_mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return dict(value) if isinstance(value, Mapping) else {}


class GlobalTaskController:
    """严格顺序推进 Capability；不实现 model/tool loop。"""

    def __init__(
        self,
        *,
        store: LocalGlobalTaskStore,
        planner: GlobalTaskPlanner,
        capabilities: Mapping[str, GlobalTaskCapability],
        publish_status_reader: PublishStatusReader,
        projection_writer: ProjectionWriter | None = None,
    ) -> None:
        self.store = store
        self.planner = planner
        self.capabilities = dict(capabilities)
        self.publish_status_reader = publish_status_reader
        self.projection_writer = projection_writer

    def _project(
        self,
        task: LocalGlobalTaskState,
        name: str,
        value: dict[str, Any],
    ) -> None:
        if self.projection_writer is None or not task.ai_work_conversation_id:
            return
        try:
            self.projection_writer(
                task.ai_work_conversation_id,
                name,
                {"task_id": task.task_id, **value},
            )
        except Exception:
            logger.exception(
                "全局任务 %s 的 AI Work 投影写入失败",
                task.task_id,
            )

    def _save(
        self,
        task: LocalGlobalTaskState,
        *,
        project_state: bool = True,
    ) -> LocalGlobalTaskState:
        saved = self.store.save_task(task)
        if project_state:
            self._project(
                saved,
                "global.task_state",
                {
                    "status": saved.status,
                    "summary": saved.assistant_message,
                },
            )
        return saved

    def create_task(
        self,
        request: GlobalTaskStartRequest,
        *,
        ai_work_conversation_id: str,
    ) -> LocalGlobalTaskState:
        now = _now()
        task = LocalGlobalTaskState(
            task_id=f"gtask_{uuid4().hex}",
            task_kind=request.task_kind,
            goal=request.goal.strip(),
            product_id=request.product_id.strip(),
            platform=request.platform.strip().lower(),
            status="planning",
            steps=[],
            current_step_index=0,
            draft_query_snapshot_id=request.draft_query_snapshot_id.strip(),
            ai_work_conversation_id=ai_work_conversation_id,
            assistant_message="正在理解目标并制定计划。",
            created_at=now,
            updated_at=now,
        )
        task = self.store.create_task(task)
        self._project(
            task,
            "global.user_message",
            {"message": task.goal},
        )
        self._project(
            task,
            "global.task_state",
            {"status": task.status, "summary": task.assistant_message},
        )
        return self._plan_and_run(task)

    def _record_planning_outcome(
        self,
        task: LocalGlobalTaskState,
        outcome: GlobalTaskPlanningOutcome,
    ) -> LocalGlobalTaskState:
        if outcome.execution_conversation_id:
            execution_ids = list(task.agent_execution_conversation_ids)
            if outcome.execution_conversation_id not in execution_ids:
                execution_ids.append(outcome.execution_conversation_id)
            task = task.model_copy(
                update={"agent_execution_conversation_ids": execution_ids}
            )
            self._project(
                task,
                "global.agent_execution_link",
                {"conversation_id": outcome.execution_conversation_id},
            )
        if outcome.finish is not None:
            outcome.finish()
        return task

    def _validate_plan_contract(
        self,
        task: LocalGlobalTaskState,
        decision: GlobalPlanningDecision,
    ) -> LocalGlobalTaskState | None:
        """在模型输出校验之外，守住 Controller 的静态执行边界。"""

        assert decision.plan is not None
        proposals = list(decision.plan.steps)
        completed_prefix_count = sum(
            step.status == "completed"
            for step in task.steps[: task.current_step_index]
        )
        if not 1 <= len(proposals) or completed_prefix_count + len(proposals) > 12:
            return task.model_copy(
                update={
                    "status": "failed",
                    "pending_inputs": [],
                    "pending_input_owner": "none",
                    "error_code": "GLOBAL_TASK_PLAN_STEP_COUNT_INVALID",
                    "error_message": "顺序计划必须包含 1 到 12 个步骤。",
                    "assistant_message": "任务计划的步骤数量无效，未执行业务操作。",
                }
            )

        missing = sorted(
            {proposal.capability for proposal in proposals}
            - self.capabilities.keys()
        )
        if missing:
            return task.model_copy(
                update={
                    "status": "failed",
                    "pending_inputs": [],
                    "pending_input_owner": "none",
                    "error_code": "GLOBAL_TASK_CAPABILITY_UNAVAILABLE",
                    "error_message": "计划引用尚未接入的 Capability："
                    + "、".join(missing),
                    "assistant_message": "当前计划包含尚未接入的业务能力。",
                }
            )

        publish_indexes = [
            index
            for index, proposal in enumerate(proposals)
            if proposal.capability == "product.publish.request"
        ]
        if len(publish_indexes) > 1:
            return task.model_copy(
                update={
                    "status": "failed",
                    "pending_inputs": [],
                    "pending_input_owner": "none",
                    "error_code": "GLOBAL_TASK_PLAN_PUBLISH_REQUEST_MULTIPLE",
                    "error_message": "一个顺序计划最多只能包含一次发布提交。",
                    "assistant_message": "任务计划包含重复发布请求，未执行业务操作。",
                }
            )
        if publish_indexes:
            publish_index = publish_indexes[0]
            validation_indexes = [
                index
                for index, proposal in enumerate(proposals)
                if proposal.capability == "product.publish.validate"
            ]
            if (
                len(validation_indexes) != 1
                or validation_indexes[0] != publish_index - 1
            ):
                return task.model_copy(
                    update={
                        "status": "failed",
                        "pending_inputs": [],
                        "pending_input_owner": "none",
                        "error_code": "GLOBAL_TASK_PLAN_PUBLISH_ORDER_INVALID",
                        "error_message": (
                            "发布提交前必须紧邻且仅执行一次确定性发布校验。"
                        ),
                        "assistant_message": "任务计划缺少发布前校验，未执行业务操作。",
                    }
                )
            if publish_index != len(proposals) - 1:
                return task.model_copy(
                    update={
                        "status": "failed",
                        "pending_inputs": [],
                        "pending_input_owner": "none",
                        "error_code": "GLOBAL_TASK_PLAN_PUBLISH_ORDER_INVALID",
                        "error_message": "发布提交必须是顺序计划的最后一步。",
                        "assistant_message": "任务计划的发布步骤顺序无效，未执行业务操作。",
                    }
                )
        return None

    def _plan_and_run(
        self,
        task: LocalGlobalTaskState,
        supplement: str = "",
    ) -> LocalGlobalTaskState:
        try:
            outcome = self.planner(task, supplement)
            task = self._record_planning_outcome(task, outcome)
            decision = outcome.decision
        except Exception as exc:
            task = task.model_copy(
                update={
                    "status": "failed",
                    "pending_inputs": [],
                    "pending_input_owner": "none",
                    "assistant_message": "任务规划失败，未执行任何业务步骤。",
                    "error_code": str(
                        getattr(exc, "code", "GLOBAL_TASK_PLANNING_FAILED")
                    ),
                    "error_message": str(exc),
                }
            )
            saved = self._save(task)
            self._project(
                saved,
                "global.assistant_message",
                {"message": saved.assistant_message},
            )
            return saved

        if decision.action == "ask_user":
            required = RequiredInput(
                key="clarification",
                label="补充说明",
                reason=decision.question,
            )
            task = task.model_copy(
                update={
                    "status": "needs_input",
                    "pending_inputs": [required],
                    "pending_input_owner": "planning",
                    "assistant_message": decision.question,
                    "plan_explanation": decision.explanation,
                }
            )
            saved = self._save(task)
            self._project(
                saved,
                "global.assistant_message",
                {"message": saved.assistant_message},
            )
            return saved

        if decision.action == "answer":
            snapshot = self.store.load_draft_query_snapshot(
                decision.query_snapshot_id
            )
            if snapshot is None:
                task = task.model_copy(
                    update={
                        "status": "failed",
                        "pending_inputs": [],
                        "pending_input_owner": "none",
                        "error_code": "GLOBAL_PLAN_SNAPSHOT_NOT_FOUND",
                        "error_message": "规划引用的草稿查询快照不存在。",
                        "assistant_message": "无法验证查询结果，请重新查询。",
                    }
                )
            else:
                total = int(getattr(snapshot, "total", len(snapshot.draft_ids)))
                task = task.model_copy(
                    update={
                        "status": "completed",
                        "pending_inputs": [],
                        "pending_input_owner": "none",
                        "draft_query_snapshot_id": snapshot.snapshot_id,
                        "assistant_message": f"当前查询匹配 {total} 个草稿。",
                        "plan_explanation": decision.explanation,
                    }
                )
            saved = self._save(task)
            self._project(
                saved,
                "global.assistant_message",
                {"message": saved.assistant_message},
            )
            return saved

        assert decision.plan is not None
        invalid_task = self._validate_plan_contract(task, decision)
        if invalid_task is not None:
            saved = self._save(invalid_task)
            self._project(
                saved,
                "global.assistant_message",
                {"message": saved.assistant_message},
            )
            return saved
        snapshot_id = decision.query_snapshot_id or task.draft_query_snapshot_id
        completed_prefix = list(task.steps[: task.current_step_index])
        if any(step.status != "completed" for step in completed_prefix):
            completed_prefix = []
        shared_inputs: dict[str, Any] = (
            dict(task.steps[task.current_step_index].inputs)
            if task.current_step_index < len(task.steps)
            else {}
        )
        if task.product_id:
            shared_inputs["product_id"] = task.product_id
        target_platform = (
            decision.plan.target_platform.strip().lower() or task.platform
        )
        if target_platform:
            shared_inputs["platform"] = target_platform
        if decision.plan.draft_position is not None:
            snapshot = self.store.load_draft_query_snapshot(snapshot_id)
            if snapshot is None or decision.plan.draft_position > len(snapshot.draft_ids):
                task = task.model_copy(
                    update={
                        "status": "failed",
                        "pending_inputs": [],
                        "pending_input_owner": "none",
                        "error_code": "GLOBAL_PLAN_DRAFT_POSITION_INVALID",
                        "error_message": "规划引用的草稿序号无法从快照解析。",
                        "assistant_message": "草稿选择已经失效，请重新查询。",
                    }
                )
                saved = self._save(task)
                self._project(
                    saved,
                    "global.assistant_message",
                    {"message": saved.assistant_message},
                )
                return saved
            shared_inputs["draft_id"] = snapshot.draft_ids[
                decision.plan.draft_position - 1
            ]
            shared_inputs["draft_position"] = decision.plan.draft_position
        if snapshot_id:
            shared_inputs["snapshot_id"] = snapshot_id
        parameters = decision.plan.parameters
        planned_steps = [
            LocalTaskStep(
                step_id=f"step_{index + 1}_{proposal.local_key}",
                capability=proposal.capability,
                objective=proposal.objective,
                inputs={
                    **shared_inputs,
                    **(
                        {"updates": dict(parameters.attribute_updates)}
                        if proposal.capability == "product.attributes.update"
                        and parameters.attribute_updates
                        else {}
                    ),
                    **(
                        {
                            "provided_attributes": dict(
                                parameters.provided_attributes
                            )
                        }
                        if proposal.capability
                        in {
                            "product.attributes.fill",
                            "draft.prepare_for_market",
                        }
                        and parameters.provided_attributes
                        else {}
                    ),
                    **(
                        {"pricing_input": dict(parameters.pricing_input)}
                        if proposal.capability == "draft.prepare_for_market"
                        and parameters.pricing_input
                        else {}
                    ),
                    **(
                        {"regenerate_copy": True}
                        if proposal.capability == "draft.prepare_for_market"
                        and parameters.regenerate_copy
                        else {}
                    ),
                },
            )
            for index, proposal in enumerate(
                decision.plan.steps,
                start=len(completed_prefix),
            )
        ]
        steps = completed_prefix + planned_steps
        publish_step = next(
            (
                step
                for step in steps
                if step.capability == "product.publish.request"
            ),
            None,
        )
        task = task.model_copy(
            update={
                "status": "running",
                "steps": steps,
                "current_step_index": len(completed_prefix),
                "pending_inputs": [],
                "pending_input_owner": "none",
                "publish_idempotency_key": (
                    f"global-task:{task.task_id}:step:{publish_step.step_id}"
                    if publish_step is not None
                    else ""
                ),
                "draft_query_snapshot_id": snapshot_id,
                "platform": target_platform,
                "publish_confirmation": PublishConfirmation(),
                "publish_job_id": "",
                "error_code": "",
                "error_message": "",
                "assistant_message": decision.explanation or "计划已创建，开始执行。",
                "plan_explanation": decision.explanation,
            }
        )
        task = self._save(task)
        return self._advance(task)

    def _failed_capability_result(
        self,
        exc: Exception,
    ) -> CapabilityResult[Any]:
        return CapabilityResult(
            status="failed",
            summary="业务步骤执行失败。",
            error=CapabilityError(
                code=str(getattr(exc, "code", "CAPABILITY_EXECUTION_FAILED")),
                message=str(exc) or "业务步骤执行失败。",
                retryable=bool(getattr(exc, "retryable", False)),
            ),
            agent_execution_conversation_ids=list(
                getattr(exc, "agent_execution_conversation_ids", ())
            ),
        )

    def _advance(self, task: LocalGlobalTaskState) -> LocalGlobalTaskState:
        while (
            task.status == "running"
            and task.current_step_index < len(task.steps)
        ):
            index = task.current_step_index
            step = task.steps[index]
            if step.status == "completed":
                task = task.model_copy(
                    update={"current_step_index": index + 1}
                )
                continue
            running_step = step.model_copy(update={"status": "running"})
            steps = list(task.steps)
            steps[index] = running_step
            task = self._save(task.model_copy(update={"steps": steps}))
            capability = self.capabilities[running_step.capability]
            try:
                result = CapabilityResult[Any].model_validate(
                    capability(task, running_step)
                )
            except Exception as exc:
                result = self._failed_capability_result(exc)
            task = self._persist_capability_execution_links(task, result)
            task = self._apply_capability_result(task, index, result)
            task = self._save(task)
        if task.status == "running" and task.current_step_index >= len(task.steps):
            task = task.model_copy(
                update={
                    "status": "completed",
                    "assistant_message": "任务已完成。",
                }
            )
            task = self._save(task)
            self._project(
                task,
                "global.assistant_message",
                {"message": task.assistant_message},
            )
        return task

    def _persist_capability_execution_links(
        self,
        task: LocalGlobalTaskState,
        result: CapabilityResult[Any],
    ) -> LocalGlobalTaskState:
        raw_ids: list[Any] = list(result.agent_execution_conversation_ids)
        mapping = _result_mapping(result.result)
        if mapping.get("conversation_id"):
            raw_ids.append(mapping["conversation_id"])
        nested_ids = mapping.get("agent_execution_conversation_ids")
        if isinstance(nested_ids, list):
            raw_ids.extend(nested_ids)
        discovered = list(
            dict.fromkeys(
                str(value or "").strip()
                for value in raw_ids
                if str(value or "").strip()
            )
        )
        if not discovered:
            return task
        existing = list(task.agent_execution_conversation_ids)
        added: list[str] = []
        for conversation_id in discovered:
            if conversation_id not in existing:
                existing.append(conversation_id)
                added.append(conversation_id)
        if not added:
            return task
        # execution link 是 Capability 已经发生的事实。先将事实写入 durable
        # task，再做 AI Work 投影和状态迁移，避免 needs_input/failed 分支丢链。
        saved = self._save(
            task.model_copy(
                update={"agent_execution_conversation_ids": existing}
            ),
            project_state=False,
        )
        for conversation_id in added:
            self._project(
                saved,
                "global.agent_execution_link",
                {"conversation_id": conversation_id},
            )
        return saved

    def _apply_capability_result(
        self,
        task: LocalGlobalTaskState,
        index: int,
        result: CapabilityResult[Any],
    ) -> LocalGlobalTaskState:
        step = task.steps[index]
        steps = list(task.steps)
        if result.status == "completed":
            mapping = _result_mapping(result.result)
            steps[index] = step.model_copy(
                update={
                    "status": "completed",
                    "result_summary": result.summary,
                    "result_ref": str(
                        mapping.get("draft_id")
                        or mapping.get("product_id")
                        or mapping.get("snapshot_id")
                        or ""
                    ),
                    "error_code": "",
                }
            )
            updates: dict[str, Any] = {
                "steps": steps,
                "current_step_index": index + 1,
                "assistant_message": result.summary,
            }
            if step.capability == "drafts.query" and mapping.get("snapshot_id"):
                updates["draft_query_snapshot_id"] = str(mapping["snapshot_id"])
            if step.capability == "product.publish.validate":
                if not mapping.get("passed"):
                    steps[index] = steps[index].model_copy(
                        update={
                            "status": "failed",
                            "error_code": "PUBLISH_VALIDATION_FAILED",
                        }
                    )
                    updates.update(
                        {
                            "steps": steps,
                            "status": "failed",
                            "error_code": "PUBLISH_VALIDATION_FAILED",
                            "error_message": "发布确定性校验未通过。",
                            "assistant_message": "发布校验未通过，未进入人工确认。",
                        }
                    )
                else:
                    digest = str(mapping.get("validation_digest") or "")
                    summary = mapping.get("summary")
                    if not digest or not isinstance(summary, dict) or not summary:
                        updates.update(
                            {
                                "status": "failed",
                                "error_code": "PUBLISH_VALIDATION_RESULT_INVALID",
                                "error_message": "发布校验缺少摘要或 digest。",
                                "assistant_message": "发布校验结果不完整。",
                            }
                        )
                    elif (
                        index + 1 < len(steps)
                        and steps[index + 1].capability
                        == "product.publish.request"
                    ):
                        updates.update(
                            {
                                "status": "waiting_publish_confirmation",
                                "publish_confirmation": PublishConfirmation(
                                    status="pending",
                                    validation_digest=digest,
                                    summary=summary,
                                ),
                                "assistant_message": "发布校验已通过，请确认发布。",
                            }
                        )
            return task.model_copy(update=updates)

        if result.status == "needs_input":
            steps[index] = step.model_copy(
                update={
                    "status": "needs_input",
                    "result_summary": result.summary,
                }
            )
            paused = task.model_copy(
                update={
                    "steps": steps,
                    "status": "needs_input",
                    "pending_inputs": result.required_inputs,
                    "pending_input_owner": "capability",
                    "assistant_message": result.summary,
                }
            )
            self._project(
                paused,
                "global.assistant_message",
                {"message": paused.assistant_message},
            )
            return paused

        if result.status == "in_progress":
            assert result.job_id is not None
            steps[index] = step.model_copy(
                update={
                    "status": "running",
                    "result_summary": result.summary,
                    "result_ref": result.job_id,
                }
            )
            return task.model_copy(
                update={
                    "steps": steps,
                    "status": "waiting_publish_result",
                    "pending_inputs": [],
                    "pending_input_owner": "none",
                    "publish_job_id": result.job_id,
                    "assistant_message": result.summary,
                }
            )

        assert result.error is not None
        if result.error.code == "PUBLISH_CONFIRMATION_STALE":
            validate_index = next(
                (
                    candidate
                    for candidate in range(index - 1, -1, -1)
                    if steps[candidate].capability == "product.publish.validate"
                ),
                None,
            )
            if validate_index is not None:
                steps[validate_index] = steps[validate_index].model_copy(
                    update={
                        "status": "pending",
                        "result_summary": "",
                        "result_ref": "",
                        "error_code": "",
                    }
                )
                steps[index] = step.model_copy(
                    update={"status": "pending", "error_code": ""}
                )
                return task.model_copy(
                    update={
                        "steps": steps,
                        "current_step_index": validate_index,
                        "status": "running",
                        "pending_inputs": [],
                        "pending_input_owner": "none",
                        "publish_confirmation": PublishConfirmation(),
                        "assistant_message": "发布内容已变化，正在重新校验。",
                    }
                )
        steps[index] = step.model_copy(
            update={"status": "failed", "error_code": result.error.code}
        )
        return task.model_copy(
            update={
                "steps": steps,
                "status": "failed",
                "pending_inputs": [],
                "pending_input_owner": "none",
                "error_code": result.error.code,
                "error_message": result.error.message,
                "assistant_message": result.summary,
            }
        )

    def submit_input(
        self,
        request: GlobalTaskInputRequest,
    ) -> LocalGlobalTaskState:
        with self.store.task_lock(request.task_id):
            task = self.store.require_task(request.task_id)
            if task.status != "needs_input":
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_INPUT_NOT_EXPECTED",
                    "当前任务不在等待补充资料。",
                    status_code=409,
                )
            expected = {item.key for item in task.pending_inputs}
            unknown = sorted(request.inputs.keys() - expected)
            if unknown:
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_INPUT_FIELD_UNKNOWN",
                    "提交了当前步骤未请求的字段：" + "、".join(unknown),
                )
            projected_message = request.message.strip()
            if not projected_message and request.inputs:
                projected_message = "已补充字段：" + "、".join(
                    sorted(request.inputs)
                )
            if projected_message:
                self._project(
                    task,
                    "global.user_message",
                    {"message": projected_message},
                )
            if task.pending_input_owner == "planning":
                clarification = request.inputs.get("clarification")
                supplement = str(clarification or "").strip()
                if request.message.strip() and request.message.strip() != supplement:
                    supplement = "\n".join(
                        item
                        for item in (supplement, request.message.strip())
                        if item
                    )
                if not supplement:
                    raise GlobalTaskControllerError(
                        "GLOBAL_TASK_CLARIFICATION_REQUIRED",
                        "请提交规划所需的补充说明。",
                    )
                return self._plan_and_run(task, supplement)
            if not request.inputs:
                return self._plan_and_run(task, request.message)
            index = task.current_step_index
            if index >= len(task.steps):
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_STEP_MISSING",
                    "等待资料的任务没有对应步骤。",
                    status_code=409,
                )
            step = task.steps[index]
            pending_by_key = {item.key: item for item in task.pending_inputs}
            merged_inputs = dict(step.inputs)
            for key, value in request.inputs.items():
                required = pending_by_key[key]
                if required.input_owner == "step":
                    merged_inputs[key] = value
                    continue
                nested = merged_inputs.get(required.input_owner)
                nested_values = dict(nested) if isinstance(nested, dict) else {}
                nested_values[key] = value
                merged_inputs[required.input_owner] = nested_values
            if request.message.strip():
                merged_message = request.message.strip()
                if merged_inputs.get("additional_context"):
                    merged_message = (
                        str(merged_inputs["additional_context"]).strip()
                        + "\n"
                        + merged_message
                    ).strip()
                merged_inputs["additional_context"] = merged_message
            remaining = [
                item for item in task.pending_inputs if item.key not in request.inputs
            ]
            steps = list(task.steps)
            steps[index] = step.model_copy(
                update={
                    "inputs": merged_inputs,
                    "status": "needs_input" if remaining else "pending",
                }
            )
            task = task.model_copy(
                update={
                    "steps": steps,
                    "pending_inputs": remaining,
                    "pending_input_owner": (
                        "capability" if remaining else "none"
                    ),
                    "status": "needs_input" if remaining else "running",
                    "assistant_message": (
                        "仍有资料需要补充。" if remaining else "资料已收到，继续执行。"
                    ),
                }
            )
            task = self._save(task)
            return task if remaining else self._advance(task)

    def confirm_publish(self, task_id: str) -> LocalGlobalTaskState:
        with self.store.task_lock(task_id):
            task = self.store.require_task(task_id)
            if task.status in {"waiting_publish_result", "completed", "failed"}:
                return self.refresh(task)
            if (
                task.status != "waiting_publish_confirmation"
                or task.publish_confirmation.status != "pending"
            ):
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_PUBLISH_CONFIRM_NOT_EXPECTED",
                    "当前任务不在等待发布确认。",
                    status_code=409,
                )
            confirmation = task.publish_confirmation.model_copy(
                update={"status": "confirmed", "confirmed_at": _now()}
            )
            task = task.model_copy(
                update={
                    "status": "running",
                    "pending_inputs": [],
                    "pending_input_owner": "none",
                    "publish_confirmation": confirmation,
                    "assistant_message": "发布已确认，正在提交。",
                }
            )
            return self._advance(self._save(task))

    def cancel(self, task_id: str) -> LocalGlobalTaskState:
        with self.store.task_lock(task_id):
            task = self.store.require_task(task_id)
            if task.status in TERMINAL_GLOBAL_TASK_STATUSES:
                return task
            if task.status == "waiting_publish_result":
                raise GlobalTaskControllerError(
                    "GLOBAL_TASK_PUBLISH_ALREADY_SUBMITTED",
                    "发布已经提交，不能用取消任务撤回外部平台操作。",
                    status_code=409,
                )
            task = task.model_copy(
                update={
                    "status": "cancelled",
                    "pending_inputs": [],
                    "pending_input_owner": "none",
                    "assistant_message": "任务已取消，未再执行后续步骤。",
                }
            )
            return self._save(task)

    def refresh(self, task: LocalGlobalTaskState) -> LocalGlobalTaskState:
        if task.status != "waiting_publish_result":
            return task
        try:
            status = self.publish_status_reader(task.publish_job_id)
        except Exception as exc:
            logger.warning(
                "读取发布任务 %s 状态失败：%s",
                task.publish_job_id,
                exc,
            )
            return task
        platforms = (
            status.get("platforms")
            if isinstance(status.get("platforms"), dict)
            else {}
        )
        platform_statuses = [
            str(item.get("status") or "").strip().lower()
            for item in platforms.values()
            if isinstance(item, dict)
        ]
        if not platform_statuses or any(
            item in {"queued", "pending", "running", "retrying"}
            for item in platform_statuses
        ):
            return task
        index = task.current_step_index
        steps = list(task.steps)
        if platform_statuses and all(item == "success" for item in platform_statuses):
            if index < len(steps):
                steps[index] = steps[index].model_copy(
                    update={
                        "status": "completed",
                        "result_summary": "平台已确认发布成功。",
                        "result_ref": task.publish_job_id,
                    }
                )
            task = task.model_copy(
                update={
                    "steps": steps,
                    "current_step_index": min(index + 1, len(steps)),
                    "status": "completed",
                    "assistant_message": "平台已确认发布成功，任务完成。",
                }
            )
        else:
            errors = [
                str(item.get("error") or "")
                for item in platforms.values()
                if isinstance(item, dict) and item.get("error")
            ]
            if index < len(steps):
                steps[index] = steps[index].model_copy(
                    update={
                        "status": "failed",
                        "error_code": "PUBLISH_PLATFORM_FAILED",
                        "result_ref": task.publish_job_id,
                    }
                )
            task = task.model_copy(
                update={
                    "steps": steps,
                    "status": "failed",
                    "error_code": "PUBLISH_PLATFORM_FAILED",
                    "error_message": "；".join(errors) or "平台发布失败。",
                    "assistant_message": "平台返回发布失败，任务未完成。",
                }
            )
        saved = self._save(task)
        self._project(
            saved,
            "global.assistant_message",
            {"message": saved.assistant_message},
        )
        return saved

    def get_state(self, task_id: str) -> LocalGlobalTaskState:
        with self.store.task_lock(task_id):
            task = self.store.require_task(task_id)
            if task.status == "planning":
                return self._plan_and_run(task)
            if task.status == "running":
                steps = list(task.steps)
                if task.current_step_index < len(steps):
                    current = steps[task.current_step_index]
                    if current.status == "running":
                        steps[task.current_step_index] = current.model_copy(
                            update={"status": "pending"}
                        )
                        task = self._save(
                            task.model_copy(update={"steps": steps})
                        )
                return self._advance(task)
            return self.refresh(task)


__all__ = [
    "GlobalTaskCapability",
    "GlobalTaskController",
    "GlobalTaskControllerError",
    "GlobalTaskPlanningOutcome",
    "GlobalTaskPlanner",
    "ProjectionWriter",
    "PublishStatusReader",
]
