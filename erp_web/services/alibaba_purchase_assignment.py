"""用商品、SKU、交易子项和包裹数量证明采购归属；缺少证据时交由人工分配。"""

import hashlib
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from erp_web.schemas.fulfillment import DomesticParcel


def supported(record):
    host = (urlsplit(record.source.product_url).hostname or "").lower()
    return record.source.source_platform.strip().lower() == "1688" or host == "1688.com" or host.endswith(".1688.com")


def quantity(value):
    try:
        number = Decimal(str(value))
        return int(number) if number.is_finite() and number > 0 and number == number.to_integral_value() else 0
    except (InvalidOperation, ValueError, OverflowError):
        return 0


def propose_parcels(record, order, logistics, normalized):
    match = re.fullmatch(r"/offer/(\d+)\.html", urlsplit(record.source.product_url).path)
    items = order.get("result", {}).get("productItems", [])
    if not match or not record.source.source_sku_id or not isinstance(items, list):
        return [], "缺少商品或 SKU 信息，请确认包裹分配"
    matches = [p for p in items if isinstance(p, dict) and str(p.get("productID", "")) == match[1]
               and str(p.get("skuID", "")) == record.source.source_sku_id]
    if len(matches) != 1 or quantity(matches[0].get("quantity")) != record.quantity:
        return [], "采购 SKU 或数量无法唯一对应，请确认包裹分配"
    entry = str(matches[0].get("subItemIDString") or matches[0].get("subItemID") or "")
    if not entry:
        return [], "未提供订单子项，请确认包裹分配"
    proposals, seen = [], set()
    for row, parcel in zip(logistics["result"], normalized, strict=True):
        goods = row.get("logisticsOrderGoods")
        entries = str(row.get("orderEntryIds") or "").split(",")
        if not isinstance(goods, list) or not goods:
            if entry in entries or not row.get("orderEntryIds"):
                return [], "未提供包裹内商品数量，请确认包裹分配"
            continue
        matched = [g for g in goods if isinstance(g, dict) and str(g.get("tradeOrderItemId", "")) == entry]
        if not matched:
            if entry in entries:
                return [], "包裹商品明细不完整，请确认包裹分配"
            continue
        total = 0
        for good in matched:
            if (str(good.get("tradeOrderId", "")) != record.purchase_order_number.strip()
                    or str(good.get("logisticsId", "")) != parcel["logistics_id"] or not quantity(good.get("quantity"))):
                return [], "包裹商品归属不明确，请确认包裹分配"
            total += quantity(good["quantity"])
        carrier = parcel["company"].strip()
        carrier = next((name for name in ("顺丰", "中通", "圆通", "申通", "韵达", "极兔", "邮政", "京东") if name in carrier), carrier)
        bill = parcel["tracking_number"].strip()
        if not carrier or not bill or bill in seen:
            return [], "运单或承运商信息不完整，请确认包裹分配"
        seen.add(bill)
        identity = hashlib.sha256(f"{record.id}:{bill}".encode()).hexdigest()[:32]
        proposals.append(DomesticParcel(id=identity, line_key=record.line_key, purchase_record_id=record.id,
                                       carrier=carrier, tracking_number=bill, quantity=total).model_dump())
    if sum(p["quantity"] for p in proposals) > record.quantity:
        return [], "包裹数量超过采购数量，请确认包裹分配"
    return proposals, "" if proposals else "尚未提供该 SKU 的发货包裹"
