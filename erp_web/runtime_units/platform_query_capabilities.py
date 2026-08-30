from __future__ import annotations

"""平台商品/订单与发布队列的只读查询 Capability。"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from erp_web.schemas.platform_query_capabilities import (
    PlatformOrdersQueryRequest,
    PlatformOrdersQueryResult,
    MercadoLibreUserProductsQueryRequest,
    MercadoLibreUserProductsQueryResult,
    ProductsIndexQueryRequest,
    ProductsIndexQueryResult,
    PublishJobStatusQueryRequest,
    PublishJobStatusQueryResult,
    PublishJobsQueryRequest,
    PublishJobsQueryResult,
    PublishLogsQueryRequest,
    PublishLogsQueryResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError


class ProductsIndexReader(Protocol):
    def load_products_index(self) -> list[dict[str, Any]]:
        ...


class PublishingBusQueryLike(Protocol):
    def list_jobs(
        self,
        *,
        limit: int,
        cursor: str,
        status: str,
        platform: str,
        product_id: str,
    ) -> dict[str, Any]:
        ...

    def get_public_status(self, job_id: str) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class PlatformQueryCapabilityScope:
    """平台/发布队列查询的可信依赖边界。"""

    products: ProductsIndexReader
    user_products_loader: Callable[..., dict[str, Any]]
    orders_loader: Callable[..., dict[str, Any]]
    publish_logs_loader: Callable[..., list[dict[str, Any]]]
    publishing_bus: PublishingBusQueryLike


def _text(value: Any) -> str:
    return str(value or "").strip()


def _require_ok(
    result: Any,
    *,
    default_code: str,
    default_message: str,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise BusinessCapabilityError(default_code, default_message)
    if not result.get("ok"):
        raise BusinessCapabilityError(
            _text(result.get("error_code")) or default_code,
            _text(result.get("error")) or default_message,
        )
    return result


def _dict_rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _products_index_snapshot_id(items: tuple[dict[str, Any], ...]) -> str:
    """为模型看到的有序商品列表生成内容寻址快照。"""

    encoded = json.dumps(
        items,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"products_{hashlib.sha256(encoded).hexdigest()[:32]}"


def _require_platform(platform: str) -> str:
    normalized = _text(platform).lower()
    if normalized != "mercadolibre":
        raise BusinessCapabilityError(
            "PLATFORM_QUERY_UNSUPPORTED",
            f"平台 {platform} 暂未接入平台查询能力。",
        )
    return normalized


PRODUCTS_INDEX_QUERY_TOOL = "products_index_query"
MERCADOLIBRE_USER_PRODUCTS_QUERY_TOOL = "mercadolibre_user_products_query"
PLATFORM_ORDERS_QUERY_TOOL = "platform_orders_query"
PUBLISH_LOGS_QUERY_TOOL = "publish_logs_query"
PUBLISH_JOBS_QUERY_TOOL = "publish_jobs_query"
PUBLISH_JOB_STATUS_QUERY_TOOL = "publish_job_status_query"


@ai_tool(
    name=PRODUCTS_INDEX_QUERY_TOOL,
    description="读取本地可信商品索引；可用快照 ID 和一基位置安全解析第几个商品。",
    permission="product.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="2",
)
def products_index_query(
    request: ProductsIndexQueryRequest,
    scope: Annotated[PlatformQueryCapabilityScope, Injected()],
) -> ProductsIndexQueryResult:
    items = _dict_rows(scope.products.load_products_index())
    snapshot_id = _products_index_snapshot_id(items)
    selected_items: tuple[dict[str, Any], ...] = ()
    if request.positions:
        if not request.snapshot_id:
            raise BusinessCapabilityError(
                "PRODUCTS_INDEX_SNAPSHOT_REQUIRED",
                "按位置选择商品时必须提交最近一次查询返回的 snapshot_id。",
            )
        if request.snapshot_id != snapshot_id:
            raise BusinessCapabilityError(
                "PRODUCTS_INDEX_SNAPSHOT_STALE",
                "商品列表已变化，请重新查询后再按位置选择。",
            )
        invalid = [position for position in request.positions if position > len(items)]
        if invalid:
            raise BusinessCapabilityError(
                "PRODUCTS_INDEX_POSITION_INVALID",
                f"商品位置超出当前列表范围：{invalid[0]}。",
            )
        selected_items = tuple(items[position - 1] for position in request.positions)
    return ProductsIndexQueryResult(
        items=items,
        count=len(items),
        snapshot_id=snapshot_id,
        selected_items=selected_items,
    )


@ai_tool(
    name=MERCADOLIBRE_USER_PRODUCTS_QUERY_TOOL,
    description="查询本地已持久化的 Mercado Siteless User Products 列表。",
    permission="platform.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def mercadolibre_user_products_query(
    request: MercadoLibreUserProductsQueryRequest,
    scope: Annotated[PlatformQueryCapabilityScope, Injected()],
) -> MercadoLibreUserProductsQueryResult:
    platform = _require_platform(request.platform)
    result = _require_ok(
        scope.user_products_loader(
            status=_text(request.status).lower() or "all",
            page=request.page,
            per_page=request.per_page,
        ),
        default_code="MERCADOLIBRE_USER_PRODUCTS_QUERY_FAILED",
        default_message="Mercado User Products 查询失败。",
    )
    pagination = (
        result.get("pagination")
        if isinstance(result.get("pagination"), dict)
        else {}
    )
    return MercadoLibreUserProductsQueryResult(
        platform=platform,
        status=_text(result.get("status")),
        items=_dict_rows(result.get("items")),
        pagination=dict(pagination),
        refresh_errors=_dict_rows(result.get("refresh_errors")),
        checked_at=_text(result.get("checked_at")),
    )


@ai_tool(
    name=PLATFORM_ORDERS_QUERY_TOOL,
    description="查询目标平台店铺最近订单列表（远端只读）。",
    permission="platform.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def platform_orders_query(
    request: PlatformOrdersQueryRequest,
    scope: Annotated[PlatformQueryCapabilityScope, Injected()],
) -> PlatformOrdersQueryResult:
    platform = _require_platform(request.platform)
    result = _require_ok(
        scope.orders_loader(limit=request.limit, offset=request.offset),
        default_code="PLATFORM_ORDERS_QUERY_FAILED",
        default_message="平台订单查询失败。",
    )
    pagination = (
        result.get("pagination")
        if isinstance(result.get("pagination"), dict)
        else {}
    )
    return PlatformOrdersQueryResult(
        platform=platform,
        items=_dict_rows(result.get("items")),
        notifications=_dict_rows(result.get("notifications")),
        pagination=dict(pagination),
        checked_at=_text(result.get("checked_at")),
    )


@ai_tool(
    name=PUBLISH_LOGS_QUERY_TOOL,
    description="查询本地发布日志（最近 N 条）。",
    permission="publish.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def publish_logs_query(
    request: PublishLogsQueryRequest,
    scope: Annotated[PlatformQueryCapabilityScope, Injected()],
) -> PublishLogsQueryResult:
    items = _dict_rows(scope.publish_logs_loader(limit=request.limit))
    return PublishLogsQueryResult(items=items, count=len(items))


@ai_tool(
    name=PUBLISH_JOBS_QUERY_TOOL,
    description="查询 PublishingBus 发布任务队列列表（可按状态/平台/商品过滤）。",
    permission="publish.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def publish_jobs_query(
    request: PublishJobsQueryRequest,
    scope: Annotated[PlatformQueryCapabilityScope, Injected()],
) -> PublishJobsQueryResult:
    result = scope.publishing_bus.list_jobs(
        limit=request.limit,
        cursor=request.cursor,
        status=_text(request.status).lower(),
        platform=_text(request.platform).lower(),
        product_id=request.product_id,
    )
    if not isinstance(result, dict):
        raise BusinessCapabilityError(
            "PUBLISH_JOBS_QUERY_FAILED",
            "发布任务查询失败。",
        )
    jobs = _dict_rows(result.get("jobs") or result.get("items"))
    next_cursor = _text(result.get("cursor") or result.get("next_cursor"))
    return PublishJobsQueryResult(jobs=jobs, next_cursor=next_cursor, count=len(jobs))


@ai_tool(
    name=PUBLISH_JOB_STATUS_QUERY_TOOL,
    description="按 job_id 查询单个发布任务的公开状态。",
    permission="publish.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def publish_job_status_query(
    request: PublishJobStatusQueryRequest,
    scope: Annotated[PlatformQueryCapabilityScope, Injected()],
) -> PublishJobStatusQueryResult:
    try:
        job = scope.publishing_bus.get_public_status(request.job_id)
    except BusinessCapabilityError:
        raise
    except Exception as exc:
        raise BusinessCapabilityError(
            "PUBLISH_JOB_NOT_FOUND",
            f"发布任务不存在或状态不可读：{exc}",
        ) from exc
    if not isinstance(job, dict):
        raise BusinessCapabilityError(
            "PUBLISH_JOB_NOT_FOUND",
            "发布任务状态不可读。",
        )
    return PublishJobStatusQueryResult(job=dict(job))


PLATFORM_QUERY_AI_CAPABILITIES = (
    products_index_query,
    mercadolibre_user_products_query,
    platform_orders_query,
    publish_logs_query,
    publish_jobs_query,
    publish_job_status_query,
)


__all__ = [
    "PLATFORM_ORDERS_QUERY_TOOL",
    "MERCADOLIBRE_USER_PRODUCTS_QUERY_TOOL",
    "PLATFORM_QUERY_AI_CAPABILITIES",
    "PRODUCTS_INDEX_QUERY_TOOL",
    "PUBLISH_JOBS_QUERY_TOOL",
    "PUBLISH_JOB_STATUS_QUERY_TOOL",
    "PUBLISH_LOGS_QUERY_TOOL",
    "PlatformQueryCapabilityScope",
    "ProductsIndexReader",
    "PublishingBusQueryLike",
    "platform_orders_query",
    "mercadolibre_user_products_query",
    "products_index_query",
    "publish_job_status_query",
    "publish_jobs_query",
    "publish_logs_query",
]
