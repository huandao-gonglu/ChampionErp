from __future__ import annotations

"""GET 路由可见的显式应用查询边界。"""

from typing import Any

from erp_web.context import get_context
from erp_web.marketplace_registry import marketplace_options
from erp_web.runtime_units.store_credentials import exchange_mercadolibre_code_from_body
from erp_web.runtime_units.image_pool import (
    current_generated_images,
    current_image_pool,
    current_source_images,
)
from erp_web.runtime_units.mercadolibre_orders import (
    load_mercadolibre_order_notifications,
    mercadolibre_recent_orders,
)
from erp_web.runtime_units.publish_adapter import get_publishing_bus
from erp_web.runtime_units.publish_bus import (
    load_publish_logs,
    persist_publish_bus_terminal_results,
)
from erp_web.runtime_units.publish_mercadolibre import mercadolibre_remote_items
from erp_web.runtime_units.runtime_api import html_page
from erp_web.runtime_units.source_collect_browser import browser_debug_status
from erp_web.stores.config_store import summarize_store_auth_states
from erp_web.stores.product_store import mask_secret


def load_app_config() -> dict[str, Any]:
    return get_context().config.load_app_config()


def load_store_config() -> dict[str, Any]:
    return get_context().config.load_store_config()


def mercadolibre_auth_checklist(
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return get_context().config.mercadolibre_auth_checklist(config)


def load_product() -> dict[str, Any]:
    return get_context().products.load_product()


def load_products_index() -> list[dict[str, Any]]:
    return get_context().products.load_products_index()


def load_drafts_index(
    scope: str = "active",
) -> list[dict[str, Any]]:
    return get_context().products.load_drafts_index(scope)


__all__ = [
    "browser_debug_status",
    "current_generated_images",
    "current_image_pool",
    "current_source_images",
    "exchange_mercadolibre_code_from_body",
    "get_publishing_bus",
    "html_page",
    "load_app_config",
    "load_drafts_index",
    "load_mercadolibre_order_notifications",
    "load_product",
    "load_products_index",
    "load_publish_logs",
    "load_store_config",
    "marketplace_options",
    "mask_secret",
    "mercadolibre_auth_checklist",
    "mercadolibre_recent_orders",
    "mercadolibre_remote_items",
    "persist_publish_bus_terminal_results",
    "summarize_store_auth_states",
]
