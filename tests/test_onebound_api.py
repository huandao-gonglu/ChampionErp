"""万邦适配只用替身：显式通道、协议归一、错误脱敏和写入语义。"""
import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from erp_web.services import onebound_api_transport as transport
from erp_web.services.alibaba_api_client import alibaba_provider, AlibabaApiError, AlibabaApiRejected, OneBoundAlibabaProvider, ORDER_DETAIL, LOGISTICS_INFO, LOGISTICS_TRACE, _parse_response
from erp_web.services.alibaba_api_client import ADDRESSES, PREVIEW, CREATE, ORDER_LIST, PAY_STATUS, PAY


CONFIG = {'application_type': 'onebound', 'api_key': 'vendor-key', 'api_secret': 'vendor-secret', 'access_token': 'buyer-token'}
DIRECTORY_CONFIG = {**CONFIG, 'access_token': 'buyer-session', 'credential_type': 'session',
                    'directory_verified': True, 'member_id': 'buyer-a'}


def response(value):
    return io.BytesIO(json.dumps(value).encode())


@pytest.mark.parametrize('api', [ADDRESSES, PREVIEW, ORDER_LIST, PAY_STATUS])
def test_custom_transport_preserves_read_arguments(monkeypatch, api):
    calls = []
    def send(request, **kwargs):
        calls.append((request, kwargs))
        return response({'response': {'success': True, 'result': {}}})
    monkeypatch.setattr(transport, 'managed_urlopen', send)
    values = {'cargoParamList': [{'offerId': 123, 'specId': '蓝色', 'quantity': 2}]}
    transport.request_onebound(CONFIG, api, values, namespace='com.alibaba.trade', semantics='read', timeout=20)
    assert len(calls) == 1
    request, kwargs = calls[0]
    parsed = urlsplit(request.full_url)
    params = parse_qs(parsed.query)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ('https', 'api-gw.onebound.cn', '/1688/custom')
    assert params['key'] == ['vendor-key'] and params['secret'] == ['vendor-secret'] and 'session' not in params
    assert params['method'] == ['com.alibaba.trade/' + api]
    assert json.loads(params['_o_args'][0]) == values
    assert params['access_token'] == ['buyer-token']
    assert params['cache'] == ['no'] and '_aop_signature' not in params
    context = kwargs['request_context']
    assert context.semantics == 'read'
    assert context.max_attempts == 1 and not kwargs['follow_redirects']
    assert not any(secret in repr(context) for secret in ('vendor-key', 'vendor-secret', 'buyer-token'))


def test_product_order_and_logistics_use_same_explicit_provider(monkeypatch):
    calls = []
    def send(request, **kwargs):
        params = parse_qs(urlsplit(request.full_url).query)
        assert params['access_token'] == ['buyer-token']
        assert 'access_token' not in json.loads(params['_o_args'][0])
        calls.append(params['method'][0])
        if 'queryProductDetail' in calls[-1]:
            return response({'response': {'result': {'success': True, 'result': {'offerId': 123}}}})
        if calls[-1].endswith(ORDER_DETAIL):
            return response({'response': {'baseInfo': {'idOfStr': '123', 'status': 'success'}}})
        if calls[-1].endswith(LOGISTICS_TRACE):
            return response({'response': {'logisticsTrace': []}})
        return response({'response': {'success': True, 'result': []}})
    monkeypatch.setattr(transport, 'managed_urlopen', send)
    client = alibaba_provider({**CONFIG, 'member_id': 'buyer-a'})
    assert client.get_product('123', 'https://detail.1688.com/offer/123.html')['product']['api_offer_id'] == '123'
    assert client.get_order('123').status['order_number'] == '123'
    assert client.get_shipments('123') == []
    assert client.get_logistics_traces('123') == []
    assert calls == ['com.alibaba.fenxiao.crossborder/product.search.queryProductDetail',
                     'com.alibaba.trade/' + ORDER_DETAIL, 'com.alibaba.logistics/' + LOGISTICS_INFO,
                     'com.alibaba.logistics/' + LOGISTICS_TRACE]


