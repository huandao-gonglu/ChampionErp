from __future__ import annotations

"""商品研究（热销选品）Capability。

领域逻辑仍由 ``product_research_service`` 拥有；Capability 只做类型化
编排。创建研究运行是写入（持久化 run 并触发后台外部检索），按统一的
persistent_job 契约返回领域无关的 ``JobReferenceResult``（job_id +
job_type）；查询是只读。Controller 通过 Job Status Reader 注册表跟踪
运行终态，不直接依赖研究领域模块。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from erp_web.schemas.ai_tools import (
    PRODUCT_RESEARCH_JOB_TYPE,
    JobReferenceResult,
)
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.collect_capabilities import (
    ResearchHotProductsSearchRequest,
    ResearchRunStatusQueryRequest,
    ResearchRunStatusQueryResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _dict_rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


@dataclass(frozen=True)
class ResearchCapabilityScope:
    """商品研究能力的可信依赖边界。"""

    run_creator: Callable[[dict[str, Any]], dict[str, Any]]
    run_loader: Callable[[str], dict[str, Any] | None]
    active_run_loader: Callable[[], dict[str, Any] | None]


RESEARCH_HOT_PRODUCTS_SEARCH_TOOL = "research_hot_products_search"
RESEARCH_RUN_STATUS_QUERY_TOOL = "research_run_status_query"

_ACTIVE_JOB_STATUSES = frozenset({"queued", "pending", "running", "retrying"})


@ai_tool(
    name=RESEARCH_HOT_PRODUCTS_SEARCH_TOOL,
    description=(
        "创建热销商品研究任务（后台异步执行多源检索）；返回通用 Job 引用，"
        "由任务系统跟踪运行终态，运行完成前步骤不会标记完成。"
    ),
    permission="research.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    execution_mode="persistent_job",
    version="1",
)
def research_hot_products_search(
    request: ResearchHotProductsSearchRequest,
    scope: Annotated[ResearchCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> JobReferenceResult:
    del execution
    body: dict[str, Any] = {}
    if request.target_markets:
        body["markets"] = {"target_markets": list(request.target_markets)}
    if request.limit > 0:
        body["result_options"] = {"limit": request.limit}
    try:
        result = scope.run_creator(body)
    except ValueError as exc:
        raise BusinessCapabilityError(
            "RESEARCH_REQUEST_INVALID",
            str(exc) or "研究请求无效。",
        ) from exc
    except BusinessCapabilityError:
        raise
    except Exception as exc:
        raise BusinessCapabilityError(
            "RESEARCH_START_FAILED",
            str(exc) or "研究任务创建失败。",
        ) from exc
    if not isinstance(result, dict) or not result.get("ok"):
        raise BusinessCapabilityError(
            "RESEARCH_START_FAILED",
            _text(result.get("error") if isinstance(result, dict) else "")
            or "研究任务创建失败。",
        )
    run = _dict_value(result.get("run"))
    job_id = _text(run.get("run_id"))
    if not job_id:
        raise BusinessCapabilityError(
            "RESEARCH_RUN_ID_MISSING",
            "选品研究运行创建成功但缺少 run_id。",
        )
    status = _text(run.get("status")).lower()
    return JobReferenceResult(
        job_id=job_id,
        job_type=PRODUCT_RESEARCH_JOB_TYPE,
        status=status if status in _ACTIVE_JOB_STATUSES else "running",
        summary=(
            _text(result.get("description"))
            or "选品研究运行已创建，后台正在执行多源检索。"
        ),
    )


@ai_tool(
    name=RESEARCH_RUN_STATUS_QUERY_TOOL,
    description="查询商品研究运行状态与候选结果；run_id 为空时返回当前活跃运行。",
    permission="research.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def research_run_status_query(
    request: ResearchRunStatusQueryRequest,
    scope: Annotated[ResearchCapabilityScope, Injected()],
) -> ResearchRunStatusQueryResult:
    if request.run_id:
        run = scope.run_loader(request.run_id)
        if run is None:
            raise BusinessCapabilityError(
                "RESEARCH_RUN_NOT_FOUND",
                f"选品运行不存在：{request.run_id}",
            )
        active = False
    else:
        run = scope.active_run_loader()
        active = run is not None
        run = run or {}
    return ResearchRunStatusQueryResult(
        ok=True,
        active=active,
        run=_dict_value(run),
        items=_dict_rows(run.get("items") if run else ()),
        source_status=_dict_rows(run.get("source_status") if run else ()),
        description=_text(run.get("description") if run else ""),
    )


RESEARCH_AI_CAPABILITIES = (
    research_hot_products_search,
    research_run_status_query,
)


__all__ = [
    "RESEARCH_AI_CAPABILITIES",
    "RESEARCH_HOT_PRODUCTS_SEARCH_TOOL",
    "RESEARCH_RUN_STATUS_QUERY_TOOL",
    "ResearchCapabilityScope",
    "research_hot_products_search",
    "research_run_status_query",
]
