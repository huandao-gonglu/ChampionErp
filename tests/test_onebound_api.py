"""万邦适配只用替身：协议、方案隔离、管理员权限和未知写入防重。"""
from copy import deepcopy
import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from erp_web.context import get_context
from erp_web.services import onebound_api_transport as transport
from erp_web.services.alibaba_api_client import AlibabaApiClient, AlibabaApiError, AlibabaApiRejected, ORDER_DETAIL, LOGISTICS_INFO
from erp_web.services.alibaba_authorization_service import AlibabaAuthorizationService
from erp_web.services.alibaba_self_purchase_client import AlibabaSelfPurchaseClient, ADDRESSES, PREVIEW, CREATE, ORDER_LIST, PAY_URL, PAY_STATUS, PAY
from erp_web.services.alibaba_self_purchase_service import account_scope
from erp_web.services.alibaba_credentials import application_fingerprint
from tests.test_accounts import accounts_http, register
from tests.test_managed_settings import settings_body, post_settings


CONFIG = {'application_type': 'onebound', 'api_key': 'vendor-key', 'api_secret': 'vendor-secret', 'access_token': 'buyer-session'}


def response(value):
    return io.BytesIO(json.dumps(value).encode())


@pytest.mark.parametrize('api', [ADDRESSES, PREVIEW, CREATE, ORDER_LIST, PAY_URL, PAY_STATUS, PAY])
def test_custom_transport_preserves_arguments_and_write_semantics(monkeypatch, api):
    calls = []
    def send(request, **kwargs):
        calls.append((request, kwargs))
        return response({'response': {'success': True, 'result': {}}})
    monkeypatch.setattr(transport, 'managed_urlopen', send)
    values = {'orderIds': [123]} if api == PAY_URL else {'cargoParamList': [{'offerId': 123, 'specId': '蓝色', 'quantity': 2}]}
    AlibabaSelfPurchaseClient(CONFIG).call(api, values)
    assert len(calls) == 1
    request, kwargs = calls[0]
    parsed = urlsplit(request.full_url)
    params = parse_qs(parsed.query)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ('https', 'api-gw.onebound.cn', '/1688/custom')
    assert params['key'] == ['vendor-key'] and params['secret'] == ['vendor-secret'] and params['session'] == ['buyer-session']
    method = 'alibaba.alipay.url.get' if api == PAY_URL else api
    assert params['method'] == ['com.alibaba.trade/' + method]
    assert json.loads(params['_o_args'][0]) == ({'orderIdList': [123]} if api == PAY_URL else values)
    assert params['cache'] == ['no'] and '_aop_signature' not in params
    context = kwargs['request_context']
    assert context.semantics == ('write' if api in {CREATE, PAY} else 'read')
    assert context.max_attempts == 1 and not kwargs['follow_redirects']
    assert not any(secret in repr(context) for secret in ('vendor-key', 'vendor-secret', 'buyer-session'))


def test_product_order_and_logistics_use_same_explicit_provider(monkeypatch):
    calls = []
    def send(request, **kwargs):
        params = parse_qs(urlsplit(request.full_url).query)
        calls.append(params['method'][0])
        if 'queryProductDetail' in calls[-1]:
            return response({'response': {'result': {'success': True, 'result': {'offerId': 123}}}})
        return response({'response': {'success': True, 'result': []}})
    monkeypatch.setattr(transport, 'managed_urlopen', send)
    client = AlibabaApiClient(CONFIG)
    assert client.product_detail('123')['offerId'] == 123
    client.query(ORDER_DETAIL, '123')
    client.query(LOGISTICS_INFO, '123')
    assert calls == ['com.alibaba.fenxiao.crossborder/product.search.queryProductDetail',
                     'com.alibaba.trade/' + ORDER_DETAIL, 'com.alibaba.logistics/' + LOGISTICS_INFO]


@pytest.mark.parametrize('code,rejected', [('4005', True), ('4016', True), ('4001', False), ('4017', False), ('5000', False)])
def test_vendor_failures_are_safe_and_do_not_claim_network_writes_failed(monkeypatch, code, rejected):
    monkeypatch.setattr(transport, 'managed_urlopen', lambda *a, **k: response({
        'error_code': code, 'error': 'vendor-secret buyer-session', 'call_args': CONFIG}))
    with pytest.raises(AlibabaApiError) as error:
        AlibabaSelfPurchaseClient(CONFIG).call(CREATE, {})
    assert isinstance(error.value, AlibabaApiRejected) is rejected
    assert 'vendor-secret' not in str(error.value) and 'buyer-session' not in str(error.value)


def configure(store):
    public = store.managed_settings()
    body = {key: public[key] for key in ('revision', 'exchange_rates')}
    body.update(alibaba_crossborder={'app_key': '', 'redirect_uri': ''}, alibaba_api_application='onebound',
                onebound={'api_key': CONFIG['api_key'], 'api_secret': CONFIG['api_secret']})
    return store.save_managed_settings(body)


