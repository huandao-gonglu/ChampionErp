"""直接录入包裹无需采购单，支持作废某 SKU 后补录及混合报单。"""

from copy import deepcopy

import pytest

from erp_web.schemas.requests import validate_request_payload
from erp_web.stores.fulfillment_store import FulfillmentStore
from tests.test_fulfillment import command, domain
from tests.test_order_purchase_replacement import replacement, cancel


@pytest.fixture
def direct(domain):
    service, bus, order, parcel, snapshot, accounts = domain
    # 模拟从未登记采购、也未选择采购来源的销售订单。
    with service.store.orders.connect() as conn:
        conn.execute('DELETE FROM order_purchases WHERE order_id=?', (order.identity,))
        conn.execute('DELETE FROM order_sources WHERE order_id=?', (order.identity,))
        conn.commit()
    direct_parcel = {k: v for k, v in parcel.items() if k != 'purchase_record_id'}
    command(service, order, 'label', label={'url': 'https://example.com/label.pdf', 'tracking_number': 'RU123'})
    return service, bus, order, direct_parcel, snapshot, accounts


def test_no_purchase_or_source_can_save_restart_create_and_update(direct):
    service, bus, order, parcel, *_ = direct
    request = validate_request_payload({'order_id': order.identity,
        'revision': service.detail(order.identity)['revision'], 'parcels': [parcel]},
        endpoint='/api/orders/fulfillment/parcels')
    saved = service.command('parcels', request)
    assert saved['parcels'] == [{**parcel, 'purchase_record_id': ''}]
    assert not saved['blocked_reason']
    assert FulfillmentStore(service.store.orders).get(order.identity)['parcels'] == saved['parcels']
    line = service.detail_provider(order.identity)['lines'][0]
    assert line['records'] == [] and line['selection']['source'] is None
    service.purchase_progress = service.detail_provider.__self__.store.purchase_progress
    command(service, order, 'parcels', parcels=saved['parcels'])
    assert service.process_one(order)
    created = next(body['order_data'][0]['package_list'] for path, body in bus.calls if path.endswith('createOrder'))
    assert created == [{'package_of': 1, 'num': 2, 'describe': 'SKU-1 · 手机支架',
        'img': 'https://platform.example/yandex-sku.jpg', 'logistics_order': 'SF123'}]
    command(service, order, 'parcels', parcels=[{**parcel, 'tracking_number': 'SF456'}])
    assert service.process_one(order)
    updated = next(body['package_list'] for path, body in bus.calls if path.endswith('updateOrderPackage'))
    assert updated == [{**created[0], 'logistics_order': 'SF456'}]
    assert service.detail_provider(order.identity)['lines'][0]['records'] == []


@pytest.mark.parametrize('shared_waybill', [True, False])
def test_void_one_sku_then_direct_parcel_preserves_other_purchase(replacement, shared_waybill):
    procurement, service, order, old, other = replacement
    cancel(replacement)
    direct_parcel = {**old, 'id': 'replacement', 'purchase_record_id': '',
                     'tracking_number': other['tracking_number'] if shared_waybill else 'YT456'}
    saved = command(service, order, 'parcels', parcels=[other, direct_parcel])
    if not shared_waybill:
        assert '合单' in saved['blocked_reason']
        saved = command(service, order, 'plan', plan={**saved['plan'], 'consolidation_service_id': 3})
    assert not saved['blocked_reason']
    assert saved['parcels'] == [other, direct_parcel]
    detail = procurement.detail(order.identity)
    assert [r['status'] for r in detail['lines'][0]['records']] == ['cancelled']
    assert detail['lines'][1]['records'][0]['status'] == 'purchased'
    assert service.process_one(order)
    packages = next(body['order_data'][0]['package_list'] for path, body in service.client.calls if path.endswith('createOrder'))
    if shared_waybill:
        package, = packages
        assert package['num'] == 3
        assert 'from_order' not in package and 'from_platform' not in package
        assert 'SKU-1' in package['describe'] and 'SKU-2' in package['describe']
        assert package['describe'].count('采购单号：PO-123') == 1
    else:
        assert len(packages) == 2
        assert packages[0]['from_order'] == 'PO-123'
        assert 'from_order' not in packages[1] and 'from_platform' not in packages[1]


