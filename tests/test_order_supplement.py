"""补单登记、只读核验、防重及沿用采购物流同步的回归。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import pytest

from erp_web.services.order_supplement_service import OrderSupplementService
from erp_web.services.alibaba_api_client import ORDER_DETAIL, LOGISTICS_INFO
from erp_web.services.alibaba_purchase_sync_service import sync_purchase
from erp_web.services.fulfillment_service import FulfillmentService
from erp_web.stores.fulfillment_store import FulfillmentStore
from tests.test_order_procurement import domain, confirm, purchase_body
from tests.test_fulfillment import Bus
from tests.receiving_warehouse_support import assign_receiving, receiving_service


@pytest.fixture
def supplement(domain):
    procurement, order, key = domain
    confirm(procurement, order, key)
    procurement.record_purchase(purchase_body(order, key, quantity=2))
    assign_receiving(receiving_service(procurement), order)
    config = {'account': 'fixture'}
    class Client:
        payload = {'result': {'baseInfo': {'idOfStr': '555', 'status': 'waitsellersend'}, 'productItems': [
            {'productID': '789', 'skuID': '456', 'specId': 'spec', 'subItemIDString': 'entry', 'quantity': 2,
             'name': '补采商品', 'skuInfos': [{'name': '颜色', 'value': '蓝色'}]}]}}
        calls = []
        hook = None
        def __init__(self, config): pass
        def query(self, api, number):
            self.calls.append((api, number))
            if self.hook:
                self.hook()
            if api == ORDER_DETAIL:
                return deepcopy(self.payload)
            if api == LOGISTICS_INFO:
                return {'result': [{'logisticsId': 'lp', 'logisticsBillNo': 'YT555', 'logisticsCompanyName': '圆通',
                    'orderEntryIds': 'entry', 'logisticsOrderGoods': [{'tradeOrderId': '555',
                    'tradeOrderItemId': 'entry', 'logisticsId': 'lp', 'quantity': 2}]}]}
            return {'logisticsTrace': []}
    service = OrderSupplementService(procurement, lambda: config, client_factory=Client)
    body = {'order_id': order.identity, 'line_key': key, 'revision': 1, 'purchase_order_number': '555'}
    return service, procurement, order, body, Client, config


def recording(setup):
    service, _, _, body, _, _ = setup
    candidate = service.lookup(body)['candidates'][0]
    return {**body, 'candidate_key': candidate['key'], 'request_id': 'supplement-1', 'confirmed': True, 'receiving_revision': 1}


def test_full_purchase_can_supplement_without_changing_original(supplement):
    service, procurement, _, _, _, _ = supplement
    body = recording(supplement)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: service.record(body), range(2)))
    line = procurement.detail(body['order_id'])['lines'][0]
    assert len(line['records']) == 2
    original, added = line['records']
    assert original['status'] == 'purchased' and not original['supplemental']
    assert added['supplemental'] and added['quantity'] == 2 and added['purchase_order_number'] == '555'
    assert added['source']['source_sku_id'] == '456'
    assert line['selection']['source'] == original['source']
    with pytest.raises(ValueError, match='已登记'):
        service.record({**body, 'request_id': 'different'})


@pytest.mark.parametrize('status', ['waitbuyerpay', 'cancel', 'terminated', 'unknown'])
def test_only_paid_valid_remote_orders(supplement, status):
    service, _, _, body, client, _ = supplement
    client.payload['result']['baseInfo']['status'] = status
    with pytest.raises(ValueError, match='已付款'):
        service.lookup(body)


def test_remote_changes_and_duplicate_items_require_new_confirmation(supplement):
    service, _, _, _, client, _ = supplement
    body = recording(supplement)
    client.payload['result']['productItems'][0]['quantity'] = 1
    with pytest.raises(ValueError, match='已变化'):
        service.record(body)
    client.payload['result']['productItems'][0]['quantity'] = 2
    client.payload['result']['productItems'] *= 2
    with pytest.raises(ValueError, match='规格不唯一'):
        service.record(body)


def test_wrong_shop_revision_and_credential_change_do_not_register(supplement):
    service, procurement, order, body, client, config = supplement
    with pytest.raises(ValueError):
        service.lookup({**body, 'revision': 99})
    assert not client.calls
    client.hook = lambda self: config.update(account='changed')
    with pytest.raises(ValueError, match='授权已变化'):
        service.lookup(body)
    client.hook = None
    procurement.accounts_provider = lambda: {'yandex': 'other'}
    with pytest.raises(ValueError, match='不属于'):
        service.lookup(body)


def test_supplement_cannot_exceed_sales_quantity_or_enter_started_fulfillment(supplement):
    service, procurement, order, _, client, _ = supplement
    client.payload['result']['productItems'][0]['quantity'] = 3
    with pytest.raises(ValueError, match='超过'):
        service.record(recording(supplement))
    client.payload['result']['productItems'][0]['quantity'] = 2
    store = FulfillmentStore(procurement.store.orders)
    current = store.ensure(order)
    store.change(order.identity, current['revision'], {'crossborderbus_order_id': 99})
    with pytest.raises(ValueError, match='报单已开始'):
        service.record(recording(supplement))


def test_new_purchase_tracks_and_uses_its_own_waybill(supplement):
    service, procurement, order, _, client, config = supplement
    detail = service.record(recording(supplement))
    record = next(r for r in detail['lines'][0]['records'] if r['supplemental'])
    store = FulfillmentStore(procurement.store.orders)
    fulfillment = FulfillmentService(store, Bus(store), procurement.accounts_provider, procurement.detail,
                                    purchase_progress=procurement.store.purchase_progress, start_worker=False)
    body = {'order_id': order.identity, 'record_id': record['id']}
    result = sync_purchase(procurement, fulfillment, lambda: config, body, client_factory=client)
    assert result['lines'][0]['records'][1]['progress']['state'] == 'synced'
    parcels = fulfillment.detail(order.identity)['parcels']
    assert len(parcels) == 1 and parcels[0]['purchase_record_id'] == record['id']
    assert parcels[0]['tracking_number'] == 'YT555'
    assert fulfillment.parcels_payload(parcels, result, complete=True)[0]['from_order'] == '555'
    original = result['lines'][0]['records'][0]
    wrong = [{**parcels[0], 'purchase_record_id': original['id']}]
    with pytest.raises(ValueError, match='补单包裹尚未齐全'):
        fulfillment.parcels_payload(wrong, result, complete=True)


def test_facade_registers_then_syncs_once(supplement, monkeypatch):
    from erp_web.facades import order_procurement_facade as facade
    service, procurement, _, _, _, config = supplement
    monkeypatch.setattr(facade, 'OrderSupplementService', lambda *args: service)
    calls = []
    monkeypatch.setattr(facade, 'sync_purchase', lambda body, **kwargs: calls.append((body, kwargs['once'])) or procurement.detail(body['order_id']))
    context = SimpleNamespace(order_procurement=procurement, config=SimpleNamespace(load_app_config=lambda: {'1688_api': config}))
    result = facade.command('supplement-record', recording(supplement), context=context)
    assert calls == [({'order_id': result['order']['id'], 'record_id': result['lines'][0]['records'][1]['id']}, True)]


def test_warehouse_version_and_missing_warehouse_block_save(supplement):
    service, procurement, _, _, _, _ = supplement
    body = recording(supplement)
    with pytest.raises(ValueError, match='收货仓'):
        service.record({**body, 'receiving_revision': 0})
    with procurement.store.orders.connect() as conn:
        conn.execute('DELETE FROM order_receiving_warehouses WHERE order_id=?', (body['order_id'],))
        conn.commit()
    with pytest.raises(ValueError, match='确定本单收货仓'):
        service.record(body)


def test_failed_initial_sync_keeps_supplement_for_later_tracking(supplement, monkeypatch):
    from erp_web.facades import order_procurement_facade as facade
    service, procurement, _, _, _, config = supplement
    monkeypatch.setattr(facade, 'OrderSupplementService', lambda *args: service)
    def fail(*args, **kwargs):
        raise ValueError('模拟物流服务不可用')
    monkeypatch.setattr(facade, 'sync_purchase', fail)
    context = SimpleNamespace(order_procurement=procurement, config=SimpleNamespace(load_app_config=lambda: {'1688_api': config}))
    result = facade.command('supplement-record', recording(supplement), context=context)
    assert len(result['lines'][0]['records']) == 2
    assert result['lines'][0]['records'][1]['supplemental']


def test_supplement_http_contract_and_external_interruption(supplement, monkeypatch):
    from erp_web.http_route_units import order_routes
    from erp_web.schemas.external_requests import ExternalRequestBlocked, RequestFailure
    from erp_web.schemas.requests import validate_request_payload
    body = recording(supplement)
    path = '/api/orders/supplement-record'
    assert validate_request_payload(body, endpoint=path) == body
    responses = []
    handler = SimpleNamespace(path=path, read_body=lambda: body, send_json=lambda value, status=200: responses.append((status, value)))
    def blocked(*args):
        raise ExternalRequestBlocked(RequestFailure('BLOCKED', '测试中断'))
    monkeypatch.setattr(order_routes.procurement, 'command', blocked)
    order_routes.POST_HANDLERS[path](handler)
    assert responses[0][0] == 409


def test_unshipped_supplement_remains_in_package_status_hint(supplement):
    service, procurement, _, _, _, _ = supplement
    result = service.record(recording(supplement))
    packages = result['order']['purchase_tracking']['packages']
    assert len(packages) == 2
    assert {number for item in packages for number in item['order_numbers']} == {'PO-1', '555'}
    assert all(item['status_label'] == '状态待查询' and not item['tracking_number'] for item in packages)