@pytest.mark.parametrize('code,rejected', [('4005', True), ('4016', True), ('4001', False), ('4017', False), ('5000', False)])
def test_vendor_failures_are_safe_and_do_not_claim_network_writes_failed(monkeypatch, code, rejected):
    monkeypatch.setattr(transport, 'managed_urlopen', lambda *a, **k: response({
        'error_code': code, 'error': 'vendor-secret buyer-token', 'call_args': CONFIG}))
    with pytest.raises(AlibabaApiError) as error:
        alibaba_provider(CONFIG).preview_order({}, [])
    assert isinstance(error.value, AlibabaApiRejected) is rejected
    assert 'vendor-secret' not in str(error.value) and 'buyer-token' not in str(error.value)


def configure(store):
    public = store.managed_settings()
    body = {key: public[key] for key in ('revision', 'exchange_rates')}
    body.update(alibaba_crossborder={'app_key': '', 'redirect_uri': ''}, alibaba_api_application='onebound',
                onebound={'api_key': CONFIG['api_key'], 'api_secret': CONFIG['api_secret']})
    return store.save_managed_settings(body)


@pytest.mark.parametrize('value', [{'errorCode': '500_005'}, {'error_code': '500_005'}, {'error': {'code': '500_005'}}])
def test_flattened_business_errors_remain_rejections(monkeypatch, value):
    monkeypatch.setattr(transport, 'managed_urlopen', lambda *a, **k: response({'response': value}))
    with pytest.raises(AlibabaApiRejected, match='起批限制'):
        alibaba_provider(CONFIG).preview_order({}, [])


def test_forwarded_query_denial_keeps_business_reason_and_masks_buyer_token(monkeypatch):
    payload = {'error': {'code': 'order.nopermission.buyer', 'message': '无权读取订单 buyer-token'}}
    monkeypatch.setattr(transport, 'managed_urlopen', lambda *a, **k: response({'response': payload}))
    with pytest.raises(AlibabaApiError) as error:
        alibaba_provider(CONFIG).get_logistics_traces('123')
    assert str(error.value) == '1688 物流轨迹查询失败：order.nopermission.buyer 无权读取订单 ***'


@pytest.mark.parametrize('payload', [
    {'success': False, 'result': [], 'totalRecord': 0},
    {'errorCode': 'gw.APIACLDecline', 'result': [], 'totalRecord': 0},
    {'result': [], 'totalRecord': True},
    {'result': [{'baseInfo': {}}], 'totalRecord': 0},
    {'result': [{}], 'totalRecord': 1},
])
def test_official_order_list_does_not_accept_failed_or_incomplete_payload(payload):
    with pytest.raises(AlibabaApiError):
        _parse_response(ORDER_LIST, payload)


@pytest.mark.parametrize('rows', [[], [{'baseInfo': {'buyerID': 'buyer-a'}}]])
def test_official_order_list_accepts_actual_response_without_success_flag(rows):
    payload = {'result': rows, 'totalRecord': len(rows)}
    assert _parse_response(ORDER_LIST, payload) == payload


def test_same_enterprise_uses_each_buyers_token_for_paginated_query(monkeypatch):
    sent = []
    def send(request, **kwargs):
        params = parse_qs(urlsplit(request.full_url).query)
        sent.append((params['key'], params['access_token'], json.loads(params['_o_args'][0])))
        return response({'response': []})
    monkeypatch.setattr(transport, 'managed_urlopen', send)
    values = {'page': 2, 'pageSize': 20, 'access_token': 'untrusted-argument'}
    for buyer in ('buyer-a', 'buyer-b'):
        transport.request_onebound({**CONFIG, 'access_token': buyer}, ORDER_LIST, values,
                                   namespace='com.alibaba.trade', semantics='read', timeout=20)
    assert sent == [(['vendor-key'], [buyer], {'page': 2, 'pageSize': 20}) for buyer in ('buyer-a', 'buyer-b')]
    assert values['access_token'] == 'untrusted-argument'


