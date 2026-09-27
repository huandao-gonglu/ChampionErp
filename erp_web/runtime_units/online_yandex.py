"""Yandex 在线目录、店铺价格、库存及隐藏状态适配。"""
from __future__ import annotations

from copy import deepcopy
from functools import cached_property
from typing import Any

from erp_web.marketplaces import yandex_http as api
from erp_web.marketplaces.yandex_currency import yandex_wire_currency
from erp_web.schemas.online_products import OnlineListing
from erp_web.runtime_units.online_yandex_read import catalog_pages, hidden_ids, read_batch, sync_yandex
from erp_web.runtime_units.online_product_status import yandex_status


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
        self.mode = str(self.config.get("stock_update_mode") or "none")

    @cached_property
    def settings(self):
        return api.fetch_yandex_business_settings(self.token, self.business)

    @cached_property
    def warehouses(self):
        return api.fetch_yandex_partner_warehouses(self.token, self.business) if self.mode == "business" else []

    def request(self, path: str, body=None, *, query=None, method="POST"):
        return api.request_yandex_json(method, path, self.token, body, query=query)

    def sync(self, ids=None):
        return sync_yandex(self, ids)

    def read_status(self, listing):
        return yandex_status(self, listing)

    def read(self, remote_id: str) -> OnlineListing:
        # 修改前与回读确认始终读取当下事实，不复用同步期间的缓存。
        mappings = list(catalog_pages(self, [remote_id]))
        if len(mappings) != 1 or len(mappings[0]) != 1:
            raise ValueError("Yandex 目录商品身份无法确认")
        result = read_batch(self, mappings[0], hidden_ids(self, remote_id))
        if result.errors:
            raise ValueError(result.errors.get(remote_id) or "Yandex 商品读取不完整")
        return result.listings[0]

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
