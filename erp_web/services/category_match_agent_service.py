"""``category.product_match`` 的唯一 Pydantic Agent service。

同步与流式入口复用同一套 factory 装配语义（``open_stream_run``）：

- ``open_category_match_stream``：focused 流式运行入口，yield opaque session 与
  渲染好的 user prompt；展示编码由 protocol service 负责，本模块不导入
  HTTP/SSE/Vercel transport。
- ``run_category_match_agent``：同步入口（Global Task 等 child 场景），在同一
  装配下消费 native events 但不建立展示流；子运行不创建独立 SSE。
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
from typing import Annotated, Any, Mapping, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import Model

from erp_web.context import get_context
from erp_web.schemas.category import (
    CATEGORY_SEARCH_PERMISSION,
    CATEGORY_SEARCH_TOOLSET_ID,
    CategoryCandidateLedger,
)
from erp_web.schemas.category import CategoryMatchTrace
from erp_web.schemas.category_search_language import category_search_language

from .ai_agent_dependencies import AiAgentDependencies
from .ai_agent_factory import (
    AiAgentExecutionError,
    AiAgentExecutionProfile,
    AiAgentFactory,
    AiAgentRunOutcome,
    AiAgentStreamSession,
)
from .ai_prompt_templates import load_ai_use_case_prompt_pair, render_prompt_template
from .ai_tool_registry import AiToolSet


CATEGORY_MATCH_USE_CASE_ID = "category.product_match"
CATEGORY_MATCH_BUDGET_PROFILE = "category.match.default"
CATEGORY_MATCH_RESULT_VERSION = "category_match.v2"
CATEGORY_MATCH_DEADLINE_SECONDS = 60

CATEGORY_IDENTITY_INSTRUCTIONS = (
    "先锁定商品身份：实际出售的物件、材质/结构、使用方式，以标题及明确规格为依据。"
    "product.source 与 facts.attributes 的原始规格是身份依据，优先于 product.target 的译文或营销扩写。"
    "关键词及最终类目必须保持该身份一致；营销功能不能证明新的用途、认证或商品类型。"
    "不得因为一个共用词而把商品扩展成另一种实物，也不得凭空添加医疗、工业、运动等专门用途。"
    "比对完整类目路径；排除材质、结构或实物类型矛盾的候选。"
    "路径是平台组织商品的目录，不能反过来给商品添加认证或用途；上级的并列集合不意味着每个叶子都具备其中某一专门用途。"
    "结合最具体叶子的范围判断：通用防护用品不自动等于医用器械，功能词缺失也不能否定同一种实物。"
    "类目通常不细分全部功能、人群和款式；缺少这些修饰语不等于不匹配。"
    "broader_type 必须是真正包含该实物的上位分类；仅材质相似或同样能遮脸，但穿戴结构不同的相邻叶子不能算上位类型。"
    "有真实类型匹配就可以选择，不要为了寻找与长标题字面完全相同的叶子反复搜索。"
    "例如：挂耳式纺织面罩可与通用的可重复使用防护口罩比较；防晒功能不使它变成医用口罩、套头帽、环形脖套或方形头巾。"
    "“医用与卫生口罩”是并列范围，不能把其下所有通用防护口罩一律认定为医用器械。"
    "evidence 简述实物与路径相符的事实及关键排除依据；真实 ID 只证明存在，不证明适用。"
    "无法可靠判断时 abstain，不得硬选，也不得声称平台不存在这个类目。"
)


CATEGORY_KEYWORD_SEARCH_INSTRUCTIONS = (
    "首次调用前，先在 product_identity 用中文摘出原始规格中的结构、使用方式与用途；再填写 product_type 锁定实物通用名，在 alternative_names 列出同一实物在固定 search_language 的不同叫法，再用 keywords 补充有区分作用的规范品名。"
    "三部分合计通常 3 至 4 个互补品名即可，一次调用批量查询。"
    "优先覆盖不同的实物叫法，至少保留一个去掉营销功能和人群修饰的简短通用名；"
    "不要用多个只更换功能修饰语的长标题占满首批。不要按功能联想另一种商品，也不要为了凑数加词。"
    "首批结果返回后先对比已有候选；商品身份与完整路径相符时直接提交最终结果。"
    "只有能指出已有候选的具体实物冲突或缺失的品名时，才进行一次针对该缺口的补查；"
    "不要把更多搜索次数当成更高可信度。后续 candidates 只展示新增类目，"
    "repeated_candidate_ids 引用之前候选，仍可选择它们。"
    "truncated=true 仅表示本批缓存还有未展示的新候选；必要时原 keywords 可继续取后续候选。"
    "query_candidate_counts 是每词排序返回量（上限 8），不表示完整类目库覆盖率。"
    "无可靠匹配时 abstain，不需要搜满关键词或调用次数。"
)


class CategoryPhysicalComparison(BaseModel):
    """商品与候选的具体实物对照，避免把邻近叶子误称为上位类目。"""

    model_config = ConfigDict(extra="forbid")
    product_form: str = Field(min_length=1, max_length=160, description="用中文描述商品实际结构/形态，依据输入事实，不能只写用途或材质。")
    category_form: str = Field(max_length=160, description="用中文解释所选叶子通常指的实物结构；未选择时为空。")
    compatible: bool = Field(description="是否同一种实物或真正包含该实物的上位分类；不同穿戴结构、不同商品类型只是相邻类目，不能因都遮脸或材质相似就判为 true。")


class CategoryMatchAgentOutput(BaseModel):
    """选择已检索且实物类型一致的类目并完成任务；候选已充分时立即提交，无可靠匹配则 abstain。"""

    model_config = ConfigDict(extra="forbid")

    selected_category_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=160),
    ]
    selected_category_path: list[Annotated[str, StringConstraints(max_length=500)]] = Field(
        max_length=20, description="复制所选候选的完整 path_segments，不能只看叶子名称；abstain 时为空。",
    )
    type_relationship: Literal["same_type", "broader_type", "uncertain", "incompatible"] = Field(
        description="以实物、材质、结构和使用方式比较商品与整个类目路径；用途相似但实物不同是 incompatible。",
    )
    physical_comparison: CategoryPhysicalComparison
    abstained: bool
    model_confidence: float = Field(ge=0, le=1)
    evidence: list[
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
        ]
    ] = Field(max_length=8, description="用中文说明实物类型及完整类目路径为何相符，或为何无法可靠确定。")

    @model_validator(mode="after")
    def validate_selection_shape(self) -> "CategoryMatchAgentOutput":
        if not self.abstained and not self.physical_comparison.compatible:
            raise ValueError("实物结构不相符时必须 abstain，不能把相邻叶子作为上位类目强选")
        if not self.abstained and self.type_relationship not in {"same_type", "broader_type"}:
            raise ValueError("实物类型不符或关系不确定时必须 abstain")
        if self.abstained and self.selected_category_path:
            raise ValueError("abstained 时不得携带已选择的类目路径")
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
    # 最多四批检索 + 一次越界纠正 + 最终输出及两次原生格式纠正。
    # 只增加模型完成输出的余量，实际工具额度和总 deadline 不变。
    max_model_requests=8,
    max_tool_calls=4,
    max_tool_output_bytes=128 * 1024,
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
                "必须先调用当前类目检索工具，再提交最终结果。",
            )
        if output.abstained:
            if not self.ledger.can_abstain:
                message = (
                    "树导航必须先展开到真实商品类型；若分支不合适，应回退并"
                    "改选之前保留的分支，最多完成 4 次导航后才能 abstain。"
                    if self.ledger.retrieval_mode == "tree_navigation"
                    else "请先完成一次实际类目检索，再根据商品事实判断是否 abstain。"
                )
                self._retry(
                    "CATEGORY_SEARCH_INCOMPLETE",
                    message,
                )
            return output
        if self.ledger.get(output.selected_category_id) is None:
            self._retry(
                "MODEL_SELECTED_UNKNOWN_CATEGORY",
                "selected_category_id 必须来自本次检索工具真实返回的商品类型。",
            )
        candidate = self.ledger.get(output.selected_category_id)
        if output.selected_category_path != list(candidate.get("path_segments") or []):
            self._retry("CATEGORY_PATH_REVIEW_REQUIRED", "请逐段核对并复制已选候选的完整 path_segments，再判断实物类型是否一致。")
        return output


@dataclass
class CategoryMatchAgentRun:
    """领域 facade 消费的 Agent service 结果。"""

    output: dict[str, Any]
    trace: CategoryMatchTrace
    outcome: AiAgentRunOutcome[CategoryMatchAgentOutput] | None = None

    def finish_business_result(self, result: Mapping[str, Any]) -> None:
        del result
        if self.outcome is None:
            return
        self.outcome.complete()


def _prompt_payload(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class _CategoryMatchRunParams:
    instructions: str
    user_prompt: str
    business_scope: dict[str, str]
    factory: AiAgentFactory


def _prepare_run_params(
    payload: Mapping[str, Any],
    *,
    factory: AiAgentFactory | None,
) -> _CategoryMatchRunParams:
    context = get_context()
    app_config = context.config.load_app_config()
    prompt = load_ai_use_case_prompt_pair(
        context.paths.app_dir,
        app_config,
        CATEGORY_MATCH_USE_CASE_ID,
    )
    instructions = prompt.get("system") or (
        "必须先调用当前类目检索工具，只能选择工具真实返回的商品类型；"
        "树导航按真实分支逐层展开；关键词模式一次提交 keywords 列表，无匹配时 abstain。"
    )
    instructions = f"{instructions} {CATEGORY_IDENTITY_INSTRUCTIONS}"
    if not payload.get("category_navigation"):
        instructions = f"{instructions} {CATEGORY_KEYWORD_SEARCH_INSTRUCTIONS}"
    else:
        instructions += (
            "树导航：从 root_nodes 保留最可能的 1 至 2 个 branch 并调用 browse_categories。"
            "只能展开真实返回的 branch node_id，直到得到 level=product_type 的 category_id。"
            "叶子不合适时可回到之前保留的分支；不得重复展开同一分支，最多 4 次导航。"
            "达到商品类型后选择实物相符的候选，无可靠匹配时 abstain。"
        )
    target = payload.get("target") if isinstance(payload.get("target"), Mapping) else {}
    language = category_search_language(str(target.get("platform") or ""), str(target.get("site") or ""))
    instructions += (
        f" 本次 search_language 固定为 {language}，由平台检索接口决定。"
        "所有搜索品名只使用该语言；商品原文、草稿 language 和用户交流语言不能覆盖它。"
        "禁止中文和换多种语言试搜。品牌/型号可保留原文，品名仍须使用固定语言。"
    )
    payload = {**payload, "search_language": language}
    user_prompt = render_prompt_template(
        prompt.get("user") or "请根据以下商品事实匹配类目：{$input_json}",
        {"input_json": _prompt_payload(payload)},
    )
    agent_factory = factory or AiAgentFactory(
        app_dir=context.paths.app_dir,
        app_config=app_config,
        message_store=context.pydantic_messages,
    )
    target = payload.get("target") if isinstance(payload.get("target"), Mapping) else {}
    return _CategoryMatchRunParams(
        instructions=instructions,
        user_prompt=user_prompt,
        business_scope={
            "platform": str(target.get("platform") or ""),
            "site": str(target.get("site") or ""),
        },
        factory=agent_factory,
    )


def _user_prompt_messages(user_prompt: str) -> list[ModelRequest]:
    return [ModelRequest(parts=[UserPromptPart(user_prompt)])]


def category_match_run_from_outcome(
    outcome: AiAgentRunOutcome[CategoryMatchAgentOutput],
) -> CategoryMatchAgentRun:
    """把类型化 outcome 转成领域结果。"""

    output = outcome.output.model_dump(mode="json")
    return CategoryMatchAgentRun(
        output=output,
        trace={
            "task_run_id": outcome.task_run_id,
            "run_id": outcome.run_id,
            "trace_id": outcome.trace_id,
        },
        outcome=outcome,
    )


@asynccontextmanager
async def open_category_match_stream(
    payload: Mapping[str, Any],
    toolset: AiToolSet,
    ledger: CategoryCandidateLedger,
    *,
    timeout_seconds: float,
    conversation_id: str,
    factory: AiAgentFactory | None = None,
    model_override: Model | None = None,
) -> AsyncIterator[
    tuple[AiAgentStreamSession[CategoryMatchAgentOutput], str]
]:
    """Focused 流式运行入口：yield opaque session 与渲染好的 user prompt。

    装配语义与原同步路径完全一致（同一 profile、validator、ToolSet、预算和
    脱敏）；展示编码与 chunk 发布由调用侧 protocol service 负责。
    """

    params = _prepare_run_params(payload, factory=factory)
    async with params.factory.open_stream_run(
        profile=CATEGORY_MATCH_AGENT_PROFILE,
        instructions=params.instructions,
        toolset=toolset,
        conversation_id=conversation_id,
        use_case_state=ledger,
        output_validator=CategoryMatchOutputValidator(ledger),
        business_scope=params.business_scope,
        idempotency_context={"result_version": CATEGORY_MATCH_RESULT_VERSION},
        timeout_seconds=timeout_seconds,
        model_override=model_override,
    ) as session:
        yield session, params.user_prompt


def category_match_prompt_messages(user_prompt: str) -> list[ModelRequest]:
    """本轮运行的用户消息；prompt 不当作用户聊天气泡展示。"""

    return _user_prompt_messages(user_prompt)


def _run_in_fresh_loop(coroutine: Any) -> Any:
    """在没有（或已有）event loop 的调用线程里安全运行协程。

    已有 event loop 时改在工作线程运行；新线程不继承 contextvars，必须
    显式复制当前上下文（重构计划 §16：presentation 等 contextvar 不得在
    线程边界丢失）。
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    context = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(context.run, asyncio.run, coroutine).result()


