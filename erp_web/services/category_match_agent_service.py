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
from typing import Annotated, Any, Mapping
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
    max_model_requests=6,
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
                    else "没有匹配时必须改换关键词，完成 3 次不同的有效搜索后才能 abstain。"
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
        "树导航按真实分支逐层展开，关键字模式最多搜索 3 次，无匹配时 abstain。"
    )
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
