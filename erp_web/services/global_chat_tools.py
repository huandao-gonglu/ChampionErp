"""global.chat 对话的有限只读 ToolSet。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from erp_web.schemas.draft_capabilities import DraftQueryRequest, DraftQueryResult
from erp_web.services.ai_tool_catalog import AiToolBindingScope, AiToolCatalog
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.ai_tool_registry import AiToolSet
from erp_web.services.draft_query_service import (
    DraftIndexReader,
    DraftSnapshotRepository,
    query_drafts,
)


GLOBAL_CHAT_TOOLSET_ID = "global.chat"
GLOBAL_CHAT_READ_PERMISSION = "global.chat.read"
DRAFTS_QUERY_TOOL = "drafts_query"


@dataclass(frozen=True)
class GlobalChatQueryScope:
    """global.chat 独立的草稿读取与查询快照边界。"""

    products: DraftIndexReader
    draft_snapshots: DraftSnapshotRepository


@ai_tool(
    name=DRAFTS_QUERY_TOOL,
    description=(
        "受限查询本地草稿；target_platform 只表示发布目标，摘要会分别返回"
        " source_platform、target_platform、target_site；并返回总数、稳定排序"
        "摘要与 query_snapshot_id；解析‘第一个/第二个’时必须传已有 "
        "snapshot_id 和 positions。"
    ),
    permission=GLOBAL_CHAT_READ_PERMISSION,
    side_effect="none",
    version="1",
)
def drafts_query(
    request: DraftQueryRequest,
    scope: Annotated[GlobalChatQueryScope, Injected()],
) -> DraftQueryResult:
    return query_drafts(
        request,
        product_store=scope.products,
        snapshot_repository=scope.draft_snapshots,
    )


GLOBAL_CHAT_AI_TOOLS = (drafts_query,)
GLOBAL_CHAT_TOOL_CATALOG = AiToolCatalog.compile(GLOBAL_CHAT_AI_TOOLS)


def build_global_chat_toolset(
    *,
    products: DraftIndexReader,
    draft_snapshots: DraftSnapshotRepository,
) -> AiToolSet:
    """装配 global.chat 的只读 ToolSet；不注入任何写能力。"""

    scope = GlobalChatQueryScope(
        products=products,
        draft_snapshots=draft_snapshots,
    )
    return GLOBAL_CHAT_TOOL_CATALOG.bind(
        toolset_id=GLOBAL_CHAT_TOOLSET_ID,
        allowed_tools=(DRAFTS_QUERY_TOOL,),
        scope=AiToolBindingScope.from_values(scope),
        declared_permissions={GLOBAL_CHAT_READ_PERMISSION},
    )


__all__ = [
    "GLOBAL_CHAT_AI_TOOLS",
    "GLOBAL_CHAT_READ_PERMISSION",
    "GLOBAL_CHAT_TOOLSET_ID",
    "GLOBAL_CHAT_TOOL_CATALOG",
    "GlobalChatQueryScope",
    "build_global_chat_toolset",
    "drafts_query",
]
