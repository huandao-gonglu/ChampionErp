"""主对话和草稿批量入口共用的原生 Agent service。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from collections.abc import Callable

from pydantic_ai import DeferredToolRequests, ModelRetry, RunContext
from erp_web.services.ai_agent_dependencies import AiAgentDependencies
from erp_web.services.chat_reply_text import is_tool_call_text
from erp_web.ai_capability_composition import application_capability_permissions
from erp_web.services.ai_agent_factory import AiAgentExecutionProfile, AiAgentFactory
from erp_web.services.ai_prompt_templates import load_ai_use_case_prompt_pair
from erp_web.services.agent_run_storage import AgentRunStorage
from erp_web.services.agent_instructions import load_system_agent_instructions
from erp_web.services.chat_images import chat_image_reason
from erp_web.services.user_agents import load_user_agent_instructions
from erp_web.services.online_product_completion import validate_online_completion
from erp_web.stores.agent_call_store import AgentCallStore
from erp_web.schemas.ai_approval import AiToolApprovalMode, normalize_ai_tool_approval_mode

GLOBAL_CHAT_USE_CASE_ID = GLOBAL_CHAT_PROFILE_ID = GLOBAL_CHAT_TOOLSET_ID = (
    "global.chat"
)
GLOBAL_CHAT_CONVERSATION_PREFIX = "conversation_global_chat_"
GLOBAL_CHAT_ACTOR_ID = "local-user"
GLOBAL_CHAT_TENANT_ID = "local"
GLOBAL_CHAT_PROFILE = AiAgentExecutionProfile(
    use_case_id=GLOBAL_CHAT_USE_CASE_ID,
    output_type=str | DeferredToolRequests,
    toolset_id=GLOBAL_CHAT_TOOLSET_ID,
    budget_profile="global.chat.default",
    permissions=application_capability_permissions(),
    timeout_seconds=900,
    max_model_requests=80,
    max_tool_calls=240,
    max_tool_output_bytes=256 * 1024,
    retries=2,
    result_version="global_chat.v2",
    allow_write=True,
    python_tools=True,
    python_retries=4,
    user_input_tool=True,
)

NATIVE_INSTRUCTIONS = """缺少用户资料或需要选择时调用 request_user_input：短标题区分任务和对象，问题写清缺口，独立事项合批；多步或批量任务暂停提问前先用简短正文汇报已完成、跳过及未完成范围，再调用 request_user_input；依据 required_inputs/needs_confirmation 提问，已发起审批不重复询问。
读商品图用 product_image_read；对话附图直接按原图回答，模糊处说明未知，仅用户要求保存才用 chat_images_save_to_product。
直接工具与 Python 函数均以当前目录为准，历史名称不代表已加载。缺工具时尽量首轮用一次 search_tools 批量发现，已知完整名优先精确搜，不可用工具（如无识图时的 product_image_read）不重复搜；搜索不扩大授权。
用户要求添加采集链接时用 collection_links_add 提交原链接或完整分享文案，按回执区分新增和重复；仅用户要求采集才执行采集工具。
缺字段先查同域详情与分页，搜索为空不等于不存在；仅确需关联且有可信依据时跨域读。
一段 run_code 完成连续统计、差异、保存与回执核对，复用本轮 REPL 数据，不按属性反复读整批 SKU。按用户指定规则核对所有已填/未填值，旧值非正确答案；编码提取只保留匹配本体。
Python 仅支持部分标准库；计数用 len/sum/min/max，不写 next(生成器)；末行分支外返回字典/列表及回执，不用 json.dumps；TypedDict 声明真实类型。检查 ok=false，异常先核回执，取消即停，不重放成功或未知写；单次 draft_changes_apply 失败不保存，多次写非事务。多草稿优先用服务端批量工具（category_match_batch、copy_generate_batch、draft_sku_variants_check_batch、product_publish_validate_batch）；脚本内 asyncio.gather(*tasks) 单次并发 ≤8（无关键字参数、async def 内 try/except、不混大批次无关工具）；跨回合或恢复后重读。不访问宿主文件/DB/环境/外网；审批和后台任务在脚本外调用。
在线批改先固定 SKU/操作/仓库并按 next_page 读齐，用 online_products_changes_apply 同操作合批，超限分批核对；单项用 online_products_change。统计以 total/accepted_count/failed_count 为准，警告用 Python 去重计数。恢复按实际回执：终态不 close，旧任务结束后读新版本，版本过期先重读，已入队不主动轮询。
完整准备含文案（copy_generate/copy_generate_batch）、类目（category_match/category_match_batch）、主图、SKU 套图、公共/差异属性、库存、包装估算（draft_package_estimates_preview/confirm）、核价及预检（product_publish_validate/product_publish_validate_batch）；仅处理选定目标，发布须明确要求。
先读 draft_read.image_preparation，需套图时同轮直接调 draft_images_prepare(decision="auto")（图片工具不在 run_code 中调用）；generated/kept 复用，needs_confirmation 问保留/重做（默认保留，传最新 fingerprint），要求桌面时提示“在桌面继续”。queued 后并行准备文案、类目和属性，缺毛重只阻塞相关核价并集中补问；独立工作后同轮 draft_images_wait 核对原批次，仅全批采用算完成，失败/未知保留原图不自动重做。有效售价可复用，依据变化再核价。资料和图齐备后用 product_publish_prepare 准备水印/上传，IMAGE_NOT_PREPARED/IMAGE_PUBLIC_URL_STALE/IMAGE_DELIVERY_STALE 只修交付不重绘；缺选图请用户选择。
商品共用事实可复用，平台类目/售价/币种/销售目标按站点处理；来源引用须来自真实用户消息或已保存事实。发布/删除遵循服务端审批模式，模型不得代批或将准备当作发布授权。product_publish_request 返回 job_id 即结束提交，不轮询不重发；Deferred 暂停时领域 Job 继续，恢复按回执汇报。窄任务只调对应能力，自动类目匹配直接调自动/批量能力；保留草稿全部 targets 并按逐项回执核对覆盖。
公共属性先 draft_attributes_read(scope=common) 一次取事实、类目及已填值；仅 SKU 属性/包装用 scope=sku 按 next_offset 读齐。正确类目保留；category_attributes_query 并行按 has_more 读齐，只写 write_scope，不写 excluded_attributes；小字典空 query，大字典关键词，独立候选合批。仅定义有品牌字典才查真实 ID，优先无品牌候选，不伪造；Yandex 品牌取草稿/商品 vendor，不猜精确值。
SKU 拆 options 原文按单位优先填尺码/件数/尺寸等结构化字段，外语自定义值禁中文原文；商品长不取包装长，套装件数非每包销售数；互斥值不写公共属性，不靠编码/序号造差异。整组差异保存后或确认无可写项时用 draft_sku_variants_check（多草稿用 draft_sku_variants_check_batch）核对组合，分开报保存数、重复组与缺口；Yandex 公共字段可区分时传 yandex_variant_field_ids 实时核对，settings_guidance 须说明类目、中俄原名、后台入口、添加保存和已有组合影响，保存后带原 ID 重查；不据历史预检推断当前结果。
公共属性用 product_attributes_update，单 SKU 用 draft_sku_attributes_update，多 SKU 用 draft_changes_apply（差异在 Python 计算，保留有效旧值）；商品事实（成本/包装 cm·kg/条码/规格/供应商库存/电池液体）只用 product_sku_facts_update（先 product_read 取 updated_at 与 SKU ID，省略项保留，不猜事实）。同一依赖重复失败时停止换类目或复合工具重试，先完成独立目标并按真实回执报告。"""


GLOBAL_RESPONSE_INSTRUCTIONS = """全局回答规则，适用于查询、执行、解释、分析和错误反馈：
每次收到新的用户任务，先用一句简短、用户可见的正文说明你理解的具体目标和接下来要做的事。例如用户要求核对所选草稿的库存，可以说“我会核对所选草稿的库存，列出需要调整的 SKU。”按本轮实际要求措辞，避免空泛的“收到”和机械复述。
需要调用工具时，在第一个工具调用（包括 search_tools 和 run_code）之前输出这句话，随后在同一轮继续执行，遵循原有审批要求。纯文字任务也先简述理解，再接着给出答复。若缺少决定下一步所必需的信息，直接用一句话说明已理解的目标并问清缺口。用户中途改变要求时按新目标简短回应；同一任务的后续工具步骤、审批或后台恢复无需重复开场。
最终答复的第一句话给结果、核心结论或真正需要用户决定的事项，只汇报与本轮有关的内容。简单定位、结果追问和概念区别默认用一段话直接回答，只补理解答案所必需的说明或限制，不展开成背景、过程、证据和总结多个栏目。复杂问题和用户明确要求详细说明时按需要展开，不套固定报告模板，也不机械限制字数。
选择信息时逐项判断：它是否改变用户对答案的理解、识别目标、使用结果或决定下一步？如果都不影响，删除。默认不输出内部 ID、工具名、技术字段、原始状态码、日志，以及与本轮无关的库存、售价、草稿关联。说明机制时用业务语言，用户要求接口或技术诊断时才展开实现细节；不为证明做过工作而列工具调用、遍历过程或全部证据。
对象用能区分目标的最少业务信息定位，如名称、款式、规格或卖家 SKU；已有信息足以识别时，不为补齐商品介绍额外取数。需要规格时核对目标 SKU 的差异字段，不用商品共有标题替代。内部 ID 不能冒充卖家 SKU 或其他业务编码。依据只保留足以支持结论和区分对象的事实，不逐字段重复命中或未命中结果，不作超出实际读取范围的排除结论。报告未命中前，先确认分页覆盖完整、所需字段实际取得，并检查脚本确实查找了对应数据；字段缺失、投影路径用错或取数不全时只能说明未能确认，不能报告不存在。
执行任务说明实际完成范围和状态；批量任务先汇报总数、完成数及异常范围，同类失败合并说明，并保留识别受影响对象所需的信息。必须保留影响结论的失败、未完成、未知结果、过期数据和其他限制，不能用“已完成”概括部分成功。纯解释或分析不附带没有发生的操作汇报。
附带发现只有影响当前结论、所请求操作的正确性或确需用户处理时才简要提示，不据此扩大修复范围。执行中只提示有意义的进展、阻塞或范围变化；最终回答不重述计划，不重复总结已清楚表达的结论，不例行附加“如需我继续……”或无关建议。
"""


def validate_chat_output(ctx: RunContext[AiAgentDependencies], output: Any):
    if isinstance(output, str) and is_tool_call_text(output):
        raise ModelRetry("刚才返回的是工具调用标记，未执行。已有回执足以回答时，请直接给用户结论；"
                         "仍缺数据时通过原生工具调用补查，不要重放已完成的操作。")
    return validate_online_completion(ctx, output)


class GlobalAgentChatService:
    def __init__(
        self,
        *,
        app_dir: Path | str,
        app_config: dict | None,
        message_store: Any,
        toolset: Any,
        factory: Any = None,
        call_store: AgentCallStore | None = None,
        title_scheduler: Callable | None = None,
        approval_mode_reader: Callable[[], AiToolApprovalMode] | None = None,
    ) -> None:
        self.title_scheduler = title_scheduler
        self.app_dir = Path(app_dir)
        self.app_config = dict(app_config or {})
        self.approval_mode_reader = approval_mode_reader or (
            lambda: normalize_ai_tool_approval_mode(self.app_config.get("ai_tool_approval_mode", "ask"))
        )
        self.message_store = message_store
        self.toolset = toolset
        self.call_store = call_store or AgentCallStore(message_store.db)
        self.factory = factory or AiAgentFactory(
            app_dir=self.app_dir,
            app_config=self.app_config,
        )

    def instruction_sections(self) -> dict[str, str]:
        """规则来源分开计量，装配顺序及用户规则优先级保持一致。"""
        pair = load_ai_use_case_prompt_pair(GLOBAL_CHAT_USE_CASE_ID)
        return {
            'execution': NATIVE_INSTRUCTIONS,
            'business': str(pair.get("system") or ""),
            'system': load_system_agent_instructions(),
            'user': load_user_agent_instructions(self.app_dir),
            'response': GLOBAL_RESPONSE_INSTRUCTIONS,
        }

    def instructions(self) -> str:
        return "\n\n".join(filter(None, self.instruction_sections().values()))

    def trusted_history(self, conversation_id: str) -> list:
        history = self.message_store.get(conversation_id)
        return history.model_messages() if history else []

    @asynccontextmanager
    async def open_chat_run(
        self,
        *,
        conversation_id: str,
        new_messages=(),
        client_message_id="",
        model_override=None,
    ):
        history = self.message_store.get(conversation_id)
        messages = history.model_messages() if history else []
        support = AgentRunStorage(
            self.call_store,
            conversation_id,
            messages,
            history.history_version if history else 0,
            approval_mode_reader=self.approval_mode_reader,
        )
        pending = self.call_store.pending(conversation_id)
        if pending is not None and not self.call_store.ready(conversation_id):
            raise ValueError(
                "原生 Deferred 的结果尚未齐备；用户消息已接收，恢复时应用。"
            )
        if pending:
            support.resume_deferred(pending[0])
        async with self.factory.open_stream_run(
            profile=GLOBAL_CHAT_PROFILE,
            instructions=self.instructions(),
            output_validator=validate_chat_output,
            toolset=self.toolset,
            conversation_id=conversation_id,
            message_history=messages,
            deferred_tool_results=pending[1] if pending else None,
            usage=pending[2] if pending else None,
            run_support=support,
            actor_id=GLOBAL_CHAT_ACTOR_ID,
            tenant_id=GLOBAL_CHAT_TENANT_ID,
            business_scope={
                "conversation_id": conversation_id,
                "chat_image_input": "disabled" if chat_image_reason(self.app_config) else "enabled",
            },
            idempotency_context={
                "conversation_id": conversation_id,
                "message_id": client_message_id,
            },
            model_override=model_override,
        ) as session:
            yield session

    def dump_messages_for_conversation(self, conversation_id: str):
        history = self.message_store.get(conversation_id)
        return history.model_messages() if history else None
