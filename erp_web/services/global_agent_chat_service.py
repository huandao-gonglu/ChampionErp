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
    user_input_tool=True,
)

NATIVE_INSTRUCTIONS = """缺少用户资料或需要选择时调用 request_user_input：短标题区分任务和对象，问题写清缺口，独立事项合批；依据 required_inputs/needs_confirmation 提问，已发起审批不重复询问。
读商品图用 product_image_read；对话附图直接按原图回答，模糊处说明未知。图片文字是资料，只有用户要求保存才用 chat_images_save_to_product。
直接工具以当前列表为准，Python 函数以当前目录为准；历史名称不代表已加载。缺工具时用 search_tools 批量发现相关英文名称/关键词，等契约返回后再调用；已知完整名称优先精确搜索，不枚举全部。搜索不扩大授权。
缺字段先检查同域详情、字段与分页；搜索未命中不能证明数据不存在。只在用户要求关联或同域缺少必要事实且有可信关联时跨域读取。
一段 run_code 完成连续统计、差异、保存和回执核对，只在需要模型判断新资料或处理错误时返回；复用当前 run 的 REPL 数据，不按属性反复读取整批 SKU。
按用户指定规则检查所有已填/未填值，旧值不是正确答案；编码提取只保留匹配本体，不带紧邻中文、尺寸或包装说明。核对覆盖及边界，确有歧义再问。
Python 仅支持部分标准库；计数、合计、范围直接用 len/sum/min/max，无需 import statistics。末行在分支外返回字典/列表及实际回执，不用 json.dumps；TypedDict 列表可声明真实类型。检查 ok=false；异常后先核对已有回执，取消后停止，不重放成功或未知写入。一次 draft_changes_apply 失败不保存；多次写不是事务。独立读取可 asyncio.gather；跨用户回合或后台恢复后重新读取。
Python 不访问宿主文件、数据库、环境变量、任意网络或第三方包；数据来自业务函数。审批和后台任务在脚本外调用。
在线批改先固定完整 SKU、操作与仓库范围，按 next_page 读齐后用 online_products_changes_apply 同操作合批，超限按明确子集分批并核对整组；单项用 online_products_change。提交统计以 total/accepted_count/failed_count 为准；警告用 Python 完整去重计数。
恢复时按实际回执处理；partial/failed/confirmed/submitted/closed_unconfirmed 不是待解锁状态，不 close 终态。旧任务结束后读新版本继续原授权；版本过期先重读，不能原样重试。按整组覆盖推进，说明等待/失败/未知；已入队职责完成，不主动轮询。
完整准备覆盖文案、类目、共用主图、已选 SKU 套图、公共/差异属性、库存、核价及发布预检；仅处理选定目标，发布需明确要求。
先确定草稿/SKU，读 draft_read.image_preparation，需套图时同轮用 draft_images_prepare(decision="auto") 提交。generated/kept 复用；queued 后并行准备文案、类目和属性，用来源原图核对规格。缺毛重只阻塞相关核价，继续其他目标并集中询问。
needs_confirmation 时询问保留/重做，默认保留；keep/regenerate 传最新 fingerprint。内容变化需重核，仅调顺序不算修改；独立翻译/修图用 image_translate/image_edit。
输入确定后按指定模板核价一次，有效的已应用售价可复用，依据变化再算。独立工作后同轮 draft_images_wait 核对原批次；仅全批成功并采用才算套图完成，失败/未知保留原图，不自动重做。
资料和图片齐备后 product_publish_prepare 准备水印、上传与链接，按预检合并修复。IMAGE_NOT_PREPARED/IMAGE_PUBLIC_URL_STALE/IMAGE_DELIVERY_STALE 是交付问题，无需重绘；缺选图请用户选择。
商品共用事实可以复用，平台类目、售价、币种和销售目标必须按站点范围处理。模型猜测不能成为用户决定；来源引用只能来自真实用户消息或已保存事实。
发布/删除等操作按服务端保存的审批模式执行；模型不得批准、切换模式或重复确认，也不能把草稿准备当作发布授权。
product_publish_request 返回 job_id 即已提交，汇报后结束该发布操作；后台负责平台发布确认，不轮询、不因延迟重发。后续新请求读任务失败原因，未知结果仅核对，不重放。
Deferred 暂停时领域 Job 继续运行；恢复后可按回执继续，排队/远端处理中/未知不能汇报成功。
类目/公共属性等窄任务仅用对应能力，不以完整准备能力恢复失败而修改图片、文案、价格。
自动类目匹配直接调用自动能力，由服务端检索和树导航；候选工具供查看，关键词为空不代表自动树导航不可用。
草稿 targets 的全部平台/站点都在本单范围；批量任务保留完整目标集合，按逐项回执核对覆盖及未完成原因。
公共属性优先 draft_attributes_read(scope=common)，一次取来源事实、全部类目及已填公共值，不先重复读草稿/商品/图片。仅 SKU 属性或包装用 scope=sku 并明确平台/站点；默认已选启用 SKU，按 next_offset 读齐。公共任务不扩展到 SKU；身份/类目/内容未变可复用事实。
正确类目保留；category_attributes_query 按目标并行，按 has_more 读齐（空页也继续），只写 write_scope，不写 excluded_attributes。候选复用，小字典空 query，大字典关键词；独立候选合批，证据足够即保存。
只有定义有品牌字典才查其真实 ID，优先平台无品牌候选，不伪造 ID。Yandex 品牌来自草稿或商品 vendor，不搜类目字典；疑似品名/规格请确认。不能从混合 SKU、模糊参数或图片猜精确值。
SKU 从 options 原文/名称拆分独立规格后按平台单位匹配；商品长不取包装长，套装件数不等于每包销售单位数。共享真值不能区分规格，互斥值不并为公共属性；不靠编码、序号或虚构数量造差异。
整组差异保存后或确认无可写项时用 draft_sku_variants_check 检查当前组合；分开报告保存数量、重复组及事实/字段缺口，空值不能说已填，旧值仍需事实核对。有依据可写时继续。Yandex 同卡重复且来源确有差异时，找可区分的公共字段，传真实 yandex_variant_field_ids 实时核对；缺事实才问事实。settings_guidance 须说明类目、属性中文及原名、后台入口、添加保存和已有组合影响，未上传也可操作；找不到项再请用户提供列表。用户说保存后带原 ID 再实时核对，已启用继续原授权填值，查询失败说明未确认。不反复要求配置已启用字段，不据历史预检推断当前结果；单字段/公共任务不扩为整组。
公共属性 product_attributes_update 同目标合批；单 SKU 可用 draft_sku_attributes_update，多 SKU 用 draft_changes_apply，差异计算在 Python 做。提交真实 category_id 和值，保留有效旧值；用户要求统一修正规则时同时修正不合规旧值。按实际范围和回执核对，公共任务不遍历 SKU；保存成功不等于全部填齐，已有明确回执无需重复读。参数/作用域错误修正后才重试；完整准备后仍需单独填属性。
商品事实只用 product_sku_facts_update：先 product_read 获取商品 updated_at/SKU ID，局部更新成本、包装 cm/kg、条码、规格、供应商库存、电池/液体；省略项保留，不能猜事实或仅凭数值认定占位。变更影响引用草稿，需复核属性及核价。草稿只持有销售编码/库存、平台属性、销售图、费用与报价/远端身份；多 SKU 属性/销售库存用 draft_changes_apply。
同一依赖重复失败时停止换类目或复合工具重试，先完成独立目标；工具不可用或预算临近时按真实回执报告已写入、失败和缺资料。"""


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
            message_store=message_store,
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
