"""应用与买家授权分离、旧授权迁移和 OAuth 生命周期回归。"""
import hashlib
import hmac
import io
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from erp_web.context import get_context
from erp_web.http_request import validate_request_metadata
from erp_web.schemas.requests import RequestValidationError
from erp_web.services import alibaba_authorization_service as oauth
from erp_web.services.alibaba_credentials import legacy_account_scope
from erp_web.services.alibaba_self_purchase_service import account_scope
from erp_web.services.config_service import public_app_config, public_ai_config, merge_ai_config

LEGACY = {'app_key': '12345', 'app_secret': 'old-app-secret', 'access_token': 'old-token'}


@pytest.fixture
def service(monkeypatch):
    for key in ('ERP_ALIBABA_APP_KEY', 'ERP_ALIBABA_APP_SECRET', 'ERP_ALIBABA_REDIRECT_URI'):
        monkeypatch.delenv(key, raising=False)
    return oauth.AlibabaAuthorizationService(get_context().config)


def save_legacy():
    store = get_context().config
    value = store.load_app_config()
    value['1688_api'] = dict(LEGACY)
    store.save_app_config(value)


def configure(monkeypatch):
    monkeypatch.setenv('ERP_ALIBABA_APP_KEY', '12345')
    monkeypatch.setenv('ERP_ALIBABA_APP_SECRET', 'enterprise-secret')
    monkeypatch.setenv('ERP_ALIBABA_REDIRECT_URI', 'https://callback.example.test/1688')


def callback(service, **changes):
    params = parse_qs(urlsplit(service.start()['url']).query)
    return params['redirect_uri'][0] + '?' + urlencode({'code': 'one-use-code', 'state': params['state'][0], **changes})


def token(member='buyer-a', **extra):
    return {'access_token': 'buyer-token', 'refresh_token': 'refresh-1', 'expires_in': 36000, 'memberId': member, **extra}


def test_legacy_migration_preserves_purchase_scope_and_cleans_old_location(service):
    save_legacy()
    config = service.runtime_config()
    assert {key: config[key] for key in LEGACY} == LEGACY
    assert account_scope(config) == legacy_account_scope(LEGACY)
    state = service.store.alibaba_credentials()
    assert state['application'] == {k: LEGACY[k] for k in ('app_key', 'app_secret')}
    assert 'app_secret' not in state['authorization']
    assert service.status()['authorized'] and not service.status()['refreshable']
    assert service.store.load_app_config()['1688_api']['access_token'] == ''
    assert oauth.AlibabaAuthorizationService(service.store).runtime_config() == config


def test_matching_environment_preserves_migration_but_new_app_never_uses_old_token(service, monkeypatch):
    save_legacy()
    monkeypatch.setenv('ERP_ALIBABA_APP_KEY', LEGACY['app_key'])
    monkeypatch.setenv('ERP_ALIBABA_APP_SECRET', LEGACY['app_secret'])
    assert service.runtime_config()['access_token'] == LEGACY['access_token']
    monkeypatch.setenv('ERP_ALIBABA_APP_SECRET', 'new-enterprise-secret')
    assert not service.runtime_config()['access_token']
    assert service.status()['needs_reauthorization']
    assert 'new-enterprise-secret' not in json.dumps(service.store.alibaba_credentials())
    monkeypatch.delenv('ERP_ALIBABA_APP_SECRET')
    assert not service.status()['application_configured']
    assert not service.runtime_config()['app_secret']


def test_public_settings_cannot_read_or_replace_application_and_user_credentials(service):
    save_legacy()
    current = service.store.load_app_config()
    incoming = {'1688_api': {'app_key': '9999', 'app_secret': 'injected', 'access_token': 'other', 'base_url': 'https://attacker.test'}}
    for merged in (merge_ai_config(get_context().paths.app_dir, current, incoming),
                   service.store.merge_app_config_fields(current, incoming)):
        assert merged['1688_api'] == current['1688_api']
    for public in (public_app_config(get_context().paths.app_dir, current), public_ai_config(get_context().paths.app_dir, current), service.status()):
        assert '1688_api' not in public
        assert not any(value in json.dumps(public) for value in ('old-app-secret', 'old-token'))


def test_official_authorization_signature_and_no_secret_on_client(service, monkeypatch):
    configure(monkeypatch)
    link = service.start()['url']
    parsed = urlsplit(link)
    assert parsed.scheme == 'https' and parsed.hostname == 'auth.1688.com'
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    signature = params.pop('_aop_signature')
    assert signature == hmac.new(b'enterprise-secret', ''.join(k + params[k] for k in sorted(params)).encode(), hashlib.sha1).hexdigest().upper()
    state = service.store.alibaba_credentials()
    assert params['state'] not in json.dumps(state)
    assert 'enterprise-secret' not in json.dumps(state) and 'enterprise-secret' not in link


