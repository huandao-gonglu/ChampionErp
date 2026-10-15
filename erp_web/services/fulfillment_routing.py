"""官方交货信息的展示及仓库服务校验；交货点不决定采购收货仓。"""

import hashlib
import json
import unicodedata

from erp_web.schemas.fulfillment import FulfillmentError
from erp_web.services.fulfillment_packages import domestic_waybill_key


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
        # 展示身份用于核对地址备注；不再用于选择或绑定合作仓。
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
    if plan.get("consolidation_service_id") is not None and plan["consolidation_service_id"] not in selected:
        raise FulfillmentError("请将确认的合单服务加入本单服务")
    if core and len(core & selected) != 1:
        raise FulfillmentError("请从该仓库的基础服务中选择一项")
    return [s for s in services["core_data"] + services["optional_data"] if s["id"] in selected]


def consolidation_reason(parcels, plan):
    """同运单承载多个 SKU 仍只算一个国内包裹；合单服务须由用户明确指定。"""
    bills = {domestic_waybill_key(p['tracking_number']) for p in parcels if p.get('tracking_number', '').strip()}
    if len(bills) > 1 and (not plan or not plan.get('consolidation_service_id')
                           or plan['consolidation_service_id'] not in plan['service_ids']):
        return f'本订单有 {len(bills)} 个国内包裹，请在报单方案中选择并确认仓库合单服务'
    return ''
