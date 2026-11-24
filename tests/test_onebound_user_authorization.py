"""万邦个人买家授权、身份验证和旧凭据读取。"""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from urllib.parse import parse_qs, urlsplit

import pytest

from erp_web.context import get_context
from erp_web.facades.collect_facade import collect_1688_payload
from erp_web.services import alibaba_api_client as official, image_service, onebound_api_transport as transport
from erp_web.services.alibaba_api_client import alibaba_provider, AlibabaApiError, ORDER_LIST
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


def test_onebound_verifies_buyer_at_authorization_then_collects_with_one_request_per_product(monkeypatch):
    store = get_context().config
    configure(store)
    service = AlibabaAuthorizationService(store)
    calls = []
    detail = 'com.alibaba.fenxiao.crossborder/product.search.queryProductDetail'
    identity = 'com.alibaba.account/alibaba.account.basic'

    def gateway(request, *, request_context, **kwargs):
        method = request_context.interface
        calls.append(method)
        if method == '1688global/buyer_token_create':
            return response({'items': {'session': 'buyer-token', 'nick': '买家甲', 'exp_time': '2099-01-01 00:00:00'}})
        params = parse_qs(urlsplit(request.full_url).query)
        assert params['access_token'] == ['buyer-token']
        if method == identity:
            return response({'response': {'memberId': 'buyer-a', 'loginId': '买家甲'}})
        assert method == detail
        offer_id = json.loads(params['_o_args'][0])['offerDetailParam']['offerId']
        product = {'offerId': offer_id, 'subject': f'商品 {offer_id}', 'images': ['https://example.test/product.jpg']}
        return response({'response': {'result': {'success': True, 'result': product}}})

    monkeypatch.setattr(transport, 'managed_urlopen', gateway)
    monkeypatch.setattr(image_service, 'download_remote_image', lambda _app, url, _product, index: {
        'id': f'image-{index}', 'url': url, 'status': 'ready',
    })
    service.start()
    assert service.complete_from_url(CALLBACK + 'code', 'onebound')['authorized']
    assert calls == ['1688global/buyer_token_create', identity]
    calls.clear()

    for offer_id in (123456, 123457):
        url = f'https://detail.1688.com/offer/{offer_id}.html'
        result, status = collect_1688_payload({'url': url, 'mode': 'api'})
        assert status == 200 and result['ok']
        assert result['product']['source']['title'] == f'商品 {offer_id}'
    assert calls == [detail, detail]


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
def test_switching_providers_preserves_saved_onebound_credential(monkeypatch, field):
    store = get_context().config
    configure(store)
    service = AlibabaAuthorizationService(store)
    sent = []

    def send(request, **kwargs):
        params = parse_qs(urlsplit(request.full_url).query)
        sent.append(params)
        assert params['method'] == ['com.alibaba.trade/' + ORDER_LIST]
        return response({'response': {'result': [], 'totalRecord': 0}})

    monkeypatch.setattr(transport, 'managed_urlopen', send)
    token = 'buyer-a+&=买家'
    store.update_alibaba_credentials({'authorization': {
        'access_token': token, 'credential_type': field, 'account_scope': 'existing-scope', 'member_id': 'buyer-a',
        'application_fingerprint': application_fingerprint(store.alibaba_application()),
    }})
    saved = store._db.runtime_secret('enterprise_applications', 'active', {})
    store._db.set_runtime_secret('enterprise_applications', 'active', {**saved, 'alibaba_api_application': 'alibaba_crossborder'})
    status = service.status('onebound')
    assert status['authorized'] and not status['active'] and token not in repr(status)
    assert not service.status()['authorized']
    store._db.set_runtime_secret('enterprise_applications', 'active', saved)

    client = alibaba_provider(service.runtime_config())
    assert client.find_order_numbers('external-id') == []
    params, = sent
    assert params['key'] == ['vendor-key'] and params['secret'] == ['vendor-secret']
    assert params.get('session') == ([token] if field == 'session' else None)
    assert params.get('access_token') == ([token] if field == 'access_token' else None)
    assert 'access_token' not in json.loads(params['_o_args'][0])


@pytest.mark.parametrize('verified', [True, False])
def test_saved_onebound_directory_session_keeps_its_verification_and_scope(verified):
    store = get_context().config
    configure(store)
    store.update_alibaba_credentials({'authorization': {
        'access_token': 'saved-session', 'credential_type': 'session', 'account_scope': 'existing-scope',
        'directory_shop_id': 'saved-binding', 'directory_verified': verified, 'account_name': '买家甲',
        'member_id': 'buyer-a' if verified else '',
        'application_fingerprint': application_fingerprint(store.alibaba_application()),
    }})
    before = store.alibaba_credentials()
    service = AlibabaAuthorizationService(store)
    assert service.status()['authorized'] is verified
    assert service.status()['needs_reauthorization'] is not verified
    if verified:
        config = service.runtime_config()
        assert config['credential_type'] == 'session' and config['directory_verified'] is True
        assert config['member_id'] == 'buyer-a' and config['account_scope'] == 'existing-scope'
    else:
        with pytest.raises(ValueError, match='请重新连接账号'):
            service.runtime_config()
    assert store.alibaba_credentials() == before


