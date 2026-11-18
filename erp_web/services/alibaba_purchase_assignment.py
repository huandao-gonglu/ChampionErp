"""采购物流按实际运单带入；件数缺失可登记，报单前再补齐。"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import hashlib

from erp_web.schemas.fulfillment import DomesticParcel


def supported(record):
    return record.source_platform == "1688"


def quantity(value):
    try:
        number = Decimal(str(value))
        return int(number) if number.is_finite() and number > 0 and number == number.to_integral_value() else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def parcel_quantity(shipments, order_number):
    """只累计该运单的明确商品件数；重复子项去重，归属或数量冲突时留空。"""
    counts = {}
    for row_index, shipment in enumerate(shipments):
        logistics_id = shipment.parcel["logistics_id"]
        if not shipment.items:
            return None
        for item_index, item in enumerate(shipment.items):
            count = item.quantity
            if count is None or (item.order_number and item.order_number != order_number):
                return None
            if item.logistics_id and logistics_id and item.logistics_id != logistics_id:
                return None
            identity = (logistics_id, item.item_id)
            if not all(identity):
                # 多段回执没有稳定子项身份时，不能判断件数是否重复。
                if len(shipments) > 1:
                    return None
                identity = (row_index, item_index)
            if identity in counts and counts[identity] != count:
                return None
            counts[identity] = count
    return sum(counts.values()) if counts else None


def propose_parcels(record, shipments, normalized):
    groups = defaultdict(list)
    for shipment, parcel in zip(shipments, normalized, strict=True):
        bill = parcel["tracking_number"].strip().upper()
        if bill:
            groups[bill].append((shipment, parcel))
    proposals = []
    for bill, group in groups.items():
        carriers = {parcel["company"] for _, parcel in group if parcel["company"]}
        identity = hashlib.sha256(f"{record.id}:{bill}".encode()).hexdigest()[:32]
        proposals.append(DomesticParcel(
            id=identity, carrier=next(iter(carriers)) if len(carriers) == 1 else "", tracking_number=bill,
            quantity=parcel_quantity([row for row, _ in group], record.purchase_order_number),
            source_platform=record.source_platform, purchase_order_number=record.purchase_order_number,
        ).model_dump())
    return proposals, "" if proposals else "采购平台暂未提供国内运单"
