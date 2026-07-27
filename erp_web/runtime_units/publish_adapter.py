# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from typing import Any

from erp_web import marketplaces as marketplace_api
from erp_web.context import AppContext, get_context
from erp_web.marketplace_registry import CAP_PUBLISH, category_id_field, platform_has_capability, platform_label
from erp_web.marketplaces.publisher import PlatformPublisher
from erp_web.runtime_units.publishing_bus_core import PublishingBus

from .product_store import normalize_product_fields
from .publish_helpers import (
    _required_attribute_summary,
    build_mercadolibre_publish_payload,
    validate_mercadolibre_publish_payload,
)
from .publish_mercadolibre import map_mercadolibre_publish_error
from .publish_validation import validate_mercadolibre_draft

logger = logging.getLogger(__name__)


class MercadoLibrePublishingAdapter:
    """Mercado Libre 的完整发布适配器；也是当前唯一可入队的平台。"""

    platform = "mercadolibre"

    def resolve_category(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        product = normalize_product_fields(product)
        local_categories = product.get("local_platform_categories") if isinstance(product.get("local_platform_categories"), dict) else {}
        platform_category = local_categories.get(self.platform) if isinstance(local_categories, dict) else None
        if isinstance(platform_category, dict):
            category_id = str(platform_category.get("category_id") or platform_category.get("platform_category_id") or "").strip()
        else:
            category_id = str(platform_category or "").strip()
        if not category_id:
            field = category_id_field(self.platform)
            category_id = str(
                product.get(field)
                or config.get(self.platform, {}).get("category_id")
                or ""
            ).strip()
        if category_id:
            product[category_id_field(self.platform)] = category_id
        return product

    def required_attributes_missing(self, product: dict[str, Any], config: dict[str, Any]) -> list[str]:
        return list(_required_attribute_summary(product, self.platform).get("missing") or [])

    def validate_draft(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return validate_mercadolibre_draft(product, config)

    def build_payload(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return build_mercadolibre_publish_payload(product, config)

    def validate_payload(self, payload: Any, config: dict[str, Any]) -> list[str]:
        return validate_mercadolibre_publish_payload(payload, config)

    def publish_payload(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        token = str((config.get(self.platform) or {}).get("access_token") or "")
        return marketplace_api.publish_mercadolibre(payload, token)

    def map_publish_error(self, error: Exception) -> dict[str, Any]:
        return map_mercadolibre_publish_error(marketplace_api.parse_mercadolibre_error(error))

    def publish(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        from .runtime_api import publish_product

        return publish_product(product, platform, config)


_PUBLISHERS: dict[str, PlatformPublisher] = {
    MercadoLibrePublishingAdapter.platform: MercadoLibrePublishingAdapter(),
}


def publishing_adapter_for(platform: str) -> PlatformPublisher | None:
    key = str(platform or "").strip().lower()
    if not platform_has_capability(key, CAP_PUBLISH):
        return None
    return _PUBLISHERS.get(key)


def require_publishing_adapter(platform: str) -> PlatformPublisher:
    adapter = publishing_adapter_for(platform)
    if adapter is None:
        raise RuntimeError(f"{platform_label(platform)}发布未接入")
    return adapter


def unsupported_publish_response(platform: str) -> dict[str, Any]:
    key = str(platform or "").strip().lower()
    return {
        "ok": False,
        "supported": False,
        "platform": key,
        "status": "unsupported",
        "error": f"{platform_label(key)}发布未接入",
    }


def build_publishing_bus(context: AppContext) -> PublishingBus:
    """为一个 AppContext 构造发布总线；测试上下文与生产上下文互不串状态。"""

    return PublishingBus(
        context.db,
        adapters=dict(_PUBLISHERS),
        config_provider=context.config.load_store_config,
        auto_resume_pending=False,
    )


def get_publishing_bus() -> PublishingBus:
    return get_context().publishing_bus


def resume_pending_publish_jobs() -> None:
    """Explicitly recover publish jobs left queued/running by a previous run."""
    try:
        get_publishing_bus().recover_pending_jobs()
    except Exception:
        logger.exception("Failed to resume pending publish jobs")


__all__ = [
    "MercadoLibrePublishingAdapter",
    "build_publishing_bus",
    "get_publishing_bus",
    "publishing_adapter_for",
    "require_publishing_adapter",
    "resume_pending_publish_jobs",
    "unsupported_publish_response",
]
