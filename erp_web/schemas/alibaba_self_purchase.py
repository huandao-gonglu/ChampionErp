"""1688 自购的请求与界面回执；金额统一为人民币分。"""
from typing import Literal, TypedDict
from pydantic import BaseModel, ConfigDict, Field


class SelfPurchasePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1, max_length=200)
    line_key: str = Field(min_length=1, max_length=300)
    candidate_id: str = Field(min_length=1, max_length=100)
    quantity: int = Field(gt=0, le=10000, strict=True)


class SelfPurchaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=1, max_length=100)
    pay_channel: Literal["alipay", "shegou"]


class SelfPurchaseRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=1, max_length=100)


class AlibabaSelfPurchaseCandidate(TypedDict):
    id: str
    offer_id: str
    sku_id: str
    specification: str
    product_url: str


class AlibabaSelfPurchasePreview(TypedDict):
    candidate: AlibabaSelfPurchaseCandidate
    quantity: int
    recipient: str
    address: str
    phone: str
    goods_fen: int
    shipping_fen: int
    total_fen: int
    flow: str
    pay_channels: list[str]
    expires_at: float


class AlibabaSelfPurchaseRecord(TypedDict):
    id: str
    state: str
    preview: AlibabaSelfPurchasePreview
    order_numbers: list[str]
    order_status: str
    message: str
    pay_channel: str
    created_at: str
    purchase_record_id: str


class AlibabaSelfPurchaseOptions(TypedDict):
    ok: bool
    remaining_quantity: int
    can_purchase: bool
    blocked_reason: str
    candidates: list[AlibabaSelfPurchaseCandidate]
    records: list[AlibabaSelfPurchaseRecord]
