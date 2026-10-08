"""授权页统一中断入口及用户动作关联，不泄漏原始请求或凭据。"""
import json
import threading
import time
from dataclasses import replace
from http.server import ThreadingHTTPServer

import pytest
import requests

from erp_web.context import get_context
from erp_web.http_handler import Handler
from erp_web.schemas.external_requests import RequestContext, RequestFailure, ExternalRequestBlocked
from erp_web.services.external_request_control_service import ExternalRequestControlService, block_id


def context(**patch):
    return RequestContext(**dict(platform='yandex', account_id='business-1', interface='/orders', source='orders', semantics='read', **patch))


def test_control_includes_background_and_manual_failures_and_no_secrets():
    manager = get_context().external_requests
    for trigger in ('manual', 'background'):
        ctx = context(trigger=trigger)
        identity = manager.start(ctx, 'POST')
        manager.check(identity, ctx, time.time()+30)
        manager.network_error(identity, ctx)
    manager.store.block(context(), RequestFailure('YANDEX_AUTH_FAILED', '平台授权失效', 'credential'))
    status = ExternalRequestControlService(manager.store).status()
    assert {r['trigger'] for r in status['history']} == {'manual', 'background'}
    assert status['total'] == 2
    assert status['blocks'][0]['recovery_mode'] == 'confirm'
    assert 'credential_id' not in json.dumps(status)
    assert 'result' not in status['history'][0]


def test_control_recovery_is_versioned_and_does_not_send_or_replay():
    manager = get_context().external_requests
    control = ExternalRequestControlService(manager.store)
    ctx = context()
    manager.store.block(ctx, RequestFailure('EXTERNAL_TRANSIENT_FAILURE', '临时故障', 'interface', resume_at=time.time()+60, cooldown_seconds=60))
    identity = control.status()['blocks'][0]['id']
    result = control.recover(identity)
    assert result['ok']
    assert manager.store.query()['stats']['network_attempts'] == 0
    with pytest.raises(ValueError, match='状态已变化'):
        control.recover(identity)
    manager.store.block(ctx, RequestFailure('YANDEX_ACCOUNT_DISABLED', '账号停用', 'account'))
    block = next(b for b in manager.store.blocks() if b['scope']=='account')
    with pytest.raises(ValueError, match='原因'):
        control.recover(block_id(block))
    control.recover(block_id(block), reason='平台已确认恢复账号')
    assert all(b['scope'] != 'account' for b in manager.store.blocks())


def test_unknown_write_and_platform_retry_after_cannot_be_replayed():
    manager = get_context().external_requests
    control = ExternalRequestControlService(manager.store)
    ctx = context()
    for failure in (
        RequestFailure('EXTERNAL_WRITE_OUTCOME_UNKNOWN', '写入未知', 'request'),
        RequestFailure('YANDEX_RATE_LIMITED', '限流', 'account', resume_at=time.time()+60),
    ):
        manager.store.block(ctx, failure)
    for block in control.status()['blocks']:
        with pytest.raises(ValueError):
            control.recover(block['id'], reason='用户点击恢复')
    assert manager.store.query()['stats']['network_attempts'] == 0


def test_ai_definite_rejection_only_blocks_original_request_and_can_be_recovered():
    manager = get_context().external_requests
    control = ExternalRequestControlService(manager.store)
    ctx = RequestContext(platform='ai:deepseek', account_id='ai-account', interface='/chat/completions', source='ai', semantics='write', fingerprint='same-body')
    identity = manager.start(ctx, 'POST')
    manager.check(identity, ctx, time.time() + 30)
    manager.result(identity, ctx, 402, {}, b'{}')
    original = control.status()['blocks'][0]
    assert original['recovery_mode'] == 'confirm_request'
    assert original['scope'] == 'request' and original['http_status'] == 402
    with pytest.raises(ExternalRequestBlocked):
        manager.check(manager.start(ctx, 'POST'), ctx, time.time() + 30)
    other = replace(ctx, operation_id='new-operation')
    other_id = manager.start(other, 'POST')
    assert manager.check(other_id, other, time.time() + 30) == 0
    manager.stream_closed(other_id, 200)
    before = manager.store.query()['stats']['network_attempts']
    history_before = control.status()['total']
    with pytest.raises(ValueError, match='原因'):
        control.recover(original['id'])
    control.recover(original['id'], reason='拒绝原因已处理，后续请求已正常')
    assert control.status()['blocks'] == []
    assert control.status()['total'] == history_before
    assert manager.store.query()['stats']['network_attempts'] == before
    assert manager.store.recoveries()[0]['reason'] == '拒绝原因已处理，后续请求已正常'


@pytest.mark.parametrize('scope', ['request', 'operation', 'interface'])
def test_store_recovery_cannot_bypass_unknown_write_protection(scope):
    store = get_context().external_requests.store
    store.block(context(), RequestFailure('EXTERNAL_WRITE_OUTCOME_UNKNOWN', '写入结果未知', scope))
    with pytest.raises(ValueError):
        store.recover_confirmed(store.blocks(), reason='直接调用恢复')
    assert len(store.blocks()) == 1


def test_recovery_rejects_recreated_block_with_identical_failure():
    store = get_context().external_requests.store
    ctx = context()
    failure = RequestFailure('YANDEX_REQUEST_INVALID', '请求被明确拒绝', 'request', status=400)
    store.block(ctx, failure)
    old = store.blocks()[0]
    store.recover_confirmed([old], reason='已修正参数')
    store.block(ctx, failure)
    with pytest.raises(ValueError, match='状态已变化'):
        store.recover_confirmed([old], reason='旧页面重复恢复')
    assert len(store.blocks()) == 1


