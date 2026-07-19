# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from erp_web.runtime_units.publishing_bus_core import PublishingBus

from .product_store import normalize_product_fields
from .runtime_api import publish_product
from .runtime_common import PUBLISHING_JOB_DIR

class ProjectPublishingAdapter:
    def resolve_category(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        product = normalize_product_fields(product)
        local_categories = product.get("local_platform_categories") if isinstance(product.get("local_platform_categories"), dict) else {}
        platform_category = local_categories.get(platform) if isinstance(local_categories, dict) else None
        if isinstance(platform_category, dict):
            category_id = str(platform_category.get("category_id") or platform_category.get("platform_category_id") or "").strip()
        else:
            category_id = str(platform_category or "").strip()
        if not category_id:
            if platform == "mercadolibre":
                category_id = str(product.get("category_id") or config.get("mercadolibre", {}).get("category_id") or "").strip()
            elif platform == "yandex":
                category_id = str(product.get("yandex_category_id") or config.get("yandex", {}).get("category_id") or "").strip()
            elif platform == "ozon":
                category_id = str(product.get("ozon_category_id") or config.get("ozon", {}).get("category_id") or "").strip()
        if category_id:
            if platform == "mercadolibre":
                product["category_id"] = category_id
            elif platform == "yandex":
                product["yandex_category_id"] = category_id
            elif platform == "ozon":
                product["ozon_category_id"] = category_id
        return product

    def validate_required_attributes(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> list[str]:
        return []

    def publish(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        return publish_product(product, platform, config)


PUBLISHING_BUS = PublishingBus(
    PUBLISHING_JOB_DIR,
    adapters={
        "mercadolibre": ProjectPublishingAdapter(),
        "yandex": ProjectPublishingAdapter(),
        "ozon": ProjectPublishingAdapter(),
    },
)


__all__ = [
    "PUBLISHING_BUS",
    "ProjectPublishingAdapter",
]
