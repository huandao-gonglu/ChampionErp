"""采购同步快照、归属证据、人工冲突、锁定和并发失效。"""

from copy import deepcopy

import pytest

from erp_web.services.alibaba_api_client import ORDER_DETAIL, LOGISTICS_INFO, AlibabaApiError
from erp_web.services.alibaba_purchase_sync_service import sync_purchase
from erp_web.services.fulfillment_service import FulfillmentService
from erp_web.stores.fulfillment_store import FulfillmentStore
from tests.test_alibaba_purchase_query import domain, CONFIG, NUMBER
from tests.test_order_procurement import confirm, purchase_body
from tests.test_fulfillment import Bus


@pytest.fixture
def setup(domain):
    procurement, order, key = domain
    confirm(procurement, order, key)
    request = {**purchase_body(order, key, quantity=2), "purchase_order_number": NUMBER}
    result = procurement.record_purchase(request)
    body = {"order_id": order.identity, "record_id": result["lines"][0]["records"][0]["id"]}
    store = FulfillmentStore(procurement.store.orders)
    fulfillment = FulfillmentService(store, Bus(store), procurement.accounts_provider, procurement.detail,
                                    purchase_progress=procurement.store.purchase_progress, start_worker=False)
    record = procurement.store.purchase_record(body['order_id'], body['record_id'], procurement.accounts_provider())
    class Client:
        calls = []
        order = {'result': {'baseInfo': {'idOfStr': NUMBER, 'status': 'waitbuyerreceive'}, 'productItems': [
            {'productID': '456', 'skuID': record.source.source_sku_id, 'subItemIDString': 'entry', 'quantity': record.quantity}]}}
        logistics = {'result': [{'logisticsId': 'LP1', 'logisticsBillNo': 'YT1', 'logisticsCompanyName': '圆通',
            'orderEntryIds': 'entry', 'logisticsOrderGoods': [{'logisticsId': 'LP1', 'tradeOrderId': NUMBER,
            'tradeOrderItemId': 'entry', 'quantity': record.quantity}]}]}
        def __init__(self, config): pass
        def query(self, api, number):
            self.calls.append(api)
            return deepcopy(self.order if api == ORDER_DETAIL else self.logistics if api == LOGISTICS_INFO else {'logisticsTrace': []})
    def run(**kwargs):
        return sync_purchase(procurement, fulfillment, lambda: CONFIG, body, client_factory=Client, **kwargs)
    return procurement, fulfillment, body, Client, run


def progress(detail):
    return detail['lines'][0]['records'][0]['progress']


def test_persist_auto_fill_and_idempotent_refresh(setup):
    procurement, fulfillment, body, client, run = setup
    result = run(once=True)
    assert progress(result)['state'] == 'synced'
    saved = fulfillment.detail(body['order_id'])
    assert saved['parcels'][0]['tracking_number'] == 'YT1'
    assert progress(procurement.detail(body['order_id'])) == progress(result)
    run(once=True)
    assert len(client.calls) == 3
    run()
    assert fulfillment.detail(body['order_id'])['revision'] == saved['revision']
    assert len(fulfillment.detail(body['order_id'])['parcels']) == 1


@pytest.mark.parametrize('missing', ['sku', 'quantity', 'carrier', 'ambiguous'])
def test_missing_evidence_never_fills(setup, missing):
    _, fulfillment, body, client, run = setup
    if missing == 'sku': client.order['result']['productItems'][0]['skuID'] = 'other'
    if missing == 'quantity': client.logistics['result'][0].pop('logisticsOrderGoods')
    if missing == 'carrier': client.logistics['result'][0].pop('logisticsCompanyName')
    if missing == 'ambiguous': client.order['result']['productItems'] *= 2
    assert progress(run())['state'] == 'pending_assignment'
    assert not fulfillment.detail(body['order_id'])['parcels']


def test_conflict_and_manual_resolution_preserved(setup):
    _, fulfillment, body, _, run = setup
    run()
    current = fulfillment.detail(body['order_id'])
    manual = deepcopy(current['parcels'])
    manual[0]['tracking_number'] = 'MANUAL'
    fulfillment.command('parcels', {'order_id': body['order_id'], 'revision': current['revision'], 'parcels': manual})
    assert progress(run())['state'] == 'manual'
    assert fulfillment.detail(body['order_id'])['parcels'] == manual


def test_new_remote_waybill_reports_conflict(setup):
    _, fulfillment, body, client, run = setup
    run()
    saved = fulfillment.detail(body['order_id'])['parcels']
    client.logistics['result'][0]['logisticsBillNo'] = 'NEW'
    assert progress(run())['state'] == 'conflict'
    assert fulfillment.detail(body['order_id'])['parcels'] == saved


def test_locked_fulfillment_only_saves_snapshot(setup):
    _, fulfillment, body, _, run = setup
    current = fulfillment.detail(body['order_id'])
    fulfillment.store.change(body['order_id'], current['revision'], {'fulfillment_status': 'SHIPPED'})
    result = progress(run())
    assert result['state'] == 'locked' and result['data']['logistics'][0]['tracking_number'] == 'YT1'
    assert not fulfillment.detail(body['order_id'])['parcels']


