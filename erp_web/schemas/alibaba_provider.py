"""1688 供应商共用的业务输入与结果。"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, TypedDict

from erp_web.schemas.alibaba_orders import AlibabaLogisticsStep, AlibabaOrderStatus, AlibabaParcel, AlibabaPurchaseProduct


class Cargo(TypedDict):
    offer_id: str
    spec_id: str
    quantity: int


class CollectedProduct(TypedDict):
    product: dict[str, Any]
    http_status: int
    request: dict[str, str]


@dataclass(frozen=True)
class PreviewItem:
    offer_id: str
    sku_id: str
    spec_id: str
    quantity: int | None
    quantity_reported: bool


@dataclass(frozen=True)
class PreviewGroup:
    items: tuple[PreviewItem, ...]
    goods_fen: int
    shipping_fen: int
    total_fen: int
    flow: str
    pay_channels: tuple[str, ...]


@dataclass(frozen=True)
class BuyerOrder:
    status: AlibabaOrderStatus
    products: list[AlibabaPurchaseProduct]
    total_fen: Decimal | None
    carrier_names: dict[tuple[str, str], str]
    products_complete: bool


@dataclass(frozen=True)
class ShipmentItem:
    item_id: str
    logistics_id: str
    order_number: str
    quantity: int | None


@dataclass(frozen=True)
class Shipment:
    parcel: AlibabaParcel
    carrier_id: str
    items: tuple[ShipmentItem, ...]


@dataclass(frozen=True)
class ShipmentTrace:
    order_number: str
    logistics_id: str
    tracking_number: str
    steps: list[AlibabaLogisticsStep]
