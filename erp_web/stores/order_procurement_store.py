"""订单采购持久化：事务校验当前订单、乐观版本及采购数量，历史来源不随关联变更。"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from erp_web.schemas.order_procurement import (
    ProcurementSource,
    PurchaseRecord,
    SalesSkuBinding,
    order_line_key,
)
from erp_web.schemas.orders import OrderSnapshot, OrderView, utc_iso
from erp_web.stores.order_notification_store import OrderNotificationStore
from erp_web.stores.purchase_progress_store import PurchaseProgressStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS sales_sku_bindings (
 id TEXT PRIMARY KEY, platform TEXT NOT NULL, store_identity TEXT NOT NULL,
 seller_sku TEXT NOT NULL, binding_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS sales_sku_scope ON sales_sku_bindings(platform,store_identity,seller_sku);
CREATE TABLE IF NOT EXISTS order_sources (
 order_id TEXT NOT NULL, line_key TEXT NOT NULL, revision INTEGER NOT NULL,
 line_signature TEXT NOT NULL, source_json TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(order_id,line_key));
CREATE TABLE IF NOT EXISTS order_purchases (
 id TEXT PRIMARY KEY, order_id TEXT NOT NULL, line_key TEXT NOT NULL,
 request_id TEXT NOT NULL, request_hash TEXT NOT NULL, line_signature TEXT NOT NULL,
 quantity INTEGER NOT NULL, status TEXT NOT NULL, record_json TEXT NOT NULL,
 UNIQUE(order_id,request_id));
CREATE INDEX IF NOT EXISTS purchases_order ON order_purchases(order_id,status);
"""


def line_signature(line):
    return json.dumps([line.sku, line.remote_id, line.variant_id], ensure_ascii=False)


