"""合作仓多地址资料；按跨境巴士授权身份隔离，修改使用版本校验。"""
import hashlib
import json
from uuid import uuid4

from erp_web.schemas.logistics import LogisticsAddress


class LogisticsStore:
    def __init__(self, orders):
        self.orders = orders
        with orders.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS logistics_addresses (id TEXT PRIMARY KEY, bus_identity TEXT NOT NULL, value TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS logistics_address_imports (source TEXT PRIMARY KEY)')
            # 旧资料只迁移地址，不把自由文本或某一订单的确认升级为承接能力。
            legacy_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='receiving_warehouse_profiles'").fetchone()
            legacy_rows = conn.execute('SELECT scope,value FROM receiving_warehouse_profiles').fetchall() if legacy_exists else []
            for row in legacy_rows:
                scope, old = json.loads(row['scope']), json.loads(row['value'])
                source = hashlib.sha256(json.dumps([scope[2], old['warehouse_id'], old['address']], sort_keys=True).encode()).hexdigest()
                if conn.execute('SELECT 1 FROM logistics_address_imports WHERE source=?', (source,)).fetchone():
                    continue
                value = LogisticsAddress(id=uuid4().hex, revision=1, warehouse_id=old['warehouse_id'], warehouse_name=old['warehouse_name'], name='原收货地址（待确认承接范围）', address=old['address'], rules=[], updated_at='', migrated=True).model_dump()
                conn.execute('INSERT INTO logistics_addresses VALUES (?,?,?)', (value['id'], scope[2], json.dumps(value, ensure_ascii=False)))
                conn.execute('INSERT INTO logistics_address_imports VALUES (?)', (source,))
            conn.commit()

    @staticmethod
    def read(conn, ident, identity):
        row = conn.execute('SELECT value FROM logistics_addresses WHERE id=? AND bus_identity=?', (ident, identity)).fetchone()
        return LogisticsAddress.model_validate_json(row['value']).model_dump() if row else None

    def list(self, identity):
        with self.orders.connect() as conn:
            rows = conn.execute('SELECT value FROM logistics_addresses WHERE bus_identity=? ORDER BY id', (identity,)).fetchall()
        return [LogisticsAddress.model_validate_json(row['value']).model_dump() for row in rows]

    def save(self, request, identity, warehouse_name, updated_at, identity_provider):
        with self.orders.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if identity != identity_provider():
                raise ValueError('跨境巴士账号已变化，请刷新后重试')
            old = self.read(conn, request.id, identity) if request.id else None
            if (request.id and not old) or (old['revision'] if old else 0) != request.revision:
                raise ValueError('收货地址资料已更新或不属于当前账号，请刷新后重试')
            if old and old['warehouse_id'] != request.warehouse_id:
                raise ValueError('已有地址不能转移到另一合作仓，请在目标合作仓新增地址')
            value = LogisticsAddress(**{**request.model_dump(), 'id': request.id or uuid4().hex, 'revision': request.revision + 1}, warehouse_name=warehouse_name, updated_at=updated_at).model_dump()
            if value['enabled'] and value['is_default']:
                for row in conn.execute('SELECT id,value FROM logistics_addresses WHERE bus_identity=?', (identity,)).fetchall():
                    other = json.loads(row['value'])
                    if other['warehouse_id'] == value['warehouse_id'] and other['id'] != value['id'] and other['is_default']:
                        other.update(is_default=False, revision=other['revision'] + 1, updated_at=updated_at)
                        conn.execute('UPDATE logistics_addresses SET value=? WHERE id=?', (json.dumps(other, ensure_ascii=False), row['id']))
            conn.execute('INSERT OR REPLACE INTO logistics_addresses VALUES (?,?,?)', (value['id'], identity, json.dumps(value, ensure_ascii=False)))
            conn.commit()
        return value
