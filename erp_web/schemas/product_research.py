from __future__ import annotations

from typing import Any, Literal, TypedDict


SourceType = Literal["api", "ai_search", "crawler", "third_party_api", "manual_import"]
SearchMode = Literal["target_only", "target_plus_reference", "global_scan"]


class ProductResearchPrice(TypedDict, total=False):
    amount: float
    currency: str


class ProductResearchMetrics(TypedDict, total=False):
    search_interest: float
    review_count: int
    rating: float
    content_heat: float
    engagement_count: int


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
    market: str
    name: str
    enabled: bool
    language: str
    currency: str
    reference_markets: list[str]
    provider_ids: list[str]


class ProductResearchConfig(TypedDict, total=False):
    search_defaults: dict[str, Any]
    provider_runtime: dict[str, Any]
    reference_market_map: dict[str, list[str]]
    market_languages: dict[str, str]
    china_element_catalog: dict[str, dict[str, Any]]
    upgrade_type_catalog: dict[str, str]
    scoring_weights: dict[str, int]
    search_providers: list[ProductResearchDataSource]
    target_markets: list[ProductResearchTargetMarket]
    source_registry: list[ProductResearchDataSource]


class ProductResearchSearchRequest(TypedDict, total=False):
    search_mode: SearchMode
    markets: dict[str, list[str]]
    keywords: list[str]
    product_intent: dict[str, bool]
    filters: dict[str, list[str]]
    sources: dict[str, list[str]]
    result_options: dict[str, Any]


class NormalizedDemandSignal(TypedDict, total=False):
    source: str
    source_id: str
    source_type: SourceType
    market: str
    language: str
    keyword: str
    china_element_type: str
    data_type: str
    title: str
    product_url: str
    image_url: str
    price: ProductResearchPrice
    metrics: ProductResearchMetrics
    captured_at: str


class ProductResearchSourceStatus(TypedDict, total=False):
    source: str
    source_id: str
    market: str
    status: str
    items_found: int
    error_message: str
    provider_strategy: str


class ProductResearchCandidate(TypedDict, total=False):
    candidate_id: str
    target_market: str
    overseas_keyword: str
    china_element_type: str
    product_type: str
    related_sources: list[str]
    chinese_purchase_keywords: list[str]
    upgrade_suggestions: list[str]
    logistics_risks: list[str]
    compliance_risks: list[str]
    china_element_strength: str
    wait_tolerance: str
    local_scarcity: str
    opportunity_score: float
    score_breakdown: dict[str, float]
    recommended_action: str
    evidence_signals: list[NormalizedDemandSignal]


class ProductResearchTask(TypedDict, total=False):
    task_id: str
    status: str
    search_mode: str
    created_at: str
    completed_at: str
    request: ProductResearchSearchRequest
    items: list[ProductResearchCandidate]
    signals: list[NormalizedDemandSignal]
    source_status: list[ProductResearchSourceStatus]


__all__ = [
    "NormalizedDemandSignal",
    "ProductResearchCandidate",
    "ProductResearchConfig",
    "ProductResearchDataSource",
    "ProductResearchMetrics",
    "ProductResearchPrice",
    "ProductResearchSearchRequest",
    "ProductResearchSourceStatus",
    "ProductResearchTargetMarket",
    "ProductResearchTask",
    "SearchMode",
    "SourceType",
]
