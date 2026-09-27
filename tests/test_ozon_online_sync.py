"""Ozon 批量请求契约回归；替身用于验证分页和失败行为，不代表实店已接通。"""
from collections import Counter
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from erp_web.context import get_context
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.runtime_units.online_ozon import OzonOnlineAdapter
from erp_web.runtime_units.online_ozon_read import catalog_pages, read_batch
from erp_web.schemas.online_products import snapshot_version
from erp_web.services.online_product_service import OnlineProductService


class Platform:
    def __init__(self, count=200):
        self.ids = list(range(1, count + 1))
        self.archived = set()
        self.calls = []
        self.failed = set()
        self.before_detail = lambda: None

    def request(self, path, body):
        self.calls.append((path, deepcopy(body)))
        if path == "/v3/product/list":
            # ALL 可能已经包含归档项，必须与 ARCHIVED 的扫描结果去重。
            ids = self.ids if body["filter"]["visibility"] == "ALL" else sorted(self.archived)
            offset = int(body["last_id"] or 0)
            return {"result": {"items": [{"product_id": key, "offer_id": f"sku-{key}"} for key in ids[offset:offset + 100]],
                "total": len(ids), "last_id": str(offset + 100)}}
        self.before_detail()
        if path == "/v3/product/info/list":
            return {"items": [{"id": key, "offer_id": f"sku-{key}", "name": f"商品 {key}", "is_archived": key in self.archived,
                "images": [], "statuses": {"status": "processing"}} for key in reversed(body["product_id"])]}
        if path == "/v5/product/info/prices":
            ids = body["filter"]["product_id"]
            assert all((key in self.archived) == (body["filter"]["visibility"] == "ARCHIVED") for key in ids)
            rows = [{"product_id": key, "price": {"price": str(key + 10), "currency_code": "RUB"}} for key in reversed(ids) if key not in self.failed]
            return {"items": rows, "total": len(rows), "cursor": ""}
        raise AssertionError(path)


def adapter(platform):
    a = object.__new__(OzonOnlineAdapter)
    a.account_id = "seller"
    a.request = platform.request
    return a


def test_200_goods_need_four_detail_requests_and_catalog_is_visible_first():
    platform = Platform(); a = adapter(platform); stream = a.sync()
    catalog = []
    for batch in stream:
        if batch.discovery_complete:
            break
        catalog.extend(batch.listings)
    assert len(catalog) == 200
    assert all(row.details_state == "pending" and row.seller_sku for row in catalog)
    assert all(not any(c.enabled for c in row.capabilities.values()) for row in catalog)
    assert all(path == "/v3/product/list" for path, _ in platform.calls)
    details = [row for batch in stream for row in batch.listings]
    assert len(details) == 200
    assert Counter(path for path, _ in platform.calls) == {"/v3/product/list": 3, "/v3/product/info/list": 2, "/v5/product/info/prices": 2}
    assert all(row.prices[0].amount == str(int(row.remote_id) + 10) for row in details)
    assert all(row.raw_status == "processing" for row in details)


def test_archived_deduplication_and_targeted_retry_preserve_scope():
    platform = Platform(3); platform.archived = {3}; a = adapter(platform)
    batches = list(a.sync())
    assert [row.remote_id for batch in batches if batch.phase == "catalog" for row in batch.listings] == ["1", "2", "3"]
    details = [row for batch in batches if batch.phase == "details" for row in batch.listings]
    assert len(details) == 3 and next(row for row in details if row.remote_id == "3").raw_status == "archived"
    platform.calls.clear()
    batches = list(a.sync(["3", "3"]))
    assert len([row for batch in batches if batch.phase == "details" for row in batch.listings]) == 1
    assert platform.calls == [
        ("/v3/product/info/list", {"product_id": [3]}),
        ("/v5/product/info/prices", {"filter": {"product_id": [3], "visibility": "ARCHIVED"}, "cursor": "", "limit": 100}),
    ]


@pytest.mark.parametrize("bad_rows", [None, [{"id": 99}], [{"id": 1}, {"id": 1}], [{}]])
def test_invalid_detail_identity_fails_batch_without_single_item_fallback(bad_rows):
    a = adapter(Platform(2)); calls = []
    def request(path, body):
        calls.append(path)
        return {"items": bad_rows}
    a.request = request
    batch = read_batch(a, ["1", "2"])
    assert not batch.listings and set(batch.errors) == {"1", "2"}
    assert calls == ["/v3/product/info/list"]


