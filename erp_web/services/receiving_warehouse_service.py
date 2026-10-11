"""采购前确定收货仓；人工确认承接范围，复用仓库收货资料。"""
import json
import time

from erp_web.schemas.orders import OrderSnapshot, OrderView, utc_iso
from erp_web.schemas.receiving_warehouse import ReceivingWarehouseRequest, ReceivingWarehouseView, delivery_carrier, delivery_key, platform_carrier
from erp_web.services.fulfillment_routing import is_yandex_section
from erp_web.services.logistics_settings_service import LogisticsSettingsService
from erp_web.schemas.logistics import CARRIERS, matching_rule


class ReceivingWarehouseService:
    def __init__(self, store, client, accounts_provider, detail_provider):
        self.store, self.client = store, client
        self.accounts_provider, self.detail_provider = accounts_provider, detail_provider
        self.logistics = LogisticsSettingsService(store.orders, client)

    def order(self, order_id):
        order = OrderView.model_validate(self.detail_provider(order_id)['order'])
        if self.accounts_provider().get(order.platform) != order.account_id:
            raise ValueError('订单不属于当前店铺')
        return order

    def detail(self, order_id):
        order = self.order(order_id)
        value = self.store.get(order_id)
        with self.store.orders.connect() as conn:
            active, reserved = self.store.purchase_state(conn, order_id)
            remote = self._remote_locked(conn, order_id)
        reason = ''
        if not value:
            reason = '请先确定采购收货仓库和地址'
        elif value['bus_identity'] != self.client.identity():
            reason = '跨境巴士账号已变化，请核对原收货仓'
        elif value['delivery_key'] != delivery_key(order):
            reason = '订单物流方案已变化，请重新确认原收货仓能否承接'
        locked = bool(reserved or remote or (active and value))
        carrier_name, evidence = delivery_carrier(order)
        carrier = platform_carrier(order)
        if not carrier:
            reason = '尚未识别平台物流商，请先同步平台交货信息'
        catalog = self.logistics.catalog()
        candidates = []
        for address in self.logistics.store.list(self.client.identity()):
            frozen = bool(active and value and value.get('address_id') == address['id'])
            if (not address['enabled'] and not frozen) or not matching_rule(address, order, carrier):
                continue
            for section in catalog.get('sections', []):
                if not is_yandex_section(section) or not any(w['id'] == address['warehouse_id'] for w in section['storehouse_list']):
                    continue
                candidates.append({**address, 'section_id': section['section_id'], 'frozen': frozen,
                                   **({'address': value['address']} if frozen else {})})
        candidates.sort(key=lambda a: (not a['is_default'], a['warehouse_name'], a['name']))
        if value and not reason:
            with self.store.orders.connect() as conn:
                try:
                    self.store.validate(conn, order)
                except ValueError as exc:
                    reason = str(exc)
        return ReceivingWarehouseView.model_validate({'ok': True, 'order_id': order_id, 'revision': value['revision'] if value else 0,
                'warehouse': self.store.public(value), 'delivery_key': delivery_key(order),
                'delivery_name': carrier_name, 'delivery_evidence': evidence, 'carrier_detected': carrier,
                'carrier': carrier, 'candidates': candidates,
                'blocked_reason': reason, 'locked': locked, 'reserved': reserved,
                'can_confirm': bool(carrier) and not reserved and not remote and order.state == 'pending_shipment' and (not value or value['bus_identity'] == self.client.identity()),
                'has_purchases': active}).model_dump(mode='json')

    @staticmethod
    def _remote_locked(conn, order_id):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fulfillments'").fetchone():
            return False
        row = conn.execute('SELECT value,claim,lease_until FROM fulfillments WHERE id=?', (order_id,)).fetchone()
        if not row:
            return False
        value = json.loads(row['value'])
        return bool(value.get('crossborderbus_order_id') or value.get('create_unknown') or value.get('cancel_requested') or (row['claim'] and row['lease_until'] > time.time()))

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
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT snapshot FROM orders WHERE id=?', (req.order_id,)).fetchone()
            latest = OrderSnapshot.model_validate_json(row[0]) if row else None
            if (not latest or latest.identity != req.order_id or latest.state != 'pending_shipment'
                    or self.accounts_provider().get(latest.platform) != latest.account_id
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
            address_config = self.logistics.store.read(conn, req.address_id, identity)
            if not address_config or address_config['revision'] != req.address_revision:
                raise ValueError('收货地址资料已更新，请刷新后重新选择')
            warehouse = next((w for w in section['storehouse_list'] if w['id'] == address_config['warehouse_id']), None)
            if not warehouse:
                raise ValueError('所选仓库不在当前账号的 Yandex 合作范围内')
            if active and old and (old['section_id'] != req.section_id or old['warehouse_id'] != warehouse['id']):
                raise ValueError('已采购订单不能更换收货仓；请先处理原采购或转寄')
            frozen = bool(active and old and old.get('address_id') == address_config['id'])
            if not address_config['enabled'] and not frozen:
                raise ValueError('收货地址已停用，请重新选择')
            carrier = platform_carrier(latest)
            if not carrier:
                raise ValueError('尚未识别平台物流商，请先同步平台交货信息')
            rule = matching_rule(address_config, latest, carrier)
            if not rule:
                raise ValueError('收货地址没有符合本单平台物流商的承接规则')
            address = old['address'] if frozen else address_config['address']
            if active and old and old['address'] != address:
                raise ValueError('已采购订单不能改写收货地址；请先处理原采购或转寄')
            if active and not old and not req.existing_purchase_confirmed:
                raise ValueError('请确认已有采购包裹实际寄往此仓库和地址')
            value = {'revision': req.revision + 1, 'section_id': req.section_id, 'warehouse_id': warehouse['id'],
                     'section_name': section['section_name'], 'warehouse_name': warehouse['name'], 'address': address,
                     'delivery_key': req.delivery_key, 'transport_service': 'Yandex / ' + CARRIERS[carrier],
                     'address_id': address_config['id'], 'address_revision': address_config['revision'], 'address_name': address_config['name'],
                     'carrier': carrier, 'support_rule': rule,
                     'bus_identity': identity, 'confirmed_at': utc_iso()}
            conn.execute('INSERT OR REPLACE INTO order_receiving_warehouses VALUES (?,?)', (req.order_id, json.dumps(value, ensure_ascii=False)))
            conn.commit()
        return self.detail(req.order_id)
