"""地址备注的复用、隔离、并发和 HTTP 边界。"""

import time
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit

import pytest
from pydantic import ValidationError

from erp_web.http_route_units import order_routes
from erp_web.schemas.order_address_notes import AddressNoteConflict, AddressNoteWrite
from erp_web.schemas.orders import OrderEvent, OrderSnapshot
from erp_web.schemas.requests import validate_request_payload
from erp_web.stores.order_address_note_store import OrderAddressNoteStore
from erp_web.stores.order_notification_store import OrderNotificationStore

ADDRESS = "浙江省义乌市 某路 23号"


def put_order(store, remote="123", account="4", address=ADDRESS, kind="IMPORT", warehouse="2"):
    now = time.time()
    snapshot = OrderSnapshot(platform="yandex", account_id=account, order_id=remote, fulfillment="FBS",
        status="PROCESSING", handover={"state": "ready", "shipments": [{
            "shipment_id": "789", "shipment_type": kind,
            "origin": {"id": "1", "address": "卖家地址"},
            "destination": {"id": warehouse, "name": "平台交货点", "address": address},
        }]})
    store.enqueue(OrderEvent(platform="yandex", account_id=account, topic="ORDER_UPDATED", resource=remote,
                             payload={"run": str(now)}))
    job = store.claim({"yandex": account}, now=now)
    assert store.save_snapshot(job, snapshot, now=now)
    store.finish(job, now=now)
    return snapshot.identity


@pytest.fixture
def setup(tmp_path):
    orders = OrderNotificationStore(tmp_path / "orders.sqlite3")
    key = put_order(orders)
    notes = OrderAddressNoteStore(orders)
    return orders, notes, key


def write(notes, key, value, note, accounts=None):
    return notes.save(AddressNoteWrite(order_id=key, shipment_id="789", address_key=value.address_key,
                                      revision=value.revision, note=note), accounts or {"yandex": "4"})


def test_new_orders_same_address_reuse_note_and_survive_sync_and_restart(setup):
    orders, notes, key = setup
    empty = notes.read(key, "789", {"yandex": "4"}, address=ADDRESS)
    assert empty.note == "" and empty.revision == 0
    note = "实际地址：另一条路1楼\n收件人：CEL转ID203299\n电话：17758059061"
    saved = write(notes, key, empty, note)
    assert saved.revision == 1
    second = put_order(orders, remote="456", address="浙江省义乌市\n某路  23号", warehouse="3")
    assert notes.read(second, "789", {"yandex": "4"}, address=ADDRESS).note == note
    put_order(orders)
    reopened = OrderAddressNoteStore(OrderNotificationStore(orders.path))
    assert reopened.read(key, "789", {"yandex": "4"}, address=ADDRESS).note == note
    changed = write(reopened, second, saved, "统一修改后的地址")
    reread = reopened.read(key, "789", {"yandex": "4"}, address=ADDRESS)
    assert (reread.note, reread.revision) == (changed.note, changed.revision)


def test_different_addresses_and_accounts_do_not_share_notes(setup):
    orders, notes, key = setup
    initial = notes.read(key, "789", {"yandex": "4"}, address=ADDRESS)
    write(notes, key, initial, "私有备注")
    other_address = put_order(orders, remote="456", address="其他地址")
    assert notes.read(other_address, "789", {"yandex": "4"}, address="其他地址").note == ""
    other_account = put_order(orders, account="9")
    assert notes.read(other_account, "789", {"yandex": "9"}, address=ADDRESS).note == ""
    with pytest.raises(ValueError, match="当前店铺"):
        notes.read(key, "789", {"yandex": "9"}, address=ADDRESS)
    with pytest.raises(ValueError, match="当前店铺"):
        write(notes, key, initial, "不应写入", {"yandex": "9"})


def test_edit_clear_and_idempotent_retry(setup):
    _, notes, key = setup
    initial = notes.read(key, "789", {"yandex": "4"}, address=ADDRESS)
    saved = write(notes, key, initial, "地址\n收件人")
    assert write(notes, key, initial, saved.note) == saved
    cleared = write(notes, key, saved, "")
    assert cleared.note == "" and cleared.revision == 2
    assert notes.read(key, "789", {"yandex": "4"}, address=ADDRESS).note == ""


def test_stale_revision_cannot_overwrite_another_order_note(setup):
    _, notes, key = setup
    initial = notes.read(key, "789", {"yandex": "4"}, address=ADDRESS)
    saved = write(notes, key, initial, "别人刚写的")
    with pytest.raises(AddressNoteConflict) as error:
        write(notes, key, initial, "过期输入")
    assert error.value.current == saved


