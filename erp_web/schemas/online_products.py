"""在线刊登与领域任务契约；远端商品不要求存在本地草稿。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
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


class OnlineProduct(BaseModel):
    """页面与 AI 共用的在线商品公开字段，不包含平台原始响应。"""
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
    version: str = ""
    synced_at: str = ""
    status_checked_at: str = ""
    errors: list[str] = Field(default_factory=list)
    details_state: Literal["pending", "ready", "failed"] = "ready"
    desired_sale_state: str = ""
    sale_state: str = "unknown"
    local_product_id: str = ""
    local_draft_id: str = ""


class OnlineListing(OnlineProduct):
    snapshot: dict[str, Any] = Field(default_factory=dict)


class MarketStatus(BaseModel):
    """单件刷新只携带市场身份与状态，不更新市场价格。"""
    model_config = ConfigDict(extra="forbid")
    id: str
    raw_status: str = Field(min_length=1)
    raw_sub_status: list[str] = Field(default_factory=list)


class OnlineStatus(BaseModel):
    """平台已核验的单件状态；不包含价格、库存和内容数据。"""
    model_config = ConfigDict(extra="forbid")
    remote_id: str
    raw_status: str = Field(min_length=1)
    sale_state: str
    raw_sub_status: list[str] = Field(default_factory=list)
    markets: list[MarketStatus] = Field(default_factory=list)


class RefreshStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    listing_id: str = Field(min_length=1)


@dataclass
class OnlineSyncBatch:
    """平台同步的领域批次；目录先展示，完整详情或失败再逐项落库。"""

    phase: Literal["catalog", "details"]
    listings: list[OnlineListing] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    discovery_complete: bool = False


class OnlineProductGroup(BaseModel):
    """列表父节点；item_ids 只引用本页匹配的刊登，父节点不接受商品修改。"""

    id: str
    title: str
    kind: Literal["group", "single"]
    item_ids: list[str]
    total_count: int


class OnlineChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    listing_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    operation: Literal["price", "stock", "content", "sale_state"]
    scope_id: str = ""
    changes: dict[str, Any] = Field(description="复用在线修改契约：price={amount,currency}；stock={quantity}（绝对数量）；sale_state={state:paused|active}；content 为 capabilities 中允许字段的局部变更，图片须传完整目标列表。")


class ChangeRequest(OnlineChange):
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
