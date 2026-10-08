"""1688 采购查询的公开形状；只返回交易状态和物流信息。"""

from typing import Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class PurchaseQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1, max_length=200)
    record_id: str = Field(min_length=1, max_length=200)
    kind: Literal["order", "logistics"] = "order"


class PurchaseSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1, max_length=200)
    record_id: str = Field(min_length=1, max_length=200)


class AlibabaOrderStatus(TypedDict):
    order_number: str
    status: str
    status_label: str


class AlibabaLogisticsStep(TypedDict):
    time: str
    description: str


class AlibabaParcel(TypedDict):
    logistics_id: str
    company: str
    tracking_number: str
    status: str
    status_label: str
    steps: list[AlibabaLogisticsStep]


class AlibabaPurchaseQueryResult(TypedDict):
    ok: bool
    record_id: str
    order_number: str
    checked_at: str
    order: AlibabaOrderStatus | None
    logistics: list[AlibabaParcel] | None
    logistics_warning: str
    logistics_checked_at: NotRequired[str]


class PurchaseTrackingSummary(TypedDict):
    orders: list[AlibabaOrderStatus]
    unknown_count: int
    has_waybill: bool
    stale: bool


class PurchaseProgressView(TypedDict):
    state: Literal["syncing", "synced", "pending_assignment", "conflict", "locked", "manual", "error"]
    attempted_at: str
    message: str
    error: str
    data: AlibabaPurchaseQueryResult | None