def test_onebound_stale_access_token_syncs_rotated_buyer_token_and_retries_read(monkeypatch):
    store = get_context().config
    configure(store)
    store.update_alibaba_credentials({'authorization': {
        'access_token': 'stale-token', 'credential_type': 'access_token',
        'member_id': 'buyer-a', 'account_name': '买家甲',
        'expires_at': 4_000_000_000, 'account_scope': 'existing-scope',
        'application_fingerprint': application_fingerprint(store.alibaba_application()),
    }})
    calls = []
    detail = 'com.alibaba.fenxiao.crossborder/product.search.queryProductDetail'
    identity = 'com.alibaba.account/alibaba.account.basic'

    def gateway(request, *, request_context, **kwargs):
        parsed = urlsplit(request.full_url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        calls.append((request_context.interface, params.get('access_token', [''])[0]))
        if request_context.interface == 'sys/shop_bind_list':
            assert params.get('nick') == ['']
            return response({'items': {
                'page': 1, 'page_size': 50, 'total_results': '2', 'pagecount': 1,
                'item': [
                    {'shop_bind_id': '1', 'nick': '默认买家', 'session': 'default-token', 'status': '1',
                     'api_type': '1688', 'memo': 'buyer-default', 'exp_time': '2099-01-01 00:00:00'},
                    {'shop_bind_id': '2', 'nick': '买家甲', 'session': 'rotated-token', 'status': '1',
                     'api_type': '1688', 'memo': 'buyer-a', 'exp_time': '2099-01-01 00:00:00'},
                ],
            }})
        token = params['access_token'][0]
        if token == 'stale-token':
            return response({'error': 'item-not-found', 'error_code': '2000',
                             'reason': 'Request need user authorized 接口文档：https://open.onebound.cn/help/api/1688.custom.html'})
        assert token == 'rotated-token'
        if request_context.interface == identity:
            return response({'response': {'memberId': 'buyer-a', 'loginId': '买家甲'}})
        offer_id = json.loads(params['_o_args'][0])['offerDetailParam']['offerId']
        return response({'response': {'result': {'success': True, 'result': {
            'offerId': offer_id, 'subject': f'商品 {offer_id}', 'images': ['https://example.test/p.jpg'],
        }}}})

    monkeypatch.setattr(transport, 'managed_urlopen', gateway)
    monkeypatch.setattr(image_service, 'download_remote_image', lambda _app, url, _product, index: {
        'id': f'image-{index}', 'url': url, 'status': 'ready',
    })
    result, status = collect_1688_payload({'url': 'https://detail.1688.com/offer/1028620347857.html', 'mode': 'api'})
    assert status == 200 and result['ok']
    assert result['product']['source']['title'] == '商品 1028620347857'
    assert calls == [
        (detail, 'stale-token'),
        ('sys/shop_bind_list', ''),
        (identity, 'rotated-token'),
        (detail, 'rotated-token'),
    ]
    assert store.alibaba_credentials()['authorization']['access_token'] == 'rotated-token'
    assert store.alibaba_credentials()['authorization']['account_scope'] == 'existing-scope'

    calls.clear()
    second, status2 = collect_1688_payload({'url': 'https://detail.1688.com/offer/525488887052.html', 'mode': 'api'})
    assert status2 == 200 and second['ok']
    assert calls == [(detail, 'rotated-token')]


@pytest.mark.parametrize('scenario', ['missing_member', 'identity_mismatch', 'unchanged_token'])
def test_onebound_token_sync_rejects_mismatched_or_unchanged_buyer_token(monkeypatch, scenario):
    store = get_context().config
    configure(store)
    initial = {
        'access_token': 'stale-token', 'credential_type': 'access_token',
        'member_id': 'buyer-a', 'account_name': '买家甲',
        'expires_at': 4_000_000_000, 'account_scope': 'existing-scope',
        'application_fingerprint': application_fingerprint(store.alibaba_application()),
    }
    store.update_alibaba_credentials({'authorization': initial})

    def gateway(request, *, request_context, **kwargs):
        if request_context.interface == 'sys/shop_bind_list':
            if scenario == 'missing_member':
                items = [{'shop_bind_id': '1', 'nick': '默认买家', 'session': 'default-token', 'status': '1',
                          'api_type': '1688', 'memo': 'buyer-default', 'exp_time': '2099-01-01 00:00:00'}]
            elif scenario == 'unchanged_token':
                items = [{'shop_bind_id': '2', 'nick': '买家甲', 'session': 'stale-token', 'status': '1',
                          'api_type': '1688', 'memo': 'buyer-a', 'exp_time': '2099-01-01 00:00:00'}]
            else:
                items = [{'shop_bind_id': '2', 'nick': '买家甲', 'session': 'wrong-owner-token', 'status': '1',
                          'api_type': '1688', 'memo': 'buyer-a', 'exp_time': '2099-01-01 00:00:00'}]
            return response({'items': {'page': 1, 'page_size': 50, 'total_results': str(len(items)), 'pagecount': 1, 'item': items}})
        if request_context.interface == 'com.alibaba.account/alibaba.account.basic':
            return response({'response': {'memberId': 'buyer-default', 'loginId': '默认买家'}})
        return response({'error': 'item-not-found', 'error_code': '2000',
                         'reason': 'Request need user authorized 接口文档：https://open.onebound.cn/help/api/1688.custom.html'})

    monkeypatch.setattr(transport, 'managed_urlopen', gateway)
    with pytest.raises(AlibabaApiError, match='重新连接账号'):
        alibaba_provider(AlibabaAuthorizationService(store).runtime_config()).get_product(
            '1028620347857', 'https://detail.1688.com/offer/1028620347857.html',
        )
    assert store.alibaba_credentials()['authorization'] == initial


def test_onebound_token_sync_handles_null_empty_shop_page_without_pagination_error(monkeypatch):
    store = get_context().config
    configure(store)
    initial = {
        'access_token': 'stale-token', 'credential_type': 'access_token',
        'member_id': 'buyer-empty', 'account_name': '南国野兽3',
        'expires_at': 4_000_000_000, 'account_scope': 'existing-scope',
        'application_fingerprint': application_fingerprint(store.alibaba_application()),
    }
    store.update_alibaba_credentials({'authorization': initial})

    def gateway(request, *, request_context, **kwargs):
        if request_context.interface == 'sys/shop_bind_list':
            return response({'error_code': '0000', 'items': {
                'page': '1', 'page_size': 50, 'total_results': '0', 'pagecount': 0, 'item': None,
            }})
        return response({'error': 'item-not-found', 'error_code': '2000',
                         'reason': 'Request need user authorized 接口文档：https://open.onebound.cn/help/api/1688.custom.html'})

    monkeypatch.setattr(transport, 'managed_urlopen', gateway)
    with pytest.raises(AlibabaApiError, match='未找到唯一有效的万邦买家授权'):
        alibaba_provider(AlibabaAuthorizationService(store).runtime_config()).get_product(
            '1028620347857', 'https://detail.1688.com/offer/1028620347857.html',
        )
    assert store.alibaba_credentials()['authorization'] == initial


def test_onebound_token_sync_paginates_and_disambiguates_same_member_by_account_name(monkeypatch):
    store = get_context().config
    configure(store)
    store.update_alibaba_credentials({'authorization': {
        'access_token': 'stale-token', 'credential_type': 'access_token',
        'member_id': 'buyer-paged', 'account_name': '南国野兽3',
        'expires_at': 4_000_000_000, 'account_scope': 'existing-scope',
        'application_fingerprint': application_fingerprint(store.alibaba_application()),
    }})
    pages_requested = []

    def gateway(request, *, request_context, **kwargs):
        params = parse_qs(urlsplit(request.full_url).query, keep_blank_values=True)
        if request_context.interface == 'sys/shop_bind_list':
            assert params.get('nick') == ['']
            page = int(params['page'][0])
            pages_requested.append(page)
            if page == 1:
                return response({'error_code': '0000', 'items': {
                    'page': '1', 'page_size': 50, 'total_results': '52', 'pagecount': 2,
                    'item': [
                        {'shop_bind_id': '1', 'nick': '历史别名', 'session': 'old-alias-token', 'status': '1',
                         'api_type': '1688', 'memo': 'buyer-paged', 'exp_time': '2099-01-01 00:00:00'},
                    ],
                }})
            return response({'error_code': '0000', 'items': {
                'page': '2', 'page_size': 50, 'total_results': '52', 'pagecount': 2,
                'item': [
                    {'shop_bind_id': '2', 'nick': '南国野兽3', 'session': 'rotated-token-2', 'status': '1',
                     'api_type': '1688', 'memo': 'buyer-paged', 'exp_time': '2099-01-01 00:00:00'},
                ],
            }})
        token = params['access_token'][0]
        if token == 'stale-token':
            return response({'error': 'item-not-found', 'error_code': '2000',
                             'reason': 'Request need user authorized'})
        assert token == 'rotated-token-2'
        if request_context.interface == 'com.alibaba.account/alibaba.account.basic':
            return response({'response': {'memberId': 'buyer-paged', 'loginId': '南国野兽3'}})
        return response({'response': {'result': {'success': True, 'result': {
            'offerId': 1028620347857, 'subject': '分页同步商品', 'images': ['https://example.test/p.jpg'],
        }}}})

    monkeypatch.setattr(transport, 'managed_urlopen', gateway)
    product = alibaba_provider(AlibabaAuthorizationService(store).runtime_config()).get_product(
        '1028620347857', 'https://detail.1688.com/offer/1028620347857.html',
    )
    assert product['product']['title'] == '分页同步商品'
    assert pages_requested == [1, 2]
    assert store.alibaba_credentials()['authorization']['access_token'] == 'rotated-token-2'
