"""未确认修改的人工收尾：证据、并发、防重放与新操作边界。"""
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from erp_web.schemas.online_products import CloseOnlineOperationRequest
from erp_web.stores.online_product_store import OnlineConflict
from tests.test_online_products import setup_online, price_request, expire  # noqa: F401


def unconfirmed(service, remote):
    remote.write_error = TimeoutError("写请求超时")
    body = price_request(service)
    job = service.change(body)["job"]
    service.run_once()
    return service.store.job(job["id"]), body


def close_body(job):
    return {"job_id": job["id"], "expected_updated_at": job["updated_at"], "acknowledge": True}


def test_conflict_links_exact_job_even_outside_recent_list(setup_online, monkeypatch):
    from erp_web.context import get_context
    from erp_web.facades.online_product_facade import mutate, read
    service, remote = setup_online
    monkeypatch.setattr(get_context(), "_online_products", service)
    blocking = service.change(price_request(service))["job"]
    with service.store.db._connect() as conn:
        conn.execute("UPDATE online_jobs SET created_at='2000-01-01T00:00:00Z' WHERE id=?", (blocking["id"],))
        conn.commit()
    for index in range(100):
        service.store.enqueue("mercadolibre", "seller", "stock", f"other-{index}", {}, uuid4().hex)
    assert blocking["id"] not in {job["id"] for job in service.list("mercadolibre")["jobs"]}
    result, status = mutate("change", price_request(service))
    assert status == 409
    assert result["error_code"] == "ONLINE_CONFLICT"
    assert result["conflict_job_id"] == blocking["id"]
    detail = read({"job_id": result["conflict_job_id"]})
    assert detail["job"]["id"] == blocking["id"]
    assert detail["job"]["target_id"] == "CBT1"
    assert remote.writes == []
    monkeypatch.setattr(service.context.config, "load_store_config", lambda: {"mercadolibre": {"user_id": "different-seller"}})
    with pytest.raises(OnlineConflict, match="店铺身份"):
        read({"job_id": blocking["id"]})


def test_unrelated_conflict_does_not_invent_job_link(setup_online, monkeypatch):
    from erp_web.context import get_context
    from erp_web.facades.online_product_facade import mutate
    service, _ = setup_online
    monkeypatch.setattr(get_context(), "_online_products", service)
    result, status = mutate("change", price_request(service, version="old-version"))
    assert status == 409
    assert "conflict_job_id" not in result


def test_close_preserves_unknown_history_and_only_unlocks_new_operations(setup_online):
    service, remote = setup_online
    job, body = unconfirmed(service, remote)
    with pytest.raises(OnlineConflict, match="成功查询"):
        service.close_operation(close_body(job))
    job = service.reconcile(job["id"])["job"]
    evidence = job["result"]["evidence"]
    assert evidence["observed"] == {"amount": "12", "currency": "USD"}
    assert evidence["checked_at"] != evidence["synced_at"]
    closed = service.close_operation(close_body(job))["job"]
    assert closed["status"] == "closed_unconfirmed"
    assert closed["dispatched"] == 1
    assert closed["request"] == body
    assert closed["result"]["error"] == job["result"]["error"]
    assert closed["result"]["resolution"]["evidence"] == evidence
    assert closed["result"]["resolution"]["previous_status"] == "outcome_unknown"
    assert service.close_operation(close_body(job))["job"] == closed
    assert service.change(body)["job"] == closed  # 原提交身份不会变成新请求。
    with pytest.raises(OnlineConflict):
        service.retry(job["id"], uuid4().hex)
    with pytest.raises(OnlineConflict):
        service.reconcile(job["id"])
    assert not service.run_once()
    assert len(remote.writes) == 1
    remote.write_error = None
    new_job = service.change(price_request(service))["job"]
    assert new_job["id"] != closed["id"]
    assert service.run_once()
    assert len(remote.writes) == 2
    assert service.store.job(closed["id"]) == closed


@pytest.mark.parametrize("acknowledge", [False, "true", 1, None])
def test_close_requires_explicit_boolean_acknowledgement(acknowledge):
    with pytest.raises(ValidationError):
        CloseOnlineOperationRequest(job_id="job", expected_updated_at="now", acknowledge=acknowledge)


