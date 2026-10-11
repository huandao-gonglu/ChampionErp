"""已确认恢复的旧中断可清理，当前阻断和未确认的未知写入继续保留。"""
from dataclasses import replace
import sqlite3
import threading
from http.server import ThreadingHTTPServer

import pytest
import requests

from erp_web.context import get_context
from erp_web.http_handler import Handler
from erp_web.schemas.external_requests import ExternalRequestBlocked, RequestContext, RequestFailure
from erp_web.services.external_request_control_service import ExternalRequestControlService
from erp_web.stores.external_request_store import ExternalRequestStore, EXTERNAL_REQUEST_SCHEMA_SQL


@pytest.fixture
def audit(monkeypatch):
    clock = [1800000000.0]
    monkeypatch.setattr('time.time', lambda: clock[0])
    manager = get_context().external_requests
    ctx = RequestContext(platform='yandex', account_id='shop', interface='/orders', source='离线测试',
                         semantics='read', credential_id='credential', quota_key='quota', fingerprint='parameters')
    return manager, ctx, clock


def record(manager, ctx, *, status=200, body=b'{}'):
    identity = manager.start(ctx, 'POST')
    manager.check(identity, ctx, 1800100000)
    manager.result(identity, ctx, status, {}, body)
    return identity


def test_repeated_local_rejections_clear_across_pages_without_recovering_block(audit):
    manager, ctx, clock = audit
    source = record(manager, ctx, status=403)
    for _ in range(105):
        with pytest.raises(ExternalRequestBlocked):
            manager.check(manager.start(ctx, 'POST'), ctx, clock[0] + 30)
    clock[0] += 601
    control = ExternalRequestControlService(manager.store)
    snapshot = control.status(offset=100)
    assert snapshot['total'] == 106 and snapshot['clearable_count'] == 105
    blocks = manager.store.blocks()
    assert control.clear_history(before=snapshot['cleanup_before'])['cleared_count'] == 105
    assert manager.store.blocks() == blocks
    assert manager.store.recoveries() == []
    assert [row['id'] for row in manager.store.query()['items']] == [source]
    assert control.clear_history(before=snapshot['cleanup_before'])['cleared_count'] == 0
    with pytest.raises(ExternalRequestBlocked):
        manager.check(manager.start(ctx, 'POST'), ctx, clock[0] + 30)


@pytest.mark.parametrize('scope', ['account', 'credential', 'interface', 'quota', 'operation', 'request'])
def test_active_block_keeps_sent_evidence_for_every_scope(audit, scope):
    manager, ctx, clock = audit
    source = record(manager, ctx, status=400)
    manager.store.recover(ctx.platform, ctx.account_id, 'request', manager.store.scope_key(ctx, 'request'), reason='准备范围隔离验证')
    manager.store.block(ctx, RequestFailure('TEST_RESTRICTION', '待处理', scope))
    recovered = manager.store.recoveries()
    clock[0] += 601
    assert manager.store.clear_interruptions(before=int(clock[0])) == 0
    assert manager.store.query()['items'][0]['id'] == source
    assert manager.store.recoveries() == recovered


def test_cleanup_preserves_unknown_writes_success_running_and_recent_records(audit):
    manager, ctx, clock = audit
    old = record(manager, ctx, status=503)
    success = record(manager, ctx)
    unknown_ctx = replace(ctx, interface='/write', semantics='write')
    unknown = manager.start(unknown_ctx, 'POST')
    manager.check(unknown, unknown_ctx, clock[0] + 30)
    manager.network_error(unknown, unknown_ctx)
    running = manager.start(replace(ctx, interface='/running'), 'POST')
    manager.store.configure('yandex', requests_per_minute=20)
    clock[0] += 601
    recent = record(manager, ctx, status=503)
    # 创建较早、刚收到结果的请求也不能删除，否则影响当前故障窗口。
    manager.store.finish(running, result={'outcome': 'network_error'})
    inflight = manager.start(ctx, 'POST')
    assert manager.store.clear_interruptions(before=int(clock[0])) == 1
    ids = {row['id'] for row in manager.store.query()['items']}
    assert old not in ids and {success, unknown, running, recent, inflight} <= ids
    with manager.store._connect() as conn:
        assert conn.execute('SELECT requests_per_minute FROM external_policies').fetchone()[0] == 20


