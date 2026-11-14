"""企业密钥不代替用户授权；未接入的在线授权不得伪装成功。"""
import pytest

from erp_web.context import get_context
from erp_web.services import alibaba_api_client as official, onebound_api_transport as transport
from erp_web.services.alibaba_api_client import AlibabaApiClient, AlibabaApiError
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


def test_business_client_requires_personal_credential_even_with_enterprise_key():
    with pytest.raises(AlibabaApiError, match='尚未连接'):
        AlibabaApiClient({'application_type': 'onebound', 'api_key': 'vendor-key', 'api_secret': 'vendor-secret'})