def test_unknown_provider_fails_before_any_request():
    with pytest.raises(AlibabaApiError, match='不支持的 1688 接入方案'):
        alibaba_provider({**CONFIG, 'application_type': 'missing'})



@pytest.mark.parametrize('config', [
    CONFIG,
    {**CONFIG, 'credential_type': 'session', 'member_id': 'buyer-a'},
    {**CONFIG, 'credential_type': 'access_token', 'member_id': ''},
    {**DIRECTORY_CONFIG, 'member_id': ''},
])
def test_onebound_writes_require_a_verified_buyer_credential(monkeypatch, config):
    monkeypatch.setattr(transport, 'managed_urlopen', lambda *a, **k: pytest.fail('未核验的授权不得发起写入'))
    provider = alibaba_provider(config)
    with pytest.raises(AlibabaApiRejected, match='已核验的买家授权'):
        provider.create_order({}, [], flow="flow", channel="alipay", external_id="record")
    with pytest.raises(AlibabaApiRejected, match='已核验的买家授权'):
        provider.pay_order("123", channel="alipay", amount_fen=100, request_id="payment")


def test_onebound_self_auth_access_token_supports_trades_without_write_retry(monkeypatch):
    number = '9007199254740994'
    address = {'fullName': '收货人', 'provinceText': '浙江省', 'cityText': '杭州市', 'address': '示例路 10 号'}
    self_auth_config = {**CONFIG, 'credential_type': 'access_token', 'member_id': 'buyer-self'}
    calls = []
    replies = {CREATE: {'success': True, 'orderId': number}, PAY: {'success': True, 'paySuccess': True, 'payChannel': 'ALIPAY'}}

    def send(request, *, request_context, follow_redirects, **kwargs):
        params = parse_qs(urlsplit(request.full_url).query)
        assert params['access_token'] == ['buyer-token'] and 'session' not in params
        assert request_context.semantics == 'write' and request_context.max_attempts == 1 and not follow_redirects
        method = params['method'][0]
        calls.append(method)
        return response({'response': replies[method.split('/', 1)[1]]})

    monkeypatch.setattr(transport, 'managed_urlopen', send)
    provider = alibaba_provider(self_auth_config)
    assert provider.create_order(address, [{'offer_id': '123', 'spec_id': 'spec-a', 'quantity': 1}], flow='general', channel='alipay', external_id='r1') == [number]
    assert provider.pay_order(number, channel='alipay', amount_fen=100, request_id='p1')[0] == 'succeeded'
    assert calls == ['com.alibaba.trade/' + CREATE, 'com.alibaba.trade/' + PAY]


