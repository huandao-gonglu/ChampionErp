"""应用与买家授权分离、旧授权迁移和 OAuth 生命周期回归。"""
import hashlib
import hmac
import io
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from erp_web.services import alibaba_api_client as api

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
    for key in ('ERP_ALIBABA_CROSSBORDER_APP_KEY', 'ERP_ALIBABA_CROSSBORDER_APP_SECRET', 'ERP_ALIBABA_CROSSBORDER_REDIRECT_URI',
                'ERP_ALIBABA_APP_KEY', 'ERP_ALIBABA_APP_SECRET', 'ERP_ALIBABA_REDIRECT_URI'):
        monkeypatch.delenv(key, raising=False)
    return oauth.AlibabaAuthorizationService(get_context().config)


def save_legacy():
    store = get_context().config
    value = store.load_app_config()
    value['1688_api'] = dict(LEGACY)
    store.save_app_config(value)


def configure(monkeypatch):
    get_context().db.set_runtime_secret('enterprise_applications', 'active', {'alibaba_api_application': 'alibaba_crossborder'})
    monkeypatch.setenv('ERP_ALIBABA_CROSSBORDER_APP_KEY', '12345')
    monkeypatch.setenv('ERP_ALIBABA_CROSSBORDER_APP_SECRET', 'enterprise-secret')
    monkeypatch.setenv('ERP_ALIBABA_CROSSBORDER_REDIRECT_URI', 'https://erp.example.test/auth/alibaba/callback')


def start_state(service):
    return parse_qs(urlsplit(service.start()['url']).query)['state'][0]


def token(member='buyer-a', **extra):
    return {'access_token': 'buyer-token', 'refresh_token': 'refresh-1', 'expires_in': 36000, 'memberId': member, **extra}


def test_self_application_is_default_and_restores_archived_authorization(service, monkeypatch):
    save_legacy()
    monkeypatch.setenv('ERP_ALIBABA_APP_KEY', LEGACY['app_key'])
    monkeypatch.setenv('ERP_ALIBABA_APP_SECRET', LEGACY['app_secret'])
    assert service.runtime_config()['access_token'] == LEGACY['access_token']
    assert service.runtime_config()['application_type'] == 'alibaba'
    assert service.status()['authorized']
    assert not service.status()['can_authorize']
    archived = service.store.archive_legacy_alibaba_credentials()
    assert archived['authorization']['account_scope'] == legacy_account_scope(LEGACY)
    assert service.store.load_app_config()['1688_api']['access_token'] == ''
    configure(monkeypatch)
    assert service.status()['can_authorize']
    assert not service.status()['authorized']


def test_archived_managed_self_grant_keeps_its_generation_and_purchase_scope(service):
    from erp_web.services.alibaba_credentials import application_fingerprint

    application = {'app_key': '1938727', 'app_secret': 'saved-secret',
        'redirect_uri': 'https://erp.example/auth/alibaba/callback', 'credential_generation': 'original-generation'}
    grant = {'access_token': 'saved-token', 'member_id': 'original-buyer',
        'application_fingerprint': application_fingerprint(application), 'account_scope': 'original-purchase-scope'}
    service.store._db.set_runtime_secret('enterprise_applications', 'active', {'alibaba': application})
    service.store._db.set_runtime_secret('credentials:alibaba', 'state', {'authorization': grant})
    assert service.status()['authorized']
    config = service.runtime_config()
    assert config['access_token'] == 'saved-token'
    assert account_scope(config) == 'original-purchase-scope'
    public = service.store.managed_settings()
    body = {key: public[key] for key in ('revision', 'exchange_rates', 'alibaba_api_application')}
    for provider in ('alibaba', 'alibaba_crossborder'):
        body[provider] = {key: public[provider][key] for key in ('app_key', 'redirect_uri')}
    service.store.save_managed_settings(body)
    assert service.store.alibaba_application()['credential_generation'] == 'original-generation'
    assert service.status()['authorized']
    assert service.store.alibaba_credentials()['authorization'] == grant


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


