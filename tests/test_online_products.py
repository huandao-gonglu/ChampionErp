"""在线商品回归：测试替身仅用于隔离平台 I/O，不进入运行时数据。"""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from erp_web.context import get_context
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.runtime_units.online_mercadolibre import MercadoOnlineAdapter
from erp_web.runtime_units.online_yandex import YandexOnlineAdapter
from erp_web.runtime_units.online_yandex_snapshot import available_stock
from erp_web.runtime_units.online_mercadolibre_read import catalog_pages
from erp_web.schemas.online_products import Capability, ChangeRequest, OnlineListing, OnlineSyncBatch, PriceScope, StockScope, snapshot_version
from erp_web.services.online_product_changes import validate_changes
from erp_web.services.online_product_service import OnlineProductService
from erp_web.stores.online_product_store import OnlineConflict


def listing(remote="CBT1"):
    return OnlineListing(id=remote, platform="mercadolibre", account_id="seller", remote_id=remote,
        model="traditional_global_items", title="测试商品", raw_status="active", sale_state="active",
        prices=[PriceScope(id="global", label="基础价", amount="12", currency="USD", writable=True)],
        stocks=[StockScope(id="shared", label="共享", quantity=5, writable=True)],
        content={"title":"测试商品", "pictures":[{"id":"p1","url":"https://example.com/p1"}]},
        capabilities={op:Capability(enabled=True, fields=["title","pictures"]) for op in ("price","stock","content","sale_state")})


class RemoteFixture:
    account_id = "seller"
    def __init__(self):
        self.rows = {"CBT1":listing()}
        self.writes = []
        self.read_error = None
        self.write_error = None
        self.receipt = {"success":True}
        self.apply = True
        self.pending = False
    def sync(self, ids=None, *, catalog="current"):
        for remote_id in ids if ids is not None else self.rows:
            try:
                yield OnlineSyncBatch("details", listings=[self.read(remote_id)])
            except Exception as exc:
                yield OnlineSyncBatch("details", errors={remote_id: str(exc)})
    def read(self, remote):
        if self.read_error:
            raise self.read_error
        return self.rows[remote].model_copy(deep=True)
    def read_confirmation(self, listing, request):
        return self.read(listing.remote_id)
    def write(self, item, operation, scope, changes):
        self.writes.append((operation,scope,changes))
        if self.write_error:
            raise self.write_error
        if self.apply and operation == "price":
            self.rows[item.remote_id].prices[0].amount = str(changes["amount"])
        return deepcopy(self.receipt)
    def confirmation_details(self, *args):
        return {"pending":self.pending,"errors":[]}


@pytest.fixture
def setup_online():
    remote = RemoteFixture()
    context = SimpleNamespace(db=get_context().db, config=SimpleNamespace(load_store_config=lambda:{"mercadolibre":{"user_id":"seller"}}))
    service = OnlineProductService(context, adapter_factories={"mercadolibre":lambda config:remote}, start_worker=False)
    service.store.save(remote.rows["CBT1"])
    context.task_queue = get_context().task_queue
    get_context()._online_products = service
    yield service,remote
    service.close()


def price_request(service, **overrides):
    return {"listing_id":"CBT1","version":service.store.get("CBT1").version,"operation":"price","scope_id":"global",
            "changes":{"amount":"15.25","currency":"USD"},"idempotency_key":uuid4().hex,**overrides}


def expire(service, job):
    with service.store.db._connect() as conn:
        conn.execute("UPDATE online_jobs SET lease_until=0,result_json=json_set(result_json,'$.next_confirmation_at',0,'$.last_confirmation_at',0) WHERE id=?",(job,))
        conn.commit()
    queue = service.context.task_queue
    with queue.store.connect() as conn:
        conn.execute("UPDATE confirmations SET last_checked_at=0,next_check_at=0 WHERE id=?", (f"online:{job}",))


def test_sync_discovers_goods_without_creating_drafts(setup_online):
    service,remote = setup_online
    remote.rows["CBT2"] = listing("CBT2")
    with service.store.db._connect() as c:
        before = c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    job=service.sync("mercadolibre",uuid4().hex)["job"]
    assert service.run_once()
    assert service.store.job(job["id"])["result"]["completed"] == 2
    assert len(service.list("mercadolibre")["items"]) == 2
    with service.store.db._connect() as c:
        assert c.execute("SELECT COUNT(*) FROM products").fetchone()[0] == before


