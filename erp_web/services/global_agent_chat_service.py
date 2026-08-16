"""global.chat 对话 profile 的业务入口。

只负责选择 prompt、只读 ToolSet、Execution Profile 和权限，再调用
``AiAgentFactory``；不负责协议转换，也不接触 Global Task 规划/执行链。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from erp_web.services.global_chat_tools import (
    GLOBAL_CHAT_READ_PERMISSION,
    build_global_chat_toolset,
)
from erp_web.services.ai_agent_factory import (
    AiAgentExecutionProfile,
    AiAgentFactory,
    AiAgentStreamSession,
)
from erp_web.services.ai_prompt_templates import load_ai_use_case_prompt_pair
from erp_web.services.draft_query_service import (
    DraftIndexReader,
    DraftSnapshotRepository,
)
from erp_web.stores.pydantic_message_store import PydanticMessageStore


GLOBAL_CHAT_USE_CASE_ID = "global.chat"
GLOBAL_CHAT_PROFILE_ID = "global.chat"
GLOBAL_CHAT_TOOLSET_ID = "global.chat"
GLOBAL_CHAT_CONVERSATION_PREFIX = "conversation_global_chat_"
GLOBAL_CHAT_ACTOR_ID = "local-user"
GLOBAL_CHAT_TENANT_ID = "local"

GLOBAL_CHAT_PROFILE = AiAgentExecutionProfile(
    use_case_id=GLOBAL_CHAT_USE_CASE_ID,
    output_type=str,
    toolset_id=GLOBAL_CHAT_TOOLSET_ID,
    budget_profile="global.chat.default",
    permissions=frozenset({GLOBAL_CHAT_READ_PERMISSION}),
    timeout_seconds=180,
    max_model_requests=12,
    max_tool_calls=8,
    max_tool_output_bytes=256 * 1024,
    retries=1,
    result_version="global_chat.v1",
)

_FALLBACK_INSTRUCTIONS = (
    "你是单用户本地 ERP 的全局对话 Agent，用自然语言回答用户问题；"
    "涉及草稿事实时先调用只读 drafts_query 工具，不得编造数据，"
    "也不执行任何写操作。"
)


class GlobalAgentChatService:
    """选择 global.chat 的业务参数并交给统一的 Agent factory。"""

    def __init__(
        self,
        *,
        app_dir: Path | str,
        app_config: dict[str, Any] | None,
        message_store: PydanticMessageStore,
        products: DraftIndexReader,
        draft_snapshots: DraftSnapshotRepository,
        factory: AiAgentFactory | None = None,
    ) -> None:
        self.app_dir = Path(app_dir)
        self.app_config = dict(app_config or {})
        self.message_store = message_store
        self.toolset = build_global_chat_toolset(
            products=products,
            draft_snapshots=draft_snapshots,
        )
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
        model_override: Model | None = None,
    ) -> AsyncIterator[AiAgentStreamSession[str]]:
        """用可信历史加本轮用户输入启动一次新的流式 run。"""

        message_history = self.trusted_history(conversation_id)
        async with self.factory.open_stream_run(
            profile=GLOBAL_CHAT_PROFILE,
            instructions=self.instructions(),
            toolset=self.toolset,
            conversation_id=conversation_id,
            message_history=message_history,
            actor_id=GLOBAL_CHAT_ACTOR_ID,
            tenant_id=GLOBAL_CHAT_TENANT_ID,
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
    "GLOBAL_CHAT_READ_PERMISSION",
    "GLOBAL_CHAT_TENANT_ID",
    "GLOBAL_CHAT_TOOLSET_ID",
    "GLOBAL_CHAT_USE_CASE_ID",
    "GlobalAgentChatService",
]