@pytest.mark.parametrize('invalid', ['state', 'expired', 'application', 'redirect'])
def test_invalid_callbacks_never_exchange_and_preserve_existing_authorization(service, monkeypatch, invalid):
    configure(monkeypatch)
    monkeypatch.setattr(service, '_token', lambda *args: token())
    service.callback('code', start_state(service))
    state_token = start_state(service)
    before = service.store.alibaba_credentials()['authorization']
    if invalid == 'state': state_token = 'wrong'
    if invalid == 'expired':
        state = service.store.alibaba_credentials()
        state['pending']['expires_at'] = 1
        service.store.update_alibaba_credentials({'pending': state['pending']})
    if invalid == 'application': monkeypatch.setenv('ERP_ALIBABA_CROSSBORDER_APP_SECRET', 'changed')
    if invalid == 'redirect': monkeypatch.setenv('ERP_ALIBABA_CROSSBORDER_REDIRECT_URI', 'https://changed.test/callback')
    monkeypatch.setattr(service, '_token', lambda *args: pytest.fail('无效回调不得发送请求'))
    with pytest.raises(ValueError): service.callback('one-use-code', state_token)
    assert service.store.alibaba_credentials()['authorization'] == before


def test_exchange_once_and_refresh_keeps_buyer_scope(service, monkeypatch):
    configure(monkeypatch)
    calls = []
    def exchange(application, values):
        calls.append(values)
        if values['grant_type'] == 'authorization_code':
            assert service.status()['attempt'] == 'waiting'
        return token(access_token='token-' + str(len(calls)), expires_in=60 if len(calls) == 1 else 36000)
    monkeypatch.setattr(service, '_token', exchange)
    state_token = start_state(service)
    assert service.callback('one-use-code', state_token)['member_id'] == 'buyer-a'
    original = service.store.alibaba_credentials()['authorization']['account_scope']
    with pytest.raises(ValueError): service.callback('one-use-code', state_token)
    config = service.runtime_config()
    assert config['access_token'] == 'token-2' and account_scope(config) == original
    service.runtime_config()
    assert len(calls) == 2 and calls[-1]['refresh_token'] == 'refresh-1'
    monkeypatch.setattr(service, '_token', lambda *args: token('buyer-b'))
    service.callback('one-use-code', start_state(service))
    assert account_scope(service.runtime_config()) != original


def test_late_exchange_cannot_overwrite_new_authorization(service, monkeypatch):
    configure(monkeypatch)
    state_token = start_state(service)
    def exchange(*args):
        service.store.update_alibaba_credentials({'authorization': {'member_id': 'newer-buyer'}})
        return token()
    monkeypatch.setattr(service, '_token', exchange)
    with pytest.raises(ValueError, match='已变化'): service.callback('one-use-code', state_token)
    assert service.store.alibaba_credentials()['authorization']['member_id'] == 'newer-buyer'


