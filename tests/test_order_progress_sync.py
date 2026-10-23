"""订单同轮刷新采购和仓库快照；不创建采购单或预报单。"""
from types import SimpleNamespace

import pytest

from erp_web.schemas.orders import OrderEvent, OrderSnapshot
from erp_web.services.order_notification_service import OrderNotificationService
from erp_web.services import order_progress_sync_service as service
from tests.test_alibaba_purchase_sync import setup, domain, progress


@pytest.mark.parametrize('warehouse_error', ['', '仓库本次未更新'])
def test_created_declaration_skips_purchase_and_preserves_snapshot(setup, monkeypatch, warehouse_error):
    procurement, fulfillment, body, _, run = setup
    previous = progress(run())
    monkeypatch.setattr(service, 'sync_purchase', lambda *a, **k: pytest.fail('报单成功后不再查询采购和国内物流'))
    current = fulfillment.detail(body['order_id'])
    fulfillment.store.change(body['order_id'], current['revision'], {'crossborderbus_order_id': 55})
    calls = []
    def sync(order_id, revision):
        calls.append(order_id)
        return {'error_message': warehouse_error}
    monkeypatch.setattr(fulfillment, 'sync', sync)
    checkpoints = []
    assert service.sync_order_progress(procurement, fulfillment, lambda: {}, body['order_id'], lambda: checkpoints.append(True)) is (not warehouse_error)
    assert calls == [body['order_id']] and len(checkpoints) == 1
    assert progress(procurement.detail(body['order_id'])) == previous


@pytest.mark.parametrize('state', ['shipped', 'delivered'])
@pytest.mark.parametrize('declaration', ['none', 'created', 'unknown'])
def test_platform_shipped_or_delivered_skips_purchase_and_warehouse(setup, monkeypatch, state, declaration):
    procurement, fulfillment, body, _, _ = setup
    detail = procurement.detail(body['order_id'])
    detail['order']['state'] = state
    current = fulfillment.detail(body['order_id'])
    if declaration != 'none':
        fulfillment.store.change(body['order_id'], current['revision'], {
            'crossborderbus_order_id': 55 if declaration == 'created' else None,
            'create_unknown': declaration == 'unknown',
        })
    before = fulfillment.store.get(body['order_id'])
    monkeypatch.setattr(service, 'sync_purchase', lambda *a, **k: pytest.fail('平台已发货后不再查询采购和国内物流'))
    monkeypatch.setattr(fulfillment, 'sync', lambda *a: pytest.fail('平台已发货后不再查询报单'))
    fake = SimpleNamespace(detail=lambda _: detail)
    assert service.sync_order_progress(fake, fulfillment, lambda: {}, body['order_id'], lambda: None)
    assert fulfillment.store.get(body['order_id']) == before


@pytest.mark.parametrize('trigger', ['sync', 'notification'])
def test_new_platform_shipping_state_stops_progress_queries_in_same_cycle(setup, monkeypatch, trigger):
    procurement, fulfillment, body, _, _ = setup
    current = fulfillment.detail(body['order_id'])
    fulfillment.store.change(body['order_id'], current['revision'], {'crossborderbus_order_id': 55})
    order = procurement.store.order(body['order_id'], procurement.accounts_provider())
    shipped = OrderSnapshot.model_validate(order.model_dump(include=set(OrderSnapshot.model_fields))).model_copy(
        update={'state': 'shipped', 'status': 'DELIVERY'},
    )
    class Adapter:
        def sync(self):
            yield shipped
        def read(self, event):
            yield shipped
    monkeypatch.setattr(service, 'sync_purchase', lambda *a, **k: pytest.fail('本轮确认平台发货后不再查询国内物流'))
    monkeypatch.setattr(fulfillment, 'sync', lambda *a: pytest.fail('本轮确认平台发货后不再查询报单'))
    store = procurement.store.orders
    worker = OrderNotificationService(
        store, lambda: {'yandex': {'campaign_id': '4', 'api_token': 'test-token'}},
        adapters={'yandex': lambda _: Adapter()}, parser=lambda *a: (None, {}),
        progress_sync=lambda order_id, checkpoint: service.sync_order_progress(
            procurement, fulfillment, lambda: {}, order_id, checkpoint,
        ),
        start_worker=False,
    )
    if trigger == 'sync':
        worker.sync('yandex')
    else:
        store.enqueue(OrderEvent(platform='yandex', account_id='4', topic='ORDER_UPDATED', resource=order.order_id))
    assert worker.process_one('yandex')
    assert procurement.detail(body['order_id'])['order']['state'] == 'shipped'
    assert store.read_notifications({'yandex': '4'})[0]['status'] == 'done'


