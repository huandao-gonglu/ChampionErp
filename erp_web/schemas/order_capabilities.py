"""订单工具只补充调用形状，业务字段复用订单、采购和履约契约。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from erp_web.schemas.alibaba_self_purchase import (
    AlibabaPaymentAgreement, AlibabaSelfPurchaseRecord,
)
from erp_web.schemas.fulfillment import FulfillmentCommand
from erp_web.schemas.order_procurement import PurchaseRegistration
from erp_web.schemas.orders import Platform
from erp_web.schemas.order_chat import OrderChatHistory, OrderChatSent


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrderRequest(OrderInput):
    order_id: str = Field(min_length=1, max_length=200)


class OrderChatSendRequest(OrderRequest):
    message: str = Field(min_length=1, max_length=4096)

    @field_validator("message")
    @classmethod
    def require_message(cls, value):
        if not value.strip():
            raise ValueError("消息正文不能为空白")
        return value


class OrderLineRequest(OrderRequest):
    line_key: str = Field(min_length=1, max_length=300)


class OrderSyncRequest(OrderInput):
    platform: Platform


class OrderNotificationRequest(OrderInput):
    action: Literal["retry", "acknowledge"]
    event_id: int | None = Field(default=None, gt=0)
    through_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_matching_identity(self):
        if self.action == "retry" and (self.event_id is None or self.through_id is not None):
            raise ValueError("重试须提供通知 event_id，不能使用提醒编号")
        if self.action == "acknowledge" and (self.through_id is None or self.event_id is not None):
            raise ValueError("标为已读须提供提醒 through_id，不能使用通知编号")
        return self


class OrderPurchaseRecordRequest(PurchaseRegistration):
    pass


class OrderAddressRequest(OrderRequest):
    shipment_id: str = Field(min_length=1, max_length=100)
    address: str = Field(default="", max_length=4000)


class OrderFulfillmentRequest(FulfillmentCommand):
    # 保留已有持久化工具调用的读取能力，新调用不依赖模型提供这个版本。
    receiving_revision: int = Field(default=0, ge=0,
        description="无需填写。收货仓版本由服务端从核验通过的审批快照传入；本字段仅保留旧调用兼容，不作为执行依据。")
    action: Literal[
        "fetch-label", "label", "parcels", "plan", "pause", "resume",
        "submit", "sync", "associate", "retry", "cancel",
    ]


class OrderServicesRequest(OrderInput):
    section_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)


class PurchaseResult(OrderInput):
    ok: bool = True
    record: AlibabaSelfPurchaseRecord


class BatchPurchaseResult(OrderInput):
    ok: bool = True
    records: list[AlibabaSelfPurchaseRecord]


class PaymentStatusResult(OrderInput):
    ok: bool = True
    agreement: AlibabaPaymentAgreement


class CashierResult(OrderInput):
    ok: bool = True
    url: str


class OrderDataResult[T](OrderInput):
    data: T


class OrderChatHistoryResult(OrderDataResult[OrderChatHistory]):
    order_id: str
    checked_at: str


class OrderChatSendResult(OrderDataResult[OrderChatSent]):
    order_id: str
