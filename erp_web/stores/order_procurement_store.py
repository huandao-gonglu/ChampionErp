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
from erp_web.schemas.orders import account_in_scope, account_pairs, OrderSnapshot, OrderView, utc_iso
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
CREATE TABLE IF NOT EXISTS procurement_meta (
 key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sku_master_links (
 platform TEXT NOT NULL, store_identity TEXT NOT NULL, seller_sku TEXT NOT NULL,
 master_sku TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(platform,store_identity,seller_sku));
CREATE INDEX IF NOT EXISTS sku_master_links_code ON sku_master_links(master_sku);
CREATE TABLE IF NOT EXISTS master_sku_sources (
 id TEXT PRIMARY KEY, master_sku TEXT NOT NULL, binding_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS master_sku_sources_code ON master_sku_sources(master_sku);
"""


def _identity_list(store_identity) -> list[str]:
    if not store_identity:
        return []
    if isinstance(store_identity, str):
        return [store_identity]
    return [value for value in dict.fromkeys(store_identity) if value]


def _binding_sort_key(binding: SalesSkuBinding):
    return (
        1 if binding.is_default else 0,
        binding.updated_at or "",
        1 if binding.origin == "alibaba_api" else 0,
        1 if binding.scope == "master" else 0,
        binding.id or "",
    )


def _master_source_id(master_sku: str, source: ProcurementSource) -> str:
    payload = {
        "master_sku": master_sku,
        "product_url": source.product_url,
        "source_sku_id": source.source_sku_id,
        "source_offer_id": source.source_offer_id,
        "source_spec_id": source.source_spec_id,
        "specification": source.specification,
    }
    return "ms:" + hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


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
        if not account_in_scope(accounts, snapshot.platform, snapshot.account_id):
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
        where = " OR ".join("(platform=? AND account_id=?)" for _ in account_pairs(accounts)) or "0"
        args = tuple(value for pair in account_pairs(accounts) for value in pair)
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
                for platform, account in {(order.platform, order.account_id) for order in orders}:
                    scope_ids = _identity_list(identities.get((platform, account)))
                    if not scope_ids:
                        continue
                    sku_list = list({line.sku for order in orders if order.platform == platform and order.account_id == account
                                     for line in order.items if line.sku})
                    if not sku_list:
                        continue
                    merged_by_sku = self._query_bindings_in_conn(conn, platform, scope_ids, sku_list)
                    for seller_sku, items in merged_by_sku.items():
                        bindings[(platform, account, seller_sku)] = items
                for order in orders:
                    if account_in_scope(accounts, order.platform, order.account_id):
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

    def meta(self, key: str) -> str:
        with self.orders.connect() as conn:
            row = conn.execute("SELECT value FROM procurement_meta WHERE key=?", (key,)).fetchone()
            return str(row[0]) if row else ""

    def set_meta(self, key: str, value: str) -> None:
        with self.orders.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO procurement_meta VALUES (?,?)", (key, str(value)))
            conn.commit()

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

    @staticmethod
    def _matches_target(value: SalesSkuBinding, *, remote_id: str = "", variant_id: str = "") -> bool:
        # 远端身份可消除同 SKU 的歧义；缺少证据不能反过来猜身份。
        if value.remote_id and remote_id and value.remote_id != remote_id:
            return False
        if value.variant_id and variant_id and value.variant_id != variant_id:
            return False
        return True

    @staticmethod
    def _project_master_row(
        row_id: str,
        master_sku: str,
        raw_json: str,
        *,
        platform: str,
        store_identity: str,
        seller_sku: str,
    ) -> SalesSkuBinding:
        data = json.loads(raw_json)
        return SalesSkuBinding(
            id=row_id,
            platform=platform,
            store_identity=store_identity,
            seller_sku=seller_sku,
            source=ProcurementSource.model_validate(data["source"]),
            image_url=str(data.get("image_url") or ""),
            origin=data.get("origin") or "alibaba_api",
            scope="master",
            master_sku=master_sku,
            is_default=bool(data.get("is_default")),
            updated_at=str(data.get("updated_at") or ""),
        )

    def _linked_masters_in_conn(self, conn, platform: str, scope_ids: list[str], seller_skus: list[str]) -> dict[str, str]:
        if not scope_ids or not seller_skus:
            return {}
        marks = ",".join("?" for _ in scope_ids)
        rows = conn.execute(
            f"SELECT seller_sku,master_sku,updated_at FROM sku_master_links "
            f"WHERE platform=? AND store_identity IN ({marks}) AND seller_sku IN (SELECT value FROM json_each(?)) "
            f"ORDER BY updated_at DESC",
            (platform, *scope_ids, json.dumps(seller_skus, ensure_ascii=False)),
        ).fetchall()
        links: dict[str, str] = {}
        for row in rows:
            if row["seller_sku"] not in links and row["master_sku"]:
                links[row["seller_sku"]] = row["master_sku"]
        return links

    def _query_bindings_in_conn(
        self,
        conn,
        platform: str,
        scope_ids: list[str],
        seller_skus: list[str],
    ) -> dict[str, list[SalesSkuBinding]]:
        unique_skus = [sku for sku in dict.fromkeys(seller_skus) if sku]
        if not scope_ids or not unique_skus:
            return {}
        marks = ",".join("?" for _ in scope_ids)
        skus_json = json.dumps(unique_skus, ensure_ascii=False)
        rows = conn.execute(
            f"SELECT id,seller_sku,binding_json FROM sales_sku_bindings "
            f"WHERE platform=? AND store_identity IN ({marks}) AND seller_sku IN (SELECT value FROM json_each(?))",
            (platform, *scope_ids, skus_json),
        ).fetchall()
        grouped: dict[str, list[SalesSkuBinding]] = {}
        for row in rows:
            binding = SalesSkuBinding.model_validate_json(row["binding_json"]).model_copy(
                update={"id": row["id"], "scope": "store"}
            )
            grouped.setdefault(row["seller_sku"], []).append(binding)
        links = self._linked_masters_in_conn(conn, platform, scope_ids, unique_skus)
        master_by_sku = {sku: links.get(sku) or sku for sku in unique_skus}
        unique_masters = list(dict.fromkeys(master_by_sku.values()))
        if unique_masters:
            master_rows = conn.execute(
                "SELECT id,master_sku,binding_json FROM master_sku_sources WHERE master_sku IN (SELECT value FROM json_each(?))",
                (json.dumps(unique_masters, ensure_ascii=False),),
            ).fetchall()
            master_grouped: dict[str, list[tuple[str, str]]] = {}
            for row in master_rows:
                master_grouped.setdefault(row["master_sku"], []).append((row["id"], row["binding_json"]))
            primary_identity = scope_ids[0]
            for seller_sku, master_sku in master_by_sku.items():
                for row_id, raw_json in master_grouped.get(master_sku, []):
                    projected = self._project_master_row(
                        row_id,
                        master_sku,
                        raw_json,
                        platform=platform,
                        store_identity=primary_identity,
                        seller_sku=seller_sku,
                    )
                    grouped.setdefault(seller_sku, []).append(projected)
        for items in grouped.values():
            items.sort(key=_binding_sort_key, reverse=True)
        return grouped

    def resolve_master_sku(self, platform: str, store_identity, seller_sku: str) -> tuple[str, str]:
        """返回 (effective_master_sku, explicit_linked_master_sku)。"""
        scope_ids = _identity_list(store_identity)
        if not seller_sku or not scope_ids:
            return seller_sku, ""
        with self.orders.connect() as conn:
            links = self._linked_masters_in_conn(conn, platform, scope_ids, [seller_sku])
        linked = links.get(seller_sku, "")
        return linked or seller_sku, linked

    def resolve_master_skus_batch(self, platform: str, store_identity, seller_skus: list[str]) -> dict[str, tuple[str, str]]:
        scope_ids = _identity_list(store_identity)
        unique_skus = [sku for sku in dict.fromkeys(seller_skus) if sku]
        if not scope_ids or not unique_skus:
            return {sku: (sku, "") for sku in unique_skus}
        with self.orders.connect() as conn:
            links = self._linked_masters_in_conn(conn, platform, scope_ids, unique_skus)
        return {sku: (links.get(sku) or sku, links.get(sku, "")) for sku in unique_skus}

    def set_master_sku_link(
        self,
        platform: str,
        store_identity: str,
        seller_sku: str,
        master_sku: str,
        *,
        clear_identities=(),
    ) -> tuple[str, str]:
        cleaned = str(master_sku or "").strip()
        scope_ids = _identity_list([store_identity, *_identity_list(clear_identities)])
        now = utc_iso()
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if scope_ids:
                marks = ",".join("?" for _ in scope_ids)
                conn.execute(
                    f"DELETE FROM sku_master_links WHERE platform=? AND store_identity IN ({marks}) AND seller_sku=?",
                    (platform, *scope_ids, seller_sku),
                )
            if cleaned and cleaned != seller_sku:
                conn.execute(
                    "INSERT OR REPLACE INTO sku_master_links VALUES (?,?,?,?,?)",
                    (platform, store_identity, seller_sku, cleaned, now),
                )
            conn.commit()
        linked = cleaned if cleaned and cleaned != seller_sku else ""
        return linked or seller_sku, linked

    def list_sku_bindings(
        self,
        platform: str,
        store_identity,
        seller_sku: str,
        *,
        remote_id: str = "",
        variant_id: str = "",
    ) -> list[SalesSkuBinding]:
        scope_ids = _identity_list(store_identity)
        if not seller_sku or not scope_ids:
            return []
        with self.orders.connect() as conn:
            raw_list = self._query_bindings_in_conn(conn, platform, scope_ids, [seller_sku]).get(seller_sku, [])
        return [
            value
            for value in raw_list
            if self._matches_target(value, remote_id=remote_id, variant_id=variant_id)
        ]

    def list_sku_bindings_batch(
        self,
        platform: str,
        store_identity,
        seller_skus: list[str],
    ) -> dict[str, list[SalesSkuBinding]]:
        scope_ids = _identity_list(store_identity)
        if not scope_ids or not seller_skus:
            return {}
        with self.orders.connect() as conn:
            return self._query_bindings_in_conn(conn, platform, scope_ids, seller_skus)

    def upsert_master_source(
        self,
        master_sku: str,
        source: ProcurementSource,
        *,
        image_url: str = "",
        origin: str = "alibaba_api",
        set_default: bool = True,
        platform: str = "",
        store_identities=(),
        seller_sku: str = "",
    ) -> SalesSkuBinding:
        now = utc_iso()
        row_id = _master_source_id(master_sku, source)
        scope_ids = _identity_list(store_identities)
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if set_default:
                for row in conn.execute("SELECT id,binding_json FROM master_sku_sources WHERE master_sku=?", (master_sku,)).fetchall():
                    if row["id"] == row_id:
                        continue
                    data = json.loads(row["binding_json"])
                    if data.get("is_default"):
                        data["is_default"] = False
                        conn.execute("UPDATE master_sku_sources SET binding_json=? WHERE id=?", (json.dumps(data, ensure_ascii=False), row["id"]))
                if platform and scope_ids and seller_sku:
                    marks = ",".join("?" for _ in scope_ids)
                    for row in conn.execute(
                        f"SELECT id,binding_json FROM sales_sku_bindings WHERE platform=? AND store_identity IN ({marks}) AND seller_sku=?",
                        (platform, *scope_ids, seller_sku),
                    ).fetchall():
                        item = SalesSkuBinding.model_validate_json(row["binding_json"])
                        if item.is_default:
                            conn.execute(
                                "UPDATE sales_sku_bindings SET binding_json=? WHERE id=?",
                                (item.model_copy(update={"is_default": False}).model_dump_json(), row["id"]),
                            )
            old_row = conn.execute("SELECT binding_json FROM master_sku_sources WHERE id=?", (row_id,)).fetchone()
            prev_img = str(json.loads(old_row["binding_json"]).get("image_url") or "") if old_row else ""
            payload = {
                "source": source.model_dump(mode="json"),
                "image_url": image_url or prev_img,
                "origin": origin,
                "is_default": set_default,
                "updated_at": now,
            }
            payload_json = json.dumps(payload, ensure_ascii=False)
            conn.execute("INSERT OR REPLACE INTO master_sku_sources VALUES (?,?,?)", (row_id, master_sku, payload_json))
            conn.commit()
        return self._project_master_row(
            row_id,
            master_sku,
            payload_json,
            platform=platform or "yandex",
            store_identity=scope_ids[0] if scope_ids else "",
            seller_sku=seller_sku or master_sku,
        )

    def set_default_master_source(
        self,
        master_sku: str,
        binding_id: str,
        *,
        platform: str,
        store_identities=(),
        seller_sku: str,
    ) -> SalesSkuBinding:
        now = utc_iso()
        scope_ids = _identity_list(store_identities)
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT id,binding_json FROM master_sku_sources WHERE master_sku=?", (master_sku,)).fetchall()
            target_json = ""
            for row in rows:
                data = json.loads(row["binding_json"])
                if row["id"] == binding_id:
                    data["is_default"] = True
                    data["updated_at"] = now
                    target_json = json.dumps(data, ensure_ascii=False)
                    conn.execute("UPDATE master_sku_sources SET binding_json=? WHERE id=?", (target_json, row["id"]))
                elif data.get("is_default"):
                    data["is_default"] = False
                    conn.execute("UPDATE master_sku_sources SET binding_json=? WHERE id=?", (json.dumps(data, ensure_ascii=False), row["id"]))
            if not target_json:
                raise ValueError("未找到对应的跨平台共享采购源，请刷新后重试")
            if scope_ids:
                marks = ",".join("?" for _ in scope_ids)
                for row in conn.execute(
                    f"SELECT id,binding_json FROM sales_sku_bindings WHERE platform=? AND store_identity IN ({marks}) AND seller_sku=?",
                    (platform, *scope_ids, seller_sku),
                ).fetchall():
                    item = SalesSkuBinding.model_validate_json(row["binding_json"])
                    if item.is_default:
                        conn.execute(
                            "UPDATE sales_sku_bindings SET binding_json=? WHERE id=?",
                            (item.model_copy(update={"is_default": False}).model_dump_json(), row["id"]),
                        )
            conn.commit()
        return self._project_master_row(
            binding_id,
            master_sku,
            target_json,
            platform=platform,
            store_identity=scope_ids[0] if scope_ids else "",
            seller_sku=seller_sku,
        )

    def delete_master_source(self, master_sku: str, binding_id: str) -> bool:
        if not master_sku or not binding_id:
            return False
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "DELETE FROM master_sku_sources WHERE id=? AND master_sku=?",
                (binding_id, master_sku),
            )
            conn.commit()
            return cursor.rowcount > 0

    def upsert_sku_binding(
        self,
        binding: SalesSkuBinding,
        *,
        set_default: bool = True,
        clear_default_identities=(),
    ) -> SalesSkuBinding:
        now = utc_iso()
        target_id = binding.identity
        scope_ids = _identity_list([binding.store_identity, *_identity_list(clear_default_identities)])
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if set_default and scope_ids:
                marks = ",".join("?" for _ in scope_ids)
                existing_rows = conn.execute(
                    f"SELECT id,binding_json FROM sales_sku_bindings WHERE platform=? AND store_identity IN ({marks}) AND seller_sku=?",
                    (binding.platform, *scope_ids, binding.seller_sku),
                ).fetchall()
                for row in existing_rows:
                    if row["id"] == target_id:
                        continue
                    current = SalesSkuBinding.model_validate_json(row["binding_json"])
                    if not self._matches_target(current, remote_id=binding.remote_id, variant_id=binding.variant_id):
                        continue
                    if current.is_default:
                        updated_other = current.model_copy(update={"is_default": False})
                        conn.execute(
                            "UPDATE sales_sku_bindings SET binding_json=? WHERE id=?",
                            (updated_other.model_dump_json(), row["id"]),
                        )
            old_row = conn.execute("SELECT binding_json FROM sales_sku_bindings WHERE id=?", (target_id,)).fetchone()
            merged = binding.model_copy(update={"id": target_id, "is_default": set_default, "updated_at": now})
            if old_row:
                previous = SalesSkuBinding.model_validate_json(old_row["binding_json"])
                merged = merged.model_copy(update={
                    "product_id": merged.product_id or previous.product_id,
                    "draft_id": merged.draft_id or previous.draft_id,
                    "sku_id": merged.sku_id or previous.sku_id,
                    "publication_id": merged.publication_id or previous.publication_id,
                    "image_url": merged.image_url or previous.image_url,
                })
            conn.execute(
                "INSERT OR REPLACE INTO sales_sku_bindings VALUES (?,?,?,?,?)",
                (target_id, merged.platform, merged.store_identity, merged.seller_sku, merged.model_dump_json()),
            )
            conn.commit()
            return merged

    def set_default_sku_binding(
        self,
        platform: str,
        store_identity,
        seller_sku: str,
        binding_id: str,
        *,
        remote_id: str = "",
        variant_id: str = "",
    ) -> SalesSkuBinding:
        scope_ids = _identity_list(store_identity)
        if not scope_ids or not seller_sku or not binding_id:
            raise ValueError("缺少必要的销售 SKU 或货源标识")
        marks = ",".join("?" for _ in scope_ids)
        now = utc_iso()
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                f"SELECT id,binding_json FROM sales_sku_bindings WHERE platform=? AND store_identity IN ({marks}) AND seller_sku=?",
                (platform, *scope_ids, seller_sku),
            ).fetchall()
            target: SalesSkuBinding | None = None
            parsed: list[tuple[str, SalesSkuBinding]] = []
            for row in rows:
                item = SalesSkuBinding.model_validate_json(row["binding_json"]).model_copy(update={"id": row["id"]})
                parsed.append((row["id"], item))
                if row["id"] == binding_id:
                    target = item
            if target is None:
                raise ValueError("未找到对应的 SKU 采购来源，请刷新后重试")
            eff_remote = remote_id or target.remote_id
            eff_variant = variant_id or target.variant_id
            for row_id, item in parsed:
                if row_id == binding_id:
                    updated = item.model_copy(update={"is_default": True, "updated_at": now})
                    conn.execute("UPDATE sales_sku_bindings SET binding_json=? WHERE id=?", (updated.model_dump_json(), row_id))
                    target = updated
                elif item.is_default and self._matches_target(item, remote_id=eff_remote, variant_id=eff_variant):
                    updated = item.model_copy(update={"is_default": False})
                    conn.execute("UPDATE sales_sku_bindings SET binding_json=? WHERE id=?", (updated.model_dump_json(), row_id))
            conn.commit()
            return target

    def delete_sku_binding(
        self,
        platform: str,
        store_identity,
        seller_sku: str,
        binding_id: str,
    ) -> bool:
        scope_ids = _identity_list(store_identity)
        if not scope_ids or not seller_sku or not binding_id:
            return False
        marks = ",".join("?" for _ in scope_ids)
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                f"DELETE FROM sales_sku_bindings WHERE id=? AND platform=? AND store_identity IN ({marks}) AND seller_sku=?",
                (binding_id, platform, *scope_ids, seller_sku),
            )
            conn.commit()
            return cursor.rowcount > 0

    def candidates(self, platform, store_identity, line):
        return self.list_sku_bindings(
            platform,
            store_identity,
            line.sku,
            remote_id=line.remote_id,
            variant_id=line.variant_id,
        )

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
