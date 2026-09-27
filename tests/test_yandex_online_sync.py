"""批量同步验证请求规模、目录可见性、分页、并发边界及失败后的快照安全。"""
from collections import Counter
from copy import deepcopy
from threading import Barrier, Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest

from erp_web.context import get_context
from erp_web.runtime_units.online_yandex import YandexOnlineAdapter
from erp_web.runtime_units.online_yandex_read import hidden_ids, read_batch
from erp_web.schemas.online_products import ChangeRequest, OnlineListing
from erp_web.services.online_product_changes import validate_changes
from erp_web.services.online_product_service import OnlineProductService
from erp_web.stores.online_product_store import OnlineConflict


class Platform:
    def __init__(self, count=201):
        self.rows = [{"offer": {"offerId": f"sku-{i}", "name": f"商品 {i}", "archived": i == count - 1,
            "groupId": "family", "campaigns": [{"campaignId": 2, "status": "CHECKING" if i % 2 else "PUBLISHED"}]}}
            for i in range(count)]
        self.calls = []
        self.fail_prices = False
        self.before_detail = lambda: None

    def request(self, path, body=None, *, query=None, method="POST"):
        body, query = body or {}, query or {}
        name = path.rsplit("/", 1)[-1]
        self.calls.append((name, deepcopy(body), deepcopy(query), method))
        ids = body.get("offerIds", [])
        if name == "offer-mappings":
            rows = [row for row in self.rows if row["offer"]["offerId"] in ids] if ids else [row for row in self.rows if row["offer"]["archived"] == body["archived"]]
            offset = int(query.get("pageToken", 0))
            page = rows[offset:offset + 100]
            paging = {"nextPageToken": str(offset + 100)} if len(rows) > offset + 100 else {}
            return {"result": {"offerMappings": page, "paging": paging}}
        self.before_detail()
        if name == "hidden-offers":
            return {"result": {"hiddenOffers": [{"offerId": "sku-1"}] if not query.get("offer_id") else []}}
        if name == "offers":
            return {"result": {"offers": [{"offerId": sku, "status": "CHECKING", "campaignPrice": {"value": 15, "currencyId": "CNY"}} for sku in ids]}}
        if name == "offer-cards":
            return {"result": {"offerCards": [{"offerId": sku, "cardStatus": "HAS_CARD_CAN_UPDATE"} for sku in ids]}}
        if name == "offer-prices":
            assert set(body) == {"offerIds"}, "实店 API 禁止 SKU 查询混用其他过滤条件"
            if self.fail_prices:
                raise TimeoutError("价格服务超时")
            return {"result": {"offers": [{"offerId": sku, "price": {"value": 12, "currencyId": "CNY"}} for sku in ids]}}
        if name == "stocks":
            return {"result": {"warehouses": [{"warehouseId": 3, "offers": [{"offerId": sku, "stocks": [{"type": "AVAILABLE", "count": 5}]} for sku in ids]}]}}
        raise AssertionError(path)


def adapter(platform):
    result = object.__new__(YandexOnlineAdapter)
    result.token, result.business, result.campaign = "test", "1", "2"
    result.account_id, result.settings = "1:2", {"currency": "CNY", "onlyDefaultPrice": False}
    result.mode, result.warehouses = "campaign_warehouses", []
    result.request = platform.request
    return result


def test_201_goods_use_16_requests_and_catalog_is_emitted_before_details():
    platform = Platform()
    batches = list(adapter(platform).sync())
    catalog = [row for batch in batches if batch.phase == "catalog" for row in batch.listings]
    details = [row for batch in batches if batch.phase == "details" for row in batch.listings]
    assert len(catalog) == len(details) == 201
    assert all(row.details_state == "pending" and not any(c.enabled for c in row.capabilities.values()) for row in catalog)
    assert all(row.details_state == "ready" for row in details)
    assert details[1].sale_state == "paused"
    assert all(row.stocks[0].quantity == 5 for row in details)
    assert Counter(call[0] for call in platform.calls) == {
        "offer-mappings": 3, "hidden-offers": 1, "offers": 3, "offer-cards": 3, "offer-prices": 3, "stocks": 3,
    }
    assert len(platform.calls) == 16
    assert all(not call[1].get("offerIds") for call in platform.calls if call[0] == "offer-mappings")
    assert any(body["offerIds"] == ["sku-200"] for name, body, _, _ in platform.calls if name == "offer-prices")
    assert [batch.phase for batch in batches[:3]] == ["catalog"] * 3
    assert batches[3].discovery_complete


