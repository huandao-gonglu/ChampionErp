"""通过集中 Agent Factory 执行类目检索和结构化匹配。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Annotated, Any, Mapping, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.models import Model

from erp_web.context import get_context
from erp_web.schemas.category import (
    CATEGORY_SEARCH_MAX_OUTPUT_BYTES,
    CATEGORY_SEARCH_PERMISSION,
    CATEGORY_SEARCH_TOOLSET_ID,
    CategoryCandidateLedger,
)
from erp_web.schemas.category import CategoryMatchTrace
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
CATEGORY_MATCH_RESULT_VERSION = "category_match.v2"
CATEGORY_MATCH_DEADLINE_SECONDS = 150

CATEGORY_IDENTITY_INSTRUCTIONS = (
    "先锁定商品身份：实际出售的物件、材质/结构、使用方式，以标题及明确规格为依据。"
    "product.source 与 facts.attributes 的原始规格是身份依据，优先于 product.target 的译文或营销扩写。"
    "关键词及最终类目必须保持该身份一致；营销功能不能证明新的用途、认证或商品类型。"
    "不得因为一个共用词而把商品扩展成另一种实物，也不得凭空添加医疗、工业、运动等专门用途。"
    "比对完整类目路径；排除材质、结构或实物类型矛盾的候选。"
    "完整消费品与替换零件不能因共享关键词归为同类；先比较日常使用类型，再比较附加功能。"
    "搜索返回的商品领域名不一定是完整路径，收到详情纠正后必须重新判断，不能只替换路径文字。"
    "路径是平台组织商品的目录，不能反过来给商品添加认证或用途；上级的并列集合不意味着每个叶子都具备其中某一专门用途。"
    "结合最具体叶子的范围判断：通用防护用品不自动等于医用器械，功能词缺失也不能否定同一种实物。"
    "类目通常不细分全部功能、人群和款式；缺少这些修饰语不等于不匹配。"
    "broader_type 必须是真正包含该实物的上位分类；仅材质相似或同样能遮脸，但穿戴结构不同的相邻叶子不能算上位类型。"
    "有真实类型匹配就可以选择，不要为了寻找与长标题字面完全相同的叶子反复搜索。"
    "例如：挂耳式纺织面罩可与通用的可重复使用防护口罩比较；防晒功能不使它变成医用口罩、套头帽、环形脖套或方形头巾。"
    "“医用与卫生口罩”是并列范围，不能把其下所有通用防护口罩一律认定为医用器械。"
    "evidence 简述实物与路径相符的事实及关键排除依据；真实 ID 只证明存在，不证明适用。"
    "带有 forbidden=true 的候选属于禁售类目，严禁选择。"
    "若商品实物结构本身合理兼容多个类目（模棱两可），且存在同样符合实物结构的非禁售候选，优先选择非禁售候选，并在 evidence 中说明避开了哪个禁售候选。"
    "若唯一与实物结构相符的类目是禁售类目、其他非禁售候选与实物冲突，必须 abstain 并设置 abstain_reason=\"forbidden_only\"，不得为绕过禁售硬选不相关类目。"
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
    "结果不按条数裁剪；超过回执字节预算时 has_more=true，保持全部搜索参数不变并以 next_offset 续页。"
    "total_candidate_count/returned_candidate_count/remaining_candidate_count 分别是去重命中总量、本页交付量、后续页数量。"
    "query_candidate_counts 是每词完整取回数量；本地树搜索覆盖该词全部命中，远端预测仅代表接口给出的候选。"
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
    # 树导航允许回退并展开第二条分支，同时给最终输出和原生纠正保留余量。
    # 总 deadline 仍约束整个检索过程。
    max_model_requests=12,
    max_tool_calls=8,
    max_tool_output_bytes=CATEGORY_SEARCH_MAX_OUTPUT_BYTES,
    # 格式纠正与更换候选后的完整路径复核共享原生输出重试额度。
    retries=4,
    result_version=CATEGORY_MATCH_RESULT_VERSION,
)


class CategoryMatchOutputValidator:
    """把 Ledger 中的确定性约束反馈给模型并在重试耗尽后保留稳定码。"""

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
        self.verified_paths = {}
        self.verified_details = {}
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
                "selected_category_id 必须来自本次检索工具真实返回的商品类型。",
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
            detail = self.candidate_detail_loader(output.selected_category_id,
                timeout_seconds=ctx.deps.execution_context.remaining_seconds())
            self.verified_details[output.selected_category_id] = detail
            self.verified_paths[output.selected_category_id] = [
                part.strip() for part in str(detail.get("category_path") or "").split(" / ") if part.strip()
            ]
        if self.forbidden_checker:
            issue = self.forbidden_checker(output.selected_category_id, detail)
            if issue:
                reason = str(issue.get("message") or "在禁售名单中").strip()
                self._retry(
                    "CATEGORY_FORBIDDEN",
                    f"{reason}。请改选其他与商品实物结构相符的非禁售候选，或补搜合规类目；"
                    "若无实物相符的非禁售类目，请 abstain 并设置 abstain_reason=\"forbidden_only\"。",
                )
        path = self.verified_paths.get(output.selected_category_id, list(candidate.get("path_segments") or []))
        if not path or output.selected_category_path != path:
            self._retry("CATEGORY_PATH_REVIEW_REQUIRED", "搜索结果可能只有商品领域名。该 ID 的真实完整类目路径是："
                        + json.dumps(path, ensure_ascii=False)
                        + "。请重新比较商品实物、父级用途与叶子类型，排除配件/替换件等不适用类目；"
                        "不符时改选其他已检索候选或补查，不能仅复制路径而保留错误判断。")
        return output


@dataclass
class CategoryMatchAgentRun:
    """领域 facade 消费的 Agent service 结果。"""

    output: dict[str, Any]
    trace: CategoryMatchTrace
    outcome: AiAgentRunOutcome[CategoryMatchAgentOutput] | None = None


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
    prompt = load_ai_use_case_prompt_pair(CATEGORY_MATCH_USE_CASE_ID)
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
) -> CategoryMatchAgentRun:
    params = _prepare_run_params(payload, factory=factory)
    outcome = params.factory.run_sync(
        profile=CATEGORY_MATCH_AGENT_PROFILE,
        instructions=params.instructions,
        user_prompt=params.user_prompt,
        toolset=toolset,
        use_case_state=ledger,
        output_validator=CategoryMatchOutputValidator(
            ledger, candidate_detail_loader, forbidden_checker=forbidden_checker,
        ),
        business_scope=params.business_scope,
        idempotency_context={"result_version": CATEGORY_MATCH_RESULT_VERSION},
        timeout_seconds=timeout_seconds,
        model_override=model_override,
    )
    return category_match_run_from_outcome(outcome)


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