def test_snapshot_does_not_include_new_records_or_newly_matured_recent_records(audit):
    manager, ctx, clock = audit
    old = record(manager, ctx, status=503)
    clock[0] += 590
    recent = record(manager, ctx, status=503)
    clock[0] += 11
    before = int(clock[0])
    clock[0] += 601
    newer = record(manager, ctx, status=503)
    assert manager.store.clear_interruptions(before=before) == 1
    assert {row['id'] for row in manager.store.query()['items']} == {recent, newer}
    assert old not in {recent, newer}


def test_new_block_after_preview_is_rechecked_before_deletion(audit):
    manager, ctx, clock = audit
    record(manager, ctx, status=503)
    clock[0] += 601
    before = int(clock[0])
    assert manager.store.clearable_interruptions(before=before) == 1
    manager.store.block(ctx, RequestFailure('TEST_RESTRICTION', '新的阻断', 'interface'))
    assert manager.store.clear_interruptions(before=before) == 0


def test_recovered_request_can_be_cleared_without_deleting_recovery_audit(audit):
    manager, ctx, clock = audit
    record(manager, ctx, status=400)
    manager.store.recover_confirmed(manager.store.blocks(), reason='参数已修正')
    audit_before = manager.store.recoveries()
    clock[0] += 601
    assert manager.store.clear_interruptions(before=int(clock[0])) == 1
    assert manager.store.recoveries() == audit_before


@pytest.mark.parametrize('scope', ['account', 'credential', 'interface', 'quota', 'operation', 'request'])
@pytest.mark.parametrize('outcome', ['failed', 'outcome_unknown'])
def test_new_block_does_not_protect_previously_confirmed_history(audit, scope, outcome):
    manager, ctx, clock = audit
    ctx = replace(ctx, semantics='write' if outcome == 'outcome_unknown' else 'read')
    old = manager.start(ctx, 'POST')
    manager.check(old, ctx, clock[0] + 30)
    manager.store.finish(old, result={'outcome': outcome})
    manager.store.block(ctx, RequestFailure('TEST_RESTRICTION', '上一轮故障', scope))
    clock[0] += 1
    manager.store.recover(ctx.platform, ctx.account_id, scope, manager.store.scope_key(ctx, scope), reason='已核对并处理上一轮故障')
    recoveries = manager.store.recoveries()
    clock[0] += 601
    before = int(clock[0])
    assert manager.store.clearable_interruptions(before=before) == 1

    # 打开清理确认框后出现同范围的新故障，也不能把已处理记录重新归入当前阻断。
    clock[0] += 1
    new = manager.start(ctx, 'POST')
    manager.check(new, ctx, clock[0] + 30)
    manager.store.finish(new, result={'outcome': outcome})
    manager.store.block(ctx, RequestFailure('TEST_RESTRICTION', '本轮新故障', scope))
    blocks = manager.store.blocks()
    clock[0] += 601
    # 重启后同样区分前后两轮；新故障即使超过十分钟仍须保留。
    store = ExternalRequestStore(manager.store.path)
    assert store.clearable_interruptions(before=int(clock[0])) == 1
    assert store.clear_interruptions(before=before) == 1
    assert {row['id'] for row in store.query()['items']} == {new}
    assert store.blocks() == blocks
    assert store.recoveries() == recoveries
    with pytest.raises(ExternalRequestBlocked):
        manager.check(manager.start(ctx, 'POST'), ctx, clock[0] + 30)


@pytest.mark.parametrize('scope', ['account', 'credential', 'interface', 'quota', 'operation', 'request'])
@pytest.mark.parametrize('outcome', ['outcome_unknown', 'network_error', 'interrupted'])
def test_manual_recovery_allows_clearing_old_unknown_write(audit, scope, outcome):
    manager, ctx, clock = audit
    ctx = replace(ctx, semantics='write')
    identity = manager.start(ctx, 'POST')
    manager.check(identity, ctx, clock[0] + 30)
    manager.store.finish(identity, result={'outcome': outcome, 'code': 'EXTERNAL_WRITE_OUTCOME_UNKNOWN'})
    manager.store.recover(ctx.platform, ctx.account_id, scope, manager.store.scope_key(ctx, scope), reason='已核对原业务回执并确认恢复')
    recoveries = manager.store.recoveries()
    clock[0] += 601
    control = ExternalRequestControlService(manager.store)
    snapshot = control.status()
    assert snapshot['clearable_count'] == 1
    assert control.clear_history(before=snapshot['cleanup_before'])['cleared_count'] == 1
    assert manager.store.recoveries() == recoveries


