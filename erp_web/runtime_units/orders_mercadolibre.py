"""Mercado Libre 订单与物流详情读取；通知 resource 永不直接作为任意 URL 请求。"""

from __future__ import annotations

import urllib.parse

from erp_web import marketplaces as publisher
from erp_web.runtime_units.order_notifications import identifier
from erp_web.runtime_units.store_credentials import get_mercadolibre_access_token
from erp_web.schemas.orders import (
    OrderDataError,
    OrderLine,
    OrderNotReadyError,
    OrderSnapshot,
    timestamp,
)


class MercadoLibreOrderAdapter:
    def __init__(self, config):
        self.config = config
        self.account = identifier(
            config["mercadolibre"].get("user_id")
            or config["mercadolibre"].get("seller_id")
        )

    def request(self, path):
        token = get_mercadolibre_access_token(self.config)
        return publisher.request_json(
            "GET", "https://api.mercadolibre.com" + path, token
        )

    def normalize(self, row):
        if str((row.get("seller") or {}).get("id") or "") != self.account:
            raise OrderDataError("Mercado Libre 订单不属于当前卖家")
        order_id = identifier(row.get("id"))
        shipping = row.get("shipping") or {}
        if shipping.get("id"):
            shipping = self.request("/shipments/" + identifier(shipping["id"]))
        status = str(row.get("status") or "")
        shipping_status = str(shipping.get("status") or "")
        option = shipping.get("shipping_option") or {}
        country = str(((shipping.get("receiver_address") or {}).get("country") or {}).get("id") or "").upper()
        state = "unknown"
        if status in {"cancelled", "invalid"}:
            state = "cancelled"
        elif shipping_status == "delivered":
            state = "delivered"
        elif shipping_status in {"shipped", "not_delivered"}:
            state = "shipped"
        elif (
            status == "paid"
            and shipping_status == "ready_to_ship"
            and shipping.get("logistic_type") != "fulfillment"
            and "fraud_risk_detected" not in (row.get("tags") or [])
        ):
            state = "pending_shipment"
        elif status in {
            "confirmed",
            "payment_required",
            "payment_in_process",
            "partially_paid",
            "paid",
        }:
            state = "processing"
        shipment_deadline = ""
        if state == "pending_shipment" and (row.get("shipping") or {}).get("id"):
            shipment_id = identifier(row["shipping"]["id"])
            # 跨境父账号与本地账号使用各自的 SLA 资源，不以买家送达日期代替发货截止。
            prefix = (
                "/marketplace"
                if self.config["mercadolibre"].get("account_site_id") == "CBT"
                else ""
            )
            sla = self.request(f"{prefix}/shipments/{shipment_id}/sla")
            shipment_deadline = str(sla.get("expected_date") or "")
        items = [
            OrderLine(
                remote_id=str((item.get("item") or {}).get("id") or ""),
                variant_id=str((item.get("item") or {}).get("variation_id") or ""),
                sku=str((item.get("item") or {}).get("seller_sku") or ""),
                title=str((item.get("item") or {}).get("title") or ""),
                quantity=int(item.get("quantity") or 0),
            )
            for item in row.get("order_items") or []
        ]
        dates = [
            str(value)
            for value in (row.get("last_updated"), shipping.get("last_updated"))
            if value
        ]
        return OrderSnapshot(
            platform="mercadolibre",
            account_id=self.account,
            order_id=order_id,
            delivery={
                "fulfillment_model": str(shipping.get("logistic_type") or shipping.get("mode") or ""),
                "warehouse_id": str((shipping.get("origin") or {}).get("warehouse_id") or (shipping.get("sender_address") or {}).get("id") or ""),
                "method_id": str(option.get("id") or ""),
                "method_name": str(option.get("name") or ""),
                "carrier": str(shipping.get("mode") or ""),
                "country": country if len(country) == 2 and country.isalpha() else "",
                "shipment_id": str(shipping.get("id") or ""),
                "tracking_number": str(shipping.get("tracking_number") or ""),
            },
            status=status,
            shipping_status=shipping_status,
            state=state,
            title=items[0].title if items else "",
            items=items,
            amount=str(row.get("total_amount") or ""),
            currency=str(row.get("currency_id") or ""),
            shipment_deadline=shipment_deadline,
            updated_at=max(dates, key=timestamp) if dates else "",
        )

    def read(self, event):
        if event.topic == "shipments":
            shipment = self.request(
                "/shipments/" + identifier(event.resource.rsplit("/", 1)[-1])
            )
            # 物流通知不保证携带订单，缺失关联时由定期对账补齐，不能生成虚构订单。
            if not shipment.get("order_id"):
                yield from self.sync()
                return
            order_id = identifier(shipment["order_id"])
        else:
            order_id = identifier(event.resource.rsplit("/", 1)[-1])
        row = self.request("/orders/" + order_id)
        if str(row.get("id") or "") != order_id:
            raise OrderNotReadyError("Mercado Libre 尚未返回所请求的订单")
        yield self.normalize(row)

    def sync(self):
        offset, seen = 0, set()
        while True:
            query = urllib.parse.urlencode(
                {"seller": self.account, "limit": 50, "offset": offset}
            )
            data = self.request("/orders/search/recent?" + query)
            rows = data.get("results")
            if not isinstance(rows, list):
                raise OrderDataError("Mercado Libre 订单响应缺少 results")
            total = int((data.get("paging") or {}).get("total") or 0)
            for row in rows:
                key = identifier(row.get("id"))
                if key in seen:
                    raise OrderDataError("Mercado Libre 订单分页重复")
                seen.add(key)
                yield self.normalize(row)
            offset += len(rows)
            if offset >= total:
                return
            if not rows:
                raise OrderDataError("Mercado Libre 订单分页未完成")
