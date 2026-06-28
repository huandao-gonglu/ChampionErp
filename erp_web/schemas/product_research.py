from __future__ import annotations

from typing import Any, Literal, TypedDict


SourceType = Literal["api", "ai_search", "crawler", "third_party_api", "manual_import"]
SearchMode = Literal["target_only", "target_plus_reference", "global_scan"]


class ProductResearchPrice(TypedDict, total=False):
    amount: float
    currency: str


class HotProductCandidate(TypedDict, total=False):
    id: str
    title: str
    image_url: str
    rank: int
    source_url: str
    market_id: str
    platform: str
    site: str
    keyword: str
    price: ProductResearchPrice
    rating: float
    review_count: int
    hot_score: float
    source_name: str
    collected_at: str


class ProductResearchDataSource(TypedDict, total=False):
    id: str
    name: str
    source_type: SourceType
    platform: str
    enabled: bool
    priority: int
    supported_markets: list[str]
    supported_languages: list[str]
    supported_data_types: list[str]
    auth_required: bool
    rate_limit_per_minute: int | None
    compliance_note: str
    config_json: dict[str, Any]


class ProductResearchTargetMarket(TypedDict, total=False):
    id: str
    platform: str
    site: str
    display_name: str


class ProductResearchMarketHotProducts(TypedDict, total=False):
    market_id: str
    items: list[HotProductCandidate]


class ProductResearchConfig(TypedDict, total=False):
    search_defaults: dict[str, Any]
    provider_runtime: dict[str, Any]
    search_providers: list[ProductResearchDataSource]
    target_markets: list[ProductResearchTargetMarket]
    market_hot_products: list[ProductResearchMarketHotProducts]
    source_registry: list[ProductResearchDataSource]


class ProductResearchSearchRequest(TypedDict, total=False):
    search_mode: SearchMode
    markets: dict[str, list[str]]
    keywords: list[str]
    result_options: dict[str, Any]


class ProductResearchSourceStatus(TypedDict, total=False):
    source: str
    source_id: str
    market: str
    status: str
    items_found: int
    error_message: str
    provider_strategy: str


class ProductResearchRun(TypedDict, total=False):
    run_id: str
    status: str
    search_mode: str
    created_at: str
    completed_at: str
    request: ProductResearchSearchRequest
    items: list[HotProductCandidate]
    source_status: list[ProductResearchSourceStatus]


__all__ = [
    "HotProductCandidate",
    "ProductResearchConfig",
    "ProductResearchDataSource",
    "ProductResearchMarketHotProducts",
    "ProductResearchPrice",
    "ProductResearchRun",
    "ProductResearchSearchRequest",
    "ProductResearchSourceStatus",
    "ProductResearchTargetMarket",
    "SearchMode",
    "SourceType",
]
