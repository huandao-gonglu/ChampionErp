from __future__ import annotations

from typing import Any, TypedDict

from .image import DraftImageRef, ImageItem


class ProductSource(TypedDict, total=False):
    source_platform: str
    source_url: str
    title: str
    price: str
    currency: str
    description: str
    bullets: list[str]
    attributes: dict[str, Any]
    attribute_matches: dict[str, Any]
    dimensions: dict[str, str]
    images: list[DraftImageRef]
    image_pool: list[ImageItem]
    created_at: str


class CategoryAttributeDefinition(TypedDict, total=False):
    id: str
    name: str
    required: bool
    options: list[str]
    value_type: str
    unit: str
    description: str


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
    currency: str
    category_id: str
    category_path: str
    category_attribute_schema: CategoryAttributeSchema
    attributes: dict[str, Any]
    validation_errors: list[Any]


class PlatformDraft(TypedDict, total=False):
    draft_id: str
    platform: str
    site: str
    status: str
    title: str
    description: str
    category_id: str
    category_path: str
    target_sites: list[DraftTargetSite]
    attributes: dict[str, Any]
    price: dict[str, Any]
    validation_errors: list[Any]
    images: list[str]


class Product(TypedDict, total=False):
    product_id: str
    id: str
    name: str
    title: str
    brand: str
    model: str
    source_platform: str
    source_url: str
    source: ProductSource
    drafts: dict[str, PlatformDraft]
    local_platform_categories: dict[str, Any]
    workflow_statuses: dict[str, str]
    image_pool: list[ImageItem]
    source_images: list[str]
    generated_images: list[str]
    pricing: dict[str, Any]
    created_at: str
    updated_at: str
