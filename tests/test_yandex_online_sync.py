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
        self.fail_cards = False
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
            return {"result": {"offers": [{"offerId": sku, "status": "CHECKING", "basicPrice": {"value": 12, "currencyId": "CNY"}, "campaignPrice": {"value": 15, "currencyId": "CNY"}} for sku in ids]}}
        if name == "offer-cards":
            if self.fail_cards:
                raise TimeoutError("卡片服务超时")
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


def test_201_goods_sync_sales_in_three_batches_without_per_sku_detail():
    platform = Platform()
    platform.rows[-1]["offer"]["archived"] = False
    batches = list(adapter(platform).sync())
    catalog = [row for batch in batches if batch.phase == "catalog" for row in batch.listings]
    details = [row for batch in batches if batch.phase == "basic" for row in batch.listings]
    assert len(catalog) == len(details) == 201
    assert all(row.details_state == "pending" and not any(c.enabled for c in row.capabilities.values()) for row in catalog)
    assert all(row.details_state == "unloaded" for row in details)
    assert details[1].sale_state == "paused"
    assert all(row.stocks[0].quantity == 5 and [p.amount for p in row.prices] == ["12", "15"] for row in details)
    assert Counter(call[0] for call in platform.calls) == {
        "offer-mappings": 3, "hidden-offers": 1, "offers": 2, "offer-cards": 2, "stocks": 3,
    }
    assert len(platform.calls) == 11
    assert all(not call[1].get("offerIds") for call in platform.calls if call[0] == "offer-mappings")
    assert [len(body["offerIds"]) for name, body, _, _ in platform.calls if name == "offer-cards"] == [200, 1]
    assert [len(body["offerIds"]) for name, body, _, _ in platform.calls if name == "stocks"] == [100, 100, 1]
    assert any(body["offerIds"] == ["sku-200"] for name, body, _, _ in platform.calls if name == "offer-cards")
    assert [batch.phase for batch in batches[:5]] == ["catalog", "catalog", "basic", "catalog", "basic"]
    assert batches[-1].discovery_complete


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


def test_detail_requests_are_concurrent_and_never_exceed_four():
    platform = Platform(2); a = adapter(platform)
    original = a.request
    barrier, lock = Barrier(4), Lock()
    current = peak = 0
    def request(path, body=None, **kwargs):
        nonlocal current, peak
        with lock:
            current += 1
            peak = max(peak, current)
        try:
            barrier.wait(timeout=5)
            return original(path, body, **kwargs)
        finally:
            with lock:
                current -= 1
    a.request = request
    # 四个独立详情接口并发且不超过单桶上限 4。
    batch = read_batch(a, platform.rows[:1], set())
    assert not batch.errors and peak == 4


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
    platform.fail_cards = True
    def visible():
        rows = service.store.listings('yandex', '1:2')
        assert len(rows) == 2
        assert {row.details_state for row in rows} == {'ready', 'pending'}
        assert service.store.get(old.id).title == old.title
    platform.before_detail = visible
    job = service.sync('yandex', uuid4().hex)['job']
    assert service.run_once()
    stored = service.store.get(old.id)
    assert stored.details_state == 'failed'
    assert (stored.version, stored.synced_at, stored.title, stored.prices, stored.stocks) == (old.version, old.synced_at, old.title, old.prices, old.stocks)
    result = service.store.job(job['id'])
    assert result['status'] == 'failed'
    assert result['result']['discovered'] == result['result']['failed'] == 2
    with pytest.raises(ValueError, match='详情尚未完整同步'):
        validate_changes(stored, ChangeRequest(listing_id=old.id, version=old.version, operation='price', scope_id='business', changes={'amount': '13', 'currency': 'CNY'}, idempotency_key='test-key'))
    platform.before_detail = lambda: None
    platform.fail_cards = False
    platform.calls.clear()
    retry = service.retry(job['id'], uuid4().hex)['job']
    service.run_once()
    assert service.store.job(retry['id'])['status'] == 'confirmed'
    assert service.store.get(old.id).details_state == 'unloaded'
    assert service.store.get(old.id).stocks == old.stocks
    mapping_calls = [body for name, body, _, _ in platform.calls if name == 'offer-mappings']
    assert len(mapping_calls) == 1 and set(mapping_calls[0]['offerIds']) == {'sku-0', 'sku-1'}
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
    assert all(row.details_state == 'unloaded' for row in service.store.listings('yandex', '1:2'))
    assert result['result']['completed'] == 100 and result['result']['failed'] == 0
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
    assert [row.remote_id for batch in batches if batch.phase == 'basic' for row in batch.listings] == ['sku-0']
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


@pytest.mark.parametrize("catalog", ["current", "archived"])
def test_sync_catalog_is_persisted_and_only_selected_directory_is_read(catalog):
    remote = Platform(4)
    client = adapter(remote)
    service = service_for(client)
    # 两类均已有本地快照，另一类必须逐字段保持不变。
    snapshots = [service.store.save(client.read(f"sku-{i}")) for i in range(4)]
    opposite = [row for row in snapshots if row.archived != (catalog == "archived")]
    remote.calls.clear()
    key = uuid4().hex
    job = service.sync("yandex", key, catalog=catalog)["job"]
    assert job["request"]["catalog"] == catalog
    assert service.sync("yandex", key, catalog=catalog)["job"]["id"] == job["id"]
    with pytest.raises(OnlineConflict, match="重复提交键"):
        service.sync("yandex", key, catalog="current" if catalog == "archived" else "archived")
    service.run_once()
    assert service.store.job(job["id"])["status"] == "confirmed"
    expected = {"sku-3"} if catalog == "archived" else {"sku-0", "sku-1", "sku-2"}
    assert [body for name, body, _, _ in remote.calls if name == "offer-mappings"] == [{"archived": catalog == "archived"}]
    assert {sku for _, body, _, _ in remote.calls for sku in body.get("offerIds", [])} == expected
    assert all(service.store.get(row.id).model_dump() == row.model_dump() for row in opposite)
    other = "current" if catalog == "archived" else "archived"
    for read in (service.list, service.read_page):
        assert read("yandex", catalog=catalog)["latest_sync"]["id"] == job["id"]
        assert read("yandex", catalog=other)["latest_sync"] is None
        assert read("yandex", catalog=other)["state"] == "never_synced"
    second = service.sync("yandex", uuid4().hex, catalog=other)["job"]
    service.run_once()
    for read in (service.list, service.read_page):
        assert read("yandex", catalog=catalog)["latest_sync"]["id"] == job["id"]
        assert read("yandex", catalog=other)["latest_sync"]["id"] == second["id"]
    service.close()


def test_archived_retry_preserves_scope_and_rejects_items_restored_in_the_meantime():
    remote = Platform(3)
    client = adapter(remote)
    service = service_for(client)
    remote.fail_cards = True
    job = service.sync("yandex", uuid4().hex, catalog="archived")["job"]
    service.run_once()
    assert service.store.job(job["id"])["status"] == "failed"
    remote.fail_cards = False
    remote.rows[-1]["offer"]["archived"] = False
    restored = service.store.save(client.read("sku-2"))
    remote.calls.clear()
    retry = service.retry(job["id"], uuid4().hex)["job"]
    assert retry["request"] == {"ids": ["sku-2"], "catalog": "archived"}
    service.run_once()
    result = service.store.job(retry["id"])
    assert result["status"] == "failed" and result["result"]["completed"] == 0
    assert "目录" in result["result"]["items"][0]["error"]
    assert remote.calls == [("offer-mappings", {"offerIds": ["sku-2"]}, {}, "POST")]
    assert service.store.get(restored.id).model_dump() == restored.model_dump()
    service.close()


@pytest.mark.parametrize("catalog", ["current", "archived"])
def test_http_and_ai_sync_forward_catalog_to_the_same_domain_queue(monkeypatch, catalog):
    from erp_web.facades import online_product_facade
    from erp_web.http_route_units.online_product_routes import handle_sync
    from erp_web.runtime_units.online_product_capabilities import OnlineProductCapabilityScope, online_products_sync
    from erp_web.schemas.online_product_capabilities import OnlineSyncRequest
    from tests.approval_support import _execution

    service = service_for(adapter(Platform(2)))
    monkeypatch.setattr(online_product_facade, "get_context", lambda: SimpleNamespace(online_products=service))
    output = []
    body = {"platform": "yandex", "catalog": catalog, "idempotency_key": uuid4().hex}
    handler = SimpleNamespace(path="/api/online-products/sync", read_body=lambda: body,
                              send_json=lambda result, status: output.append((result, status)))
    handle_sync(handler)
    response, status = output.pop()
    assert status == 200 and response["job"]["request"]["catalog"] == catalog
    service.run_once()
    reference = online_products_sync(OnlineSyncRequest(platform="yandex", catalog=catalog),
                                    OnlineProductCapabilityScope(lambda: service), _execution())
    assert service.store.job(reference.job_id)["request"]["catalog"] == catalog
    service.close()


