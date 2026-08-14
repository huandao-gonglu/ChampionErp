"""全局主 Agent 的只读规划 profile、prompt 与输出终检。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Protocol

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import DeferredToolRequests
from pydantic_ai.models import Model

from erp_web.marketplace_registry import PLATFORMS
from erp_web.schemas.draft_capabilities import DraftQuerySnapshot
from erp_web.schemas.global_tasks import (
    AnswerResolutionScope,
    GlobalPlanningDecision,
    TrustedGlobalAnswer,
)
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.draft_query_service import (
    DraftIndexReader,
    resolve_trusted_draft_answer,
)
from erp_web.stores.pydantic_message_store import PydanticMessageStore

from .ai_agent_dependencies import AiAgentDependencies
from .ai_agent_factory import (
    AiAgentExecutionError,
    AiAgentExecutionProfile,
    AiAgentFactory,
    AiAgentRunOutcome,
)
from .ai_prompt_templates import load_ai_use_case_prompt_pair, render_prompt_template
from .ai_tool_registry import AiToolSet


GLOBAL_TASK_PLAN_USE_CASE_ID = "global.task.plan"
GLOBAL_TASK_PLAN_TOOLSET_ID = "global.task.plan"
GLOBAL_TASK_PLAN_PERMISSION = "global.task.read"
GLOBAL_TASK_PLAN_RESULT_VERSION = "global_task_plan.v2"
GLOBAL_TASK_PLAN_CAPABILITIES = frozenset(
    {
        "drafts.query",
        "draft.prepare_for_market",
        "product.read",
        "category.match",
        "product.attributes.fill",
        "product.attributes.update",
        "product.images.prepare",
        "product.publish.validate",
        "product.publish.request",
    }
)


GLOBAL_TASK_PLAN_PROFILE = AiAgentExecutionProfile(
    use_case_id=GLOBAL_TASK_PLAN_USE_CASE_ID,
    output_type=GlobalPlanningDecision,
    toolset_id=GLOBAL_TASK_PLAN_TOOLSET_ID,
    budget_profile="global.task.plan.default",
    permissions=frozenset({GLOBAL_TASK_PLAN_PERMISSION}),
    timeout_seconds=60,
    max_model_requests=6,
    max_tool_calls=4,
    max_tool_output_bytes=128 * 1024,
    retries=2,
    result_version=GLOBAL_TASK_PLAN_RESULT_VERSION,
    allow_write=False,
)


class DraftSnapshotReader(Protocol):
    def save_draft_query_snapshot(
        self,
        snapshot: DraftQuerySnapshot,
    ) -> DraftQuerySnapshot:
        ...

    def load_draft_query_snapshot(
        self,
        snapshot_id: str,
    ) -> DraftQuerySnapshot | None:
        ...


class GlobalPlanningOutputValidator:
    """约束计划语法，并验证所有草稿序号都锚定真实查询快照。"""

    def __init__(
        self,
        snapshot_reader: DraftSnapshotReader,
        *,
        allowed_capabilities: frozenset[str] = GLOBAL_TASK_PLAN_CAPABILITIES,
        resolution_scope: AnswerResolutionScope | None = None,
    ) -> None:
        self.snapshot_reader = snapshot_reader
        self.allowed_capabilities = allowed_capabilities
        self.resolution_scope = AnswerResolutionScope.model_validate(
            resolution_scope or {}
        )
        self.error_code = ""

    def _retry(self, code: str, message: str) -> None:
        self.error_code = code
        raise ModelRetry(message)

    def __call__(
        self,
        ctx: RunContext[AiAgentDependencies],
        output: GlobalPlanningDecision,
    ) -> GlobalPlanningDecision:
        del ctx
        self.error_code = ""
        if output.action == "ask_user":
            return output
        snapshot = None
        if output.query_snapshot_id:
            snapshot = self.snapshot_reader.load_draft_query_snapshot(
                output.query_snapshot_id
            )
            if snapshot is None:
                self._retry(
                    "GLOBAL_PLAN_SNAPSHOT_UNKNOWN",
                    "query_snapshot_id 必须来自本次或既有 drafts_query 真实返回。",
                )
        if output.action == "answer":
            assert output.answer_kind is not None
            if output.answer_kind == "active_draft_count" and (
                self.resolution_scope.expected_product_id
                or self.resolution_scope.expected_target_platform
            ):
                self._retry(
                    "GLOBAL_PLAN_COUNT_SCOPE_CONFLICT",
                    "active_draft_count 不能用于带商品或平台绑定的任务上下文。",
                )
            if (
                output.answer_kind == "active_draft_count"
                and snapshot is not None
                and (
                    snapshot.query.scope != "active"
                    or bool(snapshot.query.target_platform)
                    or bool(snapshot.query.status)
                    or bool(snapshot.query.keyword)
                )
            ):
                self._retry(
                    "GLOBAL_PLAN_ACTIVE_SNAPSHOT_REQUIRED",
                    "active_draft_count 必须引用没有平台、状态或关键词过滤的 active 查询快照。",
                )
            if output.answer_kind == "draft_market_context":
                assert snapshot is not None
                if output.answer_draft_position is not None:
                    if output.answer_draft_position > len(snapshot.draft_ids):
                        self._retry(
                            "GLOBAL_PLAN_DRAFT_POSITION_INVALID",
                            "answer_draft_position 超出查询快照范围。",
                        )
                elif snapshot.total != 1 or len(snapshot.draft_ids) != 1:
                    self._retry(
                        "GLOBAL_PLAN_DRAFT_REFERENCE_AMBIGUOUS",
                        "draft_market_context 必须引用唯一草稿或明确序号。",
                    )
            return output

        assert output.plan is not None
        capabilities = [step.capability for step in output.plan.steps]
        unknown = sorted(set(capabilities) - self.allowed_capabilities)
        if unknown:
            self._retry(
                "GLOBAL_PLAN_CAPABILITY_FORBIDDEN",
                "计划只能使用系统明确允许的 Capability：" + "、".join(unknown),
            )
        request_indexes = [
            index
            for index, capability in enumerate(capabilities)
            if capability == "product.publish.request"
        ]
        if len(request_indexes) > 1:
            self._retry(
                "GLOBAL_PLAN_PUBLISH_DUPLICATED",
                "发布请求最多只能规划一次。",
            )
        if request_indexes:
            request_index = request_indexes[0]
            validate_indexes = [
                index
                for index, capability in enumerate(capabilities)
                if capability == "product.publish.validate"
            ]
            if (
                len(validate_indexes) != 1
                or validate_indexes[0] != request_index - 1
            ):
                self._retry(
                    "GLOBAL_PLAN_PUBLISH_ORDER_INVALID",
                    "发布请求前必须紧邻且仅有一次 product.publish.validate。",
                )
        if output.plan.draft_position is not None:
            if snapshot is None:
                self._retry(
                    "GLOBAL_PLAN_SNAPSHOT_REQUIRED",
                    "计划引用草稿序号时必须返回 query_snapshot_id。",
                )
            assert snapshot is not None
            if output.plan.draft_position > len(snapshot.draft_ids):
                self._retry(
                    "GLOBAL_PLAN_DRAFT_POSITION_INVALID",
                    "draft_position 超出查询快照范围。",
                )
        platform = output.plan.target_platform.strip().lower()
        expected_platform = self.resolution_scope.expected_target_platform
        if platform and expected_platform and platform != expected_platform:
            self._retry(
                "GLOBAL_PLAN_PLATFORM_SCOPE_MISMATCH",
                "计划 target_platform 不得覆盖任务显式绑定的平台。",
            )
        if platform and platform not in PLATFORMS:
            self._retry(
                "GLOBAL_PLAN_PLATFORM_INVALID",
                "target_platform 必须是系统已注册的平台。",
            )
        return output


@dataclass
class GlobalAgentPlanningRun:
    decision: GlobalPlanningDecision
    outcome: AiAgentRunOutcome[GlobalPlanningDecision] | None = None
    trusted_answer: TrustedGlobalAnswer | None = None

    def finish(self) -> None:
        if self.outcome is not None:
            canonical_snapshot_id = (
                self.trusted_answer.query_snapshot_id
                if self.trusted_answer is not None
                else self.decision.query_snapshot_id
            )
            result: dict[str, Any] = {
                "result_version": GLOBAL_TASK_PLAN_RESULT_VERSION,
                "action": self.decision.action,
                "step_count": (
                    len(self.decision.plan.steps)
                    if self.decision.plan is not None
                    else 0
                ),
                "query_snapshot_id": canonical_snapshot_id,
            }
            if self.trusted_answer is not None:
                result["trusted_answer"] = self.trusted_answer.model_dump(mode="json")
            self.outcome.complete(result)


class GlobalAgentService:
    """主 Agent 单一入口；ToolSet 由 composition root 显式注入。"""

    def __init__(
        self,
        *,
        app_dir: Path | str,
        app_config: dict[str, Any],
        message_store: PydanticMessageStore,
        snapshot_reader: DraftSnapshotReader,
        product_store: DraftIndexReader,
        allowed_capabilities: frozenset[str] = GLOBAL_TASK_PLAN_CAPABILITIES,
        factory: AiAgentFactory | None = None,
    ) -> None:
        self.app_dir = Path(app_dir)
        self.app_config = dict(app_config)
        self.snapshot_reader = snapshot_reader
        self.product_store = product_store
        self.allowed_capabilities = frozenset(allowed_capabilities)
        if not self.allowed_capabilities:
            raise ValueError("全局 Agent 至少需要一个可规划 Capability")
        unknown = self.allowed_capabilities - GLOBAL_TASK_PLAN_CAPABILITIES
        if unknown:
            raise ValueError(
                "全局 Agent 注册了未知 Capability：" + "、".join(sorted(unknown))
            )
        self.factory = factory or AiAgentFactory(
            app_dir=self.app_dir,
            app_config=self.app_config,
            message_store=message_store,
        )

    def _resolve_answer(
        self,
        decision: GlobalPlanningDecision,
        *,
        resolution_scope: AnswerResolutionScope,
    ) -> TrustedGlobalAnswer:
        return resolve_trusted_draft_answer(
            decision,
            product_store=self.product_store,
            snapshot_repository=self.snapshot_reader,
            resolution_scope=resolution_scope,
        )

    def plan(
        self,
        *,
        goal: str,
        toolset: AiToolSet,
        product_id: str = "",
        platform: str = "",
        recent_snapshot_id: str = "",
        model_override: Model | None = None,
    ) -> GlobalAgentPlanningRun:
        resolution_scope = AnswerResolutionScope(
            expected_product_id=product_id,
            expected_target_platform=platform,
        )
        prompt = load_ai_use_case_prompt_pair(
            self.app_dir,
            self.app_config,
            GLOBAL_TASK_PLAN_USE_CASE_ID,
        )
        instructions = prompt.get("system") or (
            "只读理解目标并输出有限顺序计划；不得执行写操作或创造 Capability。"
        )
        payload = {
            "goal": str(goal or "").strip(),
            "trusted_context": {
                "product_id": str(product_id or "").strip(),
                "platform": str(platform or "").strip().lower(),
                "recent_snapshot_id": str(recent_snapshot_id or "").strip(),
            },
            "allowed_capabilities": sorted(self.allowed_capabilities),
            "registered_platforms": list(PLATFORMS),
        }
        user_prompt = render_prompt_template(
            prompt.get("user") or "请规划以下目标：{$input_json}",
            {
                "input_json": json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            },
        )
        outcome = self.factory.run_sync(
            profile=GLOBAL_TASK_PLAN_PROFILE,
            instructions=instructions,
            user_prompt=user_prompt,
            toolset=toolset,
            output_validator=GlobalPlanningOutputValidator(
                self.snapshot_reader,
                allowed_capabilities=self.allowed_capabilities,
                resolution_scope=resolution_scope,
            ),
            business_scope={
                "product_id": str(product_id or "").strip(),
                "platform": str(platform or "").strip().lower(),
            },
            idempotency_context={
                "result_version": GLOBAL_TASK_PLAN_RESULT_VERSION
            },
            model_override=model_override,
        )
        if isinstance(outcome.output, DeferredToolRequests):
            error = AiAgentExecutionError(
                "TOOL_APPROVAL_REQUIRED",
                "全局规划只允许只读工具，不应产生审批请求。",
                conversation_id=outcome.conversation_id,
                task_run_id=outcome.task_run_id,
                run_id=outcome.run_id,
                trace_id=outcome.trace_id,
            )
            outcome.fail(error)
            raise error
        trusted_answer = None
        if outcome.output.action == "answer":
            try:
                trusted_answer = self._resolve_answer(
                    outcome.output,
                    resolution_scope=resolution_scope,
                )
            except BusinessCapabilityError as exc:
                error = AiAgentExecutionError(
                    exc.code,
                    str(exc),
                    conversation_id=outcome.conversation_id,
                    task_run_id=outcome.task_run_id,
                    run_id=outcome.run_id,
                    trace_id=outcome.trace_id,
                )
                outcome.fail(error)
                raise error from exc
        return GlobalAgentPlanningRun(
            decision=outcome.output,
            outcome=outcome,
            trusted_answer=trusted_answer,
        )


__all__ = [
    "GLOBAL_TASK_PLAN_CAPABILITIES",
    "GLOBAL_TASK_PLAN_PERMISSION",
    "GLOBAL_TASK_PLAN_PROFILE",
    "GLOBAL_TASK_PLAN_RESULT_VERSION",
    "GLOBAL_TASK_PLAN_TOOLSET_ID",
    "GLOBAL_TASK_PLAN_USE_CASE_ID",
    "GlobalAgentPlanningRun",
    "GlobalAgentService",
    "GlobalPlanningOutputValidator",
]
