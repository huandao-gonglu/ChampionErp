"""万邦个人买家授权、身份验证和旧凭据读取。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.parse import parse_qs, urlsplit

import pytest

from erp_web.context import get_context
from erp_web.services import alibaba_api_client as official, onebound_api_transport as transport
from erp_web.services.alibaba_api_client import alibaba_provider, AlibabaApiError
from erp_web.services.alibaba_authorization_service import AlibabaAuthorizationService
from erp_web.services.alibaba_credentials import application_fingerprint
from tests.test_onebound_api import configure, response


CALLBACK = 'https://dc.dev.1buyo.com/api/Accredit1688/callback_1688?state=vendor-key&code='


@pytest.mark.parametrize('default_binding', [False, True])
def test_enterprise_key_cannot_become_personal_authorization(monkeypatch, default_binding):
    store = get_context().config
    configure(store)
    if default_binding:
        store.update_alibaba_credentials({'authorization': {
            'authentication_mode': 'key_default', 'member_id': 'admin-buyer', 'account_scope': 'old-scope',
            'application_fingerprint': application_fingerprint(store.alibaba_application()),
        }})
    before = store.alibaba_credentials()
    def unexpected(*args, **kwargs):
        pytest.fail('缺少个人授权时不得访问万邦默认买家或官方接口')
    monkeypatch.setattr(transport, 'managed_urlopen', unexpected)
    monkeypatch.setattr(official, 'managed_urlopen', unexpected)
    service = AlibabaAuthorizationService(store)
    assert not service.status()['authorized']
    assert service.status()['can_authorize']
    assert service.start()['url'] == 'https://o0b.cn/9vXze?state=vendor-key'
    assert service.status()['attempt'] == 'waiting'
    assert not service.status()['authorized']
    with pytest.raises(ValueError, match='尚未连接'):
        service.runtime_config()
    assert store.alibaba_credentials().get('authorization') == before.get('authorization')


@pytest.mark.parametrize('failure, message', [
    ('exchange', '核对换码接口权限'),
    ('expiry', '买家授权已到期'),
    ('identity', '买家身份不一致'),
    ('missing_identity', '买家身份不一致'),
])
def test_onebound_failure_keeps_current_buyer_and_consumes_attempt(monkeypatch, failure, message):
    store = get_context().config
    configure(store)
    application = store.alibaba_application()
    auth = {'access_token': 'old-token', 'application_fingerprint': application_fingerprint(application),
            'account_scope': 'old-scope'}
    store.update_alibaba_credentials({'authorization': auth})
    calls = []

    def gateway(request, **kwargs):
        path = urlsplit(request.full_url).path
        calls.append(path)
        if path == '/1688global/buyer_token_create/':
            if failure == 'exchange':
                return response({'error_code': '4005', 'error': 'secret-buyer-token secret-code'})
            expires = '2000-01-01 00:00:00' if failure == 'expiry' else '2099-01-01 00:00:00'
            return response({'items': {'session': 'secret-buyer-token', 'nick': '新买家', 'exp_time': expires}})
        identity = {'memberId': 'other-buyer', 'loginId': '默认买家'} if failure == 'identity' else {'loginId': '新买家', 'memberId': ''}
        return response({'response': identity})

    monkeypatch.setattr(transport, 'managed_urlopen', gateway)
    service = AlibabaAuthorizationService(store)
    service.start()
    with pytest.raises(ValueError, match=message) as error:
        service.complete_from_url(CALLBACK + 'secret-code', 'onebound')
    assert 'secret-' not in str(error.value)
    assert service.status()['attempt'] == 'failed' and service.status()['authorized']
    assert store.alibaba_credentials()['authorization'] == auth
    with pytest.raises(ValueError, match='请求已失效'):
        service.complete_from_url(CALLBACK + 'secret-code', 'onebound')
    assert calls.count('/1688global/buyer_token_create/') == 1
    assert ('/1688/custom' in calls) == (failure in {'identity', 'missing_identity'})


def test_onebound_expired_token_requires_reauthorization_and_renewal_keeps_buyer_scope(monkeypatch):
    store = get_context().config
    configure(store)
    store.update_alibaba_credentials({'authorization': {
        'access_token': 'expired-token', 'credential_type': 'access_token', 'member_id': 'buyer-a',
        'expires_at': 1, 'account_scope': 'existing-scope',
        'application_fingerprint': application_fingerprint(store.alibaba_application()),
    }})
    service = AlibabaAuthorizationService(store)
    assert service.status()['needs_reauthorization'] and not service.status()['authorized']
    assert service.onebound_binding()['state'] == 'expired'
    with pytest.raises(ValueError, match='已到期'):
        service.runtime_config()
    monkeypatch.setattr('erp_web.services.alibaba_authorization_service.exchange_onebound_buyer_code',
                        lambda app, code: ('renewed-token', '买家甲', 4_000_000_000))
    monkeypatch.setattr(transport, 'managed_urlopen', lambda *args, **kwargs: response({
        'response': {'memberId': 'buyer-a', 'loginId': '买家甲'}}))
    service.start()
    status = service.complete_from_url(CALLBACK + 'new-code', 'onebound')
    assert status['authorized'] and not status['needs_reauthorization'] and not status['refreshable']
    assert service.runtime_config()['account_scope'] == 'existing-scope'
    assert service.runtime_config()['access_token'] == 'renewed-token'
    assert service.onebound_binding()['state'] == 'connected'


def test_concurrent_onebound_completion_cannot_replay_or_overwrite_a_new_attempt(monkeypatch):
    store = get_context().config
    configure(store)
    service = AlibabaAuthorizationService(store)
    started, release = Event(), Event()

    def exchange(app, code):
        if code == 'original':
            started.set()
            assert release.wait(5)
        return (code + '-token', '买家甲', 4_000_000_000)

    monkeypatch.setattr('erp_web.services.alibaba_authorization_service.exchange_onebound_buyer_code', exchange)
    monkeypatch.setattr(transport, 'managed_urlopen', lambda *args, **kwargs: response({
        'response': {'memberId': 'buyer-a', 'loginId': '买家甲'}}))
    service.start()
    with ThreadPoolExecutor(max_workers=1) as executor:
        original = executor.submit(service.complete_from_url, CALLBACK + 'original', 'onebound')
        try:
            assert started.wait(5)
            with pytest.raises(ValueError, match='请求已失效'):
                service.complete_from_url(CALLBACK + 'original', 'onebound')
            service.start()
            assert service.complete_from_url(CALLBACK + 'new', 'onebound')['authorized']
        finally:
            release.set()
        with pytest.raises(ValueError, match='授权已变化'):
            original.result(timeout=5)
    assert service.runtime_config()['access_token'] == 'new-token'


def test_existing_official_token_is_not_assumed_to_be_a_onebound_grant():
    store = get_context().config
    configure(store)
    native = {'authorization': {'access_token': 'official-token', 'member_id': 'buyer-a'}}
    store._db.set_runtime_secret('credentials:alibaba', 'state', native)
    with pytest.raises(ValueError, match='尚未连接'):
        AlibabaAuthorizationService(store).runtime_config()
    assert store.alibaba_credentials() == {}
    assert store._db.runtime_secret('credentials:alibaba', 'state', {}) == native


def test_previously_saved_personal_onebound_credential_remains_readable():
    store = get_context().config
    configure(store)
    store.update_alibaba_credentials({'authorization': {
        'access_token': 'saved-personal-session', 'account_scope': 'existing-scope',
        'application_fingerprint': application_fingerprint(store.alibaba_application()),
    }})
    service = AlibabaAuthorizationService(store)
    assert service.status()['authorized']
    config = service.runtime_config()
    assert config['access_token'] == 'saved-personal-session'
    assert config['account_scope'] == 'existing-scope'
    assert config['credential_type'] == 'session'


def test_business_client_requires_personal_credential_even_with_enterprise_key():
    with pytest.raises(AlibabaApiError, match='尚未连接'):
        alibaba_provider({'application_type': 'onebound', 'api_key': 'vendor-key', 'api_secret': 'vendor-secret'})


@pytest.mark.parametrize('field', ['access_token', 'session'])
def test_binding_and_switching_preserve_wire_credential(monkeypatch, field):
    import json
    from urllib.parse import parse_qs, urlsplit
    from erp_web.services.alibaba_api_client import alibaba_provider, ORDER_LIST
    from tests.test_onebound_api import response

    store = get_context().config
    configure(store)
    service = AlibabaAuthorizationService(store)
    sent = []

    def send(request, **kwargs):
        params = parse_qs(urlsplit(request.full_url).query)
        sent.append(params)
        if params['method'] == ['com.alibaba.trade/' + ORDER_LIST]:
            return response({'response': {'result': [], 'totalRecord': 0}})
        return response({'response': {'receiveAddressItems': []}})

    monkeypatch.setattr(transport, 'managed_urlopen', send)
    token = 'buyer-a+&=买家'
    saved = store._db.runtime_secret('enterprise_applications', 'active', {})
    store._db.set_runtime_secret('enterprise_applications', 'active', {**saved, 'alibaba_api_application': 'alibaba_crossborder'})
    status = service.bind_onebound({field: token})
    assert status['authorized'] and not status['active'] and token not in repr(status)
    assert not service.status()['authorized']
    store._db.set_runtime_secret('enterprise_applications', 'active', saved)

    client = alibaba_provider(service.runtime_config())
    assert client.find_order_numbers('external-id') == []
    for params in sent:
        assert params['key'] == ['vendor-key'] and params['secret'] == ['vendor-secret']
        assert params.get('session') == ([token] if field == 'session' else None)
        assert params.get('access_token') == ([token] if field == 'access_token' else None)
        assert 'access_token' not in json.loads(params['_o_args'][0])
    assert len(sent) == 2


@pytest.mark.parametrize('body', [{}, {'session': 'a', 'access_token': 'b'}, {'access_token': ' '}])
def test_invalid_binding_keeps_current_grant(body):
    store = get_context().config
    configure(store)
    store.update_alibaba_credentials({'authorization': {
        'access_token': 'original-token', 'credential_type': 'access_token', 'account_scope': 'original-scope',
        'application_fingerprint': application_fingerprint(store.alibaba_application()),
    }})
    before = store.alibaba_credentials()
    with pytest.raises(ValueError, match='一项有效的万邦买家凭据'):
        AlibabaAuthorizationService(store).bind_onebound(body)
    assert store.alibaba_credentials() == before
