"""采购前确定收货仓；人工确认承接范围，复用仓库收货资料。"""
import json

from erp_web.schemas.orders import account_in_scope, OrderSnapshot, OrderView, utc_iso
from erp_web.schemas.receiving_warehouse import ReceivingWarehouseRequest, ReceivingWarehouseView, delivery_carrier, delivery_key, platform_carrier
from erp_web.services.fulfillment_routing import is_yandex_section
from erp_web.services.logistics_settings_service import LogisticsSettingsService
from erp_web.schemas.logistics import CARRIERS, matching_rule
from erp_web.stores.fulfillment_store import FulfillmentStore


class ReceivingWarehouseService:
    def __init__(self, store, client, accounts_provider, detail_provider):
        self.store, self.client = store, client
        self.accounts_provider, self.detail_provider = accounts_provider, detail_provider
        self.logistics = LogisticsSettingsService(store.orders, client)

    def order(self, order_id):
        order = OrderView.model_validate(self.detail_provider(order_id)['order'])
        if not account_in_scope(self.accounts_provider(), order.platform, order.account_id):
            raise ValueError('订单不属于当前店铺')
        return order

    def detail(self, order_id):
        order = self.order(order_id)
        value = self.store.get(order_id)
        with self.store.orders.connect() as conn:
            active, reserved = self.store.purchase_state(conn, order_id)
            remote = self._remote_locked(conn, order_id)
            fulfillment = FulfillmentStore.read(conn, order_id)
            cancelled = bool(fulfillment and fulfillment['fulfillment_status'] == 'CANCELLED')
            binding_stale = self.store.binding_stale(conn, value)
        needs_rebind = binding_stale and order.state == 'pending_shipment'
        reason = ''
        if not value:
            reason = '请先确定采购收货仓库和地址'
        elif value['bus_identity'] != self.client.identity():
            reason = '跨境巴士账号已变化，请核对原收货仓'
        elif value['delivery_key'] != delivery_key(order):
            reason = '订单物流方案已变化，请重新确认原收货仓能否承接'
        locked = bool(reserved or remote or (active and value and not binding_stale))
        carrier_name, evidence = delivery_carrier(order)
        carrier = platform_carrier(order)
        if not carrier:
            reason = '尚未识别平台物流商，请先同步平台交货信息'
        catalog = self.logistics.catalog()
        candidates = []
        for address in self.logistics.store.list(self.client.identity()):
            frozen = bool(active and value and not binding_stale and value.get('address_id') == address['id'])
            if not address['enabled'] or not matching_rule(address, order, carrier):
                continue
            for section in catalog.get('sections', []):
                if not is_yandex_section(section) or not any(w['id'] == address['warehouse_id'] for w in section['storehouse_list']):
                    continue
                candidates.append({**address, 'section_id': section['section_id'], 'frozen': frozen})
        candidates.sort(key=lambda a: (not a['is_default'], a['warehouse_name'], a['name']))
        if value and not reason:
            with self.store.orders.connect() as conn:
                try:
                    self.store.validate(conn, order)
                except ValueError as exc:
                    reason = str(exc)
        if binding_stale and (reserved or remote):
            reason += '；采购或预报正在处理、已创建或结果待核验，须先处理原操作，不能直接重新绑定'
        if order.state == 'cancelled':
            reason = '平台订单已取消，无需重新设置报单绑定，不能继续报单'
        elif order.state in {'shipped', 'delivered'}:
            reason = '平台订单已发货或已送达，不列入报单重绑待办；收货资料仅供历史核对'
        elif order.state != 'pending_shipment':
            reason = '当前平台订单不是待发货状态，暂不支持设置报单绑定'
        return ReceivingWarehouseView.model_validate({'ok': True, 'order_id': order_id, 'revision': value['revision'] if value else 0,
                'warehouse': self.store.public(value), 'delivery_key': delivery_key(order),
                'delivery_name': carrier_name, 'delivery_evidence': evidence, 'carrier_detected': carrier,
                'carrier': carrier, 'candidates': candidates,
                'blocked_reason': reason, 'locked': locked, 'reserved': reserved,
                'can_confirm': bool(carrier) and not reserved and not remote and order.state == 'pending_shipment' and (not value or value['bus_identity'] == self.client.identity()),
                'has_purchases': active, 'binding_stale': binding_stale, 'needs_rebind': needs_rebind,
                'cancelled_fulfillment': cancelled,
                'fulfillment_revision': fulfillment['revision'] if fulfillment else None}).model_dump(mode='json')

    def _remote_locked(self, conn, order_id):
        value = FulfillmentStore.read(conn, order_id)
        if not value or FulfillmentStore.can_restart_cancelled(value, self.client.identity()):
            return False
        return bool(value.get('crossborderbus_order_id') or value.get('create_unknown')
                    or value.get('cancel_requested') or value['busy'] or value['operation'] == 'create'
                    or value['fulfillment_status'] == 'CANCELLED')

    def checked(self, order_id):
        order = self.order(order_id)
        with self.store.orders.connect() as conn:
            value = self.store.validate(conn, order)
        if value['bus_identity'] != self.client.identity():
            raise ValueError('跨境巴士账号已变化，请核对原收货仓')
        return self.store.public(value)

    def save(self, body):
        req = ReceivingWarehouseRequest.model_validate(body)
        order = self.order(req.order_id)
        if order.platform != 'yandex' or (order.delivery.fulfillment_model or order.fulfillment).upper() != 'FBS':
            raise ValueError('当前仅支持 Yandex FBS 采购收货仓')
        if not req.confirmed:
            raise ValueError('请确认收货地址及仓库对本单物流方案的承接能力')
        identity = self.client.identity()
        if not identity:
            raise ValueError('请先完成跨境巴士授权')
        catalog = self.client.catalog()
        section = next((s for s in catalog.get('sections', []) if s['section_id'] == req.section_id and is_yandex_section(s)), None)
        if not section or catalog.get('identity') != identity:
            raise ValueError('所选仓库不在当前账号的 Yandex 合作范围内')
        with self.store.orders.connect() as conn:
            _, reserved = self.store.purchase_state(conn, req.order_id)
        if reserved:
            raise ValueError('采购正在提交或结果待核验，不能修改收货仓')
        with self.store.orders.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT snapshot FROM orders WHERE id=?', (req.order_id,)).fetchone()
            latest = OrderSnapshot.model_validate_json(row[0]) if row else None
            if (not latest or latest.identity != req.order_id or latest.state != 'pending_shipment'
                    or not account_in_scope(self.accounts_provider(), latest.platform, latest.account_id)
                    or self.client.identity() != identity or delivery_key(latest) != req.delivery_key):
                raise ValueError('订单物流方案、状态或账号已变化，请刷新后重试')
            old = self.store.read(conn, req.order_id)
            if (old['revision'] if old else 0) != req.revision:
                raise ValueError('收货仓资料已更新，请刷新后重试')
            active, reserved = self.store.purchase_state(conn, req.order_id)
            if reserved or self._remote_locked(conn, req.order_id):
                raise ValueError('采购正在提交、结果待核验或预报已开始，不能修改收货仓')
            if old and old['bus_identity'] != identity:
                raise ValueError('跨境巴士账号已变化，不能改写原收货仓')
            binding_stale = self.store.binding_stale(conn, old)
            address_config = self.logistics.store.read(conn, req.address_id, identity)
            if not address_config or address_config['revision'] != req.address_revision:
                raise ValueError('收货地址资料已更新，请刷新后重新选择')
            warehouse = next((w for w in section['storehouse_list'] if w['id'] == address_config['warehouse_id']), None)
            if not warehouse:
                raise ValueError('所选仓库不在当前账号的 Yandex 合作范围内')
            if active and old and not binding_stale and (old['section_id'] != req.section_id or old['warehouse_id'] != warehouse['id']):
                raise ValueError('已采购订单不能更换收货仓；请先处理原采购或转寄')
            if not address_config['enabled']:
                raise ValueError('收货地址已停用，请重新选择')
            carrier = platform_carrier(latest)
            if not carrier:
                raise ValueError('尚未识别平台物流商，请先同步平台交货信息')
            rule = matching_rule(address_config, latest, carrier)
            if not rule:
                raise ValueError('收货地址没有符合本单平台物流商的承接规则')
            address = address_config['address']
            if active and old and not binding_stale and old['address'] != address:
                raise ValueError('已采购订单不能改写收货地址；请先处理原采购或转寄')
            if active and not old and not req.existing_purchase_confirmed:
                raise ValueError('请确认已有采购包裹实际寄往此仓库和地址')
            if active and binding_stale and not req.existing_purchase_confirmed:
                raise ValueError('请核对已有采购包裹的收货安排，并确认重新设置报单绑定')
            value = {'revision': req.revision + 1, 'section_id': req.section_id, 'warehouse_id': warehouse['id'],
                     'section_name': section['section_name'], 'warehouse_name': warehouse['name'], 'address': address,
                     'delivery_key': req.delivery_key, 'transport_service': 'Yandex / ' + CARRIERS[carrier],
                     'address_id': address_config['id'], 'address_revision': address_config['revision'], 'address_name': address_config['name'],
                     'carrier': carrier, 'support_rule': rule,
                     'bus_identity': identity, 'confirmed_at': utc_iso()}
            conn.execute('INSERT OR REPLACE INTO order_receiving_warehouses VALUES (?,?)', (req.order_id, json.dumps(value, ensure_ascii=False)))
            conn.commit()
        return self.detail(req.order_id)
