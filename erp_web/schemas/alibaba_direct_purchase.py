"""独立于 ERP 业务环节的 1688 万邦直购契约；金额统一为人民币分。"""
from __future__ import annotations

from typing import Literal, TypedDict
from pydantic import BaseModel, ConfigDict, Field, model_validator

from erp_web.schemas.alibaba_self_purchase import (
    AlibabaPaymentAgreement,
    AlibabaPurchaseAddressCandidate,
    AlibabaPurchaseAddressFields,
    AlibabaPurchaseAddresses,
    AlibabaParsedPurchaseAddress,
    AlibabaPurchasePayment,
    PurchaseAddress,
)


class DirectPurchaseResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=4000)
    refresh: bool = Field(default=False, strict=True)


class DirectPurchaseParseAddressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)


class DirectPurchaseSkuSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku_id: str = Field(min_length=1, max_length=100)
    quantity: int = Field(gt=0, le=10000, strict=True)


class DirectPurchasePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolve_token: str = Field(min_length=1, max_length=100)
    items: list[DirectPurchaseSkuSelection] = Field(min_length=1, max_length=50)
    address_id: str = Field(default="", max_length=100)
    address: PurchaseAddress | None = None
    address_confirmed: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_selection(self):
        if len({item.sku_id for item in self.items}) != len(self.items):
            raise ValueError("请勿重复勾选相同的 SKU")
        if bool(self.address_id) == bool(self.address):
            raise ValueError("请选择 1688 保存地址或填写收货地址")
        if self.address is not None and not self.address_confirmed:
            raise ValueError("请先核对并确认手动填写的收货地址")
        return self


class DirectPurchaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=1, max_length=100)
    pay_channel: Literal["alipay", "shegou"]


class DirectPurchaseRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=1, max_length=100)


class DirectPurchasePayRequest(DirectPurchaseRecordRequest):
    confirmed_total_fen: int = Field(gt=0, strict=True)


class DirectPurchasePaymentStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pay_channel: Literal["alipay", "shegou"]
    refresh: bool = Field(default=False, strict=True)


class DirectPurchaseResolvedSku(TypedDict):
    sku_id: str
    spec_id: str
    specification: str
    price: str
    stock: str
    image: str
    purchasable: bool
    block_reason: str


class DirectPurchaseResolvedProduct(TypedDict):
    ok: bool
    resolve_token: str
    offer_id: str
    product_url: str
    title: str
    supplier: str
    main_image: str
    skus: list[DirectPurchaseResolvedSku]


class DirectPurchasePreviewItem(TypedDict):
    offer_id: str
    sku_id: str
    spec_id: str
    specification: str
    price: str
    image: str
    quantity: int


class DirectPurchasePreviewView(TypedDict):
    offer_id: str
    product_url: str
    title: str
    supplier: str
    main_image: str
    items: list[DirectPurchasePreviewItem]
    total_quantity: int
    recipient: str
    phone: str
    address: str
    goods_fen: int
    shipping_fen: int
    total_fen: int
    flow: str
    pay_channels: list[str]
    expires_at: float


class DirectPurchaseRecord(TypedDict):
    id: str
    state: str
    preview: DirectPurchasePreviewView
    payment: AlibabaPurchasePayment | None
    order_numbers: list[str]
    order_status: str
    order_status_code: str
    message: str
    pay_channel: str
    quota_credited: bool
    created_at: str
    updated_at: str


class DirectPurchaseState(TypedDict):
    ok: bool
    configured: bool
    authorized: bool
    session_ready: bool
    member_id: str
    account_name: str
    blocked_reason: str
    records: list[DirectPurchaseRecord]


__all__ = [
    "AlibabaPaymentAgreement",
    "AlibabaParsedPurchaseAddress",
    "AlibabaPurchaseAddressCandidate",
    "AlibabaPurchaseAddressFields",
    "AlibabaPurchaseAddresses",
    "AlibabaPurchasePayment",
    "DirectPurchaseCreateRequest",
    "DirectPurchaseParseAddressRequest",
    "DirectPurchasePayRequest",
    "DirectPurchasePaymentStatusRequest",
    "DirectPurchasePreviewItem",
    "DirectPurchasePreviewRequest",
    "DirectPurchasePreviewView",
    "DirectPurchaseRecord",
    "DirectPurchaseRecordRequest",
    "DirectPurchaseResolveRequest",
    "DirectPurchaseResolvedProduct",
    "DirectPurchaseResolvedSku",
    "DirectPurchaseSkuSelection",
    "DirectPurchaseState",
    "PurchaseAddress",
]
