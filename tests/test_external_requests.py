"""统一外部请求管理的离线验收：只用内存响应与隔离数据库。"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from email.message import Message
import io
import json
from threading import Event
import time
import urllib.error
import urllib.request

import httpx2 as httpx
import pytest

from erp_web.context import get_context
from erp_web.schemas.external_requests import ExternalRequestBlocked, ExternalRequestOutcomeUnknown, RequestContext
from erp_web.services.external_request_manager import ExternalRequestManager
from erp_web.stores.external_request_store import ExternalRequestStore


class Response(io.BytesIO):
    def __init__(self, body=b'{}', status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        for key,value in (headers or {}).items():
            self.headers[key] = value


def context(**kwargs):
    return RequestContext(**{"platform":"ozon","account_id":"shop-1","interface":"/tree","source":"category","credential_id":"key-version-1","semantics":"read","timeout":2,**kwargs})


def request(manager, ctx, transport):
    return manager.open(urllib.request.Request("https://api-seller.ozon.ru/tree"),request_context_value=ctx,timeout=ctx.timeout,transport=transport)


def rejected(status, body, headers=None):
    def send(*args,**kwargs):
        raise urllib.error.HTTPError("https://test.invalid",status,"rejected",headers or {},io.BytesIO(body))
    return send


def test_disabled_account_blocks_queued_cross_module_and_survives_restart():
    store = ExternalRequestStore(get_context().paths.data_dir / "external-requests.sqlite3")
    manager = ExternalRequestManager(store)
    store.configure("ozon",concurrency=1)
    started,release = Event(),Event()
    sent = []
    def disabled(*args,**kwargs):
        sent.append(1)
        started.set()
        assert release.wait(2)
        return rejected(403,b'{"message":"Api access disabled"}')()
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(request,manager,context(),disabled)
        assert started.wait(1)
        second = pool.submit(request,manager,context(source="online_sync",interface="/products"),lambda *a,**k: sent.append(2))
        release.set()
        with pytest.raises(urllib.error.HTTPError):
            first.result()
        with pytest.raises(ExternalRequestBlocked):
            second.result()
    reopened = ExternalRequestManager(ExternalRequestStore(get_context().paths.data_dir / "external-requests.sqlite3"))
    with pytest.raises(ExternalRequestBlocked):
        request(reopened,context(credential_id="new-key",interface="/publish"),lambda *a,**k: sent.append(3))
    with request(reopened,context(account_id="other"),lambda *a,**k: Response()) as result:
        assert result.read() == b'{}'
    assert sent == [1]
    assert store.query()["stats"] == {"attempts":4,"operations":4,"network_attempts":2,"local_rejections":2}
    assert store.blocks()[0]["blocked_count"] == 2


def test_interface_permission_does_not_disable_unrelated_api_and_recovery_is_explicit():
    manager = get_context().external_requests
    with pytest.raises(urllib.error.HTTPError):
        request(manager,context(),rejected(403,b'{"message":"Permission denied"}'))
    request(manager,context(interface="/orders"),lambda *a,**k: Response()).close()
    with pytest.raises(ExternalRequestBlocked):
        request(manager,context(credential_id="new-key"),lambda *a,**k: Response())
    manager.store.recover("ozon","shop-1","interface","/tree",reason="已由平台开通类目权限")
    request(manager,context(),lambda *a,**k: Response()).close()


def test_rate_limit_without_recovery_time_never_retries():
    manager = get_context().external_requests
    calls = []
    def send(*args,**kwargs):
        calls.append(1)
        return rejected(429,b'{"message":"rate limit"}')()
    with pytest.raises(urllib.error.HTTPError):
        request(manager,context(max_attempts=3),send)
    with pytest.raises(ExternalRequestBlocked):
        request(manager,context(interface="/orders"),send)
    assert calls == [1]


def test_waiting_obeys_retry_after_and_can_be_cancelled():
    manager = get_context().external_requests
    with pytest.raises(urllib.error.HTTPError):
        request(manager,context(),rejected(429,b'{}',{"Retry-After":"0.15"}))
    start = time.time()
    request(manager,context(),lambda *a,**k: Response()).close()
    assert time.time()-start >= .10
    with pytest.raises(urllib.error.HTTPError):
        request(manager,context(),rejected(429,b'{}',{"Retry-After":"1"}))
    cancel = Event()
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(request,manager,context(cancel=cancel),lambda *a,**k: pytest.fail("取消后不得外发"))
        cancel.set()
        with pytest.raises(ExternalRequestBlocked,match="取消"):
            future.result()
    last = manager.store.query(limit=1)["items"][0]
    assert last["decision"] == "cancelled" and last["sent"] == 0


def test_http_200_business_rejection_is_audited_and_secret_free():
    manager = get_context().external_requests
    secret = "secret-api-key-MUST-NOT-APPEAR"
    payload = json.dumps({"status":"ERROR","errors":[{"code":"FORBIDDEN","message":secret}]}).encode()
    with pytest.raises(ExternalRequestBlocked):
        request(manager,context(),lambda *a,**k: Response(payload))
    audit = manager.store.query()
    assert secret not in json.dumps(audit)
    assert audit["items"][0]["result"]["http_status"] == 200
    assert audit["items"][0]["result"]["outcome"] == "failed"
    assert audit["items"][0]["result"]["platform_error_codes"] == ["FORBIDDEN"]


def test_retry_has_one_shared_budget_and_separate_audit_rows():
    manager = get_context().external_requests
    calls = []
    def send(*args,**kwargs):
        calls.append(1)
        if len(calls) == 1:
            return rejected(503,b'{}')()
        return Response()
    request(manager,context(max_attempts=2,retry_delay=.01),send).close()
    rows = manager.store.query()["items"]
    assert len(calls) == 2 and {r["attempt"] for r in rows} == {1,2}
    assert len({r["operation_id"] for r in rows}) == 1
    assert rows[0]["parent_id"] == rows[1]["id"]


def test_write_timeout_is_unknown_and_never_replayed():
    manager = get_context().external_requests
    calls = []
    def send(*args,**kwargs):
        calls.append(1)
        raise TimeoutError("敏感平台错误原文")
    ctx = context(semantics="write", max_attempts=3)
    with pytest.raises(ExternalRequestOutcomeUnknown) as error:
        request(manager,ctx,send)
    rows = manager.store.query()["items"]
    assert len(calls) == 1
    assert rows[0]["result"]["outcome"] == "outcome_unknown"
    assert "敏感平台" not in json.dumps(rows)
    assert error.value.details["outcome_unknown"] is True
    with pytest.raises(ExternalRequestBlocked):
        request(manager,ctx,send)
    assert len(calls) == 1


def test_sdk_transport_uses_the_same_account_pause():
    import asyncio
    from erp_web.services.external_httpx_transport import ManagedAsyncTransport
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(429,json={"error":{"message":"Too many requests"}})
    async def run():
        transport = ManagedAsyncTransport(platform="ai:test",account_id="model-1",transport_factory=lambda:httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post("https://ai.invalid/responses",json={"input":"敏感正文"})
            assert response.status_code == 429
            with pytest.raises(ExternalRequestBlocked):
                await client.post("https://ai.invalid/responses")
    asyncio.run(run())
    assert len(calls) == 1
    assert "敏感正文" not in json.dumps(get_context().external_requests.store.query())


def test_token_expiry_only_blocks_old_credential():
    manager = get_context().external_requests
    with pytest.raises(urllib.error.HTTPError):
        request(manager,context(),rejected(401,b'{}'))
    request(manager,context(credential_id="refreshed-token"),lambda *a,**k: Response()).close()
    with pytest.raises(ExternalRequestBlocked):
        request(manager,context(),lambda *a,**k: Response())


def test_repeated_bad_parameters_and_configured_failure_streak_stop_locally():
    manager = get_context().external_requests
    ctx = context(fingerprint="same-parameters")
    with pytest.raises(urllib.error.HTTPError):
        request(manager,ctx,rejected(400,b'{}'))
    with pytest.raises(ExternalRequestBlocked):
        request(manager,ctx,lambda *a,**k: pytest.fail("相同任务的错误参数不能再次发送"))
    request(manager,replace(ctx,fingerprint="corrected-parameters"),lambda *a,**k: Response()).close()
    manager.store.configure("ozon",consecutive_failure_limit=2)
    for _ in range(2):
        with pytest.raises(urllib.error.HTTPError):
            request(manager,context(interface="/unstable"),rejected(503,b'{}'))
    with pytest.raises(ExternalRequestBlocked,match="连续失败"):
        request(manager,context(interface="/unstable"),lambda *a,**k: Response())


def test_yunexpress_tokens_are_reused_across_clients_and_explicit_auth_is_fresh():
    from erp_web.runtime_units.yunexpress_client import YunExpressClient, TOKEN_PATH
    calls = []
    def send(req,**kwargs):
        calls.append(req.full_url)
        return Response(json.dumps({"accessToken":"token-123","expiresIn":7200} if req.full_url.endswith(TOKEN_PATH) else {"success":True}).encode())
    config = {"app_id":"app-1","app_secret":"secret","source_key":"source"}
    for _ in range(5):
        YunExpressClient(config,urlopen=send).create_package_order({})
    assert sum(url.endswith(TOKEN_PATH) for url in calls) == 1
    assert len(calls) == 6
    YunExpressClient(config,urlopen=send).request_access_token()
    assert sum(url.endswith(TOKEN_PATH) for url in calls) == 2


def test_mercado_ordinary_getter_performs_no_identity_or_config_write(monkeypatch):
    from erp_web.runtime_units.store_credentials import get_mercadolibre_access_token
    from erp_web import marketplaces
    ctx = get_context()
    ctx.config.save_store_config({"mercadolibre":{"access_token":"valid","user_id":"user-1"}})
    monkeypatch.setattr(marketplaces,"fetch_mercadolibre_user_profile",lambda *a:pytest.fail("普通读取不得查询身份"))
    monkeypatch.setattr(ctx.config,"update_store_config_fields",lambda *a:pytest.fail("普通读取不得回写配置"))
    assert [get_mercadolibre_access_token() for _ in range(5)] == ["valid"]*5


def test_ozon_64_keywords_share_one_failure(monkeypatch):
    from erp_web.runtime_units import ozon_category_api as api
    from erp_web.runtime_units.category_providers import OzonCategoryProvider
    from erp_web.runtime_units.category_searchers import OzonCategorySearcher
    from erp_web.runtime_units.category_keyword_search import CategoryKeywordBatchSearch
    from erp_web.schemas.category import CategoryCandidateLedger
    from erp_web.schemas.ai_trace import AiExecutionContext
    from erp_web.schemas.platform_errors import PublishAdapterError
    monkeypatch.setattr(api,"_ozon_credentials",lambda:("isolated-client","key"))
    calls = []
    def send(*a,**k):
        calls.append(1)
        raise PublishAdapterError("OZON_AUTH_FAILED","访问被拒绝",details={"http_status":403})
    monkeypatch.setattr(api,"request_ozon_json",send)
    batch = CategoryKeywordBatchSearch(searcher=OzonCategorySearcher(OzonCategoryProvider(),"global"),ledger=CategoryCandidateLedger(),search_language="ru")
    result = batch.execute({"keywords":[f"вентилятор {i}" for i in range(64)]},AiExecutionContext.create(timeout_seconds=5,budget_profile="test"))
    assert len(calls) == 1 and len(result["errors"]) == 64
    assert all(not error["retryable"] for error in result["errors"])


def test_yandex_resource_limit_does_not_stop_other_endpoints():
    from erp_web.services.platform_request_policy import classify_response
    from email.utils import formatdate
    resume = formatdate(time.time()+60,usegmt=True)
    limited = classify_response("yandex",420,{"X-RateLimit-Resource-Until":resume},b'{"message":"Hit rate limit for resource"}')
    assert limited.scope == "interface" and limited.resume_at > time.time()
    concurrent = classify_response("yandex",420,{},b'{"message":"Hit rate limit of 4 parallel requests for campaignId 123"}')
    assert concurrent.scope == "quota"
    manager = get_context().external_requests
    ctx = context(platform="yandex",quota_key="campaign:123")
    request(manager,ctx,lambda *a,**k:Response(headers={"X-RateLimit-Resource-Remaining":"0","X-RateLimit-Resource-Until":resume})).close()
    with pytest.raises(ExternalRequestBlocked):
        request(manager,ctx,lambda *a,**k:pytest.fail("资源配额已用完"))
    request(manager,replace(ctx,interface="/orders"),lambda *a,**k:Response()).close()


def test_yandex_adapter_constructor_uses_saved_binding_without_probe(monkeypatch):
    from erp_web.runtime_units.online_yandex import YandexOnlineAdapter
    from erp_web.marketplaces import yandex_http
    monkeypatch.setattr(yandex_http,"fetch_yandex_campaign",lambda *a:pytest.fail("构造适配器不得查店铺归属"))
    config = {"yandex":{"api_token":"token","business_id":"1","campaign_id":"2"}}
    assert [YandexOnlineAdapter(config).account_id for _ in range(5)] == ["1:2"]*5


def test_yandex_sync_stops_remaining_batches_after_access_rejection(monkeypatch):
    from types import SimpleNamespace
    from erp_web.runtime_units import online_yandex_read as read
    from erp_web.schemas.platform_errors import PublishAdapterError
    mappings = [{"offer":{"offerId":str(i)}} for i in range(201)]
    monkeypatch.setattr(read,"catalog_pages",lambda *a:[mappings])
    monkeypatch.setattr(read,"catalog_listing",lambda row,**k:row)
    monkeypatch.setattr(read,"hidden_ids",lambda *a:set())
    calls = []
    def send(*a,**k):
        calls.append(a[0])
        raise PublishAdapterError("YANDEX_AUTH_FAILED","拒绝",details={"http_status":403})
    adapter = SimpleNamespace(account_id="1:2",campaign="2",business="1",settings={},mode="campaign_warehouses",request=send)
    with pytest.raises(PublishAdapterError):
        list(read.sync_yandex(adapter))
    assert 1 <= len(calls) <= 3


def test_mercado_confirmation_reads_selected_price_scope_only():
    from types import SimpleNamespace
    from erp_web.runtime_units.online_change_confirmation import mercado_change
    from erp_web.schemas.online_products import ChangeRequest, OnlineListing, PriceScope, MarketSnapshot
    listing = OnlineListing(id="listing",platform="mercadolibre",account_id="1",remote_id="CBT1",model="traditional_global_items",
        prices=[PriceScope(id="MLM1",label="墨西哥",amount="1",currency="MXN")],markets=[MarketSnapshot(id="MLM1",site_id="MLM",seller_id="2")])
    paths = []
    def get(path):
        paths.append(path)
        return {"id":"MLM1","seller_id":"2","site_id":"MLM","cbt_item_id":"CBT1","price":5,"currency_id":"MXN"}
    change = ChangeRequest(listing_id="listing",version="version",operation="price",scope_id="MLM1",changes={"amount":"5","currency":"MXN"},idempotency_key="offline-confirmation")
    result = mercado_change(SimpleNamespace(account_id="1",get=get),listing,change)
    assert result.prices[0].amount == "5" and len(paths) == 1
    assert paths[0].startswith("/marketplace/items/MLM1?attributes=")
    assert "pictures" not in paths[0] and "attributes=available_quantity" not in paths[0]


def test_ozon_dictionary_next_cursor_reuses_downloaded_values(monkeypatch):
    from erp_web.runtime_units import ozon_category_api as api
    calls = []
    monkeypatch.setattr(api,"_attribute_values_cache",api._MemoryCorpusCache())
    def fetch(*a,**k):
        calls.append(a)
        return {"result":[{"id":i,"value":str(i)} for i in range(1,2001)],"has_next":True}
    monkeypatch.setattr(api,"request_ozon_json",fetch)
    options = dict(description_category_id=1,type_id=2,attribute_id=3,client_id="isolated",api_key="secret",timeout_seconds=2)
    first,more = api._attribute_value_page(last_value_id=0,**options)
    second,more = api._attribute_value_page(last_value_id=50,**options)
    assert len(calls) == 1 and len(first) == 2000 and len(second) == 1950
    assert second[0]["id"] == "51" and more


def test_sdk_queue_cancellation_is_audited_without_sending():
    import asyncio
    from erp_web.services.external_httpx_transport import ManagedAsyncTransport

    manager = get_context().external_requests
    manager.store.configure("ai:test", concurrency=1)
    occupied = context(platform="ai:test", account_id="model-1", interface="/responses", timeout=20)
    request_id = manager.start(occupied, "POST")
    assert manager.check(request_id, occupied, time.time()+20) == 0

    async def run():
        transport = ManagedAsyncTransport(platform="ai:test", account_id="model-1", transport_factory=lambda: pytest.fail("排队取消不得联网"))
        async with httpx.AsyncClient(transport=transport) as client:
            task = asyncio.create_task(client.post("https://ai.invalid/responses"))
            for _ in range(100):
                if manager.store.query()["stats"]["attempts"] == 2:
                    break
                await asyncio.sleep(.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())
    latest = manager.store.query(limit=1)["items"][0]
    assert latest["decision"] == "cancelled"
    assert latest["sent"] == 0 and latest["completed"] is not None


def test_http_stream_eof_and_caller_close_have_distinct_results():
    import asyncio
    from erp_web.services.external_httpx_transport import ManagedAsyncTransport

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: first\n\n"
            yield b"data: second\n\n"

    def handler(req):
        return httpx.Response(200, headers={"content-type":"text/event-stream"}, stream=Stream())

    async def run():
        transport = ManagedAsyncTransport(platform="ai:test", account_id="model-1", transport_factory=lambda: httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            async with client.stream("POST", "https://ai.invalid/responses") as response:
                assert b"second" in await response.aread()
            async with client.stream("POST", "https://ai.invalid/responses"):
                pass

    asyncio.run(run())
    rows = get_context().external_requests.store.query()["items"]
    assert [row["result"]["outcome"] for row in rows] == ["stream_closed", "success"]
    assert all(row["completed"] is not None for row in rows)
    assert get_context().external_requests.store.blocks() == []


def test_category_definition_and_candidates_share_raw_response():
    from erp_web.runtime_units.category_http_cache import cached_category_http

    calls = []
    def fetch(*args, **kwargs):
        calls.append(args)
        return [{"id":"BRAND", "values":[{"id":"1", "name":"品牌"}]}]

    url = "https://api.mercadolibre.com/categories/CBT1/attributes"
    first = cached_category_http(fetch, url, "token")
    first[0]["values"].clear()
    second = cached_category_http(fetch, url, "token")
    assert len(calls) == 1 and second[0]["values"]
    cached_category_http(fetch, url, "rotated-token")
    assert len(calls) == 2


def test_uncertain_writes_trip_failure_limit_without_becoming_definite_rejections():
    manager = get_context().external_requests
    manager.store.configure("ozon", consecutive_failure_limit=2)
    first = context(semantics="write")
    def send(*args, **kwargs):
        raise TimeoutError("写入响应丢失")
    with pytest.raises(ExternalRequestOutcomeUnknown):
        request(manager, first, send)
    with pytest.raises(ExternalRequestBlocked) as blocked:
        request(manager, first, send)
    assert blocked.value.details["outcome_unknown"] is True
    assert blocked.value.details["definitively_rejected"] is False
    with pytest.raises(ExternalRequestOutcomeUnknown):
        request(manager, context(semantics="write"), send)
    with pytest.raises(ExternalRequestBlocked, match="连续失败"):
        request(manager, context(semantics="write"), send)
    assert manager.store.query()["stats"]["network_attempts"] == 2
