"""多商品批量停售/归档：平台数量上限、逐项回执和原子防重。"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from erp_web.context import get_context
from erp_web.schemas.online_products import digest
from erp_web.services.online_product_service import OnlineProductService
from erp_web.stores.online_product_store import OnlineConflict
from tests.test_yandex_online_archive import ArchivePlatform
from tests.test_yandex_online_sync import Platform, adapter


def setup_batch(count=3, archived=False):
    remote = ArchivePlatform()
    remote.rows = Platform(count).rows
    for i, row in enumerate(remote.rows):
        row["offer"]["archived"] = archived
        row["offer"]["groupId"] = str(i // 2)
    client = adapter(remote)
    context = SimpleNamespace(db=get_context().db, config=SimpleNamespace(load_store_config=lambda: {"yandex": {"business_id": "1", "campaign_id": "2"}}))
    service = OnlineProductService(context, adapter_factories={"yandex": lambda _: client}, start_worker=False)
    # 批量写入测试使用已读取完整详情的快照，不把新的基础同步当作详情加载。
    for row in client.read_many([item["offer"]["offerId"] for item in remote.rows]).listings:
        service.store.save(row)
    return service, remote, client


def command(service, operation="archive", changes=None):
    rows = service.store.listings("yandex", "1:2")
    return {"items": [{"listing_id": row.id, "version": row.version} for row in rows],
            "operation": operation, "changes": changes or {"archived": True}, "idempotency_key": uuid4().hex}


@pytest.mark.parametrize("operation,changes,count,limit", [
    ("sale_state", {"state": "paused"}, 501, 500),
    ("sale_state", {"state": "active"}, 3, 500),
    ("archive", {"archived": True}, 201, 200),
    ("archive", {"archived": False}, 201, 200),
])
def test_multiple_products_use_native_batches_and_platform_limits(operation, changes, count, limit):
    service, remote, _ = setup_batch(count, archived=changes.get("archived") is False)
    body = command(service, operation, changes)
    snapshots = {row.id: row for row in service.store.listings("yandex", "1:2")}
    jobs = service.change_batch(body)["jobs"]
    assert len(jobs) == count and all(job["status"] == "queued" for job in jobs)
    assert not remote.writes
    while service.run_once():
        pass
    assert [len(data.get("offerIds", data.get("hiddenOffers", []))) for _, data in remote.writes] == ([limit, count-limit] if count > limit else [count])
    expected = "confirmed"
    assert all(service.store.job(job["id"])["status"] == expected for job in jobs)
    assert {job["id"] for job in service.change_batch(body)["jobs"]} == {job["id"] for job in jobs}
    assert not service.run_once()
    for row in service.store.listings("yandex", "1:2"):
        assert row.stocks == snapshots[row.id].stocks
        if operation == "sale_state":
            assert row.desired_sale_state == changes["state"]


def test_preview_expands_selected_parent_and_deduplicates_selected_sku_without_network():
    service, remote, _ = setup_batch(5)
    page = service.list("yandex", query="sku-0")
    group = page["groups"][0]
    other = next(row for row in service.store.listings("yandex", "1:2") if row.remote_id == "sku-4")
    remote.calls.clear()
    preview = service.preview_batch({"platform": "yandex", "group_ids": [group["id"]],
        "listing_ids": [page["items"][0]["id"], other.id], "operation": "sale_state", "changes": {"state": "paused"}})
    assert {row["seller_sku"] for row in preview["items"]} == {"sku-0", "sku-1", "sku-4"}
    assert preview["product_count"] == 2 and preview["scope"] == "当前店铺"
    assert not remote.calls and not remote.writes
    with pytest.raises(OnlineConflict, match="当前目录"):
        service.preview_batch({"platform": "yandex", "group_ids": ["invalid"], "operation": "archive", "changes": {"archived": True}})


@pytest.mark.parametrize("restore", [False, True])
def test_partial_archive_rejection_only_fails_affected_sku(restore):
    service, remote, _ = setup_batch(3, archived=restore)
    key = "notUnarchivedOfferIds" if restore else "notArchivedOffers"
    remote.receipt = {"status": "OK", "result": {key: ["sku-1"] if restore else [{"offerId": "sku-1", "error": "OFFER_HAS_STOCKS"}]}}
    jobs = service.change_batch(command(service, changes={"archived": not restore}))["jobs"]
    service.run_once()
    for job in jobs:
        row = service.store.get(job["target_id"])
        assert service.store.job(job["id"])["status"] == ("failed" if row.remote_id == "sku-1" else "confirmed")
        assert row.archived == (restore if row.remote_id == "sku-1" else not restore)
    assert len(remote.writes) == 1


def test_network_failure_is_unknown_for_entire_request_without_automatic_replay():
    service, remote, _ = setup_batch()
    remote.timeout = True
    body = command(service)
    jobs = service.change_batch(body)["jobs"]
    service.run_once()
    assert all(service.store.job(job["id"])["status"] == "outcome_unknown" for job in jobs)
    assert len(service.change_batch(body)["jobs"]) == 3
    assert not service.run_once() and len(remote.writes) == 1
    for job in jobs:
        assert service.reconcile_once(job["id"])["job"]["status"] == "confirmed"
    assert len(remote.writes) == 1


def test_atomic_enqueue_rejects_conflict_and_changed_idempotency_manifest():
    service, _, _ = setup_batch()
    body = command(service)
    rows = [service.store.get(item["listing_id"]) for item in body["items"]]
    service.change({"listing_id": rows[-1].id, "version": rows[-1].version, "operation": "sale_state", "scope_id": "global",
                    "changes": {"state": "paused"}, "idempotency_key": uuid4().hex})
    with pytest.raises(OnlineConflict):
        service.change_batch(body)
    assert not service.store.batch_jobs(digest(["online-batch", body["idempotency_key"]]))
    service.run_once()
    jobs = service.change_batch(body)["jobs"]
    for modified in ({**body, "items": body["items"][:-1]}, {**body, "changes": {"archived": False}}):
        with pytest.raises(OnlineConflict, match="提交键"):
            service.change_batch(modified)
    assert len(service.store.batch_jobs(jobs[0]["result"]["batch_id"])) == 3


def test_platform_version_conflict_does_not_prevent_other_members_from_submitting():
    service, remote, _ = setup_batch()
    jobs = service.change_batch(command(service))["jobs"]
    remote.rows[0]["offer"]["name"] = "平台已改名"
    service.run_once()
    assert remote.writes[0][1]["offerIds"] and "sku-0" not in remote.writes[0][1]["offerIds"]
    assert len(remote.writes[0][1]["offerIds"]) == 2
    failed = [job for job in jobs if service.store.job(job["id"])["status"] == "failed"]
    assert len(failed) == 1 and not service.store.job(failed[0]["id"])["dispatched"]


def test_restart_after_dispatch_never_replays_batch():
    service, remote, client = setup_batch()
    original = client.write_batch
    def crash(*args):
        original(*args)
        raise SystemExit("模拟回执落盘前进程退出")
    client.write_batch = crash
    jobs = service.change_batch(command(service))["jobs"]
    with pytest.raises(SystemExit):
        service.run_once()
    with service.store.db._connect() as conn:
        conn.execute("UPDATE online_jobs SET lease_until=0 WHERE status='running'")
        conn.commit()
    assert not service.run_once()
    assert all(service.store.job(job["id"])["status"] == "outcome_unknown" for job in jobs)
    assert len(remote.writes) == 1


def test_official_nullable_archive_failure_list_means_success():
    service, remote, _ = setup_batch()
    remote.receipt = {"status": "OK", "result": {"notArchivedOffers": None}}
    jobs = service.change_batch(command(service))["jobs"]
    service.run_once()
    assert all(service.store.job(job["id"])["status"] == "confirmed" for job in jobs)


def test_duplicate_members_and_wrong_operation_do_not_enqueue():
    service, remote, _ = setup_batch()
    body = command(service)
    for invalid in ({**body, "items": body["items"]*2}, {**body, "operation": "stock", "changes": {"quantity": 0}}):
        with pytest.raises(ValueError):
            service.change_batch(invalid)
    assert not remote.writes and not service.run_once()


def test_crash_after_receipt_keeps_archive_unknown_until_local_projection_is_reconciled():
    service, remote, _ = setup_batch()
    original = service.store.archive_state
    def crash(*args, **kwargs):
        raise SystemExit("模拟回执已保存、目录尚未更新时退出")
    service.store.archive_state = crash
    jobs = service.change_batch(command(service))["jobs"]
    with pytest.raises(SystemExit):
        service.run_once()
    with service.store.db._connect() as conn:
        conn.execute("UPDATE online_jobs SET lease_until=0 WHERE status='running'")
        conn.commit()
    assert not service.run_once()
    assert all(service.store.job(job["id"])["status"] == "outcome_unknown" for job in jobs)
    service.store.archive_state = original
    for job in jobs:
        assert service.reconcile_once(job["id"])["job"]["status"] == "confirmed"
        assert service.store.get(job["target_id"]).archived
    assert len(remote.writes) == 1


def test_http_preview_and_submission_use_shared_validation_and_return_before_remote_write(monkeypatch):
    from erp_web.facades import online_product_facade
    from erp_web.http_route_units.online_product_routes import handle_change_batch, handle_change_batch_preview
    service, remote, _ = setup_batch()
    monkeypatch.setattr(online_product_facade, "get_context", lambda: SimpleNamespace(online_products=service))
    output = []
    group = service.list("yandex")["groups"][0]
    handler = SimpleNamespace(path="/api/online-products/change-batch/preview", read_body=lambda: {
        "platform": "yandex", "group_ids": [group["id"]], "operation": "sale_state", "changes": {"state": "paused"}},
        send_json=lambda data, status: output.append((data, status)))
    handle_change_batch_preview(handler)
    preview, status = output.pop()
    assert status == 200 and not remote.writes
    body = {"items": [{"listing_id": row["listing_id"], "version": row["version"]} for row in preview["items"]],
            "operation": "sale_state", "changes": {"state": "paused"}, "idempotency_key": uuid4().hex}
    handler.path = "/api/online-products/change-batch"
    handler.read_body = lambda: body
    handle_change_batch(handler)
    response, status = output.pop()
    assert status == 200 and all(job["status"] == "queued" for job in response["jobs"])
    assert not remote.writes
    service.run_once()
    assert len(remote.writes) == 1
    handle_change_batch(handler)
    assert output[-1][1] == 200 and len(remote.writes) == 1
    assert all("before" not in job["result"] for job in output[-1][0]["jobs"])
    body["items"][0]["version"] = "stale"
    handle_change_batch(handler)
    assert output[-1][1] == 409
