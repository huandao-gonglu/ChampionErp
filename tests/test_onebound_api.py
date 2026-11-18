"""万邦适配只用替身：显式通道、协议归一、错误脱敏和写入语义。"""
import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from erp_web.services import onebound_api_transport as transport
from erp_web.services.alibaba_api_client import alibaba_provider, AlibabaApiError, AlibabaApiRejected, ORDER_DETAIL, LOGISTICS_INFO, LOGISTICS_TRACE, _parse_response
from erp_web.services.alibaba_api_client import ADDRESSES, PREVIEW, CREATE, ORDER_LIST, PAY_STATUS, PAY


CONFIG = {'application_type': 'onebound', 'api_key': 'vendor-key', 'api_secret': 'vendor-secret', 'access_token': 'buyer-token'}


def response(value):
    return io.BytesIO(json.dumps(value).encode())


@pytest.mark.parametrize('api', [ADDRESSES, PREVIEW, CREATE, ORDER_LIST, PAY_STATUS, PAY])
def test_custom_transport_preserves_arguments_and_write_semantics(monkeypatch, api):
    calls = []
    def send(request, **kwargs):
        calls.append((request, kwargs))
        return response({'response': {'success': True, 'result': {}}})
    monkeypatch.setattr(transport, 'managed_urlopen', send)
    values = {'cargoParamList': [{'offerId': 123, 'specId': '蓝色', 'quantity': 2}]}
    transport.request_onebound(CONFIG, api, values, namespace='com.alibaba.trade',
                               semantics='write' if api in {CREATE, PAY} else 'read', timeout=20)
    assert len(calls) == 1
    request, kwargs = calls[0]
    parsed = urlsplit(request.full_url)
    params = parse_qs(parsed.query)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ('https', 'api-gw.onebound.cn', '/1688/custom')
    assert params['key'] == ['vendor-key'] and params['secret'] == ['vendor-secret'] and 'session' not in params
    assert params['method'] == ['com.alibaba.trade/' + api]
    expected = values if api == ORDER_LIST else {**values, 'access_token': 'buyer-token'}
    assert json.loads(params['_o_args'][0]) == expected
    assert params.get('access_token') == (['buyer-token'] if api == ORDER_LIST else None)
    assert params['cache'] == ['no'] and '_aop_signature' not in params
    context = kwargs['request_context']
    assert context.semantics == ('write' if api in {CREATE, PAY} else 'read')
    assert context.max_attempts == 1 and not kwargs['follow_redirects']
    assert not any(secret in repr(context) for secret in ('vendor-key', 'vendor-secret', 'buyer-token'))


def test_product_order_and_logistics_use_same_explicit_provider(monkeypatch):
    calls = []
    def send(request, **kwargs):
        params = parse_qs(urlsplit(request.full_url).query)
        assert json.loads(params['_o_args'][0])['access_token'] == 'buyer-token'
        calls.append(params['method'][0])
        if 'queryProductDetail' in calls[-1]:
            return response({'response': {'result': {'success': True, 'result': {'offerId': 123}}}})
        if calls[-1].endswith(ORDER_DETAIL):
            return response({'response': {'baseInfo': {'idOfStr': '123', 'status': 'success'}}})
        if calls[-1].endswith(LOGISTICS_TRACE):
            return response({'response': {'logisticsTrace': []}})
        return response({'response': {'success': True, 'result': []}})
    monkeypatch.setattr(transport, 'managed_urlopen', send)
    client = alibaba_provider(CONFIG)
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



def test_unconfirmed_onebound_writes_fail_before_network(monkeypatch):
    monkeypatch.setattr(transport, 'managed_urlopen', lambda *args, **kwargs: pytest.fail("未确认的接口不得发请求"))
    provider = alibaba_provider(CONFIG)
    with pytest.raises(AlibabaApiRejected, match="创建采购订单.*尚未确认"):
        provider.create_order({}, [], flow="flow", channel="alipay", external_id="record")
    with pytest.raises(AlibabaApiRejected, match="免密支付.*尚未确认"):
        provider.pay_order("123", channel="alipay", amount_fen=100, request_id="payment")



def test_buyer_basic_uses_confirmed_top_level_token(monkeypatch):
    def send(request, **kwargs):
        params = parse_qs(urlsplit(request.full_url).query)
        assert params['method'] == ['com.alibaba.account/alibaba.account.basic']
        assert params['access_token'] == ['buyer-token']
        assert json.loads(params['_o_args'][0]) == {}
        return response({'response': {'memberId': 'buyer-a'}})
    monkeypatch.setattr(transport, 'managed_urlopen', send)
    assert alibaba_provider(CONFIG).get_buyer_id() == 'buyer-a'
