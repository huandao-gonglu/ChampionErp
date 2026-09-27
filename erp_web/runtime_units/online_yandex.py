"""Yandex 在线目录、店铺价格、库存及隐藏状态适配。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterator
from urllib.parse import quote

from erp_web.marketplaces import yandex_http as api
from erp_web.marketplaces.online_buyer_links import yandex_buyer_links
from erp_web.marketplaces.yandex_currency import yandex_internal_currency, yandex_wire_currency
from erp_web.schemas.online_products import Capability, MarketSnapshot, OnlineListing, PriceScope, StockScope, listing_identity


def available_stock(rows: list[dict[str, Any]]) -> int | None:
    quantities = {r.get("type"): r.get("count") for r in rows}
    if type(quantities.get("AVAILABLE")) is int:
        return quantities["AVAILABLE"]
    if type(quantities.get("FIT")) is int and type(quantities.get("FREEZE")) is int:
        return max(0, quantities["FIT"] - quantities["FREEZE"])
    return None


class YandexOnlineAdapter:
    platform = "yandex"

    def __init__(self, config: dict[str, Any]):
        self.config = config[self.platform]
        self.token = str(self.config.get("api_token") or "")
        self.business = str(self.config.get("business_id") or "")
        self.campaign = str(self.config.get("campaign_id") or "")
        if not self.token or not self.business or not self.campaign:
            raise ValueError("请先验证 Yandex 店铺授权")
        campaign = api.fetch_yandex_campaign(self.token, self.campaign)
        if str(campaign.get("business", {}).get("id")) != self.business:
            raise ValueError("Yandex 店铺与账号绑定不一致")
        self.account_id = self.business+":"+self.campaign
        self.settings = api.fetch_yandex_business_settings(self.token, self.business)
        self.mode = str(self.config.get("stock_update_mode") or "none")
        self.warehouses = api.fetch_yandex_partner_warehouses(self.token, self.business) if self.mode == "business" else []

    def request(self, path: str, body=None, *, query=None, method="POST"):
        return api.request_yandex_json(method, path, self.token, body, query=query)

    def discover(self) -> Iterator[str]:
        seen: set[str] = set()
        for archived in (False, True):
            token = ""
            tokens = set()
            while True:
                response = self.request(f"/v2/businesses/{self.business}/offer-mappings", {"archived": archived}, query={"limit": 100, **({"pageToken": token} if token else {})})
                result = response.get("result", {})
                if not isinstance(result.get("offerMappings"), list):
                    raise ValueError("Yandex 商品目录响应缺少 offerMappings")
                for row in result["offerMappings"]:
                    remote_id = str(row.get("offer", {}).get("offerId") or "")
                    if not remote_id:
                        raise ValueError("Yandex 商品缺少 offerId")
                    if remote_id not in seen:
                        seen.add(remote_id)
                        yield remote_id
                token = str(result.get("paging", {}).get("nextPageToken") or "")
                if not token:
                    break
                if token in tokens:
                    raise ValueError("Yandex 分页游标重复，已停止同步")
                tokens.add(token)

    def read(self, remote_id: str) -> OnlineListing:
        rows = api.fetch_yandex_offer_mapping(self.token, self.business, [remote_id])
        if len(rows) != 1 or rows[0].get("offer", {}).get("offerId") != remote_id:
            raise ValueError("Yandex 目录商品身份无法确认")
        offer = rows[0]["offer"]
        campaign_rows = api.fetch_yandex_campaign_offer(self.token, self.campaign, offer_ids=[remote_id])
        campaign = next((r for r in campaign_rows if r.get("offerId") == remote_id), {})
        hidden_result = self.request(f"/v2/campaigns/{self.campaign}/hidden-offers", method="GET", query={"offer_id": remote_id}).get("result", {})
        if not isinstance(hidden_result.get("hiddenOffers"), list):
            raise ValueError("Yandex 隐藏状态读取不完整")
        hidden = any(r.get("offerId") == remote_id for r in hidden_result["hiddenOffers"])
        cards_result = self.request(f"/v2/businesses/{self.business}/offer-cards", {"offerIds": [remote_id]}).get("result", {})
        card = next((r for r in cards_result.get("offerCards", []) if r.get("offerId") == remote_id), {})
        default_result = self.request(f"/v2/businesses/{self.business}/offer-prices", {"offerIds": [remote_id]}).get("result", {})
        if not isinstance(default_result.get("offers"), list):
            raise ValueError("Yandex 账号价格读取不完整")
        default_price = next((r.get("price", {}) for r in default_result["offers"] if r.get("offerId") == remote_id), {})
        prices = []
        for key, name, raw, kind, enabled in (
            ("business", "账号基础价（全部店铺）", default_price, "base_price", True),
            ("campaign", "当前店铺独立价", campaign.get("campaignPrice") or {}, "sale_price", self.settings.get("onlyDefaultPrice") is False),
        ):
            prices.append(PriceScope(id=key, label=name, amount=str(raw["value"]) if raw.get("value") is not None else None,
                currency=yandex_internal_currency(str(raw.get("currencyId") or self.settings.get("currency") or "")), kind=kind,
                writable=enabled and not offer.get("archived") and bool(raw.get("currencyId") or self.settings.get("currency")),
                reason="店铺仅使用账号基础价" if not enabled else ""))
        stocks = []
        if self.mode == "business":
            for warehouse in self.warehouses:
                wid = str(warehouse.get("id") or "")
                response = self.request(f"/v3/businesses/{self.business}/offers/stocks", {"partnerWarehouseId": int(wid), "offerIds": [remote_id]}).get("result", {})
                if str(response.get("partnerWarehouseId")) != wid or not isinstance(response.get("offers"), list):
                    raise ValueError("Yandex 库存范围响应不匹配")
                stock_offer = next((r for r in response["offers"] if r.get("offerId") == remote_id), {})
                quantity = available_stock(stock_offer.get("stocks", []))
                writable = any(m.get("apiAvailability") == "AVAILABLE" and m.get("placementType") != "FBY" for m in warehouse.get("models", []))
                stocks.append(StockScope(id=wid, label=str(warehouse.get("name") or f"卖家仓 {wid}"), warehouse_id=wid, quantity=quantity, writable=writable and quantity is not None, reason="" if writable else "仓库当前不允许写入"))
        elif self.mode == "campaign_warehouses":
            response = self.request(f"/v2/campaigns/{self.campaign}/offers/stocks", {"offerIds": [remote_id]}).get("result", {})
            warehouses = response.get("warehouses")
            if not isinstance(warehouses, list):
                raise ValueError("Yandex 仓库组库存响应无效")
            quantities = [available_stock(o.get("stocks", [])) for w in warehouses for o in w.get("offers", []) if o.get("offerId") == remote_id]
            stocks.append(StockScope(id="campaign", label="当前店铺仓库组（共享）", quantity=quantities[0] if len(quantities)==1 else None,
                writable=len(quantities)==1, reason="多仓返回无法当作一个库存数写回" if len(quantities)!=1 else ""))
        raw_status = str(campaign.get("status") or ("ARCHIVED" if offer.get("archived") else offer.get("cardStatus") or ""))
        attrs = [{"id": str(a["parameterId"]), **a} for a in card.get("parameterValues", []) if a.get("parameterId")]
        content = {"title": str(offer.get("name") or ""), "description": str(offer.get("description") or ""), "pictures": offer.get("pictures") or [], "attributes": attrs}
        return OnlineListing(id=listing_identity(self.platform, self.account_id, remote_id), platform=self.platform, account_id=self.account_id,
            remote_id=remote_id, model="business_offer", title=content["title"], seller_sku=remote_id, thumbnail=next(iter(content["pictures"]), ""),
            buyer_links=yandex_buyer_links(rows[0]),
            sale_state="paused" if hidden else ("active" if raw_status == "PUBLISHED" else "unknown"), raw_status=raw_status, raw_sub_status=[str(card.get("cardStatus") or "")], prices=prices, stocks=stocks,
            markets=[MarketSnapshot(id=self.campaign, site_id=self.campaign, seller_id=self.business, raw_status=raw_status)], content=content,
            snapshot={"mapping": rows[0].get("mapping", {}), "offer": offer, "default_price": default_price, "campaign": campaign, "card": card, "hidden": hidden},
            capabilities={"price": Capability(enabled=any(p.writable for p in prices), scope="所选账号 / 店铺"),
                "stock": Capability(enabled=any(s.writable for s in stocks), scope="所选卖家仓 / 仓库组"),
                "content": Capability(enabled=not offer.get("archived"), scope="账号商品内容（全部店铺）", fields=["title","description","pictures"]+(["attributes"] if attrs else [])),
                "sale_state": Capability(enabled=bool(campaign) and not offer.get("archived"), scope="当前店铺", reason="隐藏 / 恢复仅影响当前店铺，恢复不绕过平台审核")})

    def write(self, listing: OnlineListing, operation: str, scope: str, changes: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._write(listing, operation, scope, changes)
        except api.YandexApiError as exc:
            # 唯一 HTTP 客户端将 200 + ERROR/FAILED 转为类型化业务拒绝。
            if exc.http_status == 200:
                exc.details["definitively_rejected"] = True
            raise

    def _write(self, listing: OnlineListing, operation: str, scope: str, changes: dict[str, Any]) -> dict[str, Any]:
        if operation == "price":
            raw = listing.snapshot["default_price"] if scope == "business" else listing.snapshot["campaign"].get("campaignPrice", {})
            price = {k: raw[k] for k in ("discountBase", "vat", "minimumForBestseller") if k in raw and (scope == "campaign" or k != "vat")}
            price.update(value=float(changes["amount"]), currencyId=yandex_wire_currency(changes["currency"]))
            return api.update_yandex_price(self.token, business_id=self.business if scope=="business" else "", campaign_id=self.campaign if scope=="campaign" else "", offers=[{"offerId":listing.remote_id,"price":price}])
        if operation == "stock":
            return api.update_yandex_stock(self.token, mode=self.mode, business_id=self.business, campaign_id=self.campaign,
                warehouse_ids=[scope] if self.mode=="business" else None, offer_id=listing.remote_id, count=changes["quantity"])
        if operation == "sale_state":
            suffix = "" if changes["state"]=="paused" else "/delete"
            return self.request(f"/v2/campaigns/{self.campaign}/hidden-offers{suffix}", {"hiddenOffers":[{"offerId":listing.remote_id}]})
        payload = deepcopy(changes)
        if "title" in payload:
            payload["name"] = payload.pop("title")
        if "attributes" in payload:
            payload["parameterValues"] = [{k:v for k,v in r.items() if k != "id"} for r in payload.pop("attributes")]
            payload["marketCategoryId"] = listing.snapshot["mapping"]["marketCategoryId"]
        return api.update_yandex_offer_mapping(self.token, self.business, {"offerId":listing.remote_id, **payload})


    def confirmation_details(self, listing, operation, receipt, scope=""):
        if operation == "price":
            rows = api.fetch_yandex_price_quarantine(self.token, business_id=self.business if scope == "business" else "",
                campaign_id=self.campaign if scope == "campaign" else "", offer_ids=[listing.remote_id])
            return {"pending": bool(rows), "errors": [], "quarantine": rows,
                    "note": "价格进入平台隔离区，尚未确认生效；请先在平台处理价格审核" if rows else ""}
        if operation != "content":
            return {"pending": False, "errors": []}
        card = listing.snapshot.get("card", {})
        status = card.get("cardStatus")
        errors = card.get("errors") or []
        if status in ("HAS_CARD_CAN_UPDATE_ERRORS", "NO_CARD_ERRORS") and not errors:
            errors = [{"message": "平台未接受卡片修改", "status": status}]
        return {"pending": status not in ("HAS_CARD_CAN_UPDATE", "HAS_CARD_CAN_NOT_UPDATE", "HAS_CARD_CAN_UPDATE_ERRORS", "NO_CARD_ERRORS"),
                "errors": errors, "card_status": status, "warnings": card.get("warnings") or []}
