"""临时只读故障的冷却、并发探测和持久恢复；不访问真实平台。"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from erp_web.schemas.external_requests import ExternalRequestBlocked, RequestContext, RequestFailure
from erp_web.services.external_request_manager import ExternalRequestManager
from erp_web.stores.external_request_store import ExternalRequestStore


@pytest.fixture
def circuit(tmp_path, monkeypatch):
    clock = [1800000000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    manager = ExternalRequestManager(ExternalRequestStore(tmp_path / "requests.sqlite3"))
    ctx = RequestContext(platform="yandex", account_id="business", interface="/orders", source="orders", semantics="read")
    for _ in range(5):
        request_id = manager.start(ctx, "POST")
        manager.check(request_id, ctx, clock[0]+30)
        manager.network_error(request_id, ctx)
        clock[0] += 1
    return manager, ctx, clock


def probe(manager, ctx, clock):
    request_id = manager.start(ctx, "POST")
    manager.check(request_id, ctx, clock[0]+30)
    return request_id


def test_cooldown_survives_restart_and_success_recovers(circuit):
    manager, ctx, clock = circuit
    with pytest.raises(ExternalRequestBlocked):
        probe(manager, ctx, clock)
    clock[0] += 61
    manager = ExternalRequestManager(ExternalRequestStore(manager.store.path))
    request_id = probe(manager, ctx, clock)
    manager.result(request_id, ctx, 200, {}, b'{}')
    assert manager.store.blocks() == []
    assert "自动恢复" in manager.store.recoveries()[0]["reason"]
    probe(manager, ctx, clock)


def test_only_one_concurrent_probe_and_late_result_cannot_recover(circuit):
    manager, ctx, clock = circuit
    clock[0] += 61
    def run(_):
        try:
            return probe(manager, ctx, clock)
        except ExternalRequestBlocked:
            return None
    with ThreadPoolExecutor(8) as pool:
        ids = list(pool.map(run, range(8)))
    sent = [value for value in ids if value]
    assert len(sent) == 1
    clock[0] += 31
    with pytest.raises(ExternalRequestBlocked):
        probe(manager, ctx, clock)
    manager.result(sent[0], ctx, 200, {}, b'{}')
    assert manager.store.blocks()[0]["failure"]["cooldown_seconds"] == 120
    clock[0] += 121
    manager.result(probe(manager, ctx, clock), ctx, 200, {}, b'{}')
    assert manager.store.blocks() == []


def test_probe_failure_backs_off_to_cap_and_manual_check_does_not_fan_out(circuit):
    manager, ctx, clock = circuit
    for expected in (120, 240, 480, 960, 1800, 1800):
        manager.store.request_probe(ctx.platform, ctx.account_id, {ctx.interface})
        request_id = probe(manager, ctx, clock)
        manager.store.request_probe(ctx.platform, ctx.account_id, {ctx.interface})
        with pytest.raises(ExternalRequestBlocked):
            probe(manager, ctx, clock)
        manager.network_error(request_id, ctx)
        assert manager.store.blocks()[0]["failure"]["resume_at"] == clock[0]+expected


def test_hard_restrictions_and_unknown_writes_are_not_recovered(circuit):
    manager, ctx, clock = circuit
    clock[0] += 61
    with pytest.raises(ExternalRequestBlocked):
        probe(manager, replace(ctx, semantics="write"), clock)
    manager.store.block(ctx, RequestFailure("YANDEX_ACCOUNT_DISABLED", "账号已停用", "account"))
    manager.store.request_probe(ctx.platform, ctx.account_id, {ctx.interface})
    with pytest.raises(ExternalRequestBlocked) as error:
        probe(manager, ctx, clock)
    assert error.value.code == "YANDEX_ACCOUNT_DISABLED"
    assert not manager.store.blocks()[0]["failure"].get("probe_id")


def test_probe_permission_failure_remains_blocked_after_time(circuit):
    manager, ctx, clock = circuit
    clock[0] += 61
    manager.result(probe(manager, ctx, clock), ctx, 403, {}, b'{}')
    clock[0] += 86400
    manager.store.request_probe(ctx.platform, ctx.account_id, {ctx.interface})
    with pytest.raises(ExternalRequestBlocked) as error:
        probe(manager, ctx, clock)
    assert error.value.code == "YANDEX_AUTH_FAILED"


def test_old_block_migrates_only_with_complete_read_failure_evidence(circuit):
    manager, ctx, clock = circuit
    manager.store.block(ctx, RequestFailure("EXTERNAL_REPEATED_FAILURE", "旧阻断", "interface"))
    restored = ExternalRequestStore(manager.store.path)
    assert restored.blocks()[0]["failure"]["code"] == "EXTERNAL_TRANSIENT_FAILURE"
    other = replace(ctx, interface="/unknown")
    restored.block(other, RequestFailure("EXTERNAL_REPEATED_FAILURE", "没有历史证据", "interface"))
    restored = ExternalRequestStore(manager.store.path)
    unknown = next(b for b in restored.blocks() if b["scope_key"] == "/unknown")
    assert unknown["failure"]["resume_at"] is None


def test_confirmed_recovery_requires_reason_and_unchanged_block(circuit):
    manager, ctx, clock = circuit
    manager.store.block(ctx, RequestFailure("YANDEX_AUTH_FAILED", "权限不足", "interface"))
    blocks = manager.store.blocks()
    with pytest.raises(ValueError, match="原因"):
        manager.store.recover_confirmed(blocks, reason="")
    manager.store.block(ctx, RequestFailure("YANDEX_RESOURCE_LIMIT", "限流", "interface", resume_at=clock[0]+300))
    with pytest.raises(ValueError, match="状态已变化"):
        manager.store.recover_confirmed(blocks, reason="已开通权限")
    with pytest.raises(ValueError, match="限流"):
        manager.store.recover_confirmed(manager.store.blocks(), reason="已处理")