def test_wrong_member_refresh_is_rejected_without_replacing_token(service, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(service, '_token', lambda *args: token(expires_in=60))
    service.callback('one-use-code', start_state(service))
    before = service.store.alibaba_credentials()['authorization']
    monkeypatch.setattr(service, '_token', lambda *args: token('buyer-b'))
    with pytest.raises(ValueError, match='账号不一致'): service.runtime_config()
    assert service.store.alibaba_credentials()['authorization'] == before


@pytest.fixture
def self_use(service, monkeypatch):
    monkeypatch.setenv('ERP_ALIBABA_APP_KEY', '12345')
    monkeypatch.setenv('ERP_ALIBABA_APP_SECRET', 'self-use-secret')
    monkeypatch.setenv('ERP_ALIBABA_REDIRECT_URI', 'http://localhost:5050/auth/alibaba/callback')
    return service


@pytest.mark.parametrize('expires', ['0', -1])
def test_self_use_callback_verifies_actual_token_when_lifetime_is_nonpositive(self_use, monkeypatch, expires):
    monkeypatch.setattr(self_use, '_token', lambda *args: token(expires_in=expires))
    calls = []
    def verify(client, method, values, **kwargs):
        calls.append((method, client.access_token))
        return {'success': True, 'result': {'receiveAddressItems': []}}
    monkeypatch.setattr(api.SelfUseAlibabaProvider, '_send', verify)
    callback = 'http://localhost:5050/auth/alibaba/callback?' + urlencode({'state': start_state(self_use), 'code': 'once'})
    status = self_use.complete_from_url(callback, 'alibaba')
    assert calls == [(api.ADDRESSES, 'buyer-token')]
    assert status['authorized'] and status['member_id'] == 'buyer-a' and status['expires_at'] == 0
    assert self_use.runtime_config()['access_token'] == 'buyer-token'
    with pytest.raises(ValueError, match='失效或不匹配'):
        self_use.complete_from_url(callback, 'alibaba')


def test_manual_self_use_token_checks_current_credential_and_preserves_grant_on_failure(self_use, monkeypatch):
    monkeypatch.setattr(self_use, '_token', lambda *args: token())
    self_use.callback('once', start_state(self_use))
    original = self_use.store.alibaba_credentials()['authorization']
    def verify(client, method, values, **kwargs):
        assert method == api.ADDRESSES
        if client.access_token == 'bad':
            raise ValueError('1688 拒绝访问')
        return {'success': True, 'result': {'receiveAddressItems': []}}
    monkeypatch.setattr(api.SelfUseAlibabaProvider, '_send', verify)
    with pytest.raises(ValueError, match='拒绝访问'):
        self_use.bind_self_use_token('bad')
    monkeypatch.setattr(self_use, '_token', lambda *args: token(access_token='bad', expires_in='-1'))
    with pytest.raises(ValueError, match='-1 秒.*拒绝访问'):
        self_use.callback('new-code', start_state(self_use))
    assert self_use.store.alibaba_credentials()['authorization'] == original
    status = self_use.bind_self_use_token('  permanent-token  ')
    assert status['authorized'] and not status['member_id'] and status['attempt'] == 'idle'
    assert not status['refreshable'] and status['expires_at'] == 0
    assert 'permanent-token' not in json.dumps(status)
    cfg = self_use.runtime_config()
    assert cfg['access_token'] == 'permanent-token' and cfg['account_scope'] != original['account_scope']


def test_crossborder_expired_grant_never_uses_self_use_verification(service, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(service, '_token', lambda *args: token(expires_in=0))
    monkeypatch.setattr(service, '_verify_self_use_token', lambda *args: pytest.fail('不应验证企业自用 Token'))
    with pytest.raises(ValueError, match='0 秒'):
        service.callback('once', start_state(service))
    assert not service.status()['authorized']


def test_token_exchange_uses_managed_fixed_gateway_and_no_redirects(service, monkeypatch):
    configure(monkeypatch)
    calls = []
    def send(request, **kwargs):
        calls.append((request, kwargs))
        return io.BytesIO(json.dumps(token()).encode())
    monkeypatch.setattr(oauth, 'managed_urlopen', send)
    result = service.callback('one-use-code', start_state(service))
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


def test_manual_self_use_binding_http_is_admin_only(self_use, monkeypatch):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    import requests
    from erp_web.http_handler import Handler
    from erp_web.schemas.accounts import SESSION_COOKIE
    from tests.account_test_utils import authenticated_headers

    calls = []
    def read_addresses(client, method, values, **kwargs):
        calls.append((method, client.access_token))
        return {'success': True, 'result': {'receiveAddressItems': []}}
    monkeypatch.setattr(api.SelfUseAlibabaProvider, '_send', read_addresses)
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        admin_headers = authenticated_headers(server)
        accounts = server.account_runtime.store
        user = accounts.create_user('buyer@example.test', '采购员', 'test-hash')
        cookie, session = accounts.create_session(user['id'], user['password_hash'])
        endpoint = f'http://127.0.0.1:{server.server_port}/api/alibaba/authorization/self-use-token'
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body = {'access_token': 'private-token'}
            headers = {'Cookie': f'{SESSION_COOKIE}={cookie}', 'X-CSRF-Token': session.csrf_token}
            assert requests.post(endpoint, headers=headers, json=body, timeout=3).status_code == 403
            assert requests.post(endpoint, headers=admin_headers, json={}, timeout=3).status_code == 400
            response = requests.post(endpoint, headers=admin_headers, json=body, timeout=3)
            assert response.status_code == 200 and response.json()['authorized']
            assert 'private-token' not in response.text and calls == [(api.ADDRESSES, 'private-token')]
        finally:
            server.shutdown()
            thread.join(timeout=3)


def test_http_callback_completes_once_and_never_returns_credentials(service, monkeypatch):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    import requests
    from erp_web.http_handler import Handler

    configure(monkeypatch)
    monkeypatch.setattr(oauth.AlibabaAuthorizationService, '_token', staticmethod(lambda *args: token(resource_owner='采购账号')))
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    from tests.account_test_utils import authenticated_headers
    client = requests.Session()
    client.headers.update(authenticated_headers(server))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setenv('ERP_ALIBABA_CROSSBORDER_REDIRECT_URI', origin + '/auth/alibaba/callback')
    try:
        response = client.get(origin + '/api/alibaba/authorization', timeout=3)
        assert response.status_code == 200 and not response.json()['authorized']
        old_path = '/api/alibaba/crossborder/authorization'
        old_start = client.post(origin + old_path + '/start', json={}, timeout=3)
        assert parse_qs(urlsplit(old_start.json()['url']).query)['client_id'] == ['12345']
        response = client.post(origin + '/api/alibaba/authorization/start', json={}, timeout=3)
        assert response.status_code == 200
        query = parse_qs(urlsplit(response.json()['url']).query)
        callback_url = query['redirect_uri'][0] + '?' + urlencode({'code': 'private-code', 'state': query['state'][0]})
        headers = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}
        assert client.get(origin + '/api/alibaba/authorization', timeout=3).json()['attempt'] == 'waiting'
        assert requests.get(callback_url + '&code=duplicate', headers=headers, timeout=3).status_code == 400
        response = requests.get(callback_url, headers=headers, timeout=3)
        assert response.status_code == 200 and '账号已连接' in response.text
        assert response.headers['Cache-Control'] == 'no-store'
        assert not any(secret in response.text for secret in ('private-code', 'buyer-token', 'enterprise-secret'))
        assert client.get(callback_url, headers=headers, timeout=3).status_code == 400
        status = client.get(origin + '/api/alibaba/authorization', timeout=3).json()
        assert client.get(origin + old_path, timeout=3).json() == status
        assert (status['member_id'], status['account_name'], status['attempt']) == ('buyer-a', '采购账号', 'idle')
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_exchange_network_failure_does_not_consume_existing_buyer(service, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(service, '_token', lambda *args: token())
    service.callback('one-use-code', start_state(service))
    before = service.store.alibaba_credentials()['authorization']
    state_token = start_state(service)
    def fail(*args):
        raise ValueError('本次网络请求未完成')
    monkeypatch.setattr(service, '_token', fail)
    with pytest.raises(ValueError): service.callback('one-use-code', state_token)
    assert service.store.alibaba_credentials()['authorization'] == before
    assert service.status()['attempt'] == 'failed'
    assert not service.store.alibaba_credentials()['pending']['state_hash']


def test_interrupted_migration_reuses_persisted_authorization(service, monkeypatch):
    save_legacy()
    original_save = service.store.save_app_config
    def interrupted(*args, **kwargs):
        raise OSError('模拟清理旧位置时中断')
    monkeypatch.setattr(service.store, 'save_app_config', interrupted)
    with pytest.raises(OSError): service.store.alibaba_credentials()
    monkeypatch.setattr(service.store, 'save_app_config', original_save)
    assert service.runtime_config()['access_token'] == LEGACY['access_token']
    assert service.store.archive_legacy_alibaba_credentials()['authorization']['account_scope'] == legacy_account_scope(LEGACY)
    assert not service.store.load_app_config()['1688_api']['access_token']


def test_other_application_pending_state_cannot_replace_selected_grant(service, monkeypatch):
    from erp_web.facades.alibaba_authorization_facade import authorization_payload

    configure(monkeypatch)
    state = start_state(service)
    old = 'retired-pending'
    service.store._db.set_runtime_secret('credentials:alibaba', 'state', {'pending': {
        'state_hash': hashlib.sha256(old.encode()).hexdigest()}})
    monkeypatch.setattr(oauth.AlibabaAuthorizationService, '_token', staticmethod(lambda *args: token()))
    assert authorization_payload('callback', {'state': old, 'code': 'code'})[1] == 400
    assert authorization_payload('callback', {'state': state, 'code': 'code'})[1] == 200
    assert oauth.alibaba_runtime_config(service.store)['access_token'] == 'buyer-token'


def test_application_switch_during_exchange_preserves_both_grants(service, monkeypatch):
    configure(monkeypatch)
    original = service.store.alibaba_application()
    state = start_state(service)

    def switch_during_exchange(*args):
        service.store._db.set_runtime_secret('enterprise_applications', 'active', {'alibaba_api_application': 'alibaba'})
        return token()

    monkeypatch.setattr(service, '_token', switch_during_exchange)
    completed = service.callback('one-use-code', state)
    assert completed['authorized'] and not completed['active']
    assert completed['application_type'] == 'alibaba_crossborder'
    assert not service.store.alibaba_credentials().get('authorization')
    service.store._db.set_runtime_secret('enterprise_applications', 'active', {'alibaba_api_application': 'alibaba_crossborder'})
    assert service.store.alibaba_application() == original
    assert service.runtime_config()['access_token'] == 'buyer-token'


def test_explicit_provider_grants_survive_business_switch_and_out_of_order_callbacks(service, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv('ERP_ALIBABA_APP_KEY', '1938727')
    monkeypatch.setenv('ERP_ALIBABA_APP_SECRET', 'self-secret')
    redirect = 'https://erp.example.test/auth/alibaba/callback'
    monkeypatch.setenv('ERP_ALIBABA_REDIRECT_URI', redirect)
    monkeypatch.setattr(service, '_token', lambda app, _: token(access_token='token-' + app['app_key']))
    store = service.store

    def select(provider):
        store._db.set_runtime_secret('enterprise_applications', 'active', {'alibaba_api_application': provider})

    pending = {}
    for provider, key in [('alibaba', '1938727'), ('alibaba_crossborder', '12345')]:
        query = parse_qs(urlsplit(service.start(provider)['url']).query)
        assert query['client_id'] == [key]
        pending[provider] = query['state'][0]
    assert service.status('alibaba')['attempt'] == service.status('alibaba_crossborder')['attempt'] == 'waiting'

    select('onebound')
    callback = redirect + '?' + urlencode({'code': 'code', 'state': pending['alibaba']})
    with pytest.raises(ValueError, match='不匹配'):
        service.complete_from_url(callback, 'alibaba_crossborder')
    assert service.callback('code', pending['alibaba_crossborder'])['application_type'] == 'alibaba_crossborder'
    assert service.complete_from_url(callback, 'alibaba')['application_type'] == 'alibaba'
    assert not service.status('onebound')['authorized']
    for provider, key in [('alibaba', '1938727'), ('alibaba_crossborder', '12345'), ('alibaba', '1938727')]:
        select(provider)
        assert service.status(provider)['active']
        assert service.runtime_config()['access_token'] == 'token-' + key
        assert all(service.status(p)['authorized'] for p in pending)



def test_onebound_shop_bindings_route_only_exposes_admin_listing(monkeypatch):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    import requests
    from erp_web.http_handler import Handler
    from erp_web.schemas.accounts import SESSION_COOKIE
    from erp_web.services import onebound_api_transport as transport
    from tests.account_test_utils import authenticated_headers
    from tests.test_onebound_api import configure, response

    configure(get_context().config)
    calls = []

    def send(request, **kwargs):
        parsed = urlsplit(request.full_url)
        assert parsed.path == '/sys/shop_bind_list/'
        params = parse_qs(parsed.query)
        assert params['nick'] == ['店铺甲'] and params['page'] == ['2']
        assert params['key'] == ['vendor-key'] and params['secret'] == ['vendor-secret']
        assert 'access_token' not in params
        assert not kwargs['follow_redirects'] and kwargs['request_context'].semantics == 'read'
        calls.append(parsed.path)
        return response({'error_code': '0000', 'items': {'page': 2, 'total_results': '51', 'pagecount': 2,
            'item': [{'shop_bind_id': 'shop-1', 'nick': '店铺甲', 'status': '1', 'exp_time': None,
                      'api_key': 'vendor-key', 'session': 'private-session', 'api_type': '1688'}]}})

    monkeypatch.setattr(transport, 'managed_urlopen', send)
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        headers = authenticated_headers(server)
        accounts = server.account_runtime.store
        user = accounts.create_user('shop-reader@example.test', '采购员', 'test-hash')
        cookie, _ = accounts.create_session(user['id'], user['password_hash'])
        endpoint = f'http://127.0.0.1:{server.server_port}/api/admin/alibaba/onebound/shop-bindings'
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            assert requests.get(endpoint, timeout=3).status_code == 401
            assert requests.get(endpoint, cookies={SESSION_COOKIE: cookie}, timeout=3).status_code == 403
            assert calls == []
            result = requests.get(endpoint, headers=headers, params={'nick': '店铺甲', 'page': '2'}, timeout=3)
            assert result.status_code == 200
            assert result.json() == {'ok': True, 'page': 2, 'total': 51, 'page_count': 2,
                                     'items': [{'id': 'shop-1', 'nick': '店铺甲', 'status': '1', 'expires_at': '', 'selectable': True}]}
            assert len(calls) == 1
        finally:
            server.shutdown()
            worker.join(timeout=3)
