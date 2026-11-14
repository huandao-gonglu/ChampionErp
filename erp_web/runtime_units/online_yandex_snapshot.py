"""Yandex 平台响应到在线快照的纯投影，不发起网络或持久化操作。"""
from __future__ import annotations

from typing import Any

from erp_web.marketplaces.online_buyer_links import yandex_buyer_links
from erp_web.marketplaces.yandex_currency import yandex_internal_currency
from erp_web.product_model.online_listing_summary import summarize_yandex_status
from erp_web.schemas.online_products import Capability, MarketSnapshot, OnlineListing, PlatformIssue, PriceScope, StockScope, listing_identity


def card_issues(card, *, source="card"):
    """保留平台错误和警告的原文及说明，不把警告推断成商品不可售。"""
    return [PlatformIssue(severity=severity, source=source, code=str(row.get("code") or ""),
                          message=str(row.get("message") or row.get("comment") or "平台未提供反馈说明"),
                          comment=str(row.get("comment") or ""))
            for field, severity in (("errors", "error"), ("warnings", "warning"))
            for row in card.get(field, []) or [] if isinstance(row, dict)]


def card_attributes(card, names=None):
    """只补充展示名称，保留参数、枚举和单位身份。"""
    names = names or {}
    return [{"id": str(row["parameterId"]), **row, "name": names.get(str(row["parameterId"]), "")}
            for row in card.get("parameterValues", []) if row.get("parameterId")]


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
            response = stock_results.get(wid)
            if response is not None and (str(response.get("partnerWarehouseId")) != wid or not isinstance(response.get("offers"), list)):
                raise ValueError("Yandex 库存范围响应不匹配")
            stock_offer = next((r for r in (response or {}).get("offers", []) if r.get("offerId") == remote_id), {})
            quantity = available_stock(stock_offer.get("stocks", []))
            writable = any(m.get("apiAvailability") == "AVAILABLE" and m.get("placementType") != "FBY" for m in warehouse.get("models", []))
            # 设置绝对库存无需已有数量；平台未返回数量时保留未知值，不影响仓库写入权限。
            campaign_ids = {str(m['campaignId']) for m in warehouse.get('models', []) if m.get('campaignId') and m.get('placementType') == 'FBS'}
            stocks.append(StockScope(id=wid, label=str(warehouse.get("name") or f"卖家仓 {wid}"), warehouse_id=wid,
                campaign_id=next(iter(campaign_ids)) if len(campaign_ids) == 1 else '',
                quantity=quantity, writable=writable, reason="" if writable else "仓库当前不允许写入"))
    elif mode in ("campaign_warehouses", "platform_warehouses"):
        response = stock_results.get("campaign")
        warehouses = response.get("warehouses") if response is not None else []
        if not isinstance(warehouses, list):
            raise ValueError("Yandex 仓库组库存响应无效")
        quantities = []
        matched = 0
        for warehouse in warehouses:
            offer = next((row for row in warehouse["offers"] if row["offerId"] == remote_id), {})
            if offer:
                matched += 1
            quantities.append(available_stock(offer.get("stocks", [])))
        quantity = sum(quantities) if quantities and all(q is not None for q in quantities) else None
        writable = mode == "campaign_warehouses" and len(warehouses) == matched == 1
        label = "平台仓库合计" if mode == "platform_warehouses" else "当前店铺仓库组（共享）"
        stocks.append(StockScope(id="campaign", label=label, quantity=quantity, writable=writable,
            reason="" if writable else "库存由平台管理或无法定位单个可写仓库"))
    return stocks