def test_incomplete_sync_preserves_previous_snapshot_and_retry_failed_only(setup_online):
    service,remote=setup_online
    remote.rows["CBT1"].title="不完整新数据"
    remote.rows["CBT1"].errors=["市场详情超时"]
    remote.rows["CBT2"]=listing("CBT2")
    job=service.sync("mercadolibre",uuid4().hex)["job"]
    service.run_once()
    assert service.store.get("CBT1").title == "测试商品"
    assert service.store.job(job["id"])["status"] == "partial"
    retry=service.retry(job["id"],uuid4().hex)["job"]
    assert retry["request"]["ids"] == ["CBT1"]


def test_sync_price_receipt_completes_without_readback_and_preserves_idempotency(setup_online):
    service,remote=setup_online
    body=price_request(service)
    job=service.change(body)["job"]
    assert service.change(body)["job"]["id"] == job["id"]
    service.run_once()
    assert service.store.job(job["id"])["status"] == "confirmed"
    assert not service.run_once()
    assert service.store.job(job["id"])["status"] == "confirmed"
    assert service.change(body)["job"]["id"] == job["id"]
    assert len(remote.writes)==1
    assert service.store.get("CBT1").stocks[0].quantity==5


def test_preflight_conflict_does_not_dispatch(setup_online):
    service,remote=setup_online
    job=service.change(price_request(service))["job"]
    remote.rows["CBT1"].title="店铺已修改"
    service.run_once()
    assert not remote.writes
    assert service.store.job(job["id"])["status"]=="failed"
    assert service.store.get("CBT1").title=="店铺已修改"


def test_confirmation_failure_keeps_cooldown_and_never_replays_write(setup_online):
    service, remote = setup_online
    remote.receipt = {"task_id": "async-price"}
    job = service.change(price_request(service))["job"]
    service.run_once()
    expire(service, job["id"])
    remote.read_error = TimeoutError("平台暂未响应")
    service.reconcile(job["id"])
    result = service.store.job(job["id"])
    assert result["status"] == "waiting_confirmation"
    assert result["result"]["receipt"] == {"task_id": "async-price"}
    assert "automatic_confirmation_pending" not in result["result"]
    with pytest.raises(OnlineConflict, match="刚刚查询"):
        service.reconcile(job["id"])
    assert not service.run_once()
    assert len(remote.writes) == 1


def test_partial_confirmation_preserves_full_sync_timestamp(setup_online):
    service, remote = setup_online
    before = service.store.get("CBT1")
    remote.receipt = {"task_id": "async-price"}
    job = service.change(price_request(service))["job"]
    service.run_once()
    expire(service, job["id"])
    service.reconcile(job["id"])
    current = service.store.get("CBT1")
    assert current.synced_at == before.synced_at
    assert current.status_checked_at == before.status_checked_at
    assert current.prices[0].amount == "15.25"


def test_unknown_never_replayed_and_readonly_reconcile_can_confirm(setup_online):
    service,remote=setup_online
    remote.write_error=TimeoutError("写请求超时")
    job=service.change(price_request(service))["job"]
    service.run_once()
    assert service.store.job(job["id"])["status"]=="outcome_unknown"
    with pytest.raises(OnlineConflict): service.retry(job["id"],uuid4().hex)
    with pytest.raises(OnlineConflict): service.change(price_request(service))
    assert not service.run_once()
    remote.rows["CBT1"].prices[0].amount="15.25"
    assert service.reconcile(job["id"])["job"]["status"]=="confirmed"
    assert len(remote.writes)==1


