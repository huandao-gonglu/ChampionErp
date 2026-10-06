"""Ozon FBS/rFBS 与 FBO 发运单读取，两个履约模型分别归并。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from erp_web.marketplaces.config_http import request_ozon_json
from erp_web.runtime_units.order_notifications import identifier
from erp_web.schemas.orders import (
    OrderDataError,
    OrderLine,
    OrderNotReadyError,
    OrderSnapshot,
)


class OzonOrderAdapter:
    def __init__(self, config):
        self.store = config["ozon"]
        self.account = identifier(self.store["client_id"])

    def request(self, path, body):
        return request_ozon_json(
            "POST",
            "https://api-seller.ozon.ru" + path,
            self.account,
            self.store["api_key"],
            body,
        )

    def normalize(self, row, model):
        delivery = row.get("delivery_method") or {}
        country = str(row.get("country") or (row.get("analytics_data") or {}).get("country") or "").upper()
        status = str(row.get("status") or "")
        state = {
            "delivering": "shipped",
            "delivered": "delivered",
            "cancelled": "cancelled",
            "awaiting_packaging": "pending_shipment",
            "awaiting_deliver": "pending_shipment",
            "awaiting_registration": "processing",
            "acceptance_in_progress": "processing",
            "awaiting_approve": "processing",
            "arbitration": "processing",
            "client_arbitration": "processing",
        }.get(status, "unknown")
        if model == "fbo" and state == "pending_shipment":
            state = "processing"
        items = [
            OrderLine(
                remote_id=str(item.get("sku") or ""),
                sku=str(item.get("offer_id") or item.get("sku") or ""),
                title=str(item.get("name") or ""),
                quantity=int(item.get("quantity") or 0),
            )
            for item in row.get("products") or []
        ]
        return OrderSnapshot(
            platform="ozon",
            account_id=self.account,
            order_id=identifier(row.get("posting_number")),
            fulfillment=model,
            title=items[0].title if items else "",
            status=status,
            state=state,
            items=items,
            delivery={
                "warehouse_id": str(delivery.get("warehouse_id") or ""),
                "warehouse_name": str(delivery.get("warehouse") or delivery.get("warehouse_name") or ""),
                "method_id": str(delivery.get("id") or ""),
                "method_name": str(delivery.get("name") or ""),
                "carrier": str(delivery.get("tpl_provider") or ""),
                "tracking_number": str(row.get("tracking_number") or ""),
                "country": country if len(country) == 2 and country.isalpha() else "",
            },
            shipment_deadline=str(row.get("shipment_date") or ""),
            # in_process_at 是创建/开始处理时间，不是状态版本；不能据此丢弃后续状态。
            updated_at=str(row.get("updated_at") or ""),
        )

    def read(self, event):
        if event.topic.startswith("TYPE_ORDER_"):
            yield from self.sync()
            return
        model = "fbo" if event.topic.startswith("TYPE_FBO_") else "fbs"
        path = "/v2/posting/fbo/get" if model == "fbo" else "/v3/posting/fbs/get"
        data = self.request(path, {"posting_number": event.resource})
        row = data.get("result")
        if (
            not isinstance(row, dict)
            or str(row.get("posting_number") or "") != event.resource
        ):
            raise OrderNotReadyError("Ozon 尚未返回所请求的发运单")
        yield self.normalize(row, model)

    def sync(self):
        until = datetime.now(timezone.utc)
        since = until - timedelta(days=30)
        for model in ("fbs", "fbo"):
            cursor, cursors, seen = "", set(), set()
            while True:
                path = (
                    "/v4/posting/fbs/list" if model == "fbs" else "/v3/posting/fbo/list"
                )
                data = self.request(
                    path,
                    {
                        "sort_dir": "ASC",
                        "filter": {"since": since.isoformat(), "to": until.isoformat()},
                        "limit": 100,
                        "cursor": cursor,
                    },
                )
                rows = data.get("postings")
                if not isinstance(rows, list) or not isinstance(
                    data.get("has_next"), bool
                ):
                    raise OrderDataError("Ozon 发运单响应缺少 postings 或 has_next")
                for row in rows:
                    key = identifier(row.get("posting_number"))
                    if key in seen:
                        raise OrderDataError("Ozon 发运单分页重复")
                    seen.add(key)
                    yield self.normalize(row, model)
                if not data["has_next"]:
                    break
                cursor = str(data.get("cursor") or "")
                if not rows or not cursor or cursor in cursors:
                    raise OrderDataError("Ozon 发运单分页缺少有效游标，未完成对账")
                cursors.add(cursor)
