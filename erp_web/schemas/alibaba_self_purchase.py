"""1688 自购的请求与界面回执；金额统一为人民币分。"""
from typing import Literal, TypedDict, NotRequired
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PurchaseAddress(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    fullName: str = Field(min_length=1, max_length=100)
    mobile: str = Field(default="", max_length=40)
    phone: str = Field(default="", max_length=40)
    provinceText: str = Field(min_length=1, max_length=100)
    cityText: str = Field(min_length=1, max_length=100)
    areaText: str = Field(min_length=1, max_length=100)
    townText: str = Field(default="", max_length=100)
    address: str = Field(min_length=1, max_length=1000)
    postCode: str = Field(default="", max_length=20)

    @model_validator(mode="after")
    def contact_required(self):
        if not (self.mobile or self.phone):
            raise ValueError("请补全收货人电话")
        return self


class ParsePurchaseAddressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1, max_length=200)
    line_key: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=4000)


class SelfPurchasePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1, max_length=200)
    line_key: str = Field(min_length=1, max_length=300)
    candidate_id: str = Field(min_length=1, max_length=100)
    quantity: int = Field(gt=0, le=10000, strict=True)
    receiving_revision: int = Field(default=0, ge=0, strict=True)
    address_id: str = Field(default="", max_length=100)
    address: PurchaseAddress | None = None
    address_confirmed: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def selected_address(self):
        if self.receiving_revision:
            if self.address_id or self.address:
                raise ValueError("采购使用已确定仓库的收货地址，不能另选地址")
            return self
        if bool(self.address_id) == bool(self.address):
            raise ValueError("请选择收货地址或填写核对后的地址")
        if self.address is not None and not self.address_confirmed:
            raise ValueError("请先核对并确认收货地址")
        return self


class AlibabaPurchaseAddressFields(TypedDict):
    fullName: str
    mobile: str
    phone: str
    provinceText: str
    cityText: str
    areaText: str
    townText: str
    address: str
    postCode: str


class AlibabaPurchaseAddressCandidate(TypedDict):
    id: str
    kind: str
    label: str
    text: str
    is_default: bool
    blocked_reason: str


class AlibabaPurchaseAddresses(TypedDict):
    ok: bool
    items: list[AlibabaPurchaseAddressCandidate]
    notice: str


class AlibabaParsedPurchaseAddress(TypedDict):
    ok: bool
    address: AlibabaPurchaseAddressFields
    warnings: list[str]


class SelfPurchaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=1, max_length=100)
    pay_channel: Literal["alipay", "shegou"]


class SelfPurchaseRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=1, max_length=100)


class SelfPurchasePayRequest(SelfPurchaseRecordRequest):
    confirmed_total_fen: int = Field(gt=0, strict=True)


class SelfPurchasePaymentStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pay_channel: Literal['alipay', 'shegou']


class AlibabaPaymentAgreement(TypedDict):
    channel: Literal['alipay', 'shegou']
    ready: bool
    signed: bool
    bound: bool
    sign_url: str
    message: str


class AlibabaPurchasePayment(TypedDict):
    state: str
    channel: str
    amount_fen: int
    request_id: str
    message: str


class BatchPurchaseItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_key: str = Field(min_length=1, max_length=300)
    candidate_id: str = Field(min_length=1, max_length=100)
    quantity: int = Field(gt=0, le=10000, strict=True)


class BatchPurchasePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1, max_length=200)
    receiving_revision: int = Field(gt=0, strict=True)
    items: list[BatchPurchaseItemRequest] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_lines(self):
        if len({item.line_key for item in self.items}) != len(self.items):
            raise ValueError("不能重复选择同一订单商品")
        return self


class AlibabaSelfPurchaseCandidate(TypedDict):
    id: str
    offer_id: str
    sku_id: str
    specification: str
    product_url: str


class AlibabaBatchPurchaseItem(TypedDict):
    line_key: str
    title: str
    supplier: str
    candidate: AlibabaSelfPurchaseCandidate
    quantity: int


class AlibabaSelfPurchasePreview(TypedDict):
    items: NotRequired[list[AlibabaBatchPurchaseItem]]
    supplier: NotRequired[str]
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
    payment: NotRequired[AlibabaPurchasePayment | None]
    purchase_record_ids: NotRequired[list[str]]
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
    warehouse_required: bool
    receiving_revision: int
    warehouse_name: str
    receiving_address: AlibabaPurchaseAddressFields | None
    ok: bool
    remaining_quantity: int
    can_purchase: bool
    blocked_reason: str
    candidates: list[AlibabaSelfPurchaseCandidate]
    records: list[AlibabaSelfPurchaseRecord]


class AlibabaBatchPurchaseLine(TypedDict):
    line_key: str
    title: str
    sku: str
    supplier: str
    options: AlibabaSelfPurchaseOptions


class AlibabaBatchPurchaseOptions(TypedDict):
    ok: bool
    lines: list[AlibabaBatchPurchaseLine]
    records: list[AlibabaSelfPurchaseRecord]
