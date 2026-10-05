"""采购来源及实际采购记录：店铺隔离、冻结事实、幂等与数量边界。"""

import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.server import ThreadingHTTPServer
from threading import Thread

import pytest
import requests

from erp_web.context import get_context
from erp_web.http_handler import Handler
from erp_web.runtime_units.order_source_bindings import bindings_from_publish_job
from erp_web.schemas.order_procurement import ProcurementSource, order_line_key
from erp_web.schemas.orders import OrderEvent, OrderLine, OrderSnapshot
from erp_web.services.order_procurement_service import OrderProcurementService
from erp_web.stores.order_notification_store import OrderNotificationStore
from erp_web.stores.order_procurement_store import OrderProcurementStore


def job(
    platform="yandex", identity="shop-a", source_id="source-red", status="published"
):
    return {
        "job_id": "job-1",
        "product": {
            "product_id": "p1",
            "source": {
                "source_platform": "1688",
                "source_url": "https://detail.1688.com/offer/123.html",
            },
            "sku_items": [
                {
                    "id": "internal-red",
                    "source_sku_id": source_id,
                    "name": "红色 M",
                    "options": {"颜色": "红色", "尺码": "M"},
                }
            ],
        },
        "approved_publications": {platform: {"store_identity": identity}},
        "platforms": {
            platform: {
                "draft_id": "d1",
                "result": {
                    "sku_results": [
                        {"status": status, "sku_id": "internal-red", "sku": "SALE-1"}
                    ]
                },
            }
        },
    }


@pytest.fixture
def domain(tmp_path):
    orders = OrderNotificationStore(tmp_path / "orders.sqlite3")
    store = OrderProcurementStore(orders)
    line = OrderLine(sku="SALE-1", remote_id="SALE-1", title="商品", quantity=2)
    value = OrderSnapshot(
        platform="yandex",
        account_id="4",
        order_id="123",
        fulfillment="FBS",
        status="PROCESSING",
        state="pending_shipment",
        items=[line],
    )
    orders.enqueue(
        OrderEvent(platform="yandex", account_id="4", topic="test", resource="123")
    )
    now = time.time()
    claim = orders.claim({"yandex": "4"}, now=now)
    orders.save_snapshot(claim, value, now=now)
    orders.finish(claim, now=now)
    service = OrderProcurementService(
        store, lambda: {"yandex": "4"}, lambda platform: "shop-a"
    )
    return service, value, order_line_key(line)


def manual_source(sku="blue"):
    return {
        "supplier": "供应商",
        "source_platform": "1688",
        "product_url": "https://detail.1688.com/offer/456.html",
        "source_sku_id": sku,
        "specification": "蓝色 L",
    }


def confirm(service, order, key):
    return service.select_source(
        {
            "order_id": order.identity,
            "line_key": key,
            "revision": 0,
            "source": manual_source(),
        }
    )


def purchase_body(order, key, request_id="req-1", quantity=1, revision=1):
    return {
        "order_id": order.identity,
        "line_key": key,
        "revision": revision,
        "request_id": request_id,
        "quantity": quantity,
        "purchase_order_number": "PO-1",
    }


def test_frozen_publication_maps_order_to_actual_source(domain):
    service, order, key = domain
    frozen = job()
    bindings = bindings_from_publish_job(frozen)
    service.store.add_bindings(bindings)
    service.store.add_bindings(bindings)
    frozen["product"]["sku_items"][0]["source_sku_id"] = "changed"
    detail = service.detail(order.identity)
    choice = detail["lines"][0]["selection"]
    assert choice["status"] == "matched"
    assert choice["source"]["source_sku_id"] == "source-red"
    assert choice["source"]["sku_url_verified"] is False
    assert len(choice["candidates"]) == 1
    assert choice["candidates"][0]["sku_id"] == "internal-red"
    result = service.select_source(
        {
            "order_id": order.identity,
            "line_key": key,
            "revision": 0,
            "candidate_id": choice["candidates"][0]["id"],
        }
    )
    assert result["lines"][0]["selection"]["status"] == "confirmed"


def test_binding_requires_confirmed_publish_and_trusted_shop():
    assert bindings_from_publish_job(job(status="pending_confirmation")) == []
    assert bindings_from_publish_job(job(identity="")) == []
    for platform in ("yandex", "ozon", "mercadolibre"):
        result = bindings_from_publish_job(job(platform))
        assert result[0].platform == platform


def test_other_shop_and_ambiguous_sources_never_auto_match(domain):
    service, order, key = domain
    service.store.add_bindings(bindings_from_publish_job(job(identity="shop-b")))
    assert (
        service.detail(order.identity)["lines"][0]["selection"]["status"] == "unmatched"
    )
    service.store.add_bindings(bindings_from_publish_job(job()))
    service.store.add_bindings(bindings_from_publish_job(job(source_id="source-blue")))
    assert (
        service.detail(order.identity)["lines"][0]["selection"]["status"] == "ambiguous"
    )
    with pytest.raises(ValueError):
        service.select_source(
            {
                "order_id": order.identity,
                "line_key": key,
                "revision": 0,
                "candidate_id": "other",
            }
        )
    other = OrderProcurementService(
        service.store, lambda: {"yandex": "another"}, lambda p: "shop-a"
    )
    with pytest.raises(ValueError):
        other.detail(order.identity)
    with pytest.raises(ValueError):
        confirm(other, order, key)


