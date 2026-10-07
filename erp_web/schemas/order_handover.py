"""平台交货批次的只读契约，与商品库存仓和跨境仓预报分别展示。"""

from typing import Literal

from pydantic import BaseModel, Field


class HandoverWarehouse(BaseModel):
    id: str = ""
    name: str = ""
    address: str = ""


class OrderHandoverShipment(BaseModel):
    shipment_id: str
    shipment_type: str
    status: str = ""
    planned_from: str = ""
    planned_to: str = ""
    origin: HandoverWarehouse | None = None
    destination: HandoverWarehouse | None = None


class OrderHandoverSnapshot(BaseModel):
    state: Literal["ready", "unavailable"]
    message: str = ""
    checked_at: str = ""
    shipments: list[OrderHandoverShipment] = Field(default_factory=list)