def build_listing(mapping, campaign, hidden, card, default_price, stocks, *, account_id, campaign_id, settings,
                  attribute_names=None, attribute_names_error=""):
    offer = mapping["offer"]
    remote_id = offer["offerId"]
    prices = []
    for key, name, raw, kind, enabled in (
        ("business", "账号基础价（全部店铺）", default_price, "base_price", True),
        ("campaign", "当前店铺独立价", campaign.get("campaignPrice") or {}, "sale_price", settings.get("onlyDefaultPrice") is False),
    ):
        prices.append(PriceScope(id=key, label=name, amount=str(raw["value"]) if raw.get("value") is not None else None,
            currency=yandex_internal_currency(str(raw.get("currencyId") or settings.get("currency") or "")), kind=kind,
            writable=enabled and bool(raw.get("currencyId") or settings.get("currency")),
            reason="店铺仅使用账号基础价" if not enabled else ""))
    raw_status = str(campaign.get("status") or ("ARCHIVED" if offer.get("archived") else offer.get("cardStatus") or ""))
    attrs = card_attributes(card, attribute_names)
    content = {"title": str(offer.get("name") or ""), "description": str(offer.get("description") or ""), "pictures": offer.get("pictures") or [], "attributes": attrs,
               "attribute_names_error": attribute_names_error, "guarantee_period": offer.get("guaranteePeriod") or {}}
    return OnlineListing(id=listing_identity("yandex", account_id, remote_id), platform="yandex", account_id=account_id,
        remote_id=remote_id, model="business_offer", title=content["title"], seller_sku=remote_id, thumbnail=next(iter(content["pictures"]), ""),
        buyer_links=yandex_buyer_links(mapping),
        sale_state="paused" if hidden else ("active" if raw_status == "PUBLISHED" and not offer.get("archived") else "unknown"), raw_status=raw_status, raw_sub_status=[str(card.get("cardStatus") or "")], platform_issues=card_issues(card)+card_issues(campaign, source="campaign"), prices=prices, stocks=stocks,
        markets=[MarketSnapshot(id=campaign_id, site_id=campaign_id, seller_id=account_id.split(":", 1)[0], raw_status=raw_status)], content=content,
        snapshot={"mapping": mapping.get("mapping", {}), "offer": offer, "default_price": default_price, "campaign": campaign, "card": card, "hidden": hidden},
        capabilities={"price": Capability(enabled=any(p.writable for p in prices), scope="所选账号 / 店铺"),
            "stock": Capability(enabled=any(s.writable for s in stocks), scope="所选卖家仓 / 仓库组"),
            "content": Capability(enabled=True, scope="账号商品内容（全部店铺）", fields=["title","description","pictures","guarantee_period"]+(["attributes"] if attrs else [])),
            "sale_state": Capability(enabled=bool(campaign), scope="当前店铺", reason="隐藏 / 恢复仅影响当前店铺；归档商品需先恢复归档，恢复销售不绕过平台审核")})


def with_warehouses(listing, warehouses, campaigns, hidden, *, settings=None):
    """各仓库的库存与其实际店铺状态分别保存，不更改刊登持久身份。"""
    catalog = []
    markets = []
    for warehouse in warehouses:
        ids = {str(model['campaignId']) for model in warehouse.get('models', [])
               if model.get('placementType') == 'FBS' and model.get('campaignId')}
        if len(ids) != 1:
            continue
        campaign_id = ids.pop()
        row = campaigns.get(campaign_id, {})
        status = str(row.get('status') or '')
        paused = listing.remote_id in hidden.get(campaign_id, set())
        sale_state = 'paused' if paused else 'active' if status == 'PUBLISHED' and not listing.archived else 'unknown'
        catalog.append({'id': str(warehouse['id']), 'name': str(warehouse.get('name') or warehouse['id']), 'campaign_id': campaign_id})
        if not any(m.id == campaign_id for m in markets):
            markets.append(MarketSnapshot(id=campaign_id, site_id=campaign_id, seller_id=listing.account_id.split(':')[0],
                name=str(warehouse.get('name') or campaign_id), raw_status=status, raw_sub_status=['hidden'] if paused else []))
        stock = next((stock for stock in listing.stocks if stock.id == str(warehouse['id'])), None)
        if stock:
            stock.campaign_id, stock.raw_status, stock.sale_state = campaign_id, status, sale_state
    if catalog:
        listing.markets = markets
        listing.snapshot.update(warehouse_catalog=catalog, campaigns=campaigns,
                                hidden_campaigns={key: listing.remote_id in ids for key, ids in hidden.items()})
        listing.platform_issues = card_issues(listing.snapshot.get('card', {})) + [issue
            for row in campaigns.values() for issue in card_issues(row, source='campaign')]
        listing.capabilities['sale_state'].scope = '所选仓库对应店铺（scope_id=campaign:<店铺 ID>）'
        listing.capabilities['sale_state'].enabled = any(campaigns.values())
        if listing.prices and settings is not None:
            listing.prices = [price for price in listing.prices if price.id == 'business']
            for market in markets:
                raw = campaigns.get(market.id, {}).get('campaignPrice') or {}
                currency = yandex_internal_currency(str(raw.get('currencyId') or settings.get('currency') or ''))
                enabled = settings.get('onlyDefaultPrice') is False
                listing.prices.append(PriceScope(id='campaign:' + market.id, label=market.name + ' 独立价',
                    amount=str(raw['value']) if raw.get('value') is not None else None, currency=currency,
                    writable=enabled and bool(currency), reason='' if enabled else '店铺仅使用账号基础价'))
                market.price, market.currency = listing.prices[-1].amount, currency
        summarize_yandex_status(listing)
    return listing


def catalog_listing(mapping, *, account_id, campaign_id, settings=None):
    """目录记录可展示，详情未齐时不开放任何修改能力。"""
    offer = mapping["offer"]
    campaign = next((row for row in offer.get("campaigns", []) if str(row.get("campaignId")) == campaign_id), {})
    listing = build_listing(mapping, campaign, False, {}, {}, [], account_id=account_id,
                            campaign_id=campaign_id, settings=settings or {})
    listing.details_state = "pending"
    listing.capabilities = {name: Capability(reason="商品详情同步中，完成后可修改")
                            for name in ("price", "stock", "content", "sale_state", "archive")}
    return listing
