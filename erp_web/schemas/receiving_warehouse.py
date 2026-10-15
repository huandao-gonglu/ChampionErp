"""订单收货绑定及采购历史地址；平台交货点不参与仓库身份。"""
import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict, Field
from erp_web.schemas.alibaba_self_purchase import PurchaseAddress
from erp_web.schemas.logistics import Carrier, CARRIERS, LogisticsAddress


def delivery_carrier(order):
    """从已校验归属的进口交货点名称识别承运商，不推定货代承接能力。"""
    handover = order.handover
    if order.platform != 'yandex' or not handover or handover.state != 'ready':
        return '', []
    names = sorted({s.destination.name.strip() if s.destination else ''
                    for s in handover.shipments if s.shipment_type == 'IMPORT' and s.status != 'ERROR'})
    carriers = set()
    for name in names:
        match = re.match(r'^(CEL|UNI|GUOO(?:\s*\(GTA\))?|GBS(?:\s*\(GUOO\))?|URAL|ATC)(?=$|[\s\-–—])', name, re.I)
        carriers.add(re.sub(r'\s+', '', match[1]).upper() if match else '')
    # 多个交货点必须全部可识别且一致；不从卖家仓库、地址或备注猜测。
    carrier = next(iter(carriers)) if len(carriers) == 1 else ''
    return carrier, [name for name in names if name]


def platform_carrier(order):
    name = delivery_carrier(order)[0]
    return next((key for key, label in CARRIERS.items() if label.upper() == name.upper()), None)


def delivery_key(order):
    d = order.delivery
    # 名称只能识别承运商，不能证明运输服务兼容；承接确认仅对本单有效。
    # 更新指纹使旧通用配送标签的确认失效，保留已冻结的收货地址供重新核对。
    values = ['carrier-v2', order.platform, order.account_id, order.order_id,
              d.fulfillment_model or order.fulfillment, d.method_id, d.method_name,
              d.carrier, d.country, delivery_carrier(order)[0]]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


class ReceivingWarehouse(BaseModel):
    revision: int = Field(ge=1)
    section_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)
    warehouse_name: str
    section_name: str
    address: PurchaseAddress
    transport_service: str = Field(min_length=1, max_length=300)
    delivery_key: str
    confirmed_at: str
    address_id: str = ''
    address_revision: int = 0
    address_name: str = ''
    carrier: Carrier | None = None
    shipping_service: str | None = None
    support_rule: dict | None = None


class ReceivingWarehouseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    order_id: str = Field(min_length=1, max_length=500)
    revision: int = Field(ge=0, strict=True)
    address_id: str = Field(min_length=1, max_length=100)
    address_revision: int = Field(gt=0, strict=True)
    section_id: int = Field(gt=0, strict=True)
    delivery_key: str = Field(min_length=1)
    confirmed: bool = Field(strict=True)
    existing_purchase_confirmed: bool = False
    fulfillment_revision: int | None = Field(default=None, ge=0, strict=True, description="兼容已保存请求；收货绑定不再依赖履约版本，无需填写。")


class ReceivingCandidate(LogisticsAddress):
    section_id: int
    frozen: bool = False


class ReceivingWarehouseView(BaseModel):
    ok: bool = True
    order_id: str
    revision: int
    warehouse: ReceivingWarehouse | None
    delivery_key: str
    delivery_name: str
    delivery_evidence: list[str] = Field(default_factory=list)
    blocked_reason: str
    locked: bool = Field(description='当前收货安排已锁定；不能仅根据有采购记录推断锁定或锁定原因。')
    reserved: bool
    can_confirm: bool = Field(description='当前平台状态、账号及操作锁是否允许确认；false 时不能建议直接重绑。仍须选择有效候选并通过保存校验。')
    has_purchases: bool
    binding_stale: bool = Field(default=False, description='已有收货绑定失效；无绑定时为 false，不表示该订单需要继续履约。')
    needs_rebind: bool = Field(default=False, description='待发货订单的已有绑定失效，需要重新绑定；统计重绑待办使用本字段。为 true 仍须检查 can_confirm 和阻断原因，不能绕过原报单或采购锁定。')
    cancelled_fulfillment: bool = Field(default=False, description='本地履约已取消；不代表平台订单仍有效，也不代表需要重新报单。')
    fulfillment_revision: int | None = None
    candidates: list[ReceivingCandidate]
    carrier_detected: Carrier | None = None
    carrier: Carrier | None = None
