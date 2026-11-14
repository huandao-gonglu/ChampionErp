"""基础同步与单件详情读取的请求边界、缓存保留及并发保护。"""
from collections import Counter
from dataclasses import replace
from uuid import uuid4

import pytest

from erp_web.context import get_context
from erp_web.facades import online_product_facade
from erp_web.schemas.requests import validate_request_payload
from erp_web.stores.online_product_store import OnlineConflict
from tests.test_yandex_online_sync import Platform, adapter, service_for


def sync(service):
    job = service.sync("yandex", uuid4().hex)["job"]
    assert service.run_once()
    return service.store.job(job["id"])


def feedback_platform():
    platform = Platform(3)
    original = platform.request

    def request(path, body=None, **kwargs):
        result = original(path, body, **kwargs)
        for card in result.get("result", {}).get("offerCards", []):
            card.update(parameterValues=[{"parameterId": 12, "value": "红色"}],
                        warnings=[{"code": "CARD_WARNING", "message": "卡片警告"}])
        for offer in result.get("result", {}).get("offers", []):
            if path.endswith("/offers"):
                offer["errors"] = [{"code": "SHOP_ERROR", "message": "店铺商品错误"}]
        return result

    platform.request = request
    return platform


def test_basic_sync_includes_feedback_and_attributes_without_detail_requests():
    platform = feedback_platform()
    client = adapter(platform)
    client.attribute_names = lambda _: pytest.fail("基础同步不得读取类目名称")
    del client.settings  # 测试适配器没有有效凭据，列表路径不能隐式查询账号价格设置。
    service = service_for(client)
    try:
        job = sync(service)
        assert job["status"] == "confirmed" and job["result"]["completed"] == 2
        assert job["result"]["data_scope"] == "listing"
        assert {call[0] for call in platform.calls} == {"offer-mappings", "hidden-offers", "offers", "offer-cards", "stocks"}
        for row in service.store.listings("yandex", "1:2"):
            assert row.basic_synced_at and row.status_checked_at and row.synced_at == ""
            assert row.details_state == "unloaded" and row.sales_checked_at == row.basic_synced_at
            assert row.stocks[0].quantity == 5 and row.prices[0].amount == "12"
            assert row.content["attributes"][0]["value"] == "红色"
            assert {(issue.source, issue.code) for issue in row.platform_issues} == {("card", "CARD_WARNING"), ("campaign", "SHOP_ERROR")}
            assert not row.capabilities["stock"].enabled
    finally:
        service.close()


def test_open_detail_reads_current_sku_latest_content_and_feedback():
    platform = feedback_platform()
    client = adapter(platform)
    service = service_for(client)
    try:
        sync(service)
        before = next(row for row in service.store.listings("yandex", "1:2") if row.remote_id == "sku-0")
        other = next(row for row in service.store.listings("yandex", "1:2") if row.remote_id == "sku-1")
        platform.rows[0]["offer"].update(name="当前平台标题", description="当前平台描述", pictures=["https://example.com/new.jpg"])
        platform.calls.clear()
        detail = service.refresh_detail(before.id)["item"]
        assert detail["synced_at"] == detail["basic_synced_at"] > before.basic_synced_at
        assert detail["status_checked_at"] > before.status_checked_at
        assert detail["details_state"] == "ready" and detail["stocks"][0]["quantity"] == 5
        assert detail["title"] == detail["content"]["title"] == "当前平台标题"
        assert detail["content"]["description"] == "当前平台描述"
        assert detail["content"]["pictures"] == ["https://example.com/new.jpg"]
        assert detail["content"]["attributes"][0]["value"] == "红色"
        assert detail["platform_issues"] == [issue.model_dump() for issue in before.platform_issues]
        assert Counter(name for name, *_ in platform.calls) == {
            "offer-mappings": 1, "hidden-offers": 1, "offer-cards": 1, "offer-prices": 1, "stocks": 1, "offers": 1}
        assert all(body.get("offerIds") == ["sku-0"] for _, body, *_ in platform.calls if body)
        assert next(query for name, _, query, _ in platform.calls if name == "hidden-offers") == {"offer_id": "sku-0"}
        assert service.store.get(other.id).model_dump() == other.model_dump()
        # 本地列表和详情 GET 不会因缺少详细数据而隐式请求平台。
        platform.calls.clear()
        service.list("yandex")
        service.detail(other.id)
        assert not platform.calls
    finally:
        service.close()


def test_basic_resync_preserves_detail_values_and_original_read_time():
    platform = Platform(3)
    service = service_for(adapter(platform))
    try:
        sync(service)
        before = service.store.listings("yandex", "1:2")[0]
        service.refresh_detail(before.id)
        previous = service.store.get(before.id)
        platform.rows[int(previous.remote_id.split("-")[1])]["offer"]["name"] = "平台新标题"
        platform.calls.clear()
        assert sync(service)["status"] == "confirmed"
        current = service.store.get(before.id)
        assert current.title == "平台新标题" and current.basic_synced_at > previous.basic_synced_at
        assert (current.synced_at, current.prices, current.stocks) == (previous.synced_at, previous.prices, previous.stocks)
        assert Counter(name for name, *_ in platform.calls)["stocks"] == 1
        assert not any(name == "offer-prices" for name, *_ in platform.calls)
    finally:
        service.close()