def test_failure_keeps_previous_snapshot(setup, monkeypatch):
    _, _, _, client, run = setup
    saved = progress(run())['data']
    def fail(*args): raise AlibabaApiError('授权已过期')
    monkeypatch.setattr(client, 'query', fail)
    result = progress(run())
    assert result['state'] == 'error' and result['error'] == '授权已过期'
    assert result['data'] == saved


def test_cancel_during_network_never_writes_parcels(setup, monkeypatch):
    procurement, fulfillment, body, client, run = setup
    original = client.query
    def query(self, api, number):
        if api == LOGISTICS_INFO:
            procurement.cancel_purchase(body)
        return original(self, api, number)
    monkeypatch.setattr(client, 'query', query)
    with pytest.raises(ValueError, match='作废'): run()
    assert not fulfillment.detail(body['order_id'])['parcels']


def test_generation_guard_rejects_stale_response(setup):
    procurement, _, body, _, _ = setup
    store = procurement.store.purchase_progress
    accounts = procurement.accounts_provider()
    generation, value = store.begin(**body, accounts=accounts)
    with pytest.raises(ValueError, match='正在同步'): store.begin(**body, accounts=accounts)
    store.finish(**body, generation=generation, accounts=accounts, value=value)
    store.begin(**body, accounts=accounts)
    with pytest.raises(ValueError, match='过期'): store.finish(**body, generation=generation, accounts=accounts, value=value)


def test_proven_split_shipments_add_incrementally(setup):
    _, fulfillment, body, client, run = setup
    first = client.logistics['result'][0]
    first['logisticsOrderGoods'][0]['quantity'] = 1
    assert progress(run())['state'] == 'synced'
    assert fulfillment.detail(body['order_id'])['parcels'][0]['quantity'] == 1
    second = deepcopy(first)
    second.update(logisticsId='LP2', logisticsBillNo='YT2')
    second['logisticsOrderGoods'][0]['logisticsId'] = 'LP2'
    client.logistics['result'].append(second)
    assert progress(run())['state'] == 'synced'
    parcels = fulfillment.detail(body['order_id'])['parcels']
    assert len(parcels) == 2 and sum(p['quantity'] for p in parcels) == 2


def test_competing_purchase_records_require_assignment(setup):
    procurement, fulfillment, body, client, run = setup
    procurement.cancel_purchase(body)
    record = procurement.detail(body['order_id'])['lines'][0]['records'][0]
    request = {'order_id': body['order_id'], 'line_key': record['line_key'], 'revision': 1,
               'quantity': 1, 'purchase_order_number': NUMBER}
    first = procurement.record_purchase({**request, 'request_id': 'split-first'})
    body['record_id'] = first['lines'][0]['records'][-1]['id']
    procurement.record_purchase({**request, 'request_id': 'split-second'})
    client.order['result']['productItems'][0]['quantity'] = 1
    client.logistics['result'][0]['logisticsOrderGoods'][0]['quantity'] = 1
    result = run()
    current = next(r for r in result['lines'][0]['records'] if r['id'] == body['record_id'])
    assert current['progress']['state'] == 'conflict'
    assert not fulfillment.detail(body['order_id'])['parcels']


def test_facade_registration_queries_once_and_keeps_purchase_on_failure(setup, monkeypatch):
    from types import SimpleNamespace
    from erp_web.facades import order_procurement_facade as facade
    from erp_web.services.alibaba_api_client import AlibabaApiClient
    procurement, fulfillment, body, client, _ = setup
    record = procurement.store.purchase_record(**body, accounts=procurement.accounts_provider())
    monkeypatch.setattr(facade, 'get_context', lambda: SimpleNamespace(order_procurement=procurement,
        fulfillment=fulfillment, config=SimpleNamespace(load_app_config=lambda: {'1688_api': CONFIG})))
    calls = []
    def fail(*args):
        calls.append(True)
        raise AlibabaApiError('授权已过期')
    monkeypatch.setattr(AlibabaApiClient, 'query', fail)
    request = {'order_id': body['order_id'], 'line_key': record.line_key, 'revision': 1,
               'request_id': record.request_id, 'quantity': record.quantity, 'purchase_order_number': NUMBER}
    result = facade.command('record-purchase', request)
    assert result['lines'][0]['records'][0]['status'] == 'purchased'
    assert progress(result)['state'] == 'error'
    facade.command('record-purchase', request)
    assert len(calls) == 1


def test_company_from_same_order_requires_unique_waybill_and_company_id(setup):
    _, fulfillment, body, client, run = setup
    client.logistics['result'][0].pop('logisticsCompanyName')
    client.logistics['result'][0]['logisticsCompanyId'] = '1'
    client.order['result']['nativeLogistics'] = {'logisticsItems': [
        {'logisticsBillNo': 'YT1', 'logisticsCompanyId': '1', 'logisticsCompanyName': '圆通速递(YTO)'}]}
    assert progress(run())['state'] == 'synced'
    assert fulfillment.detail(body['order_id'])['parcels'][0]['carrier'] == '圆通'
