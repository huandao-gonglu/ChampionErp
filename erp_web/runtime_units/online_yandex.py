"""Yandex 在线目录、店铺价格、库存、停售及账号归档适配。"""
from __future__ import annotations

from copy import deepcopy
from functools import cached_property
from typing import Any

from erp_web.product_model.yandex_warranty_model import compile_yandex_guarantee_period

from erp_web.runtime_units.online_change_confirmation import yandex_change

from erp_web.marketplaces import yandex_http as api
from erp_web.marketplaces.yandex_currency import yandex_wire_currency
from erp_web.schemas.online_products import OnlineListing
from erp_web.runtime_units.online_yandex_read import catalog_pages, hidden_ids, read_batch, sync_yandex
from erp_web.runtime_units.online_product_status import yandex_status
from erp_web.runtime_units.category_catalog import get_category_catalog
from erp_web.product_model.online_attribute_model import merge_yandex_attributes, yandex_category_id
from erp_web.product_model.online_scope_model import yandex_campaign_id


class YandexOnlineAdapter:
    platform = "yandex"
    batch_limits = {"sale_state": 500, "archive": 200}

    def __init__(self, config: dict[str, Any]):
        self.config = config[self.platform]
        self.token = str(self.config.get("api_token") or "")
        self.business = str(self.config.get("business_id") or "")
        self.campaign = str(self.config.get("campaign_id") or "")
        if not self.token or not self.business or not self.campaign:
            raise ValueError("请先验证 Yandex 店铺授权")
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

    def attribute_names(self, category_id: str):
        """一次适配器运行中按类目共用名称查询；定义仍由 Catalog 缓存管理。"""
        if not hasattr(self, "_attribute_names"):
            self._attribute_names = {}
        if category_id not in self._attribute_names:
            try:
                definition = get_category_catalog().attribute_definitions("yandex", category_id, site="global")
                self._attribute_names[category_id] = ({a.id: a.name for a in (*definition.required, *definition.optional)}, "")
            except Exception as exc:
                # 名称查询失败不丢失已读到的属性值和卡片反馈，也不伪装成名称已加载。
                self._attribute_names[category_id] = ({}, f"读取属性名称失败：{exc}")
        return self._attribute_names[category_id]

    def sync(self, ids=None, *, catalog="current"):
        return sync_yandex(self, ids, catalog=catalog)

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

    def read_confirmation(self, listing, request):
        return yandex_change(self, listing, request)

    def read_many(self, remote_ids):
        """目标目录和详情按平台批量接口校验，不逐个 SKU 重复下载。"""
        from erp_web.schemas.online_products import OnlineSyncBatch
        result = OnlineSyncBatch("details")
        for batch in sync_yandex(self, remote_ids, catalog=None, full=True):
            if batch.phase == "details":
                result.listings.extend(batch.listings)
                result.errors.update(batch.errors)
        return result

    def write_batch(self, listings, operation, scope, changes):
        ids = [row.remote_id for row in listings]
        if operation not in self.batch_limits or not 1 <= len(ids) <= self.batch_limits[operation] or len(set(ids)) != len(ids):
            raise ValueError("Yandex 批量操作或数量无效")
        try:
            if operation == "sale_state":
                campaigns = {yandex_campaign_id(row, scope) for row in listings}
                if len(campaigns) != 1:
                    raise ValueError('批量停售只能操作同一个仓库店铺')
                campaign = campaigns.pop()
                suffix = "" if changes["state"] == "paused" else "/delete"
                receipt = self.request(f"/v2/campaigns/{campaign}/hidden-offers{suffix}", {"hiddenOffers": [{"offerId": sku} for sku in ids]})
                return {sku: deepcopy(receipt) for sku in ids}
            action = "archive" if changes["archived"] else "unarchive"
            receipt = self.request(f"/v2/businesses/{self.business}/offer-mappings/{action}", {"offerIds": ids})
            if receipt.get("status") != "OK":
                return {sku: deepcopy(receipt) for sku in ids}
            result = receipt.get("result", {})
            if not isinstance(result, dict):
                raise ValueError("Yandex 归档回执结果格式无效，结果需核对")
            key = "notArchivedOffers" if changes["archived"] else "notUnarchivedOfferIds"
            rejected = result.get(key)
            # 官方允许失败清单为 null；其他非列表值不能当作全部成功。
            if rejected is None:
                rejected = []
            if not isinstance(rejected, list):
                raise ValueError("Yandex 归档失败清单格式无效，结果需核对")
            failures = {}
            for row in rejected:
                sku = row.get("offerId") if changes["archived"] and isinstance(row, dict) else row if not changes["archived"] else None
                if not isinstance(sku, str) or sku not in ids or sku in failures:
                    raise ValueError("Yandex 归档回执商品身份不一致，结果需核对")
                failures[sku] = row
            return {sku: {**receipt, "result": {key: [failures[sku]] if sku in failures else []}} for sku in ids}
        except api.YandexApiError as exc:
            if exc.http_status == 200:
                exc.details["definitively_rejected"] = True
            raise

    def write(self, listing: OnlineListing, operation: str, scope: str, changes: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._write(listing, operation, scope, changes)
        except api.YandexApiError as exc:
            # 唯一 HTTP 客户端将 200 + ERROR/FAILED 转为类型化业务拒绝。
            if exc.http_status == 200:
                exc.details["definitively_rejected"] = True
            raise

    def _write(self, listing: OnlineListing, operation: str, scope: str, changes: dict[str, Any]) -> dict[str, Any]:
        if operation in self.batch_limits:
            return self.write_batch([listing], operation, scope, changes)[listing.remote_id]
        if operation == "price":
            campaign = yandex_campaign_id(listing, scope) if scope != 'business' else ''
            raw = (listing.snapshot["default_price"] if scope == "business" else
                   listing.snapshot.get('campaigns', {}).get(campaign, listing.snapshot.get('campaign', {})).get('campaignPrice', {}))
            price = {k: raw[k] for k in ("discountBase", "vat", "minimumForBestseller") if k in raw and (scope != "business" or k != "vat")}
            price.update(value=float(changes["amount"]), currencyId=yandex_wire_currency(changes["currency"]))
            return api.update_yandex_price(self.token, business_id=self.business if scope=="business" else "", campaign_id=campaign, offers=[{"offerId":listing.remote_id,"price":price}])
        if operation == "stock":
            return api.update_yandex_stock(self.token, mode=self.mode, business_id=self.business, campaign_id=self.campaign,
                warehouse_ids=[scope] if self.mode=="business" else None, offer_id=listing.remote_id, count=changes["quantity"])
        payload = deepcopy(changes)
        if "guarantee_period" in payload:
            payload["guaranteePeriod"] = compile_yandex_guarantee_period(payload.pop("guarantee_period"))
            if payload["guaranteePeriod"] is None:
                raise ValueError("请填写要提交的 Yandex 保修期限")
        if "title" in payload:
            payload["name"] = payload.pop("title")
        if "attributes" in payload:
            payload["parameterValues"] = merge_yandex_attributes(listing.content.get("attributes", []), payload.pop("attributes"))
            payload["marketCategoryId"] = int(yandex_category_id(listing))
        return api.update_yandex_offer_mapping(self.token, self.business, {"offerId":listing.remote_id, **payload})


    def confirmation_details(self, listing, operation, receipt, scope=""):
        if operation == "price":
            rows = api.fetch_yandex_price_quarantine(self.token, business_id=self.business if scope == "business" else "",
                campaign_id=yandex_campaign_id(listing, scope) if scope != "business" else "", offer_ids=[listing.remote_id])
            return {"pending": False, "needs_attention": bool(rows), "errors": [], "quarantine": rows,
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