class OrderProcurementStore:
    def __init__(self, orders: OrderNotificationStore):
        self.orders = orders
        with orders.connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()
        self.purchase_progress = PurchaseProgressStore(self)

    @staticmethod
    def _order(conn, order_id, accounts):
        row = conn.execute(
            "SELECT snapshot,checked_at FROM orders WHERE id=?", (order_id,)
        ).fetchone()
        if not row:
            raise ValueError("订单不存在")
        snapshot = OrderSnapshot.model_validate_json(row["snapshot"])
        if accounts.get(snapshot.platform) != snapshot.account_id:
            raise ValueError("订单不属于当前店铺")
        return OrderView(
            **snapshot.model_dump(), id=order_id, checked_at=row["checked_at"]
        )

    @staticmethod
    def _line(order, key):
        rows = [line for line in order.items if order_line_key(line) == key]
        if not key or len(rows) != 1:
            raise ValueError("订单行身份不明确，不能提交采购操作")
        return rows[0]

    def order(self, order_id, accounts):
        with self.orders.connect() as conn:
            return self._order(conn, order_id, accounts)

    def checked_purchase(self, conn, order_id, record_id, accounts):
        order = self._order(conn, order_id, accounts)
        row = conn.execute("SELECT * FROM order_purchases WHERE order_id=? AND id=?", (order_id, record_id)).fetchone()
        if not row:
            raise ValueError("采购记录不存在或不属于该订单")
        if row["status"] != "purchased":
            raise ValueError("采购记录已作废，不能查询")
        if row["line_signature"] != line_signature(self._line(order, row["line_key"])):
            raise ValueError("订单商品身份已变化，请核对采购记录")
        return json.loads(row["record_json"])

    def purchase_record(self, order_id, record_id, accounts):
        with self.orders.connect() as conn:
            conn.execute("BEGIN")
            return PurchaseRecord.model_validate(self.checked_purchase(conn, order_id, record_id, accounts))

    def add_bindings(self, bindings: list[SalesSkuBinding]):
        with self.orders.connect() as conn:
            for binding in bindings:
                conn.execute(
                    """INSERT INTO sales_sku_bindings VALUES (?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET binding_json=json_set(
                        sales_sku_bindings.binding_json, '$.image_url', json_extract(excluded.binding_json, '$.image_url'))
                    WHERE COALESCE(json_extract(sales_sku_bindings.binding_json, '$.image_url'), '')=''
                    AND COALESCE(json_extract(excluded.binding_json, '$.image_url'), '')!=''""",
                    (
                        binding.identity,
                        binding.platform,
                        binding.store_identity,
                        binding.seller_sku,
                        binding.model_dump_json(),
                    ),
                )
            conn.commit()

    def candidates(self, platform, store_identity, line):
        if not line.sku:
            return []
        with self.orders.connect() as conn:
            rows = conn.execute(
                "SELECT id,binding_json FROM sales_sku_bindings WHERE platform=? AND store_identity=? AND seller_sku=?",
                (platform, store_identity, line.sku),
            ).fetchall()
        result = []
        for row in rows:
            value = SalesSkuBinding.model_validate_json(row["binding_json"])
            # 远端身份可消除同 SKU 的歧义；缺少证据不能反过来猜身份。
            if value.remote_id and line.remote_id and value.remote_id != line.remote_id:
                continue
            if (
                value.variant_id
                and line.variant_id
                and value.variant_id != line.variant_id
            ):
                continue
            result.append(value.model_copy(update={"id": row["id"]}))
        return result

    def line_state(self, order_id, key, accounts):
        with self.orders.connect() as conn:
            conn.execute("BEGIN")
            order = self._order(conn, order_id, accounts)
            line = self._line(order, key)
            row = conn.execute(
                "SELECT * FROM order_sources WHERE order_id=? AND line_key=?",
                (order_id, key),
            ).fetchone()
            records = conn.execute(
                "SELECT p.*, s.value AS progress_json FROM order_purchases p LEFT JOIN purchase_progress s ON s.record_id=p.id WHERE p.order_id=? AND p.line_key=? ORDER BY p.rowid",
                (order_id, key),
            ).fetchall()
            selected = dict(row) if row else None
            purchased = sum(
                r["quantity"]
                for r in records
                if r["status"] == "purchased"
                and r["line_signature"] == line_signature(line)
            )
            return (
                selected,
                [PurchaseRecord.model_validate({**json.loads(r["record_json"]), "progress": json.loads(r["progress_json"]) if r["progress_json"] else None}) for r in records],
                purchased,
            )

    def progress(self, order):
        keys = [order_line_key(line) for line in order.items]
        if (
            not keys
            or not all(keys)
            or len(set(keys)) != len(keys)
            or any(line.quantity <= 0 for line in order.items)
        ):
            return "unknown"
        with self.orders.connect() as conn:
            rows = conn.execute(
                "SELECT line_key,line_signature,quantity FROM order_purchases WHERE order_id=? AND status='purchased'",
                (order.id,),
            ).fetchall()
        quantities = []
        for line in order.items:
            quantities.append(
                sum(
                    row["quantity"]
                    for row in rows
                    if row["line_key"] == order_line_key(line)
                    and row["line_signature"] == line_signature(line)
                )
            )
        if all(done >= line.quantity for done, line in zip(quantities, order.items)):
            return "purchased"
        return "partial" if any(quantities) else "unpurchased"

    def select(self, request, source: ProcurementSource, accounts):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            order = self._order(conn, request.order_id, accounts)
            line = self._line(order, request.line_key)
            if order.state in {"cancelled", "delivered"}:
                raise ValueError("已取消或已送达订单不能更改采购来源")
            row = conn.execute(
                "SELECT revision FROM order_sources WHERE order_id=? AND line_key=?",
                (request.order_id, request.line_key),
            ).fetchone()
            if (row[0] if row else 0) != request.revision:
                raise ValueError("采购来源已被更新，请刷新后重新确认")
            conn.execute(
                "INSERT OR REPLACE INTO order_sources VALUES (?,?,?,?,?,?)",
                (
                    request.order_id,
                    request.line_key,
                    request.revision + 1,
                    line_signature(line),
                    source.model_dump_json(),
                    utc_iso(),
                ),
            )
            conn.commit()

    def purchase(self, request, accounts):
        digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            order = self._order(conn, request.order_id, accounts)
            old = conn.execute(
                "SELECT * FROM order_purchases WHERE order_id=? AND request_id=?",
                (request.order_id, request.request_id),
            ).fetchone()
            if old:
                if old["request_hash"] != digest:
                    raise ValueError("重复请求标识对应不同采购内容")
                return PurchaseRecord.model_validate_json(old["record_json"])
            if order.state != "pending_shipment":
                raise ValueError("只有待发货订单可新增采购记录")
            line = self._line(order, request.line_key)
            source = conn.execute(
                "SELECT * FROM order_sources WHERE order_id=? AND line_key=?",
                (request.order_id, request.line_key),
            ).fetchone()
            if (
                not source
                or source["revision"] != request.revision
                or source["line_signature"] != line_signature(line)
            ):
                raise ValueError("请先确认当前订单规格的采购来源")
            purchased = conn.execute(
                "SELECT COALESCE(SUM(quantity),0) FROM order_purchases WHERE order_id=? AND line_key=? AND line_signature=? AND status='purchased'",
                (request.order_id, request.line_key, line_signature(line)),
            ).fetchone()[0]
            if purchased + request.quantity > line.quantity:
                raise ValueError("采购数量超过订单剩余数量；请先检查已有采购记录")
            number = request.purchase_order_number.strip()
            if not number:
                raise ValueError("请填写采购单号")
            value = PurchaseRecord(
                id=uuid4().hex,
                line_key=request.line_key,
                request_id=request.request_id,
                quantity=request.quantity,
                purchase_order_number=number,
                source=ProcurementSource.model_validate_json(source["source_json"]),
                created_at=utc_iso(),
            )
            conn.execute(
                "INSERT INTO order_purchases VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    value.id,
                    request.order_id,
                    request.line_key,
                    request.request_id,
                    digest,
                    line_signature(line),
                    request.quantity,
                    "purchased",
                    value.model_dump_json(),
                ),
            )
            conn.commit()
            return value

    def cancel(self, request, accounts):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._order(conn, request.order_id, accounts)
            row = conn.execute(
                "SELECT record_json FROM order_purchases WHERE id=? AND order_id=?",
                (request.record_id, request.order_id),
            ).fetchone()
            if not row:
                raise ValueError("采购记录不存在")
            record = PurchaseRecord.model_validate_json(row[0])
            if record.status != "cancelled":
                record.status, record.cancelled_at = "cancelled", utc_iso()
                conn.execute(
                    "UPDATE order_purchases SET status='cancelled',record_json=? WHERE id=?",
                    (record.model_dump_json(), record.id),
                )
            conn.commit()
