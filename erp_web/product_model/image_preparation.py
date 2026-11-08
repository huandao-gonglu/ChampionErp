"""图片内容决策与发布资源交付分离；只根据当前引用判断内容状态。"""

import hashlib
import json


def image_bindings(product, draft, *, selected_only=True):
    defaults = {row["id"]: row.get("image_asset_id", "") for row in product.get("sku_items", [])}
    sku_bindings = []
    for row in draft.get("sku_items", []):
        if selected_only and not row.get("selected"):
            continue
        override = row.get("overrides", {}).get("image_asset_id")
        sku_bindings.append((row["sku_id"], defaults.get(row["sku_id"], "") if override is None else override))
    return {
        "common": sorted({ref["asset_id"] for ref in draft.get("images", [])}),
        "skus": sorted(sku_bindings),
    }


def image_content_fingerprint(product, draft):
    value = {**image_bindings(product, draft), "targets": [
        [target.get("platform"), target.get("site"), target.get("language")]
        for target in draft.get("target_sites", [])
    ], "manual_revision": draft.get("images_manually_modified_at", "")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def image_preparation_status(product, draft):
    fingerprint = image_content_fingerprint(product, draft)
    saved = draft.get("image_preparation") or {}
    bindings = image_bindings(product, draft)
    ids = set(bindings["common"]) | {asset for _, asset in bindings["skus"] if asset}
    pool = {item["id"]: item for item in product.get("source", {}).get("image_pool", [])}
    # 老数据没有操作者证据。只保护当前已加工素材，不伪称它一定由用户修改。
    processed = any(pool.get(id, {}).get("origin") in {"ai_translated", "ai_generated", "local_upload", "chatgpt_import"}
                    or pool.get(id, {}).get("derived_from_id") for id in ids)
    manual = bool(draft.get("images_manually_modified_at")) or any(
        pool.get(id, {}).get("raw", {}).get("image_content_actor") == "user" for id in ids)
    if saved.get("fingerprint") == fingerprint:
        return {"status": saved["mode"], "fingerprint": fingerprint, "manually_modified": manual}
    return {"status": "needs_confirmation" if manual or processed else "unprocessed",
            "fingerprint": fingerprint, "manually_modified": manual}