def test_hidden_pagination_and_loop_detection():
    a = adapter(Platform())
    queries = []
    def request(path, body=None, **kwargs):
        query = kwargs["query"]; queries.append(dict(query))
        if not query.get("pageToken"):
            return {"result": {"hiddenOffers": [{"offerId": "a"}], "paging": {"nextPageToken": "next"}}}
        return {"result": {"hiddenOffers": [{"offerId": "b"}]}}
    a.request = request
    assert hidden_ids(a) == {"a", "b"}
    assert queries[1]["pageToken"] == "next"
    a.request = lambda *args, **kwargs: {"result": {"hiddenOffers": [], "paging": {"nextPageToken": "repeat"}}}
    with pytest.raises(ValueError, match="游标重复"):
        hidden_ids(a)


def test_card_pages_and_single_read_keep_fresh_data_and_request_scope():
    platform = Platform(2); a = adapter(platform)
    original = a.request
    def request(path, body=None, **kwargs):
        if path.endswith('/offer-cards'):
            if not (kwargs.get('query') or {}).get('pageToken'):
                return {"result": {"offerCards": [], "paging": {"nextPageToken": "cards"}}}
        return original(path, body, **kwargs)
    a.request = request
    first = a.read("sku-0")
    platform.rows[0]["offer"]["name"] = "平台刚修改的名称"
    second = a.read("sku-0")
    assert first.title != second.title
    assert second.title == "平台刚修改的名称"
    assert all(body["offerIds"] == ["sku-0"] for _, body, _, _ in platform.calls if "offerIds" in body)
    assert all(query["offer_id"] == "sku-0" for name, _, query, _ in platform.calls if name == 'hidden-offers')


def test_detail_requests_are_concurrent_and_never_exceed_three():
    platform = Platform(2); a = adapter(platform)
    original = a.request
    barrier, lock = Barrier(3), Lock()
    current = peak = 0
    def request(path, body=None, **kwargs):
        nonlocal current, peak
        with lock:
            current += 1
            peak = max(peak, current)
        try:
            if not path.endswith('/stocks'):
                barrier.wait(timeout=5)
            return original(path, body, **kwargs)
        finally:
            with lock:
                current -= 1
    a.request = request
    # 三个独立详情接口先并发，库存随后执行。
    batch = read_batch(a, platform.rows[:1], set())
    assert not batch.errors and peak == 3


@pytest.mark.parametrize('malformed', [None, {}, {"result": {}}, {"result": {"offers": [{"offerId": "other"}]}}])
def test_bad_price_responses_fail_batch_without_single_item_fallback(malformed):
    platform = Platform(3); a = adapter(platform); original = a.request
    a.request = lambda path, body=None, **kwargs: malformed if path.endswith('/offer-prices') else original(path, body, **kwargs)
    result = read_batch(a, platform.rows[:2], set())
    assert set(result.errors) == {'sku-0', 'sku-1'}
    assert not result.listings
    assert not any(name == 'offer-mappings' for name, *_ in platform.calls)


def service_for(a):
    context = SimpleNamespace(db=get_context().db, config=SimpleNamespace(load_store_config=lambda: {
        'yandex': {'business_id': '1', 'campaign_id': '2'},
    }))
    return OnlineProductService(context, adapter_factories={'yandex': lambda _: a}, start_worker=False)


def test_catalog_visible_before_network_details_and_failed_batch_preserves_old_snapshot():
    platform = Platform(3); a = adapter(platform); service = service_for(a)
    old = service.store.save(a.read('sku-0'))
    platform.rows[0]['offer']['name'] = '目录中更新的名称'
    platform.fail_prices = True
    def visible():
        rows = service.store.listings('yandex', '1:2')
        assert len(rows) == 3
        assert all(row.details_state == 'pending' for row in rows)
        assert service.store.get(old.id).title == old.title
    platform.before_detail = visible
    job = service.sync('yandex', uuid4().hex)['job']
    assert service.run_once()
    stored = service.store.get(old.id)
    assert stored.details_state == 'failed'
    assert (stored.version, stored.synced_at, stored.title, stored.prices, stored.stocks) == (old.version, old.synced_at, old.title, old.prices, old.stocks)
    result = service.store.job(job['id'])
    assert result['status'] == 'partial'
    assert result['result']['discovered'] == result['result']['failed'] == 3
    with pytest.raises(ValueError, match='详情尚未完整同步'):
        validate_changes(stored, ChangeRequest(listing_id=old.id, version=old.version, operation='price', scope_id='business', changes={'amount': '13', 'currency': 'CNY'}, idempotency_key='test-key'))
    platform.before_detail = lambda: None
    platform.fail_prices = False
    platform.calls.clear()
    retry = service.retry(job['id'], uuid4().hex)['job']
    service.run_once()
    assert service.store.job(retry['id'])['status'] == 'confirmed'
    assert service.store.get(old.id).details_state == 'ready'
    mapping_calls = [body for name, body, _, _ in platform.calls if name == 'offer-mappings']
    assert len(mapping_calls) == 1 and set(mapping_calls[0]['offerIds']) == {'sku-0', 'sku-1', 'sku-2'}
    service.close()