@pytest.mark.parametrize("catalog", ["all", "", None, True])
def test_sync_rejects_invalid_catalog_before_enqueue(catalog):
    from erp_web.schemas.requests import validate_request_payload
    from erp_web.schemas.online_product_capabilities import OnlineSyncRequest
    service = service_for(adapter(Platform(2)))
    # 通用 HTTP 校验保留可选字段的 null，由领域校验拒绝。
    if catalog is not None:
        with pytest.raises(ValueError):
            validate_request_payload({"platform": "yandex", "catalog": catalog, "idempotency_key": uuid4().hex}, endpoint="/api/online-products/sync")
    with pytest.raises(ValueError):
        OnlineSyncRequest(platform="yandex", catalog=catalog)
    with pytest.raises(ValueError):
        service.sync("yandex", uuid4().hex, catalog=catalog)
    assert not service.store.jobs("yandex", "1:2")
    service.close()


def test_business_warehouses_keep_separate_stock_status_and_operation_targets(monkeypatch):
    from erp_web.services.online_product_changes import confirmation
    from erp_web.schemas.online_product_capabilities import OnlineReadResult
    remote = Platform(3)
    a = adapter(remote)
    a.mode = 'business'
    a.warehouses = [{'id': wid, 'name': name, 'models': [{'placementType': 'FBS', 'apiAvailability': 'AVAILABLE', 'campaignId': cid}]}
                    for wid, name, cid in [(3, 'Economy', 2), (4, 'Express', 3)]]
    calls = []
    hidden = set()
    def request(path, body=None, *, query=None, method='POST'):
        calls.append((path, deepcopy(body), query, method))
        ids = (body or {}).get('offerIds', [])
        if path.endswith('/offers'):
            return {'result': {'offers': [{'offerId': sku, 'status': 'PUBLISHED' if '/3/' in path else 'NO_STOCKS',
                'campaignPrice': {'value': 15, 'currencyId': 'CNY'}} for sku in ids]}}
        if path.endswith('/stocks'):
            wid = body['partnerWarehouseId']
            return {'result': {'partnerWarehouseId': wid, 'offers': [{'offerId': sku,
                'stocks': [{'type': 'AVAILABLE', 'count': 7 if wid == 4 else 0}]} for sku in ids]}}
        if path.endswith('/hidden-offers'):
            if method == 'POST':
                hidden.update(row['offerId'] for row in body['hiddenOffers'])
                return {'status': 'OK'}
            return {'result': {'hiddenOffers': [{'offerId': sku} for sku in hidden if not (query or {}).get('offer_id') or sku == query['offer_id']]}}
        return remote.request(path, body, query=query, method=method)
    a.request = request
    result = read_batch(a, remote.rows[:2], set())
    assert not result.errors
    first = result.listings[0]
    assert first.sale_state == 'active' and first.raw_status == 'PUBLISHED'
    assert [(s.id, s.campaign_id, s.quantity, s.raw_status) for s in first.stocks] == [('3', '2', 0, 'NO_STOCKS'), ('4', '3', 7, 'PUBLISHED')]
    assert {price.id for price in first.prices} == {'business', 'campaign:2', 'campaign:3'}
    assert len([path for path, _, _, _ in calls if path.endswith('/offers')]) == 2
    assert len([path for path, _, _, _ in calls if path.endswith('/stocks')]) == 2
    assert all(len(body['offerIds']) == 2 for _, body, _, _ in calls if body and 'offerIds' in body)
    service = OnlineProductService(get_context(), adapter_factories={'yandex': lambda _: a}, start_worker=False)
    monkeypatch.setattr(get_context().config, 'load_store_config', lambda: {'yandex': {'business_id': '1', 'campaign_id': '2'}})
    for listing in result.listings:
        service.store.save(listing)
    all_rows = service.list('yandex')
    assert len(all_rows['warehouses']) == 2 and len(all_rows['items'][0]['stocks']) == 2
    filtered = service.list('yandex', warehouse_id='4')
    assert all(row['stocks'][0]['quantity'] == 7 and row['raw_status'] == 'PUBLISHED' for row in filtered['items'])
    assert service.list('yandex', warehouse_id='3')['items'][0]['raw_status'] == 'NO_STOCKS'
    assert OnlineReadResult.model_validate(service.read_page('yandex', warehouse_id='4')).warehouses[1].id == '4'
    command = ChangeRequest(listing_id=first.id, version=first.version, operation='sale_state',
        scope_id='campaign:3', changes={'state': 'paused'}, idempotency_key='warehouse-target')
    validate_changes(first, command)
    a.write_batch(result.listings, command.operation, command.scope_id, command.changes)
    assert calls[-1][0] == '/v2/campaigns/3/hidden-offers'
    assert len(calls[-1][1]['hiddenOffers']) == 2
    checked = a.read_confirmation(first, command)
    assert confirmation(checked, command) == {'state': True}
    assert checked.snapshot['hidden_campaigns']['3'] is True
    assert checked.snapshot['hidden_campaigns']['2'] is False
    assert checked.stocks[0].sale_state == 'unknown' and checked.stocks[1].sale_state == 'paused'
    # 选中仓库库存为零时不能借用其他仓库的数量请求恢复销售。
    with pytest.raises(ValueError, match='库存为零'):
        validate_changes(first, command.model_copy(update={'scope_id': 'campaign:2', 'changes': {'state': 'active'}}))
    # 单件刷新状态更新全部仓库的状态，保留价格、库存数量与详情读取时间。
    saved = service.store.get(first.id)
    status = a.read_status(saved)
    refreshed = service.store.update_status(saved, status)
    assert [stock.quantity for stock in refreshed.stocks] == [0, 7]
    assert refreshed.synced_at == saved.synced_at and refreshed.prices == saved.prices
    assert refreshed.stocks[1].sale_state == 'paused'
    service.close()


