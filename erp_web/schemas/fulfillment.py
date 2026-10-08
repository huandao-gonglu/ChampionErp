"""跨境履约的内部契约；平台配送、采购事实与仓库状态分别保存。"""

from __future__ import annotations

import ipaddress
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, field_validator


class FulfillmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DeliverySource(BaseModel):
    fulfillment_model: str = ""
    warehouse_id: str = ""
    warehouse_name: str = ""
    method_id: str = ""
    method_name: str = ""
    carrier: str = ""
    country: str = ""
    shipment_id: str = ""
    tracking_number: str = ""
    label_url: str = ""


PositiveId = Annotated[int, Field(gt=0, strict=True)]


def bus_identifier(value):
    """官方实际回执会将整数 ID 编码为字符串，边界统一转为正整数。"""
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    if type(value) is not int or value <= 0:
        raise ValueError("跨境巴士 ID 必须是正整数")
    return value


BusIdentifier = Annotated[int, BeforeValidator(bus_identifier)]


class BusWarehouse(BaseModel):
    id: BusIdentifier
    name: str
    code: str = ""


class BusSection(BaseModel):
    section_id: BusIdentifier
    section_name: str
    storehouse_list: list[BusWarehouse]


class BusCatalog(BaseModel):
    identity: str = ""
    checked_at: float = 0
    sections: list[BusSection] = Field(default_factory=list)


class BusService(BaseModel):
    id: BusIdentifier
    name: str
    gold: float = Field(default=0, ge=0)


class BusServices(BaseModel):
    core_data: list[BusService]
    optional_data: list[BusService]


def normalize_bus_catalog(value):
    try:
        return BusCatalog.model_validate(value).model_dump(mode="json")
    except ValidationError:
        raise FulfillmentError("合作仓库回执字段无效，请重新读取合作仓库。") from None


def normalize_bus_services(value):
    try:
        return BusServices.model_validate(value).model_dump(mode="json")
    except ValidationError:
        raise FulfillmentError("合作增值服务回执字段无效，请重新读取仓库服务。") from None


class FulfillmentPlan(FulfillmentInput):
    section_id: int = Field(gt=0, strict=True)
    warehouse_id: int = Field(gt=0, strict=True)
    service_ids: list[PositiveId] = Field(default_factory=list, max_length=100)


class FulfillmentRule(FulfillmentPlan):
    id: str = ""
    platform: Literal["ozon", "yandex", "mercadolibre"]
    account_id: str = Field(min_length=1, max_length=128)
    fulfillment: str = Field(min_length=1, max_length=80)
    platform_warehouse_id: str = Field(min_length=1, max_length=128)
    delivery_method_id: str = Field(min_length=1, max_length=128)
    delivery_method_name: str = Field(min_length=1, max_length=200)
    country: str = Field(pattern=r"^[A-Z]{2}$")
    compatible_warehouse_ids: list[PositiveId] = Field(min_length=1, max_length=100)
    confirmed: bool
    auto_submit: bool = True


class DomesticParcel(FulfillmentInput):
    id: str = Field(min_length=1, max_length=80)
    line_key: str = Field(min_length=1, max_length=500)
    purchase_record_id: str = Field(min_length=1, max_length=80)
    carrier: str = Field(min_length=1, max_length=80)
    tracking_number: str = Field(min_length=1, max_length=100)
    quantity: int = Field(gt=0, strict=True)


class LabelInput(FulfillmentInput):
    url: str = Field(min_length=1, max_length=4096)
    tracking_number: str = Field(min_length=1, max_length=100)

    @field_validator("url")
    @classmethod
    def public_label(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("面单必须使用可供仓库下载的 HTTPS 地址")
        host = parsed.hostname.lower()
        if host == "localhost" or host.endswith((".local", ".internal", ".localhost")) or parsed.fragment or any(c.isspace() for c in value) or "\\" in value:
            raise ValueError("面单地址必须指向公开下载地址")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise ValueError("面单地址不能指向内网")
        return value


class FulfillmentCommand(FulfillmentInput):
    order_id: str = Field(min_length=1, max_length=500)
    revision: int = Field(ge=0)
    plan: FulfillmentPlan | None = None
    parcels: list[DomesticParcel] | None = None
    label: LabelInput | None = None
    country: str = Field(default="", pattern=r"^$|^[A-Z]{2}$")


class BusCredentials(FulfillmentInput):
    client_secret: str = Field(default="", max_length=1000)
    user_name: str = Field(default="", max_length=200)
    password: str = Field(default="", max_length=1000)


class LabelUpload(FulfillmentInput):
    order_id: str = Field(min_length=1, max_length=500)
    revision: int = Field(ge=0, strict=True)
    content_base64: str = Field(min_length=1, max_length=12 * 1024 * 1024)


FulfillmentStatus = Literal[
    "NEW", "FULFILLMENT_CREATED", "WAITING_DOMESTIC_SHIPMENT", "WAREHOUSE_RECEIVED",
    "PACKING", "SHIPPED", "COMPLETED", "EXCEPTION", "CANCELLED",
]


class FulfillmentRuleView(FulfillmentRule):
    model_config = ConfigDict(extra="ignore")


class FulfillmentSummary(BaseModel):
    """列表只读事实；不传输面单地址、包裹明细、凭据或内部占位。"""

    model_config = ConfigDict(extra="ignore")
    fulfillment_status: FulfillmentStatus
    crossborderbus_order_id: int | None = None
    busy: bool = False
    operation: str = ""
    create_unknown: bool = False
    cancel_requested: bool = False
    cancel_rejected: bool = False
    error_message: str = ""
    label_error: str = ""
    has_domestic_waybill: bool = False


class FulfillmentView(BaseModel):
    """订单详情公开形状；内部租约、账号指纹与占位字段不向前端传输。"""

    ok: bool = True
    erp_order_id: str
    revision: int
    busy: bool
    editing: bool
    editable: bool
    plan_editable: bool
    create_unknown: bool
    cancel_requested: bool
    cancel_rejected: bool = False
    crossborderbus_order_id: int | None
    fulfillment_status: FulfillmentStatus
    operation: str
    error_message: str
    blocked_reason: str
    last_attempt_at: str
    last_synced_at: str
    next_attempt: float
    platform_label: str
    platform_tracking_number: str
    label_fetch_supported: bool = False
    label_fetch_reason: str = ""
    label_error: str = ""
    label_attempt_at: str = ""
    country: str
    plan: FulfillmentPlan | None
    override: bool
    rule: FulfillmentRuleView | None
    section_name: str
    warehouse_name: str
    delivery: DeliverySource
    parcels: list[DomesticParcel]
    has_domestic_waybill: bool = False
    update_pending: bool = False


class FulfillmentError(ValueError):
    def __init__(self, message: str, *, unknown=False, definitive=True):
        super().__init__(message)
        self.unknown = unknown
        self.definitive = definitive