def test_same_sku_can_mix_purchase_and_direct_quantities_and_cancel_independently(domain):
    service, _, order, linked, snapshot, _ = domain
    order = order.model_copy(update={'items': [order.items[0].model_copy(update={'quantity': 4})]})
    snapshot(order)
    direct_parcel = {**linked, 'id': 'direct', 'purchase_record_id': ''}
    command(service, order, 'label', label={'url': 'https://example.com/label.pdf', 'tracking_number': 'RU123'})
    service.purchase_progress = service.detail_provider.__self__.store.purchase_progress
    result = command(service, order, 'parcels', parcels=[linked, direct_parcel])
    assert not result['blocked_reason']
    detail = service.detail_provider(order.identity)
    assert sum(p['num'] for p in service.delivery_packages(service.store.get(order.identity), detail)) == 4
    service.detail_provider.__self__.cancel_purchase({'order_id': order.identity, 'record_id': linked['purchase_record_id']})
    assert service.detail(order.identity)['parcels'] == [direct_parcel]
    assert '还缺 2 件' in service.detail(order.identity)['blocked_reason']


def test_direct_parcel_cannot_hide_unallocated_active_purchase(domain):
    service, _, order, linked, *_ = domain
    with pytest.raises(ValueError, match='有效采购 2 件与直接录入包裹 2 件超过销售数量 2 件'):
        command(service, order, 'parcels', parcels=[{**linked, 'purchase_record_id': ''}])
    assert service.detail(order.identity)['parcels'] == []


@pytest.mark.parametrize('quantity', [1, 2])
def test_missing_item_reship_keeps_original_purchase_and_reports_two_packages(replacement, quantity):
    procurement, service, order, original, other = replacement
    purchases = deepcopy([line['records'] for line in procurement.detail(order.identity)['lines']])
    service.purchase_progress = procurement.store.purchase_progress
    reship = {**original, 'id': 'reship', 'tracking_number': 'YT456', 'carrier': '', 'quantity': quantity}
    parcels = ([{**original, 'quantity': original['quantity'] - quantity}]
               if quantity < original['quantity'] else []) + [other, reship]
    saved = command(service, order, 'parcels', parcels=parcels)
    assert saved['parcels'] == parcels
    assert '合单' in saved['blocked_reason']
    assert FulfillmentStore(service.store.orders).get(order.identity)['parcels'] == parcels
    # 漏发补寄没有新采购单；全部数量仍归原采购，其他 SKU 和原运单保留。
    assert [line['records'] for line in procurement.detail(order.identity)['lines']] == purchases
    saved = command(service, order, 'plan', plan={**saved['plan'], 'consolidation_service_id': 3})
    assert not saved['blocked_reason']
    assert service.process_one(order)
    packages = next(body['order_data'][0]['package_list'] for path, body in service.client.calls if path.endswith('createOrder'))
    assert len(packages) == 2
    assert {p['logistics_order']: p['num'] for p in packages} == {'SF123': 3 - quantity, 'YT456': quantity}
    assert all(p['from_order'] == 'PO-123' for p in packages)


@pytest.mark.parametrize('change', [
    {'line_key': 'foreign'}, {'purchase_record_id': 'foreign'},
    {'quantity': 3}, {'quantity': 0}, {'quantity': -1}, {'quantity': 1.5},
    {'tracking_number': ' '}, {'carrier': 'x' * 81},
])
def test_direct_parcel_rejects_invalid_identity_quantity_and_waybill(direct, change):
    service, _, order, parcel, *_ = direct
    with pytest.raises(ValueError):
        command(service, order, 'parcels', parcels=[{**parcel, **change}])
    assert service.detail(order.identity)['parcels'] == []


@pytest.mark.parametrize('kind', ['domain', 'direct'])
@pytest.mark.parametrize('carrier', [None, '', '   '])
def test_optional_carrier_can_save_and_report(request, kind, carrier):
    service, bus, order, parcel, *_ = request.getfixturevalue(kind)
    parcel = dict(parcel)
    if carrier is None:
        parcel.pop('carrier')
    else:
        parcel['carrier'] = carrier
    command(service, order, 'label', label={'url': 'https://example.com/label.pdf', 'tracking_number': 'RU123'})
    body = validate_request_payload({'order_id': order.identity,
        'revision': service.detail(order.identity)['revision'], 'parcels': [parcel]},
        endpoint='/api/orders/fulfillment/parcels')
    saved = service.command('parcels', body)
    assert saved['parcels'][0]['carrier'] == ''
    assert not saved['blocked_reason']
    assert FulfillmentStore(service.store.orders).get(order.identity)['parcels'][0]['carrier'] == ''
    assert service.process_one(order)
    package, = next(body['order_data'][0]['package_list'] for path, body in bus.calls if path.endswith('createOrder'))
    assert package['logistics_order'] == parcel['tracking_number']
    assert package['num'] == parcel['quantity']
    assert 'carrier' not in package


