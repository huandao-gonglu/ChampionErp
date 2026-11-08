"""从现有草稿选取本地原图作为水印预览底图，不修改草稿或素材。"""
from collections.abc import Callable, Iterable
from pathlib import Path

from erp_web.product_model import draft_image_asset_ids, normalize_image_pool
from erp_web.product_model.sku_model import selected_skus
from erp_web.schemas.config import WatermarkPreview
from erp_web.services.image_content import image_content, read_image_file
from erp_web.services.watermark_service import preview_watermark, validate_watermark


def draft_watermark_preview(settings: dict, *, drafts: Iterable[dict],
                            load_product: Callable[[str], dict],
                            source_path: Callable[[dict], Path | None]) -> WatermarkPreview:
    settings = validate_watermark(settings)
    products = {}
    checked = set()
    for record in drafts:
        draft = record["raw"]
        product_id = str(record.get("product_id") or draft.get("source_product_id") or draft.get("product_id") or "")
        if product_id not in products:
            products[product_id] = load_product(product_id)
        product = products[product_id]
        pool = normalize_image_pool(product.get("source", {}).get("image_pool", []))
        ids = draft_image_asset_ids(draft.get("images"))
        try:
            sku_ids = [fact.get("image_asset_id") for fact, _ in selected_skus(product, draft)]
        except ValueError:
            sku_ids = []
        ids.extend(asset_id for asset_id in sku_ids if asset_id)
        by_id = {item["id"]: item for item in pool}
        for asset_id in ids:
            key = (product_id, asset_id)
            if key in checked or asset_id not in by_id:
                continue
            checked.add(key)
            path = source_path(by_id[asset_id])
            if path is None:
                continue
            try:
                data = read_image_file(path)
                image_content(data)
            except (ValueError, OSError):
                continue
            return {"preview_url": preview_watermark(settings, data),
                    "sample_name": str(product.get("source", {}).get("title") or product.get("name") or product_id or "草稿图片"), "message": ""}
    return {"preview_url": "", "sample_name": "", "message": "草稿中暂无可用的本地图片，请先为草稿添加图片。"}


__all__ = ["draft_watermark_preview"]
