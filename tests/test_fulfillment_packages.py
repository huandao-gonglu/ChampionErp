"""订单级包裹：多商品、补寄、独立件数、确认清单和采购解耦。"""
from copy import deepcopy

import pytest

from erp_web.schemas.fulfillment import DomesticParcel
from erp_web.services.fulfillment_packages import parcel_payloads
from tests.test_fulfillment import domain, command, ready


def test_multiple_products_and_skus_can_share_one_parcel():
    parcel = DomesticParcel(id='p', tracking_number=' sf123 ', quantity=6,
        description='衣服 A：红 M 两件、黑 L 一件；杯子 B：白色三件')
    package, = parcel_payloads([parcel.model_dump()], complete=True)
    assert package['logistics_order'] == 'SF123' and package['num'] == 6
    assert '杯子' in package['describe'] and 'from_order' not in package


def test_duplicate_waybill_is_rejected():
    parcels = [dict(id='a', tracking_number='SF123', quantity=1), dict(id='b', tracking_number=' sf123 ', quantity=2)]
    with pytest.raises(ValueError, match='重复'):
        parcel_payloads(parcels)


def test_unknown_count_is_saved_but_not_reported(domain):
    service, bus, order, parcel, *_ = domain
    view = command(service, order, 'parcels', parcels=[{**parcel, 'quantity': None}], confirm_parcels=False)
    assert view['parcels'][0]['quantity'] is None and not view['parcels_confirmed']
    with pytest.raises(ValueError, match='商品件数'):
        command(service, order, 'parcels', parcels=view['parcels'])
    service.process_one(order)
    assert not any(path.endswith('createOrder') for path, _ in bus.calls)


def test_more_parcels_than_sales_units_and_reship_preserves_original(domain):
    service, bus, order, parcel, *_ = domain
    command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2, 3], 'consolidation_service_id': 3})
    parcels = [{**parcel, 'id': str(i), 'tracking_number': f'WAY{i}', 'quantity': 3} for i in range(3)]
    view = command(service, order, 'parcels', parcels=parcels)
    assert view['parcels'] == parcels and len(view['parcels']) > sum(l.quantity for l in order.items)
    assert [p['num'] for p in parcel_payloads(view['parcels'], complete=True)] == [3, 3, 3]


@pytest.mark.parametrize('reported', [False, True])
def test_purchase_void_and_add_do_not_change_packages(domain, reported):
    service, bus, order = ready(domain)
    if reported:
        service.process_one(order)
    procurement = service.detail_provider.__self__
    before = deepcopy(service.store.get(order.identity))
    record = procurement.detail(order.identity)['purchases'][0]
    procurement.cancel_purchase({'order_id': order.identity, 'record_id': record['id']})
    procurement.record_purchase({'order_id': order.identity, 'source_platform': '1688', 'purchase_order_number': 'NEW', 'request_id': 'new'})
    assert service.store.get(order.identity) == before
    assert len(procurement.detail(order.identity)['purchases']) == 2


def test_no_purchase_or_sales_mapping_needed(domain):
    service, bus, order, parcel, *_ = domain
    with service.store.orders.connect() as conn:
        conn.execute('DELETE FROM order_purchase_requests')
        conn.execute('DELETE FROM order_purchases')
        conn.execute('DELETE FROM order_sources')
        conn.commit()
    command(service, order, 'label', label={'url': 'https://example.com/label.pdf', 'tracking_number': 'RU'})
    view = command(service, order, 'parcels', parcels=[{**parcel, 'source_platform': '', 'purchase_order_number': ''}])
    assert not view['blocked_reason']
    service.process_one(order)
    assert service.detail(order.identity)['crossborderbus_order_id'] == 99


def test_excluded_parcel_preserved_but_not_sent(domain):
    service, bus, order, parcel, *_ = domain
    parcels = [parcel, {**parcel, 'id': 'old', 'tracking_number': 'OLD', 'quantity': None, 'included': False}]
    view = command(service, order, 'parcels', parcels=parcels)
    assert len(view['parcels']) == 2
    assert len(parcel_payloads(view['parcels'], complete=True)) == 1
    assert '合单' not in view['blocked_reason']


def test_edit_clears_confirmation_and_blocks_automatic_report(domain):
    service, bus, order = ready(domain)
    parcels = service.detail(order.identity)['parcels']
    command(service, order, 'parcels', parcels=parcels, confirm_parcels=False)
    service.process_one(order)
    assert not any(path.endswith('createOrder') for path, _ in bus.calls)
    assert '确认' in service.detail(order.identity)['blocked_reason']