def test_cancelled_record_must_not_be_silently_treated_as_direct(replacement):
    _, service, order, old, other = replacement
    cancel(replacement)
    with pytest.raises(ValueError, match='有效采购记录'):
        command(service, order, 'parcels', parcels=[old, other])
    assert service.detail(order.identity)['parcels'] == [other]


def test_partial_direct_parcels_remain_editable_but_cannot_submit(direct):
    service, _, order, parcel, *_ = direct
    saved = command(service, order, 'parcels', parcels=[{**parcel, 'quantity': 1}])
    assert '还缺 1 件' in saved['blocked_reason']
    with pytest.raises(ValueError, match='还缺 1 件'):
        command(service, order, 'submit')
    with pytest.raises(ValueError, match='不能重复'):
        command(service, order, 'parcels', parcels=[parcel, parcel])


def test_direct_parcels_keep_version_account_and_lock_checks(direct):
    service, _, order, parcel, _, accounts = direct
    revision = service.detail(order.identity)['revision']
    saved = command(service, order, 'parcels', parcels=[parcel])
    with pytest.raises(ValueError, match='履约资料已更新'):
        service.command('parcels', {'order_id': order.identity, 'revision': revision, 'parcels': []})
    assert service.detail(order.identity)['parcels'] == saved['parcels']
    accounts['yandex'] = 'other'
    with pytest.raises(ValueError, match='当前店铺'):
        command(service, order, 'parcels', parcels=[])
    accounts['yandex'] = '4'
    service.store.change(order.identity, saved['revision'], {'warehouse_locked': True})
    with pytest.raises(ValueError, match='资料已锁定'):
        command(service, order, 'parcels', parcels=[])


def test_duplicate_order_line_identity_is_rejected(direct):
    service, _, order, parcel, *_ = direct
    detail = service.detail_provider(order.identity)
    detail['lines'].append(deepcopy(detail['lines'][0]))
    with pytest.raises(ValueError, match='商品身份不明确'):
        service.parcels_payload([parcel], detail)


def test_shared_waybill_with_only_direct_items_has_no_purchase_fields(direct):
    service, _, order, parcel, *_ = direct
    command(service, order, 'parcels', parcels=[{**parcel, 'quantity': 1},
        {**parcel, 'id': 'second', 'quantity': 1, 'tracking_number': ' sf123 '}])
    package, = service.delivery_packages(service.store.get(order.identity), service.detail_provider(order.identity))
    assert package['num'] == 2 and package['logistics_order'] == 'SF123'
    assert 'from_order' not in package and 'from_platform' not in package
    assert package['describe'].count('数量：1 件') == 2
    assert '采购单号' not in package['describe']


def test_purchase_sync_preserves_direct_parcels_and_never_duplicates_them(domain):
    service, _, order, linked, snapshot, _ = domain
    order = order.model_copy(update={'items': [order.items[0].model_copy(update={'quantity': 4})]})
    snapshot(order)
    direct_parcel = {**linked, 'id': 'direct', 'purchase_record_id': ''}
    command(service, order, 'parcels', parcels=[direct_parcel])
    args = (order.identity, linked['purchase_record_id'], [linked], lambda conn: None)
    assert service.merge_purchase_parcels(*args)[0] == 'synced'
    assert service.merge_purchase_parcels(*args)[0] == 'synced'
    assert service.detail(order.identity)['parcels'] == [direct_parcel, linked]
    changed = {**linked, 'tracking_number': 'YT456'}
    assert service.merge_purchase_parcels(order.identity, linked['purchase_record_id'], [changed], lambda conn: None)[0] == 'conflict'
    assert service.detail(order.identity)['parcels'] == [direct_parcel, linked]
