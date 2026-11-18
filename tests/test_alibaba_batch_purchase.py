"""多 SKU 分组采购：原子数量占位、部分成功、重启核验及按商品回填物流。"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import pytest

from tests.alibaba_test_provider import AlibabaFixtureProvider

from erp_web.schemas.orders import OrderLine
from erp_web.schemas.order_procurement import SalesSkuBinding, order_line_key
from erp_web.services.alibaba_api_client import AlibabaApiRejected, ORDER_DETAIL, LOGISTICS_INFO
from erp_web.services.alibaba_api_client import PREVIEW, CREATE, ORDER_LIST
from erp_web.services.alibaba_self_purchase_service import account_scope
from erp_web.stores.alibaba_self_purchase_store import AlibabaSelfPurchaseStore
from tests.test_alibaba_self_purchase import setup_purchase, domain, preview_payload, preview, create, NUMBER


@pytest.fixture
def batch(setup_purchase):
    service, old_client, config, (order, key) = setup_purchase
    with service.procurement.store.orders.connect() as conn:
        original = SalesSkuBinding.model_validate_json(conn.execute('SELECT binding_json FROM sales_sku_bindings LIMIT 1').fetchone()[0])
    keys = [key]
    for index in (2, 3):
        line = OrderLine(sku=f'SALE-{index}', remote_id=f'SALE-{index}', title=f'商品 {index}', quantity=2)
        order.items.append(line)
        keys.append(order_line_key(line))
    with service.procurement.store.orders.connect() as conn:
        conn.execute('UPDATE orders SET snapshot=? WHERE id=?', (order.model_dump_json(), order.identity))
        conn.commit()
    for index in (2, 3):
        offer = '12345' if index == 2 else '22222'
        source = original.source.model_copy(update={'supplier': 'A' if index == 2 else 'B',
            'product_url': f'https://detail.1688.com/offer/{offer}.html', 'source_offer_id': offer,
            'source_sku_id': str(67889 + index), 'source_spec_id': f'spec-{index}', 'specification': f'规格 {index}'})
        binding = original.model_copy(update={'id': '', 'seller_sku': f'SALE-{index}', 'remote_id': f'SALE-{index}', 'sku_id': f'internal-{index}', 'source': source})
        service.procurement.store.add_bindings([binding])
        service.procurement.select_source({'order_id': order.identity, 'line_key': keys[index-1], 'revision': 0, 'candidate_id': binding.identity})
    class Client(AlibabaFixtureProvider):
        calls = []
        fail_offer = ''
        reject_offer = ''
        callback = None
        orders = {}
        def call(self, api, params):
            self.calls.append((api, deepcopy(params)))
            if api == PREVIEW:
                if self.callback: self.callback()
                groups = {}
                for cargo in params['cargoParamList']:
                    offer = str(cargo['offerId'])
                    group = groups.setdefault(offer, deepcopy(preview_payload()['orderPreviewResuslt'][0]))
                    if 'added' not in group:
                        group.update(cargoList=[], added=True)
                    sku = '67890' if cargo['specId'] == 'spec-one' else '67891' if cargo['specId'] == 'spec-2' else '67892'
                    group['cargoList'].append({'offerId': offer, 'skuId': sku, 'specId': cargo['specId']})
                return {'orderPreviewResuslt': list(groups.values())}
            if api == CREATE:
                offer = str(params['cargoParamList'][0]['offerId'])
                if self.reject_offer == offer: raise AlibabaApiRejected('库存不足')
                number = NUMBER if offer == '12345' else str(int(NUMBER) + 1)
                self.orders[params['outOrderId']] = (number, deepcopy(params))
                if self.fail_offer == offer: raise TimeoutError('网络超时')
                return {'result': {'orderId': number}}
            if api == ORDER_LIST:
                entry = self.orders.get(params['outOrderId'])
                return {'result': {'orderList': [{'baseInfo': {'idOfStr': entry[0], 'outOrderId': params['outOrderId']}}] if entry else []}}
            pytest.fail(api)
        def query(self, api, number):
            _, params = next(value for value in self.orders.values() if value[0] == number)
            return {'result': {'baseInfo': {'idOfStr': number, 'status': 'waitbuyerpay'}, 'productItems': [
                {'productID': item['offerId'], 'specId': item['specId'],
                 'skuID': '67890' if item['specId'] == 'spec-one' else '67891' if item['specId'] == 'spec-2' else '67892',
                 'quantity': item['quantity'], 'subItemIDString': str(index)} for index, item in enumerate(params['cargoParamList'])]}}
    client = Client()
    service.client_factory = lambda _: client
    body = {'order_id': order.identity, 'receiving_revision': 1,
            'items': [{'line_key': key, 'candidate_id': '1', 'quantity': 2} for key in keys]}
    return service, client, config, order, body


def groups(batch):
    return batch[0].batch_preview(batch[4])['records']


def test_native_grouping_and_atomic_backfill(batch):
    service, client, _, order, _ = batch
    records = groups(batch)
    assert sorted(len(r['preview']['items']) for r in records) == [1, 2]
    assert not any(api == CREATE for api, _ in client.calls)
    for record in records:
        result = create(service, record)
        assert result['state'] == 'created'
        assert len(result['purchase_record_ids']) == 1
        assert create(service, record) == result
    detail = service.procurement.detail(order.identity)
    assert all(line['purchased_quantity'] == 2 for line in detail['lines'])
    assert [row['purchase_order_number'] for row in detail['purchases']] == [NUMBER, str(int(NUMBER)+1)]
    assert [len(row['items']) for row in detail['purchases']] == [2, 1]
    assert all(row['receiving_warehouse']['revision'] == 1 for row in detail['purchases'])
    calls = [params for api, params in client.calls if api == CREATE]
    assert len(calls) == 2 and all(params['addressParam'] == service.receiving.checked(order.identity)['address'] for params in calls)


def test_partial_failure_repreviews_only_remaining(batch):
    service, client, _, order, body = batch
    client.reject_offer = '22222'
    results = [create(service, record) for record in groups(batch)]
    assert sorted(r['state'] for r in results) == ['created', 'failed']
    options = service.batch_options(order.identity)
    assert [line['options']['remaining_quantity'] for line in options['lines']] == [0, 0, 2]
    retry = {**body, 'items': body['items'][2:]}
    client.reject_offer = ''
    create(service, service.batch_preview(retry)['records'][0])
    assert len([api for api, _ in client.calls if api == CREATE]) == 3


def test_unknown_locks_all_group_lines_across_restart_and_credentials(batch):
    service, client, config, order, body = batch
    client.fail_offer = '12345'
    group = next(record for record in groups(batch) if len(record['preview']['items']) == 2)
    result = create(service, group)
    assert result['state'] == 'unknown'
    service.store = AlibabaSelfPurchaseStore(service.store.path)
    config['access_token'] = 'changed'
    with pytest.raises(ValueError, match='待处理采购'):
        service.batch_preview(body)
    with pytest.raises(ValueError, match='待处理采购'):
        preview(service)
    config['access_token'] = 'test-token'
    recovered = service.reconcile({'preview_id': group['id']})['record']
    assert recovered['state'] == 'created' and len(recovered['purchase_record_ids']) == 1
    assert len([api for api, _ in client.calls if api == CREATE]) == 1


def test_group_reservation_rolls_back_all_lines_on_late_sku_change(batch):
    service, client, _, order, _ = batch
    group = next(record for record in groups(batch) if len(record['preview']['items']) == 2)
    with service.procurement.store.orders.connect() as conn:
        changed = order.model_copy(deep=True)
        changed.items[1].sku = "CHANGED"
        conn.execute('UPDATE orders SET snapshot=? WHERE id=?', (changed.model_dump_json(), order.identity))
        conn.commit()
    assert create(service, group)['state'] == 'failed'
    with service.procurement.store.orders.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM order_purchase_reservations WHERE state='reserved'").fetchone()[0] == 0
    assert not any(api == CREATE for api, _ in client.calls)


def test_concurrent_overlapping_groups_cannot_double_order(batch):
    service, client, _, _, _ = batch
    group = next(record for record in groups(batch) if len(record['preview']['items']) == 2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(service, group), range(2)))
    assert all(result['state'] in {'created', 'submitting'} for result in results)
    assert len([api for api, _ in client.calls if api == CREATE]) == 1


def test_changed_warehouse_invalidates_preview(batch):
    service, client, _, order, _ = batch
    record = groups(batch)[0]
    with service.procurement.store.orders.connect() as conn:
        conn.execute("UPDATE order_receiving_warehouses SET value=json_set(value,'$.revision',2) WHERE order_id=?", (order.identity,))
        conn.commit()
    assert create(service, record)['state'] == 'failed'
    assert not any(api == CREATE for api, _ in client.calls)


def test_mismatched_recovery_never_backfills(batch):
    service, client, _, order, _ = batch
    client.fail_offer = '12345'
    record = next(record for record in groups(batch) if len(record['preview']['items']) == 2)
    create(service, record)
    client.orders[record['id']][1]['cargoParamList'].pop()
    with pytest.raises(ValueError, match='不一致'):
        service.reconcile({'preview_id': record['id']})
    assert not service.procurement.detail(order.identity)['purchases']


def test_group_cancel_closes_every_corresponding_record(batch, monkeypatch):
    service, client, _, order, _ = batch
    record = next(record for record in groups(batch) if len(record['preview']['items']) == 2)
    create(service, record)
    query = client.query
    def cancelled(api, number):
        result = query(api, number)
        result['result']['baseInfo']['status'] = 'cancel'
        return result
    monkeypatch.setattr(client, 'query', cancelled)
    assert service.reconcile({'preview_id': record['id']})['record']['state'] == 'closed'
    detail = service.procurement.detail(order.identity)
    assert all(line['remaining_quantity'] == 2 for line in detail['lines'])
    assert all(r['status'] == 'cancelled' for r in detail['purchases'])


@pytest.mark.parametrize('kind', ['missing', 'duplicate', 'spec', 'quantity'])
def test_preview_must_cover_exact_selected_cargo(batch, monkeypatch, kind):
    service, client, _, _, body = batch
    call = client.call
    def broken(api, params):
        result = call(api, params)
        row = result['orderPreviewResuslt'][0]
        if kind == 'missing': row['cargoList'].pop()
        if kind == 'duplicate': row['cargoList'] *= 2
        if kind == 'spec': row['cargoList'][0]['specId'] = 'wrong'
        if kind == 'quantity': row['cargoList'][0]['quantity'] = 999
        return result
    monkeypatch.setattr(client, 'call', broken)
    with pytest.raises(ValueError): service.batch_preview(body)
    assert not any(api == CREATE for api, _ in client.calls)


def test_duplicate_lines_rejected_before_remote_preview(batch):
    service, client, _, _, body = batch
    with pytest.raises(ValueError): service.batch_preview({**body, 'items': body['items'] * 2})
    assert not client.calls


@pytest.mark.parametrize('shared_waybill', [True, False])
@pytest.mark.parametrize('manual_refresh', [True, False])
def test_multi_sku_waybills_backfill_only_corresponding_lines(batch, shared_waybill, manual_refresh):
    from erp_web.services.alibaba_purchase_sync_service import sync_purchase, sync_purchases
    from erp_web.services.fulfillment_service import FulfillmentService
    from erp_web.stores.fulfillment_store import FulfillmentStore
    from tests.test_fulfillment import Bus
    service, client, config, order, _ = batch
    record = next(record for record in groups(batch) if len(record['preview']['items']) == 2)
    created = create(service, record)
    store = FulfillmentStore(service.procurement.store.orders)
    fulfillment = FulfillmentService(store, Bus(store), service.procurement.accounts_provider, service.procurement.detail, start_worker=False)
    class LogisticsClient(AlibabaFixtureProvider):
        calls = []
        def __init__(self, config): pass
        def query(self, api, number):
            self.calls.append((api, number))
            if api == ORDER_DETAIL: return client.query(api, number)
            if api == LOGISTICS_INFO:
                if not shared_waybill:
                    return {'result': [{'logisticsId': f'LP{i}', 'logisticsBillNo': f'YT{i}', 'logisticsCompanyName': '圆通',
                        'orderEntryIds': str(i), 'logisticsOrderGoods': [{'logisticsId': f'LP{i}', 'tradeOrderId': number,
                        'tradeOrderItemId': str(i), 'quantity': 2}]} for i in range(2)]}
                return {'result': [{'logisticsId': 'LP1', 'logisticsBillNo': 'YT1', 'logisticsCompanyName': '圆通',
                    'orderEntryIds': '0,1', 'logisticsOrderGoods': [{'logisticsId': 'LP1', 'tradeOrderId': number,
                    'tradeOrderItemId': str(i), 'quantity': 2} for i in range(2)]}]}
            return {'logisticsTrace': []}
    if manual_refresh:
        sync_purchase(service.procurement, fulfillment, lambda: config,
                      {'order_id': order.identity, 'record_id': created['purchase_record_ids'][0]}, client_factory=LogisticsClient)
    else:
        sync_purchases(service.procurement, fulfillment, lambda: config, order.identity, created['purchase_record_ids'], client_factory=LogisticsClient)
    assert len(LogisticsClient.calls) == 3
    parcels = fulfillment.detail(order.identity)['parcels']
    assert len(parcels) == (1 if shared_waybill else 2)
    assert all('line_key' not in p and 'purchase_record_id' not in p for p in parcels)
    assert all(p['purchase_order_number'] == NUMBER for p in parcels)
    assert sum(p['quantity'] for p in parcels) == 4
    assert {p['tracking_number'] for p in parcels} == ({'YT1'} if shared_waybill else {'YT0', 'YT1'})
    assert not fulfillment.detail(order.identity)['parcels_confirmed']


def test_new_preview_invalidates_overlapping_single_and_batch_previews(batch):
    service, _, _, _, body = batch
    single = preview(service)
    group = next(record for record in groups(batch) if len(record['preview']['items']) == 2)
    with pytest.raises(ValueError, match='预览已过期'):
        create(service, single)
    preview(service)
    with pytest.raises(ValueError, match='预览已过期'):
        create(service, group)


def test_native_preview_can_merge_different_offers_without_using_supplier_names(batch, monkeypatch):
    service, client, _, _, body = batch
    call = client.call
    def merged(api, params):
        result = call(api, params)
        if api != PREVIEW:
            return result
        rows = result['orderPreviewResuslt']
        rows[0]['cargoList'].extend(rows[1]['cargoList'])
        return {'orderPreviewResuslt': rows[:1]}
    monkeypatch.setattr(client, 'call', merged)
    records = service.batch_preview(body)['records']
    assert len(records) == 1 and len(records[0]['preview']['items']) == 3
    created = create(service, records[0])
    assert created['state'] == 'created' and len(created['purchase_record_ids']) == 1


def test_batch_atomic_backfill_failure_preserves_remote_and_recovers_without_create(batch, monkeypatch):
    service, client, _, order, _ = batch
    record = next(record for record in groups(batch) if len(record['preview']['items']) == 2)
    store = service.procurement.store
    complete = store._complete_purchase
    def fail_second(conn, ident, *args, **kwargs):
        if ident.endswith(':1'): raise ValueError('模拟第二行回填失败')
        return complete(conn, ident, *args, **kwargs)
    monkeypatch.setattr(store, '_complete_purchase', fail_second)
    result = create(service, record)
    assert result['state'] == 'created' and result['order_numbers'] == [NUMBER]
    assert not result['purchase_record_id']
    assert not service.procurement.detail(order.identity)['purchases']
    monkeypatch.setattr(store, '_complete_purchase', complete)
    result = service.reconcile({'preview_id': record['id']})['record']
    assert len(result['purchase_record_ids']) == 1
    assert len([api for api, _ in client.calls if api == CREATE]) == 1


def test_quantity_reservation_transaction_rejects_second_line_and_rolls_back_first(batch):
    service, _, config, order, _ = batch
    record = next(record for record in groups(batch) if len(record['preview']['items']) == 2)
    row = service.store.get(record['id'], account_scope(config))
    entries = [(ident, item['binding'], item['quantity']) for ident, item in zip(service._reservation_ids(row), row['payload']['items'], strict=True)]
    # 第一行已进入同一事务后，第二行的版本校验失败。
    entries[1][1]['revision'] += 1
    store = service.procurement.store
    with pytest.raises(ValueError, match='采购来源已变化'):
        store.reserve_purchases(entries, service.procurement.accounts_provider())
    with store.orders.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM order_purchase_reservations WHERE state='reserved'").fetchone()[0] == 0


def test_cancel_during_group_repreview_prevents_remote_creation(batch):
    service, client, _, order, _ = batch
    record = groups(batch)[0]
    def cancel():
        with service.procurement.store.orders.connect() as conn:
            cancelled = order.model_copy(update={'state': 'cancelled'})
            conn.execute('UPDATE orders SET snapshot=? WHERE id=?', (cancelled.model_dump_json(), order.identity))
            conn.commit()
    client.callback = cancel
    result = create(service, record)
    assert result['state'] == 'failed'
    assert not any(api == CREATE for api, _ in client.calls)


def test_batch_http_and_facade_use_validated_contract_and_sync_all_records(batch, monkeypatch):
    from types import SimpleNamespace
    from erp_web.facades import alibaba_self_purchase_facade as facade
    from erp_web.http_route_units import order_routes as routes
    service, client, _, order, body = batch
    sent, synced = [], []
    context = SimpleNamespace(order_procurement=service.procurement, fulfillment=object())
    monkeypatch.setattr(facade, 'get_context', lambda: context)
    monkeypatch.setattr(facade, 'create_service', lambda _: service)
    monkeypatch.setattr(facade, 'sync_purchases', lambda *args, **kwargs: synced.append((args[3:5], kwargs)))
    handler = SimpleNamespace(path='/api/orders/alibaba-purchase/batch-preview', read_body=lambda: body,
                              send_json=lambda value, status=200: sent.append((value, status)))
    routes.handle_self_purchase_batch_preview(handler)
    record = next(record for record in sent[-1][0]['records'] if len(record['preview']['items']) == 2)
    result = facade.command('create', {'preview_id': record['id'], 'pay_channel': 'alipay'})['record']
    assert synced == [((order.identity, result['purchase_record_ids']), {'once': True})]
    handler.read_body = lambda: {**body, 'items': 'invalid'}
    routes.handle_self_purchase_batch_preview(handler)
    assert sent[-1][1] == 400


@pytest.fixture
def grouped_sync(batch):
    from erp_web.services.alibaba_purchase_sync_service import sync_purchase, sync_purchases
    from erp_web.services.fulfillment_service import FulfillmentService
    from erp_web.stores.fulfillment_store import FulfillmentStore
    from tests.test_fulfillment import Bus

    service, client, config, order, _ = batch
    created = [create(service, record) for record in groups(batch)]
    same = created[0]['purchase_record_ids']
    other = created[1]['purchase_record_ids'][0]
    store = FulfillmentStore(service.procurement.store.orders)
    fulfillment = FulfillmentService(store, Bus(store), service.procurement.accounts_provider,
                                     service.procurement.detail, start_worker=False)

    class Query(AlibabaFixtureProvider):
        calls = []
        status = 'waitbuyerpay'
        callback = None
        fail = False

        def __init__(self, config):
            pass

        def query(self, api, number):
            self.calls.append((api, number))
            if self.callback:
                self.callback()
            if self.fail:
                raise ValueError('采购查询失败')
            if api == ORDER_DETAIL:
                value = client.query(api, number)
                value['result']['baseInfo']['status'] = self.status
                return value
            return {'result': []}

    ids = same + [other]
    sync_purchases(service.procurement, fulfillment, lambda: config, order.identity, ids, client_factory=Query)
    Query.calls.clear()

    def run(record_id=None):
        return sync_purchase(service.procurement, fulfillment, lambda: config,
                             {'order_id': order.identity, 'record_id': record_id or same[0]}, client_factory=Query)

    def snapshots():
        detail = service.procurement.detail(order.identity)
        return {row['id']: row['progress'] for row in detail['purchases']}

    return service.procurement, order.identity, same, other, Query, run, snapshots


def test_refresh_header_updates_all_products_but_not_other_purchase(grouped_sync):
    _, _, same, other, query, run, snapshots = grouped_sync
    before = snapshots()
    query.status = 'waitsellersend'
    detail = run(same[0])
    after = snapshots()
    assert all(after[key]['data']['order']['status'] == 'waitsellersend' for key in same)
    assert after[other] == before[other]
    assert query.calls == [(ORDER_DETAIL, NUMBER), (LOGISTICS_INFO, NUMBER)]
    assert {row['status'] for row in detail['order']['purchase_tracking']['orders'] if row['order_number'] == NUMBER} == {'waitsellersend'}
    assert {p['sku_id'] for p in after[same[0]]['data']['products']} == {'67890', '67891'}


def test_cancelled_header_cannot_refresh_or_modify_other_purchase(grouped_sync):
    procurement, order_id, same, other, query, run, snapshots = grouped_sync
    before = snapshots()
    procurement.cancel_purchase({'order_id': order_id, 'record_id': same[0]})
    with pytest.raises(ValueError, match='作废'):
        run()
    assert snapshots()[other] == before[other]
    assert not query.calls


def test_group_query_failure_retains_all_previous_snapshots_without_retry(grouped_sync):
    _, _, same, other, query, run, snapshots = grouped_sync
    before = snapshots()
    query.fail = True
    run()
    after = snapshots()
    assert query.calls == [(ORDER_DETAIL, NUMBER)]
    for key in same:
        assert after[key]['state'] == 'error'
        assert after[key]['data'] == before[key]['data']
        assert after[key]['error'] == '采购查询失败'
    assert after[other] == before[other]


def test_header_refresh_claims_before_network(grouped_sync):
    _, _, same, _, query, run, snapshots = grouped_sync
    def concurrent_refresh():
        assert all(snapshots()[key]['state'] == 'syncing' for key in same)
        with pytest.raises(ValueError, match='正在同步'):
            run(same[0])
    query.callback = staticmethod(concurrent_refresh)
    query.status = 'waitsellersend'
    run()
    assert query.calls == [(ORDER_DETAIL, NUMBER), (LOGISTICS_INFO, NUMBER)]
    assert all(snapshots()[key]['state'] == 'synced' for key in same)


def test_busy_header_cannot_refresh_twice(grouped_sync):
    procurement, order_id, same, _, query, run, snapshots = grouped_sync
    progress = procurement.store.purchase_progress
    accounts = procurement.accounts_provider()
    generation, value = progress.begin(order_id, same[0], accounts)
    before = snapshots()
    with pytest.raises(ValueError, match='正在同步'):
        run()
    assert snapshots() == before
    assert not query.calls
    progress.finish(order_id, same[0], generation, accounts, value)
