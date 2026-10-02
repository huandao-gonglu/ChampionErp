"""锁外交付完成后，短锁内检查源与选择并合并交付字段。"""
from __future__ import annotations

from typing import Any

from erp_web.context import get_context
from erp_web.schemas.image_hosting import DELIVERY_FIELDS, ImageHostingError
from erp_web.services.image_hosting_config import default_profile, target_fingerprint
from erp_web.services.image_content import image_content, read_image_file
from .draft_publish_context import load_required_draft_publish_context
from .image_pool import save_image_pool_for_product


def persist_delivered_image_pool(prepared: dict[str, Any], original: dict[str, Any], platform: str) -> dict[str, Any]:
    context = get_context()
    product_id = str(prepared.get("product_id") or "")
    pool = prepared.get("source", {}).get("image_pool", [])
    if not product_id or not pool:
        return prepared
    fields = (*DELIVERY_FIELDS, "delivery_error")
    original_pool = {item["id"]: item for item in original.get("source", {}).get("image_pool", [])}
    selected = context.image_delivery._target_asset_ids(original, list(original_pool.values()), platform)
    updates = {item["id"]: item for item in pool if item["id"] in selected and item["id"] in original_pool
               and any(item.get(field, "") != original_pool[item["id"]].get(field, "") for field in fields)}
    # 同时保护配置检查与交付写回；网络早已完成，配置锁内没有请求。
    with context.products.mutation_scope({"product_id": product_id}):
        with context.config.image_hosting_commit_scope():
            managed = [item for item in pool if item["id"] in selected and item.get("delivery_provider") == "s3_compatible"]
            if managed:
                profile = default_profile(context.config.load_app_config())
                if any(item.get("hosting_profile_id") != profile["id"] or item.get("delivery_fingerprint") != target_fingerprint(profile) for item in managed):
                    raise ImageHostingError("IMAGE_DELIVERY_STALE", "准备期间默认托管目标已变化")
            current = context.products.load_product_from_index(product_id, "")
            if not current:
                raise ImageHostingError("IMAGE_DELIVERY_STALE", "准备期间商品已删除")
            draft = original.get("drafts", {}).get(platform, {})
            if draft.get("draft_id"):
                loaded, error, _ = load_required_draft_publish_context({"draft_id": draft["draft_id"], "platform": platform, "site": draft.get("site", "")}, context=context)
                if error:
                    raise ImageHostingError("IMAGE_DELIVERY_STALE", "准备期间发布草稿已变化")
                active_draft = loaded["product"].get("drafts", {}).get(platform, {})
                if any(active_draft.get(key) != draft.get(key) for key in ("images", "sku_items", "category_id")):
                    raise ImageHostingError("IMAGE_DELIVERY_STALE", "准备期间图片选择或类目已变化")
            current_pool = current.get("source", {}).get("image_pool", [])
            current_by_id = {item["id"]: item for item in current_pool}
            if selected - current_by_id.keys():
                raise ImageHostingError("IMAGE_DELIVERY_STALE", "准备期间图片已删除")
            for asset_id in selected:
                before = original_pool.get(asset_id)
                item = current_by_id[asset_id]
                if before is None or any(item.get(key, "") != before.get(key, "") for key in ("path", "preview_url", "selected", "platforms")):
                    raise ImageHostingError("IMAGE_DELIVERY_STALE", "准备期间源图片或图片选择已变化")
                delivered = next(row for row in pool if row["id"] == asset_id)
                if any(item.get(key, "") not in (before.get(key, ""), delivered.get(key, "")) for key in fields):
                    raise ImageHostingError("IMAGE_DELIVERY_STALE", "准备期间交付信息已变化")
                path = context.image_delivery._source_path(item)
                if delivered.get("content_sha256") and path and image_content(read_image_file(path))[0] != delivered["content_sha256"]:
                    raise ImageHostingError("IMAGE_DELIVERY_STALE", "准备期间图片内容已变化")
            for asset_id, update in updates.items():
                current_by_id[asset_id].update({field: update.get(field, "") for field in fields})
            saved = save_image_pool_for_product(product_id, current_pool) if updates else {"ok": True, "product": current}
    if not saved.get("ok"):
        raise ImageHostingError("IMAGE_DELIVERY_STALE", "交付字段写回失败")
    prepared["source"] = {**prepared.get("source", {}), "image_pool": saved["product"]["source"]["image_pool"]}
    return prepared


__all__ = ["persist_delivered_image_pool"]
