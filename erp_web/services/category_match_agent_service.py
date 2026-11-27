"""通过集中 Agent Factory 执行类目检索和结构化匹配（支持预召回单轮精排与多模态输入）。"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models import Model

from erp_web.context import get_context
from erp_web.schemas.category import (
    CATEGORY_SEARCH_MAX_OUTPUT_BYTES,
    CATEGORY_SEARCH_PERMISSION,
    CATEGORY_SEARCH_TOOLSET_ID,
    CategoryCandidateLedger,
    CategoryMatchTrace,
)
from erp_web.schemas.category_search_language import category_search_language

from .ai_agent_dependencies import AiAgentDependencies
from .ai_agent_factory import (
    AiAgentExecutionProfile,
    AiAgentFactory,
    AiAgentRunOutcome,
)
from .ai_prompt_templates import load_ai_use_case_prompt_pair, render_prompt_template
from .ai_tool_registry import AiToolSet


CATEGORY_MATCH_USE_CASE_ID = "category.product_match"
CATEGORY_MATCH_BUDGET_PROFILE = "category.match.default"
CATEGORY_MATCH_RESULT_VERSION = "category_match.v3"
CATEGORY_MATCH_DEADLINE_SECONDS = 150

CATEGORY_IDENTITY_INSTRUCTIONS = (
    "先锁定商品身份：实际出售的物件、材质/结构与日常使用方式，以标题、明确规格及主图视觉事实（如有）为依据。"
    "product.source 与 facts.attributes / facts.sku_options 的原始规格优先于 product.target 的译文或营销扩写。"
    "搜索词与最终类目必须保持该身份一致；营销功能不能证明新的认证、专门用途或商品类型，不得凭空归入医疗、工业或运动专类。"
    "比对完整类目路径，排除材质、穿戴/工作结构或实物类型矛盾的候选；完整消费品与替换零件/配件不得混归同类。"
    "上级目录的并列范围不代表下属所有叶子都具备某一专门资质；结合最具体叶子的真实范围判断。"
    "类目通常不细分全部功能、人群和款式，缺少修饰语不等于不匹配；有真实类型匹配即可选择，不必为寻找字面完全一致的叶子反复搜索。"
    "broader_type 必须是真正包含该实物的上位分类，结构和形态不同的相邻叶子不能算上位类型。"
    "evidence 简述实物与完整路径相符的事实及关键排除依据；真实 ID 只证明存在，不证明适用。"
    "带有 forbidden=true 的候选属于禁售类目，严禁选择。"
    "若商品合理兼容多个类目且存在非禁售候选，优先选择非禁售候选并在 evidence 中说明；"
    "若唯一与实物相符的类目属于禁售且无合规替代，必须 abstain 并设置 abstain_reason=\"forbidden_only\"。"
    "无法可靠判断时 abstain，不得硬选，也不得声称平台不存在这个类目。"
)

CATEGORY_PREFETCHED_INSTRUCTIONS = (
    "输入中的 prefetched_candidates 是服务端已基于商品标准化信息预先召回的真实可发布叶子类目。"
    "请优先在 prefetched_candidates 中逐项比对完整路径（及中文参考名）；"
    "若已存在与商品实物完全相符的合规叶子类目，请直接提交最终结果，无需再调用检索工具。"
    "若预召回中唯一与商品实物精确匹配的叶子类目已标为 forbidden=true，且同领域没有其他真实兼容的合规替代类目，"
    "应直接 abstain 并设置 abstain_reason=\"forbidden_only\"，不要反复换词重搜同一禁售商品。"
    "仅当预召回候选中确实缺少匹配项、或所有候选与实物存在明确冲突时，才调用工具针对缺口补查或展开类目树分支。"
)

CATEGORY_KEYWORD_SEARCH_INSTRUCTIONS = (
    "需要补查时，先在 product_identity 用中文摘出实物结构、使用方式与用途；"
    "再填写 product_type 锁定实物通用名，在 alternative_names 列出同一实物在固定 search_language 的别称，"
    "用 keywords 补充有区分作用的规范品名（三部分合计通常 3 至 4 个互补词即可，一次批量查询）。"
    "至少保留一个去掉营销功能和人群修饰的简短通用名；不要用只换形容词的长标题占满首批，也不要联想另一种商品。"
    "后续 candidates 只展示新增类目，repeated_candidate_ids 引用之前候选，仍可选择。"
    "超过回执字节预算时 has_more=true，保持全部搜索参数不变并以 next_offset 续页。"
)


class CategoryPhysicalComparison(BaseModel):
    """商品与候选的具体实物对照，避免把邻近叶子误称为上位类目。"""

    model_config = ConfigDict(extra="forbid")
    product_form: str = Field(
        min_length=1,
        max_length=160,
        description="用中文描述商品实际结构/形态，依据输入事实，不能只写用途或材质。",
    )
    category_form: str = Field(
        max_length=160,
        description="用中文解释所选叶子通常指的实物结构；未选择时为空。",
    )
    compatible: bool = Field(
        description="是否同一种实物或真正包含该实物的上位分类；结构不同的相邻类目不能判为 true。",
    )


class CategoryMatchAgentOutput(BaseModel):
    """选择已检索且实物类型一致的类目并完成任务；候选已充分时立即提交，无可靠匹配则 abstain。"""

    model_config = ConfigDict(extra="forbid")

    selected_category_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=160),
    ]
    selected_category_path: list[Annotated[str, StringConstraints(max_length=500)]] = Field(
        max_length=20,
        description="复制所选候选的完整 path_segments（原文路径），不能只看叶子名称；abstain 时为空。",
    )
    type_relationship: Literal["same_type", "broader_type", "uncertain", "incompatible"] = Field(
        description="以实物、材质、结构和使用方式比较商品与整个类目路径；用途相似但实物不同是 incompatible。",
    )
    physical_comparison: CategoryPhysicalComparison
    abstained: bool
    abstain_reason: Literal["", "no_match", "forbidden_only"] = Field(
        default="",
        description="abstain 时填写原因：唯一与实物相符的类目属于禁售且无合规替代时填 forbidden_only；其余填 no_match；未 abstain 时为空。",
    )
    model_confidence: float = Field(ge=0, le=1)
    evidence: list[
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
        ]
    ] = Field(
        max_length=8,
        description="用中文说明实物类型及完整类目路径为何相符，或为何无法可靠确定。",
    )

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
        if not self.abstained and self.abstain_reason:
            raise ValueError("未 abstain 时不得填写 abstain_reason")
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
    max_tool_output_bytes=CATEGORY_SEARCH_MAX_OUTPUT_BYTES,
    retries=3,
    result_version=CATEGORY_MATCH_RESULT_VERSION,
)


class CategoryMatchOutputValidator:
    """把 Ledger 中的确定性约束反馈给模型并在重试耗尽后保留稳定码与详情缓存。"""

    def __init__(
        self,
        ledger: CategoryCandidateLedger,
        candidate_detail_loader=None,
        *,
        forbidden_checker=None,
    ) -> None:
        self.ledger = ledger
        self.candidate_detail_loader = candidate_detail_loader
        self.forbidden_checker = forbidden_checker
        self.verified_paths: dict[str, list[str]] = {}
        self.verified_details: dict[str, dict[str, Any]] = {}
        self.error_code = ""

    def _retry(self, code: str, message: str) -> None:
        self.error_code = code
        raise ModelRetry(message)

    def __call__(
        self,
        ctx: RunContext[AiAgentDependencies],
        output: CategoryMatchAgentOutput,
    ) -> CategoryMatchAgentOutput:
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
                "selected_category_id 必须来自本次检索或预召回真实返回的商品类型。",
            )
        candidate = self.ledger.get(output.selected_category_id)
        if candidate and candidate.get("forbidden"):
            reason = str(candidate.get("forbidden_reason") or "在禁售名单中").strip()
            self._retry(
                "CATEGORY_FORBIDDEN",
                f"所选类目 {output.selected_category_id} 属于禁售范围（{reason}），严禁选择。"
                "请改选其他与商品实物结构相符的非禁售候选，或补搜合规类目；"
                "若无实物相符的非禁售类目，请 abstain 并设置 abstain_reason=\"forbidden_only\"。",
            )
        detail = self.verified_details.get(output.selected_category_id)
        if self.candidate_detail_loader and output.selected_category_id not in self.verified_paths:
            detail = self.candidate_detail_loader(
                output.selected_category_id,
                timeout_seconds=ctx.deps.execution_context.remaining_seconds(),
            )
            self.verified_details[output.selected_category_id] = detail
            self.verified_paths[output.selected_category_id] = [
                part.strip()
                for part in str(detail.get("category_path") or "").split(" / ")
                if part.strip()
            ]
        if self.forbidden_checker:
            issue = self.forbidden_checker(output.selected_category_id, detail)
            if issue:
                reason = str(issue.get("message") or "在禁售名单中").strip()
                # 同步写回 Ledger，避免模型随后 abstain("forbidden_only") 时终检看不到禁售记录。
                self.ledger.mark_forbidden(output.selected_category_id, reason)
                self._retry(
                    "CATEGORY_FORBIDDEN",
                    f"{reason}。请改选其他与商品实物结构相符的非禁售候选，或补搜合规类目；"
                    "若无实物相符的非禁售类目，请 abstain 并设置 abstain_reason=\"forbidden_only\"。",
                )
        path = self.verified_paths.get(
            output.selected_category_id,
            list(candidate.get("path_segments") or []) if candidate else [],
        )
        if not path or output.selected_category_path != path:
            self._retry(
                "CATEGORY_PATH_REVIEW_REQUIRED",
                "搜索结果可能只有商品领域名。该 ID 的真实完整类目路径是："
                + json.dumps(path, ensure_ascii=False)
                + "。请重新比较商品实物、父级用途与叶子类型，排除配件/替换件等不适用类目；"
                "不符时改选其他已检索候选或补查，不能仅复制路径而保留错误判断。",
            )
        return output


@dataclass
class CategoryMatchAgentRun:
    """领域 facade 消费的 Agent service 结果。"""

    output: dict[str, Any]
    trace: CategoryMatchTrace
    outcome: AiAgentRunOutcome[CategoryMatchAgentOutput] | None = None
    verified_details: dict[str, dict[str, Any]] = field(default_factory=dict)


def _prompt_payload(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class _CategoryMatchRunParams:
    instructions: str
    user_prompt: str | list[Any]
    business_scope: dict[str, str]
    factory: AiAgentFactory


def _prepare_run_params(
    payload: Mapping[str, Any],
    *,
    factory: AiAgentFactory | None,
    image_content: BinaryContent | None = None,
) -> _CategoryMatchRunParams:
    context = get_context()
    app_config = context.config.load_app_config()
    prompt = load_ai_use_case_prompt_pair(CATEGORY_MATCH_USE_CASE_ID)
    instructions = prompt.get("system") or (
        "只能选择预召回或类目检索工具真实返回的商品类型；"
        "候选已充分时直接提交最终结果，存在缺口时再调用工具补查，无可靠匹配时 abstain。"
    )
    instructions = f"{instructions} {CATEGORY_IDENTITY_INSTRUCTIONS}"

    nav = payload.get("category_navigation")
    nav_mode = str(nav.get("mode") or "") if isinstance(nav, Mapping) else ""
    has_prefetched = bool(payload.get("prefetched_candidates"))
    if has_prefetched:
        instructions = f"{instructions} {CATEGORY_PREFETCHED_INSTRUCTIONS}"

    is_pure_tree = nav_mode == "tree_navigation"
    if not is_pure_tree:
        instructions = f"{instructions} {CATEGORY_KEYWORD_SEARCH_INSTRUCTIONS}"
    if nav:
        instructions += (
            " 树导航：可从 root_nodes 选择 1 至 2 个最相关的 branch 调用 browse_categories 逐层展开，"
            "直到得到 level=product_type 的 category_id；不得重复展开同一分支，最多 4 次导航。"
        )

    target = payload.get("target") if isinstance(payload.get("target"), Mapping) else {}
    language = category_search_language(
        str(target.get("platform") or ""),
        str(target.get("site") or ""),
    )
    if not is_pure_tree:
        instructions += (
            f" 本次 search_language 固定为 {language}，由平台检索接口决定。"
            "所有搜索品名只使用该语言；商品原文、草稿 language 和用户交流语言不能覆盖它。"
            "禁止中文和换多种语言试搜。品牌/型号可保留原文，品名仍须使用固定语言。"
        )
        payload = {**payload, "search_language": language}

    user_prompt_text = render_prompt_template(
        prompt.get("user") or "请根据以下商品事实匹配类目：{$input_json}",
        {"input_json": _prompt_payload(payload)},
    )
    user_prompt: str | list[Any] = (
        [user_prompt_text, image_content]
        if image_content is not None
        else user_prompt_text
    )
    agent_factory = factory or AiAgentFactory(
        app_dir=context.paths.app_dir,
        app_config=app_config,
    )
    return _CategoryMatchRunParams(
        instructions=instructions,
        user_prompt=user_prompt,
        business_scope={
            "platform": str(target.get("platform") or ""),
            "site": str(target.get("site") or ""),
        },
        factory=agent_factory,
    )


def category_match_run_from_outcome(
    outcome: AiAgentRunOutcome[CategoryMatchAgentOutput],
    *,
    verified_details: Mapping[str, dict[str, Any]] | None = None,
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
        verified_details=dict(verified_details or {}),
    )


def run_category_match_agent(
    payload: Mapping[str, Any],
    toolset: AiToolSet,
    ledger: CategoryCandidateLedger,
    *,
    timeout_seconds: float,
    factory: AiAgentFactory | None = None,
    model_override: Model | None = None,
    candidate_detail_loader=None,
    forbidden_checker=None,
    image_content: BinaryContent | None = None,
) -> CategoryMatchAgentRun:
    params = _prepare_run_params(
        payload,
        factory=factory,
        image_content=image_content,
    )
    validator = CategoryMatchOutputValidator(
        ledger,
        candidate_detail_loader,
        forbidden_checker=forbidden_checker,
    )
    outcome = params.factory.run_sync(
        profile=CATEGORY_MATCH_AGENT_PROFILE,
        instructions=params.instructions,
        user_prompt=params.user_prompt,
        toolset=toolset,
        use_case_state=ledger,
        output_validator=validator,
        business_scope=params.business_scope,
        idempotency_context={"result_version": CATEGORY_MATCH_RESULT_VERSION},
        timeout_seconds=timeout_seconds,
        model_override=model_override,
    )
    return category_match_run_from_outcome(
        outcome,
        verified_details=validator.verified_details,
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
    "category_match_run_from_outcome",
    "run_category_match_agent",
]
