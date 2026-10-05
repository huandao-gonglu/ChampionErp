"""Yandex 订单读取，使用当前 business orders 接口；分页不截断为成功。"""

from __future__ import annotations

from erp_web.marketplaces.yandex_http import request_yandex_json
from erp_web.runtime_units.order_notifications import identifier
from erp_web.schemas.orders import (
    OrderDataError,
    OrderLine,
    OrderNotReadyError,
    OrderSnapshot,
)


class YandexOrderAdapter:
    def __init__(self, config):
        self.store = config["yandex"]
        self.account = identifier(self.store["campaign_id"])
        self.business = identifier(self.store["business_id"])

    def pages(self, body):
        cursor = ""
        seen = set()
        while True:
            data = request_yandex_json(
                "POST",
                f"/v1/businesses/{self.business}/orders",
                self.store["api_token"],
                {"campaignIds": [int(self.account)], **body},
                query={"limit": 50, "pageToken": cursor},
            )
            rows = data.get("orders")
            if not isinstance(rows, list):
                raise OrderDataError("Yandex 订单响应缺少 orders")
            for row in rows:
                yield self.normalize(row)
            cursor = str((data.get("paging") or {}).get("nextPageToken") or "")
            if not cursor:
                return
            if cursor in seen or not rows:
                raise OrderDataError("Yandex 订单分页未前进")
            seen.add(cursor)

    def normalize(self, row):
        if str(row.get("campaignId") or "") != self.account:
            raise OrderDataError("Yandex 订单不属于当前店铺")
        status = str(row.get("status") or "")
        model = str(row.get("programType") or "")
        substatus = str(row.get("substatus") or "")
        state = {
            "DELIVERY": "shipped",
            "PICKUP": "shipped",
            "DELIVERED": "delivered",
            "CANCELLED": "cancelled",
            "RETURNED": "cancelled",
            "PARTIALLY_RETURNED": "delivered",
            "PENDING": "processing",
            "UNPAID": "processing",
            "PLACING": "processing",
        }.get(status, "unknown")
        if status == "PROCESSING":
            state = (
                "pending_shipment"
                if model in {"FBS", "DBS", "EXPRESS"}
                and substatus in {"STARTED", "READY_TO_SHIP", "PACKAGING"}
                else "processing"
            )
        items = [
            OrderLine(
                sku=str(item.get("offerId") or ""),
                title=str(item.get("offerName") or ""),
                quantity=int(item.get("count") or 0),
            )
            for item in row.get("items") or []
        ]
        payment = (row.get("prices") or {}).get("payment") or {}
        return OrderSnapshot(
            platform="yandex",
            account_id=self.account,
            order_id=identifier(row.get("orderId")),
            fulfillment=model,
            title=items[0].title if items else "",
            status=status,
            shipping_status=substatus,
            state=state,
            amount=str(payment.get("value") or ""),
            currency=str(payment.get("currencyId") or ""),
            updated_at=str(row.get("updateDate") or ""),
            items=items,
        )

    def read(self, event):
        rows = list(self.pages({"orderIds": [int(event.resource)]}))
        if len(rows) != 1 or rows[0].order_id != event.resource:
            raise OrderNotReadyError("Yandex 尚未返回所请求的订单")
        yield from rows

    def sync(self):
        # 当前接口默认最近 30 天；窗口外的本地未完成订单由服务逐单核对。
        yield from self.pages({})
