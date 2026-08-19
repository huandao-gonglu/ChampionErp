# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from erp_web.context import get_context
from erp_web.product_model import (
    SOURCE_IMAGE_ORIGINS,
    default_source,
    image_pool_refs,
    normalize_image_pool,
    normalize_image_pool_item,
)
from erp_web.services import image_service
from erp_web.stores.product_store import normalize_product_fields

from .image_pool_core import (
    _display_image_ref,
    _pool_display_item,
    _source_only_pool_items,
    _source_pool_items,
    current_collect_debug_files,
    current_generated_images,
    current_image_pool,
    current_source_images,
    enrich_image_pool_item_dimensions,
    enrich_product_image_dimensions,
    image_files,
    image_items_from_paths,
    image_pool_refs_for_platform,
)

def sync_generated_images_into_pool(product: dict[str, Any]) -> dict[str, Any]:
    paths = get_context().paths
    normalized = normalize_product_fields(product)
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else default_source()
    pool = _source_pool_items(normalized)
    existing_keys = {
        str(item.get("path") or item.get("url") or item.get("preview_url") or item.get("id") or "").strip()
        for item in pool
        if str(item.get("path") or item.get("url") or item.get("preview_url") or item.get("id") or "").strip()
    }
    for file_item in image_files(paths.chatgpt_dir, recursive=True):
        key = str(file_item.get("path") or file_item.get("url") or "").strip()
        if not key or key in existing_keys:
            continue
        pool.append(
            enrich_image_pool_item_dimensions(normalize_image_pool_item(
                {
                    "id": f"gen_{len(pool) + 1}",
                    "path": str(file_item.get("path") or ""),
                    "url": str(file_item.get("url") or ""),
                    "preview_url": str(file_item.get("url") or file_item.get("path") or ""),
                    "origin": "ai_generated",
                    "usage": "scene",
                    "platforms": [],
                    "is_main": False,
                    "selected": False,
                    "order": len(pool),
                    "status": "ready",
                    "note": "generated file sync",
                },
                order=len(pool),
                origin_hint="ai_generated",
            ))
        )
        existing_keys.add(key)
    source["image_pool"] = normalize_image_pool(pool, "source")
    source["images"] = image_pool_refs(
        source["image_pool"],
        SOURCE_IMAGE_ORIGINS,
    )
    normalized["source"] = source
    return sync_draft_images_from_pool(normalized)


def append_images_to_product_pool(product: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else default_source()
    existing = _source_pool_items(normalized)
    existing_keys = {
        str(item.get("path") or item.get("url") or item.get("preview_url") or item.get("id") or "").strip()
        for item in existing
        if str(item.get("path") or item.get("url") or item.get("preview_url") or item.get("id") or "").strip()
    }
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("path") or item.get("url") or item.get("preview_url") or item.get("id") or "").strip()
        if not key or key in existing_keys:
            continue
        existing.append(enrich_image_pool_item_dimensions(normalize_image_pool_item(item, order=len(existing), origin_hint=str(item.get("origin") or "source"))))
        existing_keys.add(key)
    source["image_pool"] = normalize_image_pool(existing, "source")
    source["images"] = image_pool_refs(
        source["image_pool"],
        SOURCE_IMAGE_ORIGINS,
    )
    normalized["source"] = source
    return sync_draft_images_from_pool(normalized)


def sync_draft_images_from_pool(product: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    return get_context().products.sync_product_workflow_statuses(normalized)


def save_image_pool_for_product(product_id: str, image_pool: list[dict[str, Any]]) -> dict[str, Any]:
    product = get_context().products.load_product_from_index(product_id, "")
    if not product:
        return {"ok": False, "error": "商品不存在", "product_id": product_id}
    normalized = normalize_product_fields(product)
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else default_source()
    pool = normalize_image_pool(
        image_pool if isinstance(image_pool, list) else [],
        "source",
    )
    source["image_pool"] = [enrich_image_pool_item_dimensions(item) for item in pool]
    source["images"] = image_pool_refs(
        source["image_pool"],
        SOURCE_IMAGE_ORIGINS,
    )
    normalized["source"] = source
    saved = get_context().products.save_product(
        sync_draft_images_from_pool(normalized)
    )
    return {
        "ok": True,
        "product": saved,
        "imagePool": current_image_pool(saved),
        "productsIndex": get_context().products.load_products_index(),
    }


def apply_service_image_pool(product: dict[str, Any], image_pool: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = normalize_product_fields(product)
    source = normalized.get("source") if isinstance(normalized.get("source"), dict) else default_source()
    source["image_pool"] = image_service.normalize_pool(
        image_pool if isinstance(image_pool, list) else [],
        get_context().paths.app_dir,
    )
    source["images"] = image_pool_refs(
        source["image_pool"],
        SOURCE_IMAGE_ORIGINS,
    )
    normalized["source"] = source
    return sync_draft_images_from_pool(normalized)


__all__ = [
    "append_images_to_product_pool",
    "apply_service_image_pool",
    "current_collect_debug_files",
    "current_generated_images",
    "current_image_pool",
    "current_source_images",
    "enrich_image_pool_item_dimensions",
    "enrich_product_image_dimensions",
    "image_files",
    "image_items_from_paths",
    "image_pool_refs_for_platform",
    "save_image_pool_for_product",
    "sync_draft_images_from_pool",
    "sync_generated_images_into_pool",
]