def test_missing_detail_or_price_is_an_individual_failure():
    platform = Platform(3); platform.failed = {2}; a = adapter(platform); original = a.request
    def request(path, body):
        result = original(path, body)
        if path == "/v3/product/info/list":
            result["items"] = [row for row in result["items"] if row["id"] != 3]
        return result
    a.request = request
    batch = read_batch(a, ["1", "2", "3"])
    assert [row.remote_id for row in batch.listings] == ["1"]
    assert set(batch.errors) == {"2", "3"}
    assert "详情" in batch.errors["3"] and "价格" in batch.errors["2"]


def test_price_pagination_is_followed_and_mismatched_rows_fail_closed():
    platform = Platform(3); a = adapter(platform); original = a.request
    cursors = []
    def request(path, body):
        if path == "/v5/product/info/prices":
            cursors.append(body["cursor"])
            key = 1 if not body["cursor"] else 2
            return {"items": [{"product_id": key, "price": {"price": str(key), "currency_code": "RUB"}}], "cursor": "next" if key == 1 else "", "total": 2}
        return original(path, body)
    a.request = request
    batch = read_batch(a, ["1", "2"])
    assert not batch.errors and len(batch.listings) == 2 and cursors == ["", "next"]
    a.request = lambda path, body: {"items": [{"product_id": 99}]} if path.endswith("/prices") else original(path, body)
    batch = read_batch(a, ["1", "2"])
    assert not batch.listings and set(batch.errors) == {"1", "2"}


@pytest.mark.parametrize("second", [
    {"items": [], "total": 2},
    {"items": [{"product_id": 1}], "total": 2, "last_id": "same"},
])
def test_truncated_or_repeated_catalog_does_not_report_completion(second):
    a = adapter(Platform()); pages = iter([
        {"items": [{"product_id": 1}], "total": 2, "last_id": "same"}, second,
    ])
    a.request = lambda *args: {"result": next(pages)}
    stream = catalog_pages(a); assert next(stream) == [{"product_id": 1}]
    with pytest.raises(ValueError):
        next(stream)


def test_missing_catalog_cursor_does_not_skip_later_pages():
    a = adapter(Platform()); a.request = lambda *args: {"result": {"items": [{"product_id": 1}], "total": 2}}
    stream = catalog_pages(a); next(stream)
    with pytest.raises(ValueError, match="游标缺失"):
        next(stream)


def test_batch_timeout_is_not_replayed_and_auth_rejection_stops_later_batches():
    platform = Platform(); a = adapter(platform); calls = []
    def timeout(path, body):
        calls.append(path)
        raise TimeoutError("平台超时")
    a.request = timeout
    batch = read_batch(a, ["1", "2"])
    assert len(batch.errors) == 2 and len(calls) == 1
    def forbidden(path, body):
        calls.append(path)
        raise PublishAdapterError("AUTH", "API 已禁用", details={"http_status": 403})
    a.request = forbidden; calls.clear()
    with pytest.raises(PublishAdapterError):
        list(a.sync([str(key) for key in platform.ids]))
    assert calls == ["/v3/product/info/list"]


def test_single_read_and_batch_have_same_business_version():
    a = adapter(Platform(2))
    batch = read_batch(a, ["1", "2"])
    assert snapshot_version(a.read("1")) == snapshot_version(next(row for row in batch.listings if row.remote_id == "1"))


def test_failure_preserves_old_snapshot_and_retry_does_not_rescan_directory():
    platform = Platform(2); a = adapter(platform)
    context = SimpleNamespace(db=get_context().db, config=SimpleNamespace(load_store_config=lambda: {"ozon": {"client_id": "seller"}}))
    service = OnlineProductService(context, adapter_factories={"ozon": lambda _: a}, start_worker=False)
    try:
        old = service.store.save(a.read("1")); platform.failed = {1}
        def visible():
            assert len(service.store.listings("ozon", "seller")) == 2
            assert service.store.get(old.id).title == old.title
        platform.before_detail = visible
        job = service.sync("ozon", uuid4().hex)["job"]; service.run_once()
        stored = service.store.get(old.id)
        assert stored.details_state == "failed"
        assert (stored.version, stored.synced_at, stored.prices) == (old.version, old.synced_at, old.prices)
        result = service.store.job(job["id"])
        assert result["status"] == "partial" and result["result"]["completed"] == result["result"]["failed"] == 1
        platform.failed.clear(); platform.calls.clear()
        retry = service.retry(job["id"], uuid4().hex)["job"]; service.run_once()
        assert service.store.job(retry["id"])["status"] == "confirmed"
        assert len(platform.calls) == 2
        assert platform.calls[0] == ("/v3/product/info/list", {"product_id": [1]})
    finally:
        service.close()