def test_purchase_retains_original_source_when_future_source_changes(domain):
    service, order, key = domain
    confirm(service, order, key)
    result = service.record_purchase(purchase_body(order, key))
    original = result["lines"][0]["records"][0]
    service.select_source(
        {
            "order_id": order.identity,
            "line_key": key,
            "revision": 1,
            "source": manual_source("green"),
        }
    )
    result = service.detail(order.identity)
    assert result["lines"][0]["selection"]["source"]["source_sku_id"] == "green"
    assert result["lines"][0]["records"][0]["source"]["source_sku_id"] == "blue"
    assert service.store.progress(result_order(service, order)) == "partial"
    result = service.cancel_purchase(
        {"order_id": order.identity, "record_id": original["id"]}
    )
    assert result["lines"][0]["purchased_quantity"] == 0
    assert result["lines"][0]["records"][0]["status"] == "cancelled"
    # 作废幂等，记录及当时来源仍保留。
    service.cancel_purchase({"order_id": order.identity, "record_id": original["id"]})
    assert len(service.detail(order.identity)["lines"][0]["records"]) == 1


def result_order(service, order):
    return service.store.order(order.identity, {"yandex": "4"})


def test_duplicate_purchase_and_concurrent_quantity_are_transactional(domain):
    service, order, key = domain
    confirm(service, order, key)
    body = purchase_body(order, key, quantity=2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.record_purchase(body), range(2)))
    assert len(results[-1]["lines"][0]["records"]) == 1
    with pytest.raises(ValueError):
        service.record_purchase(purchase_body(order, key, quantity=1))
    with pytest.raises(ValueError):
        service.record_purchase(purchase_body(order, key, request_id="req-2"))
    assert service.store.progress(result_order(service, order)) == "purchased"


def test_changed_source_revision_and_cancelled_order_block_purchase(domain):
    service, order, key = domain
    confirm(service, order, key)
    with pytest.raises(ValueError):
        confirm(service, order, key)
    with pytest.raises(ValueError):
        service.record_purchase(purchase_body(order, key, revision=2))
    with service.store.orders.connect() as conn:
        raw = order.model_copy(update={"state": "cancelled"}).model_dump_json()
        conn.execute("UPDATE orders SET snapshot=? WHERE id=?", (raw, order.identity))
        conn.commit()
    with pytest.raises(ValueError):
        service.record_purchase(purchase_body(order, key))


def test_repeated_or_missing_order_line_identity_cannot_create_records(domain):
    service, order, key = domain
    with service.store.orders.connect() as conn:
        raw = order.model_copy(
            update={"items": [order.items[0], deepcopy(order.items[0])]}
        ).model_dump_json()
        conn.execute("UPDATE orders SET snapshot=? WHERE id=?", (raw, order.identity))
        conn.commit()
    assert (
        "缺少唯一身份"
        in service.detail(order.identity)["lines"][0]["selection"]["reason"]
    )
    with pytest.raises(ValueError):
        confirm(service, order, key)


@pytest.mark.parametrize(
    "patch",
    [
        {"product_url": "javascript:alert(1)"},
        {"product_url": "https://user:pass@example.com/a"},
        {"sku_url_verified": True},
        {"sku_url": "https://other.example/spec", "sku_url_verified": True},
    ],
)
def test_purchase_links_do_not_invent_verified_sku_deep_links(patch):
    with pytest.raises(ValueError):
        ProcurementSource.model_validate({**manual_source(), **patch})


def test_verified_sku_link_requires_explicit_confirmation():
    source = ProcurementSource.model_validate(
        {
            **manual_source(),
            "sku_url": "https://detail.1688.com/offer/456.html?skuId=123",
            "sku_url_verified": True,
        }
    )
    assert source.sku_url_verified


def test_source_binding_survives_restart(domain):
    service, order, key = domain
    service.store.add_bindings(bindings_from_publish_job(job()))
    confirm(service, order, key)
    service.record_purchase(purchase_body(order, key))
    other = OrderProcurementService(
        OrderProcurementStore(OrderNotificationStore(service.store.orders.path)),
        lambda: {"yandex": "4"},
        lambda p: "shop-a",
    )
    result = other.detail(order.identity)
    assert result["lines"][0]["purchased_quantity"] == 1
    assert result["lines"][0]["selection"]["status"] == "confirmed"


def test_http_detail_source_and_purchase_contract(domain):
    service, order, key = domain
    get_context()._order_procurement = service
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/orders"
    try:
        response = requests.get(
            url + "/detail", params={"order_id": order.identity}, timeout=3
        )
        assert response.status_code == 200
        response = requests.post(
            url + "/select-source",
            json={
                "order_id": order.identity,
                "line_key": key,
                "revision": 0,
                "source": manual_source(),
            },
            timeout=3,
        )
        assert response.status_code == 200
        response = requests.post(
            url + "/record-purchase", json=purchase_body(order, key), timeout=3
        )
        assert (
            response.status_code == 200
            and response.json()["lines"][0]["purchased_quantity"] == 1
        )
        assert (
            requests.post(
                url + "/record-purchase",
                json={"order_id": order.identity, "quantity": -1},
                timeout=3,
            ).status_code
            == 400
        )
        assert (
            requests.get(
                url + "/detail", params={"order_id": "not-owned"}, timeout=3
            ).status_code
            == 400
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_summary_counts_all_failures_and_ignores_list_pagination(domain):
    service, order, _ = domain
    orders = service.store.orders
    for index in range(55):
        orders.enqueue(
            OrderEvent(
                platform="yandex",
                account_id="4",
                topic="test",
                resource=str(index),
                payload={"i": index},
            )
        )
    with orders.connect() as conn:
        conn.execute("UPDATE inbox SET status='failed' WHERE status='queued'")
        conn.commit()
    summary = orders.summary({"yandex": "4"})
    assert summary["attention_count"] == 55
    assert len(summary["recent"]) == 1
    assert "notifications" not in summary
    assert orders.read({"yandex": "4"}, query="SALE-1")["total"] == 1
    assert orders.read({"yandex": "4"}, query="absent")["total"] == 0