@pytest.mark.parametrize('invalid', ['state', 'expired', 'application', 'redirect', 'duplicate', 'host'])
def test_invalid_callbacks_never_exchange_and_preserve_existing_authorization(service, monkeypatch, invalid):
    save_legacy()
    configure(monkeypatch)
    uri = callback(service)
    before = service.store.alibaba_credentials()['authorization']
    if invalid == 'state': uri = uri.replace('state=', 'state=wrong')
    if invalid == 'expired':
        state = service.store.alibaba_credentials()
        state['pending']['expires_at'] = 1
        service.store.update_alibaba_credentials({'pending': state['pending']})
    if invalid == 'application': monkeypatch.setenv('ERP_ALIBABA_APP_SECRET', 'changed')
    if invalid == 'redirect': monkeypatch.setenv('ERP_ALIBABA_REDIRECT_URI', 'https://changed.test/callback')
    if invalid == 'duplicate': uri += '&code=duplicate'
    if invalid == 'host': uri = uri.replace('callback.example.test', 'attacker.test')
    monkeypatch.setattr(service, '_token', lambda *args: pytest.fail('无效回调不得发送请求'))
    with pytest.raises(ValueError): service.complete(uri)
    assert service.store.alibaba_credentials()['authorization'] == before


def test_exchange_once_and_refresh_keeps_buyer_scope(service, monkeypatch):
    configure(monkeypatch)
    calls = []
    def exchange(application, values):
        calls.append(values)
        return token(access_token='token-' + str(len(calls)), expires_in=60 if len(calls) == 1 else 36000)
    monkeypatch.setattr(service, '_token', exchange)
    uri = callback(service)
    assert service.complete(uri)['member_id'] == 'buyer-a'
    original = service.store.alibaba_credentials()['authorization']['account_scope']
    with pytest.raises(ValueError): service.complete(uri)
    config = service.runtime_config()
    assert config['access_token'] == 'token-2' and account_scope(config) == original
    service.runtime_config()
    assert len(calls) == 2 and calls[-1]['refresh_token'] == 'refresh-1'
    monkeypatch.setattr(service, '_token', lambda *args: token('buyer-b'))
    service.complete(callback(service))
    assert account_scope(service.runtime_config()) != original


def test_late_exchange_cannot_overwrite_new_authorization(service, monkeypatch):
    configure(monkeypatch)
    uri = callback(service)
    def exchange(*args):
        service.store.update_alibaba_credentials({'authorization': {'member_id': 'newer-buyer'}})
        return token()
    monkeypatch.setattr(service, '_token', exchange)
    with pytest.raises(ValueError, match='已变化'): service.complete(uri)
    assert service.store.alibaba_credentials()['authorization']['member_id'] == 'newer-buyer'