def record_rejection(manager, ctx, status=200):
    identity = manager.start(ctx, 'POST')
    manager.check(identity, ctx, time.time() + 30)
    manager.result(identity, ctx, status, {}, b'{"success":false}')


def test_same_rejections_group_for_display_and_recover_with_individual_audit():
    manager = get_context().external_requests
    control = ExternalRequestControlService(manager.store)
    first = context(operation_id='orders:1', fingerprint='first')
    second = replace(first, operation_id='orders:2', fingerprint='second')
    for ctx in (first, second):
        record_rejection(manager, ctx)
    with pytest.raises(ExternalRequestBlocked):
        manager.check(manager.start(first, 'POST'), first, time.time() + 30)
    status = control.status()
    assert len(status['blocks']) == 1
    group = status['blocks'][0]
    assert group['count'] == 2 and group['interface'] == '/orders'
    assert group['blocked_count'] == 1
    assert group['created_at'] < group['last_created_at']
    assert len(group['occurrences']) == 2
    assert len(manager.store.blocks()) == 2  # 展示合并不改变真实放行范围。
    attempts = manager.store.query()['stats']['network_attempts']
    control.recover(group['id'], reason='已处理这两次请求的拒绝原因')
    assert manager.store.blocks() == []
    assert len(manager.store.recoveries()) == 2
    assert control.status()['total'] == status['total']
    assert manager.store.query()['stats']['network_attempts'] == attempts


@pytest.mark.parametrize('patch', [
    {'account_id': 'another-account'}, {'platform': 'ozon'}, {'interface': '/another'},
])
def test_rejection_groups_do_not_cross_account_platform_or_interface(patch):
    manager = get_context().external_requests
    ctx = context()
    record_rejection(manager, ctx)
    record_rejection(manager, replace(ctx, operation_id='different', **patch))
    assert len(ExternalRequestControlService(manager.store).status()['blocks']) == 2


def test_new_member_invalidates_group_confirmation():
    manager = get_context().external_requests
    control = ExternalRequestControlService(manager.store)
    for operation in ('one', 'two'):
        record_rejection(manager, context(operation_id=operation))
    old_id = control.status()['blocks'][0]['id']
    record_rejection(manager, context(operation_id='three'))
    with pytest.raises(ValueError, match='状态已变化'):
        control.recover(old_id, reason='旧确认框')
    assert len(manager.store.blocks()) == 3
    assert manager.store.recoveries() == []


def test_group_recovery_rolls_back_if_one_member_changes(monkeypatch):
    manager = get_context().external_requests
    control = ExternalRequestControlService(manager.store)
    contexts = [context(operation_id=operation) for operation in ('one', 'two')]
    for ctx in contexts:
        record_rejection(manager, ctx)
    identity = control.status()['blocks'][0]['id']
    original = manager.store.recover_confirmed
    def concurrent_change(blocks, *, reason):
        manager.store.block(contexts[-1], RequestFailure('YANDEX_REQUEST_INVALID', '新拒绝', 'request', status=400))
        original(blocks, reason=reason)
    monkeypatch.setattr(manager.store, 'recover_confirmed', concurrent_change)
    with pytest.raises(ValueError, match='状态已变化'):
        control.recover(identity, reason='仅确认旧状态')
    assert len(manager.store.blocks()) == 2
    assert manager.store.recoveries() == []


def test_unknown_or_untraceable_requests_are_not_grouped():
    store = get_context().external_requests.store
    for operation in ('one', 'two'):
        store.block(context(operation_id=operation), RequestFailure('YANDEX_REQUEST_INVALID', '明确拒绝', 'request'))
        store.block(context(operation_id='unknown-' + operation), RequestFailure('EXTERNAL_WRITE_OUTCOME_UNKNOWN', '结果未知', 'request'))
    assert len(ExternalRequestControlService(store).status()['blocks']) == 4


def test_notices_are_limited_to_observed_user_jobs():
    manager = get_context().external_requests
    manager.store.block(context(), RequestFailure('EXTERNAL_TRANSIENT_FAILURE', '冷却中', 'interface', resume_at=time.time()+60, cooldown_seconds=60))
    for operation in ('user-job', 'scheduled-job'):
        ctx = context(operation_id=operation)
        with pytest.raises(ExternalRequestBlocked):
            manager.check(manager.start(ctx,'POST'),ctx,time.time()+30)
    notices = ExternalRequestControlService(manager.store).status(operation_ids=['user-job'])['notices']
    assert len(notices) == 1 and notices[0]['operation_id'] == 'user-job'


def test_http_block_notice_survives_domain_error_rewriting(monkeypatch):
    from erp_web.facades import auth_config_facade
    manager = get_context().external_requests
    manager.store.block(context(), RequestFailure('EXTERNAL_TRANSIENT_FAILURE','冷却中','interface',resume_at=time.time()+60,cooldown_seconds=60))
    def action(*args, **kwargs):
        ctx = context()
        try:
            manager.check(manager.start(ctx,'POST'),ctx,time.time()+30)
        except ExternalRequestBlocked:
            return {'ok':False,'error':'领域层的通用错误'}, 200
    monkeypatch.setattr(auth_config_facade, 'test_store_auth_payload', action)
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        response = requests.post(base+'/api/test-store-auth',json={'platform':'yandex'},headers={'Origin':base})
        assert response.status_code == 200
        assert response.headers['X-External-Request-Blocked'] == '1'
        assert response.headers['X-External-Operation-Ids']
        response = requests.get(base+'/api/external-requests/status')
        assert response.status_code == 200
        assert 'X-External-Request-Blocked' not in response.headers
        assert response.json()['blocks'][0]['recovery_mode'] == 'probe'
        denied = requests.post(base+'/api/external-requests/recover',json={'block_id':response.json()['blocks'][0]['id']},headers={'Origin':'https://untrusted.example'})
        assert denied.status_code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
