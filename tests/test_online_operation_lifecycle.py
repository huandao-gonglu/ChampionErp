"""平台受理与商品占用分离；覆盖历史回执、真实异步任务和并发写入。"""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from erp_web.db import ErpDatabase, _current_schema_signature, _schema_signature
from erp_web.runtime_units.online_product_capabilities import _call
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.stores.online_product_store import OnlineConflict
from scripts.migrate_online_operations import V16_SCHEMA_SQL, migrate
from tests.test_online_products import setup_online, price_request, expire  # noqa: F401


def use_yandex(service, remote):
    remote.account_id = "business:campaign"
    row = remote.rows["CBT1"]
    row.platform, row.account_id, row.model = "yandex", remote.account_id, "business_offer"
    remote.receipt = {"status": "OK"}
    service.context.config.load_store_config = lambda: {"yandex": {"business_id": "business", "campaign_id": "campaign"}}
    service.adapter_factories["yandex"] = lambda _: remote
    service.store.save(row)


def change(service, operation, **changes):
    return price_request(service, operation=operation, changes=changes,
                         scope_id="shared" if operation == "stock" else "global")


def old_job(service, request, receipt, *, status="submitted"):
    job = service.change(request)["job"]
    result = {"before": service.store.get(request["listing_id"]).model_dump(),
              "receipt": receipt, "changes": request["changes"], "scope_id": request["scope_id"]}
    with service.store.db._connect() as conn:
        conn.execute("UPDATE online_jobs SET status=?,dispatched=1,result_json=?,created_at='2020-01-01T00:00:00Z' WHERE id=?",
                     (status, json.dumps(result, ensure_ascii=False), job["id"]))
        conn.commit()
    return service.store.job(job["id"])


@pytest.mark.parametrize("operation,changes", [("stock", {"quantity": 8}), ("sale_state", {"state": "paused"})])
def test_old_accepted_stock_does_not_lock_new_changes_or_rewrite_history(setup_online, operation, changes):
    service, remote = setup_online
    use_yandex(service, remote)
    previous = old_job(service, change(service, "stock", quantity=0), {"status": "OK"})
    with service.store.db._connect() as conn:
        before = tuple(conn.execute("SELECT * FROM online_jobs WHERE id=?", (previous["id"],)).fetchone())
    new = service.change(change(service, operation, **changes))["job"]
    assert new["id"] != previous["id"] and new["status"] == "queued"
    assert service.change(previous["request"])["job"] == previous
    with service.store.db._connect() as conn:
        assert tuple(conn.execute("SELECT * FROM online_jobs WHERE id=?", (previous["id"],)).fetchone()) == before
    assert not remote.writes


def test_acknowledged_changes_finish_submission_without_confirmation_or_extra_requests(setup_online):
    service, remote = setup_online
    use_yandex(service, remote)
    body = change(service, "sale_state", state="paused")
    job = service.change(body)["job"]
    assert service.run_once()
    current = service.store.job(job["id"])
    assert current["status"] == "submitted" and "polls" not in current["result"]
    assert service.store.get("CBT1").sale_state == "active"  # 受理不伪造平台已生效。
    assert not service.run_once()
    assert service.change(body)["job"]["id"] == job["id"]
    assert len(remote.writes) == 1
    assert service.change(change(service, "stock", quantity=8))["job"]["status"] == "queued"


@pytest.mark.parametrize("status,receipt", [("outcome_unknown", None), ("submitted", None)])
def test_unknown_stock_blocks_related_changes_but_not_yandex_pause(setup_online, status, receipt):
    service, remote = setup_online
    use_yandex(service, remote)
    previous = old_job(service, change(service, "stock", quantity=0), receipt, status=status)
    assert previous["status"] == "outcome_unknown"
    for request in (change(service, "stock", quantity=8), change(service, "sale_state", state="active")):
        with pytest.raises(OnlineConflict) as error:
            service.change(request)
        assert error.value.job_id == previous["id"]
    assert service.change(change(service, "sale_state", state="paused"))["job"]["status"] == "queued"
    assert not remote.writes


@pytest.mark.parametrize("platform,receipt", [("yandex", {"status": "OK"}), ("mercadolibre", {"listing_sites": [{"task_id": "remote-task"}]})])
def test_real_async_price_protects_price_without_locking_content(setup_online, platform, receipt):
    service, remote = setup_online
    if platform == "yandex":
        use_yandex(service, remote)
    previous = old_job(service, price_request(service), receipt)
    assert previous["status"] == "waiting_confirmation"
    with pytest.raises(OnlineConflict):
        service.change(price_request(service))
    assert service.change(change(service, "content", title="新标题"))["job"]["status"] == "queued"
    assert not remote.writes


def test_mercado_unknown_stock_still_protects_sale_state(setup_online):
    service, _ = setup_online
    old_job(service, change(service, "stock", quantity=0), None, status="outcome_unknown")
    with pytest.raises(OnlineConflict):
        service.change(change(service, "sale_state", state="paused"))