def test_failed_basic_feedback_read_preserves_previous_warnings_without_retry():
    platform = feedback_platform()
    service = service_for(adapter(platform))
    try:
        sync(service)
        previous = service.store.listings("yandex", "1:2")[0]
        platform.fail_cards = True
        platform.calls.clear()
        result = sync(service)
        assert result["status"] == "partial" and result["result"]["failed"] == 2
        current = service.store.get(previous.id)
        assert (current.platform_issues, current.basic_synced_at, current.content) == (previous.platform_issues, previous.basic_synced_at, previous.content)
        assert Counter(name for name, *_ in platform.calls)["offer-cards"] == 1
    finally:
        service.close()


def test_failed_detail_read_preserves_entire_cache_and_does_not_retry():
    platform = Platform(3)
    service = service_for(adapter(platform))
    try:
        sync(service)
        before = service.store.listings("yandex", "1:2")[0]
        service.refresh_detail(before.id)
        before = service.store.get(before.id)
        platform.fail_prices = True
        platform.calls.clear()
        with pytest.raises(ValueError, match="价格服务超时"):
            service.refresh_detail(before.id)
        assert service.store.get(before.id).model_dump() == before.model_dump()
        assert Counter(name for name, *_ in platform.calls)["offer-prices"] == 1
    finally:
        service.close()


def test_detail_response_cannot_overwrite_concurrent_update(monkeypatch):
    platform = Platform(3)
    client = adapter(platform)
    service = service_for(client)
    try:
        sync(service)
        before = service.store.listings("yandex", "1:2")[0]
        original = client.read
        def read(remote_id):
            result = original(remote_id)
            current = service.store.get(before.id)
            current.title = "并发保存的标题"
            service.store.save(current, full_snapshot=False)
            return result
        monkeypatch.setattr(client, "read", read)
        with pytest.raises(OnlineConflict, match="查询期间商品已更新"):
            service.refresh_detail(before.id)
        assert service.store.get(before.id).title == "并发保存的标题"
        assert not service.store.get(before.id).synced_at
    finally:
        service.close()


def test_refresh_detail_http_contract_and_facade_use_same_focused_service(monkeypatch):
    class Service:
        def refresh_detail(self, listing_id):
            assert listing_id == "sku-local-id"
            return {"ok": True, "item": {"id": listing_id}}
    monkeypatch.setattr(online_product_facade, "get_context", lambda: type("Context", (), {"online_products": Service()})())
    body = validate_request_payload({"listing_id": "sku-local-id"}, endpoint="/api/online-products/refresh-detail")
    assert online_product_facade.mutate("refresh-detail", body) == ({"ok": True, "item": {"id": "sku-local-id"}}, 200)


def test_reader_reports_actual_scope_without_relabeling_old_full_syncs():
    from erp_web.runtime_units.online_product_job_reader import OnlineProductJobReader
    old = {"platform": "yandex", "operation": "sync", "status": "confirmed", "result": {"completed": 219, "failed": 0}}
    assert "价格和库存未刷新" not in OnlineProductJobReader(lambda _: old).read_job_state("old").summary
    new = {**old, "result": {**old["result"], "data_scope": "basic"}}
    assert "价格和库存未刷新" in OnlineProductJobReader(lambda _: new).read_job_state("new").summary


def test_ai_detail_resource_calls_same_service_for_only_selected_sku():
    from erp_web.facades.agent_capability_facade import build_global_chat_toolset
    from erp_web.schemas.ai_tools import AiToolCommand
    from erp_web.services.ai_tool_runtime import AiToolRuntime
    from tests.approval_support import _execution
    platform = Platform(3)
    service = service_for(adapter(platform))
    app = get_context()
    app._online_products = service
    try:
        sync(service)
        before = service.store.listings("yandex", "1:2")[0]
        platform.calls.clear()
        execution = replace(_execution(), permissions=frozenset({"online_product.sync"}), allow_write=True)
        runtime = AiToolRuntime(toolset=build_global_chat_toolset(app), execution_context=execution)
        result = runtime.execute(AiToolCommand(call_id="detail", tool_name="online_products_refresh_detail",
            tool_version="1", arguments={"listing_id": before.id}, round=1))
        assert result.ok
        item = result.to_dict()["output"]["item"]
        assert item["id"] == before.id and item["details_state"] == "ready" and item["basic_synced_at"]
        assert all(body.get("offerIds") == [before.remote_id] for _, body, *_ in platform.calls if body)
        assert Counter(name for name, *_ in platform.calls) == {
            "offer-mappings": 1, "hidden-offers": 1, "offers": 1, "offer-cards": 1, "offer-prices": 1, "stocks": 1}
    finally:
        service.close()


