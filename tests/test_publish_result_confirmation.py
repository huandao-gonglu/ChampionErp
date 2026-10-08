"""发布确认的手动冷却、只读边界、持久化恢复和并发行为。"""

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
    queued = bus.enqueue({"product_id": "p"}, ["ozon"], targets={"ozon": {
        "draft_id": "d", "site": "global", "product_id": "p",
    }}, idempotency_key="first")
    job_id = queued["job_id"]
    bus.wait(job_id, timeout=2)
    yield bus, adapter, clock, job_id
    bus.close()


def platform_state(bus, job_id):
    return bus.get_status(job_id)["platforms"]["ozon"]


def test_submission_waits_for_manual_check_and_local_reads_do_not_query(setup):
    bus, adapter, clock, job_id = setup
    item = platform_state(bus, job_id)
    assert adapter.writes == 1 and adapter.reads == 0
    assert item["status"] == bus.get_status(job_id)["status"] == "pending_confirmation"
    assert item["confirmation"]["next_check_at"] == ""
    clock[0] += 10000
    bus.get_public_status(job_id)
    bus.list_jobs()
    bus.recover_pending_jobs()
    assert adapter.reads == 0


@pytest.mark.parametrize("trigger", ["scheduled", "view"])
def test_automatic_triggers_are_rejected_without_remote_reads(setup, trigger):
    bus, adapter, clock, job_id = setup
    clock[0] += 10000
    with pytest.raises(ValueError, match="仅支持手动"):
        bus.check_publish_result(job_id, "ozon", trigger=trigger)
    assert adapter.reads == 0


def test_manual_check_has_no_initial_delay_but_keeps_cooldown(setup):
    bus, adapter, clock, job_id = setup
    assert bus.check_publish_result(job_id, "ozon")["checked"]
    assert not bus.check_publish_result(job_id, "ozon")["checked"]
    clock[0] += 30
    assert bus.check_publish_result(job_id, "ozon")["checked"]
    assert adapter.reads == 2 and adapter.writes == 1


def test_read_failure_keeps_receipt_pending_state_and_no_write_retry(setup):
    bus, adapter, clock, job_id = setup
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


def test_concurrent_manual_checks_share_one_remote_request(setup):
    bus, adapter, clock, job_id = setup
    clock[0] += 120
    adapter.entered, adapter.release = Event(), Event()
    running = bus.executor.submit(bus.check_publish_result, job_id, "ozon")
    try:
        assert adapter.entered.wait(2)
        second = bus.check_publish_result(job_id, "ozon")
        assert second["resolution"] == "checking" and second["checked"] is False
    finally:
        adapter.release.set()
    assert running.result(timeout=2)["checked"]
    assert adapter.reads == 1


@pytest.mark.parametrize("status, expected", [("published", "success"), ("real_publish_failed", "failed"), ("partial", "partial")])
def test_explicit_terminal_result_persists_and_stops_further_checks(setup, status, expected):
    bus, adapter, clock, job_id = setup
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
    bus, adapter, _, job_id = setup
    queued = bus.enqueue({"product_id": "p"}, ["ozon"], targets={"ozon": {
        "draft_id": "d", "site": "global", "product_id": "p",
    }}, idempotency_key="second")
    assert queued["job_id"] == job_id
    assert adapter.writes == 1


def test_restart_ignores_legacy_schedule_but_preserves_manual_confirmation(setup):
    bus, adapter, clock, job_id = setup
    state = bus.get_status(job_id)
    state["platforms"]["ozon"]["confirmation"]["next_check_at"] = confirmation.iso_time(clock[0] - 1)
    bus.store.save_publish_job(state)
    restored = PublishingBus(bus.store, {"ozon": adapter}, auto_resume_pending=False)
    try:
        restored.recover_pending_jobs()
        assert adapter.writes == 1 and adapter.reads == 0
        assert restored.check_publish_result(job_id, "ozon")["checked"]
        restored.recover_pending_jobs()
        assert adapter.reads == adapter.writes == 1
        assert platform_state(restored, job_id)["confirmation"]["next_check_at"] == ""
    finally:
        restored.close()


def test_invalid_confirmation_response_does_not_erase_acceptance(setup):
    bus, adapter, _, job_id = setup
    adapter.result = {"ok": False, "status": "outcome_unknown", "task_id": "remote-1", "error": "响应身份无法验证"}
    assert bus.check_publish_result(job_id, "ozon")["resolution"] == "query_failed"
    item = platform_state(bus, job_id)
    assert item["status"] == "pending_confirmation"
    assert item["result"]["status"] == "pending_confirmation"
    assert item["confirmation"]["check_error"] == "响应身份无法验证"


def test_confirmation_rejects_changed_store_before_reading_remote(setup):
    bus, adapter, _, job_id = setup
    state = bus.get_status(job_id)
    state["approved_publications"] = {"ozon": {"store_identity": "original-store"}}
    bus.store.save_publish_job(state)
    bus.config_provider = lambda: {"ozon": {"client_id": "another-store", "api_key": "test"}}
    assert bus.check_publish_result(job_id, "ozon")["resolution"] == "query_failed"
    assert adapter.reads == 0
    assert platform_state(bus, job_id)["status"] == "pending_confirmation"


def test_non_object_response_keeps_persisted_receipt(setup):
    bus, adapter, _, job_id = setup
    adapter.result = []
    assert bus.check_publish_result(job_id, "ozon")["resolution"] == "query_failed"
    assert platform_state(bus, job_id)["result"]["task_id"] == "remote-1"
