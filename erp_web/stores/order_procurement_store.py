"""订单级采购单、来源确认及自动采购请求占位；包裹由履约独立管理。"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from uuid import uuid4
from datetime import datetime

from erp_web.schemas.order_procurement import (
    ProcurementSource,
    PurchaseRecord,
    PurchaseItem,
    SalesSkuBinding,
    order_line_key,
)
from erp_web.schemas.orders import OrderSnapshot, OrderView, utc_iso
from erp_web.stores.order_notification_store import OrderNotificationStore
from erp_web.stores.purchase_progress_store import PurchaseProgressStore
from erp_web.stores.receiving_warehouse_store import ReceivingWarehouseStore

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
 id TEXT PRIMARY KEY, order_id TEXT NOT NULL, source_platform TEXT NOT NULL,
 purchase_order_number TEXT NOT NULL, status TEXT NOT NULL, record_json TEXT NOT NULL,
 UNIQUE(order_id,source_platform,purchase_order_number));
CREATE TABLE IF NOT EXISTS order_purchase_requests (
 order_id TEXT NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
 record_id TEXT NOT NULL REFERENCES order_purchases(id), PRIMARY KEY(order_id,request_id));
CREATE TABLE IF NOT EXISTS order_purchase_reservations (
 id TEXT PRIMARY KEY, order_id TEXT NOT NULL, line_key TEXT NOT NULL,
 quantity INTEGER NOT NULL, binding_json TEXT NOT NULL, state TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS purchases_order ON order_purchases(order_id,status);
"""


def _tracking_time(value):
    """仅比较成功快照的更新时间，失败尝试时间不推进包裹状态。"""
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else 0
    except (ValueError, TypeError, AttributeError):
        return 0


def line_signature(line):
    return json.dumps([line.sku, line.remote_id, line.variant_id], ensure_ascii=False)


