"""Ozon 详情和价格的纯投影，读取不开放尚未核验的写入能力。"""
from erp_web.schemas.online_products import Capability, OnlineListing, PriceScope, StockScope, listing_identity

CONTRACT_BLOCK = "Ozon 官方完整更新 Schema 尚未核验，暂不开放此写操作"


def build_listing(info, price_row, *, account_id):
    remote_id = str(info["id"])
    price = price_row.get("price", {})
    if not isinstance(price, dict):
        raise ValueError("Ozon 商品价格格式无效")
    images = info.get("images") or []
    title = str(info.get("name") or "")
    raw_status = "archived" if info.get("is_archived") else str(info.get("statuses", {}).get("status") or "unknown")
    return OnlineListing(id=listing_identity("ozon", account_id, remote_id), platform="ozon", account_id=account_id,
        remote_id=remote_id, model="ozon_product", seller_sku=str(info.get("offer_id") or ""), title=title, thumbnail=images[0] if images else "",
        raw_status=raw_status, content={"title": title, "pictures": images}, snapshot={"info": info, "prices": {"items": [price_row]}},
        prices=[PriceScope(id="product", label="商品售价", amount=str(price["price"]) if price.get("price") is not None else None,
            currency=str(price.get("currency_code") or ""), reason=CONTRACT_BLOCK)],
        stocks=[StockScope(id="unknown", label="卖家仓库存尚未取得", reason="逐仓库存契约尚未核验")],
        capabilities={name: Capability(reason=CONTRACT_BLOCK) for name in ("price", "stock", "content", "sale_state")})
