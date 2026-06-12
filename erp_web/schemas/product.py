from __future__ import annotations

from typing import Any, TypedDict

from .image import ImageItem


class ProductSource(TypedDict, total=False):
    source_platform: str
    source_url: str
    title: str
    price: str
    currency: str
    description: str
    bullets: list[str]
    attributes: dict[str, Any]
    images: list[str]
    image_pool: list[ImageItem]
    created_at: str


class PlatformDraft(TypedDict, total=False):
    platform: str
    site: str
    status: str
    title: str
    description: str
    category_id: str
    category_path: str
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
