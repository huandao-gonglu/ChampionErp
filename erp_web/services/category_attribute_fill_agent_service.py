"""``category.attribute_fill`` 的唯一 Pydantic Agent service。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Annotated, Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.models import Model

from erp_web.context import get_context
from erp_web.schemas.category_attribute import (
    CATEGORY_ATTRIBUTE_VALUE_PERMISSION,
    CATEGORY_ATTRIBUTE_VALUE_TOOLSET_ID,
    CategoryAttributeValueLedger,
)

from .ai_agent_dependencies import AiAgentDependencies
from .ai_agent_factory import (
    AiAgentExecutionProfile,
    AiAgentFactory,
    AiAgentRunOutcome,
)
from .ai_prompt_templates import load_ai_use_case_prompt_pair, render_prompt_template
from .ai_tool_registry import AiToolSet


CATEGORY_ATTRIBUTE_FILL_USE_CASE_ID = "category.attribute_fill"
CATEGORY_ATTRIBUTE_FILL_BUDGET_PROFILE = "category.attribute_fill.default"
CATEGORY_ATTRIBUTE_FILL_RESULT_VERSION = "category_attribute_fill.v2"
CATEGORY_ATTRIBUTE_FILL_DEADLINE_SECONDS = 120


class CategoryAttributeAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attribute_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
    ]
    value: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    ]
    dictionary_value_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=160),
    ] = ""


class CategoryAttributeReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
    ]
    reason: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
    ]


class CategoryAttributeFillAgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignments: list[CategoryAttributeAssignment] = Field(max_length=100)
    need_review: list[CategoryAttributeReview] = Field(max_length=100)


CATEGORY_ATTRIBUTE_FILL_AGENT_PROFILE = AiAgentExecutionProfile(
    use_case_id=CATEGORY_ATTRIBUTE_FILL_USE_CASE_ID,
    output_type=CategoryAttributeFillAgentOutput,
    toolset_id=CATEGORY_ATTRIBUTE_VALUE_TOOLSET_ID,
    budget_profile=CATEGORY_ATTRIBUTE_FILL_BUDGET_PROFILE,
    permissions=frozenset({CATEGORY_ATTRIBUTE_VALUE_PERMISSION}),
    timeout_seconds=CATEGORY_ATTRIBUTE_FILL_DEADLINE_SECONDS,
    max_model_requests=8,
    max_tool_calls=4,
    max_tool_output_bytes=96 * 1024,
    retries=2,
    result_version=CATEGORY_ATTRIBUTE_FILL_RESULT_VERSION,
)


class CategoryAttributeFillOutputValidator:
    """强制字典值只能来自本次工具结果；普通选项允许自定义文本。"""

    def __init__(self, ledger: CategoryAttributeValueLedger) -> None:
        self.ledger = ledger
        self.error_code = ""

    def _retry(self, code: str, message: str) -> None:
        self.error_code = code
        raise ModelRetry(message)

    def __call__(
        self,
        ctx: RunContext[AiAgentDependencies],
        output: CategoryAttributeFillAgentOutput,
    ) -> CategoryAttributeFillAgentOutput:
        del ctx
        self.error_code = ""
        assignment_counts: dict[str, int] = {}
        assigned_ids: set[str] = set()
        for assignment in output.assignments:
            attr_id = assignment.attribute_id
            definition = self.ledger.definition(attr_id)
            if definition is None:
                self._retry(
                    "MODEL_SELECTED_UNKNOWN_ATTRIBUTE",
                    f"attribute_id {attr_id} 不属于当前类目属性。",
                )
            assignment_counts[attr_id] = assignment_counts.get(attr_id, 0) + 1
            assigned_ids.add(attr_id)
            value_mode = str(definition.get("value_mode") or "free_text")
            if value_mode == "strict_enum":
                if not assignment.dictionary_value_id:
                    self._retry(
                        "ATTRIBUTE_ENUM_ID_REQUIRED",
                        f"强制枚举属性 {attr_id} 必须返回工具候选的 dictionary_value_id。",
                    )
                candidate = self.ledger.get(
                    attr_id,
                    assignment.dictionary_value_id,
                )
                if candidate is None:
                    self._retry(
                        "ATTRIBUTE_ENUM_VALUE_NOT_RETURNED",
                        f"强制枚举属性 {attr_id} 只能选择本次工具真实返回的值。",
                    )
                if candidate["value"].casefold() != assignment.value.casefold():
                    self._retry(
                        "ATTRIBUTE_ENUM_LABEL_MISMATCH",
                        f"属性 {attr_id} 的 value 必须与 dictionary_value_id 对应的工具值一致。",
                    )
            elif assignment.dictionary_value_id:
                self._retry(
                    "ATTRIBUTE_CUSTOM_VALUE_ID_FORBIDDEN",
                    f"非强制枚举属性 {attr_id} 应直接填写 value，不得填写 dictionary_value_id。",
                )

        for attr_id, count in assignment_counts.items():
            definition = self.ledger.definition(attr_id) or {}
            maximum = int(definition.get("max_value_count") or 0)
            if (
                definition.get("value_mode") != "strict_enum"
                or not definition.get("is_collection")
            ) and count > 1:
                self._retry(
                    "ATTRIBUTE_VALUE_COUNT_INVALID",
                    f"属性 {attr_id} 只能填写一个值。",
                )
            if maximum > 0 and count > maximum:
                self._retry(
                    "ATTRIBUTE_VALUE_COUNT_INVALID",
                    f"属性 {attr_id} 最多填写 {maximum} 个值。",
                )

        review_ids: set[str] = set()
        for review in output.need_review:
            if self.ledger.definition(review.id) is None:
                self._retry(
                    "MODEL_REVIEWED_UNKNOWN_ATTRIBUTE",
                    f"need_review 中的 {review.id} 不属于当前类目属性。",
                )
            if review.id in review_ids:
                self._retry(
                    "ATTRIBUTE_REVIEW_DUPLICATED",
                    f"need_review 中的属性 {review.id} 重复。",
                )
            review_ids.add(review.id)
        conflict = sorted(assigned_ids & review_ids)
        if conflict:
            self._retry(
                "ATTRIBUTE_DECISION_CONFLICT",
                "同一属性不能同时填写并进入 need_review：" + "、".join(conflict),
            )
        undecided_required = sorted(
            attr_id
            for attr_id, definition in self.ledger.definitions.items()
            if definition.get("required")
            and attr_id not in assigned_ids
            and attr_id not in review_ids
        )
        if undecided_required:
            self._retry(
                "REQUIRED_ATTRIBUTE_DECISION_MISSING",
                "每个必填属性必须填写或进入 need_review："
                + "、".join(undecided_required),
            )
        return output


@dataclass
class CategoryAttributeFillAgentRun:
    output: dict[str, Any]
    outcome: AiAgentRunOutcome[CategoryAttributeFillAgentOutput] | None = None

    def finish_business_result(self, result: Mapping[str, Any]) -> None:
        del result
        if self.outcome is not None:
            self.outcome.complete()


def _prompt_payload(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))


def run_category_attribute_fill_agent(
    payload: Mapping[str, Any],
    toolset: AiToolSet,
    ledger: CategoryAttributeValueLedger,
    *,
    timeout_seconds: float = CATEGORY_ATTRIBUTE_FILL_DEADLINE_SECONDS,
    factory: AiAgentFactory | None = None,
    model_override: Model | None = None,
) -> CategoryAttributeFillAgentRun:
    context = get_context()
    app_config = context.config.load_app_config()
    prompt = load_ai_use_case_prompt_pair(
        context.paths.app_dir,
        app_config,
        CATEGORY_ATTRIBUTE_FILL_USE_CASE_ID,
    )
    instructions = prompt.get("system") or (
        "value_mode=strict_enum 必须先调用 category_attribute_values_search，并且只能"
        "选择工具返回的值；value_mode=open_enum 优先使用 options，也允许填写有依据的"
        "自定义文本；value_mode=free_text 直接填写有依据的文本。"
    )
    user_prompt = render_prompt_template(
        prompt.get("user") or "请填写以下类目属性：{$input_json}",
        {"input_json": _prompt_payload(payload)},
    )
    agent_factory = factory or AiAgentFactory(
        app_dir=context.paths.app_dir,
        app_config=app_config,
        message_store=context.pydantic_messages,
    )
    outcome = agent_factory.run_sync(
        profile=CATEGORY_ATTRIBUTE_FILL_AGENT_PROFILE,
        instructions=instructions,
        user_prompt=user_prompt,
        toolset=toolset,
        use_case_state=ledger,
        output_validator=CategoryAttributeFillOutputValidator(ledger),
        business_scope={
            "platform": str(payload.get("platform") or ""),
            "site": str(payload.get("site") or ""),
            "category_id": str(payload.get("category_id") or ""),
        },
        idempotency_context={
            "result_version": CATEGORY_ATTRIBUTE_FILL_RESULT_VERSION
        },
        timeout_seconds=timeout_seconds,
        model_override=model_override,
    )
    return CategoryAttributeFillAgentRun(
        output=outcome.output.model_dump(mode="json"),
        outcome=outcome,
    )


__all__ = [
    "CATEGORY_ATTRIBUTE_FILL_AGENT_PROFILE",
    "CATEGORY_ATTRIBUTE_FILL_BUDGET_PROFILE",
    "CATEGORY_ATTRIBUTE_FILL_DEADLINE_SECONDS",
    "CATEGORY_ATTRIBUTE_FILL_RESULT_VERSION",
    "CATEGORY_ATTRIBUTE_FILL_USE_CASE_ID",
    "CategoryAttributeAssignment",
    "CategoryAttributeFillAgentOutput",
    "CategoryAttributeFillAgentRun",
    "CategoryAttributeFillOutputValidator",
    "CategoryAttributeReview",
    "run_category_attribute_fill_agent",
]