def test_changed_platform_address_requires_reopening_order(setup):
    orders, notes, key = setup
    initial = notes.read(key, "789", {"yandex": "4"}, address=ADDRESS)
    put_order(orders, address="新地址")
    with pytest.raises(ValueError, match="地址已更新"):
        notes.read(key, "789", {"yandex": "4"}, address=ADDRESS)
    with pytest.raises(ValueError, match="地址已更新"):
        write(notes, key, initial, "旧地址备注")
    assert notes.read(key, "789", {"yandex": "4"}, address="新地址").note == ""


@pytest.mark.parametrize("kind,address", [("NEW_TYPE", ADDRESS), ("IMPORT", "")])
def test_unknown_or_missing_address_cannot_have_note(setup, kind, address):
    orders, notes, _ = setup
    key = put_order(orders, kind=kind, address=address)
    with pytest.raises(ValueError, match="可备注的地址"):
        notes.read(key, "789", {"yandex": "4"}, address=address)


def test_pickup_uses_origin_and_unknown_shipment_is_rejected(setup):
    orders, notes, _ = setup
    key = put_order(orders, kind="WITHDRAW")
    assert notes.read(key, "789", {"yandex": "4"}, address="卖家地址").address == "卖家地址"
    with pytest.raises(ValueError, match="批次已变化"):
        notes.read(key, "not-owned", {"yandex": "4"}, address="卖家地址")


def test_write_schema_requires_all_fields_and_limits_note_length():
    with pytest.raises(ValueError):
        validate_request_payload({"note": "x"}, endpoint="/api/orders/address-note")
    with pytest.raises(ValidationError):
        AddressNoteWrite(order_id="a", shipment_id="b", address_key="x" * 64, revision=0, note="字" * 4001)


def test_http_reads_only_allowed_local_identifiers_and_reports_conflicts(monkeypatch):
    calls, responses = [], []
    monkeypatch.setitem(order_routes.GET_HANDLERS, "/api/orders/address-note", lambda **kwargs: calls.append(kwargs) or {"ok": True})
    handler = SimpleNamespace(send_json=lambda *args: responses.append(args))
    assert order_routes.handle_get(handler, urlsplit("/api/orders/address-note?" + urlencode({
        "order_id": "local", "shipment_id": "789", "address": ADDRESS, "account_id": "other",
    })))
    assert calls == [{"order_id": "local", "shipment_id": "789", "address": ADDRESS}]
    handler.path = "/api/orders/address-note"
    handler.read_body = lambda: {"note": "x"}
    order_routes.handle_address_note(handler)
    assert responses[-1][1] == 400


def test_http_save_keeps_multiline_text_and_reports_revision_conflict(setup, monkeypatch):
    orders, notes, key = setup
    context = SimpleNamespace(order_notifications=SimpleNamespace(store=orders, accounts=lambda: {"yandex": "4"}))
    monkeypatch.setattr(order_routes.address_notes, "get_context", lambda: context)
    initial = notes.read(key, "789", {"yandex": "4"}, address=ADDRESS)
    body = {"order_id": key, "shipment_id": "789", "address_key": initial.address_key,
            "revision": 0, "note": "  实际地址\n收件人：ID203299\n<script>纯文本</script>"}
    responses = []
    handler = SimpleNamespace(path="/api/orders/address-note", read_body=lambda: body,
                              send_json=lambda *args: responses.append(args))
    order_routes.handle_address_note(handler)
    assert responses[-1][0]["note"] == body["note"]
    assert responses[-1][0]["revision"] == 1
    body["note"] = "过期页面的修改"
    order_routes.handle_address_note(handler)
    assert responses[-1][1] == 409
    assert responses[-1][0]["code"] == "ADDRESS_NOTE_CONFLICT"
    assert responses[-1][0]["current"]["revision"] == 1


def test_existing_order_database_adds_note_table_without_changing_orders(setup):
    orders, _, key = setup
    with orders.connect() as conn:
        conn.execute("DROP TABLE order_address_notes")
        before = conn.execute("SELECT snapshot FROM orders WHERE id=?", (key,)).fetchone()[0]
        conn.commit()
    reopened = OrderNotificationStore(orders.path)
    assert OrderAddressNoteStore(reopened).read(key, "789", {"yandex": "4"}, address=ADDRESS).note == ""
    with reopened.connect() as conn:
        assert conn.execute("SELECT snapshot FROM orders WHERE id=?", (key,)).fetchone()[0] == before
