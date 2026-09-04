"""``category.attribute_fill`` 的唯一 Pydantic Agent service。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Annotated, Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.models import Model

from erp_web.context import get_context
from erp_web.schemas.category import (
    category_attribute_uses_unit,
    category_attribute_uses_numeric_unit,
    normalize_category_attribute_unit,
    normalize_category_attribute_number_unit_value,
)
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
CATEGORY_ATTRIBUTE_FILL_RESULT_VERSION = "category_attribute_fill.v3"
CATEGORY_ATTRIBUTE_FILL_DEADLINE_SECONDS = 120

BRAND_IDENTITY_INSTRUCTIONS = (
    "具体品牌的跨语言名称、拉丁转写、国际商标名和商业别名由你结合商品事实"
    "判断。品牌属性第一轮必须在同一次批量工具调用中查询源品牌原文以及最可能的"
    "平台品牌名，例如“悦尚”可查询 YueShang，“大疆”应查询 DJI；只能选择本次"
    "工具返回的真实平台候选。商品存在具体品牌时不得选择平台官方无品牌候选。"
    "product、source、draft 的具体品牌相互冲突且无法确定时，必填品牌进入"
    " need_review。"
)


class CategoryAttributeAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attribute_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
    ]
    value: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
        Field(
            description="属性值；带数值单位的属性只填写数值，不把单位拼入字符串"
        ),
    ]
    dictionary_value_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=160),
    ] = ""
    unit: Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=80),
        Field(
            description="带单位属性必须填写类目允许的单位；不带单位属性留空"
        ),
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
        assignment_units: dict[str, str] = {}
        assignment_values: dict[str, set[tuple[str, str, str]]] = {}
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
            if category_attribute_uses_unit(definition):
                if not assignment.unit:
                    self._retry(
                        "ATTRIBUTE_UNIT_REQUIRED",
                        f"带单位属性 {attr_id} 必须同时返回 value 和 unit。",
                    )
                canonical_unit = normalize_category_attribute_unit(
                    definition,
                    assignment.unit,
                )
                if canonical_unit is None:
                    self._retry(
                        "ATTRIBUTE_UNIT_INVALID",
                        f"属性 {attr_id} 的 unit 必须原样选择类目定义提供的单位。",
                    )
                previous_unit = assignment_units.get(attr_id)
                if previous_unit and previous_unit != canonical_unit:
                    self._retry(
                        "ATTRIBUTE_UNIT_INCONSISTENT",
                        f"同一属性 {attr_id} 的多个值必须使用相同单位。",
                    )
                assignment_units[attr_id] = canonical_unit
                assignment.unit = canonical_unit
                if category_attribute_uses_numeric_unit(definition):
                    normalized_number_unit = (
                        normalize_category_attribute_number_unit_value(
                            definition,
                            assignment.value,
                            assignment.unit,
                        )
                    )
                    if normalized_number_unit is None:
                        self._retry(
                            "ATTRIBUTE_NUMBER_INVALID",
                            f"数值单位属性 {attr_id} 的 value 必须是有限数值。",
                        )
                    assignment.value = normalized_number_unit["value"]
                    assignment.unit = normalized_number_unit["unit"]
            elif assignment.unit:
                self._retry(
                    "ATTRIBUTE_UNIT_FORBIDDEN",
                    f"不带单位的属性 {attr_id} 不得返回 unit。",
                )
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

            value_key = (
                assignment.dictionary_value_id.casefold(),
                assignment.value.casefold(),
                assignment.unit.casefold(),
            )
            seen_values = assignment_values.setdefault(attr_id, set())
            if value_key in seen_values:
                self._retry(
                    "ATTRIBUTE_VALUE_DUPLICATED",
                    f"属性 {attr_id} 不得重复填写相同的值。",
                )
            seen_values.add(value_key)

        for attr_id, count in assignment_counts.items():
            definition = self.ledger.definition(attr_id) or {}
            maximum = int(definition.get("max_value_count") or 0)
            if not definition.get("is_collection") and count > 1:
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
        "required=true 的属性必须填写或进入 need_review；required=false 的可选属性"
        "只在能由商品事实确定时填写，不确定时直接跳过。value_mode=strict_enum 必须先"
        "调用 category_attribute_values_search，并且只能选择工具返回的值；value_mode="
        "open_enum 优先使用 options，也允许填写有依据的自定义文本；value_mode="
        "free_text 直接填写有依据的文本。包含非空 unit_options 或 value_type="
        "number_unit 的属性必须把数值与单位分别填写到 value 和 unit，"
        "单位只能来自类目定义且不能根据默认单位猜测商品事实；不带单位的属性 unit"
        "留空。商品品牌为 Generic、其他、无品牌、白牌或"
        "同义占位值时，应把它视为无品牌事实，并为 strict_enum 品牌属性查询、"
        "选择平台工具返回的官方无品牌候选。非品牌 strict_enum 允许依据商品事实和"
        "已确认类目路径进行跨语言语义匹配，但不得猜测技术规格。"
    )
    instructions = f"{instructions} {BRAND_IDENTITY_INSTRUCTIONS}"
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
