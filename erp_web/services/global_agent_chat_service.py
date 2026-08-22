"""global.chat 唯一主 Agent service。

负责选择 prompt、合并后的 Direct + 任务控制 ToolSet、Execution Profile 和
权限，再调用 ``AiAgentFactory``；不负责协议转换。对话是唯一的模型入口：
任务计划通过 ``global_task_start`` 类型化参数提交，不存在第二个 Planner。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from erp_web.ai_capability_composition import (
    application_capability_permissions,
)
from erp_web.schemas.global_tasks import TASK_CONTROL_PERMISSION
from erp_web.services.ai_agent_factory import (
    AiAgentExecutionProfile,
    AiAgentFactory,
    AiAgentStreamSession,
)
from erp_web.services.ai_prompt_templates import load_ai_use_case_prompt_pair
from erp_web.services.ai_tool_registry import AiToolSet
from erp_web.stores.pydantic_message_store import PydanticMessageStore


GLOBAL_CHAT_USE_CASE_ID = "global.chat"
GLOBAL_CHAT_PROFILE_ID = "global.chat"
GLOBAL_CHAT_TOOLSET_ID = "global.chat"
GLOBAL_CHAT_CONVERSATION_PREFIX = "conversation_global_chat_"
GLOBAL_CHAT_ACTOR_ID = "local-user"
GLOBAL_CHAT_TENANT_ID = "local"

GLOBAL_CHAT_PROFILE = AiAgentExecutionProfile(
    use_case_id=GLOBAL_CHAT_USE_CASE_ID,
    # 包含 DeferredToolRequests 后，global_task_start 的 CallDeferred 才会让
    # run 以官方 Deferred 语义暂停；任务终结后由 continuation 用
    # DeferredToolResults 恢复同一 conversation，模型不需要忙轮询。
    output_type=str | DeferredToolRequests,
    toolset_id=GLOBAL_CHAT_TOOLSET_ID,
    budget_profile="global.chat.default",
    permissions=application_capability_permissions() | {TASK_CONTROL_PERMISSION},
    timeout_seconds=180,
    max_model_requests=16,
    max_tool_calls=12,
    max_tool_output_bytes=256 * 1024,
    retries=1,
    result_version="global_chat.v1",
    allow_write=True,
)

_FALLBACK_INSTRUCTIONS = (
    "你是单用户本地 ERP 的全局对话 Agent，用自然语言回答用户问题；"
    "涉及草稿事实时先调用只读查询工具，不得编造数据。"
    "需要修改商品、准备目标市场或发布等业务操作时，"
    "用 global_task_start 提交类型化任务步骤。提交任务后你的本轮结束，"
    "任务终结时系统会通过 Deferred 机制自动恢复并生成最终回复，"
    "你不要忙轮询任务状态。"
)


class GlobalAgentChatService:
    """选择 global.chat 的业务参数并交给统一的 Agent factory。"""

    def __init__(
        self,
        *,
        app_dir: Path | str,
        app_config: dict[str, Any] | None,
        message_store: PydanticMessageStore,
        toolset: AiToolSet,
        factory: AiAgentFactory | None = None,
    ) -> None:
        self.app_dir = Path(app_dir)
        self.app_config = dict(app_config or {})
        self.message_store = message_store
        if toolset.toolset_id != GLOBAL_CHAT_TOOLSET_ID:
            raise ValueError(
                f"global.chat ToolSet 标识必须是 {GLOBAL_CHAT_TOOLSET_ID}"
            )
        self.toolset = toolset
        self.factory = factory or AiAgentFactory(
            app_dir=self.app_dir,
            app_config=self.app_config,
            message_store=message_store,
        )

    def instructions(self) -> str:
        """服务端 instructions；客户端永远不能覆盖。"""

        prompt = load_ai_use_case_prompt_pair(
            self.app_dir,
            self.app_config,
            GLOBAL_CHAT_USE_CASE_ID,
        )
        system = str(prompt.get("system") or "").strip()
        return system or _FALLBACK_INSTRUCTIONS

    def trusted_history(self, conversation_id: str) -> list[ModelMessage]:
        """读取服务端可信历史；不存在时返回空列表。"""

        history = self.message_store.get(conversation_id)
        if history is None:
            return []
        return history.model_messages()

    @asynccontextmanager
    async def open_chat_run(
        self,
        *,
        conversation_id: str,
        new_messages: Sequence[ModelMessage],
        client_message_id: str = "",
        model_override: Model | None = None,
    ) -> AsyncIterator[AiAgentStreamSession[str]]:
        """用可信历史加本轮用户输入启动一次新的流式 run。

        conversation 与 message ID 进入可信 business/idempotency Scope，
        供任务控制 Capability 绑定幂等上下文。首次 Deferred 握手的 history
        由协议层与 link ready/outbox 同事务提交，session 不自动保存。
        """

        message_history = self.trusted_history(conversation_id)
        business_scope = {"conversation_id": conversation_id}
        idempotency_context = {"conversation_id": conversation_id}
        normalized_message_id = str(client_message_id or "").strip()
        if normalized_message_id:
            business_scope["message_id"] = normalized_message_id
            idempotency_context["message_id"] = normalized_message_id
        async with self.factory.open_stream_run(
            profile=GLOBAL_CHAT_PROFILE,
            instructions=self.instructions(),
            toolset=self.toolset,
            conversation_id=conversation_id,
            message_history=message_history,
            external_deferred_commit=True,
            actor_id=GLOBAL_CHAT_ACTOR_ID,
            tenant_id=GLOBAL_CHAT_TENANT_ID,
            business_scope=business_scope,
            idempotency_context=idempotency_context,
            model_override=model_override,
        ) as session:
            yield session

    @asynccontextmanager
    async def open_continuation_run(
        self,
        *,
        conversation_id: str,
        deferred_tool_results: DeferredToolResults,
        model_override: Model | None = None,
    ) -> AsyncIterator[AiAgentStreamSession[str]]:
        """后台 continuation 的唯一入口：同一 ``global.chat`` 运行路径。

        复用相同 conversation_id、生成新 run_id，不合成新的
        ``UserPromptPart``；最终 history 由恢复服务按 link 冻结版本 CAS 提交
        （``external_final_commit``），Factory/session 不自动落消息。
        """

        message_history = self.trusted_history(conversation_id)
        async with self.factory.open_stream_run(
            profile=GLOBAL_CHAT_PROFILE,
            instructions=self.instructions(),
            toolset=self.toolset,
            conversation_id=conversation_id,
            message_history=message_history,
            deferred_tool_results=deferred_tool_results,
            external_final_commit=True,
            actor_id=GLOBAL_CHAT_ACTOR_ID,
            tenant_id=GLOBAL_CHAT_TENANT_ID,
            business_scope={"conversation_id": conversation_id},
            idempotency_context={"conversation_id": conversation_id},
            model_override=model_override,
        ) as session:
            yield session

    def dump_messages_for_conversation(
        self,
        conversation_id: str,
    ) -> list[ModelMessage] | None:
        """读取规范 ModelMessage 历史；不存在返回 None。"""

        history = self.message_store.get(conversation_id)
        if history is None:
            return None
        return history.model_messages()


__all__ = [
    "GLOBAL_CHAT_ACTOR_ID",
    "GLOBAL_CHAT_CONVERSATION_PREFIX",
    "GLOBAL_CHAT_PROFILE",
    "GLOBAL_CHAT_PROFILE_ID",
    "GLOBAL_CHAT_TENANT_ID",
    "GLOBAL_CHAT_TOOLSET_ID",
    "GLOBAL_CHAT_USE_CASE_ID",
    "GlobalAgentChatService",
]
