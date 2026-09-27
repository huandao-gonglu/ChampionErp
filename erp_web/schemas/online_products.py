"""在线刊登与领域任务契约；远端商品不要求存在本地草稿。"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

Platform = Literal["mercadolibre", "ozon", "yandex"]
Operation = Literal["sync", "price", "stock", "content", "sale_state"]
JobStatus = Literal["queued", "running", "submitted", "waiting_confirmation", "confirmed", "partial", "failed", "outcome_unknown"]


class PriceScope(BaseModel):
    id: str
    label: str
    amount: str | None = None
    currency: str = ""
    kind: str = "sale_price"
    writable: bool = False
    reason: str = ""


class StockScope(BaseModel):
    id: str
    label: str
    quantity: int | None = None
    writable: bool = False
    reason: str = ""
    warehouse_id: str = ""
    variation_id: str = ""


class MarketSnapshot(BaseModel):
    id: str
    site_id: str
    seller_id: str = ""
    logistic_type: str = ""
    raw_status: str = ""
    raw_sub_status: list[str] = Field(default_factory=list)
    price: str | None = None
    currency: str = ""


class Capability(BaseModel):
    enabled: bool = False
    reason: str = ""
    fields: list[str] = Field(default_factory=list)
    scope: str = ""


class BuyerLink(BaseModel):
    """平台返回的买家页面；仅用于导航，不代表当前可购买。"""

    label: str
    url: str
    site_id: str = ""

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = HttpUrl(value)
        if parsed.username or parsed.password:
            raise ValueError("买家链接不能包含登录凭据")
        return str(parsed)


class OnlineListing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    platform: Platform
    account_id: str
    remote_id: str
    model: str
    seller_sku: str = ""
    title: str = ""
    thumbnail: str = ""
    buyer_links: list[BuyerLink] = Field(default_factory=list)
    raw_status: str = ""
    raw_sub_status: list[str] = Field(default_factory=list)
    markets: list[MarketSnapshot] = Field(default_factory=list)
    prices: list[PriceScope] = Field(default_factory=list)
    stocks: list[StockScope] = Field(default_factory=list)
    content: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Capability] = Field(default_factory=dict)
    snapshot: dict[str, Any] = Field(default_factory=dict)
    version: str = ""
    synced_at: str = ""
    errors: list[str] = Field(default_factory=list)
    desired_sale_state: str = ""
    sale_state: str = "unknown"
    local_product_id: str = ""
    local_draft_id: str = ""


class ChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    listing_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    operation: Literal["price", "stock", "content", "sale_state"]
    scope_id: str = ""
    changes: dict[str, Any]
    idempotency_key: str = Field(min_length=8, max_length=128)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def listing_identity(platform: str, account: str, remote_id: str) -> str:
    return digest([platform, account, remote_id])[:32]


def snapshot_version(listing: OnlineListing) -> str:
    """版本只覆盖业务事实，时间戳、请求序号与能力提示不制造冲突。"""
    return digest({key: listing.model_dump()[key] for key in (
        "remote_id", "model", "title", "sale_state", "raw_status", "raw_sub_status", "markets", "prices", "stocks", "content",
    )})