def test_one_warehouse_failure_and_missing_sku_keep_only_their_own_cached_values():
    platform = Platform(3)
    client = adapter(platform)
    client.mode = "business"
    client.warehouses = [{"id": wid, "models": [{"placementType": "FBS", "campaignId": 2}]} for wid in (10, 20)]
    original = client.request
    phase = 1
    def request(path, body=None, **kwargs):
        if not path.endswith("/stocks"):
            return original(path, body, **kwargs)
        wid = body["partnerWarehouseId"]
        if phase == 2 and wid == 10:
            raise TimeoutError("仓库 10 超时")
        ids = body["offerIds"] if phase == 1 else ["sku-0"]
        return {"result": {"partnerWarehouseId": wid, "offers": [
            {"offerId": sku, "stocks": [{"type": "AVAILABLE", "count": wid if phase == 1 else 0}]} for sku in ids]}}
    client.request = request
    service = service_for(client)
    try:
        assert sync(service)["status"] == "confirmed"
        first = {row.id: row for row in service.store.listings("yandex", "1:2")}
        phase = 2
        assert sync(service)["status"] == "partial"
        for row in service.store.listings("yandex", "1:2"):
            stale, other = row.stocks
            assert stale.quantity == 10 and stale.cached_at == first[row.id].sales_checked_at < row.sales_checked_at
            if row.remote_id == "sku-0":
                assert other.quantity == 0 and not other.cached_at and not other.read_error
            else:
                assert other.quantity == 20 and other.cached_at == first[row.id].sales_checked_at
        assert sync(service)["status"] == "partial"
        assert all(row.stocks[0].cached_at == first[row.id].sales_checked_at for row in service.store.listings("yandex", "1:2"))
    finally:
        service.close()


def test_visible_sales_refresh_batches_only_due_skus_and_reuses_concurrent_reads():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    platform = Platform(4)
    client = adapter(platform)
    service = service_for(client)
    entered, release = Event(), Event()
    original = client.request
    calls = []
    def request(path, body=None, **kwargs):
        calls.append((path, body))
        result = original(path, body, **kwargs)
        if path.endswith("/offers"):
            for row in result["result"]["offers"]:
                if row["offerId"] == "sku-0":
                    row.pop("basicPrice")
                else:
                    row["basicPrice"]["value"] = 42
            entered.set()
            assert release.wait(3)
        return result
    try:
        sync(service)
        rows = {row.remote_id: row for row in service.store.listings("yandex", "1:2")}
        ids = [rows[sku].id for sku in ("sku-0", "sku-1")]
        for sku in ("sku-0", "sku-1"):
            rows[sku].sales_checked_at = "2020-01-01T00:00:00Z"
            service.store.save(rows[sku], full_snapshot=False)
        assert set(service.list("yandex")["sales_due_ids"]) == set(ids)
        client.request = request
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(service.sales.refresh, "1:2", ids)
            assert entered.wait(3)
            second = pool.submit(service.sales.refresh, "1:2", [*ids, rows["sku-2"].id])
            release.set()
            assert first.result()["ok"] and second.result()["ok"]
        assert Counter(path.rsplit("/", 1)[1] for path, _ in calls) == {"offers": 1, "stocks": 1, "offer-prices": 1}
        assert next(body for path, body in calls if path.endswith("/offer-prices")) == {"offerIds": ["sku-0"]}
        assert service.list("yandex")["sales_due_ids"] == []
        assert service.store.get(ids[1]).prices[0].amount == "42"
        for sku in ("sku-0", "sku-1"):
            updated = service.store.get(rows[sku].id)
            assert updated.details_state == "unloaded" and updated.content == rows[sku].content
            assert updated.basic_synced_at == rows[sku].basic_synced_at and updated.synced_at == ""
        assert service.store.get(rows["sku-2"].id) == rows["sku-2"]
        with pytest.raises(OnlineConflict):
            service.sales.refresh("other:2", ids)
    finally:
        release.set()
        service.close()


def test_platform_warehouse_sales_are_readable_across_warehouses_and_never_writable():
    from erp_web.runtime_units.online_yandex import YandexOnlineAdapter

    client = YandexOnlineAdapter({"yandex": {"api_token": "test", "business_id": "1", "campaign_id": "2",
                                            "stock_update_mode": "none", "placement_type": "FBY"}})
    client.settings = {}
    platform = Platform(2)
    def request(path, body=None, **kwargs):
        if path.endswith("/stocks"):
            return {"result": {"warehouses": [{"warehouseId": wid, "offers": [
                {"offerId": "sku-0", "stocks": [{"type": "AVAILABLE", "count": wid}]}]} for wid in (7, 3)]}}
        return platform.request(path, body, **kwargs)
    client.request = request
    sales = client.read_sales(["sku-0"]).listings[0]
    assert sales.stocks[0].quantity == 10 and not sales.stocks[0].writable