def test_no_stocks_and_inactive_warehouses_succeed_and_skip_empty_stock_queries():
    """未铺货或 NO_STOCKS 仓库在列表同步中不误报失败，并跳过确定缺货仓的库存查询。"""
    remote = Platform(3)
    client = adapter(remote)
    client.mode = 'business'
    client.warehouses = [
        {'id': 10, 'name': '主仓', 'models': [{'placementType': 'FBS', 'apiAvailability': 'AVAILABLE', 'campaignId': 2}]},
        {'id': 20, 'name': '空仓', 'models': [{'placementType': 'FBS', 'apiAvailability': 'AVAILABLE', 'campaignId': 3}]},
        {'id': 30, 'name': '停售仓', 'models': [{'placementType': 'FBS', 'apiAvailability': 'AVAILABLE', 'campaignId': 4}]},
    ]
    calls = []
    phase = 1

    def request(path, body=None, *, query=None, method='POST'):
        calls.append((path, deepcopy(body), query, method))
        ids = (body or {}).get('offerIds', [])
        if path.endswith('/offers'):
            if '/2/' in path:
                return {'result': {'offers': [
                    {'offerId': sku, 'status': 'PUBLISHED' if (phase == 1 or sku == 'sku-0') else 'NO_STOCKS',
                     'basicPrice': {'value': 12, 'currencyId': 'CNY'}}
                    for sku in ids
                ]}}
            if '/3/' in path:
                return {'result': {'offers': [{'offerId': sku, 'status': 'NO_STOCKS'} for sku in ids]}}
            return {'result': {'offers': [{'offerId': sku, 'status': 'DISABLED_AUTOMATICALLY'} for sku in ids]}}
        if path.endswith('/stocks'):
            wid = body['partnerWarehouseId']
            if wid == 10:
                return {'result': {'partnerWarehouseId': 10, 'offers': [
                    {'offerId': sku, 'stocks': [{'type': 'AVAILABLE', 'count': 9}]} for sku in ids
                ]}}
            return {'result': {'partnerWarehouseId': wid, 'offers': []}}
        return remote.request(path, body, query=query, method=method)

    client.request = request
    service = service_for(client)
    try:
        job = service.sync('yandex', uuid4().hex)['job']
        service.run_once()
        stored_job = service.store.job(job['id'])
        assert stored_job['status'] == 'confirmed'
        assert stored_job['result']['completed'] == 2 and stored_job['result']['failed'] == 0
        stock_calls = [(body['partnerWarehouseId'], body['offerIds']) for path, body, *_ in calls if path.endswith('/stocks')]
        # 空仓 20 全批为 NO_STOCKS，列表同步完全跳过其库存接口；主仓 10 与停售仓 30 照常核对。
        assert [wid for wid, _ in stock_calls] == [10, 30]
        rows = {row.remote_id: row for row in service.store.listings('yandex', '1:2')}
        for row in rows.values():
            by_wh = {s.id: s for s in row.stocks}
            assert by_wh['10'].quantity == 9 and not by_wh['10'].read_error
            assert by_wh['20'].quantity is None and not by_wh['20'].read_error and by_wh['20'].writable
            assert by_wh['30'].quantity is None and not by_wh['30'].read_error
        # 详情读取（scope=details）仍显式查询全部仓库库存，不跳过 NO_STOCKS。
        calls.clear()
        detail = client.read('sku-0')
        assert sorted(body['partnerWarehouseId'] for path, body, *_ in calls if path.endswith('/stocks')) == [10, 20, 30]
        assert detail.stocks[1].quantity is None and not detail.stocks[1].read_error
        # sku-1 在主仓从 PUBLISHED（库存 9）变为 NO_STOCKS 后，不再沿用旧库存 9，也不报失败。
        phase = 2
        calls.clear()
        second = service.sync('yandex', uuid4().hex)['job']
        service.run_once()
        assert service.store.job(second['id'])['status'] == 'confirmed'
        stock_calls = [(body['partnerWarehouseId'], body['offerIds']) for path, body, *_ in calls if path.endswith('/stocks')]
        assert (10, ['sku-0']) in stock_calls
        sku1 = service.store.get(rows['sku-1'].id)
        main_stock = next(s for s in sku1.stocks if s.id == '10')
        assert main_stock.quantity is None and not main_stock.cached_at and not main_stock.read_error
    finally:
        service.close()


def test_hidden_ids_run_concurrently_and_partial_campaign_failure_preserves_healthy_warehouses():
    """多仓隐藏清单并发读取；单仓店铺接口失败时保留健康仓更新与失败仓旧快照，不连坐整批。"""
    remote = Platform(3)
    client = adapter(remote)
    client.mode = 'business'
    client.warehouses = [
        {'id': 10, 'name': '仓A', 'models': [{'placementType': 'FBS', 'apiAvailability': 'AVAILABLE', 'campaignId': 2}]},
        {'id': 20, 'name': '仓B', 'models': [{'placementType': 'FBS', 'apiAvailability': 'AVAILABLE', 'campaignId': 3}]},
    ]
    hidden_barrier = Barrier(2)
    fail_campaign_3 = False
    phase = 1

    def request(path, body=None, *, query=None, method='POST'):
        if path.endswith('/hidden-offers'):
            hidden_barrier.wait(timeout=3)
            return {'result': {'hiddenOffers': []}}
        ids = (body or {}).get('offerIds', [])
        if path.endswith('/offers'):
            if '/3/' in path:
                if fail_campaign_3:
                    raise TimeoutError('仓B店铺接口超时')
                return {'result': {'offers': [
                    {'offerId': sku, 'status': 'PUBLISHED', 'basicPrice': {'value': 12, 'currencyId': 'CNY'},
                     'campaignPrice': {'value': 30, 'currencyId': 'CNY'},
                     'warnings': [{'code': 'W_B', 'message': '仓B历史警告'}]}
                    for sku in ids
                ]}}
            return {'result': {'offers': [
                {'offerId': sku, 'status': 'PUBLISHED', 'basicPrice': {'value': 12 if phase == 1 else 18, 'currencyId': 'CNY'},
                 'campaignPrice': {'value': 15 if phase == 1 else 22, 'currencyId': 'CNY'}}
                for sku in ids
            ]}}
        if path.endswith('/stocks'):
            wid = body['partnerWarehouseId']
            return {'result': {'partnerWarehouseId': wid, 'offers': [
                {'offerId': sku, 'stocks': [{'type': 'AVAILABLE', 'count': 5 if phase == 1 else 8}]} for sku in ids
            ]}}
        return remote.request(path, body, query=query, method=method)

    client.request = request
    service = service_for(client)
    try:
        first = service.sync('yandex', uuid4().hex)['job']
        service.run_once()
        assert service.store.job(first['id'])['status'] == 'confirmed'
        before = {row.remote_id: row for row in service.store.listings('yandex', '1:2')}
        # 第二次同步：仓A 标题/价格/库存均更新，仓B 店铺状态接口超时。
        phase = 2
        fail_campaign_3 = True
        remote.rows[0]['offer']['name'] = '更新后的标题'
        second = service.sync('yandex', uuid4().hex)['job']
        service.run_once()
        result = service.store.job(second['id'])
        assert result['status'] == 'failed' and result['result']['failed'] == 2
        assert all('店铺状态' in item['error'] for item in result['result']['items'])
        after = service.store.get(before['sku-0'].id)
        # 仓A 和基础资料已更新，仓B 保留上次状态、价格与警告而非清空。
        assert after.title == '更新后的标题'
        prices = {p.id: p for p in after.prices}
        assert prices['business'].amount == '18' and not prices['business'].read_error
        assert prices['campaign:2'].amount == '22' and not prices['campaign:2'].read_error
        assert prices['campaign:3'].amount == '30' and prices['campaign:3'].cached_at and '店铺状态' in prices['campaign:3'].read_error
        stocks = {s.id: s for s in after.stocks}
        assert stocks['10'].quantity == 8 and stocks['10'].raw_status == 'PUBLISHED'
        assert stocks['20'].raw_status == 'PUBLISHED' and stocks['20'].quantity == 8
        assert any(issue.code == 'W_B' for issue in after.platform_issues)
    finally:
        service.close()


def test_sync_prefetches_next_catalog_page_while_reading_current_batch():
    """多页目录同步时在当前 200-SKU 详情读取期间预取下一页目录，不阻塞串行翻页。"""
    remote = Platform(301)
    client = adapter(remote)
    overlap = Barrier(2)
    original = remote.request

    def request(path, body=None, *, query=None, method='POST'):
        if path.endswith('/offer-mappings') and (query or {}).get('pageToken') == '200':
            overlap.wait(timeout=3)
        elif path.endswith('/offer-cards') and 'sku-0' in (body or {}).get('offerIds', []):
            assert len(body['offerIds']) == 200
            overlap.wait(timeout=3)
        return original(path, body, query=query, method=method)

    client.request = request
    batches = list(client.sync())
    assert [b.phase for b in batches[:5]] == ['catalog', 'catalog', 'basic', 'catalog', 'basic']
    assert batches[-1].discovery_complete
    assert len([row for b in batches if b.phase == 'basic' for row in b.listings]) == 300