def test_discovery_failure_is_not_reported_as_complete_and_can_retry_known_goods():
    platform = Platform(201); a = adapter(platform); original = a.request
    def request(path, body=None, **kwargs):
        if (kwargs.get('query') or {}).get('pageToken'):
            raise TimeoutError('第二页超时')
        return original(path, body, **kwargs)
    a.request = request
    service = service_for(a)
    job = service.sync('yandex', uuid4().hex)['job']; service.run_once()
    result = service.store.job(job['id'])
    assert result['status'] == 'partial' and not result['result']['discovery_complete']
    assert len(service.store.listings('yandex', '1:2')) == 100
    assert all(row.details_state == 'failed' for row in service.store.listings('yandex', '1:2'))
    service.close()


def test_active_sync_conflict_names_sync_and_reports_progress():
    service = service_for(adapter(Platform()))
    service.sync('yandex', uuid4().hex)
    with pytest.raises(OnlineConflict, match='正在同步，已处理 0 件'):
        service.sync('yandex', uuid4().hex)
    service.close()


def test_persisted_old_snapshots_default_to_ready_without_changing_version():
    row = OnlineListing(id='id', platform='yandex', account_id='1:2', remote_id='sku', model='business_offer')
    assert OnlineListing.model_validate(row.model_dump(exclude={'details_state'})).details_state == 'ready'


def test_missing_requested_sku_is_recorded_as_failure_and_never_expands_scope():
    platform = Platform(2); a = adapter(platform)
    batches = list(a.sync(['sku-0', 'missing']))
    errors = {sku: error for batch in batches for sku, error in batch.errors.items()}
    assert set(errors) == {'missing'}
    assert [row.remote_id for batch in batches if batch.phase == 'details' for row in batch.listings] == ['sku-0']
    assert all(body.get('offerIds') for name, body, _, _ in platform.calls if name == 'offer-mappings')


def test_business_warehouses_are_batched_and_keep_distinct_stock_scopes():
    platform = Platform(3); a = adapter(platform); original = a.request
    a.mode = 'business'
    a.warehouses = [{'id': wid, 'models': [{'apiAvailability': 'AVAILABLE', 'placementType': 'FBS'}]} for wid in (10, 20)]
    def request(path, body=None, **kwargs):
        if path.endswith('/stocks'):
            platform.calls.append(('stocks', deepcopy(body), {}, 'POST'))
            return {'result': {'partnerWarehouseId': body['partnerWarehouseId'], 'offers': [
                {'offerId': sku, 'stocks': [{'type': 'AVAILABLE', 'count': body['partnerWarehouseId']}]} for sku in body['offerIds']]}}
        return original(path, body, **kwargs)
    a.request = request
    result = read_batch(a, platform.rows, set())
    assert not result.errors and len(result.listings) == 3
    for listing in result.listings:
        assert [(stock.id, stock.quantity, stock.writable) for stock in listing.stocks] == [('10', 10, True), ('20', 20, True)]
    assert len([call for call in platform.calls if call[0] == 'stocks']) == 2


@pytest.mark.parametrize('stock_response', [
    {'warehouses': [{'offers': [{'offerId': 'not-requested'}]}]},
    {'warehouses': [{'offers': []}], 'paging': {'nextPageToken': 'missing-page'}},
    {'warehouses': [{}]},
])
def test_incomplete_or_wrong_stock_identity_cannot_be_reported_as_success(stock_response):
    platform = Platform(2); a = adapter(platform); original = a.request
    a.request = lambda path, body=None, **kwargs: {'result': stock_response} if path.endswith('/stocks') else original(path, body, **kwargs)
    result = read_batch(a, platform.rows, set())
    assert not result.listings and set(result.errors) == {'sku-0', 'sku-1'}