def run_category_match_agent(
    payload: Mapping[str, Any],
    toolset: AiToolSet,
    ledger: CategoryCandidateLedger,
    *,
    timeout_seconds: float,
    factory: AiAgentFactory | None = None,
    model_override: Model | None = None,
) -> CategoryMatchAgentRun:
    """同步入口（Global Task 等 child 场景）；不建立展示流。

    内部复用与流式入口完全一致的 ``open_stream_run`` 装配；native events 被
    消费但不转换为展示 chunk，子运行展示遵循父运行单 SSE 规则。
    """

    async def _execute() -> CategoryMatchAgentRun:
        async with open_category_match_stream(
            payload,
            toolset,
            ledger,
            timeout_seconds=timeout_seconds,
            conversation_id=f"conversation_{uuid4().hex}",
            factory=factory,
            model_override=model_override,
        ) as (session, user_prompt):
            native = session.events(_user_prompt_messages(user_prompt))
            try:
                async for _event in native:
                    pass
            finally:
                await native.aclose()
            if not session.finalizing:
                raise AiAgentExecutionError(
                    "AI_AGENT_STREAM_RESULT_UNAVAILABLE",
                    "类目匹配运行未产生类型化完成结果。",
                    conversation_id=session.conversation_id,
                    task_run_id=session.task_run_id,
                    run_id=session.run_id,
                    trace_id=session.trace_id,
                )
            outcome = session.require_outcome()
        return category_match_run_from_outcome(outcome)

    return _run_in_fresh_loop(_execute())


__all__ = [
    "CATEGORY_MATCH_AGENT_PROFILE",
    "CATEGORY_MATCH_BUDGET_PROFILE",
    "CATEGORY_MATCH_DEADLINE_SECONDS",
    "CATEGORY_MATCH_RESULT_VERSION",
    "CATEGORY_MATCH_USE_CASE_ID",
    "CategoryMatchAgentOutput",
    "CategoryMatchAgentRun",
    "CategoryMatchOutputValidator",
    "category_match_prompt_messages",
    "category_match_run_from_outcome",
    "open_category_match_stream",
    "run_category_match_agent",
]