@pytest.mark.parametrize('payment_response', [
    {'paySuccess': True, 'payChannel': 'SHEGOU'},
    {'success': True, 'paySuccess': True, 'payChannel': 'SHEGOU'},
    {'success': True, 'result': {'paySuccess': True, 'payChannel': 'SHEGOU'}},
])
def test_onebound_trades_keep_session_parameters_and_write_semantics(monkeypatch, payment_response):
    number = '9007199254740993'
    address = {'fullName': '收货人', 'provinceText': '浙江省', 'cityText': '杭州市', 'address': '示例路 10 号'}
    calls = []
    replies = {CREATE: {'success': True, 'orderId': number, 'orderList': []}, PAY: payment_response}

    def send(request, *, request_context, follow_redirects, **kwargs):
        parsed = urlsplit(request.full_url)
        assert (parsed.scheme, parsed.netloc, parsed.path) == ('https', 'api-gw.onebound.cn', '/1688/custom')
        params = parse_qs(parsed.query)
        assert params['session'] == [DIRECTORY_CONFIG['access_token']] and 'access_token' not in params
        assert params['key'] == [CONFIG['api_key']] and params['secret'] == [CONFIG['api_secret']]
        assert request.get_method() == 'GET' and params['cache'] == ['no']
        assert request_context.max_attempts == 1 and not follow_redirects
        method = params['method'][0]
        calls.append((method, json.loads(params['_o_args'][0]), request_context.semantics))
        return response({'response': replies[method.split('/', 1)[1]]})

    monkeypatch.setattr(transport, 'managed_urlopen', send)
    provider = alibaba_provider(DIRECTORY_CONFIG)
    cargo = [{'offer_id': '123', 'spec_id': 'spec-a', 'quantity': 2}]
    assert provider.create_order(address, cargo, flow='general', channel='shegou', external_id='record') == [number]
    assert provider.pay_order(number, channel='shegou', amount_fen=1580, request_id='payment')[0] == 'succeeded'
    assert calls == [
        ('com.alibaba.trade/' + CREATE, {'addressParam': address,
         'cargoParamList': [{'offerId': 123, 'specId': 'spec-a', 'quantity': 2}], 'flow': 'general',
         'tradeType': 'assureTrade', 'preSelectPayChannel': 'shegou', 'outOrderId': 'record'}, 'write'),
        ('com.alibaba.trade/' + PAY, {'tradeWithholdPreparePayParam': {
            'orderId': int(number), 'payChannel': 'shegou', 'payAmount': 1580, 'opRequestId': 'payment'}}, 'write'),
    ]


def test_buyer_basic_uses_confirmed_top_level_token(monkeypatch):
    def send(request, **kwargs):
        params = parse_qs(urlsplit(request.full_url).query)
        assert params['method'] == ['com.alibaba.account/alibaba.account.basic']
        assert params['access_token'] == ['buyer-token']
        assert json.loads(params['_o_args'][0]) == {}
        return response({'response': {'memberId': 'buyer-a', 'loginId': '买家甲'}})
    monkeypatch.setattr(transport, 'managed_urlopen', send)
    assert OneBoundAlibabaProvider(CONFIG, 20).get_buyer_profile() == ('buyer-a', '买家甲')


@pytest.mark.parametrize('reason, rejected', [
    ('Request need user authorized 接口文档：https://open.onebound.cn/help/api/1688.custom.html', True),
    ('item not found', False),
])
def test_onebound_2000_error_distinguishes_unauthorized_from_unconfirmed(monkeypatch, reason, rejected):
    monkeypatch.setattr(transport, 'managed_urlopen', lambda *a, **k: response({
        'error_code': '2000', 'error': 'item-not-found vendor-secret buyer-token', 'reason': reason,
    }))
    with pytest.raises(AlibabaApiError) as error:
        alibaba_provider(CONFIG).get_product('123', 'https://detail.1688.com/offer/123.html')
    assert isinstance(error.value, AlibabaApiRejected) is rejected
    assert 'vendor-secret' not in str(error.value) and 'buyer-token' not in str(error.value)


def test_onebound_write_never_retries_on_unauthorized(monkeypatch):
    calls = 0
    def send(request, **kwargs):
        nonlocal calls
        calls += 1
        return response({'error_code': '2000', 'error': 'item-not-found', 'reason': 'Request need user authorized'})
    monkeypatch.setattr(transport, 'managed_urlopen', send)
    synced = []
    provider = alibaba_provider({**DIRECTORY_CONFIG, '_onebound_token_sync': lambda token: synced.append(token) or 'new-token'})
    with pytest.raises(AlibabaApiRejected, match='授权已失效'):
        provider.create_order({'fullName': '收货人'}, [{'offer_id': '123', 'spec_id': 's1', 'quantity': 1}],
                              flow='general', channel='shegou', external_id='record')
    assert calls == 1 and synced == []