def test_sync_recovers_from_transient_network_and_500_via_managed_read_retry(monkeypatch):
    """在线商品同步通过统一外部请求管理器在瞬时断连和 500 后自动恢复，并记录独立尝试审计。"""
    from dataclasses import replace as dc_replace
    import http.client
    import io
    import json
    import urllib.error
    import urllib.parse
    from erp_web.marketplaces import yandex_http
    from erp_web.services import external_request_manager as erm

    # 缩短重试间隔以便离线回归快速验证真实重试链路。
    original_rc = erm.request_context
    monkeypatch.setattr(erm, "request_context", lambda *a, **kw: dc_replace(original_rc(*a, **kw), retry_delay=0.01))
    attempts: dict[str, int] = {}

    class _Resp(io.BytesIO):
        status = 200
        headers = {}

    def fake_urlopen(req, timeout=None):
        path = urllib.parse.urlparse(req.full_url).path
        attempts[path] = attempts.get(path, 0) + 1
        if path.endswith("/offer-mappings") and attempts[path] == 1:
            raise http.client.IncompleteRead(b"truncated")
        if path.endswith("/offer-cards") and attempts[path] == 1:
            raise urllib.error.HTTPError(req.full_url, 500, "err", {}, io.BytesIO(b"{}"))
        if path.endswith("/offer-mappings"):
            payload = {"status": "OK", "result": {"offerMappings": [
                {"offer": {"offerId": "sku-0", "name": "商品 0", "archived": False, "basicPrice": {"value": 12, "currencyId": "CNY"}}}
            ], "paging": {}}}
        elif path.endswith("/hidden-offers"):
            payload = {"status": "OK", "result": {"hiddenOffers": []}}
        elif path.endswith("/offer-cards"):
            payload = {"status": "OK", "result": {"offerCards": [{"offerId": "sku-0", "cardStatus": "HAS_CARD_CAN_UPDATE"}]}}
        elif path.endswith("/offers"):
            payload = {"status": "OK", "result": {"offers": [{"offerId": "sku-0", "status": "PUBLISHED", "basicPrice": {"value": 12, "currencyId": "CNY"}}]}}
        elif path.endswith("/stocks"):
            payload = {"status": "OK", "result": {"warehouses": [{"warehouseId": 3, "offers": [{"offerId": "sku-0", "stocks": [{"type": "AVAILABLE", "count": 5}]}]}]}}
        else:
            raise AssertionError(path)
        return _Resp(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(yandex_http.urllib.request, "urlopen", fake_urlopen)
    cfg = {"yandex": {"api_token": "tok", "business_id": "1", "campaign_id": "2", "stock_update_mode": "campaign_warehouses"}}
    context = SimpleNamespace(db=get_context().db, config=SimpleNamespace(load_store_config=lambda: cfg))
    service = OnlineProductService(context, adapter_factories={"yandex": YandexOnlineAdapter}, start_worker=False)
    try:
        job = service.sync("yandex", uuid4().hex)["job"]
        assert service.run_once()
        stored = service.store.job(job["id"])
        assert stored["status"] == "confirmed"
        assert stored["result"]["completed"] == 1 and stored["result"]["failed"] == 0
        assert attempts["/v2/businesses/1/offer-mappings"] == 2
        assert attempts["/v2/businesses/1/offer-cards"] == 2
        audit = get_context().external_requests.store.query(operation_id=job["id"])["items"]
        assert any(row["attempt"] == 2 and row["interface"] == "/v2/businesses/:id/offer-mappings" for row in audit)
        assert any(row["attempt"] == 2 and row["interface"] == "/v2/businesses/:id/offer-cards" for row in audit)
    finally:
        service.close()


def test_list_page_jobs_omits_retry_count_for_full_sync_and_reports_retry_count_for_targeted_retry():
    """全量同步 request.ids 为 null 时不返回 retry_count=0，仅失败项重试返回实际数量。"""
    remote = Platform(3)
    client = adapter(remote)
    service = service_for(client)
    try:
        full_job = service.sync("yandex", uuid4().hex)["job"]
        jobs = service.store.list_page_jobs("yandex", "1:2", full_job["id"])
        assert len(jobs) == 1 and "retry_count" not in jobs[0]["request"]
        service.run_once()
        retry_job = service.sync("yandex", uuid4().hex, ids=["sku-0", "sku-1"])["job"]
        jobs = service.store.list_page_jobs("yandex", "1:2", retry_job["id"])
        assert jobs[0]["request"]["retry_count"] == 2
    finally:
        service.close()


def test_multi_warehouse_sync_batches_200_details_and_accumulates_sparse_stocks_across_pages():
    """多仓同步按 200 合批卡片与店铺状态，并跨目录页按 100 攒批稀疏仓库库存。"""
    remote = Platform(251)
    client = adapter(remote)
    client.mode = "business"
    client.warehouses = [
        {"id": 10, "name": "主仓", "models": [{"placementType": "FBS", "apiAvailability": "AVAILABLE", "campaignId": 2}]},
        {"id": 20, "name": "次仓", "models": [{"placementType": "FBS", "apiAvailability": "AVAILABLE", "campaignId": 3}]},
        {"id": 30, "name": "停售仓", "models": [{"placementType": "FBS", "apiAvailability": "AVAILABLE", "campaignId": 4}]},
        {"id": 40, "name": "纯缺货仓", "models": [{"placementType": "FBS", "apiAvailability": "AVAILABLE", "campaignId": 5}]},
    ]
    calls = []

    def request(path, body=None, *, query=None, method="POST"):
        calls.append((path, deepcopy(body), deepcopy(query), method))
        ids = (body or {}).get("offerIds", [])
        if path.endswith("/offers"):
            cid = path.split("/campaigns/")[1].split("/")[0]
            rows = []
            for sku in ids:
                idx = int(sku.split("-")[1])
                if cid == "2":
                    status = "PUBLISHED" if idx < 180 else "NO_STOCKS"
                elif cid == "3":
                    status = "PUBLISHED" if idx % 7 == 0 else "NO_STOCKS"
                elif cid == "4":
                    status = "DISABLED_AUTOMATICALLY" if idx % 31 == 0 else "NO_STOCKS"
                else:
                    status = "NO_STOCKS"
                rows.append({"offerId": sku, "status": status, "basicPrice": {"value": 12, "currencyId": "CNY"}})
            return {"result": {"offers": rows}}
        if path.endswith("/stocks"):
            wid = body["partnerWarehouseId"]
            assert len(ids) <= 100
            if wid in (10, 20):
                return {"result": {"partnerWarehouseId": wid, "offers": [
                    {"offerId": sku, "stocks": [{"type": "AVAILABLE", "count": wid}]} for sku in ids
                ]}}
            return {"result": {"partnerWarehouseId": wid, "offers": []}}
        return remote.request(path, body, query=query, method=method)

    client.request = request
    service = service_for(client)
    try:
        job = service.sync("yandex", uuid4().hex)["job"]
        assert service.run_once()
        stored = service.store.job(job["id"])
        assert stored["status"] == "confirmed"
        assert stored["result"]["completed"] == 250 and stored["result"]["failed"] == 0
        counts = Counter(path.rsplit("/", 1)[-1] for path, *_ in calls)
        assert counts == {"offer-mappings": 3, "hidden-offers": 4, "offer-cards": 2, "offers": 8, "stocks": 4}
        stock_batches = [(body["partnerWarehouseId"], len(body["offerIds"])) for path, body, *_ in calls if path.endswith("/stocks")]
        assert sorted(stock_batches) == [(10, 80), (10, 100), (20, 36), (30, 9)]
        listings = {row.remote_id: row for row in service.store.listings("yandex", "1:2")}
        assert len(listings) == 250
        sku0 = {s.id: s for s in listings["sku-0"].stocks}
        assert sku0["10"].quantity == 10 and sku0["20"].quantity == 20 and sku0["30"].quantity is None and sku0["40"].quantity is None
        assert all(not s.read_error for row in listings.values() for s in row.stocks)
    finally:
        service.close()


def test_full_sync_prunes_removed_listings_while_protecting_opposite_catalog_and_unresolved_jobs():
    """完整全量同步清理已移出目录的陈旧快照，同时保留另一目录、未结束任务目标，且不完整发现或局部重试绝不清理。"""
    remote = Platform(3)
    client = adapter(remote)
    service = service_for(client)
    try:
        # 预置：普通目录 2 件在店商品 + 2 件已下线旧商品 + 1 件归档商品。
        for sku in ("sku-0", "sku-1", "sku-2"):
            service.store.save(client.read(sku))
        orphan_clean = service.store.get(service.store.listings("yandex", "1:2")[0].id).model_copy(deep=True)
        orphan_clean.remote_id = orphan_clean.seller_sku = "orphan-clean"
        orphan_clean.id = "orphan-clean-id"
        orphan_clean.archived = False
        orphan_clean.sales_checked_at = "2020-01-01T00:00:00Z"
        service.store.save(orphan_clean, full_snapshot=False)
        orphan_busy = orphan_clean.model_copy(deep=True)
        orphan_busy.remote_id = orphan_busy.seller_sku = "orphan-busy"
        orphan_busy.id = "orphan-busy-id"
        service.store.save(orphan_busy, full_snapshot=False)
        # 为 orphan-busy 制造一条未确认修改任务，确认同步不会将其删除。
        busy_job = service.store.enqueue("yandex", "1:2", "price", orphan_busy.id,
                                         {"listing_id": orphan_busy.id, "operation": "price", "changes": {"amount": "15"}}, uuid4().hex)
        claim = service.store.claim("worker")
        service.store.update_job(busy_job["id"], "outcome_unknown", {}, owner=claim["lease_token"], dispatched=True)

        # 1. 局部重试不得清理任何目录外商品。
        retry_job = service.sync("yandex", uuid4().hex, ids=["sku-0"])["job"]
        service.run_once()
        assert service.store.job(retry_job["id"])["status"] == "confirmed"
        assert {row.remote_id for row in service.store.listings("yandex", "1:2")} == {"sku-0", "sku-1", "sku-2", "orphan-clean", "orphan-busy"}

        # 2. 发现阶段中断不得清理任何商品。
        original = client.request
        client.request = lambda path, body=None, **kw: (_ for _ in ()).throw(TimeoutError("目录中断")) if path.endswith("/offer-mappings") else original(path, body, **kw)
        broken_job = service.sync("yandex", uuid4().hex)["job"]
        service.run_once()
        assert service.store.job(broken_job["id"])["status"] == "failed"
        assert service.store.get(orphan_clean.id).remote_id == "orphan-clean"
        client.request = original

        # 3. 完整全量同步普通目录后，清理无未决任务的 orphan-clean，保留 orphan-busy 与归档目录 sku-2。
        full_job = service.sync("yandex", uuid4().hex, catalog="current")["job"]
        service.run_once()
        stored = service.store.job(full_job["id"])
        assert stored["status"] == "confirmed" and stored["result"].get("removed") == 1
        remaining = {row.remote_id: row for row in service.store.listings("yandex", "1:2")}
        assert set(remaining) == {"sku-0", "sku-1", "sku-2", "orphan-busy"}
        assert remaining["sku-2"].archived is True
        assert "orphan-clean-id" not in service.list("yandex")["sales_due_ids"]
    finally:
        service.close()


def test_sales_refresh_skips_empty_warehouses_and_shop_names_cache_respects_token_and_ttl():
    """可见销售刷新同样跳过 NO_STOCKS 与未入店仓库；店铺名称按账号和凭据在 TTL 内复用。"""
    remote = Platform(2)
    client = adapter(remote)
    client.mode = "business"
    client.warehouses = [
        {"id": 10, "name": "主仓", "models": [{"placementType": "FBS", "apiAvailability": "AVAILABLE", "campaignId": 2}]},
        {"id": 20, "name": "空仓", "models": [{"placementType": "FBS", "apiAvailability": "AVAILABLE", "campaignId": 3}]},
    ]
    calls = []

    def request(path, body=None, *, query=None, method="POST"):
        calls.append((path, deepcopy(body)))
        ids = (body or {}).get("offerIds", [])
        if path.endswith("/offers"):
            if "/2/" in path:
                return {"result": {"offers": [
                    {"offerId": sku, "status": "PUBLISHED", "basicPrice": {"value": 12, "currencyId": "CNY"}}
                    for sku in ids if sku == "sku-0"
                ]}}
            return {"result": {"offers": [
                {"offerId": sku, "status": "NO_STOCKS"} for sku in ids if sku == "sku-0"
            ]}}
        if path.endswith("/stocks"):
            wid = body["partnerWarehouseId"]
            return {"result": {"partnerWarehouseId": wid, "offers": [
                {"offerId": sku, "stocks": [{"type": "AVAILABLE", "count": 6}]} for sku in ids
            ]}}
        if path.endswith("/offer-prices"):
            return {"result": {"offers": []}}
        return remote.request(path, body, query=query, method=method)

    client.request = request
    batch = client.read_sales(["sku-0", "dead-sku"])
    stock_calls = [(body["partnerWarehouseId"], body["offerIds"]) for path, body in calls if path.endswith("/stocks")]
    assert stock_calls == [(10, ["sku-0"])]
    assert next(row for row in batch.listings if row.remote_id == "sku-0").stocks[0].quantity == 6

    shop_calls = []
    client.shop_names = lambda: shop_calls.append(client.token) or {"2": "主店"}
    service = service_for(client)
    try:
        assert service.yandex_shop_names("1:2") == {"2": "主店"}
        assert service.yandex_shop_names("1:2") == {"2": "主店"}
        assert shop_calls == ["test"]
        client.token = "rotated-token"
        assert service.yandex_shop_names("1:2") == {"2": "主店"}
        assert shop_calls == ["test", "rotated-token"]
    finally:
        service.close()


def test_full_sync_reconciles_remotely_archived_sku_and_prunes_confirmed_deleted_sku():
    """全量同步缺失项经 offer-mappings 复核：平台改归档的迁入归档目录，已删商品清理，复核失败不误删。"""
    remote = Platform(3)
    remote.rows[0]["offer"]["archived"] = False
    remote.rows[1]["offer"]["archived"] = False
    remote.rows[2]["offer"]["archived"] = True
    client = adapter(remote)
    service = service_for(client)
    try:
        for sku in ("sku-0", "sku-1"):
            service.store.save(client.read(sku))
        sku1_id = next(row.id for row in service.store.listings("yandex", "1:2") if row.remote_id == "sku-1")
        deleted_row = service.store.get(sku1_id).model_copy(deep=True)
        deleted_row.id = "deleted-ghost-id"
        deleted_row.remote_id = deleted_row.seller_sku = "deleted-ghost"
        deleted_row.archived = False
        service.store.save(deleted_row, full_snapshot=False)

        # 卖家在 Yandex 后台直接将 sku-1 归档，而 deleted-ghost 已从平台删除。
        remote.rows[1]["offer"]["archived"] = True

        # 若复核请求出现瞬时网络异常，保守保留原记录，绝不误删。
        original_req = client.request
        def fail_reconcile(path, body=None, **kwargs):
            if path.endswith("/offer-mappings") and (body or {}).get("offerIds"):
                raise TimeoutError("复核超时")
            return original_req(path, body, **kwargs)
        client.request = fail_reconcile
        job_err = service.sync("yandex", uuid4().hex, catalog="current")["job"]
        service.run_once()
        assert service.store.job(job_err["id"])["status"] == "confirmed"
        assert "removed" not in service.store.job(job_err["id"])["result"]
        assert service.store.get("deleted-ghost-id").remote_id == "deleted-ghost"
        assert service.store.get(sku1_id).archived is False

        # 恢复网络后再次全量同步：sku-1 迁移至归档目录，deleted-ghost 清理，list_page_jobs 透出计数。
        client.request = original_req
        job_ok = service.sync("yandex", uuid4().hex, catalog="current")["job"]
        service.run_once()
        stored = service.store.job(job_ok["id"])
        assert stored["status"] == "confirmed"
        assert stored["result"].get("removed") == 1
        assert stored["result"].get("moved") == 1
        assert service.store.get(sku1_id).archived is True
        with pytest.raises(ValueError):
            service.store.get("deleted-ghost-id")
        page_job = service.store.list_page_jobs("yandex", "1:2", job_ok["id"])[0]
        assert page_job["result"]["removed"] == 1 and page_job["result"]["moved"] == 1
    finally:
        service.close()


def test_sales_refresh_on_missing_sku_preserves_status_and_omits_disabled_campaign_price_errors():
    """已删 SKU 触发可见销售刷新时：保留既有停售状态不降级为待核实，且不对未启用独立价的店铺刷屏报错。"""
    remote = Platform(2)
    client = adapter(remote)
    client.settings = {"currency": "CNY", "onlyDefaultPrice": True}
    service = service_for(client)
    try:
        saved = service.store.save(client.read("sku-0"))
        saved.raw_status = "DISABLED_BY_PARTNER"
        saved.sale_state = "paused"
        saved.markets = saved.markets[:1]
        saved.markets[0].raw_status = "DISABLED_BY_PARTNER"
        saved.stocks[0].raw_status = "DISABLED_BY_PARTNER"
        saved.stocks[0].sale_state = "paused"
        saved.prices = [p for p in saved.prices if p.id == "business"]
        saved.prices[0].amount = "200"
        saved.sales_checked_at = "2020-01-01T00:00:00Z"
        service.store.save(saved, full_snapshot=False)

        # 店铺升级为多仓模式后，平台全部店铺、价格、库存均返回 HTTP 200 空结果（典型如商品已在平台删除）。
        client.mode = "business"
        client.warehouses = [
            {"id": 10, "name": "仓A", "models": [{"placementType": "FBS", "apiAvailability": "AVAILABLE", "campaignId": 2}]},
            {"id": 20, "name": "仓B", "models": [{"placementType": "FBS", "apiAvailability": "AVAILABLE", "campaignId": 3}]},
            {"id": 30, "name": "仓C", "models": [{"placementType": "FBS", "apiAvailability": "AVAILABLE", "campaignId": 4}]},
        ]
        def empty_request(path, body=None, **kwargs):
            if path.endswith("/offers") or path.endswith("/offer-prices"):
                return {"result": {"offers": []}}
            if path.endswith("/stocks"):
                return {"result": {"partnerWarehouseId": body["partnerWarehouseId"], "offers": []}}
            return remote.request(path, body, **kwargs)
        client.request = empty_request

        assert service.sales.refresh("1:2", [saved.id])["ok"]
        after = service.store.get(saved.id)
        assert after.raw_status == "DISABLED_BY_PARTNER" and after.sale_state == "paused"
        assert len(after.stocks) == 1 and after.stocks[0].sale_state == "paused"
        # 投影后的列表视图同样保持停售而非待核实。
        listed = service.list("yandex")["items"][0]
        assert listed["raw_status"] == "DISABLED_BY_PARTNER" and listed["sale_state"] == "paused"
        # 仅账号基础价保留旧值与读取异常，其余未启用独立价的店铺不挂载噪音错误。
        by_price = {p.id: p for p in after.prices}
        assert by_price["business"].amount == "200" and by_price["business"].cached_at and by_price["business"].read_error
        assert all(not p.read_error for pid, p in by_price.items() if pid != "business")
    finally:
        service.close()


def test_yandex_hot_sync_skips_mappings_and_cards_while_updating_prices_stocks_and_status():
    """热数据快刷（mode=hot）零 offer-mappings / offer-cards 调用，保留图文与卡片反馈并更新价格、库存、隐藏与店铺状态。"""
    remote = Platform(202)
    remote.rows[-1]["offer"]["archived"] = True  # 201 normal + 1 archived
    client = adapter(remote)
    client.mode = "business"
    client.warehouses = [
        {"id": 10, "name": "主仓", "models": [{"placementType": "FBS", "apiAvailability": "AVAILABLE", "campaignId": 2}]},
        {"id": 20, "name": "空仓", "models": [{"placementType": "FBS", "apiAvailability": "AVAILABLE", "campaignId": 3}]},
    ]
    phase = 1
    calls = []

    def request(path, body=None, *, query=None, method="POST"):
        calls.append((path, deepcopy(body)))
        ids = (body or {}).get("offerIds", [])
        if path.endswith("/hidden-offers"):
            return {"result": {"hiddenOffers": [{"offerId": "sku-1"}] if (phase == 2 and "/2/" in path) else []}}
        if path.endswith("/offer-cards"):
            return {"result": {"offerCards": [
                {"offerId": sku, "cardStatus": "HAS_CARD_CAN_UPDATE",
                 "warnings": [{"code": "CARD_W", "message": "卡片历史警告"}] if sku == "sku-0" else []}
                for sku in ids
            ]}}
        if path.endswith("/offers"):
            if "/2/" in path:
                return {"result": {"offers": [
                    {"offerId": sku, "status": "PUBLISHED",
                     "basicPrice": {"value": 12 if phase == 1 else 25, "currencyId": "CNY"},
                     "campaignPrice": {"value": 15 if phase == 1 else 29, "currencyId": "CNY"},
                     "warnings": [{"code": "CAMP_W2", "message": "新店铺警告"}] if (phase == 2 and sku == "sku-0") else []}
                    for sku in ids
                ]}}
            return {"result": {"offers": [{"offerId": sku, "status": "NO_STOCKS"} for sku in ids]}}
        if path.endswith("/stocks"):
            wid = body["partnerWarehouseId"]
            return {"result": {"partnerWarehouseId": wid, "offers": [
                {"offerId": sku, "stocks": [{"type": "AVAILABLE", "count": 5 if phase == 1 else 18}]}
                for sku in ids
            ]}}
        return remote.request(path, body, query=query, method=method)

    client.request = request
    service = service_for(client)
    try:
        # 未同步空目录不允许直接发起热刷。
        with pytest.raises(ValueError, match="先同步目录"):
            service.sync("yandex", uuid4().hex, mode="hot")

        # 首次全量同步建立 201 件普通商品快照（含卡片警告与标题）。
        full_job = service.sync("yandex", uuid4().hex, catalog="current")["job"]
        assert service.run_once()
        assert service.store.job(full_job["id"])["status"] == "confirmed"
        before_0 = next(row for row in service.store.listings("yandex", "1:2") if row.remote_id == "sku-0")
        before_1 = next(row for row in service.store.listings("yandex", "1:2") if row.remote_id == "sku-1")

        # 第二阶段仅发起热数据快刷（mode=hot）：完全不调 offer-mappings / offer-cards！
        phase = 2
        calls.clear()
        hot_job = service.sync("yandex", uuid4().hex, catalog="current", mode="hot")["job"]
        assert hot_job["request"]["mode"] == "hot"
        assert service.run_once()
        stored = service.store.job(hot_job["id"])
        assert stored["status"] == "confirmed"
        assert stored["result"]["data_scope"] == "hot"
        assert stored["result"]["completed"] == 201 and stored["result"]["failed"] == 0

        counts = Counter(path.rsplit("/", 1)[-1] for path, _ in calls)
        assert "offer-mappings" not in counts
        assert "offer-cards" not in counts
        assert counts == {"hidden-offers": 2, "offers": 4, "stocks": 3}

        after_0 = service.store.get(before_0.id)
        after_1 = service.store.get(before_1.id)
        # 热数据全部更新（价格、库存、隐藏停售状态、店铺级警告）。
        assert {p.id: p.amount for p in after_0.prices}["business"] == "25"
        assert {p.id: p.amount for p in after_0.prices}["campaign:2"] == "29"
        assert next(s for s in after_0.stocks if s.id == "10").quantity == 18
        assert after_1.sale_state == "paused" and after_1.raw_status == "DISABLED_BY_PARTNER"
        # 冷数据（标题、基础同步时间、卡片警告）完整保留！
        assert after_0.title == before_0.title
        assert after_0.basic_synced_at == before_0.basic_synced_at
        assert after_0.synced_at == before_0.synced_at
        assert {(i.source, i.code) for i in after_0.platform_issues} == {("card", "CARD_W"), ("campaign", "CAMP_W2")}
        # list_page_jobs 透出 mode=hot 与 data_scope=hot。
        page_job = service.store.list_page_jobs("yandex", "1:2", hot_job["id"])[0]
        assert page_job["request"]["mode"] == "hot" and page_job["result"]["data_scope"] == "hot"
    finally:
        service.close()


def test_publish_terminal_schedules_targeted_ingest_without_mixing_publish_status(monkeypatch):
    """发布终态自动为新发 SKU 入队定向同步（不扫全店、不覆盖全店同步时间），在线列表不混入发布任务状态。"""
    from erp_web.runtime_units.publish_bus import persist_publish_bus_terminal_results, published_online_remote_ids
    from erp_web.runtime_units.publish_confirmation import resolve_publish_store_binding
    from erp_web.stores.task_queue_reader import TaskQueueReader

    app = get_context()
    cfg = {"yandex": {"api_token": "tok", "business_id": "1", "campaign_id": "2", "shop_name": "测试店"}}
    monkeypatch.setattr(app.config, "load_store_config", lambda: cfg)
    store_identity = resolve_publish_store_binding("yandex", cfg).identity

    # 1. 验证三平台远端 ID 提取契约。
    extracted = published_online_remote_ids({
        "job_id": "pub-multi",
        "approved_publications": {
            "yandex": {"store_identity": store_identity},
            "ozon": {"store_identity": "ozon:abc"},
            "mercadolibre": {"store_identity": "mercadolibre:def"},
        },
        "platforms": {
            "yandex": {"status": "partial", "result": {"sku_results": [
                {"sku": "YDX-1", "offer_id": "YDX-1", "status": "published"},
                {"sku": "YDX-2", "offer_id": "YDX-2", "status": "failed"},
            ]}},
            "ozon": {"status": "success", "result": {"sku_results": [
                {"sku": "OZ-1", "product_id": 987654, "status": "published"},
            ]}},
            "mercadolibre": {"status": "success", "result": {"sku_results": [
                {"sku": "ML-1", "item_id": "CBT123456", "status": "published"},
            ]}},
        },
    })
    assert extracted == [
        ("yandex", store_identity, ["YDX-1"]),
        ("ozon", "ozon:abc", ["987654"]),
        ("mercadolibre", "mercadolibre:def", ["CBT123456"]),
    ]

    # 2. 真实草稿 + 发布终态落库：自动入队定向同步，并在唤醒后仅拉新发 SKU 落库。
    remote = Platform(3)
    client = adapter(remote)
    service = OnlineProductService(app, adapter_factories={"yandex": lambda _: client}, start_worker=False)
    app._online_products = service
    try:
        # 先做一次全店同步作为基准 latest_sync。
        base_job = service.sync("yandex", uuid4().hex, catalog="current")["job"]
        service.run_once()
        assert service.store.job(base_job["id"])["status"] == "confirmed"

        # 新增一个刚发布成功的 SKU，并触发 persist_publish_bus_terminal_results。
        remote.rows.append({"offer": {"offerId": "sku-new", "name": "刚发布的新品", "archived": False, "campaigns": [{"campaignId": 2, "status": "PUBLISHED"}]}})
        app.db.upsert_product_model({"product_id": "p-new", "source": {"title": "刚发布的新品"}, "sku_items": []})
        draft = {"draft_id": "d-new", "product_id": "p-new", "platform": "yandex", "site": "global",
                 "target_sites": [{"platform": "yandex", "site": "global"}], "sku_items": []}
        app.db.upsert_draft_model("p-new", "yandex", draft)
        job_state = {
            "job_id": "pub-job-1",
            "product_id": "p-new",
            "product": {"product_id": "p-new", "source": {"title": "刚发布的新品"}, "sku_items": []},
            "approved_publications": {"yandex": {"store_identity": store_identity}},
            "platforms": {
                "yandex": {
                    "draft_id": "d-new", "product_id": "p-new", "site": "global",
                    "status": "success", "stage": "finished",
                    "result": {"sku_results": [{"sku_id": "s1", "sku": "sku-new", "offer_id": "sku-new", "status": "published"}]},
                }
            },
        }
        remote.calls.clear()
        persist_publish_bus_terminal_results(job_state, context=app)

        # 确认定向入库任务已排队，标签清晰，且不覆盖 latest_sync_summary。
        tasks = TaskQueueReader(app.db).tasks(source="online")
        ingest_task = next(t for t in tasks if t["title"] == "新发商品自动入库")
        assert ingest_task["status"] == "queued"
        page_before = service.list("yandex", list_only=True)
        assert page_before["latest_sync"]["id"] == base_job["id"]
        queued_ingest = next(j for j in page_before["jobs"] if j["request"].get("source") == "publish")
        assert queued_ingest["request"]["ingest_count"] == 1 and "retry_count" not in queued_ingest["request"]

        # 执行定向入库：只查 sku-new，不扫全店目录，完成后立即可见！
        assert service.run_once()
        mapping_calls = [body for name, body, _, _ in remote.calls if name == "offer-mappings"]
        assert mapping_calls == [{"offerIds": ["sku-new"]}]
        assert any(row.remote_id == "sku-new" for row in service.store.listings("yandex", "1:2"))
        assert service.list("yandex", list_only=True)["latest_sync"]["id"] == base_job["id"]

        # 3. 确认在线列表不再混入发布任务状态（发布状态统一留在任务队列）。
        listed = service.list("yandex", catalog="current", list_only=True)
        assert "publish_items" not in listed
        assert listed["summary"]["total"] == 3  # 仅计真实 online_listings（sku-0, sku-1, sku-new）

        # 4. 店铺正忙（存在排队同步）时，发布终态落库触发自动入库遇到冲突应优雅跳过，不阻断落库。
        busy_sync = service.sync("yandex", uuid4().hex, catalog="current")["job"]
        assert busy_sync["status"] == "queued"
        conflict_job_state = {**job_state, "job_id": "pub-job-conflict"}
        persist_publish_bus_terminal_results(conflict_job_state, context=app)
        assert "terminal_persistence_error" not in conflict_job_state
    finally:
        service.close()


def test_online_list_multi_dimensional_filters(monkeypatch):
    """表格多维筛选（目录、库存、平台反馈、价格、形态）在服务端分页前统一执行并校验枚举。"""
    from erp_web.schemas.online_products import PlatformIssue, PriceScope, StockScope, listing_identity

    app = get_context()
    cfg = {"yandex": {"api_token": "tok", "business_id": "1", "campaign_id": "2", "shop_name": "测试店"}}
    monkeypatch.setattr(app.config, "load_store_config", lambda: cfg)
    service = OnlineProductService(app, adapter_factories={"yandex": lambda _: adapter(Platform(1))}, start_worker=False)
    try:
        def make_row(remote_id: str, **kwargs) -> OnlineListing:
            return OnlineListing(
                id=listing_identity("yandex", "1:2", remote_id),
                platform="yandex", account_id="1:2", remote_id=remote_id, model="business_offer",
                seller_sku=remote_id, title=f"商品 {remote_id}", raw_status=kwargs.pop("raw_status", "PUBLISHED"),
                sale_state=kwargs.pop("sale_state", "active"),
                prices=kwargs.pop("prices", [PriceScope(id="business", label="基础价", amount="48", currency="CNY", kind="base_price")]),
                stocks=kwargs.pop("stocks", [StockScope(id="w1", label="主仓", quantity=10, campaign_id="2", raw_status="PUBLISHED")]),
                snapshot=kwargs.pop("snapshot", {"offer": {"offerId": remote_id}}),
                **kwargs,
            )

        # 组合商品（2 个 SKU）：g-1 有库存/无异常，g-2 零库存/有平台警告/报价读取异常。
        service.store.save(make_row("g-1", snapshot={"offer": {"offerId": "g-1", "groupId": "grp-1"}}))
        service.store.save(make_row(
            "g-2", raw_status="NO_STOCKS", sale_state="unknown",
            prices=[PriceScope(id="business", label="基础价", amount="48", currency="CNY", kind="base_price", read_error="未返回")],
            stocks=[StockScope(id="w1", label="主仓", quantity=0, campaign_id="2", raw_status="NO_STOCKS")],
            platform_issues=[PlatformIssue(severity="warning", source="card", code="WARN_1", message="补充说明")],
            snapshot={"offer": {"offerId": "g-2", "groupId": "grp-1"}},
        ))
        # 单品：库存未知、缺报价、有平台错误。
        service.store.save(make_row(
            "s-err", raw_status="REJECTED_BY_MARKET", sale_state="unknown",
            prices=[PriceScope(id="business", label="基础价", amount=None, currency="CNY", kind="base_price")],
            stocks=[StockScope(id="w1", label="主仓", quantity=None, campaign_id="2", raw_status="REJECTED_BY_MARKET")],
            platform_issues=[PlatformIssue(severity="error", source="card", code="ERR_1", message="属性缺失")],
        ))
        # 归档单品：与普通目录隔离。
        service.store.save(make_row("s-arch", archived=True, raw_status="ARCHIVED", sale_state="unknown"))

        ids = lambda **kw: {row["remote_id"] for row in service.list("yandex", **kw)["items"]}
        assert ids(stock_state="in_stock") == {"g-1"}
        assert ids(stock_state="out_of_stock") == {"g-2"}
        assert ids(stock_state="unknown") == {"s-err"}
        assert ids(issue_state="attention") == {"g-2", "s-err"}
        assert ids(issue_state="error") == {"s-err"}
        assert ids(issue_state="warning") == {"g-2"}
        assert ids(issue_state="clean") == {"g-1"}
        assert ids(price_state="priced") == {"g-1", "g-2"}
        assert ids(price_state="missing") == {"s-err"}
        assert ids(price_state="read_error") == {"g-2"}
        assert ids(group_kind="group") == {"g-1", "g-2"}
        assert ids(group_kind="single") == {"s-err"}
        assert ids(group_kind="group", stock_state="out_of_stock", issue_state="warning") == {"g-2"}
        assert ids(catalog="archived", group_kind="single") == {"s-arch"}

        with pytest.raises(ValueError, match="库存状态"):
            service.list("yandex", stock_state="invalid")
        with pytest.raises(ValueError, match="平台反馈"):
            service.list("yandex", issue_state="invalid")
        with pytest.raises(ValueError, match="价格状态"):
            service.list("yandex", price_state="invalid")
        with pytest.raises(ValueError, match="商品形态"):
            service.list("yandex", group_kind="invalid")
    finally:
        service.close()


def test_yandex_hot_sync_pipelines_four_warehouses_merges_prices_reconciles_ghosts_and_caches_warehouses(monkeypatch):
    """验证热刷 4 仓扁平并行 + 合并 offer-prices + 自动复核清理幽灵 SKU + 仓库目录 15 分钟缓存（全量刷新）。"""
    from erp_web.runtime_units.online_yandex import YandexOnlineAdapter
    from erp_web.marketplaces import yandex_http as yandex_api
    from erp_web.schemas.online_products import listing_identity

    # 构造 450 个活跃 SKU（3 个 200-SKU 分片）+ 1 个已删幽灵 + 1 个已归档幽灵。
    remote = Platform(451)
    # remote.rows[-1] 默认 archived=True，再加一个显式归档 SKU 模拟本地误留在 current 的旧快照。
    live_skus = [row["offer"]["offerId"] for row in remote.rows if not row["offer"]["archived"]]
    ghost_deleted = "sku-ghost-deleted"
    ghost_archived = "sku-ghost-archived"
    remote.rows.append({"offer": {"offerId": ghost_archived, "name": "已归档旧品", "archived": True, "campaigns": []}})

    warehouses = [
        {"id": 10 + i, "name": f"仓{i}", "models": [{"placementType": "FBS", "apiAvailability": "AVAILABLE", "campaignId": 2 + i}]}
        for i in range(4)
    ]
    wh_fetch_count = 0
    monkeypatch.setattr(yandex_api, "fetch_yandex_partner_warehouses", lambda token, business: _bump_wh())

    def _bump_wh():
        nonlocal wh_fetch_count
        wh_fetch_count += 1
        return deepcopy(warehouses)

    # 验证 4 仓能同时并行（无 3+1 二波阻塞）且同窗多批能同时在飞。
    wave_barrier = Barrier(4)
    barrier_sku = [""]
    camp_lock = Lock()
    camp_active = camp_peak = 0
    offer_price_calls = []
    mapping_calls = []

    def make_adapter(cfg):
        client = YandexOnlineAdapter(cfg)
        client.mode = "business"
        client.settings = {}
        def request(path, body=None, *, query=None, method="POST"):
            nonlocal camp_active, camp_peak
            body, query = body or {}, query or {}
            name = path.rsplit("/", 1)[-1]
            if name == "offer-mappings":
                mapping_calls.append(deepcopy(body))
                return remote.request(path, body, query=query, method=method)
            if name == "hidden-offers":
                return {"result": {"hiddenOffers": []}}
            if name == "offers":
                with camp_lock:
                    camp_active += 1
                    camp_peak = max(camp_peak, camp_active)
                try:
                    # 首个 4 仓同批请求必须能够 4 并发到达（打破旧 MAX_REQUESTS=3 瓶颈）。
                    if barrier_sku[0] and barrier_sku[0] in body.get("offerIds", []):
                        wave_barrier.wait(timeout=5)
                    ids = body.get("offerIds", [])
                    cid = path.split("/")[3]
                    offers = []
                    for sku in ids:
                        if sku in (ghost_deleted, ghost_archived):
                            continue
                        # 只有 2 个仓在售，另外 2 个仓 NO_STOCKS；并让前 2 个 SKU 缺少 basicPrice 测试跨批合并 offer-prices。
                        status = "PUBLISHED" if cid in ("2", "3") else "NO_STOCKS"
                        row = {"offerId": sku, "status": status}
                        if sku not in (live_skus[0], live_skus[250]):
                            row["basicPrice"] = {"value": 88, "currencyId": "CNY"}
                        offers.append(row)
                    return {"result": {"offers": offers}}
                finally:
                    with camp_lock:
                        camp_active -= 1
            if name == "stocks":
                wid = body["partnerWarehouseId"]
                return {"result": {"partnerWarehouseId": wid, "offers": [
                    {"offerId": sku, "stocks": [{"type": "AVAILABLE", "count": 9}]}
                    for sku in body["offerIds"]
                ]}}
            if name == "offer-prices":
                ids = list(body.get("offerIds", []))
                offer_price_calls.append(ids)
                return {"result": {"offers": [
                    {"offerId": sku, "price": {"value": 99, "currencyId": "CNY"}}
                    for sku in ids if sku not in (ghost_deleted, ghost_archived)
                ]}}
            return remote.request(path, body, query=query, method=method)
        client.request = request
        return client

    app = get_context()
    cfg = {"yandex": {"api_token": "tok-hot-opt", "business_id": "1", "campaign_id": "2", "stock_update_mode": "business"}}
    monkeypatch.setattr(app.config, "load_store_config", lambda: cfg)
    service = OnlineProductService(app, adapter_factories={"yandex": make_adapter}, start_worker=False)
    try:
        # 种子写入本地 450 活跃 + 2 幽灵记录（模拟旧全量快照）。
        seed_rows = [
            OnlineListing(
                id=listing_identity("yandex", "1:2", sku),
                platform="yandex", account_id="1:2", remote_id=sku, model="business_offer",
                seller_sku=sku, title=f"商品 {sku}", raw_status="PUBLISHED", sale_state="active",
                snapshot={"offer": {"offerId": sku, "archived": False}},
            )
            for sku in (*live_skus, ghost_deleted, ghost_archived)
        ]
        for row in seed_rows:
            service.store.save(row, full_snapshot=False)
        barrier_sku[0] = service.store.listings("yandex", "1:2")[0].remote_id

        # 第一次热刷：拉一次 warehouses（入 15 分钟缓存），4 仓同波并发 >= 4，跨分片合并为 1 次 offer-prices，自动复核清理 2 幽灵。
        job1 = service.sync("yandex", uuid4().hex, catalog="current", mode="hot")["job"]
        assert service.run_once()
        res1 = service.store.job(job1["id"])
        assert res1["status"] == "confirmed" and res1["result"]["failed"] == 0
        assert res1["result"]["completed"] == len(live_skus)
        assert res1["result"]["removed"] == 1 and res1["result"]["moved"] == 1
        assert camp_peak >= 4
        # 跨批缺失价格的 2 个真实 SKU + 2 个幽灵 SKU 合并在单次 offer-prices 调用中，不再每批单独发！
        assert len(offer_price_calls) == 1
        assert set(offer_price_calls[0]) == {live_skus[0], live_skus[250], ghost_deleted, ghost_archived}
        # 仅对 2 个全仓均无记录的幽灵 SKU 发起 1 次定向 offer-mappings 复核。
        assert mapping_calls == [{"offerIds": sorted([ghost_deleted, ghost_archived])}]
        assert wh_fetch_count == 1
        # 已删幽灵被移除，已归档幽灵被迁入归档目录。
        current_ids = {row.remote_id for row in service.store.listings("yandex", "1:2") if not row.archived}
        archived_ids = {row.remote_id for row in service.store.listings("yandex", "1:2") if row.archived}
        assert ghost_deleted not in current_ids and ghost_deleted not in archived_ids
        assert ghost_archived in archived_ids and ghost_archived not in current_ids

        # 第二次热刷：命中 15 分钟 warehouses 缓存（不重拉 warehouses），且无幽灵 SKU → 0 次 offer-mappings！
        mapping_calls.clear()
        offer_price_calls.clear()
        barrier_sku[0] = service.store.listings("yandex", "1:2")[0].remote_id
        wave_barrier.reset()
        job2 = service.sync("yandex", uuid4().hex, catalog="current", mode="hot")["job"]
        assert service.run_once()
        res2 = service.store.job(job2["id"])
        assert res2["status"] == "confirmed" and res2["result"]["failed"] == 0
        assert wh_fetch_count == 1  # 命中仓库目录缓存！
        assert mapping_calls == []  # 零目录请求！

        # 全量同步（mode="full"）主动刷新仓库目录缓存。
        full_adapter = service._adapter("yandex", "1:2", refresh_warehouses=True)
        assert len(full_adapter.warehouses) == 4
        assert wh_fetch_count == 2
    finally:
        service.close()
