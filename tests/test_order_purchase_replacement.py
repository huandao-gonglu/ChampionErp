"""按 SKU 作废采购、保留共用运单、重新采购及合单报单的回归。"""

import json
import sqlite3

import pytest

from erp_web.schemas.order_procurement import order_line_key
from erp_web.schemas.orders import OrderLine
from tests.test_fulfillment import domain, command


@pytest.fixture
def replacement(domain):
    fulfillment, bus, order, parcel, snapshot, accounts = domain
    procurement = fulfillment.detail_provider.__self__
    second = OrderLine(sku='SKU-2', remote_id='SKU-2', title='另一件商品', quantity=1)
    order = order.model_copy(update={'items': [*order.items, second]})
    snapshot(order)
    key = order_line_key(second)
    source = procurement.detail(order.identity)['lines'][0]['selection']['source']
    procurement.select_source({'order_id': order.identity, 'line_key': key, 'revision': 0,
                               'source': {**source, 'source_sku_id': 'source-2'}})
    result = procurement.record_purchase({'order_id': order.identity, 'line_key': key, 'revision': 1,
        'request_id': 'purchase-2', 'purchase_order_number': 'PO-123', 'quantity': 1,
        'receiving_revision': 1, 'receiving_confirmed': True})
    other_record = result['lines'][1]['records'][0]
    other = {**parcel, 'id': 'parcel-2', 'line_key': key, 'purchase_record_id': other_record['id'], 'quantity': 1}
    command(fulfillment, order, 'parcels', parcels=[parcel, other])
    command(fulfillment, order, 'label', label={'url': 'https://example.com/label.pdf', 'tracking_number': 'RU123'})
    return procurement, fulfillment, order, parcel, other


def cancel(setup):
    procurement, _, order, parcel, _ = setup
    return procurement.cancel_purchase({'order_id': order.identity, 'record_id': parcel['purchase_record_id']})


def new_purchase(setup):
    procurement, _, order, parcel, _ = setup
    return procurement.record_purchase({'order_id': order.identity, 'line_key': parcel['line_key'], 'revision': 1,
        'request_id': 'replacement', 'purchase_order_number': 'PO-456', 'quantity': 2,
        'receiving_revision': 1, 'receiving_confirmed': True})


def test_void_one_sku_keeps_shared_order_and_waybill_then_consolidates(replacement):
    procurement, fulfillment, order, parcel, other = replacement
    old = fulfillment.detail(order.identity)
    with pytest.raises(ValueError, match='超过订单剩余数量'):
        new_purchase(replacement)
    result = cancel(replacement)
    assert result['lines'][0]['remaining_quantity'] == 2
    assert result['lines'][0]['records'][0]['status'] == 'cancelled'
    assert result['lines'][1]['records'][0]['status'] == 'purchased'
    current = fulfillment.detail(order.identity)
    assert current['parcels'] == [other]
    assert current['revision'] == old['revision'] + 1
    # 旧页面、旧后台任务都不能用作废前的版本重新写回分配或发起报单。
    with pytest.raises(ValueError, match='已更新'):
        fulfillment.store.change(order.identity, old['revision'], {'parcels': [parcel, other]})
    assert fulfillment.store.claim(order.identity, 'create', old['revision']) is None
    cancel(replacement)
    assert fulfillment.detail(order.identity)['revision'] == current['revision']
    result = new_purchase(replacement)
    new = result['lines'][0]['records'][1]
    assert new['status'] == 'purchased' and not new['supplemental']
    assert result['lines'][0]['remaining_quantity'] == 0
    replacement_parcel = {**parcel, 'id': 'replacement-parcel', 'purchase_record_id': new['id'], 'tracking_number': 'JT456'}
    result = command(fulfillment, order, 'parcels', parcels=[other, replacement_parcel])
    assert '合单' in result['blocked_reason']
    result = command(fulfillment, order, 'plan', plan={**result['plan'], 'consolidation_service_id': 3})
    assert result['blocked_reason'] == ''
    payload = fulfillment.parcels_payload(result['parcels'], procurement.detail(order.identity), complete=True)
    assert {(p['from_order'], p['num']) for p in payload} == {('PO-123', 1), ('PO-456', 2)}
    assert command(fulfillment, order, 'submit')['crossborderbus_order_id'] is None
    assert fulfillment.store.get(order.identity)['manual_submit']


@pytest.mark.parametrize('changes', [
    {'crossborderbus_order_id': 99}, {'create_unknown': True}, {'operation': 'create'},
    {'warehouse_locked': True},
])
def test_started_or_uncertain_report_preserves_purchase_and_parcels(replacement, changes):
    procurement, fulfillment, order, parcel, _ = replacement
    old = fulfillment.store.get(order.identity)
    current = fulfillment.store.change(order.identity, old['revision'], changes)
    with pytest.raises(ValueError, match='不能作废采购'):
        cancel(replacement)
    assert fulfillment.store.get(order.identity) == current
    assert procurement.detail(order.identity)['lines'][0]['records'][0]['status'] == 'purchased'


def test_active_claim_blocks_cancellation_even_if_lease_expired(replacement):
    procurement, fulfillment, order, *_ = replacement
    current = fulfillment.store.get(order.identity)
    assert fulfillment.store.claim(order.identity, 'fetch-label', current['revision'])
    with procurement.store.orders.connect() as conn:
        conn.execute('UPDATE fulfillments SET lease_until=0 WHERE id=?', (order.identity,))
        conn.commit()
    with pytest.raises(ValueError, match='不能作废采购'):
        cancel(replacement)


def test_purchase_write_failure_rolls_back_parcel_removal(replacement):
    procurement, fulfillment, order, *_ = replacement
    current = fulfillment.store.get(order.identity)
    with procurement.store.orders.connect() as conn:
        conn.execute("CREATE TRIGGER reject_cancel BEFORE UPDATE OF status ON order_purchases BEGIN SELECT RAISE(ABORT, '模拟写入失败'); END")
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match='模拟写入失败'):
        cancel(replacement)
    assert fulfillment.store.get(order.identity) == current
    assert procurement.detail(order.identity)['lines'][0]['records'][0]['status'] == 'purchased'


def test_saved_supplement_remains_readable_and_usable_after_original_void(replacement):
    procurement, fulfillment, order, parcel, other = replacement
    cancel(replacement)
    result = new_purchase(replacement)
    new = result['lines'][0]['records'][1]
    # 历史补单标记仅描述来源；仍按有效采购与销售数量检查，不丢失记录。
    with procurement.store.orders.connect() as conn:
        value = json.loads(conn.execute('SELECT record_json FROM order_purchases WHERE id=?', (new['id'],)).fetchone()[0])
        value['supplemental'] = True
        conn.execute('UPDATE order_purchases SET record_json=? WHERE id=?', (json.dumps(value), new['id']))
        conn.commit()
    detail = procurement.detail(order.identity)
    assert detail['lines'][0]['records'][1]['supplemental']
    replacement_parcel = {**parcel, 'id': 'new-parcel', 'purchase_record_id': new['id'], 'tracking_number': 'JT456'}
    assert len(fulfillment.parcels_payload([other, replacement_parcel], detail, complete=True)) == 2
    with pytest.raises(ValueError, match='尚未取得国内运单'):
        fulfillment.parcels_payload([other], detail, complete=True)
    with pytest.raises(ValueError, match='包裹数量超过'):
        fulfillment.parcels_payload([other, {**replacement_parcel, 'quantity': 3}], detail, complete=True)