class OrderProcurementStore:
    def __init__(self, orders: OrderNotificationStore):
        self.orders = orders
        self.receiving = ReceivingWarehouseStore(orders)
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

    def iter_query_facts(self, accounts, identities, *, state=""):
        """同一读事务分批读取订单及可信来源；按账号、发布店铺双重隔离。"""
        where = " OR ".join("(platform=? AND account_id=?)" for _ in accounts) or "0"
        args = tuple(value for pair in accounts.items() for value in pair)
        with self.orders.connect() as conn:
            conn.execute("BEGIN")
            cursor = conn.execute(
                f"SELECT id,snapshot,checked_at FROM orders WHERE ({where})"
                + (" AND state=?" if state else "") + " ORDER BY checked_at DESC,id",
                args + ((state,) if state else ()),
            )
            while rows := cursor.fetchmany(100):
                orders = [OrderView(**json.loads(row["snapshot"]), id=row["id"], checked_at=row["checked_at"])
                          for row in rows]
                ids = json.dumps([order.id for order in orders])
                sources = {}
                for row in conn.execute("SELECT * FROM order_sources WHERE order_id IN (SELECT value FROM json_each(?))", (ids,)):
                    sources[(row["order_id"], row["line_key"])] = dict(row)
                purchases = {}
                for row in conn.execute("SELECT * FROM order_purchases WHERE status='purchased' AND order_id IN (SELECT value FROM json_each(?))", (ids,)):
                    record = json.loads(row["record_json"])
                    for item in record["items"]:
                        if item["line_key"] and item["source"]:
                            purchases.setdefault((row["order_id"], item["line_key"]), []).append({
                                **item, "record_id": row["id"], "purchase_order_number": record["purchase_order_number"]})
                bindings = {}
                for platform in {order.platform for order in orders}:
                    identity = identities.get(platform)
                    if not identity:
                        continue
                    skus = json.dumps(list({line.sku for order in orders if order.platform == platform for line in order.items if line.sku}))
                    for row in conn.execute(
                        "SELECT id,binding_json FROM sales_sku_bindings WHERE platform=? AND store_identity=? "
                        "AND seller_sku IN (SELECT value FROM json_each(?))", (platform, identity, skus),
                    ):
                        binding = SalesSkuBinding.model_validate_json(row["binding_json"]).model_copy(update={"id": row["id"]})
                        bindings.setdefault((platform, binding.seller_sku), []).append(binding)
                for order in orders:
                    if accounts.get(order.platform) == order.account_id:
                        yield order, sources, purchases, bindings

    def checked_purchase(self, conn, order_id, record_id, accounts):
        self._order(conn, order_id, accounts)
        row = conn.execute("SELECT * FROM order_purchases WHERE order_id=? AND id=?", (order_id, record_id)).fetchone()
        if not row:
            raise ValueError("采购记录不存在或不属于该订单")
        if row["status"] != "purchased":
            raise ValueError("采购记录已作废，不能查询")
        return json.loads(row["record_json"])

    def purchase_record(self, order_id, record_id, accounts):
        with self.orders.connect() as conn:
            conn.execute("BEGIN")
            record = self.checked_purchase(conn, order_id, record_id, accounts)
            progress = conn.execute("SELECT value FROM purchase_progress WHERE record_id=?", (record_id,)).fetchone()
            return PurchaseRecord.model_validate({**record, "progress": json.loads(progress[0]) if progress else None})

    def records(self, order_id, accounts):
        with self.orders.connect() as conn:
            self._order(conn, order_id, accounts)
            rows = conn.execute("""SELECT p.record_json, s.value FROM order_purchases p
                LEFT JOIN purchase_progress s ON s.record_id=p.id WHERE p.order_id=? ORDER BY p.rowid""", (order_id,)).fetchall()
            return [PurchaseRecord.model_validate({**json.loads(r[0]), "progress": json.loads(r[1]) if r[1] else None}) for r in rows]

    def records_many(self, orders):
        """批量读取已按当前账号限定的订单采购历史，供本地列表检索。"""
        result = {order.id: [] for order in orders}
        if not result:
            return result
        with self.orders.connect() as conn:
            rows = conn.execute("""SELECT p.order_id,p.record_json,s.value FROM order_purchases p
                LEFT JOIN purchase_progress s ON s.record_id=p.id
                WHERE p.order_id IN (SELECT value FROM json_each(?)) ORDER BY p.rowid""",
                (json.dumps(list(result)),)).fetchall()
        for row in rows:
            result[row['order_id']].append(PurchaseRecord.model_validate({
                **json.loads(row['record_json']), 'progress': json.loads(row['value']) if row['value'] else None,
            }))
        return result

    @staticmethod
    def _purchased(conn, order_id, key, signature):
        return conn.execute("""SELECT COALESCE(SUM(json_extract(i.value,'$.quantity')),0)
            FROM order_purchases p, json_each(p.record_json,'$.items') i
            WHERE p.order_id=? AND p.status='purchased'
            AND json_extract(i.value,'$.line_key')=? AND json_extract(i.value,'$.line_signature')=?""",
            (order_id, key, signature)).fetchone()[0]

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
            return dict(row) if row else None, self._purchased(conn, order_id, key, line_signature(line))

    def progress(self, order):
        return self.progress_for_records(order, self.records_many([order])[order.id])

    @staticmethod
    def progress_for_records(order, records):
        keys = [order_line_key(line) for line in order.items]
        if (
            not keys
            or not all(keys)
            or len(set(keys)) != len(keys)
            or any(line.quantity <= 0 for line in order.items)
        ):
            return "unknown"
        active = [record for record in records if record.status == 'purchased']
        quantities = Counter()
        for record in active:
            for item in record.items:
                quantities[item.line_key, item.line_signature] += item.quantity
        if all(quantities[key, line_signature(line)] >= line.quantity for key, line in zip(keys, order.items)):
            return "purchased"
        return "partial" if active else "unpurchased"

    def tracking_summaries(self, orders):
        """批量读取当前订单有效采购单的状态；商品变更不删除已发生的采购事实。"""
        if not orders:
            return {}
        order_ids = [order.id for order in orders]
        placeholders = ",".join("?" for _ in order_ids)
        with self.orders.connect() as conn:
            rows = conn.execute(f"""SELECT p.order_id,p.record_json,s.value FROM order_purchases p
                LEFT JOIN purchase_progress s ON s.record_id=p.id
                WHERE p.order_id IN ({placeholders}) AND p.status='purchased'""", tuple(order_ids)).fetchall()
        result = {}
        packages = {}
        for row in rows:
            summary = result.setdefault(row["order_id"], {"orders": [], "unknown_count": 0, "has_waybill": False, "stale": False})
            progress = json.loads(row["value"]) if row["value"] else {}
            data = progress.get("data") or {}
            status = data.get("order")
            if status and status.get("status"):
                summary["orders"].append(status)
            else:
                summary["unknown_count"] += 1
            summary["has_waybill"] |= any(str(p.get("tracking_number") or "").strip() for p in data.get("logistics") or [])
            stale = bool(progress.get("error")) or progress.get("state") == "syncing"
            summary["stale"] |= stale
            record = json.loads(row['record_json'])
            number = record['purchase_order_number']
            # 不同采购单可能共同装在一个运单中；列表按真实运单去重。
            logistics = [p for p in data.get('logistics') or [] if str(p.get('tracking_number') or '').strip()]
            entries = logistics or [None]
            grouped = packages.setdefault(row['order_id'], {})
            for parcel in entries:
                tracking = str(parcel.get('tracking_number') or '').strip() if parcel else ''
                ident = 'waybill:' + tracking.upper() if tracking else 'order:' + number
                item = {
                    'id': ident, 'order_numbers': [number], 'company': (parcel.get('company') or '') if parcel else '',
                    'tracking_number': tracking, 'source': 'logistics' if parcel else 'order',
                    'status': (parcel or status or {}).get('status') or '',
                    'status_label': (parcel or status or {}).get('status_label') or '状态待查询',
                    'updated_at': (data.get('logistics_checked_at', data.get('checked_at', '')) if parcel else data.get('checked_at', '')),
                    'stale': stale or bool(parcel and data.get('logistics_warning')),
                }
                previous = grouped.get(ident)
                if previous:
                    numbers = sorted(set(previous['order_numbers'] + [number]))
                    if _tracking_time(previous['updated_at']) > _tracking_time(item['updated_at']):
                        previous['order_numbers'] = numbers
                        continue
                    item['order_numbers'] = numbers
                grouped[ident] = item
        for order_id, summary in result.items():
            values = list(packages[order_id].values())
            with_waybill = {number for item in values if item['tracking_number'] for number in item['order_numbers']}
            values = [item for item in values if item['tracking_number'] or not set(item['order_numbers']) <= with_waybill]
            summary['packages'] = sorted(values, key=lambda p: (-_tracking_time(p['updated_at']), p['id']))
        return result

    def select(self, request, source: ProcurementSource, accounts):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            order = self._order(conn, request.order_id, accounts)
            line = self._line(order, request.line_key)
            if order.state in {"cancelled", "delivered"}:
                raise ValueError("已取消或已送达订单不能更改采购来源")
            if self._reserved(conn, request.order_id, request.line_key):
                raise ValueError("1688 采购正在提交或等待核验，暂不能修改来源")
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

    @staticmethod
    def _save_record(conn, order_id, record):
        conn.execute("""INSERT INTO order_purchases VALUES (?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET status=excluded.status,record_json=excluded.record_json""",
            (record.id, order_id, record.source_platform, record.purchase_order_number, record.status,
             record.model_dump_json(exclude={"progress"})))

    def purchase(self, request, accounts):
        digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            order = self._order(conn, request.order_id, accounts)
            previous = conn.execute("SELECT * FROM order_purchase_requests WHERE order_id=? AND request_id=?",
                                    (order.id, request.request_id)).fetchone()
            if previous:
                if previous["request_hash"] != digest:
                    raise ValueError("重复请求标识对应不同采购内容")
                row = conn.execute("SELECT record_json FROM order_purchases WHERE id=?", (previous["record_id"],)).fetchone()
                return PurchaseRecord.model_validate_json(row[0])
            if order.state != "pending_shipment":
                raise ValueError("只有待发货订单可新增采购记录")
            platform = request.source_platform.lower()
            existing = conn.execute("SELECT id FROM order_purchases WHERE order_id=? AND source_platform=? AND purchase_order_number=?",
                                    (order.id, platform, request.purchase_order_number)).fetchone()
            if existing:
                raise ValueError("本订单已登记该采购单，请查看已有记录")
            items = []
            for item in request.items:
                line = self._line(order, item.line_key) if item.line_key else None
                items.append(PurchaseItem(**item.model_dump(), id=uuid4().hex,
                                          line_signature=line_signature(line) if line else ""))
            record = PurchaseRecord(id=uuid4().hex, request_id=request.request_id,
                source_platform=platform, purchase_order_number=request.purchase_order_number,
                items=items, note=request.note, created_at=utc_iso(), receiving_warehouse=request.receiving_warehouse)
            self._save_record(conn, order.id, record)
            conn.execute("INSERT INTO order_purchase_requests VALUES (?,?,?,?)", (order.id, request.request_id, digest, record.id))
            conn.commit()
            return record

    def cancel(self, request, accounts):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._order(conn, request.order_id, accounts)
            row = conn.execute("SELECT record_json FROM order_purchases WHERE id=? AND order_id=?",
                               (request.record_id, request.order_id)).fetchone()
            if not row:
                raise ValueError("采购记录不存在")
            record = PurchaseRecord.model_validate_json(row[0])
            if record.status == "cancelled":
                return
            record.status, record.cancelled_at = "cancelled", utc_iso()
            self._save_record(conn, request.order_id, record)
            conn.commit()

    @staticmethod
    def _reserved(conn, order_id, line_key):
        return conn.execute("SELECT COALESCE(SUM(quantity),0) FROM order_purchase_reservations WHERE order_id=? AND line_key=? AND state='reserved'", (order_id, line_key)).fetchone()[0]

    def reserve_purchase(self, ident, binding, quantity, accounts):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = self._reserve_purchase(conn, ident, binding, quantity, accounts)
            conn.commit()
            return result

    def _reserve_purchase(self, conn, ident, binding, quantity, accounts):
        order = self._order(conn, binding["order_id"], accounts)
        line = self._line(order, binding["line_key"])
        source = conn.execute("SELECT * FROM order_sources WHERE order_id=? AND line_key=?", (order.id, binding["line_key"])).fetchone()
        if order.state != "pending_shipment" or not source or source["revision"] != binding["revision"] or source["line_signature"] != line_signature(line):
            raise ValueError("订单状态或采购来源已变化，请重新预览")
        if json.loads(source["source_json"]) != binding["source"]:
            raise ValueError("采购来源已变化，请重新预览")
        if self._reserved(conn, order.id, binding["line_key"]):
            raise ValueError("本商品存在正在提交或待核验的采购，请先处理原请求")
        if order.platform == "yandex":
            if not binding.get('receiving_warehouse'):
                raise ValueError('请先确定采购收货仓库并重新预览')
            self.receiving.validate(conn, order, binding['receiving_warehouse'])
        frozen = {**binding, "line_signature": line_signature(line)}
        conn.execute("INSERT INTO order_purchase_reservations VALUES (?,?,?,?,?,'reserved')", (ident, order.id, binding["line_key"], quantity, json.dumps(frozen, ensure_ascii=False)))

    def validate_reserved_purchase(self, ident, accounts):
        with self.orders.connect() as conn:
            conn.execute("BEGIN")
            result = self._validate_reserved_purchase(conn, ident, accounts)
            conn.commit()
            return result

    def _validate_reserved_purchase(self, conn, ident, accounts):
        reservation = conn.execute("SELECT * FROM order_purchase_reservations WHERE id=? AND state='reserved'", (ident,)).fetchone()
        if not reservation:
            raise ValueError("采购数量预留不存在，请重新预览")
        binding = json.loads(reservation["binding_json"])
        order = self._order(conn, reservation["order_id"], accounts)
        line = self._line(order, reservation["line_key"])
        if order.state != "pending_shipment" or binding["line_signature"] != line_signature(line):
            raise ValueError("销售订单状态或商品已变化，不能继续采购")
        if order.platform == "yandex":
            self.receiving.validate(conn, order, binding.get('receiving_warehouse'))
    def complete_purchase(self, ident, number, accounts, *, cancelled=False):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = self._complete_purchase(conn, ident, number, accounts, cancelled=cancelled)
            conn.commit()
            return result

    def _complete_purchase(self, conn, ident, number, accounts, *, cancelled=False):
        reservation = conn.execute("SELECT * FROM order_purchase_reservations WHERE id=?", (ident,)).fetchone()
        if not reservation or reservation["state"] == "released":
            raise ValueError("采购数量预留不存在，请核对原请求")
        self._order(conn, reservation["order_id"], accounts)
        binding = json.loads(reservation["binding_json"])
        old = conn.execute("SELECT record_id FROM order_purchase_requests WHERE order_id=? AND request_id=?",
                           (reservation["order_id"], ident)).fetchone()
        if old:
            row = conn.execute("SELECT record_json FROM order_purchases WHERE id=?", (old[0],)).fetchone()
            value = PurchaseRecord.model_validate_json(row[0])
            if value.purchase_order_number != number:
                raise ValueError("远端采购单号与已关联记录不一致")
        else:
            row = conn.execute("SELECT record_json FROM order_purchases WHERE order_id=? AND source_platform='1688' AND purchase_order_number=?",
                               (reservation["order_id"], number)).fetchone()
            value = PurchaseRecord.model_validate_json(row[0]) if row else PurchaseRecord(
                id=uuid4().hex, request_id=ident, source_platform="1688", purchase_order_number=number,
                created_at=utc_iso(), receiving_warehouse=binding.get("receiving_warehouse"))
            if value.status == "cancelled" and not cancelled:
                raise ValueError("原采购登记已作废，请核对采购结果")
            source = ProcurementSource.model_validate(binding["source"])
            value.items.append(PurchaseItem(id=ident, line_key=reservation["line_key"],
                line_signature=binding["line_signature"], quantity=reservation["quantity"], source=source,
                product_id=source.source_offer_id, sku=source.source_sku_id, title=source.specification))
        if cancelled:
            value.status, value.cancelled_at = "cancelled", utc_iso()
        self._save_record(conn, reservation["order_id"], value)
        if not old:
            conn.execute("INSERT INTO order_purchase_requests VALUES (?,?,?,?)", (reservation["order_id"], ident, ident, value.id))
        conn.execute("UPDATE order_purchase_reservations SET state=? WHERE id=?", ("cancelled" if value.status == "cancelled" else "recorded", ident))
        return value.id

    def reserve_purchases(self, entries, accounts):
        """同一供应商组的所有商品一起占位，任一失败整组回滚。"""
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for ident, binding, quantity in entries:
                self._reserve_purchase(conn, ident, binding, quantity, accounts)
            conn.commit()

    def validate_reserved_purchases(self, idents, accounts):
        with self.orders.connect() as conn:
            conn.execute("BEGIN")
            for ident in idents:
                self._validate_reserved_purchase(conn, ident, accounts)

    def release_purchases(self, idents):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany("UPDATE order_purchase_reservations SET state='released' WHERE id=? AND state='reserved'", [(ident,) for ident in idents])
            conn.commit()

    def complete_purchases(self, idents, number, accounts, *, cancelled=False):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = [self._complete_purchase(conn, ident, number, accounts, cancelled=cancelled) for ident in idents]
            conn.commit()
            return list(dict.fromkeys(result))
