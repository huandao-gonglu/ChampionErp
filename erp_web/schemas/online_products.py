"""在线刊登与领域任务契约；远端商品不要求存在本地草稿。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

Platform = Literal["mercadolibre", "ozon", "yandex"]
Operation = Literal["sync", "price", "stock", "content", "sale_state", "archive"]
JobStatus = Literal["queued", "running", "submitted", "waiting_confirmation", "confirmed", "partial", "failed", "outcome_unknown", "closed_unconfirmed"]


class OnlineConflictResponse(BaseModel):
    ok: Literal[False] = False
    error: str
    error_code: Literal["ONLINE_CONFLICT"] = "ONLINE_CONFLICT"
    conflict_job_id: str | None = None


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


class PlatformIssue(BaseModel):
    """平台对商品的业务反馈；与 ERP 同步失败分开保存。"""

    model_config = ConfigDict(extra="forbid")
    severity: Literal["error", "warning"]
    source: str = "card"
    code: str = ""
    message: str
    comment: str = ""


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
    platform_issues: list[PlatformIssue] = Field(default_factory=list)
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
    archived: bool = False
    local_product_id: str = ""
    local_draft_id: str = ""


class OnlineListing(OnlineProduct):
    snapshot: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def read_archive_state(cls, value):
        """旧持久快照保留平台归档事实，并开放当前版本的独立归档能力。"""
        if not isinstance(value, dict) or value.get("platform") != "yandex":
            return value
        value = dict(value)
        value.setdefault("archived", value.get("snapshot", {}).get("offer", {}).get("archived") is True)
        value["capabilities"] = {**value.get("capabilities", {}), "archive": {
            "enabled": value.get("details_state", "ready") == "ready" and not value.get("errors"),
            "scope": "商家账号全部店铺", "fields": ["archived"],
            "reason": "归档 / 恢复归档影响该商家账号全部店铺，不修改库存或停售设置",
        }}
        return value


class SourceImageOption(BaseModel):
    """关联源商品的图片选项；提交时仅使用资产身份与内容版本。"""

    asset_id: str
    fingerprint: str
    preview_url: str
    existing_picture_id: str = ""
    existing_url: str = ""


class SourceImageSelection(BaseModel):
    ok: bool = True
    local_product_id: str = ""
    local_draft_id: str = ""
    images: list[SourceImageOption] = Field(default_factory=list)
    reason: str = ""


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
    archived: bool | None = None
    raw_sub_status: list[str] = Field(default_factory=list)
    platform_issues: list[PlatformIssue] = Field(default_factory=list)
    markets: list[MarketStatus] = Field(default_factory=list)


class RefreshStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    listing_id: str = Field(min_length=1)


class CloseOnlineOperationRequest(BaseModel):
    """人工接受当前平台状态，仅结束本地待核对操作，不撤销或重放远端请求。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    job_id: str = Field(min_length=1)
    expected_updated_at: str = Field(min_length=1, description="最近一次查询平台结果返回的 job.updated_at。")
    acknowledge: bool = Field(description="明确确认：原请求结果仍未确认且可能延迟生效；结束本地操作后允许新的修改。")

    @field_validator("acknowledge")
    @classmethod
    def require_acknowledgement(cls, value: bool) -> bool:
        if not value:
            raise ValueError("请确认原请求结果仍未确认，结束本地操作后允许新的修改")
        return value


@dataclass
class OnlineSyncBatch:
    """平台同步的领域批次；目录先展示，完整详情或失败再逐项落库。"""

    phase: Literal["catalog", "details"]
    listings: list[OnlineListing] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    discovery_complete: bool = False


class OnlineFeedbackSummary(BaseModel):
    """组合内全部 SKU 的平台反馈计数；条数与受影响 SKU 数分别计算。"""

    affected_sku_count: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)


class OnlineProductGroup(BaseModel):
    """列表父节点；item_ids 只引用本页匹配的刊登，父节点不接受商品修改。"""

    id: str
    title: str
    kind: Literal["group", "single"]
    item_ids: list[str]
    total_count: int
    feedback_summary: OnlineFeedbackSummary = Field(default_factory=OnlineFeedbackSummary)


class OnlineChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    listing_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    operation: Literal["price", "stock", "content", "sale_state", "archive"]
    scope_id: str = ""
    changes: dict[str, Any] = Field(description="复用在线修改契约：price={amount,currency}；stock={quantity}（绝对数量）；sale_state={state:paused|active}；archive={archived:true|false}，仅支持 Yandex，scope_id=business，影响账号全部店铺，独立于停售与库存；content 为 capabilities 中允许字段的局部变更，图片须传完整目标列表；保留的图片使用当前平台 ID/地址，新增图片只允许关联源草稿的 {asset_id,fingerprint} 引用。")


class ChangeRequest(OnlineChange):
    idempotency_key: str = Field(min_length=8, max_length=128)


class BatchChangeTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    listing_id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class BatchChangePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    platform: Literal["yandex"]
    catalog: Literal["current", "archived"] = "current"
    group_ids: list[str] = Field(default_factory=list, max_length=1000)
    listing_ids: list[str] = Field(default_factory=list, max_length=1000)
    operation: Literal["sale_state", "archive"]
    changes: dict[str, Any]


class BatchChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[BatchChangeTarget] = Field(min_length=1, max_length=5000)
    operation: Literal["sale_state", "archive"]
    changes: dict[str, Any]
    idempotency_key: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def unique_targets(self):
        if len({item.listing_id for item in self.items}) != len(self.items):
            raise ValueError("批量操作不能包含重复 SKU")
        return self


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def listing_identity(platform: str, account: str, remote_id: str) -> str:
    return digest([platform, account, remote_id])[:32]


def snapshot_version(listing: OnlineListing) -> str:
    """版本只覆盖业务事实，时间戳、请求序号与能力提示不制造冲突。"""
    data = listing.model_dump()
    content = data["content"]
    if listing.platform == "yandex":
        content.pop("attribute_names_error", None)
    if listing.platform == "yandex" and isinstance(content.get("attributes"), list):
        content["attributes"] = [{key: value for key, value in row.items() if key != "name"}
                                 for row in content["attributes"]]
    facts = {key: data[key] for key in (
        "remote_id", "model", "title", "sale_state", "raw_status", "raw_sub_status", "markets", "prices", "stocks", "content",
    )}
    # 未归档的旧快照保持版本稳定；归档变化进入业务并发校验。
    if listing.archived:
        facts["archived"] = True
    return digest(facts)
