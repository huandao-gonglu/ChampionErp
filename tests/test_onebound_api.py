"""万邦适配只用替身：显式通道、协议归一、错误脱敏和写入语义。"""
import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from erp_web.services import onebound_api_transport as transport
from erp_web.services.alibaba_api_client import AlibabaApiClient, AlibabaApiError, AlibabaApiRejected, ORDER_DETAIL, LOGISTICS_INFO, LOGISTICS_TRACE, _parse_response
from erp_web.services.alibaba_self_purchase_client import AlibabaSelfPurchaseClient, ADDRESSES, PREVIEW, CREATE, ORDER_LIST, PAY_URL, PAY_STATUS, PAY


CONFIG = {'application_type': 'onebound', 'api_key': 'vendor-key', 'api_secret': 'vendor-secret', 'access_token': 'buyer-token'}


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
    assert params['key'] == ['vendor-key'] and params['secret'] == ['vendor-secret'] and 'session' not in params
    method = 'alibaba.alipay.url.get' if api == PAY_URL else api
    assert params['method'] == ['com.alibaba.trade/' + method]
    assert json.loads(params['_o_args'][0]) == {**({'orderIdList': [123]} if api == PAY_URL else values), 'access_token': 'buyer-token'}
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
        return response({'response': {'success': True, 'result': []}})
    monkeypatch.setattr(transport, 'managed_urlopen', send)
    client = AlibabaApiClient(CONFIG)
    assert client.product_detail('123')['offerId'] == 123
    client.query(ORDER_DETAIL, '123')
    client.query(LOGISTICS_INFO, '123')
    client.query(LOGISTICS_TRACE, '123', logistics_id='456')
    assert calls == ['com.alibaba.fenxiao.crossborder/product.search.queryProductDetail',
                     'com.alibaba.trade/' + ORDER_DETAIL, 'com.alibaba.logistics/' + LOGISTICS_INFO,
                     'com.alibaba.logistics/' + LOGISTICS_TRACE]


@pytest.mark.parametrize('code,rejected', [('4005', True), ('4016', True), ('4001', False), ('4017', False), ('5000', False)])
def test_vendor_failures_are_safe_and_do_not_claim_network_writes_failed(monkeypatch, code, rejected):
    monkeypatch.setattr(transport, 'managed_urlopen', lambda *a, **k: response({
        'error_code': code, 'error': 'vendor-secret buyer-token', 'call_args': CONFIG}))
    with pytest.raises(AlibabaApiError) as error:
        AlibabaSelfPurchaseClient(CONFIG).call(CREATE, {})
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
        AlibabaSelfPurchaseClient(CONFIG).call(CREATE, {})


def test_forwarded_query_denial_keeps_business_reason_and_masks_buyer_token(monkeypatch):
    payload = {'error': {'code': 'order.nopermission.buyer', 'message': '无权读取订单 buyer-token'}}
    monkeypatch.setattr(transport, 'managed_urlopen', lambda *a, **k: response({'response': payload}))
    with pytest.raises(AlibabaApiError) as error:
        AlibabaApiClient(CONFIG).query(LOGISTICS_TRACE, '123')
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
        sent.append((params['key'], json.loads(params['_o_args'][0])))
        return response({'response': []})
    monkeypatch.setattr(transport, 'managed_urlopen', send)
    values = {'page': 2, 'pageSize': 20, 'access_token': 'untrusted-argument'}
    for buyer in ('buyer-a', 'buyer-b'):
        AlibabaSelfPurchaseClient({**CONFIG, 'access_token': buyer}).call(ORDER_LIST, values)
    assert sent == [(['vendor-key'], {**values, 'access_token': buyer}) for buyer in ('buyer-a', 'buyer-b')]
    assert values['access_token'] == 'untrusted-argument'


def test_unknown_provider_fails_before_any_request():
    with pytest.raises(AlibabaApiError, match='不支持的 1688 接入方案'):
        AlibabaApiClient({**CONFIG, 'application_type': 'missing'})