@pytest.mark.parametrize("state", ["confirmed", "waiting_confirmation", "outcome_unknown"])
def test_unconfirmed_change_allows_sync_without_replaying_or_resolving_write(setup_online, state):
    service, remote = setup_online
    remote.apply = False
    if state == "outcome_unknown":
        remote.write_error = TimeoutError("写请求超时")
    elif state == "waiting_confirmation":
        remote.receipt = {"task_id": "pending-price"}
    job = service.change(price_request(service))["job"]
    service.run_once()
    if state == "waiting_confirmation":
        service.reconcile(job["id"])
    original = service.store.job(job["id"])
    assert original["status"] == state
    remote.rows["CBT1"].prices[0].amount = "15.25"
    remote.rows["CBT2"] = listing("CBT2")

    sync = service.sync("mercadolibre", uuid4().hex)["job"]
    assert service.run_once()
    assert service.store.job(sync["id"])["status"] == "confirmed"
    assert service.store.get("CBT1").prices[0].amount == "15.25"
    assert service.store.get("CBT2").remote_id == "CBT2"
    # 即使同步值与原修改相同，也不代替原任务的结果核对。
    assert service.store.job(job["id"]) == original
    if state == "confirmed":
        assert service.change(price_request(service))["job"]["status"] == "queued"
    else:
        with pytest.raises(OnlineConflict):
            service.change(price_request(service))
    with pytest.raises(OnlineConflict):
        service.retry(job["id"], uuid4().hex)
    assert len(remote.writes) == 1


@pytest.mark.parametrize("running", [False, True])
def test_sync_still_excludes_active_modifications_and_duplicate_sync(setup_online, running):
    service, _ = setup_online
    service.change(price_request(service))
    if running:
        service.store.claim("writer")
    with pytest.raises(OnlineConflict, match="排队或执行中的商品修改"):
        service.sync("mercadolibre", uuid4().hex)


@pytest.mark.parametrize("running", [False, True])
def test_sync_and_result_query_are_mutually_exclusive(setup_online, running):
    service, remote = setup_online
    remote.receipt = {"task_id": "async-price"}
    job = service.change(price_request(service))["job"]
    service.run_once()
    sync = service.sync("mercadolibre", uuid4().hex)["job"]
    original = service.store.job(job["id"])
    if running:
        service.store.claim("sync-worker")
    with pytest.raises(OnlineConflict, match="正在同步"):
        service.reconcile_once(job["id"])
    assert service.store.job(job["id"]) == original
    with pytest.raises(OnlineConflict, match="正在同步"):
        service.sync("mercadolibre", uuid4().hex)
    with pytest.raises(OnlineConflict):
        service.change(price_request(service))
    assert service.store.job(sync["id"])["status"] == ("running" if running else "queued")
    assert len(remote.writes) == 1


def test_result_query_in_progress_blocks_sync_until_query_finishes(setup_online):
    service, remote = setup_online
    remote.receipt = {"task_id": "async-price"}
    job = service.change(price_request(service))["job"]
    service.run_once()
    claim = service.store.claim_reconcile(job["id"])
    with pytest.raises(OnlineConflict, match="排队或执行中的商品修改"):
        service.sync("mercadolibre", uuid4().hex)
    service.store.update_job(job["id"], "waiting_confirmation", {}, owner=claim["lease_token"], dispatched=True)
    assert service.sync("mercadolibre", uuid4().hex)["job"]["status"] == "queued"


@pytest.mark.parametrize("dispatched",[False,True])
def test_restart_recovery_respects_durable_dispatch_boundary(setup_online,dispatched):
    service,remote=setup_online
    job=service.change(price_request(service))["job"]
    claim=service.store.claim("dead-worker")
    service.store.update_job(job["id"],"running",{},owner=claim["lease_token"],dispatched=dispatched)
    expire(service,job["id"])
    if dispatched:
        assert not service.run_once()
        assert service.store.job(job["id"])["status"]=="outcome_unknown"
    else:
        assert service.run_once()
    assert len(remote.writes)==(0 if dispatched else 1)
    with pytest.raises(OnlineConflict):
        service.store.update_job(job["id"],"confirmed",{},owner=claim["lease_token"])
    with pytest.raises(OnlineConflict): service.store.save(listing(),lease=claim)


