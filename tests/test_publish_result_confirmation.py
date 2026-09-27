"""发布确认的时间门槛、只读边界、持久化恢复和并发行为。"""

from copy import deepcopy
from threading import Event
from types import SimpleNamespace

import pytest

from erp_web.db import ErpDatabase
from erp_web.runtime_units import publish_result_confirmation as confirmation
from erp_web.runtime_units.publishing_bus_core import PublishingBus


class Adapter:
    def __init__(self):
        self.writes = 0
        self.reads = 0
        self.result = {"ok": True, "status": "pending_confirmation", "task_id": "remote-1"}
        self.error = None
        self.entered = None
        self.release = None

    def resolve_category(self, product, config):
        return product

    def required_attributes_missing(self, context, config):
        return []

    def publish(self, product, platform, config):
        self.writes += 1
        return deepcopy(self.result)

    def poll_publish_status(self, result, config):
        self.reads += 1
        assert result["task_id"] == "remote-1"
        if self.entered:
            self.entered.set()
        if self.release:
            assert self.release.wait(2)
        if self.error:
            raise self.error
        return deepcopy(self.result)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(confirmation, "time", SimpleNamespace(time=lambda: clock[0]))
    adapter = Adapter()
    db = ErpDatabase(tmp_path / "publish.sqlite3")
    bus = PublishingBus(db, {"ozon": adapter}, auto_resume_pending=False)
    schedules = []
    monkeypatch.setattr(bus.confirmation, "schedule", lambda *args: schedules.append(args))
    queued = bus.enqueue({"product_id": "p"}, ["ozon"], targets={"ozon": {
        "draft_id": "d", "site": "global", "product_id": "p",
    }}, idempotency_key="first")
    job_id = queued["job_id"]
    bus.wait(job_id, timeout=2)
    yield bus, adapter, clock, job_id, schedules
    bus.close()


def platform_state(bus, job_id):
    return bus.get_status(job_id)["platforms"]["ozon"]


def test_submission_releases_worker_and_only_schedules_one_delayed_check(setup):
    bus, adapter, clock, job_id, schedules = setup
    item = platform_state(bus, job_id)
    assert adapter.writes == 1 and adapter.reads == 0
    assert item["status"] == bus.get_status(job_id)["status"] == "pending_confirmation"
    assert confirmation.timestamp(item["confirmation"]["next_check_at"]) == clock[0] + 120
    assert len(schedules) == 1
    bus.get_public_status(job_id)
    bus.list_jobs()
    assert adapter.reads == 0


def test_first_check_is_delayed_once_and_never_rearmed(setup):
    bus, adapter, clock, job_id, schedules = setup
    assert bus.check_publish_result(job_id, "ozon", trigger="scheduled")["checked"] is False
    clock[0] += 120
    assert bus.check_publish_result(job_id, "ozon", trigger="scheduled")["resolution"] == "pending"
    clock[0] += 10000
    assert bus.check_publish_result(job_id, "ozon", trigger="scheduled")["checked"] is False
    assert adapter.reads == 1 and adapter.writes == 1 and len(schedules) == 1
    assert platform_state(bus, job_id)["confirmation"]["next_check_at"] == ""


def test_view_checks_require_first_delay_and_five_minutes_after_last_check(setup):
    bus, adapter, clock, job_id, _ = setup
    assert bus.check_publish_result(job_id, "ozon", trigger="view")["resolution"] == "cooldown"
    clock[0] += 120
    assert bus.check_publish_result(job_id, "ozon", trigger="view")["checked"]
    clock[0] += 299
    assert not bus.check_publish_result(job_id, "ozon", trigger="view")["checked"]
    clock[0] += 1
    assert bus.check_publish_result(job_id, "ozon", trigger="view")["checked"]
    assert adapter.reads == 2


def test_manual_check_can_run_early_but_has_cooldown_and_consumes_first_plan(setup):
    bus, adapter, clock, job_id, _ = setup
    assert bus.check_publish_result(job_id, "ozon")["checked"]
    assert not bus.check_publish_result(job_id, "ozon")["checked"]
    clock[0] += 30
    assert bus.check_publish_result(job_id, "ozon")["checked"]
    clock[0] += 120
    assert not bus.check_publish_result(job_id, "ozon", trigger="scheduled")["checked"]
    assert adapter.reads == 2


def test_read_failure_keeps_receipt_pending_state_and_no_write_retry(setup):
    bus, adapter, clock, job_id, _ = setup
    adapter.error = TimeoutError("平台暂时无法连接")
    result = bus.check_publish_result(job_id, "ozon")
    assert result["resolution"] == "query_failed"
    item = platform_state(bus, job_id)
    assert item["status"] == "pending_confirmation"
    assert item["error"] == ""
    assert item["result"]["task_id"] == "remote-1"
    assert item["confirmation"]["check_error"] == "平台暂时无法连接"
    assert item["confirmation"]["next_check_at"] == ""
    assert adapter.reads == adapter.writes == 1