@pytest.mark.parametrize("problem", ["expired", "changed_job", "changed_listing", "syncing", "read_failure", "incomplete", "pending", "missing_scope"])
def test_close_rejects_stale_incomplete_or_inflight_evidence(setup_online, problem):
    service, remote = setup_online
    job, _ = unconfirmed(service, remote)
    job = service.reconcile(job["id"])["job"]
    body = close_body(job)
    if problem == "expired":
        result = job["result"]
        result["evidence"]["checked_at"] = "2020-01-01T00:00:00Z"
        with service.store.db._connect() as conn:
            conn.execute("UPDATE online_jobs SET result_json=? WHERE id=?", (json.dumps(result), job["id"]))
            conn.commit()
    elif problem == "changed_job":
        body["expected_updated_at"] = "old-version"
    elif problem == "changed_listing":
        item = service.store.get("CBT1")
        item.prices[0].amount = "13"
        service.store.save(item)
    elif problem == "syncing":
        service.sync("mercadolibre", uuid4().hex)
    else:
        expire(service, job["id"])
        if problem == "read_failure":
            remote.read_error = TimeoutError("查询超时")
        elif problem == "incomplete":
            remote.rows["CBT1"].errors = ["回读不完整"]
        elif problem == "pending":
            remote.pending = True
        elif problem == "missing_scope":
            remote.rows["CBT1"].prices = []
        job = service.reconcile(job["id"])["job"]
        body = close_body(job)
        if problem == "read_failure":
            assert not job["result"].get("evidence")  # 失败不能沿用上次成功的证据。
    with pytest.raises(OnlineConflict):
        service.close_operation(body)
    assert service.store.job(job["id"])["status"] != "closed_unconfirmed"
    assert len(remote.writes) == 1


def test_confirmed_change_is_not_manually_closed(setup_online):
    service, remote = setup_online
    job, _ = unconfirmed(service, remote)
    remote.rows["CBT1"].prices[0].amount = "15.25"
    job = service.reconcile(job["id"])["job"]
    assert job["status"] == "confirmed"
    with pytest.raises(OnlineConflict):
        service.close_operation(close_body(job))


def test_stock_recovery_compares_the_requested_warehouse_and_keeps_remote_quantity(setup_online):
    service, remote = setup_online
    remote.write_error = TimeoutError("库存写入结果未知")
    body = price_request(service, operation="stock", scope_id="shared", changes={"quantity": 0})
    job = service.change(body)["job"]
    service.run_once()
    job = service.reconcile(job["id"])["job"]
    assert job["result"]["evidence"]["observed"] == {"quantity": 5}
    assert job["result"]["confirmation"] == {"quantity": False}
    service.close_operation(close_body(job))
    assert service.store.get("CBT1").stocks[0].quantity == 5
    assert len(remote.writes) == 1


def test_waiting_confirmation_can_be_closed_when_platform_not_pending(setup_online):
    service, remote = setup_online
    remote.apply = False
    job = service.change(price_request(service))["job"]
    service.run_once()
    job = service.reconcile(job["id"])["job"]
    assert job["status"] == "waiting_confirmation"
    assert service.close_operation(close_body(job))["job"]["status"] == "closed_unconfirmed"


def test_closing_revokes_old_lease_and_checks_account(setup_online):
    service, remote = setup_online
    job, _ = unconfirmed(service, remote)
    claim = service.store.claim_reconcile(job["id"])
    service._prepare_confirmation(claim, claim["result"])
    service._confirm(claim, remote, claim["result"])
    job = service.store.job(job["id"])
    original_config = service.context.config.load_store_config
    service.context.config.load_store_config = lambda: {"mercadolibre": {"user_id": "another"}}
    with pytest.raises(OnlineConflict, match="店铺身份"):
        service.close_operation(close_body(job))
    service.context.config.load_store_config = original_config
    service.close_operation(close_body(job))
    with pytest.raises(OnlineConflict, match="租约"):
        service.store.update_job(job["id"], "outcome_unknown", claim["result"], owner=claim["lease_token"])


def test_http_close_contract_is_validated_and_routes_to_same_service(setup_online, monkeypatch):
    from erp_web.context import get_context
    from erp_web.facades.online_product_facade import mutate
    from erp_web.schemas.requests import validate_request_payload, RequestValidationError
    service, remote = setup_online
    monkeypatch.setattr(get_context(), "_online_products", service)
    job, _ = unconfirmed(service, remote)
    job = service.reconcile(job["id"])["job"]
    body = close_body(job)
    with pytest.raises(RequestValidationError):
        validate_request_payload({"job_id": job["id"]}, endpoint="/api/online-products/close")
    result, status = mutate("close", validate_request_payload(body, endpoint="/api/online-products/close"))
    assert status == 200 and result["job"]["status"] == "closed_unconfirmed"