def test_readback_authorization_failure_does_not_authorize_replay(setup_online):
    service,remote=setup_online
    remote.apply=False
    remote.receipt={"task_id":"pending-price"}
    job=service.change(price_request(service))["job"]
    service.run_once()
    assert service.store.job(job["id"])["status"]=="waiting_confirmation"
    expire(service,job["id"])
    service.reconcile(job["id"])
    assert service.store.job(job["id"])["status"]=="waiting_confirmation"
    remote.read_error=PublishAdapterError("AUTH","授权失效",details={"http_status":403})
    expire(service,job["id"])
    assert not service.run_once()
    service.reconcile(job["id"])
    assert service.store.job(job["id"])["status"]=="waiting_confirmation"
    assert len(remote.writes)==1


def test_async_receipt_does_not_confirm_before_task_finishes(setup_online):
    service,remote=setup_online
    remote.pending=True
    remote.receipt={"task_id":"pending-price"}
    job=service.change(price_request(service))["job"]
    service.run_once()
    assert service.store.job(job["id"])["status"]=="waiting_confirmation"
    expire(service,job["id"])
    service.reconcile(job["id"])
    assert service.store.job(job["id"])["status"]=="waiting_confirmation"
    remote.pending=False
    expire(service,job["id"])
    assert service.reconcile(job["id"])["job"]["status"]=="confirmed"


def test_partial_receipt_does_not_retry_whole_mutation(setup_online):
    service,remote=setup_online
    remote.receipt={"success":True,"listing_sites":[{"id":"MLM1","success":False,"errors":[{"message":"价格无效"}]}]}
    job=service.change(price_request(service))["job"]
    service.run_once()
    assert service.store.job(job["id"])["status"]=="partial"
    with pytest.raises(OnlineConflict): service.retry(job["id"],uuid4().hex)


@pytest.mark.parametrize("changes",[{"amount":"1","currency":"CNY"},{"amount":"NaN","currency":"USD"},{"amount":"1.001","currency":"USD"},{"amount":"1","currency":"USD","quantity":20}])
def test_invalid_price_scope_never_enqueued(setup_online,changes):
    service,_=setup_online
    with pytest.raises(ValueError): service.change(price_request(service,changes=changes))
    assert not service.store.jobs("mercadolibre","seller")


def test_intentional_pause_can_restock_only_after_platform_pause_evidence():
    item=listing(); item.desired_sale_state="paused"; item.sale_state="paused"; item.raw_status="paused"; item.stocks[0].quantity=0
    req=ChangeRequest(listing_id=item.id,version="v",operation="stock",scope_id="shared",changes={"quantity":8},idempotency_key="test-key")
    with pytest.raises(ValueError): validate_changes(item,req)
    item.raw_sub_status=["paused_by_seller"]
    validate_changes(item,req)


def test_scan_follows_cursor_deduplicates_and_rejects_identity():
    adapter=object.__new__(MercadoOnlineAdapter);adapter.account_id="seller"
    pages=iter([{"seller_id":"seller","results":["CBT1"],"scroll_id":"next","paging":{"total":2}},
                {"seller_id":"seller","results":["CBT1","CBT2"],"paging":{"total":2}}])
    calls=[]
    def get(path): calls.append(path);return next(pages)
    adapter.get=get
    assert [key for page in catalog_pages(adapter) for key in page]==["CBT1","CBT2"]
    assert "scroll_id=next" in calls[1]
    adapter.get=lambda path:{"seller_id":"other","results":[]}
    with pytest.raises(ValueError):[key for page in catalog_pages(adapter) for key in page]


def test_scan_missing_cursor_is_partial_not_complete():
    adapter=object.__new__(MercadoOnlineAdapter);adapter.account_id="seller"
    adapter.get=lambda path:{"seller_id":"seller","results":["CBT1"],"paging":{"total":2}}
    iterator=catalog_pages(adapter);assert next(iterator)==["CBT1"]
    with pytest.raises(ValueError):next(iterator)


