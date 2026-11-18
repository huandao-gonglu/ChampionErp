"""企业密钥不代替用户授权；未接入的在线授权不得伪装成功。"""
import pytest

from erp_web.context import get_context
from erp_web.services import alibaba_api_client as official, onebound_api_transport as transport
from erp_web.services.alibaba_api_client import alibaba_provider, AlibabaApiError
from erp_web.services.alibaba_authorization_service import AlibabaAuthorizationService
from erp_web.services.alibaba_credentials import application_fingerprint
from tests.test_onebound_api import configure


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
    assert not service.status()['can_authorize']
    with pytest.raises(ValueError, match='在线授权尚未接入'):
        service.start()
    with pytest.raises(ValueError, match='尚未连接'):
        service.runtime_config()
    assert store.alibaba_credentials() == before


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
        order_list = params['method'] == ['com.alibaba.trade/' + ORDER_LIST]
        assert params.get('access_token') == ([token] if field == 'access_token' and order_list else None)
        assert json.loads(params['_o_args'][0]).get('access_token') == (token if field == 'access_token' and not order_list else None)
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
