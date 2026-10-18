"""一次性离线导入保留采购历史、真实运单和未知回执，不修改原库。"""

import hashlib
import json
import sqlite3

import pytest

from erp_web.schemas.order_procurement import PurchaseRecord, order_line_key
from erp_web.schemas.orders import OrderLine, OrderSnapshot
from erp_web.stores.fulfillment_store import SCHEMA as FULFILLMENT_SCHEMA
from scripts.import_order_center_data import import_databases


@pytest.fixture
def old_databases(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    order = OrderSnapshot(platform="yandex", account_id="4", order_id="123", status="PROCESSING",
                          state="pending_shipment", items=[OrderLine(sku="A", title="衣服", quantity=2), OrderLine(sku="B", title="杯子", quantity=1)])
    keys = [order_line_key(line) for line in order.items]
    records = [{"id": ident, "request_id": ident, "line_key": keys[index], "quantity": quantity,
                "source": {"source_platform": "1688", "product_url": "https://detail.1688.com/offer/123.html",
                           "source_sku_id": str(index), "specification": "规格" + str(index)},
                "purchase_order_number": "100001", "status": status, "created_at": "2026-10-01",
                "cancelled_at": "2026-10-02" if status == "cancelled" else "", "receiving_warehouse": None}
               for ident, index, quantity, status in [("p1", 0, 2, "purchased"), ("p2", 1, 1, "purchased"), ("p3", 0, 1, "cancelled")]]
    with sqlite3.connect(source / "order-notifications.sqlite3") as conn:
        conn.execute("CREATE TABLE orders(id TEXT PRIMARY KEY, snapshot TEXT)")
        conn.execute("INSERT INTO orders VALUES (?,?)", (order.identity, order.model_dump_json()))
        conn.execute("CREATE TABLE order_purchases(id TEXT PRIMARY KEY, order_id TEXT, line_key TEXT, request_id TEXT, request_hash TEXT, line_signature TEXT, quantity INTEGER, status TEXT, record_json TEXT)")
        for record in records:
            conn.execute("INSERT INTO order_purchases VALUES (?,?,?,?,?,?,?,?,?)", (record["id"], order.identity, record["line_key"], record["request_id"], "original-hash", "signature", record["quantity"], record["status"], json.dumps(record)))
        conn.execute("CREATE TABLE purchase_progress(record_id TEXT PRIMARY KEY,generation INTEGER,lease_until REAL,value TEXT)")
        conn.execute("CREATE TABLE preserved_setting(value TEXT)")
        conn.execute("INSERT INTO preserved_setting VALUES ('unchanged')")
        conn.executescript(FULFILLMENT_SCHEMA)
        parcels = [{"id": "parcel-" + r["id"], "line_key": r["line_key"], "purchase_record_id": r["id"], "carrier": "圆通", "tracking_number": " yt1 ", "quantity": r["quantity"]} for r in records[:2]]
        value = {"parcels": parcels, "fulfillment_status": "EXCEPTION", "crossborderbus_order_id": 555,
                 "create_unknown": True, "manual_submit": True, "update_pending": True,
                 "operation": "", "attempt_history": [{"crossborderbus_order_id": 444, "parcels": parcels}]}
        conn.execute("INSERT INTO fulfillments VALUES (?,?,?,?,?,5,'',0)", (order.identity, "yandex", "4", "123", json.dumps(value)))
        for index, record in enumerate(records[:2]):
            value = {"state": "manual", "attempted_at": "2026-10-02", "message": "已人工调整", "error": "",
                     "resolution": {"logistics": [{"tracking_number": "removed"}], "parcels": []},
                     "data": {"ok": True, "record_id": record["id"], "order_number": "100001", "checked_at": "2026-10-01", "order": None,
                              "logistics": [], "logistics_warning": "", "product_warning": "",
                              "product": {"offer_id": "123", "sku_id": str(index), "spec_id": "", "name": "商品", "specification": "规格", "image_url": ""}}}
            conn.execute("INSERT INTO purchase_progress VALUES (?,1,0,?)", (record["id"], json.dumps(value)))
    with sqlite3.connect(source / "alibaba-self-purchases.sqlite3") as conn:
        conn.execute("CREATE TABLE alibaba_self_purchases(id TEXT PRIMARY KEY, state TEXT, result TEXT, payment TEXT)")
        conn.execute("INSERT INTO alibaba_self_purchases VALUES ('request','unknown',?,'payment-unknown')", (json.dumps({"purchase_record_id": "p2", "purchase_record_ids": ["p1", "p2"], "order_numbers": ["100001"]}),))
    return source, tmp_path / "output", order.identity


def test_import_groups_facts_preserves_history_and_never_mutates_original(old_databases):
    source, output, order_id = old_databases
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
    report = import_databases(source, output)
    assert report["old_purchase_rows"] == 3 and report["purchase_orders"] == 1
    assert report["old_parcel_rows"] == 2 and report["parcels"] == 1
    assert report["record_mapping"] == {"p1": "p1", "p2": "p1", "p3": "p1"}
    assert report["cancelled_registrations"][0]["id"] == "p3"
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()} == hashes
    with sqlite3.connect(output / "order-notifications.sqlite3") as conn:
        record = PurchaseRecord.model_validate_json(conn.execute("SELECT record_json FROM order_purchases").fetchone()[0])
        assert record.status == "purchased" and sum(i.quantity for i in record.items) == 3
        assert "p3" in record.note and "作废" in record.note
        assert conn.execute("SELECT count(*) FROM order_purchase_requests").fetchone()[0] == 3
        assert conn.execute("SELECT value FROM preserved_setting").fetchone()[0] == "unchanged"
        value, revision = conn.execute("SELECT value,revision FROM fulfillments WHERE id=?", (order_id,)).fetchone()
        value = json.loads(value)
        assert revision == 6 and value["crossborderbus_order_id"] == 555 and value["create_unknown"]
        assert value["parcels"][0]["quantity"] == 3 and value["parcels"][0]["tracking_number"] == "YT1"
        assert "衣服" in value["parcels"][0]["description"] and "杯子" in value["parcels"][0]["description"]
        assert not value["manual_submit"] and not value["update_pending"] and not value["parcels_confirmed"]
        assert set(value["observed_waybills"]) == {"YT1", "REMOVED"}
        assert value["attempt_history"][0]["crossborderbus_order_id"] == 444
        progress = json.loads(conn.execute("SELECT value FROM purchase_progress").fetchone()[0])
        assert len(progress["data"]["products"]) == 2 and "resolution" not in progress
        assert "product" not in progress["data"]
    with sqlite3.connect(output / "alibaba-self-purchases.sqlite3") as conn:
        state, raw, payment = conn.execute("SELECT state,result,payment FROM alibaba_self_purchases").fetchone()
        assert state == "unknown" and payment == "payment-unknown"
        assert json.loads(raw)["purchase_record_ids"] == ["p1"]
        assert json.loads(raw)["purchase_record_id"] == "p1"


def test_import_rejects_overwriting_or_converting_new_schema(old_databases, tmp_path):
    source, output, _ = old_databases
    import_databases(source, output)
    with pytest.raises(ValueError, match="目录必须不存在"):
        import_databases(source, output)
    with pytest.raises(ValueError, match="禁止重复转换"):
        import_databases(output, tmp_path / "again")


def test_import_does_not_clear_inflight_operation(old_databases):
    source, output, _ = old_databases
    with sqlite3.connect(source / "order-notifications.sqlite3") as conn:
        conn.execute("UPDATE fulfillments SET claim='inflight'")
    with pytest.raises(ValueError, match="未结束的履约操作"):
        import_databases(source, output)
    with sqlite3.connect(source / "order-notifications.sqlite3") as conn:
        assert conn.execute("SELECT claim FROM fulfillments").fetchone()[0] == "inflight"