def test_mercado_variation_stock_preserves_unselected_variations(monkeypatch):
    adapter=object.__new__(MercadoOnlineAdapter);adapter.token="test"
    item=listing();item.stocks=[StockScope(id="1",label="变体",variation_id="1",quantity=5,writable=True)]
    item.snapshot={"parent":{"variations":[{"id":1,"available_quantity":5},{"id":2,"available_quantity":8}]}}
    captured=[]
    monkeypatch.setattr("erp_web.runtime_units.online_mercadolibre.request_json",lambda *args,**kwargs:captured.append(args) or {})
    adapter.write(item,"stock","1",{"quantity":7})
    assert captured[0][3]=={"variations":[{"id":1,"available_quantity":7},{"id":2,"available_quantity":8}]}


def test_yandex_price_preserves_existing_auxiliary_fields(monkeypatch):
    adapter=object.__new__(YandexOnlineAdapter);adapter.token="test";adapter.business="1";adapter.campaign="2"
    item=listing();item.snapshot={"default_price":{"discountBase":20,"minimumForBestseller":8,"value":12},"campaign":{}}
    captured=[]
    monkeypatch.setattr("erp_web.runtime_units.online_yandex.api.update_yandex_price",lambda *args,**kwargs:captured.append(kwargs) or {})
    adapter.write(item,"price","business",{"amount":"15.25","currency":"CNY"})
    assert captured[0]["offers"][0]["price"]=={"value":15.25,"currencyId":"CNY","discountBase":20,"minimumForBestseller":8}
    assert captured[0]["campaign_id"]==""


def test_yandex_stock_does_not_treat_fit_as_free_stock():
    assert available_stock([{"type":"FIT","count":9},{"type":"FREEZE","count":2}])==7
    assert available_stock([{"type":"FIT","count":9}]) is None


def test_version_excludes_poll_timestamps_but_includes_hidden_state():
    item=listing();version=snapshot_version(item)
    item.synced_at="later"
    assert snapshot_version(item)==version
    item.sale_state="paused"
    assert snapshot_version(item)!=version


def test_explicit_v15_migration_preserves_data_and_backup(tmp_path):
    import sqlite3
    from erp_web.db import ONLINE_SCHEMA_SQL, _SCHEMA_SQL, ErpDatabase
    from scripts.migrate_online_products import migrate
    path=tmp_path/"previous.sqlite3"
    with sqlite3.connect(path) as c:
        c.executescript(_SCHEMA_SQL.removesuffix(ONLINE_SCHEMA_SQL))
        c.execute("PRAGMA user_version=15")
        c.execute("INSERT INTO store_auth(platform,credentials_json) VALUES(?,?)",("mercadolibre", '{"user_id":"preserved"}'))
    backup=migrate(path)
    assert backup.is_file()
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as c: assert c.execute("PRAGMA user_version").fetchone()[0]==15
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0]==17
        assert c.execute("SELECT COUNT(*) FROM online_listings").fetchone()[0]==0
        assert c.execute("SELECT credentials_json FROM store_auth WHERE platform=?",("mercadolibre",)).fetchone()[0]=='{"user_id":"preserved"}'
    ErpDatabase(path)
    with pytest.raises(ValueError): migrate(path)


def test_online_http_contracts(backend_server):
    import requests
    from erp_web.http_route_units.get_routes import FRONTEND_PAGE_ROUTES
    assert FRONTEND_PAGE_ROUTES["/online-products"] == "online-products"
    assert requests.get(backend_server+"/ml-items",timeout=10).status_code==404
    page=requests.get(backend_server+"/api/online-products",params={"platform":"mercadolibre"},timeout=10)
    assert page.status_code==200
    assert page.json()["state"]=="authorization_required"
    invalid=requests.post(backend_server+"/api/online-products/change",json={"operation":"price"},timeout=10)
    assert invalid.status_code==400
    missing_source=requests.get(backend_server+"/api/online-products/source-images",timeout=10)
    assert missing_source.status_code==400 and "listing_id" in missing_source.json()["error"]


def test_user_product_price_targets_one_market_without_fallback(monkeypatch):
    adapter=object.__new__(MercadoOnlineAdapter);adapter.token="test"
    item=listing();item.model="user_products";item.snapshot={"siteless_id":"U123"}
    item.prices=[PriceScope(id="MLM123",label="墨西哥",amount="8",currency="USD",kind="net_proceeds",writable=True)]
    captured=[]
    monkeypatch.setattr("erp_web.runtime_units.online_mercadolibre.request_json",lambda *args,**kwargs:captured.append(args) or {"success":True})
    adapter.write(item,"price","MLM123",{"amount":"9.50","currency":"USD"})
    assert captured==[("PUT","https://api.mercadolibre.com/global/user-products/U123","test",{"listing_sites":[{"listing_id":"MLM123","net_proceeds":9.5}]})]