def test_concurrent_manual_and_view_checks_share_one_remote_request(setup):
    bus, adapter, clock, job_id, _ = setup
    clock[0] += 120
    adapter.entered, adapter.release = Event(), Event()
    running = bus.executor.submit(bus.check_publish_result, job_id, "ozon")
    try:
        assert adapter.entered.wait(2)
        second = bus.check_publish_result(job_id, "ozon", trigger="view")
        assert second["resolution"] == "checking" and second["checked"] is False
    finally:
        adapter.release.set()
    assert running.result(timeout=2)["checked"]
    assert adapter.reads == 1


@pytest.mark.parametrize("status, expected", [("published", "success"), ("real_publish_failed", "failed"), ("partial", "partial")])
def test_explicit_terminal_result_persists_and_stops_further_checks(setup, status, expected):
    bus, adapter, clock, job_id, _ = setup
    adapter.result = {"ok": status == "published", "status": status, "task_id": "remote-1"}
    callbacks = []
    bus.terminal_callback = lambda state: callbacks.append(state)
    assert bus.check_publish_result(job_id, "ozon")["resolved"]
    assert platform_state(bus, job_id)["status"] == expected
    assert len(callbacks) == 1
    clock[0] += 10000
    assert not bus.check_publish_result(job_id, "ozon")["checked"]
    assert adapter.reads == 1


def test_pending_publish_lock_survives_different_idempotency_key(setup):
    bus, adapter, _, job_id, _ = setup
    queued = bus.enqueue({"product_id": "p"}, ["ozon"], targets={"ozon": {
        "draft_id": "d", "site": "global", "product_id": "p",
    }}, idempotency_key="second")
    assert queued["job_id"] == job_id
    assert adapter.writes == 1


def test_restart_restores_only_unconsumed_first_plan(setup, monkeypatch):
    bus, adapter, clock, job_id, _ = setup
    restored = PublishingBus(bus.store, {"ozon": adapter}, auto_resume_pending=False)
    scheduled = []
    monkeypatch.setattr(restored.confirmation, "schedule", lambda *args: scheduled.append(args))
    try:
        restored.recover_pending_jobs()
        assert len(scheduled) == 1 and scheduled[0][0] == job_id
        assert adapter.writes == 1 and adapter.reads == 0
        clock[0] += 120
        restored.check_publish_result(job_id, "ozon", trigger="scheduled")
        scheduled.clear()
        restored.recover_pending_jobs()
        assert not scheduled
        assert adapter.reads == adapter.writes == 1
    finally:
        restored.close()


def test_scheduler_dispatches_due_record_without_waiting_in_publish_worker(tmp_path):
    adapter = Adapter()
    adapter.entered = Event()
    bus = PublishingBus(ErpDatabase(tmp_path / "scheduler.sqlite3"), {"ozon": adapter}, auto_resume_pending=False)
    try:
        queued = bus.enqueue({"product_id": "p"}, ["ozon"], targets={"ozon": {
            "draft_id": "d", "site": "global", "product_id": "p",
        }}, idempotency_key="scheduler")
        job_id = queued["job_id"]
        bus.wait(job_id, timeout=2)
        state = bus.get_status(job_id)
        due = confirmation.iso_time(confirmation.time.time() - 1)
        state["platforms"]["ozon"]["confirmation"]["next_check_at"] = due
        bus.store.save_publish_job(state)
        bus.confirmation.schedule(job_id, "ozon", due)
        assert adapter.entered.wait(2)
    finally:
        bus.close()
    assert adapter.reads == adapter.writes == 1
    assert platform_state(bus, job_id)["confirmation"]["next_check_at"] == ""


def test_invalid_confirmation_response_does_not_erase_acceptance(setup):
    bus, adapter, _, job_id, _ = setup
    adapter.result = {"ok": False, "status": "outcome_unknown", "task_id": "remote-1", "error": "响应身份无法验证"}
    assert bus.check_publish_result(job_id, "ozon")["resolution"] == "query_failed"
    item = platform_state(bus, job_id)
    assert item["status"] == "pending_confirmation"
    assert item["result"]["status"] == "pending_confirmation"
    assert item["confirmation"]["check_error"] == "响应身份无法验证"


def test_confirmation_rejects_changed_store_before_reading_remote(setup):
    bus, adapter, _, job_id, _ = setup
    state = bus.get_status(job_id)
    state["approved_publications"] = {"ozon": {"store_identity": "original-store"}}
    bus.store.save_publish_job(state)
    bus.config_provider = lambda: {"ozon": {"client_id": "another-store", "api_key": "test"}}
    assert bus.check_publish_result(job_id, "ozon")["resolution"] == "query_failed"
    assert adapter.reads == 0
    assert platform_state(bus, job_id)["status"] == "pending_confirmation"


def test_non_object_response_keeps_persisted_receipt(setup):
    bus, adapter, _, job_id, _ = setup
    adapter.result = []
    assert bus.check_publish_result(job_id, "ozon")["resolution"] == "query_failed"
    assert platform_state(bus, job_id)["result"]["task_id"] == "remote-1"
