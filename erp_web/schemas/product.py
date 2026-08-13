from __future__ import annotations

from typing import Any, TypedDict

from .image import DraftImageRef, ImageItem


PRODUCT_SCHEMA_VERSION = 2


class ProductSource(TypedDict, total=False):
    source_platform: str
    source_url: str
    title: str
    price: str
    currency: str
    description: str
    bullets: list[str]
    material: str
    package_contents: list[str]
    variants: list[dict[str, Any]]
    skus: list[dict[str, Any]]
    attributes: dict[str, Any]
    attribute_matches: dict[str, Any]
    dimensions: dict[str, str]
    weight_kg: str
    images: list[str]
    image_pool: list[ImageItem]
    collect_status: str
    collect_logs: list[Any]
    collect_diagnostics: dict[str, Any]
    brand: str
    model: str
    sku: str
    created_at: str


class CategoryAttributeDefinition(TypedDict, total=False):
    id: str
    name: str
    required: bool
    options: list[str]
    value_type: str
    unit: str
    description: str
    dictionary_id: str
    is_dictionary: bool
    is_collection: bool
    max_value_count: int
    category_dependent: bool
    raw: dict[str, Any]


class CategoryAttributeSchema(TypedDict, total=False):
    version: int
    platform: str
    site: str
    category_id: str
    category_path: str
    source: str
    fetched_at: str
    required: list[CategoryAttributeDefinition]
    optional: list[CategoryAttributeDefinition]


class DraftTargetSite(TypedDict, total=False):
    platform: str
    site: str
    language: str
    market_currency: str
    listing_currency: str
    currency_resolution: dict[str, Any]
    category_id: str
    description_category_id: str
    category_path: str
    category_attribute_schema: CategoryAttributeSchema
    attributes: dict[str, Any]
    validation_errors: list[Any]
    category_precheck: dict[str, Any]
    publish_status: str
    status: str
    last_precheck: dict[str, Any]
    last_precheck_target: dict[str, Any]
    last_publish_task: dict[str, Any]


class PlatformDraft(TypedDict, total=False):
    draft_id: str
    product_id: str
    source_product_id: str
    platform: str
    platforms: list[str]
    enabled: bool
    site: str
    country: str
    status: str
    publish_status: str
    title: str
    description: str
    brand: str
    model: str
    category_id: str
    description_category_id: str
    category_path: str
    category_attribute_schema: CategoryAttributeSchema
    target_sites: list[DraftTargetSite]
    attributes: dict[str, Any]
    pricing: dict[str, Any]
    stock: str
    sku: str
    upc: str
    bullets: list[str]
    search_terms: list[str]
    language: str
    package_dimensions: dict[str, str]
    validation_errors: list[Any]
    images: list[DraftImageRef]
    sale_terms: list[dict[str, Any]]
    allow_gtin_exemption: bool
    shipping: dict[str, Any]
    category_precheck: dict[str, Any]
    last_precheck: dict[str, Any]
    last_precheck_target: dict[str, Any]
    last_publish_task: dict[str, Any]
    ai_copy_ready: bool
    copy_generated_at: str
    copy_source: str
    copy_operation_key: str
    created_at: str
    updated_at: str


class Product(TypedDict, total=False):
    schema_version: int
    product_id: str
    name: str
    brand: str
    model: str
    category: str
    target_customer: str
    sku: str
    stock: str
    upc: str
    cost: str
    materials: list[str]
    selling_points: list[str]
    package_includes: list[str]
    colors: list[str]
    avoid_claims: list[str]
    description: str
    dimensions: str
    weight_kg: str
    source: ProductSource
    drafts: dict[str, PlatformDraft]
    marketplace_terms: dict[str, Any]
    attributes: dict[str, Any]
    listing_overrides: dict[str, Any]
    copy_results: dict[str, Any]
    sku_items: list[dict[str, Any]]
    selected_sku_indices: list[int]
    pricing_defaults: dict[str, Any]
    publish_preview: dict[str, Any]
    collect_status: str
    collect_logs: list[Any]
    local_platform_categories: dict[str, Any]
    workflow_statuses: dict[str, str]
    created_at: str
    updated_at: str


__all__ = [
    "PRODUCT_SCHEMA_VERSION",
    "CategoryAttributeDefinition",
    "CategoryAttributeSchema",
    "DraftTargetSite",
    "PlatformDraft",
    "Product",
    "ProductSource",
]