def test_wrong_member_refresh_is_rejected_without_replacing_token(service, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(service, '_token', lambda *args: token(expires_in=60))
    service.complete(callback(service))
    before = service.store.alibaba_credentials()['authorization']
    monkeypatch.setattr(service, '_token', lambda *args: token('buyer-b'))
    with pytest.raises(ValueError, match='账号不一致'): service.runtime_config()
    assert service.store.alibaba_credentials()['authorization'] == before


def test_token_exchange_uses_managed_fixed_gateway_and_no_redirects(service, monkeypatch):
    configure(monkeypatch)
    calls = []
    def send(request, **kwargs):
        calls.append((request, kwargs))
        return io.BytesIO(json.dumps(token()).encode())
    monkeypatch.setattr(oauth, 'managed_urlopen', send)
    result = service.complete(callback(service))
    request, kwargs = calls[0]
    assert request.full_url == 'https://gw.open.1688.com/openapi/http/1/system.oauth2/getToken/12345'
    assert not kwargs['follow_redirects'] and kwargs['request_context'].semantics == 'write'
    assert parse_qs(request.data.decode())['client_secret'] == ['enterprise-secret']
    assert 'buyer-token' not in json.dumps(result)


def test_only_oauth_document_navigation_bypasses_cross_site_rejection():
    handler = SimpleNamespace(command='GET', path='/auth/alibaba/callback?code=c&state=s', headers={
        'Host': '127.0.0.1:5050', 'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'})
    validate_request_metadata(handler)
    for path in ('/api/alibaba/authorization/start', '/api/state'):
        handler.path = path
        with pytest.raises(RequestValidationError): validate_request_metadata(handler)
    handler.path = '/auth/alibaba/callback'; handler.command = 'POST'
    with pytest.raises(RequestValidationError): validate_request_metadata(handler)


def test_http_callback_completes_once_and_never_returns_credentials(service, monkeypatch):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    import requests
    from erp_web.http_handler import Handler

    configure(monkeypatch)
    monkeypatch.setattr(oauth.AlibabaAuthorizationService, '_token', staticmethod(lambda *args: token()))
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    from tests.account_test_utils import authenticated_headers
    client = requests.Session()
    client.headers.update(authenticated_headers(server))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setenv('ERP_ALIBABA_REDIRECT_URI', origin + '/auth/alibaba/callback')
    try:
        response = client.get(origin + '/api/alibaba/authorization', timeout=3)
        assert response.status_code == 200 and not response.json()['authorized']
        response = client.post(origin + '/api/alibaba/authorization/start', json={}, timeout=3)
        assert response.status_code == 200
        query = parse_qs(urlsplit(response.json()['url']).query)
        callback_url = query['redirect_uri'][0] + '?' + urlencode({'code': 'private-code', 'state': query['state'][0]})
        headers = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}
        response = client.get(callback_url, headers=headers, timeout=3)
        assert response.status_code == 200 and '账号已连接' in response.text
        assert response.headers['Cache-Control'] == 'no-store'
        assert not any(secret in response.text for secret in ('private-code', 'buyer-token', 'enterprise-secret'))
        assert client.get(callback_url, headers=headers, timeout=3).status_code == 400
        assert client.get(origin + '/api/alibaba/authorization', timeout=3).json()['member_id'] == 'buyer-a'
        assert client.post(origin + '/api/alibaba/authorization/complete', json={'callback_url': 123}, timeout=3).status_code == 400
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_exchange_network_failure_does_not_consume_existing_buyer(service, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(service, '_token', lambda *args: token())
    service.complete(callback(service))
    before = service.store.alibaba_credentials()['authorization']
    uri = callback(service)
    def fail(*args):
        raise ValueError('本次网络请求未完成')
    monkeypatch.setattr(service, '_token', fail)
    with pytest.raises(ValueError): service.complete(uri)
    assert service.store.alibaba_credentials()['authorization'] == before
    assert not service.store.alibaba_credentials()['pending']


def test_interrupted_migration_reuses_persisted_authorization(service, monkeypatch):
    save_legacy()
    original_save = service.store.save_app_config
    def interrupted(*args, **kwargs):
        raise OSError('模拟清理旧位置时中断')
    monkeypatch.setattr(service.store, 'save_app_config', interrupted)
    with pytest.raises(OSError): service.store.alibaba_credentials()
    monkeypatch.setattr(service.store, 'save_app_config', original_save)
    assert service.runtime_config()['access_token'] == LEGACY['access_token']
    assert account_scope(service.runtime_config()) == legacy_account_scope(LEGACY)
    assert not service.store.load_app_config()['1688_api']['access_token']


def test_crossborder_callback_is_isolated_from_purchase(service, monkeypatch):
    from erp_web.facades.alibaba_authorization_facade import authorization_payload

    configure(monkeypatch)
    monkeypatch.setenv('ERP_ALIBABA_CROSSBORDER_APP_KEY', '9876')
    monkeypatch.setenv('ERP_ALIBABA_CROSSBORDER_APP_SECRET', 'cross-secret')
    monkeypatch.setenv('ERP_ALIBABA_CROSSBORDER_REDIRECT_URI', 'http://127.0.0.1:5050/auth/alibaba/callback')
    cross = oauth.AlibabaAuthorizationService(service.store, crossborder=True)
    purchase_callback = callback(service)
    cross_callback = callback(cross)
    purchase_state = service.store.alibaba_credentials()
    assert not cross.runtime_config()['access_token']
    monkeypatch.setattr(oauth.AlibabaAuthorizationService, '_token', staticmethod(
        lambda app, values: token(access_token='buyer-' + app['app_key'])))
    with pytest.raises(ValueError):
        service.complete(cross_callback)
    params = parse_qs(urlsplit(cross_callback).query)
    body = {k: v[0] for k, v in params.items()}
    assert authorization_payload('callback', body)[1] == 200
    assert authorization_payload('callback', body)[1] == 400
    assert service.store.alibaba_credentials() == purchase_state
    assert service.complete(purchase_callback)['authorized']
    assert service.runtime_config()['access_token'] == 'buyer-12345'
    assert cross.runtime_config()['access_token'] == 'buyer-9876'


def test_crossborder_product_counts_only_returned_offer(monkeypatch):
    from erp_web.services import alibaba_api_client as api

    client = api.AlibabaApiClient(LEGACY)
    details = {'offerId': 123, 'productSkuInfos': [{'specId': 'a'}, {'skuId': 2}]}

    def send(request, **kwargs):
        assert kwargs['request_context'].semantics == 'read' and not kwargs['follow_redirects']
        params = parse_qs(request.data.decode())
        assert json.loads(params['offerDetailParam'][0]) == {'offerId': 123, 'country': 'en'}
        return io.BytesIO(json.dumps({'result': {'success': True, 'result': details}}).encode())

    monkeypatch.setattr(api, 'managed_urlopen', send)
    assert client.test_product('123') == {'ok': True, 'sku_count': 2, 'spec_id_count': 1}
    details['offerId'] = 124
    with pytest.raises(api.AlibabaApiError, match='该商品'):
        client.test_product('123')
