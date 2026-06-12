from __future__ import annotations

from typing import Any, TypedDict

from .product import Product


class PublishPlatformState(TypedDict, total=False):
    platform: str
    status: str
    stage: str
    error: str
    result: dict[str, Any] | None
    attempts: int
    created_at: str
    updated_at: str
    category_id: str


class PublishJob(TypedDict, total=False):
    job_id: str
    status: str
    product_name: str
    product: Product
    config: dict[str, Any]
    platforms: dict[str, PublishPlatformState]
    created_at: str
    updated_at: str
