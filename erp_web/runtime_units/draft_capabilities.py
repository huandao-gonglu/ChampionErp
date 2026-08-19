from __future__ import annotations

"""草稿查询的类型化 Capability；global.chat direct 与 Task 复用同一实现。"""

from dataclasses import dataclass
from typing import Annotated

from erp_web.schemas.draft_capabilities import DraftQueryRequest, DraftQueryResult
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.draft_query_service import (
    DraftIndexReader,
    DraftSnapshotRepository,
    query_drafts,
)


@dataclass(frozen=True)
class DraftQueryCapabilityScope:
    """草稿读取与查询快照的可信边界。"""

    products: DraftIndexReader
    draft_snapshots: DraftSnapshotRepository


DRAFTS_QUERY_TOOL = "drafts_query"


@ai_tool(
    name=DRAFTS_QUERY_TOOL,
    description=(
        "受限查询本地草稿；target_platform 只表示发布目标，摘要会分别返回"
        " source_platform、target_platform、target_site；并返回总数、稳定排序"
        "摘要与 query_snapshot_id；解析‘第一个/第二个’时必须传已有 "
        "snapshot_id 和 positions。"
    ),
    permission="draft.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="2",
)
def drafts_query(
    request: DraftQueryRequest,
    scope: Annotated[DraftQueryCapabilityScope, Injected()],
) -> DraftQueryResult:
    return query_drafts(
        request,
        product_store=scope.products,
        snapshot_repository=scope.draft_snapshots,
    )


DRAFT_QUERY_AI_CAPABILITIES = (drafts_query,)


__all__ = [
    "DRAFTS_QUERY_TOOL",
    "DRAFT_QUERY_AI_CAPABILITIES",
    "DraftQueryCapabilityScope",
    "drafts_query",
]