def test_user_product_mapping_rejects_another_owner():
    from erp_web.marketplaces.mercadolibre_mapping import validate_user_product_mapping
    response=[{"siteless_user_product_id":"U123","owner_id":"other","item_id":"CBT1","user_product_id":"CBTU123","site_items":[]}]
    with pytest.raises(RuntimeError,match="OWNER_ID_MISMATCH"):
        validate_user_product_mapping(response,{"siteless_user_product_id":"U123","account_user_id":"seller"})


def test_finished_user_product_task_keeps_per_item_failure():
    adapter=object.__new__(MercadoOnlineAdapter)
    adapter.get=lambda path:{"task_id":"task-1","status":"finished","user_products":[{"id":"MLMU1","status":"succeeded"},{"id":"MLCU2","status":"failed","reasons":[{"code":"invalid"}]}]}
    result=adapter.confirmation_details(listing(),"content",{"listing_sites":[{"task_id":"task-1"}]})
    assert result["pending"] is False
    assert result["errors"][0]["id"]=="MLCU2"


def test_rejected_pause_does_not_leave_unaccepted_local_intent(setup_online):
    service,remote=setup_online
    remote.write_error=PublishAdapterError("INVALID","平台拒绝",details={"http_status":400})
    body=price_request(service,operation="sale_state",scope_id="global",changes={"state":"paused"})
    job=service.change(body)["job"];service.run_once()
    assert service.store.job(job["id"])["status"]=="failed"
    assert service.store.get("CBT1").desired_sale_state==""


def test_yandex_http_200_business_rejection_is_definite(monkeypatch):
    from erp_web.marketplaces.yandex_http import YandexApiError
    adapter=object.__new__(YandexOnlineAdapter)
    def reject(*args):
        raise YandexApiError("INVALID_CONTENT","内容未接受",http_status=200)
    monkeypatch.setattr(adapter,"_write",reject)
    with pytest.raises(YandexApiError) as error:
        adapter.write(listing(),"content","global",{"title":"新标题"})
    assert error.value.details["definitively_rejected"] is True


def test_yandex_price_quarantine_requires_attention_and_stops_pending(monkeypatch):
    adapter=object.__new__(YandexOnlineAdapter);adapter.token="test";adapter.business="1";adapter.campaign="2"
    captured=[]
    monkeypatch.setattr("erp_web.runtime_units.online_yandex.api.fetch_yandex_price_quarantine",lambda *args,**kwargs:captured.append(kwargs) or [{"offerId":"CBT1"}])
    result=adapter.confirmation_details(listing(),"price",{},"campaign")
    assert result["pending"] is False
    assert result["needs_attention"] is True
    assert captured[0]["business_id"]=="" and captured[0]["campaign_id"]=="2"


def test_legacy_confirmation_timer_never_dispatches_and_manual_check_ignores_due_time(setup_online):
    import time
    service, remote = setup_online
    remote.receipt = {"task_id": "async-price"}
    job = service.change(price_request(service))["job"]
    assert service.run_once()
    with service.store.db._connect() as conn:
        conn.execute("""UPDATE online_jobs SET lease_until=0,
            result_json=json_set(result_json,'$.automatic_confirmation_pending',1,'$.next_confirmation_at',0)
            WHERE id=?""", (job["id"],))
        conn.commit()
    for _ in range(3):
        assert not service.run_once()
    assert service.store.job(job["id"])["status"] == "waiting_confirmation"
    with service.store.db._connect() as conn:
        conn.execute("UPDATE online_jobs SET result_json=json_set(result_json,'$.next_confirmation_at',?) WHERE id=?", (time.time()+86400, job["id"]))
        conn.commit()
    assert service.reconcile(job["id"])["job"]["status"] == "confirmed"
    assert len(remote.writes) == 1
