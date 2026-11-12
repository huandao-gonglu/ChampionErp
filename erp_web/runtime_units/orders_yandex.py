"""Yandex 订单读取，使用当前 business orders 接口；分页不截断为成功。"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal, InvalidOperation

from erp_web.marketplaces.yandex_http import request_yandex_json
from erp_web.services.order_handover_service import enrich_yandex_handover
from erp_web.runtime_units.order_notifications import identifier
from erp_web.schemas.orders import (
    OrderAmountBreakdown,
    OrderDataError,
    OrderLine,
    OrderNotReadyError,
    OrderSnapshot,
)


def normalize_yandex_amount(prices):
    """平台已按数量汇总金额；使用十进制定点加总，不能再次乘商品数量。"""
    if not prices or prices.get("payment") is None:
        return {"amount": "", "currency": "", "amount_breakdown": None}
    payment = prices["payment"]
    currency = payment.get("currencyId") if isinstance(payment, dict) else None
    if not isinstance(currency, str) or not currency:
        raise OrderDataError("Yandex 商品金额缺少币种")
    amounts = {}
    for key in ("payment", "subsidy", "cashback"):
        part = prices.get(key)
        if part is None:
            amounts[key] = Decimal(0)
            continue
        if not isinstance(part, dict) or part.get("currencyId") != currency:
            raise OrderDataError("Yandex 商品金额组成的币种不一致")
        try:
            value = Decimal(str(part.get("value")))
        except InvalidOperation as exc:
            raise OrderDataError("Yandex 商品金额不是有效数字") from exc
        if not value.is_finite() or value < 0:
            raise OrderDataError("Yandex 商品金额必须为非负有限数字")
        amounts[key] = value

    def money(value):
        # 至少保留两位小数，不提前舍入平台返回的精度。
        return format(value, f".{max(2, -value.as_tuple().exponent)}f")

    return {
        "amount": money(sum(amounts.values(), Decimal(0))),
        "currency": currency,
        "amount_breakdown": OrderAmountBreakdown(
            **{key: money(value) for key, value in amounts.items()}
        ),
    }


def normalize_yandex_shipment(delivery):
    """保留平台提供的日期精度；未返回时间或时区时不补造零点或偏移。"""
    shipment = (delivery or {}).get("shipment") or {}
    value = shipment.get("shipmentDate")
    if not value:
        return ""
    try:
        day = date.fromisoformat(value).isoformat()
        clock = shipment.get("shipmentTime")
        return f"{day}T{time.fromisoformat(clock).isoformat()}" if clock else day
    except (TypeError, ValueError) as exc:
        raise OrderDataError("Yandex 发货日期或时间格式无效") from exc


class YandexOrderAdapter:
    def __init__(self, config, *, shops=None, on_shops=None):
        self.store = config["yandex"]
        self.account = identifier(self.store["campaign_id"])
        self.business = identifier(self.store["business_id"])
        self.shops = dict(shops) if shops is not None else {self.account: str(self.store.get("shop_name") or self.account)}
        self.on_shops = on_shops

    def pages(self, body, *, campaigns):
        cursor = ""
        seen = set()
        while True:
            data = request_yandex_json(
                "POST",
                f"/v1/businesses/{self.business}/orders",
                self.store["api_token"],
                {"campaignIds": [int(account) for account in campaigns], **body},
                query={"limit": 50, "pageToken": cursor},
            )
            rows = data.get("orders")
            if not isinstance(rows, list):
                raise OrderDataError("Yandex 订单响应缺少 orders")
            snapshots = [self.normalize(row) for row in rows]
            if any(row.account_id not in campaigns for row in snapshots):
                raise OrderDataError("Yandex 订单不属于本次查询店铺")
            # 同页跨店铺时，交货批次仍按真实 campaign 合批，不能借用默认发布店铺。
            enriched = {}
            for account in dict.fromkeys(row.account_id for row in snapshots):
                enriched.update((row.identity, row) for row in enrich_yandex_handover(
                    [row for row in snapshots if row.account_id == account],
                    account=account, api_token=self.store["api_token"], request=request_yandex_json,
                ))
            yield from (enriched[row.identity] for row in snapshots)
            cursor = str((data.get("paging") or {}).get("nextPageToken") or "")
            if not cursor:
                return
            if cursor in seen or not rows:
                raise OrderDataError("Yandex 订单分页未前进")
            seen.add(cursor)

    def normalize(self, row):
        delivery = row.get("delivery") or {}
        country = str(delivery.get("countryCode") or "").upper()
        account = str(row.get("campaignId") or "")
        if account not in self.shops:
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
                line_id=str(item.get("id") or ""),
                remote_id=str(item.get("offerId") or ""),
                sku=str(item.get("offerId") or ""),
                title=str(item.get("offerName") or ""),
                quantity=int(item.get("count") or 0),
                **normalize_yandex_amount(item.get("prices")),
            )
            for item in row.get("items") or []
        ]
        return OrderSnapshot(
            platform="yandex",
            account_id=account,
            order_id=identifier(row.get("orderId")),
            fulfillment=model,
            title=items[0].title if items else "",
            status=status,
            shipping_status=substatus,
            state=state,
            **normalize_yandex_amount(row.get("prices")),
            updated_at=str(row.get("updateDate") or ""),
            shipment_deadline=normalize_yandex_shipment(row.get("delivery")),
            items=items,
            delivery={
                "warehouse_id": str(delivery.get("warehouseId") or ""),
                "method_id": str(delivery.get("deliveryServiceId") or ""),
                "method_name": str(delivery.get("serviceName") or ""),
                "carrier": str(delivery.get("deliveryPartnerType") or ""),
                "country": country if len(country) == 2 and country.isalpha() else "",
            },
        )

    def read(self, event):
        if event.account_id not in self.shops:
            raise OrderDataError("Yandex 订单不属于当前授权店铺")
        rows = list(self.pages({"orderIds": [int(event.resource)]}, campaigns=[event.account_id]))
        if len(rows) != 1 or rows[0].order_id != event.resource:
            raise OrderNotReadyError("Yandex 尚未返回所请求的订单")
        yield from rows

    def sync(self):
        from erp_web.marketplaces.yandex_http import fetch_yandex_shop_names

        self.shops = fetch_yandex_shop_names(self.store["api_token"], self.business, request=request_yandex_json)
        if not self.shops:
            raise OrderDataError("Yandex 当前企业没有可读取的店铺")
        if self.on_shops:
            self.on_shops(self.shops)
        # 官方 campaignIds 上限为 50，同一企业合批查询全部店铺。
        campaigns = list(self.shops)
        for start in range(0, len(campaigns), 50):
            yield from self.pages({}, campaigns=campaigns[start:start + 50])
