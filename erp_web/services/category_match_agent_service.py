"""``category.product_match`` 的唯一 Pydantic Agent service。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Annotated, Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import DeferredToolRequests
from pydantic_ai.models import Model

from erp_web.context import get_context
from erp_web.schemas.category import (
    CATEGORY_SEARCH_PERMISSION,
    CATEGORY_SEARCH_TOOLSET_ID,
    CategoryCandidateLedger,
)
from erp_web.schemas.category import CategoryMatchTrace

from .ai_agent_dependencies import AiAgentDependencies
from .ai_agent_factory import (
    AiAgentExecutionError,
    AiAgentExecutionProfile,
    AiAgentFactory,
    AiAgentRunOutcome,
)
from .ai_prompt_templates import load_ai_use_case_prompt_pair, render_prompt_template
from .ai_tool_registry import AiToolSet


CATEGORY_MATCH_USE_CASE_ID = "category.product_match"
CATEGORY_MATCH_BUDGET_PROFILE = "category.match.default"
CATEGORY_MATCH_RESULT_VERSION = "category_match.v1"
CATEGORY_MATCH_DEADLINE_SECONDS = 60


class CategoryMatchAgentOutput(BaseModel):
    """模型边界的严格类型；平台详情终检仍由 facade 负责。"""

    model_config = ConfigDict(extra="forbid")

    selected_category_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=160),
    ]
    abstained: bool
    model_confidence: float = Field(ge=0, le=1)
    evidence: list[
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
        ]
    ] = Field(max_length=8)

    @model_validator(mode="after")
    def validate_selection_shape(self) -> "CategoryMatchAgentOutput":
        if self.abstained and self.selected_category_id:
            raise ValueError("abstained 时不得同时选择 category_id")
        if not self.abstained and not self.selected_category_id:
            raise ValueError("未 abstain 时必须选择 category_id")
        return self


CATEGORY_MATCH_AGENT_PROFILE = AiAgentExecutionProfile(
    use_case_id=CATEGORY_MATCH_USE_CASE_ID,
    output_type=CategoryMatchAgentOutput,
    toolset_id=CATEGORY_SEARCH_TOOLSET_ID,
    budget_profile=CATEGORY_MATCH_BUDGET_PROFILE,
    permissions=frozenset({CATEGORY_SEARCH_PERMISSION}),
    timeout_seconds=CATEGORY_MATCH_DEADLINE_SECONDS,
    max_model_requests=5,
    max_tool_calls=3,
    max_tool_output_bytes=32 * 1024,
    retries=2,
    result_version=CATEGORY_MATCH_RESULT_VERSION,
)


class CategoryMatchOutputValidator:
    """把 Ledger 中的确定性约束反馈给模型并在重试耗尽后保留稳定码。"""

    def __init__(self, ledger: CategoryCandidateLedger) -> None:
        self.ledger = ledger
        self.error_code = ""

    def _retry(self, code: str, message: str) -> None:
        self.error_code = code
        raise ModelRetry(message)

    def __call__(
        self,
        ctx: RunContext[AiAgentDependencies],
        output: CategoryMatchAgentOutput,
    ) -> CategoryMatchAgentOutput:
        del ctx
        self.error_code = ""
        if self.ledger.search_count == 0:
            self._retry(
                "CATEGORY_SEARCH_REQUIRED",
                "必须先调用 search_categories，再提交最终结果。",
            )
        if output.abstained:
            if self.ledger.search_count < 3:
                self._retry(
                    "CATEGORY_SEARCH_INCOMPLETE",
                    "没有匹配时必须改换关键词，完成 3 次不同的有效搜索后才能 abstain。",
                )
            return output
        if self.ledger.get(output.selected_category_id) is None:
            self._retry(
                "MODEL_SELECTED_UNKNOWN_CATEGORY",
                "selected_category_id 必须来自本次 search_categories 的真实结果。",
            )
        return output


@dataclass
class CategoryMatchAgentRun:
    """领域 facade 消费的 Agent service 结果。"""

    output: dict[str, Any]
    trace: CategoryMatchTrace
    outcome: AiAgentRunOutcome[CategoryMatchAgentOutput] | None = None

    @classmethod
    def for_test(
        cls,
        output: Mapping[str, Any],
        trace: CategoryMatchTrace | None = None,
    ) -> "CategoryMatchAgentRun":
        return cls(dict(output), dict(trace or {}), None)

    def finish_business_result(self, result: Mapping[str, Any]) -> None:
        if self.outcome is None:
            return
        decision = result.get("decision") if isinstance(result.get("decision"), Mapping) else {}
        failure = result.get("failure") if isinstance(result.get("failure"), Mapping) else {}
        self.outcome.complete(
            {
                "result_version": CATEGORY_MATCH_RESULT_VERSION,
                "status": str(result.get("status") or "failed"),
                "selected_category_id": str(result.get("selected_category_id") or ""),
                "search_count": int(decision.get("search_count") or 0),
                "error_code": str(failure.get("code") or ""),
            }
        )


def _prompt_payload(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))


def run_category_match_agent(
    payload: Mapping[str, Any],
    toolset: AiToolSet,
    ledger: CategoryCandidateLedger,
    *,
    timeout_seconds: float,
    factory: AiAgentFactory | None = None,
    model_override: Model | None = None,
) -> CategoryMatchAgentRun:
    """运行 Pydantic Agent；不存在旧协议或 Provider fallback。"""

    context = get_context()
    app_config = context.config.load_app_config()
    prompt = load_ai_use_case_prompt_pair(
        context.paths.app_dir,
        app_config,
        CATEGORY_MATCH_USE_CASE_ID,
    )
    instructions = prompt.get("system") or (
        "必须先调用 search_categories，只能选择本次工具返回的 category_id；"
        "无匹配时改换关键词搜索 3 次后 abstain。"
    )
    user_prompt = render_prompt_template(
        prompt.get("user") or "请根据以下商品事实匹配类目：{$input_json}",
        {"input_json": _prompt_payload(payload)},
    )
    agent_factory = factory or AiAgentFactory(
        app_dir=context.paths.app_dir,
        app_config=app_config,
        journal=context.ai_journal,
    )
    target = payload.get("target") if isinstance(payload.get("target"), Mapping) else {}
    outcome = agent_factory.run_sync(
        profile=CATEGORY_MATCH_AGENT_PROFILE,
        instructions=instructions,
        user_prompt=user_prompt,
        toolset=toolset,
        use_case_state=ledger,
        output_validator=CategoryMatchOutputValidator(ledger),
        business_scope={
            "platform": str(target.get("platform") or ""),
            "site": str(target.get("site") or ""),
        },
        idempotency_context={"result_version": CATEGORY_MATCH_RESULT_VERSION},
        input_summary={
            "platform": str(target.get("platform") or ""),
            "site": str(target.get("site") or ""),
            "result_version": CATEGORY_MATCH_RESULT_VERSION,
        },
        timeout_seconds=timeout_seconds,
        model_override=model_override,
    )
    if isinstance(outcome.output, DeferredToolRequests):
        error = AiAgentExecutionError(
            "AI_TOOL_APPROVAL_REQUIRED",
            "类目匹配只允许只读工具，不应产生审批请求。",
            conversation_id=outcome.conversation_id,
            task_run_id=outcome.task_run_id,
            run_id=outcome.run_id,
            trace_id=outcome.trace_id,
        )
        outcome.fail(error)
        raise error
    output = outcome.output.model_dump(mode="json")
    return CategoryMatchAgentRun(
        output=output,
        trace={
            "conversation_id": outcome.conversation_id,
            "task_run_id": outcome.task_run_id,
            "run_id": outcome.run_id,
            "trace_id": outcome.trace_id,
        },
        outcome=outcome,
    )


__all__ = [
    "CATEGORY_MATCH_AGENT_PROFILE",
    "CATEGORY_MATCH_BUDGET_PROFILE",
    "CATEGORY_MATCH_DEADLINE_SECONDS",
    "CATEGORY_MATCH_RESULT_VERSION",
    "CATEGORY_MATCH_USE_CASE_ID",
    "CategoryMatchAgentOutput",
    "CategoryMatchAgentRun",
    "CategoryMatchOutputValidator",
    "run_category_match_agent",
]
