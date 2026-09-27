"""Mercado 同步的分页完整性、有界并发与两种商品模型回归。"""
from copy import deepcopy
from threading import Barrier, Lock
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from erp_web.context import get_context
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.runtime_units.online_mercadolibre import MercadoOnlineAdapter
from erp_web.runtime_units.online_mercadolibre_read import catalog_pages
from erp_web.services.online_product_service import OnlineProductService


class Platform:
    def __init__(self, count=6):
        self.ids = [f"CBT{i}" for i in range(1, count + 1)]
        self.calls = []
        self.failed = set()
        self.before_detail = lambda: None

    def get(self, path, **kwargs):
        self.calls.append(path)
        if "/items/search?" in path:
            offset = int(parse_qs(urlsplit(path).query).get("scroll_id", [0])[0])
            return {"seller_id": "seller", "results": self.ids[offset:offset + 50], "paging": {"total": len(self.ids)}, "scroll_id": str(offset + 50)}
        self.before_detail()
        key = path.rsplit("/", 1)[-1]
        if key in self.failed:
            raise TimeoutError("商品详情超时")
        if key.startswith("CBT"):
            return {"id": key, "seller_id": "seller", "site_id": "CBT", "title": "测试商品", "status": "active", "price": 12,
                "currency_id": "USD", "available_quantity": 5, "marketplace_items": [
                    {"item_id": "MLM" + key[3:], "site_id": "MLM", "user_id": "market-seller", "logistic_type": "remote"}]}
        return {"id": key, "seller_id": "market-seller", "site_id": "MLM", "cbt_item_id": "CBT" + key[3:], "status": "active",
            "price": 300, "currency_id": "MXN", "net_proceeds": {"amount": 10, "currency_id": "USD"}}


def adapter(platform):
    a = object.__new__(MercadoOnlineAdapter)
    a.account_id = "seller"
    a.get = platform.get
    return a


def test_full_catalog_is_emitted_before_any_detail_and_includes_all_statuses():
    platform = Platform(91); a = adapter(platform)
    stream = a.sync()
    first, second, marker = next(stream), next(stream), next(stream)
    assert [len(b.listings) for b in (first, second)] == [50, 41]
    assert all(row.details_state == "pending" and not any(c.enabled for c in row.capabilities.values()) for b in (first, second) for row in b.listings)
    assert marker.discovery_complete and len(platform.calls) == 2
    assert all("status" not in parse_qs(urlsplit(path).query) for path in platform.calls)
    rows = [row for batch in stream for row in batch.listings]
    assert len(rows) == 91 and len(platform.calls) == 184
    assert all(len(row.markets) == 1 and row.prices[1].kind == "net_proceeds" and row.prices[1].amount == "10" for row in rows)


def test_parallel_window_is_three_and_closing_does_not_queue_entire_store():
    platform = Platform(30); a = adapter(platform)
    original = a.get; barrier = Barrier(3); lock = Lock()
    active = peak = started = 0
    def get(path):
        nonlocal active, peak, started
        if path.startswith("/marketplace/items/CBT"):
            with lock:
                active += 1; started += 1; peak = max(peak, active)
            try:
                barrier.wait(timeout=3)
                return original(path)
            finally:
                with lock:
                    active -= 1
        return original(path)
    a.get = get
    stream = a.sync()
    next(stream); next(stream); batch = next(stream)
    assert batch.listings
    stream.close()
    assert peak == 3 and started == 3


@pytest.mark.parametrize("status", [401, 403, 429])
def test_account_rejection_or_rate_limit_stops_the_remaining_store(status):
    platform = Platform(30); a = adapter(platform)
    calls = []
    def get(path):
        calls.append(path)
        raise PublishAdapterError("AUTH", "平台拒绝访问", details={"http_status": status})
    a.get = get
    with pytest.raises(PublishAdapterError):
        list(a.sync(platform.ids))
    assert 1 <= len(calls) <= 3


def test_early_empty_catalog_page_is_failure_not_complete():
    a = adapter(Platform())
    pages = iter([{"seller_id": "seller", "results": ["CBT1"], "paging": {"total": 2}, "scroll_id": "next"},
        {"seller_id": "seller", "results": [], "paging": {"total": 2}}])
    a.get = lambda _: next(pages)
    stream = catalog_pages(a)
    assert next(stream) == ["CBT1"]
    with pytest.raises(ValueError, match="提前返回空页"):
        next(stream)


def test_user_product_mapping_and_site_prices_survive_parallel_sync():
    a = adapter(Platform())
    responses = {
        "/marketplace/items/CBT1": {"id": "CBT1", "site_id": "CBT", "seller_id": "seller", "status": "active", "user_product_id": "U10"},
        "/marketplace/user-products/U10/mapping": [{"siteless_user_product_id": "U10", "owner_id": "seller", "item_id": "CBT1",
            "user_product_id": "U10", "site_items": [{"item_id": "MLM1", "site_id": "MLM", "user_id": "market-seller"}]}],
        "/user-products/U10": {"id": "U10", "family_name": "同族商品", "family_id": "family", "available_quantity": 8},
        "/marketplace/items/MLM1": {"id": "MLM1", "site_id": "MLM", "seller_id": "market-seller", "cbt_item_id": "CBT1",
            "status": "active", "net_proceeds": {"amount": 11, "currency_id": "USD"}, "shipping": {"logistic_type": "cross_docking"}},
    }
    def get(path, **kwargs):
        if path == "/user-products/U10":
            assert kwargs == {"version": True}
        return deepcopy(responses[path])
    a.get = get
    rows = [row for batch in a.sync(["CBT1"]) if batch.phase == "details" for row in batch.listings]
    assert len(rows) == 1 and not rows[0].errors
    assert rows[0].model == "user_products" and rows[0].stocks[0].writable
    assert rows[0].prices[0].amount == "11" and rows[0].snapshot["user_product"]["family_id"] == "family"
    responses["/marketplace/items/MLM1"]["cbt_item_id"] = "CBT2"
    rows = [row for batch in a.sync(["CBT1"]) if batch.phase == "details" for row in batch.listings]
    assert rows[0].errors and not rows[0].capabilities["content"].enabled


def test_failed_child_preserves_snapshot_and_retry_only_reads_failed_parent():
    platform = Platform(2); a = adapter(platform)
    context = SimpleNamespace(db=get_context().db, config=SimpleNamespace(load_store_config=lambda: {"mercadolibre": {"user_id": "seller"}}))
    service = OnlineProductService(context, adapter_factories={"mercadolibre": lambda _: a}, start_worker=False)
    try:
        old = service.store.save(a.read("CBT1"))
        platform.failed = {"MLM1"}
        def visible():
            assert len(service.store.listings("mercadolibre", "seller")) == 2
            assert service.store.get(old.id).title == old.title
        platform.before_detail = visible
        job = service.sync("mercadolibre", uuid4().hex)["job"]; service.run_once()
        stored = service.store.get(old.id)
        assert (stored.version, stored.synced_at, stored.prices, stored.stocks) == (old.version, old.synced_at, old.prices, old.stocks)
        assert stored.details_state == "failed"
        result = service.store.job(job["id"])
        assert result["status"] == "partial" and result["result"]["completed"] == result["result"]["failed"] == 1
        platform.failed.clear(); platform.calls.clear()
        retry = service.retry(job["id"], uuid4().hex)["job"]; service.run_once()
        assert service.store.job(retry["id"])["status"] == "confirmed"
        assert platform.calls == ["/marketplace/items/CBT1", "/marketplace/items/MLM1"]
    finally:
        service.close()
