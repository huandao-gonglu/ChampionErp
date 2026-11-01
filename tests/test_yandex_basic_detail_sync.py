"""基础同步与单件详情读取的请求边界、缓存保留及并发保护。"""
from collections import Counter
from dataclasses import replace
from uuid import uuid4

import pytest

from erp_web.context import get_context
from erp_web.facades import online_product_facade
from erp_web.schemas.online_products import BuyerLink
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
    service = service_for(client)
    try:
        job = sync(service)
        assert job["status"] == "confirmed" and job["result"]["completed"] == 2
        assert job["result"]["data_scope"] == "basic"
        assert {call[0] for call in platform.calls} == {"offer-mappings", "hidden-offers", "offers", "offer-cards"}
        for row in service.store.listings("yandex", "1:2"):
            assert row.basic_synced_at and row.status_checked_at and row.synced_at == ""
            assert row.details_state == "unloaded" and row.prices == row.stocks == []
            assert row.content["attributes"][0]["value"] == "红色"
            assert {(issue.source, issue.code) for issue in row.platform_issues} == {("card", "CARD_WARNING"), ("campaign", "SHOP_ERROR")}
            assert not row.capabilities["stock"].enabled
    finally:
        service.close()


def test_open_detail_reads_only_current_sku_and_reuses_received_basic_content():
    platform = feedback_platform()
    client = adapter(platform)
    service = service_for(client)
    try:
        sync(service)
        before = next(row for row in service.store.listings("yandex", "1:2") if row.remote_id == "sku-0")
        other = next(row for row in service.store.listings("yandex", "1:2") if row.remote_id == "sku-1")
        before.buyer_links = [BuyerLink(label="商品", url="https://market.yandex.ru/product/1")]
        service.store.save(before, full_snapshot=False)
        platform.calls.clear()
        detail = service.refresh_detail(before.id)["item"]
        assert detail["synced_at"] and detail["basic_synced_at"] == before.basic_synced_at
        assert detail["details_state"] == "ready" and detail["stocks"][0]["quantity"] == 5
        assert detail["content"]["attributes"][0]["value"] == "红色"
        assert detail["platform_issues"] == [issue.model_dump() for issue in before.platform_issues]
        assert detail["buyer_links"] == [link.model_dump() for link in before.buyer_links]
        assert Counter(name for name, *_ in platform.calls) == {"offer-prices": 1, "stocks": 1, "offers": 1}
        assert all(body.get("offerIds") == ["sku-0"] for _, body, *_ in platform.calls)
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
        assert not {"offer-prices", "stocks"} & {name for name, *_ in platform.calls}
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
        original = client.read_details
        def read(row):
            result = original(row)
            current = service.store.get(row.id)
            current.title = "并发保存的标题"
            service.store.save(current, full_snapshot=False)
            return result
        monkeypatch.setattr(client, "read_details", read)
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
        assert all(body.get("offerIds") == [before.remote_id] for _, body, *_ in platform.calls)
        assert not {"offer-mappings", "offer-cards", "hidden-offers"} & {name for name, *_ in platform.calls}
    finally:
        service.close()
