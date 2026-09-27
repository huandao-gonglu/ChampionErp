"""Ozon 只读同步边界；未经完整官方 Schema 核验的写操作不对用户开放。"""
from __future__ import annotations

from typing import Any, Iterator
from erp_web.marketplaces.config_http import request_ozon_json
from erp_web.schemas.online_products import Capability, OnlineListing, PriceScope, StockScope, listing_identity

API = "https://api-seller.ozon.ru"
CONTRACT_BLOCK = "Ozon 官方完整更新 Schema 尚未核验，暂不开放此写操作"


class OzonOnlineAdapter:
    platform = "ozon"

    def __init__(self, config: dict[str, Any]):
        self.config = config[self.platform]
        self.account_id = str(self.config.get("client_id") or "")
        self.key = str(self.config.get("api_key") or "")
        if not self.account_id or not self.key:
            raise ValueError("请先配置 Ozon Client ID 和 API Key")

    def request(self, path: str, body: dict[str, Any]):
        return request_ozon_json("POST", API+path, self.account_id, self.key, body)

    def discover(self) -> Iterator[str]:
        seen = set()
        for visibility in ("ALL", "ARCHIVED"):
            cursor, cursors = "", set()
            while True:
                page = self.request("/v3/product/list", {"filter": {"visibility": visibility}, "last_id": cursor, "limit": 100}).get("result", {})
                if not isinstance(page.get("items"), list):
                    raise ValueError("Ozon 列表响应缺少 result.items")
                if not page["items"]:
                    break
                for row in page["items"]:
                    remote_id = str(row.get("product_id") or "")
                    if not remote_id.isdigit():
                        raise ValueError("Ozon 商品缺少 product_id")
                    if remote_id not in seen:
                        seen.add(remote_id)
                        yield remote_id
                cursor = str(page.get("last_id") or "")
                if not cursor:
                    break
                if cursor in cursors:
                    raise ValueError("Ozon 列表游标重复，旧快照保留")
                cursors.add(cursor)

    def read(self, remote_id: str) -> OnlineListing:
        response = self.request("/v3/product/info/list", {"product_id": [int(remote_id)]})
        rows = response.get("items")
        if not isinstance(rows, list) or len(rows) != 1 or str(rows[0].get("id")) != remote_id:
            raise ValueError("Ozon 详情响应商品身份不一致")
        info = rows[0]
        price_response = self.request("/v5/product/info/prices", {"filter": {"product_id": [int(remote_id)], "visibility": "ALL"}, "cursor": "", "limit": 100})
        price_rows = price_response.get("items")
        if not isinstance(price_rows, list):
            raise ValueError("Ozon 价格响应无效")
        price = next((r.get("price", {}) for r in price_rows if str(r.get("product_id"))==remote_id), {})
        images = info.get("images") or []
        title = str(info.get("name") or "")
        raw_status = "archived" if info.get("is_archived") else str(info.get("statuses", {}).get("status") or "unknown")
        return OnlineListing(id=listing_identity(self.platform,self.account_id,remote_id), platform=self.platform, account_id=self.account_id,
            remote_id=remote_id, model="ozon_product", seller_sku=str(info.get("offer_id") or ""), title=title, thumbnail=images[0] if images else "",
            raw_status=raw_status, content={"title": title, "pictures": images}, snapshot={"info":info,"prices":price_response},
            prices=[PriceScope(id="product", label="商品售价", amount=str(price["price"]) if price.get("price") is not None else None, currency=str(price.get("currency_code") or ""), reason=CONTRACT_BLOCK)],
            stocks=[StockScope(id="unknown", label="卖家仓库存尚未取得", reason="逐仓库存契约尚未核验")],
            capabilities={name:Capability(reason=CONTRACT_BLOCK) for name in ("price","stock","content","sale_state")})

    def write(self, *_args, **_kwargs):
        raise ValueError(CONTRACT_BLOCK)


    def confirmation_details(self, listing, operation, receipt, scope=""):
        raise ValueError("Ozon 写入契约尚未核验，不存在可确认的修改")
