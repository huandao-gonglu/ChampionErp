from __future__ import annotations

from typing import Any, Literal, TypedDict


SourceType = Literal["api", "ai_search", "crawler", "third_party_api", "manual_import"]
SearchMode = Literal["target_only", "target_plus_reference", "global_scan"]


class ProductResearchPrice(TypedDict, total=False):
    amount: float
    currency: str


class SorftimeQuotaReceipt(TypedDict):
    endpoint: str
    domain: int
    request_consumed: float | None
    request_left: float | None


class ProductResearchSupplier(TypedDict):
    product_id: str
    title: str
    source_url: str
    image_url: str
    price_cny: float | None
    store_name: str
    service_score: float | None
    monthly_sales: float | None
    min_order_quantity: float | None
    repurchase_rate: float | None
    collected_at: str


class ProductResearchSourcingQuery(TypedDict):
    mode: Literal["keyword", "image"]
    keyword: str


class ProductResearchSourcing(TypedDict, total=False):
    query: ProductResearchSourcingQuery
    status: str
    items: list[ProductResearchSupplier]
    quota_receipts: list[SorftimeQuotaReceipt]


class ProductResearchSupplierSearchResponse(TypedDict):
    ok: bool
    sourcing: ProductResearchSourcing
    cached: bool


class ProductResearchSupplierImportResponse(TypedDict):
    ok: bool
    product_id: str
    already_imported: bool
    quota_receipts: list[SorftimeQuotaReceipt]


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
    rating: float | None
    review_count: int | None
    hot_score: float
    source_name: str
    collected_at: str
    asin: str
    monthly_sales: float | None
    data_updated_at: str
    sourcing: ProductResearchSourcing
    imported_product_id: str
    import_quota_receipts: list[SorftimeQuotaReceipt]


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


class ProductResearchMarketSearchMethodBinding(TypedDict, total=False):
    method_id: str
    enabled: bool
    prompt: str
    config_json: dict[str, Any]


class ProductResearchTargetMarket(TypedDict, total=False):
    id: str
    platform: str
    site: str
    display_name: str
    search_methods: list[ProductResearchMarketSearchMethodBinding]


class ProductResearchConfig(TypedDict, total=False):
    search_defaults: dict[str, Any]
    provider_runtime: dict[str, Any]
    search_providers: list[ProductResearchDataSource]
    target_markets: list[ProductResearchTargetMarket]
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
    raw_items_found: int
    items_filtered: int
    diagnostic_message: str
    quota_receipts: list[SorftimeQuotaReceipt]
    ai_model_id: str
    api_style: str
    stream_enabled: bool
    stream_fallback_used: bool


class ProductResearchRun(TypedDict, total=False):
    run_id: str
    status: str
    search_mode: str
    created_at: str
    completed_at: str
    description: str
    progress_description: str
    request: ProductResearchSearchRequest
    items: list[HotProductCandidate]
    source_status: list[ProductResearchSourceStatus]


__all__ = [
    "SorftimeQuotaReceipt",
    "ProductResearchSupplier",
    "ProductResearchSourcingQuery",
    "ProductResearchSourcing",
    "ProductResearchSupplierSearchResponse",
    "ProductResearchSupplierImportResponse",
    "HotProductCandidate",
    "ProductResearchConfig",
    "ProductResearchDataSource",
    "ProductResearchMarketSearchMethodBinding",
    "ProductResearchPrice",
    "ProductResearchRun",
    "ProductResearchSearchRequest",
    "ProductResearchSourceStatus",
    "ProductResearchTargetMarket",
    "SearchMode",
    "SourceType",
]