@pytest.mark.parametrize('mismatch', ['platform', 'account_id', 'interface', 'operation_id', 'earlier', 'after_snapshot', 'active_block', 'system'])
def test_recovery_must_match_scope_time_and_manual_confirmation(audit, mismatch):
    manager, ctx, clock = audit
    identity = manager.start(replace(ctx, semantics='write'), 'POST')
    manager.check(identity, ctx, clock[0] + 30)
    manager.store.finish(identity, result={'outcome': 'outcome_unknown'})
    recovered = replace(ctx, **{mismatch: 'another'}) if mismatch in {'platform', 'account_id', 'interface', 'operation_id'} else ctx
    if mismatch == 'earlier':
        clock[0] -= 1
    if mismatch == 'after_snapshot':
        clock[0] += 1000
    scope = 'request' if mismatch == 'operation_id' else 'interface'
    if mismatch == 'system':
        manager.store.block(ctx, RequestFailure('EXTERNAL_TRANSIENT_FAILURE', '等待探测', 'interface', resume_at=clock[0]+60))
        manager.store.request_probe(ctx.platform, ctx.account_id, {ctx.interface})
        # 移除阻断以单独验证：开始探测的审计不能充当人工确认。
        with manager.store._connect() as conn:
            conn.execute('DELETE FROM external_blocks')
            conn.commit()
    else:
        manager.store.recover(recovered.platform, recovered.account_id, scope, manager.store.scope_key(recovered, scope), reason='已确认恢复')
    if mismatch == 'active_block':
        manager.store.block(ctx, RequestFailure('TEST_RESTRICTION', '新的阻断', 'interface'))
    clock[0] = 1800001001.0
    assert manager.store.clear_interruptions(before=1800000900) == 0


def test_v1_recovery_migration_preserves_manual_confirmation_and_system_history(tmp_path):
    path = tmp_path / 'audit.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.executescript(EXTERNAL_REQUEST_SCHEMA_SQL.replace(", kind TEXT NOT NULL DEFAULT 'manual'", ''))
        conn.execute('PRAGMA user_version=1')
        conn.executemany('INSERT INTO external_recoveries VALUES(?,?,?,?,?,?,?)', [
            ('manual', 'ai:deepseek', 'shop', 'interface', '/chat', 1, '原请求为误报，已核实并恢复'),
            ('probe', 'ai:deepseek', 'shop', 'interface', '/chat', 2, '用户请求提前进行一次只读恢复检查'),
            ('auto', 'ai:deepseek', 'shop', 'interface', '/chat', 3, '冷却后的只读请求成功，自动恢复'),
        ])
    store = ExternalRequestStore(path)
    assert {r['id']: r['kind'] for r in store.recoveries()} == {'manual': 'manual', 'probe': 'system', 'auto': 'system'}
    assert ExternalRequestStore(path).recoveries() == store.recoveries()
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 2


@pytest.mark.parametrize('before', [True, 0, -1, 'all', float('nan'), float('inf')])
def test_store_rejects_invalid_cleanup_boundary(audit, before):
    manager, _, _ = audit
    with pytest.raises(ValueError):
        manager.store.clear_interruptions(before=before)


def test_http_cleanup_validates_body_origin_and_returns_count(audit):
    manager, ctx, clock = audit
    record(manager, ctx, status=503)
    clock[0] += 601
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        url = base + '/api/external-requests/history/clear'
        snapshot = requests.get(base + '/api/external-requests/status', timeout=3).json()
        assert snapshot['clearable_count'] == 1
        payload = {'before': snapshot['cleanup_before']}
        assert requests.post(url, json=payload, headers={'Origin': 'https://untrusted.example'}, timeout=3).status_code == 403
        for invalid in ({}, {'before': True}, {'before': -1}, {'before': None}):
            assert requests.post(url, json=invalid, headers={'Origin': base}, timeout=3).status_code == 400
        response = requests.post(url, json=payload, headers={'Origin': base}, timeout=3)
        assert response.status_code == 200 and response.json()['cleared_count'] == 1
        assert manager.store.query()['stats']['attempts'] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