def test_bind_is_read_only_keeps_separate_scope_and_rejects_late_switch(monkeypatch):
    store = get_context().config
    public = configure(store)
    assert public['onebound'] == {'api_key': 'vendor-key', 'secret_configured': True}
    assert 'vendor-secret' not in json.dumps(public)
    calls = []
    def send(request, **kwargs):
        calls.append(kwargs['request_context'].semantics)
        return response({'response': {'success': True, 'result': {'receiveAddressItems': []}}})
    monkeypatch.setattr(transport, 'managed_urlopen', send)
    service = AlibabaAuthorizationService(store)
    assert not service.status()['can_authorize']
    assert not service.status()['authorized']
    with pytest.raises(ValueError, match='在线授权暂未开放'):
        service.start()
    assert service.bind_onebound({'session': 'buyer-session'})['authorized']
    first = deepcopy(store.alibaba_credentials())
    scope = account_scope(service.runtime_config())
    assert service.bind_onebound({'session': 'buyer-session'})['authorized']
    assert account_scope(service.runtime_config()) == scope
    service.bind_onebound({'session': 'different-buyer-session'})
    assert account_scope(service.runtime_config()) != scope
    assert calls == ['read'] * 3
    def late(request, **kwargs):
        saved = store._db.runtime_secret('enterprise_applications', 'active', {})
        store._db.set_runtime_secret('enterprise_applications', 'active', {**saved, 'alibaba_api_application': 'alibaba'})
        return response({'response': {'success': True, 'result': {'receiveAddressItems': []}}})
    monkeypatch.setattr(transport, 'managed_urlopen', late)
    with pytest.raises(ValueError, match='已变化'):
        service.bind_onebound({'session': 'late-session'})
    assert not store.alibaba_credentials().get('authorization')
    assert store._db.runtime_secret('credentials:alibaba', 'onebound', {})['authorization']['access_token'] == 'different-buyer-session'
    assert first['authorization']['access_token'] == 'buyer-session'


def test_admin_configuration_and_binding_are_hidden_from_other_users(accounts_http, monkeypatch):
    url, runtime, login = accounts_http
    admin = login()
    user = register(url)
    client = login('user@example.com')
    body = settings_body(admin, url)
    body.update(alibaba_api_application='onebound', onebound={'api_key': 'vendor-key', 'api_secret': 'vendor-secret'})
    post_settings(admin, url, body)
    monkeypatch.setattr(transport, 'managed_urlopen', lambda *a, **k: response({
        'response': {'success': True, 'result': {'receiveAddressItems': []}}}))
    path = url + '/api/alibaba/authorization/onebound'
    assert client.get(url + '/api/admin/settings').status_code == 403
    assert client.post(path, json={'session': 'user-session'}).status_code == 403
    bound = admin.post(path, json={'session': 'admin-session'})
    assert bound.status_code == 200 and bound.json()['authorized']
    assert not any(secret in bound.text for secret in ('vendor-secret', 'vendor-key', 'admin-session'))
    user_status = client.get(url + '/api/alibaba/authorization').json()
    assert user_status['application_type'] == 'onebound' and not user_status['authorized']
    assert not user_status['can_authorize']
    context = runtime.context_for(next(row for row in runtime.store.users() if row['id'] == user['id']))
    assert context.config.alibaba_credentials() == {}
    invalid = admin.post(path, json={'session': 'private-session-' * 400})
    assert invalid.status_code == 400 and 'private-session-' not in invalid.text
    # 省略新段的旧客户端不能清空万邦凭据或改变当前选择。
    body = settings_body(admin, url)
    body.pop('onebound'); body.pop('alibaba_api_application')
    assert post_settings(admin, url, body)['alibaba_api_application'] == 'onebound'
    for secret in ('rotated-secret', 'vendor-secret'):
        body = settings_body(admin, url)
        body['onebound']['api_secret'] = secret
        post_settings(admin, url, body)
        assert not admin.get(url + '/api/alibaba/authorization').json()['authorized']


def test_three_scheme_switch_restores_each_grant_without_scope_transfer(monkeypatch):
    store = get_context().config
    configure(store)
    saved = store._db.runtime_secret('enterprise_applications', 'active', {})
    saved.update({provider: {'app_key': key, 'app_secret': provider + '-secret',
                            'redirect_uri': 'https://erp.example/auth/alibaba/callback'}
                  for provider, key in [('alibaba', '1938727'), ('alibaba_crossborder', '4848173')]})
    store._db.set_runtime_secret('enterprise_applications', 'active', saved)
    monkeypatch.setattr(transport, 'managed_urlopen', lambda *a, **k: response({
        'response': {'success': True, 'result': {'receiveAddressItems': []}}}))
    service = AlibabaAuthorizationService(store)
    service.bind_onebound({'session': 'buyer-session'})
    grants = {'onebound': deepcopy(store.alibaba_credentials())}
    def select(provider):
        public = store.managed_settings()
        store.save_managed_settings({'revision': public['revision'], 'exchange_rates': public['exchange_rates'],
            'alibaba_api_application': provider,
            'alibaba_crossborder': {key: public['alibaba_crossborder'][key] for key in ('app_key', 'redirect_uri')}})
    for provider in ('alibaba', 'alibaba_crossborder'):
        select(provider)
        application = store.alibaba_application()
        grants[provider] = store.update_alibaba_credentials({'authorization': {
            'access_token': provider + '-token', 'account_scope': provider + '-scope',
            'application_fingerprint': application_fingerprint(application)}})
    scopes = set()
    for provider in ('onebound', 'alibaba', 'alibaba_crossborder', 'onebound'):
        select(provider)
        assert service.status()['authorized']
        assert store.alibaba_credentials() == grants[provider]
        scopes.add(account_scope(service.runtime_config()))
    assert len(scopes) == 3
