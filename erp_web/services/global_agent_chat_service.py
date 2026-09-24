"""主对话和草稿批量入口共用的原生 Agent service。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from pydantic_ai import DeferredToolRequests
from erp_web.ai_capability_composition import application_capability_permissions
from erp_web.services.ai_agent_factory import AiAgentExecutionProfile, AiAgentFactory
from erp_web.services.ai_prompt_templates import load_ai_use_case_prompt_pair
from erp_web.services.agent_run_storage import AgentRunStorage
from erp_web.services.agent_memory import load_agent_memory_instructions
from erp_web.stores.agent_call_store import AgentCallStore

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
)

NATIVE_INSTRUCTIONS = """你是本地 ERP 的主 Agent。根据用户目标直接选择代码已注册的 focused 业务能力，读取工具结果后决定下一步。
开展新的业务操作时，先复用对话中仍有效的事实，仅按需读取目标草稿、关联商品、店铺/平台资料。缺字段或可修复错误时，先查询、纠正参数或选择适用工具；只询问确实缺失或冲突的信息。
多条独立草稿可以并发准备，一条缺资料时继续处理其他可执行草稿。不处理用户未选中的草稿。
商品共用事实可以复用，平台类目、售价、币种和销售目标必须按站点范围处理。模型猜测不能成为用户决定；来源引用只能来自真实用户消息或已保存事实。
草稿准备不包含发布授权。发布、删除等需审批操作必须由用户批准；在可独立完成的准备之后再收集审批。
后台工具暂停时已启动的领域 Job 继续运行，Agent 等待结果。恢复后可继续查询和调用业务工具，不能把排队、远端处理中或未知结果汇报成成功。
收到纠正或取消后优先遵守最新要求。按草稿报告已完成内容、剩余缺口和证据；预算耗尽时明确未完成事项。
用户只要求类目、公共属性时，只执行这些操作；完整准备能力会额外修改文案、图片和价格，不能用于窄范围任务的失败恢复。
自动匹配或复核类目时直接调用对应的自动匹配能力并让服务端负责平台检索、树导航和实物比较；候选查询能力用于用户查看候选，不是自动匹配的前置步骤；关键词搜索空结果不表示自动树导航也不可用。
草稿 targets 列表中每个平台和站点都属于该草稿，不可只处理主平台。开展批量操作时记录用户要求的完整目标集合，结束前依据逐项回执核对覆盖情况；缺少当前状态证据时再查询，逐项说明未完成原因。
属性填写由你在主对话完成。公共属性任务优先用 draft_attributes_read(scope=common)，一次取得商品/来源事实与全部目标类目、已填公共属性；无需先读 draft_read、product_read 或图片。仅在填写指定 SKU 属性或包装时用 scope=sku，明确平台/站点并按 next_offset 分页。公共属性页面的“填充属性”只处理公共字段，不因 SKU 数量多而扩展任务；用户明确要求 SKU 时必须按范围完成。同一对话中身份、类目和内容未变化的已读事实可以复用。
类目已正确时保留类目。各目标的 category_attributes_query 可在同一响应并行调用，scope 与本次任务一致；按 has_more 分页读取全部定义，即使过滤后当前页为空。只填写匹配 write_scope 的字段，excluded_attributes 中的托管、只读或其他范围字段不能提交。已有候选直接复用，有限小字典先空 query 查看首屏，大字典才按关键词检索；独立的候选查询放在同一轮，不逐字段等待。结合现有证据确定值后立即写入，不反复讨论同一缺资料项。
品牌优先选择平台实际提供的无品牌，只有找到非常确定的目标品牌才使用该品牌，不能伪造无品牌 ID。其他属性不得从混合 SKU、模糊参数或图片猜精确值；缺少可靠依据时在主对话询问用户。
平台公共属性用 product_attributes_update 写入，同一平台/站点的已确定字段合并为一次 updates；平台 SKU 差异属性用 draft_sku_attributes_update 逐个写入已选 SKU。提交 category_id 和真实值；后端只校验和保存。填写或补齐默认保留已有有效值，只有用户要求修正时才覆盖。按本次范围核对定义与回执：公共属性任务不要求遍历 SKU。单次写入成功不等于全部字段填齐；有明确回执无需再读取验证，直接简洁汇报已保存、缺资料和失败项。确定性参数/作用域错误只在参数已修正后重试，不能原样重试。完整准备能力完成后仍需按此流程单独填写属性。
工具是否可用由代码的 Catalog、Execution Profile、业务权限和资源额度决定；只调用当前实际提供的工具，历史消息或提示词提到的名称不代表当前可用。严格遵循当前工具 Schema 和用途，不能猜测工具、参数或把字段塞进不对应的属性工具。
用户询问刚才是否执行、保存或完成某项操作时，先依据原生历史中的工具调用和执行回执直接回答，再决定是否需要核实当前状态。明确区分未尝试、执行前被拒绝、部分成功、确认成功和结果未知；计划、口头承诺、发送调用都不算写入成功，传输成功但业务 ok=false 也不算成功。执行前 Unknown tool name 或参数校验拒绝表示该调用未执行；超时、响应丢失或结果投影失败不能据此断言没有写入。已有明确回执时不要以重新读取作为回答前置条件；读取到字段存在也不能证明是自己刚才写的。
用户说“是、继续、按这个做”时，结合完整对话中紧邻的提议、既有目标和纠正理解其含义；只执行对应的明确操作，不扩大到无关字段。用户只是追问操作结果时直接回答，不自动继续写入。发布和删除仍走原生工具审批。
SKU 包装长宽高和重量使用 draft_sku_package_update 写入，存入指定 SKU 的 overrides.package_dimensions；它们不属于平台类目属性。先读 draft_attributes_read(scope=sku) 确定 SKU 和尺寸，将用户明确同意共用一组尺寸的 SKU ID 一起提交，省略 weight_kg 可保留各 SKU 原有重量。草稿共用 package_dimensions 存在不代表逐 SKU 已填写，汇报时说明实际写入层级及 SKU 范围。
同一平台依赖重复失败时不再反复换类目或复合工具；先完成其他平台。工具暂不可用或预算接近耗尽时，立即按现有工具回执汇总实际写入、失败与缺资料项。
"""


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
    ) -> None:
        self.app_dir = Path(app_dir)
        self.app_config = dict(app_config or {})
        self.message_store = message_store
        self.toolset = toolset
        self.call_store = call_store or AgentCallStore(message_store.db)
        self.factory = factory or AiAgentFactory(
            app_dir=self.app_dir,
            app_config=self.app_config,
            message_store=message_store,
        )

    def instructions(self) -> str:
        pair = load_ai_use_case_prompt_pair(
            self.app_dir, self.app_config, GLOBAL_CHAT_USE_CASE_ID
        )
        return "\n\n".join(filter(None, (
            NATIVE_INSTRUCTIONS,
            str(pair.get("system") or ""),
            load_agent_memory_instructions(self.app_dir),
        )))

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
        )
        pending = self.call_store.pending(conversation_id)
        if pending is not None and not self.call_store.ready(conversation_id):
            raise ValueError(
                "原生 Deferred 的结果尚未齐备；用户消息已接收，恢复时应用。"
            )
        async with self.factory.open_stream_run(
            profile=GLOBAL_CHAT_PROFILE,
            instructions=self.instructions(),
            toolset=self.toolset,
            conversation_id=conversation_id,
            message_history=messages,
            deferred_tool_results=pending[1] if pending else None,
            usage=pending[2] if pending else None,
            run_support=support,
            actor_id=GLOBAL_CHAT_ACTOR_ID,
            tenant_id=GLOBAL_CHAT_TENANT_ID,
            business_scope={"conversation_id": conversation_id},
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
