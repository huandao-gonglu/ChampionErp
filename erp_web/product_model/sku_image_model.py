"""SKU 图片资产引用、采集来源匹配与旧持久化地址迁移；不执行 I/O。"""

from __future__ import annotations

import hashlib
from typing import Any

from .common import text_or_empty
from .image_pool_model import normalize_image_pool, normalize_image_pool_item


def source_image_asset_id(pool: list[dict[str, Any]], reference: Any) -> str:
    """仅在采集和数据迁移边界将来源地址解析成稳定资产 ID。"""
    value = text_or_empty(reference)
    if not value:
        return ""
    for asset in pool:
        raw = asset.get("raw") if isinstance(asset.get("raw"), dict) else {}
        refs = {text_or_empty(asset.get(key)) for key in ("id", "url", "path", "preview_url")}
        refs.add(text_or_empty(raw.get("source_url")))
        if value in refs:
            return text_or_empty(asset.get("id"))
    return ""


def ensure_source_image_asset(pool: list[dict[str, Any]], reference: Any) -> str:
    value = text_or_empty(reference)
    existing = source_image_asset_id(pool, value)
    if existing or not value:
        return existing
    remote = value.startswith(("https://", "http://"))
    asset_id = "src_" + hashlib.sha256(value.encode()).hexdigest()[:24]
    pool.append(normalize_image_pool_item({
        "id": asset_id, "url" if remote else "path": value,
        "origin": "source", "usage": "other", "is_sku": True,
        "selected": False, "is_main": False, "order": len(pool),
        "status": "pending_download" if remote else "ready",
        "raw": {"source_url": value},
    }, len(pool)))
    return asset_id


def migrate_sku_image_addresses(product: dict[str, Any]) -> None:
    """读取已持久化的 image 地址；迁移后内部仅保留 image_asset_id。

    缺少的资产先保留来源与待下载状态，读取过程不发网络请求。
    来源 skus[].image 是采集原始事实，继续保留用于重新采集和修复。
    """
    source = product.get("source")
    if not isinstance(source, dict):
        source = product["source"] = {}
    pool = normalize_image_pool(source.get("image_pool") or source.get("images") or [])
    source["image_pool"] = pool

    def migrate(row: dict[str, Any]) -> None:
        if "image" not in row:
            return
        legacy = row.pop("image")
        if "image_asset_id" not in row:
            row["image_asset_id"] = ensure_source_image_asset(pool, legacy)

    for sku in product.get("sku_items") or []:
        if not isinstance(sku, dict):
            continue
        migrate(sku)
        if isinstance(sku.get("source_snapshot"), dict):
            migrate(sku["source_snapshot"])
    for draft in (product.get("drafts") or {}).values():
        if not isinstance(draft, dict):
            continue
        for row in draft.get("sku_items") or []:
            if isinstance(row.get("overrides"), dict):
                migrate(row["overrides"])


def sku_image_asset(product: dict[str, Any], fact: dict[str, Any]) -> dict[str, Any] | None:
    """发布只接受资产 ID，不在发布流程猜测 URL 或路径。"""
    asset_id = text_or_empty(fact.get("image_asset_id"))
    if not asset_id:
        return None
    pool = (product.get("source") or {}).get("image_pool", [])
    asset = next((item for item in pool if item.get("id") == asset_id), None)
    if asset is None:
        raise ValueError("SKU 关联图片已不在商品图片池，请在 SKU 页重新选图")
    return asset


def replace_draft_sku_images(product: dict[str, Any], draft: dict[str, Any], created: list[dict[str, Any]]) -> None:
    """显式替换所选原图时，只把当前草稿引用换成处理后的图片。"""
    replacements = {
        text_or_empty(item.get("source_asset_id") or item.get("derived_from_id")): text_or_empty(item.get("id"))
        for item in created
        if item.get("id") and (item.get("source_asset_id") or item.get("derived_from_id"))
    }
    defaults = {sku["id"]: sku.get("image_asset_id", "") for sku in product.get("sku_items", [])}
    for row in draft.get("sku_items", []):
        overrides = row.setdefault("overrides", {})
        current = overrides.get("image_asset_id", defaults.get(row["sku_id"], ""))
        if current in replacements:
            overrides["image_asset_id"] = replacements[current]


__all__ = ["ensure_source_image_asset", "migrate_sku_image_addresses", "replace_draft_sku_images", "sku_image_asset", "source_image_asset_id"]
