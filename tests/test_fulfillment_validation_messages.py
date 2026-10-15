"""报单阻断提示区分采购超量、分配冲突、已有运单和缺少运单。"""

from copy import deepcopy
import json

import pytest

from erp_web.schemas.fulfillment import FulfillmentError
from tests.test_fulfillment import domain, ready, command


@pytest.fixture
def validation(domain):
    service, _, order = ready(domain)
    current = {**service.store.get(order.identity), **service.detail(order.identity)}
    _, detail = service.order_detail(order.identity)
    return service, order, current, detail


def reason(setup, detail=None, parcels=None):
    service, order, current, original = setup
    value = {**current, 'parcels': current['parcels'] if parcels is None else parcels}
    return service.readiness(order, detail or original, value, current['rule'], current['plan'])


def test_excess_purchase_precedes_missing_allocation_and_conflict(validation):
    _, _, _, detail = validation
    detail = deepcopy(detail)
    record = detail['lines'][0]['records'][0]
    detail['lines'][0]['records'].append({**record, 'id': 'replacement', 'purchase_order_number': 'PO-456',
        'supplemental': True, 'progress': {'state': 'conflict', 'message': '包裹数量超过订单或采购数量，请核对。'}})
    # 更前面的另一行尚无运单，也不能遮挡后面的采购超量。
    earlier = deepcopy(detail['lines'][0])
    earlier['line']['sku'] = 'OTHER'
    earlier['selection']['line_key'] = 'other'
    earlier['records'] = [{**record, 'id': 'other-record'}]
    detail['lines'].insert(0, earlier)
    message = reason(validation, detail, [])
    assert message == 'SKU SKU-1 销售需要 2 件，当前有效采购共 4 件，超出 2 件。请先作废不再用于履约的原采购记录，再确认包裹分配。'
    assert '等待发货' not in message
    service = validation[0]
    with pytest.raises(FulfillmentError, match='当前有效采购共 4 件'):
        service.parcels_payload([], detail, complete=True)


def test_conflict_identifies_sku_purchase_and_saved_reason(validation):
    detail = deepcopy(validation[3])
    detail['lines'][0]['records'][0]['progress'] = {'state': 'conflict', 'message': '运单与已保存的包裹不同，请确认包裹分配'}
    message = reason(validation, detail, [])
    assert 'SKU SKU-1 的采购单 PO-123 存在包裹分配冲突' in message
    assert '运单与已保存的包裹不同' in message
    assert '包裹尚未齐全' not in message


@pytest.mark.parametrize('assigned', [0, 1])
def test_known_waybill_explains_unallocated_quantity(validation, assigned):
    detail = deepcopy(validation[3])
    detail['lines'][0]['records'][0]['progress'] = {'state': 'pending_assignment', 'data': {
        'logistics': [{'tracking_number': 'YT123'}]}}
    parcels = [{**validation[2]['parcels'][0], 'quantity': assigned}] if assigned else []
    message = reason(validation, detail, parcels)
    assert f'SKU SKU-1 的采购单 PO-123：采购 2 件，已分配 {assigned} 件，还缺 {2 - assigned} 件' in message
    assert '已有国内运单，但包裹分配未齐全' in message
    assert '等待发货' not in message


@pytest.mark.parametrize('progress', [None, {'state': 'synced', 'data': {'logistics': []}},
    {'state': 'error', 'data': {'logistics': None}}])
def test_missing_waybill_does_not_claim_supplier_has_not_shipped(validation, progress):
    detail = deepcopy(validation[3])
    detail['lines'][0]['records'][0]['progress'] = progress
    message = reason(validation, detail, [])
    assert '采购单 PO-123' in message and '尚未取得国内运单' in message
    assert '请刷新采购物流' in message and '如已发货' in message


def test_assigned_parcel_is_evidence_even_without_logistics_snapshot(validation):
    parcels = [{**validation[2]['parcels'][0], 'quantity': 1}]
    assert '已有国内运单，但包裹分配未齐全' in reason(validation, parcels=parcels)


def test_missing_purchase_and_empty_order_have_distinct_messages(validation):
    detail = deepcopy(validation[3])
    detail['lines'][0]['records'] = []
    assert '有效采购 0 件、直接录入包裹 0 件，还缺 2 件。请直接录入包裹' in reason(validation, detail, [])
    detail['lines'] = []
    assert reason(validation, detail, []) == '订单缺少商品明细，请同步订单后核对。'


def test_cancelled_history_does_not_block_valid_assignment(validation):
    detail = deepcopy(validation[3])
    record = detail['lines'][0]['records'][0]
    detail['lines'][0]['records'].append({**record, 'id': 'cancelled', 'status': 'cancelled',
        'progress': {'state': 'conflict', 'message': '历史冲突'}})
    assert reason(validation, detail) == ''


def test_manual_allocation_can_resolve_conflict_without_bypassing_quantity(validation):
    service, _, current, detail = validation
    detail = deepcopy(detail)
    detail['lines'][0]['records'][0]['progress'] = {'state': 'conflict', 'message': '待人工确认'}
    assert service.parcels_payload(current['parcels'], detail, complete=True)
    with pytest.raises(FulfillmentError, match='采购单 PO-123 包裹数量超过采购数量：采购 2 件，已分配 3 件'):
        service.parcels_payload([{**current['parcels'][0], 'quantity': 3}], detail)


def test_detail_and_submit_report_the_same_legacy_purchase_conflict(validation):
    service, order, _, detail = validation
    record = deepcopy(detail['lines'][0]['records'][0])
    record.update(id='old-supplement', request_id='old-supplement', purchase_order_number='PO-456', supplemental=True)
    with service.store.orders.connect() as conn:
        row = conn.execute('SELECT * FROM order_purchases WHERE id=?', (detail['lines'][0]['records'][0]['id'],)).fetchone()
        conn.execute('INSERT INTO order_purchases VALUES (?,?,?,?,?,?,?,?,?)',
            (record['id'], order.identity, record['line_key'], record['request_id'], 'fixture', row['line_signature'],
             record['quantity'], 'purchased', json.dumps(record)))
        conn.commit()
    message = service.detail(order.identity)['blocked_reason']
    assert 'SKU SKU-1 销售需要 2 件，当前有效采购共 4 件' in message
    with pytest.raises(FulfillmentError) as exc:
        command(service, order, 'submit')
    assert str(exc.value) == message
    assert not service.store.get(order.identity)['crossborderbus_order_id']
