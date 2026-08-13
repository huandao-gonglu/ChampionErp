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
from erp_web.schemas.global_tasks import GlobalPlanningDecision

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
GLOBAL_TASK_PLAN_RESULT_VERSION = "global_task_plan.v1"
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
    ) -> None:
        self.snapshot_reader = snapshot_reader
        self.allowed_capabilities = allowed_capabilities
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
        if platform and platform not in PLATFORMS:
            self._retry(
                "GLOBAL_PLAN_PLATFORM_INVALID",
                "target_platform 必须是系统已注册的平台。",
            )
        return output


@dataclass
class GlobalAgentPlanningRun:
    decision: GlobalPlanningDecision
    conversation_id: str
    outcome: AiAgentRunOutcome[GlobalPlanningDecision] | None = None

    def finish(self) -> None:
        if self.outcome is not None:
            self.outcome.complete(
                {
                    "result_version": GLOBAL_TASK_PLAN_RESULT_VERSION,
                    "action": self.decision.action,
                    "step_count": (
                        len(self.decision.plan.steps)
                        if self.decision.plan is not None
                        else 0
                    ),
                    "query_snapshot_id": self.decision.query_snapshot_id,
                }
            )


class GlobalAgentService:
    """主 Agent 单一入口；ToolSet 由 composition root 显式注入。"""

    def __init__(
        self,
        *,
        app_dir: Path | str,
        app_config: dict[str, Any],
        journal: Any,
        snapshot_reader: DraftSnapshotReader,
        allowed_capabilities: frozenset[str] = GLOBAL_TASK_PLAN_CAPABILITIES,
        factory: AiAgentFactory | None = None,
    ) -> None:
        self.app_dir = Path(app_dir)
        self.app_config = dict(app_config)
        self.snapshot_reader = snapshot_reader
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
            journal=journal,
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
        parent_conversation_id: str | None = None,
    ) -> GlobalAgentPlanningRun:
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
            ),
            business_scope={
                "product_id": str(product_id or "").strip(),
                "platform": str(platform or "").strip().lower(),
            },
            idempotency_context={
                "result_version": GLOBAL_TASK_PLAN_RESULT_VERSION
            },
            input_summary={
                "use_case_id": GLOBAL_TASK_PLAN_USE_CASE_ID,
                "product_id": str(product_id or "").strip(),
                "platform": str(platform or "").strip().lower(),
            },
            model_override=model_override,
            parent_conversation_id=parent_conversation_id,
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
        return GlobalAgentPlanningRun(
            decision=outcome.output,
            conversation_id=outcome.conversation_id,
            outcome=outcome,
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
