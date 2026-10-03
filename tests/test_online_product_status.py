"""单件状态刷新只更新目标状态，失败及并发变化不得覆盖已存快照。"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from erp_web.context import get_context
from erp_web.facades.online_product_facade import mutate
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.runtime_units.online_mercadolibre import MercadoOnlineAdapter
from erp_web.runtime_units.online_ozon import OzonOnlineAdapter
from erp_web.runtime_units.online_yandex import YandexOnlineAdapter
from erp_web.runtime_units.online_product_capabilities import OnlineProductCapabilityScope, online_products_refresh_status
from erp_web.schemas.online_products import MarketSnapshot, MarketStatus, OnlineStatus, PlatformIssue, RefreshStatusRequest
from erp_web.services.online_product_service import OnlineProductService
from erp_web.stores.online_product_store import OnlineConflict
from tests.test_online_products import listing
from tests.approval_support import _execution


@pytest.fixture
def setup_status(monkeypatch):
    app = get_context()
    config = {"mercadolibre": {"user_id": "seller"}}
    monkeypatch.setattr(app.config, "load_store_config", lambda: config)
    status = OnlineStatus(remote_id="CBT1", raw_status="paused", sale_state="paused",
        raw_sub_status=["paused_by_seller"], markets=[MarketStatus(id="MLM1", raw_status="paused")])
    remote = SimpleNamespace(account_id="seller", read_status=Mock(return_value=status))
    service = OnlineProductService(app, adapter_factories={"mercadolibre": lambda _: remote}, start_worker=False)
    app._online_products = service
    row = listing()
    row.snapshot = {"parent": {"status": "active", "price": 12}}
    row.markets = [MarketSnapshot(id="MLM1", site_id="MLM", raw_status="active", price="20", currency="MXN")]
    service.store.save(row)
    service.store.save(listing("CBT2"))
    yield service, remote, config
    service.close()


def test_refresh_preserves_other_fields_and_other_products_without_creating_jobs(setup_status):
    service, remote, _ = setup_status
    before, other = service.store.get("CBT1"), service.store.get("CBT2")
    result = service.refresh_status("CBT1")
    after = service.store.get("CBT1")
    assert result["ok"] and "snapshot" not in result["item"]
    assert after.raw_status == after.sale_state == after.markets[0].raw_status == "paused"
    assert after.raw_sub_status == ["paused_by_seller"]
    assert after.status_checked_at > before.status_checked_at
    assert after.version != before.version
    changed = {"raw_status", "sale_state", "raw_sub_status", "markets", "status_checked_at", "version"}
    assert after.model_dump(exclude=changed) == before.model_dump(exclude=changed)
    assert after.markets[0].model_dump(exclude={"raw_status"}) == before.markets[0].model_dump(exclude={"raw_status"})
    assert service.store.get("CBT2") == other
    assert not service.store.jobs("mercadolibre", "seller")
    remote.read_status.assert_called_once_with(before)


def test_unchanged_status_updates_check_time_but_not_business_version(setup_status):
    service, _, _ = setup_status
    first = service.refresh_status("CBT1")["item"]
    second = service.refresh_status("CBT1")["item"]
    assert first["version"] == second["version"]
    assert first["synced_at"] == second["synced_at"]
    assert first["status_checked_at"] < second["status_checked_at"]


def test_status_refresh_replaces_feedback_without_changing_content_or_creating_version_conflicts(setup_status):
    service, remote, _ = setup_status
    first = service.refresh_status('CBT1')['item']
    remote.read_status.return_value.platform_issues = [PlatformIssue(severity='warning', message='尺寸警告', comment='请核对包装')]
    second = service.refresh_status('CBT1')['item']
    assert second['platform_issues'][0]['comment'] == '请核对包装'
    assert second['version'] == first['version']
    assert second['content'] == first['content'] and second['synced_at'] == first['synced_at']
    assert second['errors'] == first['errors'] == []
    remote.read_status.return_value.platform_issues = []
    third = service.refresh_status('CBT1')['item']
    assert not third['platform_issues'] and third['version'] == second['version']
    assert not service.store.jobs('mercadolibre', 'seller')


@pytest.mark.parametrize("exception", [TimeoutError("查询超时"), PublishAdapterError("AUTH", "授权失效")])
def test_platform_failure_preserves_snapshot_and_returns_actionable_error(setup_status, exception):
    service, remote, _ = setup_status
    before = service.store.get("CBT1")
    remote.read_status.side_effect = exception
    result, code = mutate("refresh-status", {"listing_id": "CBT1"})
    assert code == 502 and result["error_code"] == "ONLINE_PLATFORM_ERROR"
    assert "原数据已保留" in result["error"]
    assert service.store.get("CBT1") == before


@pytest.mark.parametrize("timing", ["before", "during"])
def test_account_switch_stops_refresh(setup_status, timing):
    service, remote, config = setup_status
    before = service.store.get("CBT1")
    def change_account(_):
        config["mercadolibre"]["user_id"] = "other"
        return remote.read_status.return_value
    if timing == "before":
        change_account(None)
    else:
        remote.read_status.side_effect = change_account
    with pytest.raises(OnlineConflict, match="店铺身份"):
        service.refresh_status("CBT1")
    assert remote.read_status.call_count == (timing == "during")
    assert service.store.get("CBT1") == before


@pytest.mark.parametrize("update", ["sync", "status"])
def test_late_response_cannot_overwrite_newer_sync_or_status_even_if_version_unchanged(setup_status, update):
    service, remote, _ = setup_status
    before = service.store.get("CBT1")
    newer = []
    def concurrent(_):
        if update == "sync":
            newer.append(service.store.save(before.model_copy(deep=True)))
        else:
            newer.append(service.store.update_status(before, OnlineStatus(remote_id="CBT1", raw_status="active", sale_state="active")))
        return remote.read_status.return_value
    remote.read_status.side_effect = concurrent
    with pytest.raises(OnlineConflict, match="查询期间"):
        service.refresh_status("CBT1")
    assert newer[0].version == before.version
    assert service.store.get("CBT1") == newer[0]


@pytest.mark.parametrize("target", ["CBT1", "*"])
@pytest.mark.parametrize("timing", ["before", "during"])
def test_running_or_newly_queued_operation_blocks_status_merge(setup_status, target, timing):
    service, remote, _ = setup_status
    before = service.store.get("CBT1")
    def enqueue(_):
        service.store.enqueue("mercadolibre", "seller", "sync" if target == "*" else "price", target, {}, uuid4().hex)
        return remote.read_status.return_value
    if timing == "before":
        enqueue(None)
        service.store.claim("worker")
    else:
        remote.read_status.side_effect = enqueue
    with pytest.raises(OnlineConflict, match="正在同步或执行修改"):
        service.refresh_status("CBT1")
    assert service.store.get("CBT1") == before


@pytest.mark.parametrize("job_status", ["submitted", "waiting_confirmation", "outcome_unknown"])
def test_status_refresh_does_not_confirm_pending_mutations(setup_status, job_status):
    service, _, _ = setup_status
    job = service.store.enqueue("mercadolibre", "seller", "sale_state", "CBT1", {}, uuid4().hex)
    claim = service.store.claim("worker")
    service.store.sale_intent("CBT1", "paused", lease=claim)
    service.store.update_job(job["id"], job_status, {"polls": 20}, owner=claim["lease_token"], dispatched=True)
    before = service.store.job(job["id"])
    assert service.refresh_status("CBT1")["item"]["desired_sale_state"] == "paused"
    assert service.store.job(job["id"]) == before


def test_other_product_operation_does_not_block_refresh(setup_status):
    service, _, _ = setup_status
    service.store.enqueue("mercadolibre", "seller", "price", "CBT2", {}, uuid4().hex)
    assert service.refresh_status("CBT1")["ok"]


def test_wrong_product_or_market_never_overwrites_snapshot(setup_status):
    service, remote, _ = setup_status
    before = service.store.get("CBT1")
    original = remote.read_status.return_value
    for changes in ({"remote_id": "CBT2"}, {"markets": [MarketStatus(id="other", raw_status="paused")]}):
        remote.read_status.return_value = original.model_copy(update=changes)
        with pytest.raises(ValueError):
            service.refresh_status("CBT1")
        assert service.store.get("CBT1") == before


def test_status_refresh_does_not_claim_incomplete_details_are_ready(setup_status):
    service, _, _ = setup_status
    row = service.store.get("CBT1")
    row.details_state, row.errors = "failed", ["库存读取失败"]
    service.store.save(row)
    result = service.refresh_status("CBT1")["item"]
    assert result["details_state"] == "failed" and result["errors"] == ["库存读取失败"]


def test_ai_status_tool_reuses_service_without_creating_jobs(setup_status):
    service, remote, _ = setup_status
    result = online_products_refresh_status(RefreshStatusRequest(listing_id="CBT1"), OnlineProductCapabilityScope(lambda: service), _execution())
    assert result.item.raw_status == "paused" and result.item.status_checked_at
    remote.read_status.assert_called_once()
    assert not service.store.jobs("mercadolibre", "seller")


def test_http_refresh_request_validation(backend_server):
    import requests
    for payload in ({}, {"listing_id": 3}, {"listing_id": ""}, {"listing_id": "unknown", "platform": "yandex"}):
        response = requests.post(backend_server + "/api/online-products/refresh-status", json=payload, timeout=10)
        assert response.status_code == 400


def test_yandex_status_requests_only_the_target_and_skips_price_stock_and_settings(monkeypatch):
    from erp_web.runtime_units import online_yandex as module
    monkeypatch.setattr(module.api, "fetch_yandex_campaign", lambda *_: {"business": {"id": 1}})
    for name in ("fetch_yandex_business_settings", "fetch_yandex_partner_warehouses"):
        monkeypatch.setattr(module.api, name, Mock(side_effect=AssertionError("刷新状态不应读取此接口")))
    adapter = YandexOnlineAdapter({"yandex": {"business_id": "1", "campaign_id": "2", "api_token": "test", "stock_update_mode": "business"}})
    calls = []
    def request(path, body=None, *, query=None, method="POST"):
        calls.append((path, body, query, method))
        if path.endswith("/hidden-offers"):
            assert method == "GET" and query == {"offer_id": "sku-1"}
            return {"result": {"hiddenOffers": [{"offerId": "sku-1"}]}}
        assert body == {"offerIds": ["sku-1"]} and query is None
        if path.endswith("/offer-cards"):
            return {"result": {"offerCards": [{"offerId": "sku-1", "cardStatus": "HAS_CARD_CAN_UPDATE",
                "errors": [{"message": "Ошибка формата", "comment": "Название: значение"}],
                "warnings": [{"message": "Размер упаковки"}]}]}}
        assert path == "/v2/campaigns/2/offers"
        return {"result": {"offers": [{"offerId": "sku-1", "status": "PUBLISHED"}]}}
    adapter.request = request
    result = adapter.read_status(listing("sku-1"))
    assert result.raw_status == "PUBLISHED" and result.sale_state == "paused"
    assert result.raw_sub_status == ["HAS_CARD_CAN_UPDATE"] and len(calls) == 3
    assert [(issue.severity, issue.message) for issue in result.platform_issues] == [('error', 'Ошибка формата'), ('warning', 'Размер упаковки')]


@pytest.mark.parametrize("rows", [[], [{"offerId": "other", "status": "PUBLISHED"}], [{"offerId": "sku-1"}]])
def test_yandex_missing_or_foreign_status_is_not_success(rows):
    adapter = object.__new__(YandexOnlineAdapter)
    adapter.business, adapter.campaign = "1", "2"
    def request(path, *args, **kwargs):
        key = "hiddenOffers" if path.endswith("hidden-offers") else "offerCards" if path.endswith("offer-cards") else "offers"
        return {"result": {key: rows if key == "offers" else []}}
    adapter.request = request
    with pytest.raises(ValueError):
        adapter.read_status(listing("sku-1"))


def test_yandex_missing_target_card_does_not_turn_unknown_feedback_into_empty_feedback():
    adapter = object.__new__(YandexOnlineAdapter)
    adapter.business, adapter.campaign = '1', '2'
    def request(path, *args, **kwargs):
        if path.endswith('/offers'):
            return {'result': {'offers': [{'offerId': 'sku-1', 'status': 'PUBLISHED'}]}}
        key = 'hiddenOffers' if path.endswith('hidden-offers') else 'offerCards'
        return {'result': {key: []}}
    adapter.request = request
    with pytest.raises(ValueError, match='已保留原记录'):
        adapter.read_status(listing('sku-1'))


@pytest.mark.parametrize("archived", [False, True])
def test_ozon_status_only_uses_one_product_info_request(archived):
    adapter = object.__new__(OzonOnlineAdapter)
    adapter.request = Mock(return_value={"items": [{"id": 123, "is_archived": archived, "statuses": {"status": "processed"}}]})
    result = adapter.read_status(listing("123"))
    assert result.raw_status == ("archived" if archived else "processed")
    adapter.request.assert_called_once_with("/v3/product/info/list", {"product_id": [123]})


@pytest.mark.parametrize("items", [[], [{"id": 999}], [{"id": 123}], [{"id": 123}, {"id": 123}]])
def test_ozon_missing_status_or_wrong_identity_is_not_success(items):
    adapter = object.__new__(OzonOnlineAdapter)
    adapter.request = Mock(return_value={"items": items})
    with pytest.raises(ValueError):
        adapter.read_status(listing("123"))


@pytest.mark.parametrize("user_product", [False, True])
def test_mercado_status_reads_one_parent_and_its_markets_without_catalog_or_stock_requests(user_product):
    adapter = object.__new__(MercadoOnlineAdapter)
    adapter.account_id = "seller"
    refs = [{"item_id": "MLM1", "site_id": "MLM", "user_id": "market-seller"}]
    responses = {
        "/marketplace/items/CBT1": {"id": "CBT1", "site_id": "CBT", "seller_id": "seller", "status": "paused", "marketplace_items": refs},
        "/marketplace/items/MLM1": {"id": "MLM1", "site_id": "MLM", "seller_id": "market-seller", "status": "under_review", "cbt_item_id": "CBT1"},
    }
    if user_product:
        responses["/marketplace/items/CBT1"]["user_product_id"] = "U123"
        responses["/marketplace/user-products/U123/mapping"] = [{"siteless_user_product_id": "U123", "owner_id": "seller", "item_id": "CBT1", "user_product_id": "CBTU123", "site_items": refs}]
    adapter.get = Mock(side_effect=lambda path: deepcopy(responses[path]))
    row = listing()
    row.markets = [MarketSnapshot(id="MLM1", site_id="MLM")]
    result = adapter.read_status(row)
    assert result.raw_status == "paused" and result.markets[0].raw_status == "under_review"
    assert adapter.get.call_count == (3 if user_product else 2)
    responses["/marketplace/items/MLM1"]["seller_id"] = "other"
    with pytest.raises(ValueError, match="卖家身份"):
        adapter.read_status(row)
    row.markets = []
    with pytest.raises(ValueError, match="关联站点已变化"):
        adapter.read_status(row)