def test_failed_purchase_still_allows_warehouse_and_other_purchases(setup, monkeypatch):
    procurement, fulfillment, body, _, _ = setup
    detail = procurement.detail(body['order_id'])
    record = detail["purchases"][0]
    detail["purchases"] += [{**record, 'id': 'second', 'purchase_order_number': '123456'},
                                     {**record, 'id': 'cancelled', 'status': 'cancelled'}]
    calls = []
    def sync(*args):
        ident = args[3]['record_id']; calls.append(ident)
        if ident == body['record_id']: raise ValueError('失败')
        return detail
    monkeypatch.setattr(service, 'sync_purchase', sync)
    fake = SimpleNamespace(detail=lambda _: detail)
    current = fulfillment.detail(body['order_id'])
    fulfillment.store.change(body['order_id'], current['revision'], {'create_unknown': True})
    monkeypatch.setattr(fulfillment, 'sync', lambda *a: calls.append('warehouse') or {'error_message': ''})
    assert not service.sync_order_progress(fake, fulfillment, lambda: {}, body['order_id'], lambda: None)
    assert calls == [body['record_id'], 'second', 'warehouse']


@pytest.mark.parametrize('archived', [False, True])
def test_no_current_warehouse_order_refreshes_purchase_without_submitting(setup, monkeypatch, archived):
    procurement, fulfillment, body, client, _ = setup
    current = fulfillment.detail(body['order_id'])
    if archived:
        fulfillment.store.change(body['order_id'], current['revision'], {
            'attempt_history': [{'crossborderbus_order_id': 55, 'order_number': 'old', 'archived_at': '2026-10-18T00:00:00Z'}],
            'awaiting_resubmission': True,
        })
    original = service.sync_purchase
    monkeypatch.setattr(service, 'sync_purchase', lambda *a, **k: original(*a, **k, client_factory=client))
    monkeypatch.setattr(fulfillment, 'sync', lambda *a: pytest.fail('没有报单不能同步或创建仓库单'))
    assert service.sync_order_progress(procurement, fulfillment, lambda: {}, body['order_id'], lambda: None)
    assert fulfillment.detail(body['order_id'])['crossborderbus_order_id'] is None
    assert progress(procurement.detail(body['order_id']))['data']['logistics'][0]['tracking_number'] == 'YT1'


def test_unknown_declaration_keeps_purchase_refresh_and_reconciles(setup, monkeypatch):
    procurement, fulfillment, body, client, _ = setup
    original = service.sync_purchase
    monkeypatch.setattr(service, 'sync_purchase', lambda *a, **k: original(*a, **k, client_factory=client))
    current = fulfillment.detail(body['order_id'])
    fulfillment.store.change(body['order_id'], current['revision'], {'create_unknown': True})
    calls = []
    def sync(order_id, revision):
        assert progress(procurement.detail(order_id))['data']['order']['status'] == 'waitbuyerreceive'
        assert fulfillment.store.get(order_id)['revision'] == revision
        calls.append(order_id)
        return {'error_message': ''}
    monkeypatch.setattr(fulfillment, 'sync', sync)
    assert service.sync_order_progress(procurement, fulfillment, lambda: {}, body['order_id'], lambda: None)
    assert calls == [body['order_id']]


@pytest.mark.parametrize('failure', [False, True])
def test_purchase_is_queried_once_and_logistics_warning_is_reported(setup, monkeypatch, failure):
    _, fulfillment, body, _, run = setup
    detail = run()
    record = detail["purchases"][0]
    record['progress']['data']['logistics_warning'] = '物流本次未更新'
    calls = []
    def sync(*args):
        calls.append(args[3]['record_id'])
        if failure:
            raise ValueError('查询失败')
        return detail
    monkeypatch.setattr(service, 'sync_purchase', sync)
    fake = SimpleNamespace(detail=lambda _: detail)
    assert not service.sync_order_progress(fake, fulfillment, lambda: {}, body['order_id'], lambda: None)
    assert calls == [body['record_id']]