def test_two_concurrent_commands_after_accepted_history_still_have_one_writer(setup_online):
    service, remote = setup_online
    remote.apply = False
    previous = old_job(service, price_request(service), {})
    barrier = Barrier(2)
    def submit(_):
        body = price_request(service)
        barrier.wait()
        try:
            return service.change(body)["job"]["id"]
        except OnlineConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert sum(result is not None for result in results) == 1
    assert service.store.job(previous["id"])["status"] == "submitted"
    with pytest.raises(OnlineConflict):
        service.reconcile(previous["id"])
    assert not remote.writes


@pytest.mark.parametrize("receipt,status", [({}, "submitted"), ({"task_id": "remote-task"}, "waiting_confirmation"), (None, "outcome_unknown")])
def test_interrupted_result_query_keeps_saved_receipt_without_replaying_write(setup_online, receipt, status):
    service, remote = setup_online
    previous = old_job(service, price_request(service), receipt)
    claim = service.store.claim_reconcile(previous["id"])
    service._prepare_confirmation(claim, claim["result"])
    with pytest.raises(OnlineConflict):
        service.change(change(service, "content", title="查询期间禁止覆盖"))
    expire(service, previous["id"])
    assert not service.run_once()
    current = service.store.job(previous["id"])
    assert current["status"] == status and current["result"]["receipt"] == receipt
    assert not remote.writes
    with pytest.raises(OnlineConflict):
        service.store.update_job(previous["id"], "confirmed", {}, owner=claim["lease_token"])
    assert service.change(change(service, "content", title="查询恢复后允许独立修改"))["job"]["status"] == "queued"


def test_checking_old_pause_cannot_override_newer_sale_intent(setup_online):
    service, remote = setup_online
    first = service.change(change(service, "sale_state", state="paused"))["job"]
    service.run_once()
    second = service.change(change(service, "sale_state", state="active"))["job"]
    with pytest.raises(OnlineConflict):
        service.reconcile(first["id"])
    service.run_once()
    assert service.store.job(second["id"])["status"] == "submitted"
    assert service.store.get("CBT1").desired_sale_state == "active"
    remote.rows["CBT1"].raw_status = remote.rows["CBT1"].sale_state = "paused"
    assert service.reconcile(first["id"])["job"]["status"] == "confirmed"
    assert service.store.get("CBT1").desired_sale_state == "active"
    assert len(remote.writes) == 2


def test_ai_conflict_keeps_blocking_job_identity(setup_online):
    service, _ = setup_online
    job = service.change(price_request(service))["job"]
    with pytest.raises(BusinessCapabilityError) as error:
        _call(service.change, price_request(service))
    assert error.value.code == "ONLINE_CONFLICT"
    assert error.value.details == {"conflict_job_id": job["id"]}


def test_v16_migration_keeps_all_rows_and_backup_and_limits_execution_index(tmp_path):
    path = tmp_path / "previous.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(V16_SCHEMA_SQL)
        conn.execute("PRAGMA user_version=16")
        conn.execute("INSERT INTO store_auth(platform,credentials_json) VALUES('ozon',?)", ('{"client_id":"保留账号"}',))
        conn.execute("""INSERT INTO online_jobs(id,platform,account_id,operation,target_id,status,idempotency_key,
            request_json,result_json,dispatched,created_at,updated_at) VALUES
            ('old','yandex','shop','stock','sku','submitted','original-key','{}','{"receipt":{"status":"OK"}}',1,'old','old')""")
        original = conn.execute("SELECT * FROM online_jobs").fetchall()
    backup = migrate(path)
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 16
        assert conn.execute("SELECT * FROM online_jobs").fetchall() == original
    ErpDatabase(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 17
        assert _schema_signature(conn) == _current_schema_signature()
        assert conn.execute("SELECT * FROM online_jobs").fetchall() == original
        assert conn.execute("SELECT credentials_json FROM store_auth").fetchone()[0] == '{"client_id":"保留账号"}'
        conn.execute("""INSERT INTO online_jobs SELECT 'new',platform,account_id,operation,target_id,'queued','new-key',
            request_json,'{}','',0,0,created_at,updated_at FROM online_jobs WHERE id='old'""")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""INSERT INTO online_jobs SELECT 'duplicate',platform,account_id,operation,target_id,'running','other-key',
                request_json,'{}','',0,0,created_at,updated_at FROM online_jobs WHERE id='old'""")
    with pytest.raises(ValueError):
        migrate(path)


def test_v16_migration_rejects_unrecognized_schema_without_touching_data(tmp_path):
    path = tmp_path / "other.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(V16_SCHEMA_SQL)
        conn.execute("PRAGMA user_version=16")
        conn.execute("CREATE TABLE unknown_table(id TEXT)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="结构完整"):
        migrate(path)
    assert path.read_bytes() == before and not list(tmp_path.glob('*.bak'))
