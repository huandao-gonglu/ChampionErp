"""三平台订单通知的领域契约；未知状态不推断为待发货。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

Platform = Literal["mercadolibre", "ozon", "yandex"]
PLATFORMS = ("mercadolibre", "ozon", "yandex")
OrderState = Literal[
    "pending_shipment", "processing", "shipped", "delivered", "cancelled", "unknown"
]


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp(value: str) -> float:
    if not value:
        return 0
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("平台时间必须包含时区")
    return parsed.timestamp()


class OrderAmountBreakdown(BaseModel):
    """同币种的商品金额组成，不包含配送费用或结算扣费。"""

    payment: str
    subsidy: str = "0.00"
    cashback: str = "0.00"


class OrderLine(BaseModel):
    sku: str = ""
    title: str = ""
    quantity: int = 0
    amount: str = ""
    currency: str = ""
    amount_breakdown: OrderAmountBreakdown | None = None


class OrderSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Platform
    account_id: str
    order_id: str
    fulfillment: str = ""
    title: str = ""
    status: str
    shipping_status: str = ""
    state: OrderState = "unknown"
    amount: str = ""
    currency: str = ""
    amount_breakdown: OrderAmountBreakdown | None = None
    updated_at: str = ""
    items: list[OrderLine] = Field(default_factory=list)

    @property
    def identity(self) -> str:
        return f"{self.platform}:{self.account_id}:{self.fulfillment}:{self.order_id}"


class OrderEvent(BaseModel):
    platform: Platform
    account_id: str
    topic: str
    resource: str
    occurred_at: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        body = {
            k: v for k, v in self.payload.items() if k not in {"attempts", "received"}
        }
        identity = str(body.get("_id") or body.get("uuid") or "") or json.dumps(
            body, sort_keys=True, ensure_ascii=False
        )
        return hashlib.sha256(
            f"{self.platform}:{self.account_id}:{self.topic}:{self.resource}:{identity}".encode()
        ).hexdigest()


def configured_accounts(config: dict[str, Any]) -> dict[str, str]:
    result = {}
    for platform in PLATFORMS:
        store = config.get(platform) or {}
        key = {"mercadolibre": "user_id", "ozon": "client_id", "yandex": "campaign_id"}[
            platform
        ]
        account = str(
            store.get(key)
            or (store.get("seller_id") if platform == "mercadolibre" else "")
            or ""
        )
        credential = store.get(
            {"mercadolibre": "access_token", "ozon": "api_key", "yandex": "api_token"}[
                platform
            ]
        )
        if account and credential:
            result[platform] = account
    return result


class OrderView(OrderSnapshot):
    id: str
    checked_at: str


class NotificationView(BaseModel):
    id: int
    platform: Platform
    topic: str
    status: Literal["queued", "running", "retry", "failed", "done"]
    attempts: int
    error: str
    received_at: str
    next_attempt: float


class OrderAlert(BaseModel):
    id: int
    order_id: str
    platform: Platform
    account_id: str
    title: str
    created_at: str
    read_at: str


class OrdersPage(BaseModel):
    ok: bool = True
    items: list[OrderView]
    total: int
    counts: dict[str, int]
    notifications: list[NotificationView]
    alerts: list[OrderAlert]
    unread: int
    latest_alert_id: int


class OrderDataError(ValueError):
    """适配器确定的协议错误；消息只能由项目固定文案构造。"""

    code = "ORDER_DATA_INVALID"
    retryable = False


class OrderNotReadyError(OrderDataError):
    """平台通知先于可读取的订单详情到达，允许退避重试。"""

    code = "ORDER_NOT_READY"
    retryable = True


class OrderAdapter(Protocol):
    def read(self, event: OrderEvent) -> Iterable[OrderSnapshot]: ...

    def sync(self) -> Iterable[OrderSnapshot]: ...
