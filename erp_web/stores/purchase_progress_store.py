"""采购同步快照和短期占位；网络读取期间不持有事务。"""

import json
import time

from erp_web.schemas.orders import utc_iso
from erp_web.schemas.order_procurement import PurchaseRecord, PurchaseItem
from uuid import uuid4


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
        return self.begin_many(order_id, [record_id], accounts, once=once).get(record_id)

    def begin_many(self, order_id, record_ids, accounts, *, once=False):
        """整组在短事务内占位；任一记录忙碌或失效时不启动部分刷新。"""
        started = {}
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for record_id in dict.fromkeys(record_ids):
                self._record(conn, order_id, record_id, accounts)
                row = conn.execute("SELECT * FROM purchase_progress WHERE record_id=?", (record_id,)).fetchone()
                if once and row:
                    continue
                if row and row["lease_until"] > time.time():
                    raise ValueError("该采购记录正在同步，请稍后刷新详情")
                previous = json.loads(row["value"]) if row else {}
                generation = (row["generation"] if row else 0) + 1
                value = {**previous, "state": "syncing", "attempted_at": utc_iso(),
                         "message": "正在同步采购进度", "error": "", "data": previous.get("data")}
                conn.execute("INSERT OR REPLACE INTO purchase_progress VALUES (?,?,?,?)",
                             (record_id, generation, time.time() + 120, json.dumps(value, ensure_ascii=False)))
                started[record_id] = (generation, value)
            conn.commit()
        return started

    def guard(self, conn, order_id, record_id, generation, accounts):
        record = self._record(conn, order_id, record_id, accounts)
        row = conn.execute("SELECT generation FROM purchase_progress WHERE record_id=?", (record_id,)).fetchone()
        if not row or row[0] != generation:
            raise ValueError("采购同步结果已过期，请重新刷新")
        return record

    def finish(self, order_id, record_id, generation, accounts, value):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = PurchaseRecord.model_validate(self.guard(conn, order_id, record_id, generation, accounts))
            products = (value.get("data") or {}).get("products") or []
            if products and all(p["quantity"] for p in products):
                items = []
                for product in products:
                    matches = [item for item in record.items if item.product_id == product["offer_id"] and item.sku == product["sku_id"]]
                    previous = matches[0] if len(matches) == 1 else None
                    items.append(PurchaseItem(
                        id=previous.id if previous else product["item_id"] or uuid4().hex,
                        line_key=previous.line_key if previous else "", line_signature=previous.line_signature if previous else "",
                        product_id=product["offer_id"], sku=product["sku_id"], title=product["name"],
                        quantity=product["quantity"], image_url=product["image_url"], source=previous.source if previous else None))
                record.items = items
                self.procurement._save_record(conn, order_id, record)
            conn.execute("UPDATE purchase_progress SET lease_until=0,value=? WHERE record_id=? AND generation=?",
                         (json.dumps(value, ensure_ascii=False), record_id, generation))
            conn.commit()
