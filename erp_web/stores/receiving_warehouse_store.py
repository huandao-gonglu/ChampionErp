"""采购与履约共用收货仓；使用订单数据库事务阻止采购期间换仓。"""
import json

from erp_web.schemas.receiving_warehouse import ReceivingWarehouse, delivery_key, platform_carrier
from erp_web.schemas.logistics import matching_rule

SCHEMA = """
CREATE TABLE IF NOT EXISTS order_receiving_warehouses (
 order_id TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS receiving_warehouse_profiles (
 scope TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class ReceivingWarehouseStore:
    def __init__(self, orders):
        self.orders = orders
        with orders.connect() as conn:
            conn.executescript(SCHEMA)

    @staticmethod
    def read(conn, order_id):
        row = conn.execute("SELECT value FROM order_receiving_warehouses WHERE order_id=?", (order_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def get(self, order_id):
        with self.orders.connect() as conn:
            return self.read(conn, order_id)

    @staticmethod
    def public(value):
        return ReceivingWarehouse.model_validate(value).model_dump() if value else None

    @staticmethod
    def validate(conn, order, expected=None):
        value = ReceivingWarehouseStore.read(conn, order.identity)
        if not value:
            raise ValueError("请先在商品与采购中确定收货仓库和地址")
        if value['delivery_key'] != delivery_key(order):
            raise ValueError("订单物流方案已变化，请重新核对收货仓的承接范围")
        active = ReceivingWarehouseStore.purchase_state(conn, order.identity)[0]
        if not value.get('address_id') and not active:
            raise ValueError('请在物流设置中确认承接范围，并重新选择采购收货地址')
        carrier = platform_carrier(order)
        if not carrier:
            raise ValueError('尚未识别平台物流商，请先同步平台交货信息')
        if value.get('support_rule') and not matching_rule({'rules': [value['support_rule']]}, order, carrier):
            raise ValueError('订单平台物流商已变化，请重新核对收货仓')
        if value.get('address_id') and not active:
            row = conn.execute('SELECT value FROM logistics_addresses WHERE id=? AND bus_identity=?', (value['address_id'], value['bus_identity'])).fetchone()
            configured = json.loads(row['value']) if row else None
            if not configured or not configured['enabled'] or configured['revision'] != value['address_revision']:
                raise ValueError('收货地址或承接规则已更新，请在采购前重新选择地址')
        if expected is not None and ReceivingWarehouseStore.public(value) != expected:
            raise ValueError("收货仓库或地址已变化，请重新预览或刷新后确认")
        return value

    @staticmethod
    def purchase_state(conn, order_id):
        # 某些只读履约场景尚未初始化采购表。
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        active = bool('order_purchases' in tables and conn.execute("SELECT 1 FROM order_purchases WHERE order_id=? AND status='purchased' LIMIT 1", (order_id,)).fetchone())
        reserved = bool('order_purchase_reservations' in tables and conn.execute("SELECT 1 FROM order_purchase_reservations WHERE order_id=? AND state='reserved' LIMIT 1", (order_id,)).fetchone())
        return active, reserved
