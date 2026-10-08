"""履约配置及出站操作持久化；创建前原子占位，未知结果等待页面核实。"""

from __future__ import annotations

import hashlib
import json
import time
from uuid import uuid4

from erp_web.schemas.orders import OrderSnapshot, utc_iso
from erp_web.schemas.fulfillment import FulfillmentSummary, normalize_bus_catalog


SCHEMA = """
CREATE TABLE IF NOT EXISTS bus_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
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
            "order_number": "ERP-" + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:32],
            "crossborderbus_order_id": None, "bus_identity": "", "plan": None, "override": False,
            "parcels": [], "platform_label": "", "platform_tracking_number": "",
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
        return {**json.loads(row["value"]), "revision": row["revision"], "busy": bool(row["claim"] and row["lease_until"] > time.time())}

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
        if not orders:
            return {}
        keys = {(o.platform, o.account_id, o.order_id): o.id for o in orders}
        where = " OR ".join("(platform=? AND account_id=? AND platform_order_id=?)" for _ in keys)
        args = tuple(value for key in keys for value in key)
        with self.orders.connect() as conn:
            rows = conn.execute(f"SELECT * FROM fulfillments WHERE {where}", args).fetchall()
        return {
            keys[(row["platform"], row["account_id"], row["platform_order_id"])]:
            FulfillmentSummary.model_validate(self._value(row)).model_dump(mode="json")
            for row in rows
        }

    def change(self, order_id, revision, changes, *, allow_busy=False, guard=None):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM fulfillments WHERE id=?", (order_id,)).fetchone()
            if row is None or row["revision"] != revision:
                raise ValueError("履约资料已更新，请刷新后再操作")
            if not allow_busy and row["claim"] and row["lease_until"] > time.time():
                raise ValueError("履约操作正在执行，请稍后重试")
            if guard is not None:
                guard(conn)
            value = json.loads(row["value"])
            value.update(changes)
            conn.execute("UPDATE fulfillments SET value=?,revision=revision+1 WHERE id=?", (json.dumps(value, ensure_ascii=False), order_id))
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
            if action == "create" and (value["editing_until"] > now or value["cancel_requested"]):
                return None
            # 失效的创建占位不能回到待创建，即使进程在写回回执前崩溃。
            if value["operation"] == "create" or value["create_unknown"]:
                value["create_unknown"] = True
                action = "reconcile"
            if action == "create":
                value["create_unknown"] = True
            value.update(operation=action, last_attempt_at=utc_iso(), cancel_at_claim=value["cancel_requested"])
            token = uuid4().hex
            conn.execute("UPDATE fulfillments SET value=?,claim=?,lease_until=?,revision=revision+1 WHERE id=?", (json.dumps(value, ensure_ascii=False), token, now + 180, order_id))
            conn.commit()
        return self.get(order_id), token

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
