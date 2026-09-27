"""Ozon 只读同步边界；未经完整官方 Schema 核验的写操作不对用户开放。"""
from __future__ import annotations

from typing import Any
from erp_web.marketplaces.config_http import request_ozon_json
from erp_web.runtime_units.online_ozon_read import read_batch, sync_ozon
from erp_web.runtime_units.online_ozon_snapshot import CONTRACT_BLOCK
from erp_web.runtime_units.online_product_status import ozon_status
from erp_web.schemas.online_products import OnlineListing

API = "https://api-seller.ozon.ru"


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

    def sync(self, ids=None):
        return sync_ozon(self, ids)

    def read_status(self, listing):
        return ozon_status(self, listing)

    def read(self, remote_id: str) -> OnlineListing:
        batch = read_batch(self, [remote_id])
        if batch.errors:
            raise ValueError(batch.errors[remote_id])
        return batch.listings[0]

    def write(self, *_args, **_kwargs):
        raise ValueError(CONTRACT_BLOCK)


    def confirmation_details(self, listing, operation, receipt, scope=""):
        raise ValueError("Ozon 写入契约尚未核验，不存在可确认的修改")
