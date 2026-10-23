"""履约配置及出站操作持久化；创建前原子占位，未知结果等待页面核实。"""

from __future__ import annotations

import hashlib
import json
import time
from uuid import uuid4

from erp_web.schemas.orders import OrderSnapshot, utc_iso
from erp_web.stores.receiving_warehouse_store import ReceivingWarehouseStore
from erp_web.schemas.fulfillment import FulfillmentSummary, normalize_bus_catalog


SCHEMA = """
CREATE TABLE IF NOT EXISTS bus_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fulfillment_warehouse_links (key TEXT PRIMARY KEY, revision INTEGER NOT NULL, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fulfillment_rules (id TEXT PRIMARY KEY, scope TEXT UNIQUE NOT NULL, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fulfillments (
 id TEXT PRIMARY KEY, platform TEXT NOT NULL, account_id TEXT NOT NULL, platform_order_id TEXT NOT NULL,
 value TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
 claim TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
 UNIQUE(platform,account_id,platform_order_id));
"""


class FulfillmentStore:
    def __init__(self, orders):
        self.orders = orders
        self.receiving = ReceivingWarehouseStore(orders)
        with orders.connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def setting(self, key, default=None):
        with self.orders.connect() as conn:
            row = conn.execute("SELECT value FROM bus_settings WHERE key=?", (key,)).fetchone()
        value = json.loads(row[0]) if row else default
        return normalize_bus_catalog(value or {}) if key == "catalog" else value

    def set_setting(self, key, value):
        with self.orders.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO bus_settings VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))
            conn.commit()

    def rules(self):
        with self.orders.connect() as conn:
            return [json.loads(row[0]) for row in conn.execute("SELECT value FROM fulfillment_rules ORDER BY id")]

    def save_rule(self, rule):
        scope = json.dumps([rule[k] for k in ("platform", "account_id", "fulfillment", "platform_warehouse_id", "delivery_method_id", "country")])
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT id FROM fulfillment_rules WHERE scope=?", (scope,)).fetchone()
            if rule["id"]:
                if not conn.execute("SELECT 1 FROM fulfillment_rules WHERE id=?", (rule["id"],)).fetchone():
                    raise ValueError("默认方案已经删除，请重新读取配置。")
                if existing and existing[0] != rule["id"]:
                    raise ValueError("该配送条件已有默认方案，请编辑现有方案。")
            else:
                rule["id"] = existing[0] if existing else uuid4().hex
            conn.execute("INSERT OR REPLACE INTO fulfillment_rules VALUES (?,?,?)", (rule["id"], scope, json.dumps(rule, ensure_ascii=False)))
            conn.commit()
        return rule

    def delete_rule(self, rule_id):
        with self.orders.connect() as conn:
            conn.execute("DELETE FROM fulfillment_rules WHERE id=?", (rule_id,))
            conn.commit()

    def snapshots(self, accounts):
        where = " OR ".join("(platform=? AND account_id=?)" for _ in accounts) or "0"
        args = tuple(v for pair in accounts.items() for v in pair)
        with self.orders.connect() as conn:
            rows = conn.execute(f"SELECT snapshot FROM orders WHERE {where}", args).fetchall()
        return [OrderSnapshot.model_validate_json(row[0]) for row in rows]

    def ensure(self, order):
        key = [order.platform, order.account_id, order.order_id]
        value = {
            "erp_order_id": order.identity, "platform": order.platform,
            "account_id": order.account_id, "platform_order_id": order.order_id,
            "order_number": order.order_id if order.platform == "yandex" else "ERP-" + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:32],
            "crossborderbus_order_id": None, "bus_identity": "", "plan": None, "override": False,
            "parcels": [], "parcels_confirmed": False, "observed_waybills": [], "platform_label": "", "platform_tracking_number": "", "remark": "",
            "label_error": "", "label_attempt_at": "",
            "country": "", "fulfillment_status": "NEW", "operation": "", "error_message": "",
            "last_attempt_at": "", "last_synced_at": "", "next_attempt": 0,
            "cancel_requested": False, "editing_until": 0, "create_unknown": False,
            "cancel_sent": False, "cancel_rejected": False,
        }
        with self.orders.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO fulfillments(id,platform,account_id,platform_order_id,value) VALUES (?,?,?,?,?)", (order.identity, *key, json.dumps(value)))
            row = conn.execute("SELECT * FROM fulfillments WHERE platform=? AND account_id=? AND platform_order_id=?", key).fetchone()
            conn.commit()
        return self._value(row)

    @staticmethod
    def _value(row):
        value = json.loads(row["value"])
        return {**value, "revision": row["revision"], "busy": bool(row["claim"] and row["lease_until"] > time.time()),
                "has_domestic_waybill": any(str(p.get("tracking_number") or "").strip() for p in value.get("parcels", []) if p["included"])}

    def get(self, order_id):
        with self.orders.connect() as conn:
            row = conn.execute("SELECT * FROM fulfillments WHERE id=?", (order_id,)).fetchone()
        if row is None:
            raise ValueError("履约订单不存在")
        return self._value(row)

    def tracked(self, order):
        with self.orders.connect() as conn:
            return conn.execute("SELECT 1 FROM fulfillments WHERE platform=? AND account_id=? AND platform_order_id=?", (order.platform, order.account_id, order.order_id)).fetchone() is not None

    def summaries(self, orders):
        """仅批量读取当前列表内的履约摘要，不创建履约记录。"""
        return {key: FulfillmentSummary.model_validate(value).model_dump(mode="json")
                for key, value in self.query_records(orders).items()}

    def query_records(self, orders):
        """领域内的批量只读事实，包含本地备注和历史报单；不对外返回内部字段。"""
        if not orders:
            return {}
        keys = {(o.platform, o.account_id, o.order_id): o.id for o in orders}
        where = " OR ".join("(platform=? AND account_id=? AND platform_order_id=?)" for _ in keys)
        args = tuple(value for key in keys for value in key)
        with self.orders.connect() as conn:
            rows = conn.execute(f"SELECT * FROM fulfillments WHERE {where}", args).fetchall()
        catalog = self.setting("catalog", {})
        summaries = {}
        for row in rows:
            value = self._value(row)
            plan = value.get("plan") or {}
            # 账号切换后不能把新账号目录中的同号仓库套用到旧履约单。
            sections = catalog.get("sections", []) if catalog.get("identity") == value.get("bus_identity") else []
            section = next((s for s in sections if s["section_id"] == plan.get("section_id")), {})
            warehouse = next((w for w in section.get("storehouse_list", []) if w["id"] == plan.get("warehouse_id")), {})
            packages = {}
            for parcel in value.get("parcels", []):
                number = str(parcel.get("tracking_number") or "").strip()
                if number and parcel["included"]:
                    packages.setdefault(number.upper(), {"carrier": parcel.get("carrier", ""), "tracking_number": number})
            summary = {**value, "section_name": section.get("section_name", ""), "warehouse_name": warehouse.get("name", ""),
                       "registered_packages": list(packages.values()), "consolidation_service_id": plan.get("consolidation_service_id"),
                       "selected_services": [s for s in value.get("selected_services", []) if s["id"] in plan.get("service_ids", [])]}
            summaries[keys[(row["platform"], row["account_id"], row["platform_order_id"])]] = summary
        return summaries

    def change(self, order_id, revision, changes, *, allow_busy=False, guard=None, restart_identity=None):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM fulfillments WHERE id=?", (order_id,)).fetchone()
            if row is None or row["revision"] != revision:
                raise ValueError("履约资料已更新，请刷新后再操作")
            if not allow_busy and row["claim"] and row["lease_until"] > time.time():
                raise ValueError("履约操作正在执行，请稍后重试")
            if guard is not None:
                guard(conn)
            value = (self.archive_cancelled(conn, self._value(row), restart_identity)
                     if restart_identity is not None else json.loads(row["value"]))
            value.update(changes)
            conn.execute("UPDATE fulfillments SET value=?,revision=revision+1,claim=?,lease_until=? WHERE id=?",
                         (json.dumps(value, ensure_ascii=False),
                          '' if restart_identity is not None else row['claim'],
                          0 if restart_identity is not None else row['lease_until'], order_id))
            conn.commit()
        return self.get(order_id)

    def claim(self, order_id, action, revision):
        now = time.time()
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM fulfillments WHERE id=?", (order_id,)).fetchone()
            if row["revision"] != revision:
                return None
            if row["claim"] and row["lease_until"] > now:
                return None
            value = json.loads(row["value"])
            if action == "create" and (value["editing_until"] > now or value["cancel_requested"] or value.get('awaiting_resubmission')):
                return None
            # 失效的创建占位不能回到待创建，即使进程在写回回执前崩溃。
            if value["operation"] == "create" or value["create_unknown"]:
                value["create_unknown"] = True
                action = "reconcile"
            if action == "create" and value['platform'] == 'yandex':
                snapshot = conn.execute('SELECT snapshot FROM orders WHERE id=?', (order_id,)).fetchone()
                if not snapshot:
                    return None
                order = OrderSnapshot.model_validate_json(snapshot[0])
                try:
                    receiving = self.receiving.validate(conn, order)
                except ValueError:
                    return None
                plan = value.get('plan') or {}
                if (order.state != 'pending_shipment' or value.get('receiving_revision') != receiving['revision']
                        or any(plan.get(k) != receiving[k] for k in ('section_id', 'warehouse_id'))):
                    return None
                value['receiving_warehouse_snapshot'] = self.receiving.public(receiving)
            if action == "create":
                value["create_unknown"] = True
            if action == "associate":
                # 进程中断后不能自动再次发送关联写请求。
                value["platform_link_state"] = "unknown"
            value.update(operation=action, last_attempt_at=utc_iso(), cancel_at_claim=value["cancel_requested"])
            token = uuid4().hex
            conn.execute("UPDATE fulfillments SET value=?,claim=?,lease_until=?,revision=revision+1 WHERE id=?", (json.dumps(value, ensure_ascii=False), token, now + 180, order_id))
            conn.commit()
        return self.get(order_id), token

    @staticmethod
    def read(conn, order_id):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fulfillments'").fetchone():
            return None
        row = conn.execute('SELECT * FROM fulfillments WHERE id=?', (order_id,)).fetchone()
        return FulfillmentStore._value(row) if row else None

    @staticmethod
    def can_restart_cancelled(value, identity):
        return bool(value and value['fulfillment_status'] == 'CANCELLED'
                    and not value['busy'] and not value['create_unknown'] and value['operation'] != 'create'
                    and identity and (not value['bus_identity'] or value['bus_identity'] == identity))

    @staticmethod
    def archive_cancelled(conn, value, identity):
        """在保存新方案的事务内生成归档与新尝试；仓库绑定保持不变。"""
        if not FulfillmentStore.can_restart_cancelled(value, identity):
            raise ValueError('原履约状态已变化，请刷新后重新设置报单方案')
        receiving = value.get('receiving_warehouse_snapshot')
        if not receiving:
            previous = ReceivingWarehouseStore.read(conn, value['erp_order_id'])
            # 旧数据没有报单快照时，只使用仍与旧方案一致的绑定，不能把新仓库记到旧单。
            if (previous and previous['revision'] == value.get('receiving_revision')
                    and all(previous[k] == (value.get('plan') or {}).get(k) for k in ('section_id', 'warehouse_id'))):
                receiving = ReceivingWarehouseStore.public(previous)
        value = {k: v for k, v in value.items() if k not in {'revision', 'busy', 'has_domestic_waybill'}}
        history = value.get('attempt_history', [])
        history.append({**{k: v for k, v in value.items() if k != 'attempt_history'},
                        'archived_at': utc_iso(), 'receiving_warehouse': receiving})
        value.update(attempt_history=history, crossborderbus_order_id=None, plan=None, override=False,
                     receiving_revision=0, selected_services=[], fulfillment_status='NEW',
                     operation='', error_message='', last_synced_at='', last_attempt_at='', next_attempt=0,
                     cancel_requested=False, cancel_sent=False, cancel_rejected=False, cancel_at_claim=False,
                     create_unknown=False, warehouse_locked=False, update_pending=False,
                     platform_link_state='', platform_link_error='', editing_until=0,
                     receiving_warehouse_snapshot=None, manual_submit=False, awaiting_resubmission=True)
        return value

    def finish(self, order_id, token, changes, *, expected_revision=None):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM fulfillments WHERE id=? AND claim=?", (order_id, token)).fetchone()
            if row is None:
                return
            value = json.loads(row["value"])
            if expected_revision is not None and row["revision"] != expected_revision:
                # 网络期间取消或资料变更时，旧回执不能覆盖新资料；仍释放自己的占位。
                changes = {}
            value.update(changes)
            if value["cancel_requested"] and not value.get("cancel_at_claim") and value["fulfillment_status"] not in {"CANCELLED", "SHIPPED"}:
                value["next_attempt"] = 0
            value["operation"] = ""
            conn.execute("UPDATE fulfillments SET value=?,claim='',lease_until=0,revision=revision+1 WHERE id=? AND claim=?", (json.dumps(value, ensure_ascii=False), order_id, token))
            conn.commit()

    def checkpoint(self, order_id, token, changes):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM fulfillments WHERE id=? AND claim=?", (order_id, token)).fetchone()
            if not row:
                raise ValueError("履约操作占位已失效，本次发送停止。")
            value = json.loads(row[0])
            value.update(changes)
            conn.execute("UPDATE fulfillments SET value=?,revision=revision+1 WHERE id=? AND claim=?", (json.dumps(value), order_id, token))
            conn.commit()
