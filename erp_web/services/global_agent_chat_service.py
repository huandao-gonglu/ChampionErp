"""主对话和草稿批量入口共用的原生 Agent service。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from pydantic_ai import DeferredToolRequests, ModelRetry
from pydantic_ai.messages import ToolCallPart, ToolReturnPart, UserPromptPart
from erp_web.ai_capability_composition import application_capability_permissions
from erp_web.services.ai_agent_factory import AiAgentExecutionProfile, AiAgentFactory
from erp_web.services.ai_prompt_templates import load_ai_use_case_prompt_pair
from erp_web.services.agent_run_storage import AgentRunStorage
from erp_web.services.agent_memory import load_agent_memory_instructions
from erp_web.stores.agent_call_store import AgentCallStore
from erp_web.services.chat_operation_scope import resolve_chat_operation_scope

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
先读取目标草稿、关联商品、店铺/平台资料和相关用户历史，复用已有事实。缺字段或可修复错误时，先查询、纠正参数或选择适用工具；只询问确实缺失或冲突的信息。
多条独立草稿可以并发准备，一条缺资料时继续处理其他可执行草稿。不处理用户未选中的草稿。
商品共用事实可以复用，平台类目、售价、币种和销售目标必须按站点范围处理。模型猜测不能成为用户决定；来源引用只能来自真实用户消息或已保存事实。
草稿准备不包含发布授权。发布、删除等需审批操作必须由用户批准；在可独立完成的准备之后再收集审批。
后台工具暂停时已启动的领域 Job 继续运行，Agent 等待结果。恢复后可继续查询和调用业务工具，不能把排队、远端处理中或未知结果汇报成成功。
收到纠正或取消后优先遵守最新要求。按草稿报告已完成内容、剩余缺口和证据；预算耗尽时明确未完成事项。
用户只要求类目、公共属性时，只执行这些操作；完整准备能力会额外修改文案、图片和价格，不能用于窄范围任务的失败恢复。
自动匹配或复核类目时直接调用对应的自动匹配能力并让服务端负责平台检索、树导航和实物比较；候选查询能力用于用户查看候选，不是自动匹配的前置步骤；关键词搜索空结果不表示自动树导航也不可用。
草稿 targets 列表中每个平台和站点都属于该草稿，不可只处理主平台。开始时记录完整目标集合，结束前再次查询核对每个目标，逐项说明未完成原因。
属性填写由你在主对话完成：用 draft_read 定位当前草稿与全部目标，用 draft_attributes_read 按目标读取完整已填属性及 SKU 事实（next_offset 非空时继续分页），必要时用 product_read 或 inspect_source_facts 补充商品事实。同一对话中身份、类目和内容未变化的已读事实可以复用；发生编辑或切换对象时重新读取。
类目已正确时保留类目。用 category_attributes_query 分页读取全部定义（has_more 时继续），按需要用 category_attribute_values_query 查询真实候选；不得只看前若干可选字段。根据事实填写可确定的必填和可选属性，不强填缺资料项。
品牌优先选择平台实际提供的无品牌，只有找到非常确定的目标品牌才使用该品牌，不能伪造无品牌 ID。其他属性不得从混合 SKU、模糊参数或图片猜精确值；缺少可靠依据时在主对话询问用户。
公共字段用 product_attributes_update 写入；SKU 差异字段用 draft_sku_attributes_update 逐个写入已选 SKU。提交 category_id 和真实值；后端只校验和保存。填写或补齐默认保留已有有效值，只有用户要求修正时才覆盖。单次写入回执不表示全部完成，核对全部定义、已选 SKU 与实际已存值，汇报写入、缺资料和失败项。完整准备能力完成后仍需按此流程单独填写属性。
同一平台依赖重复失败时不再反复换类目或复合工具；先完成其他平台。工具暂不可用或预算接近耗尽时，立即按现有工具回执汇总实际写入、失败与缺资料项。
"""


class GlobalAgentChatService:
    @staticmethod
    def validate_target_coverage(ctx, output):
        """结果提交前核对实际查询范围；只反馈缺漏，由原生 Agent 决定如何处理。"""
        support = ctx.deps.tool_runtime.run_support
        if not isinstance(output, str) or not support.all_drafts:
            return output
        requested = set(support.allowed_write_tools or ()) & {
            "category_match"
        }
        targets = {}
        called = set()
        # Deferred 恢复会产生新 run_id；同一真实用户回合的回执仍属于当前任务。
        user_indexes = [index for index, message in enumerate(ctx.messages)
                        if any(isinstance(part, UserPromptPart) for part in message.parts)]
        turn_messages = (ctx.messages[user_indexes[-1]:] if user_indexes else
                         [message for message in ctx.messages if message.run_id == ctx.run_id])
        returned_calls = {
            (part.tool_name, part.tool_call_id): part.content
            for message in turn_messages
            for part in message.parts if isinstance(part, ToolReturnPart)
        }
        reader = getattr(support, "target_reader", None)
        if reader is not None:
            for item in reader():
                if support.target_draft_ids and item["draft_id"] not in support.target_draft_ids:
                    continue
                for target in item.get("raw", {}).get("target_sites", []):
                    targets[(item["draft_id"], target["platform"], target["site"])] = target
        for message in turn_messages:
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == "drafts_query" and isinstance(part.content, dict):
                    for item in part.content.get("items", []):
                        for target in item.get("targets", []):
                            key = (item["draft_id"], target["platform"], target["site"])
                            targets[key] = target
                if (isinstance(part, ToolCallPart) and part.tool_name in requested
                        and (part.tool_name, part.tool_call_id) in returned_calls):
                    args = part.args_as_dict()
                    key = (args.get("draft_id"), args.get("target_platform"), args.get("site", ""), part.tool_name)
                    called.add(key)
        missing = []
        for key, target in targets.items():
            for operation in requested:
                if operation == "category_match" and target.get("category_id"):
                    continue
                if (*key, operation) not in called and (key[0], key[1], "", operation) not in called:
                    missing.append(f"{key[0]}/{key[1]}/{key[2]}:{operation}")
        if missing:
            raise ModelRetry("完整目标集合仍有未处理项：" + "、".join(missing)
                             + "。请核对未处理的类目目标；若上游持续失败或确实不能执行，"
                             "逐项明确说明原因，不得宣称全部完成。")
        return output

    def __init__(
        self,
        *,
        app_dir: Path | str,
        app_config: dict | None,
        message_store: Any,
        toolset: Any,
        factory: Any = None,
        call_store: AgentCallStore | None = None,
        scope_resolver=None,
        target_reader=None,
    ) -> None:
        self.app_dir = Path(app_dir)
        self.app_config = dict(app_config or {})
        self.message_store = message_store
        self.toolset = toolset
        self.call_store = call_store or AgentCallStore(message_store.db)
        self.target_reader = target_reader
        self.factory = factory or AiAgentFactory(
            app_dir=self.app_dir,
            app_config=self.app_config,
            message_store=message_store,
        )
        self.scope_resolver = scope_resolver or (
            lambda messages: resolve_chat_operation_scope(
                self.factory, GLOBAL_CHAT_PROFILE, self.toolset, messages
            )
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
            scope_resolver=self.scope_resolver,
        )
        support.target_reader = self.target_reader
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
            output_validator=self.validate_target_coverage,
        ) as session:
            yield session

    def dump_messages_for_conversation(self, conversation_id: str):
        history = self.message_store.get(conversation_id)
        return history.model_messages() if history else None
