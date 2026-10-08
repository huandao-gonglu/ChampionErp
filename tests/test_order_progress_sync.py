"""订单同轮刷新采购和仓库快照；不创建采购单或预报单。"""
from types import SimpleNamespace

import pytest

from erp_web.services import order_progress_sync_service as service
from tests.test_alibaba_purchase_sync import setup, domain, progress


def test_same_cycle_persists_purchase_status_then_refreshes_existing_warehouse(setup, monkeypatch):
    procurement, fulfillment, body, client, _ = setup
    original = service.sync_purchase
    monkeypatch.setattr(service, 'sync_purchase', lambda *a, **k: original(*a, **k, client_factory=client))
    current = fulfillment.detail(body['order_id'])
    fulfillment.store.change(body['order_id'], current['revision'], {'crossborderbus_order_id': 55})
    calls = []
    def sync(order_id, revision):
        assert progress(procurement.detail(order_id))['data']['order']['status'] == 'waitbuyerreceive'
        calls.append(order_id)
        return {'error_message': ''}
    monkeypatch.setattr(fulfillment, 'sync', sync)
    checkpoints = []
    assert service.sync_order_progress(procurement, fulfillment, lambda: {}, body['order_id'], lambda: checkpoints.append(True))
    assert calls == [body['order_id']] and len(checkpoints) == 2
    assert progress(procurement.detail(body['order_id']))['data']['logistics'][0]['tracking_number'] == 'YT1'


def test_failed_purchase_still_allows_warehouse_and_other_purchases(setup, monkeypatch):
    procurement, fulfillment, body, _, _ = setup
    detail = procurement.detail(body['order_id'])
    record = detail['lines'][0]['records'][0]
    detail['lines'][0]['records'] += [{**record, 'id': 'second'}, {**record, 'id': 'cancelled', 'status': 'cancelled'}]
    calls = []
    def sync(*args):
        ident = args[3]['record_id']; calls.append(ident)
        if ident == body['record_id']: raise ValueError('失败')
        return detail
    monkeypatch.setattr(service, 'sync_purchase', sync)
    fake = SimpleNamespace(detail=lambda _: detail)
    current = fulfillment.detail(body['order_id'])
    fulfillment.store.change(body['order_id'], current['revision'], {'crossborderbus_order_id': 55})
    monkeypatch.setattr(fulfillment, 'sync', lambda *a: calls.append('warehouse') or {'error_message': ''})
    assert not service.sync_order_progress(fake, fulfillment, lambda: {}, body['order_id'], lambda: None)
    assert calls == [body['record_id'], 'second', 'warehouse']


def test_no_warehouse_order_never_submits_one(setup, monkeypatch):
    procurement, fulfillment, body, client, _ = setup
    original = service.sync_purchase
    monkeypatch.setattr(service, 'sync_purchase', lambda *a, **k: original(*a, **k, client_factory=client))
    monkeypatch.setattr(fulfillment, 'sync', lambda *a: pytest.fail('没有报单不能同步或创建仓库单'))
    assert service.sync_order_progress(procurement, fulfillment, lambda: {}, body['order_id'], lambda: None)
    assert fulfillment.detail(body['order_id'])['crossborderbus_order_id'] is None
