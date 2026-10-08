"""平台交货点身份与跨境仓对应；不同系统的 ID 不直接比较。"""

import hashlib
import json
import unicodedata

from erp_web.schemas.fulfillment import FulfillmentError


def _normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def handover_target(order, bus_identity):
    result = {"key": "", "warehouse_id": "", "name": "", "address": "", "shipment_type": "", "reason": ""}
    if order.platform != "yandex":
        return result
    if not order.handover or order.handover.state != "ready":
        return {**result, "reason": "请先同步 Yandex 订单交货信息"}
    points = {}
    for shipment in order.handover.shipments:
        if shipment.status == "ERROR":
            continue
        point = shipment.destination if shipment.shipment_type == "IMPORT" else shipment.origin if shipment.shipment_type == "WITHDRAW" else None
        if not point or not point.address.strip():
            return {**result, "reason": "订单交货地址不完整，请先同步并核对"}
        # 不使用批次号，允许后续订单复用；同 ID 地址变更时重新确认。
        identity = [order.platform, order.account_id, bus_identity, shipment.shipment_type,
                    point.id, _normalized(point.name), _normalized(point.address)]
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        points[key] = {**result, "key": key, "warehouse_id": point.id, "name": point.name,
                       "address": point.address, "shipment_type": shipment.shipment_type}
    if len(points) != 1:
        return {**result, "reason": "订单交货点不唯一，请先核对平台交货批次" if points else "平台尚未提供订单交货地址"}
    return next(iter(points.values()))


def is_yandex_section(section):
    return _normalized(section["section_name"]) in {"yandex", "yandex market", "яндекс", "яндекс маркет"}


def validate_services(plan, services):
    core = {s["id"] for s in services["core_data"]}
    available = core | {s["id"] for s in services["optional_data"]}
    selected = set(plan["service_ids"])
    if len(selected) != len(plan["service_ids"]) or not selected <= available:
        raise FulfillmentError("服务已变化或存在重复，请重新选择该仓库的服务")
    if core and len(core & selected) != 1:
        raise FulfillmentError("请从该仓库的基础服务中选择一项")
    return [s for s in services["core_data"] + services["optional_data"] if s["id"] in selected]
