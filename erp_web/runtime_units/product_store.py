# -*- coding: utf-8 -*-
from __future__ import annotations

"""Thin delegation layer over the context-owned stores.

Business logic lives in ``erp_web.stores.product_store`` (ProductStore) and
``erp_web.stores.config_store`` (ConfigStore); this module only keeps the
historical function names alive for the many existing call sites. Every
function is a one-line delegate to ``get_context().products`` /
``get_context().config`` — no business logic, no direct IO here.
"""

from typing import Any

from erp_web.context import get_context
from erp_web.db import product_identity
from erp_web.product_model.common import normalize_list
from erp_web.stores.config_store import (
    _store_auth_result_fields,
    auth_next_action,
    explain_mercadolibre_auth_error,
    store_auth_failure_code,
    summarize_store_auth,
    summarize_store_auth_states,
)
from erp_web.stores.product_store import (
    mask_secret,
    normalize_product_fields,
    normalize_sku_items,
    normalize_space,
    product_id_from_body,
)


# -- products / drafts (ProductStore) ----------------------------------------

def load_product() -> dict[str, Any]:
    return get_context().products.load_product()


def save_product(data: dict[str, Any]) -> dict[str, Any]:
    return get_context().products.save_product(data)


def save_product_profile(data: dict[str, Any]) -> dict[str, Any]:
    return get_context().products.save_product_profile(data)


def draft_workflow_status(product: dict[str, Any], platform: str = "mercadolibre") -> str:
    return get_context().products.draft_workflow_status(product, platform)


def publish_queue_platforms(product: dict[str, Any], requested_platforms: list[str] | None = None) -> list[str]:
    return get_context().products.publish_queue_platforms(product, requested_platforms)


def sync_product_workflow_statuses(product: dict[str, Any]) -> dict[str, Any]:
    return get_context().products.sync_product_workflow_statuses(product)


def product_index_status(product: dict[str, Any], platform: str = "mercadolibre") -> dict[str, Any]:
    return get_context().products.product_index_status(product, platform)


def sanitize_products_index(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return get_context().products.sanitize_products_index(items)


def load_products_index() -> list[dict[str, Any]]:
    return get_context().products.load_products_index()


def load_drafts_index(scope: str = "active") -> list[dict[str, Any]]:
    return get_context().products.load_drafts_index(scope)


def delete_products_from_index(product_ids: list[Any]) -> dict[str, Any]:
    return get_context().products.delete_products_from_index(product_ids)


def delete_draft_from_index(draft_id: Any) -> dict[str, Any]:
    return get_context().products.delete_draft_from_index(draft_id)


def load_product_from_index(product_id: str = "", file_path: str = "") -> dict[str, Any]:
    return get_context().products.load_product_from_index(product_id, file_path)


def load_required_product_from_body(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    return get_context().products.load_required_product_from_body(body)


def load_draft_from_index(draft_id: str) -> dict[str, Any]:
    return get_context().products.load_draft_from_index(draft_id)


def draft_product_context(product: dict[str, Any]) -> dict[str, Any]:
    return get_context().products.draft_product_context(product)


def load_draft_detail_from_index(draft_id: str) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    return get_context().products.load_draft_detail_from_index(draft_id)


def save_draft_detail(draft_payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    return get_context().products.save_draft_detail(draft_payload)


def apply_image_assets_to_draft(
    draft_id: str,
    created_items: list[dict[str, Any]],
    strategy: str = "append",
) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    return get_context().products.apply_image_assets_to_draft(draft_id, created_items, strategy)


def save_draft_copy_result(product: dict[str, Any], target_market: str, copy: dict[str, Any]) -> dict[str, Any]:
    return get_context().products.save_draft_copy_result(product, target_market, copy)


# -- app/store config (ConfigStore) -------------------------------------------

def load_app_config() -> dict[str, Any]:
    return get_context().config.load_app_config()


def save_app_config(config: dict[str, Any]) -> None:
    return get_context().config.save_app_config(config)


def default_app_config() -> dict[str, Any]:
    return get_context().config.default_app_config()


def normalize_app_config(config: dict[str, Any]) -> dict[str, Any]:
    return get_context().config.normalize_app_config(config)


def merge_app_config_fields(current: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any]:
    return get_context().config.merge_app_config_fields(current, incoming)


def load_store_config() -> dict[str, Any]:
    return get_context().config.load_store_config()


def save_store_config(config: dict[str, Any], *, preserve_empty_sensitive: bool = True) -> None:
    return get_context().config.save_store_config(config, preserve_empty_sensitive=preserve_empty_sensitive)


def default_store_config() -> dict[str, Any]:
    return get_context().config.default_store_config()


def merge_store_config_fields(
    base: dict[str, Any] | None,
    updates: dict[str, Any] | None,
    *,
    preserve_empty_sensitive: bool = True,
) -> dict[str, Any]:
    return get_context().config.merge_store_config_fields(base, updates, preserve_empty_sensitive=preserve_empty_sensitive)


def normalize_store_config(config: dict[str, Any] | None) -> dict[str, Any]:
    return get_context().config.normalize_store_config(config)


def update_store_config_fields(platform: str, fields: dict[str, Any], *, preserve_empty_sensitive: bool = True) -> dict[str, Any]:
    return get_context().config.update_store_config_fields(platform, fields, preserve_empty_sensitive=preserve_empty_sensitive)


def mercadolibre_auth_checklist(config: dict[str, Any] | None = None) -> dict[str, Any]:
    return get_context().config.mercadolibre_auth_checklist(config)


__all__ = [
    "apply_image_assets_to_draft",
    "auth_next_action",
    "delete_draft_from_index",
    "delete_products_from_index",
    "draft_product_context",
    "draft_workflow_status",
    "explain_mercadolibre_auth_error",
    "load_app_config",
    "load_draft_detail_from_index",
    "load_draft_from_index",
    "load_drafts_index",
    "load_product",
    "load_product_from_index",
    "load_products_index",
    "load_required_product_from_body",
    "load_store_config",
    "mask_secret",
    "mercadolibre_auth_checklist",
    "merge_app_config_fields",
    "merge_store_config_fields",
    "normalize_app_config",
    "normalize_list",
    "normalize_product_fields",
    "normalize_sku_items",
    "normalize_space",
    "normalize_store_config",
    "product_id_from_body",
    "product_identity",
    "publish_queue_platforms",
    "save_app_config",
    "save_draft_copy_result",
    "save_draft_detail",
    "save_product",
    "save_product_profile",
    "save_store_config",
    "store_auth_failure_code",
    "summarize_store_auth",
    "summarize_store_auth_states",
    "sync_product_workflow_statuses",
    "update_store_config_fields",
]
