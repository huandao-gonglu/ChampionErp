"""Yandex 平台响应到在线快照的纯投影，不发起网络或持久化操作。"""
from __future__ import annotations

from typing import Any

from erp_web.marketplaces.online_buyer_links import yandex_buyer_links
from erp_web.marketplaces.yandex_currency import yandex_internal_currency
from erp_web.schemas.online_products import Capability, MarketSnapshot, OnlineListing, PriceScope, StockScope, listing_identity


def available_stock(rows: list[dict[str, Any]]) -> int | None:
    quantities = {r.get("type"): r.get("count") for r in rows}
    if type(quantities.get("AVAILABLE")) is int:
        return quantities["AVAILABLE"]
    if type(quantities.get("FIT")) is int and type(quantities.get("FREEZE")) is int:
        return max(0, quantities["FIT"] - quantities["FREEZE"])
    return None


def build_stocks(mode, warehouses, stock_results, remote_id):
    stocks = []
    if mode == "business":
        for warehouse in warehouses:
            wid = str(warehouse.get("id") or "")
            response = stock_results[wid]
            if str(response.get("partnerWarehouseId")) != wid or not isinstance(response.get("offers"), list):
                raise ValueError("Yandex 库存范围响应不匹配")
            stock_offer = next((r for r in response["offers"] if r.get("offerId") == remote_id), {})
            quantity = available_stock(stock_offer.get("stocks", []))
            writable = any(m.get("apiAvailability") == "AVAILABLE" and m.get("placementType") != "FBY" for m in warehouse.get("models", []))
            stocks.append(StockScope(id=wid, label=str(warehouse.get("name") or f"卖家仓 {wid}"), warehouse_id=wid, quantity=quantity, writable=writable and quantity is not None, reason="" if writable else "仓库当前不允许写入"))
    elif mode == "campaign_warehouses":
        response = stock_results["campaign"]
        warehouses = response.get("warehouses")
        if not isinstance(warehouses, list):
            raise ValueError("Yandex 仓库组库存响应无效")
        quantities = [available_stock(o.get("stocks", [])) for w in warehouses for o in w.get("offers", []) if o.get("offerId") == remote_id]
        stocks.append(StockScope(id="campaign", label="当前店铺仓库组（共享）", quantity=quantities[0] if len(quantities)==1 else None,
            writable=len(quantities)==1, reason="多仓返回无法当作一个库存数写回" if len(quantities)!=1 else ""))
    return stocks


def build_listing(mapping, campaign, hidden, card, default_price, stocks, *, account_id, campaign_id, settings):
    offer = mapping["offer"]
    remote_id = offer["offerId"]
    prices = []
    for key, name, raw, kind, enabled in (
        ("business", "账号基础价（全部店铺）", default_price, "base_price", True),
        ("campaign", "当前店铺独立价", campaign.get("campaignPrice") or {}, "sale_price", settings.get("onlyDefaultPrice") is False),
    ):
        prices.append(PriceScope(id=key, label=name, amount=str(raw["value"]) if raw.get("value") is not None else None,
            currency=yandex_internal_currency(str(raw.get("currencyId") or settings.get("currency") or "")), kind=kind,
            writable=enabled and not offer.get("archived") and bool(raw.get("currencyId") or settings.get("currency")),
            reason="店铺仅使用账号基础价" if not enabled else ""))
    raw_status = str(campaign.get("status") or ("ARCHIVED" if offer.get("archived") else offer.get("cardStatus") or ""))
    attrs = [{"id": str(a["parameterId"]), **a} for a in card.get("parameterValues", []) if a.get("parameterId")]
    content = {"title": str(offer.get("name") or ""), "description": str(offer.get("description") or ""), "pictures": offer.get("pictures") or [], "attributes": attrs}
    return OnlineListing(id=listing_identity("yandex", account_id, remote_id), platform="yandex", account_id=account_id,
        remote_id=remote_id, model="business_offer", title=content["title"], seller_sku=remote_id, thumbnail=next(iter(content["pictures"]), ""),
        buyer_links=yandex_buyer_links(mapping),
        sale_state="paused" if hidden else ("active" if raw_status == "PUBLISHED" else "unknown"), raw_status=raw_status, raw_sub_status=[str(card.get("cardStatus") or "")], prices=prices, stocks=stocks,
        markets=[MarketSnapshot(id=campaign_id, site_id=campaign_id, seller_id=account_id.split(":", 1)[0], raw_status=raw_status)], content=content,
        snapshot={"mapping": mapping.get("mapping", {}), "offer": offer, "default_price": default_price, "campaign": campaign, "card": card, "hidden": hidden},
        capabilities={"price": Capability(enabled=any(p.writable for p in prices), scope="所选账号 / 店铺"),
            "stock": Capability(enabled=any(s.writable for s in stocks), scope="所选卖家仓 / 仓库组"),
            "content": Capability(enabled=not offer.get("archived"), scope="账号商品内容（全部店铺）", fields=["title","description","pictures"]+(["attributes"] if attrs else [])),
            "sale_state": Capability(enabled=bool(campaign) and not offer.get("archived"), scope="当前店铺", reason="隐藏 / 恢复仅影响当前店铺，恢复不绕过平台审核")})


def catalog_listing(mapping, *, account_id, campaign_id, settings):
    """目录记录可展示，详情未齐时不开放任何修改能力。"""
    offer = mapping["offer"]
    campaign = next((row for row in offer.get("campaigns", []) if str(row.get("campaignId")) == campaign_id), {})
    listing = build_listing(mapping, campaign, False, {}, {}, [], account_id=account_id,
                            campaign_id=campaign_id, settings=settings)
    listing.details_state = "pending"
    listing.capabilities = {name: Capability(reason="商品详情同步中，完成后可修改")
                            for name in ("price", "stock", "content", "sale_state")}
    return listing
