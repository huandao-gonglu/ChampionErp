"""支付仅用替身：签约门槛、金额与商品核验、并发防重和重启恢复。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
import io
import json
from urllib.parse import parse_qs

import pytest

from erp_web.services import alibaba_api_client as api
from erp_web.services.alibaba_purchase_payment import payment_agreement, payment_receipt
from erp_web.services.alibaba_api_client import alibaba_provider, PAY, PAY_STATUS
from erp_web.services.alibaba_self_purchase_service import account_scope
from erp_web.stores.alibaba_self_purchase_store import AlibabaSelfPurchaseStore
from tests.test_alibaba_self_purchase import setup_purchase, domain, preview, create, NUMBER, CONFIG
from tests.test_alibaba_batch_purchase import batch, groups


def agreement(signed='true', bound='true', channel='shegou'):
    return {'success': True, 'result': {'paymentAgreements': [
        {'payChannel': channel.upper(), 'signedStatus': signed, 'bindingStatus': bound,
         'signUrl': 'https://tradeconfig.1688.com/foundation/withhold_manage.htm'}]}}


@pytest.fixture(params=['shegou', 'alipay'])
def payment(setup_purchase, request):
    service, client, config, _ = setup_purchase
    client.channel = request.param
    record = service.create({'preview_id': preview(service)['id'], 'pay_channel': client.channel})['record']
    client.order['result']['baseInfo']['totalAmount'] = '15.80'
    client.order['result']['productItems'][0]['quantity'] = 1
    original = client.call
    client.agreement = agreement(channel=client.channel)
    client.payment_response = {'success': True, 'result': {'paySuccess': True, 'payChannel': client.channel.upper()}}
    client.payment_error = None
    def call(name, values):
        if name not in {PAY, PAY_STATUS}:
            return original(name, values)
        client.calls.append((name, deepcopy(values)))
        if name == PAY_STATUS:
            return deepcopy(client.agreement)
        if client.payment_error:
            raise client.payment_error
        return deepcopy(client.payment_response)
    client.call = call
    return service, client, config, {'preview_id': record['id'], 'confirmed_total_fen': 1580}


def test_selected_channel_with_frozen_amount_and_durable_request(payment):
    service, client, config, body = payment
    first = service.pay(body)['record']
    assert first['payment']['state'] == 'succeeded'
    assert first['state'] == 'created' and first['purchase_record_id']
    assert service.pay(body)['record'] == first
    params = [p for n, p in client.calls if n == PAY]
    assert params == [{'tradeWithholdPreparePayParam': {'orderId': int(NUMBER), 'payChannel': client.channel,
        'payAmount': 1580, 'opRequestId': first['payment']['request_id']}}]
    assert service.store.get(body['preview_id'], account_scope(config))['payment'] == first['payment']
    binding = service.store.get(body['preview_id'], account_scope(config))['payload']['binding']
    assert service.options(binding['order_id'], binding['line_key'])['records'][0]['payment'] == first['payment']


@pytest.mark.parametrize('signed,bound', [('false', 'true'), ('true', 'false'), (1, True)])
def test_unsigned_or_unbound_never_pays(payment, signed, bound):
    service, client, _, body = payment
    client.agreement = agreement(signed, bound, client.channel)
    assert not service.payment_status({'pay_channel': client.channel})['agreement']['ready']
    with pytest.raises(ValueError, match='签约'):
        service.pay(body)
    assert not any(n == PAY for n, _ in client.calls)


def test_other_channel_agreement_cannot_authorize_selected_channel(payment):
    service, client, _, body = payment
    other = 'alipay' if client.channel == 'shegou' else 'shegou'
    client.agreement = agreement(signed='false', channel=client.channel)
    client.agreement['result']['paymentAgreements'] += agreement(channel=other)['result']['paymentAgreements']
    assert service.payment_status({'pay_channel': other})['agreement']['ready']
    assert not service.payment_status({'pay_channel': client.channel})['agreement']['ready']
    with pytest.raises(ValueError, match='签约'):
        service.pay(body)
    assert not any(n == PAY for n, _ in client.calls)


def test_caller_cannot_override_saved_channel_and_store_rechecks_it(payment):
    service, client, config, body = payment
    other = 'alipay' if client.channel == 'shegou' else 'shegou'
    with pytest.raises(ValueError):
        service.pay({**body, 'pay_channel': other})
    with pytest.raises(ValueError, match='已变化'):
        service.store.claim_payment(body['preview_id'], account_scope(config), 1580, other)
    assert not any(n == PAY for n, _ in client.calls)


@pytest.mark.parametrize('change', ['amount', 'missing_amount', 'sku', 'quantity', 'already_paid', 'number', 'confirmed_amount', 'account'])
def test_payment_preconditions_prevent_wrong_payment(payment, change):
    service, client, config, body = payment
    if change == 'amount': client.order['result']['baseInfo']['totalAmount'] = '15.81'
    elif change == 'missing_amount': del client.order['result']['baseInfo']['totalAmount']
    elif change == 'sku': client.order['result']['productItems'][0]['skuID'] = 'other'
    elif change == 'quantity': client.order['result']['productItems'][0]['quantity'] = 2
    elif change == 'already_paid': client.order['result']['baseInfo']['status'] = 'waitsellersend'
    elif change == 'number': client.order['result']['baseInfo']['idOfStr'] = '123'
    elif change == 'confirmed_amount': body['confirmed_total_fen'] = 1581
    else: config['access_token'] = 'different'
    with pytest.raises(ValueError): service.pay(body)
    assert not any(n == PAY for n, _ in client.calls)


@pytest.mark.parametrize('failure', ['timeout', 'false', 'malformed', 'other_channel'])
def test_uncertain_payment_survives_restart_and_never_replays(payment, failure):
    service, client, config, body = payment
    if failure == 'timeout': client.payment_error = TimeoutError('秘密错误不应显示')
    elif failure == 'false': client.payment_response['result']['paySuccess'] = False
    elif failure == 'malformed': client.payment_response = {'success': True}
    else: client.payment_response['result']['payChannel'] = 'ALIPAY' if client.channel == 'shegou' else 'SHEGOU'
    first = service.pay(body)['record']
    assert first['payment']['state'] == 'unknown'
    assert '秘密' not in first['payment']['message']
    service.store = AlibabaSelfPurchaseStore(service.store.path)
    assert service.pay(body)['record']['payment'] == first['payment']
    queried = service.reconcile({'preview_id': body['preview_id']})['record']
    assert queried['payment']['state'] == 'unknown'
    client.order['result']['baseInfo']['status'] = 'waitsellersend'
    queried = service.reconcile({'preview_id': body['preview_id']})['record']
    assert queried['payment']['state'] == 'settled'
    assert service.pay(body)['record']['payment']['state'] == 'settled'
    assert len([n for n, _ in client.calls if n == PAY]) == 1
    # 模拟迟到的失败保存，不能抹掉已核验结果。
    assert service.store.finish_payment(body['preview_id'], account_scope(config), first['payment']['request_id'],
        'unknown', '迟到回执')['payment']['state'] == 'settled'


def test_concurrent_payment_has_one_owner(payment):
    service, client, _, body = payment
    original_query = client.query
    barrier = Barrier(2)
    def query(*args):
        result = original_query(*args)
        barrier.wait(timeout=5)
        return result
    client.query = query
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.pay(body)['record'], range(2)))
    assert len([n for n, _ in client.calls if n == PAY]) == 1
    assert {r['payment']['state'] for r in results} <= {'submitting', 'succeeded'}


def test_crash_after_claim_does_not_replay_on_restart(payment):
    service, client, config, body = payment
    row, claimed = service.store.claim_payment(body['preview_id'], account_scope(config), 1580, client.channel)
    assert claimed
    service.store = AlibabaSelfPurchaseStore(service.store.path)
    assert service.pay(body)['record']['payment'] == row['payment']
    assert not any(n == PAY for n, _ in client.calls)


def test_payment_intent_is_saved_before_network_and_other_results_cannot_erase_it(payment):
    service, client, config, body = payment
    original = client.call
    def call(name, values):
        if name == PAY:
            row = service.store.get(body['preview_id'], account_scope(config))
            assert row['payment']['state'] == 'submitting'
            service.store.finish(row['id'], row['account'], row['state'], row['result'])
            assert service.store.get(row['id'], row['account'])['payment'] == row['payment']
        return original(name, values)
    client.call = call
    assert service.pay(body)['record']['payment']['state'] == 'succeeded'


def test_api_permission_rejection_is_visible_without_claiming_payment_success(payment):
    service, client, _, body = payment
    client.payment_error = api.AlibabaApiRejected('支付接口未授权（gw.APIACLDecline）')
    value = service.pay(body)['record']['payment']
    assert value['state'] == 'rejected' and 'gw.APIACLDecline' in value['message']
    service.pay(body)
    assert len([n for n, _ in client.calls if n == PAY]) == 1


def test_native_nested_models_and_untrusted_sign_url():
    native = agreement()
    native['result'] = {'success': True, 'result': native['result']}
    assert payment_agreement(native, 'shegou')['ready']
    native['result']['result']['paymentAgreements'][0]['signUrl'] = 'https://1688.com.attacker.test/pay'
    assert payment_agreement(native, 'shegou')['sign_url'] == ''
    assert payment_receipt({'success': True, 'result': {'success': True, 'result': {'paySuccess': 'true', 'payChannel': 'SHEGOU'}}}, 'shegou')[0] == 'succeeded'


@pytest.mark.parametrize('channel', ['shegou', 'alipay'])
def test_transport_signs_payment_as_non_retrying_write(monkeypatch, channel):
    calls = []
    def send(req, **kwargs):
        calls.append((req, kwargs))
        return io.BytesIO(b'{"success":true,"result":{}}')
    monkeypatch.setattr(api, 'managed_urlopen', send)
    client = alibaba_provider(CONFIG)
    value = {'orderId': int(NUMBER), 'payChannel': channel, 'payAmount': 1580, 'opRequestId': 'fixed'}
    client._request(PAY, {'tradeWithholdPreparePayParam': value}, semantics="write")
    request, kwargs = calls[0]
    assert kwargs['request_context'].semantics == 'write'
    assert kwargs['request_context'].max_attempts == 1
    assert kwargs['follow_redirects'] is False
    form = parse_qs(request.data.decode())
    assert json.loads(form['tradeWithholdPreparePayParam'][0]) == value
    assert form['_aop_signature']
    client._request(PAY_STATUS, {})
    assert calls[1][1]['request_context'].semantics == 'read'


@pytest.mark.parametrize('channel', ['shegou', 'alipay'])
def test_multi_sku_group_pays_once_for_the_whole_order(batch, channel):
    service, client, _, order, _ = batch
    record = next(row for row in groups(batch) if len(row['preview']['items']) == 2)
    service.create({'preview_id': record['id'], 'pay_channel': channel})
    original_call, original_query = client.call, client.query
    def call(name, values):
        if name == PAY_STATUS:
            return agreement(channel=channel)
        if name == PAY:
            client.calls.append((name, deepcopy(values)))
            return {'success': True, 'result': {'paySuccess': True, 'payChannel': channel.upper()}}
        return original_call(name, values)
    def query(*args):
        result = original_query(*args)
        result['result']['baseInfo']['totalAmount'] = '15.80'
        return result
    client.call, client.query = call, query
    body = {'preview_id': record['id'], 'confirmed_total_fen': 1580}
    result = service.pay(body)['record']
    assert result['payment']['state'] == 'succeeded'
    assert len(result['purchase_record_ids']) == 1
    purchase = service.procurement.store.purchase_record(order.identity, result['purchase_record_ids'][0], service.procurement.accounts_provider())
    assert len(purchase.items) == 2
    service.pay(body)
    assert len([n for n, _ in client.calls if n == PAY]) == 1

def test_unsupported_vendor_payment_is_recorded_as_rejected(payment, monkeypatch):
    from erp_web.services import onebound_api_transport as transport
    config = {"application_type": "onebound", "api_key": "key", "api_secret": "secret", "access_token": "session"}
    service, client, _, body = payment
    client.pay_order = alibaba_provider(config).pay_order
    monkeypatch.setattr(transport, 'managed_urlopen', lambda *a, **k: pytest.fail("不应发送万邦付款请求"))
    first = service.pay(body)['record']
    assert first['payment']['state'] == 'rejected'
    assert '万邦免密支付' in first['payment']['message']
    assert service.pay(body)['record']['payment'] == first['payment']


def test_old_scope_and_unknown_payment_survive_buyer_merge_and_restart(payment):
    service, client, config, body = payment
    client.payment_error = TimeoutError('模拟未确认回执')
    first = service.pay(body)['record']
    old_scope = account_scope(config)
    assert first['payment']['state'] == 'unknown'
    # 模拟既有 v3 库升级，不丢失支付意图与原请求编号。
    with service.store.connect() as conn:
        conn.execute('DROP TABLE alibaba_purchase_accounts')
        conn.execute('PRAGMA user_version=3')
        conn.commit()
    service.store = AlibabaSelfPurchaseStore(service.store.path)
    service.store.merge_account_scopes('shared-buyer', [old_scope], 'buyer-a')
    config.update(account_scope='shared-buyer', account_scope_aliases=[old_scope], member_id='buyer-a')
    service.store = AlibabaSelfPurchaseStore(service.store.path)
    assert service.store.get(body['preview_id'], old_scope)['payment'] == first['payment']
    assert service.pay(body)['record']['payment'] == first['payment']
    assert len([name for name, _ in client.calls if name == PAY]) == 1
    with pytest.raises(ValueError, match='不能合并到其他'):
        service.store.merge_account_scopes('other-buyer', [old_scope], 'buyer-b')
