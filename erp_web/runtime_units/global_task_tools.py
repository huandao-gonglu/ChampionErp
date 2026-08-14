"""全局 Agent planning run 的有限只读 ToolSet。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from erp_web.schemas.draft_capabilities import DraftQueryRequest, DraftQueryResult
from erp_web.services.ai_tool_catalog import AiToolBindingScope, AiToolCatalog
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.ai_tool_registry import AiToolSet
from erp_web.services.draft_query_service import query_drafts
from erp_web.stores.global_task_store import LocalGlobalTaskStore
from erp_web.stores.product_store import ProductStore


GLOBAL_TASK_PLAN_TOOLSET_ID = "global.task.plan"
GLOBAL_TASK_READ_PERMISSION = "global.task.read"
DRAFTS_QUERY_TOOL = "drafts_query"


@dataclass(frozen=True)
class GlobalTaskPlanningScope:
    """由 composition root 注入、模型不可提交的本地读取边界。"""

    products: ProductStore
    global_tasks: LocalGlobalTaskStore
    recent_snapshot_id: str = ""


@ai_tool(
    name=DRAFTS_QUERY_TOOL,
    description=(
        "受限查询本地草稿；target_platform 只表示发布目标，摘要会分别返回"
        " source_platform、target_platform、target_site；并返回总数、稳定排序"
        "摘要与 query_snapshot_id；"
        "解析‘第一个/第二个’时必须传已有 snapshot_id 和 positions。"
    ),
    permission=GLOBAL_TASK_READ_PERMISSION,
    side_effect="none",
    version="1",
)
def drafts_query(
    request: DraftQueryRequest,
    scope: Annotated[GlobalTaskPlanningScope, Injected()],
) -> DraftQueryResult:
    effective = request
    if request.positions and not request.snapshot_id and scope.recent_snapshot_id:
        effective = request.model_copy(
            update={"snapshot_id": scope.recent_snapshot_id}
        )
    return query_drafts(
        effective,
        product_store=scope.products,
        snapshot_repository=scope.global_tasks,
    )


GLOBAL_TASK_AI_TOOLS = (drafts_query,)
GLOBAL_TASK_TOOL_CATALOG = AiToolCatalog.compile(GLOBAL_TASK_AI_TOOLS)


def build_global_task_planning_toolset(
    *,
    products: ProductStore,
    global_tasks: LocalGlobalTaskStore,
    recent_snapshot_id: str = "",
) -> AiToolSet:
    scope = GlobalTaskPlanningScope(
        products=products,
        global_tasks=global_tasks,
        recent_snapshot_id=str(recent_snapshot_id or "").strip(),
    )
    return GLOBAL_TASK_TOOL_CATALOG.bind(
        toolset_id=GLOBAL_TASK_PLAN_TOOLSET_ID,
        allowed_tools=(DRAFTS_QUERY_TOOL,),
        scope=AiToolBindingScope.from_values(scope),
        declared_permissions={GLOBAL_TASK_READ_PERMISSION},
    )


__all__ = [
    "DRAFTS_QUERY_TOOL",
    "GLOBAL_TASK_AI_TOOLS",
    "GLOBAL_TASK_PLAN_TOOLSET_ID",
    "GLOBAL_TASK_READ_PERMISSION",
    "GLOBAL_TASK_TOOL_CATALOG",
    "GlobalTaskPlanningScope",
    "build_global_task_planning_toolset",
    "drafts_query",
]
