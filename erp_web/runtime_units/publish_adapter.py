# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from typing import Any

from erp_web import marketplaces as marketplace_api
from erp_web.context import AppContext, get_context
from erp_web.marketplace_registry import CAP_PUBLISH, platform_has_capability, platform_label
from erp_web.marketplaces.publisher import PlatformPublisher
from erp_web.product_model import default_draft
from erp_web.runtime_units.publishing_bus_core import PublishingBus
from erp_web.stores.product_store import normalize_product_fields

from .publish_helpers import (
    _required_attribute_summary,
    build_mercadolibre_publish_payload,
    validate_mercadolibre_publish_payload,
)
from .publish_mercadolibre import map_mercadolibre_publish_error
from .publish_ozon import (
    build_ozon_publish_payload,
    map_ozon_publish_error,
    ozon_category_pair,
    ozon_required_attributes_missing,
    publish_ozon_payload,
    validate_ozon_publish_payload,
)
from .publish_validation import validate_mercadolibre_draft
from .publish_validation import validate_ozon_draft

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
            drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
            draft = drafts.get(self.platform) if isinstance(drafts.get(self.platform), dict) else {}
            category_id = str(
                draft.get("category_id")
                or config.get(self.platform, {}).get("category_id")
                or ""
            ).strip()
        if category_id:
            drafts = product.setdefault("drafts", {})
            draft = drafts.setdefault(
                self.platform,
                default_draft(self.platform),
            )
            draft["category_id"] = category_id
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


class OzonPublishingAdapter:
    """通过 Ozon Seller API 创建或更新商品，并确认异步导入终态。"""

    platform = "ozon"

    def resolve_category(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        product = normalize_product_fields(product)
        type_id, _ = ozon_category_pair(product)
        if type_id:
            drafts = product.setdefault("drafts", {})
            draft = drafts.setdefault(self.platform, default_draft(self.platform))
            draft["category_id"] = type_id
        return product

    def required_attributes_missing(self, product: dict[str, Any], config: dict[str, Any]) -> list[str]:
        return ozon_required_attributes_missing(product)

    def validate_draft(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return validate_ozon_draft(product, config)

    def build_payload(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return build_ozon_publish_payload(product, config)

    def validate_payload(self, payload: Any, config: dict[str, Any]) -> list[str]:
        return validate_ozon_publish_payload(payload, config)

    def publish_payload(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        store = config.get(self.platform) if isinstance(config.get(self.platform), dict) else {}
        return publish_ozon_payload(
            payload,
            str(store.get("client_id") or "").strip(),
            str(store.get("api_key") or "").strip(),
            timeout_seconds=float(store.get("publish_timeout_seconds") or 30),
            poll_interval_seconds=float(store.get("publish_poll_interval_seconds") or 0.5),
        )

    def map_publish_error(self, error: Exception) -> dict[str, Any]:
        return map_ozon_publish_error(error)

    def publish(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        from .runtime_api import publish_product

        return publish_product(product, platform, config)


_PUBLISHERS: dict[str, PlatformPublisher] = {
    MercadoLibrePublishingAdapter.platform: MercadoLibrePublishingAdapter(),
    OzonPublishingAdapter.platform: OzonPublishingAdapter(),
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

    from .publish_bus import persist_publish_bus_terminal_results

    return PublishingBus(
        context.db,
        adapters=dict(_PUBLISHERS),
        config_provider=context.config.load_store_config,
        terminal_callback=lambda state: (
            persist_publish_bus_terminal_results(
                state,
                context=context,
            )
        ),
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
    "OzonPublishingAdapter",
    "build_publishing_bus",
    "get_publishing_bus",
    "publishing_adapter_for",
    "require_publishing_adapter",
    "resume_pending_publish_jobs",
    "unsupported_publish_response",
]
