"""通知接收、后台恢复、三平台归一化与本地读取的回归测试。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.server import ThreadingHTTPServer

import pytest
import requests

from erp_web.context import get_context
from erp_web.http_handler import Handler
from erp_web.runtime_units.order_notifications import parse_notification
from erp_web.runtime_units.orders_mercadolibre import MercadoLibreOrderAdapter
from erp_web.runtime_units.orders_ozon import OzonOrderAdapter
from erp_web.runtime_units.orders_yandex import YandexOrderAdapter
from erp_web.schemas.orders import OrderEvent, OrderSnapshot
from erp_web.services.order_notification_service import OrderNotificationService
from erp_web.stores.order_notification_migration import import_historical_notifications
from erp_web.stores.order_notification_store import OrderNotificationStore

CONFIG = {
    "mercadolibre": {"user_id": "1", "app_id": "2", "access_token": "ml-secret"},
    "ozon": {"client_id": "3", "api_key": "ozon-secret"},
    "yandex": {"campaign_id": "4", "business_id": "5", "api_token": "ya-secret"},
}


def event(**kwargs):
    return OrderEvent(
        platform="ozon",
        account_id="3",
        topic="TYPE_NEW_POSTING",
        resource="10-1",
        payload={
            "message_type": "TYPE_NEW_POSTING",
            "posting_number": "10-1",
            **kwargs,
        },
    )


def snapshot(**kwargs):
    return OrderSnapshot(
        platform="ozon",
        account_id="3",
        order_id="10-1",
        fulfillment="fbs",
        title="测试商品",
        status="awaiting_packaging",
        **kwargs,
    )


@pytest.fixture
def store(tmp_path):
    return OrderNotificationStore(tmp_path / "orders.sqlite3")


def save(store, value, payload=None, now=None):
    now = time.time() if now is None else now
    store.enqueue(event(sequence=payload or value.model_dump()))
    job = store.claim({"ozon": "3"}, now=now)
    assert job
    assert store.save_snapshot(job, value, now=now)
    store.finish(job, now=now)
    return job


def service(store, adapters):
    return OrderNotificationService(
        store,
        lambda: deepcopy(CONFIG),
        adapters=adapters,
        parser=parse_notification,
        start_worker=False,
    )


def test_duplicate_callbacks_are_atomic_and_survive_restart(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: store.enqueue(event()), range(30)))
    assert len(set(ids)) == 1
    reopened = OrderNotificationStore(store.path)
    assert len(reopened.read({"ozon": "3"})["notifications"]) == 1
    assert reopened.claim({"ozon": "3"}, now=time.time())["attempts"] == 1


def test_order_state_is_latest_not_union_of_old_notifications(store):
    save(store, snapshot(state="pending_shipment", updated_at="2026-10-05T10:00:00Z"))
    save(store, snapshot(state="shipped", updated_at="2026-10-05T11:00:00Z"))
    save(store, snapshot(state="pending_shipment", updated_at="2026-10-05T09:00:00Z"))
    page = store.read({"ozon": "3"})
    assert page["counts"] == {"shipped": 1}
    assert page["unread"] == 0
    assert page["latest_alert_id"] == 1


def test_missing_version_cannot_overwrite_versioned_state(store):
    save(store, snapshot(state="shipped", updated_at="2026-10-05T10:00:00Z"))
    save(store, snapshot(state="pending_shipment"))
    assert store.read({"ozon": "3"})["counts"] == {"shipped": 1}


def test_unknown_state_does_not_count_as_pending(store):
    save(store, snapshot())
    page = store.read({"ozon": "3"})
    assert page["counts"] == {"unknown": 1}
    assert page["unread"] == 0


def test_same_state_does_not_duplicate_alert_and_ack_is_bounded(store):
    save(store, snapshot(state="pending_shipment"), payload="first")
    first = store.read({"ozon": "3"})["latest_alert_id"]
    save(store, snapshot(state="pending_shipment"), payload="second")
    assert store.read({"ozon": "3"})["unread"] == 1
    other = snapshot(state="pending_shipment").model_copy(update={"order_id": "11-1"})
    save(store, other, payload="third")
    store.acknowledge(first, {"ozon": "3"})
    assert store.read({"ozon": "3"})["unread"] == 1


def test_counts_are_not_limited_to_latest_notifications_or_page(store):
    for index in range(55):
        save(
            store,
            snapshot(state="pending_shipment").model_copy(
                update={"order_id": str(index)}
            ),
            payload=str(index),
        )
    page = store.read({"ozon": "3"}, limit=10, offset=50)
    assert page["total"] == 55
    assert len(page["items"]) == 5
    assert page["counts"]["pending_shipment"] == 55
    assert page["unread"] == 55


def test_accounts_are_isolated_for_reads_claims_retry_and_ack(store):
    save(store, snapshot(state="pending_shipment"))
    store.enqueue(
        OrderEvent(platform="ozon", account_id="another", topic="sync", resource="")
    )
    assert store.claim({"ozon": "another"}, now=time.time())["account_id"] == "another"
    page = store.read({"ozon": "another"})
    assert page["items"] == [] and page["unread"] == 0
    store.acknowledge(100, {"ozon": "another"})
    assert store.read({"ozon": "3"})["unread"] == 1
    with pytest.raises(ValueError):
        store.retry(1, {"ozon": "another"})


def test_expired_lease_is_reclaimed_and_late_result_is_fenced(store):
    store.enqueue(event())
    now = time.time()
    old = store.claim({"ozon": "3"}, now=now)
    assert store.claim({"ozon": "3"}, now=now + 1) is None
    fresh = store.claim({"ozon": "3"}, now=now + 181)
    assert fresh["claim"] != old["claim"]
    assert (
        store.save_snapshot(old, snapshot(state="pending_shipment"), now=now + 182)
        is False
    )
    store.finish(old, now=now + 182)
    assert store.read({"ozon": "3"})["notifications"][0]["status"] == "running"
    assert store.save_snapshot(fresh, snapshot(state="shipped"), now=now + 182)


def test_schedule_coalesces_manual_and_periodic_requests(store):
    now = time.time()
    store.schedule({"ozon": "3"}, now=now)
    store.schedule({"ozon": "3"}, now=now, force=True)
    assert len(store.read({"ozon": "3"})["notifications"]) == 1
    job = store.claim({"ozon": "3"}, now=now)
    store.finish(job, now=now)
    store.schedule({"ozon": "3"}, now=now + 100)
    assert len(store.read({"ozon": "3"})["notifications"]) == 1
    store.schedule({"ozon": "3"}, now=now + 301)
    assert len(store.read({"ozon": "3"})["notifications"]) == 2


def test_receive_only_persists_and_does_not_run_adapter(store):
    def forbidden(_):
        raise AssertionError("回调不得访问远端")

    svc = service(store, {"ozon": forbidden})
    body = {
        "message_type": "TYPE_NEW_POSTING",
        "seller_id": 3,
        "posting_number": "10-1",
    }
    token = store.setting("token:ozon")
    result = svc.receive("ozon", token, body)
    assert result["name"] == "Champion ERP"
    assert store.read({"ozon": "3"})["notifications"][0]["status"] == "queued"
    with pytest.raises(PermissionError):
        svc.receive("ozon", "invalid", body)
    with pytest.raises(ValueError):
        svc.receive("ozon", token, {**body, "seller_id": 99})


@pytest.mark.parametrize(
    ("platform", "body"),
    [("ozon", {"message_type": "TYPE_PING"}), ("yandex", {"notificationType": "PING"})],
)
def test_platform_ping_has_required_response_without_enqueue(store, platform, body):
    result = service(store, {}).receive(
        platform, store.setting("token:" + platform), body
    )
    assert set(result) == {"name", "version", "time"}
    assert store.read({"ozon": "3", "yandex": "4"})["notifications"] == []


def test_ml_resource_whitelist_and_retry_dedup():
    body = {
        "_id": "notification-1",
        "topic": "orders_v2",
        "user_id": 1,
        "application_id": 2,
        "resource": "/orders/7",
        "attempts": 1,
    }
    first, _ = parse_notification("mercadolibre", body, CONFIG)
    second, _ = parse_notification("mercadolibre", {**body, "attempts": 5}, CONFIG)
    assert first.dedup_key == second.dedup_key
    for resource in [
        "/users/me",
        "//other/orders/7",
        "/orders/7?token=bad",
        "https://api.mercadolibre.com/orders/7",
        "/orders/../users/me",
    ]:
        with pytest.raises(ValueError):
            parse_notification("mercadolibre", {**body, "resource": resource}, CONFIG)


def test_transient_failure_retries_after_backoff_without_leaking_secret(store):
    class Adapter:
        def __init__(self, _):
            pass

        def read(self, _):
            raise TimeoutError("ozon-secret")

    svc = service(store, {"ozon": Adapter})
    store.enqueue(event())
    assert svc.process_one("ozon")
    row = store.read({"ozon": "3"})["notifications"][0]
    assert row["status"] == "retry"
    assert "ozon-secret" not in row["error"]
    assert not svc.process_one("ozon")
    store.retry(row["id"], {"ozon": "3"})

    class Ready(Adapter):
        def read(self, _):
            yield snapshot(state="pending_shipment")

    svc.adapters["ozon"] = Ready
    assert svc.process_one("ozon")
    assert store.read({"ozon": "3"})["counts"]["pending_shipment"] == 1


def test_sync_rechecks_tracked_orders_outside_list_window(store):
    save(store, snapshot(state="pending_shipment"))

    class Adapter:
        def __init__(self, _):
            pass

        def sync(self):
            return iter(())

        def read(self, request):
            assert request.resource == "10-1"
            yield snapshot(state="delivered")

    svc = service(store, {"ozon": Adapter})
    store.schedule({"ozon": "3"}, now=time.time())
    assert svc.process_one("ozon")
    assert store.read({"ozon": "3"})["counts"] == {"delivered": 1}


def test_ozon_fbs_fbo_mapping_and_list_pagination(monkeypatch):
    adapter = OzonOrderAdapter(CONFIG)
    calls = []

    def request(path, body):
        calls.append((path, body))
        row = {
            "posting_number": "10-1" if not body.get("cursor") else "10-2",
            "status": "awaiting_packaging",
        }
        if "/fbs/" in path:
            return {"postings": [row], "has_next": not body["cursor"], "cursor": "next"}
        return {"postings": [row], "has_next": False, "cursor": ""}

    monkeypatch.setattr(adapter, "request", request)
    rows = list(adapter.sync())
    assert [row.state for row in rows] == [
        "pending_shipment",
        "pending_shipment",
        "processing",
    ]
    assert calls[1][1]["cursor"] == "next"
    assert calls[0][0] == "/v4/posting/fbs/list"
    assert calls[-1][0] == "/v3/posting/fbo/list"
    assert all("offset" not in body for _, body in calls)
    assert rows[0].identity != rows[2].identity
    assert (
        adapter.normalize({"posting_number": "x", "status": "unexpected"}, "fbs").state
        == "unknown"
    )


def test_yandex_current_business_contract_and_page_token(monkeypatch):
    calls = []

    def request(method, path, token, body, **kwargs):
        calls.append((method, path, body, kwargs))
        cursor = kwargs["query"].get("pageToken")
        return {
            "orders": [
                {
                    "orderId": 11 if not cursor else 12,
                    "campaignId": 4,
                    "programType": "FBS",
                    "status": "PROCESSING",
                    "substatus": "READY_TO_SHIP",
                    "updateDate": "2026-10-05T11:00:00+03:00",
                }
            ],
            "paging": {"nextPageToken": "next"} if not cursor else {},
        }

    monkeypatch.setattr(
        "erp_web.runtime_units.orders_yandex.request_yandex_json", request
    )
    rows = list(YandexOrderAdapter(CONFIG).sync())
    assert len(rows) == 2 and all(row.state == "pending_shipment" for row in rows)
    assert calls[0][1] == "/v1/businesses/5/orders"
    assert calls[0][2] == {"campaignIds": [4]}
    assert calls[1][3]["query"]["pageToken"] == "next"


def test_ml_shipping_detail_and_fraud_prevent_false_pending(monkeypatch):
    adapter = MercadoLibreOrderAdapter(CONFIG)
    shipping = {
        "status": "ready_to_ship",
        "logistic_type": "cross_docking",
        "last_updated": "2026-10-05T10:00:00Z",
    }
    monkeypatch.setattr(adapter, "request", lambda path: shipping)
    row = {"id": 7, "seller": {"id": 1}, "status": "paid", "shipping": {"id": 8}}
    assert adapter.normalize(row).state == "pending_shipment"
    assert (
        adapter.normalize({**row, "tags": ["fraud_risk_detected"]}).state
        == "processing"
    )
    shipping["status"] = "shipped"
    assert adapter.normalize(row).state == "shipped"
    with pytest.raises(ValueError):
        adapter.normalize({**row, "seller": {"id": 99}})


def test_historical_import_is_idempotent_and_keeps_source(store, tmp_path):
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as conn:
        conn.execute(
            "CREATE TABLE order_notifications(id INTEGER,topic TEXT,resource TEXT,raw_json TEXT)"
        )
        conn.execute(
            "INSERT INTO order_notifications VALUES (1,?,?,?)",
            (
                "orders_v2",
                "/orders/7",
                json.dumps({"user_id": "1", "raw": {"_id": "old"}}),
            ),
        )
    import_historical_notifications(store, source)
    import_historical_notifications(store, source)
    assert len(store.read({"mercadolibre": "1"})["notifications"]) == 1
    with sqlite3.connect(source) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM order_notifications").fetchone()[0] == 1
        )


def test_http_callback_and_reads_are_separate_from_remote_availability(store):
    ctx = get_context()
    svc = service(store, {})
    ctx._order_notifications = svc
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        callback = (
            base + "/api/yandex/notifications?token=" + store.setting("token:yandex")
        )
        response = requests.post(
            callback,
            json={"notificationType": "ORDER_CREATED", "orderId": 10, "campaignId": 4},
            timeout=3,
        )
        assert response.status_code == 200
        assert response.json()["version"] == "1.0.0"
        page = requests.get(base + "/api/orders", timeout=3)
        assert page.status_code == 200 and len(page.json()["notifications"]) == 1
        assert (
            requests.get(base + "/api/mercadolibre/orders", timeout=3).status_code
            == 404
        )
        assert (
            requests.post(
                base + "/api/orders/retry", json={"event_id": -1}, timeout=3
            ).status_code
            == 400
        )
        assert (
            requests.post(
                base + "/api/yandex/notifications",
                json={"notificationType": "PING"},
                timeout=3,
            ).status_code
            == 403
        )
        assert (
            requests.get(
                base + "/api/orders", headers={"Host": "evil.example"}, timeout=3
            ).status_code
            == 403
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("cursor", ["", "repeated"])
def test_ozon_current_cursor_protocol_never_silently_truncates(monkeypatch, cursor):
    adapter = OzonOrderAdapter(CONFIG)
    calls = []

    def request(path, body):
        calls.append(path)
        return {
            "postings": [{"posting_number": str(len(calls)), "status": "delivering"}],
            "has_next": True,
            "cursor": cursor,
        }

    monkeypatch.setattr(adapter, "request", request)
    with pytest.raises(ValueError, match="游标"):
        list(adapter.sync())
    assert all(path == "/v4/posting/fbs/list" for path in calls)


def test_ozon_order_and_fbo_notifications_use_unique_event_id():
    first, _ = parse_notification(
        "ozon",
        {
            "message_type": "TYPE_ORDER_NEW",
            "order_id": 42,
            "order_number": "10-2",
            "uuid": "event-42",
            "seller_id": 3,
        },
        CONFIG,
    )
    repeated = first.model_copy(
        update={"payload": {**first.payload, "time": "2026-10-05T10:00:00Z"}}
    )
    assert first.dedup_key == repeated.dedup_key
    fbo, _ = parse_notification(
        "ozon",
        {
            "message_type": "TYPE_FBO_POSTING_NEW",
            "posting_number": "10-2-1",
            "uuid": "event-43",
            "seller_id": 3,
        },
        CONFIG,
    )
    assert fbo.resource == "10-2-1"


def test_stale_snapshot_renews_live_lease_without_changing_order(store):
    now = time.time()
    save(store, snapshot(state="shipped", updated_at="2026-10-05T10:00:00Z"), now=now)
    store.enqueue(event(sequence="stale"))
    job = store.claim({"ozon": "3"}, now=now)
    assert store.save_snapshot(
        job,
        snapshot(state="pending_shipment", updated_at="2026-10-05T09:00:00Z"),
        now=now + 170,
    )
    assert store.claim({"ozon": "3"}, now=now + 181) is None
    store.finish(job, now=now + 181)
    page = store.read({"ozon": "3"})
    assert page["items"][0]["state"] == "shipped"
    assert page["notifications"][0]["status"] == "done"


def test_expired_worker_cannot_finish_before_reclaim(store):
    now = time.time()
    store.enqueue(event())
    job = store.claim({"ozon": "3"}, now=now)
    store.finish(job, now=now + 181)
    assert store.read({"ozon": "3"})["notifications"][0]["status"] == "running"
    assert store.claim({"ozon": "3"}, now=now + 182)["claim"] != job["claim"]


def yandex_price_row(prices, count=1):
    return {
        "orderId": 62668010304,
        "campaignId": 4,
        "programType": "FBS",
        "status": "PROCESSING",
        "substatus": "READY_TO_SHIP",
        "prices": prices,
        "items": [{"offerId": "example", "count": count, "prices": prices}],
    }


def money_part(value, currency="CNY"):
    return {"value": value, "currencyId": currency}


def test_yandex_order_amount_includes_subsidy_and_excludes_delivery(store):
    row = yandex_price_row(
        {
            "payment": money_part(48.1),
            "subsidy": money_part(25.9),
            "delivery": {"payment": money_part(10), "subsidy": money_part(5)},
        }
    )
    row["items"] = [
        {
            "offerId": sku,
            "count": 1,
            "prices": {
                "payment": money_part(24.05),
                "subsidy": money_part(12.95),
            },
        }
        for sku in ("sku-a", "sku-b")
    ]
    result = YandexOrderAdapter(CONFIG).normalize(row)
    assert result.amount == "74.00" and result.currency == "CNY"
    assert result.amount_breakdown.payment == "48.10"
    assert result.amount_breakdown.subsidy == "25.90"
    assert [item.amount for item in result.items] == ["37.00", "37.00"]
    # 新金额与明细须经持久化后原样返回，而不是仅在适配器中有效。
    store.enqueue(
        OrderEvent(
            platform="yandex",
            account_id="4",
            topic="ORDER_UPDATED",
            resource="62668010304",
        )
    )
    now = time.time()
    job = store.claim({"yandex": "4"}, now=now)
    assert store.save_snapshot(job, result, now=now)
    saved = store.read({"yandex": "4"})["items"][0]
    assert saved["amount_breakdown"]["subsidy"] == "25.90"
    assert saved["items"][0]["amount"] == "37.00"


def test_yandex_cashback_decimal_sum_is_not_multiplied_by_quantity():
    result = YandexOrderAdapter(CONFIG).normalize(
        yandex_price_row(
            {
                "payment": money_part(0.1),
                "subsidy": money_part(0.2),
                "cashback": money_part(0.3),
            },
            count=3,
        )
    )
    assert result.amount == result.items[0].amount == "0.60"
    assert result.amount_breakdown.cashback == "0.30"


@pytest.mark.parametrize(
    "prices,amount",
    [
        (None, ""),
        ({"subsidy": money_part(25.9)}, ""),
        ({"payment": money_part(0)}, "0.00"),
        ({"payment": money_part(48.1), "subsidy": None, "cashback": None}, "48.10"),
    ],
)
def test_yandex_missing_amount_is_distinct_from_zero(prices, amount):
    result = YandexOrderAdapter(CONFIG).normalize(yandex_price_row(prices))
    assert result.amount == amount
    assert (result.amount_breakdown is None) == (amount == "")


@pytest.mark.parametrize(
    "part",
    [
        money_part("NaN"),
        money_part("Infinity"),
        money_part(-1),
        money_part("oops"),
        money_part(25.9, "RUR"),
    ],
)
def test_yandex_invalid_amount_or_mixed_currency_is_rejected(part):
    from erp_web.schemas.orders import OrderDataError

    with pytest.raises(OrderDataError):
        YandexOrderAdapter(CONFIG).normalize(
            yandex_price_row(
                {
                    "payment": money_part(48.1),
                    "subsidy": part,
                }
            )
        )


def test_persisted_old_order_retains_payment_without_inventing_subsidy(store):
    old = yandex_price_row(None)
    result = YandexOrderAdapter(CONFIG).normalize(old)
    raw = result.model_dump(exclude={"amount_breakdown"})
    raw["amount"] = "48.1"
    raw["currency"] = "CNY"
    for item in raw["items"]:
        for key in ("amount", "currency", "amount_breakdown"):
            item.pop(key)
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?)",
            (
                result.identity,
                "yandex",
                "4",
                result.state,
                json.dumps(raw),
                0,
                "",
            ),
        )
        conn.commit()
    saved = store.read({"yandex": "4"})["items"][0]
    assert saved["amount"] == "48.1"
    assert saved["amount_breakdown"] is None
    assert saved["items"][0]["amount"] == ""


@pytest.mark.parametrize(
    ("shipment", "expected"),
    [
        ({"shipmentDate": "2026-10-13"}, "2026-10-13"),
        (
            {"shipmentDate": "2026-10-13", "shipmentTime": "12:30:00"},
            "2026-10-13T12:30:00",
        ),
        ({"shipmentDate": "2026-10-13", "shipmentTime": None}, "2026-10-13"),
        ({}, ""),
    ],
)
def test_yandex_shipment_preserves_platform_precision(shipment, expected):
    row = yandex_price_row(None)
    row["delivery"] = {"shipment": shipment, "dates": {"toDate": "2026-10-20"}}
    assert YandexOrderAdapter(CONFIG).normalize(row).shipment_deadline == expected


def test_yandex_shipment_date_rejects_invalid_calendar():
    row = yandex_price_row(None)
    row["delivery"] = {"shipment": {"shipmentDate": "2026-02-30"}}
    with pytest.raises(ValueError, match="发货日期或时间格式无效"):
        YandexOrderAdapter(CONFIG).normalize(row)


def test_yandex_resync_fills_old_snapshot_shipment_date(store, monkeypatch):
    row = yandex_price_row(None)
    monkeypatch.setattr(
        "erp_web.runtime_units.orders_yandex.request_yandex_json",
        lambda *args, **kwargs: {"orders": [row]},
    )
    svc = service(store, {"yandex": YandexOrderAdapter})
    for sequence in (1, 2):
        if sequence == 2:
            row["delivery"] = {"shipment": {"shipmentDate": "2026-10-13"}}
        store.enqueue(
            OrderEvent(
                platform="yandex",
                account_id="4",
                topic="ORDER_UPDATED",
                resource="62668010304",
                payload={"sequence": sequence},
            )
        )
        assert svc.process_one("yandex")
    saved = store.read({"yandex": "4"})["items"][0]
    assert saved["shipment_deadline"] == "2026-10-13"


@pytest.mark.parametrize(("site", "prefix"), [("CBT", "/marketplace"), ("MLM", "")])
def test_ml_dispatch_deadline_uses_sla(monkeypatch, site, prefix):
    config = deepcopy(CONFIG)
    config["mercadolibre"]["account_site_id"] = site
    adapter = MercadoLibreOrderAdapter(config)
    calls = []

    def request(path):
        calls.append(path)
        if path.endswith("/sla"):
            return {"expected_date": "2026-10-13T18:00:00-03:00"}
        return {"status": "ready_to_ship", "logistic_type": "cross_docking"}

    monkeypatch.setattr(adapter, "request", request)
    row = {"id": 7, "seller": {"id": 1}, "status": "paid", "shipping": {"id": 8}}
    result = adapter.normalize(row)
    assert calls[-1] == f"{prefix}/shipments/8/sla"
    assert result.shipment_deadline == "2026-10-13T18:00:00-03:00"
    calls.clear()
    adapter.normalize({**row, "status": "cancelled"})
    assert not any(path.endswith("/sla") for path in calls)


def test_ozon_dispatch_date_uses_shipment_field():
    row = {
        "posting_number": "10-1",
        "status": "awaiting_packaging",
        "shipment_date": "2026-10-13T12:00:00Z",
        "delivery_date": "2026-10-20T12:00:00Z",
    }
    assert (
        OzonOrderAdapter(CONFIG).normalize(row, "fbs").shipment_deadline
        == row["shipment_date"]
    )


def test_order_sync_health_uses_business_scope_and_ignores_old_credentials(store):
    from erp_web.runtime_units.order_notifications import order_request_scopes
    from erp_web.schemas.external_requests import RequestFailure
    from erp_web.services.external_request_context import credential_fingerprint
    from dataclasses import replace
    external = get_context().external_requests.store
    scopes = order_request_scopes(CONFIG)
    svc = OrderNotificationService(store, lambda: deepcopy(CONFIG), adapters={}, parser=parse_notification,
                                   start_worker=False, external_store=external, request_scopes=order_request_scopes)
    yandex = scopes['yandex'][0]
    assert yandex.account_id == '5'  # 订单以 campaign 归属，请求控制按 business 归属。
    old = replace(yandex, credential_id=credential_fingerprint('old-key'))
    external.block(old, RequestFailure('YANDEX_AUTH_FAILED', '旧凭据失效', 'credential'))
    external.block(replace(yandex, interface='/unrelated'), RequestFailure('YANDEX_AUTH_FAILED', '其他接口', 'interface'))
    assert next(r for r in svc.sync_status() if r['platform'] == 'yandex')['status'] == 'idle'
    external.block(yandex, RequestFailure('EXTERNAL_TRANSIENT_FAILURE', '网络暂时不可用', 'interface', resume_at=time.time()+60, cooldown_seconds=60))
    health = next(r for r in svc.sync_status() if r['platform'] == 'yandex')
    assert health['status'] == 'cooldown'
    external.request_probe('yandex', '5', {scope.interface for scope in order_request_scopes(CONFIG)['yandex']})
    svc.sync('yandex')
    job = store.claim({'yandex':'4'}, now=time.time())
    assert job is not None
    store.finish(job, now=time.time())
    external.recover('yandex','5','interface',yandex.interface,reason='测试已恢复')
    health = next(r for r in svc.sync_status() if r['platform'] == 'yandex')
    assert health['status'] == 'done' and health['last_success_at']
    assert len(external.blocks()) == 2


def test_order_sync_cooling_does_not_exhaust_retry_and_manual_sync_wakes_job(store):
    from erp_web.runtime_units.order_notifications import order_request_scopes
    from erp_web.schemas.external_requests import ExternalRequestBlocked, RequestFailure
    external = get_context().external_requests.store
    scope = order_request_scopes(CONFIG)['yandex'][0]
    resume = time.time()+120
    failure = RequestFailure('EXTERNAL_TRANSIENT_FAILURE', '临时故障', 'interface', resume_at=resume, cooldown_seconds=120)
    external.block(scope, failure)
    class Adapter:
        def sync(self):
            raise ExternalRequestBlocked(failure)
    svc = OrderNotificationService(store, lambda: deepcopy(CONFIG), adapters={'yandex':lambda c:Adapter()}, parser=parse_notification,
                                   start_worker=False, external_store=external, request_scopes=order_request_scopes)
    svc.sync('yandex')
    with store.connect() as conn:
        conn.execute("UPDATE inbox SET attempts=8")
        conn.commit()
    svc.process_one('yandex')
    row = store.read({'yandex':'4'})['notifications'][0]
    assert row['status'] == 'retry' and row['next_attempt'] == resume
    external.request_probe('yandex', '5', {scope.interface for scope in order_request_scopes(CONFIG)['yandex']})
    svc.sync('yandex')
    assert store.claim({'yandex':'4'}, now=time.time()) is not None
