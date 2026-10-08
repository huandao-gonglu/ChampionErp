"""采购同步快照和短期占位；网络读取期间不持有事务。"""

import json
import time

from erp_web.schemas.orders import utc_iso


class PurchaseProgressStore:
    def __init__(self, procurement):
        self.procurement = procurement
        self.orders = procurement.orders
        with self.orders.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS purchase_progress (
                record_id TEXT PRIMARY KEY, generation INTEGER NOT NULL,
                lease_until REAL NOT NULL, value TEXT NOT NULL)""")
            conn.commit()

    def _record(self, conn, order_id, record_id, accounts):
        return self.procurement.checked_purchase(conn, order_id, record_id, accounts)

    def begin(self, order_id, record_id, accounts, *, once=False):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._record(conn, order_id, record_id, accounts)
            row = conn.execute("SELECT * FROM purchase_progress WHERE record_id=?", (record_id,)).fetchone()
            if once and row:
                return None
            if row and row["lease_until"] > time.time():
                raise ValueError("该采购记录正在同步，请稍后刷新详情")
            previous = json.loads(row["value"]) if row else {}
            generation = (row["generation"] if row else 0) + 1
            value = {**previous, "state": "syncing", "attempted_at": utc_iso(),
                     "message": "正在同步采购进度", "error": "", "data": previous.get("data")}
            conn.execute("INSERT OR REPLACE INTO purchase_progress VALUES (?,?,?,?)",
                         (record_id, generation, time.time() + 120, json.dumps(value, ensure_ascii=False)))
            conn.commit()
        return generation, value

    def guard(self, conn, order_id, record_id, generation, accounts):
        record = self._record(conn, order_id, record_id, accounts)
        row = conn.execute("SELECT generation FROM purchase_progress WHERE record_id=?", (record_id,)).fetchone()
        if not row or row[0] != generation:
            raise ValueError("采购同步结果已过期，请重新刷新")
        return record

    def competing(self, conn, record):
        return conn.execute("""SELECT COUNT(*) FROM order_purchases
            WHERE status='purchased' AND id<>?
            AND json_extract(record_json,'$.purchase_order_number')=?
            AND json_extract(record_json,'$.source.source_sku_id')=?""",
            (record["id"], record["purchase_order_number"], record["source"]["source_sku_id"])).fetchone()[0] > 0

    def finish(self, order_id, record_id, generation, accounts, value):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.guard(conn, order_id, record_id, generation, accounts)
            conn.execute("UPDATE purchase_progress SET lease_until=0,value=? WHERE record_id=? AND generation=?",
                         (json.dumps(value, ensure_ascii=False), record_id, generation))
            conn.commit()

    def resolve(self, conn, order_id, parcels, accounts):
        """人工分配与包裹保存处于同一事务，并使正在执行的旧查询失效。"""
        self.procurement._order(conn, order_id, accounts)
        rows = conn.execute("""SELECT s.* FROM purchase_progress s JOIN order_purchases p
            ON p.id=s.record_id WHERE p.order_id=? AND p.status='purchased'""", (order_id,)).fetchall()
        for row in rows:
            value = json.loads(row["value"])
            if not value.get("data"):
                continue
            value.update(state="manual", message="国内包裹已人工确认", error="", resolution={
                "logistics": value["data"]["logistics"],
                "parcels": [p for p in parcels if p["purchase_record_id"] == row["record_id"]],
            })
            conn.execute("UPDATE purchase_progress SET generation=generation+1,lease_until=0,value=? WHERE record_id=?",
                         (json.dumps(value, ensure_ascii=False), row["record_id"]))
