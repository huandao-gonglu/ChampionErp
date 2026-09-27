from __future__ import annotations

from typing import Any, TypedDict

from .product import Product


class PublishConfirmation(TypedDict, total=False):
    """一次发布的确认计划与最近检查；空 next_check_at 表示没有自动检查计划。"""

    submitted_at: str
    next_check_at: str
    last_checked_at: str
    check_error: str


class PublishPlatformState(TypedDict, total=False):
    platform: str
    product_id: str
    draft_id: str
    site: str
    status: str
    stage: str
    error: str
    result: dict[str, Any] | None
    attempts: int
    created_at: str
    updated_at: str
    category_id: str
    confirmation: PublishConfirmation


class PublishJob(TypedDict, total=False):
    job_id: str
    draft_id: str
    status: str
    product_name: str
    product: Product
    platforms: dict[str, PublishPlatformState]
    persisted_drafts: dict[str, dict[str, Any]]
    created_at: str
    updated_at: str


class PublishJobSiteToSellSummary(TypedDict):
    """发布队列公开摘要中的销售子市场白名单。"""

    site_id: str
    logistic_type: str


class PublishJobMarketResultSummary(TypedDict):
    """统一刊登内单个销售市场的公开结果。"""

    site_id: str
    logistic_type: str
    status: str
    item_id: str
    error: str
    error_code: str


class PublishJobPlatformSummary(TypedDict):
    """单个平台的轻量发布状态，不包含已批准 payload。"""

    platform: str
    draft_id: str
    site: str
    sites_to_sell: list[PublishJobSiteToSellSummary]
    market_results: list[PublishJobMarketResultSummary]
    status: str
    stage: str
    attempts: int
    error: str
    error_code: str
    next_action: str
    updated_at: str
    confirmation: PublishConfirmation


class PublishJobSummary(TypedDict):
    """``GET /api/publish-bus/jobs`` 的单条任务摘要。"""

    job_id: str
    product_id: str
    product_name: str
    draft_id: str
    status: str
    raw_status: str
    stage: str
    attempts: int
    error: str
    error_code: str
    next_action: str
    platforms: list[PublishJobPlatformSummary]
    created_at: str
    updated_at: str


__all__ = [
    "PublishConfirmation",
    "PublishJob",
    "PublishJobMarketResultSummary",
    "PublishJobPlatformSummary",
    "PublishJobSiteToSellSummary",
    "PublishJobSummary",
    "PublishPlatformState",
]
