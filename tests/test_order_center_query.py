"""订单中心跨领域本地检索：全量筛选后分页、阶段组合、时间精度和账号隔离。"""

from datetime import datetime, timezone
import json
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from erp_web.facades import order_notification_facade
from erp_web.http_route_units import order_routes
from erp_web.schemas.order_center import validate_order_center_filters
from erp_web.schemas.orders import OrderSnapshot
from erp_web.services.order_center_query_service import OrderCenterQueryService
from tests.test_alibaba_purchase_sync import domain, setup
from tests.test_order_query_service import bind, save_order


@pytest.fixture
def center(setup):
    procurement, fulfillment, body, _, run = setup
    notifications = SimpleNamespace(
        store=procurement.store.orders, accounts=procurement.accounts_provider, sync_status=lambda: [], shops=lambda: [],
    )
    query = OrderCenterQueryService(procurement, notifications, fulfillment.store)
    order = procurement.store.order(body['order_id'], procurement.accounts_provider())
    snapshot = OrderSnapshot.model_validate(order.model_dump(include=set(OrderSnapshot.model_fields)))
    return query, snapshot, fulfillment, body, run


def change_fulfillment(center, **changes):
    _, order, fulfillment, _, _ = center
    current = fulfillment.store.ensure(order)
    return fulfillment.store.change(order.identity, current['revision'], changes)


def test_keyword_searches_purchase_waybill_declaration_source_and_notes(center):
    query, order, fulfillment, body, run = center
    run()
    change_fulfillment(center, crossborderbus_order_id=987654, remark='易碎请加固', platform_tracking_number='LABEL-123')
    record = query.procurement.store.purchase_record(**body, accounts=query.procurement.accounts_provider())
    with query.procurement.store.orders.connect() as conn:
        changed = record.model_copy(update={'note': '补寄配件'})
        query.procurement.store._save_record(conn, order.identity, changed)
        conn.commit()
    bind(query.procurement, '供应货号-橙色')
    before = fulfillment.store.get(order.identity)
    for term in (record.purchase_order_number, 'yt1', '987654', '易碎', '补寄', 'label-123', '供应货号-橙色'):
        result = query.read(q=term)
        assert [row['id'] for row in result['items']] == [order.identity], term
    assert query.read(q='record_json')['total'] == 0
    assert query.read(q='不存在的关键词')['total'] == 0
    assert fulfillment.store.get(order.identity) == before


def test_query_does_not_create_fulfillment_or_call_remote_clients(center, monkeypatch):
    query, order, fulfillment, _, _ = center
    assert not fulfillment.store.tracked(order)
    monkeypatch.setattr(fulfillment.client, 'request', lambda *a, **k: pytest.fail('本地检索不得发出仓库请求'))
    monkeypatch.setattr(fulfillment, 'sync', lambda *a: pytest.fail('本地检索不得启动同步'))
    assert query.read()['total'] == 1
    assert not fulfillment.store.tracked(order)


def test_stage_filters_combine_and_use_current_declaration_not_history(center):
    query, order, _, _, run = center
    assert query.read(procurement_status='purchased', declaration_status='unsubmitted', has_waybill='no')['total'] == 1
    run()
    assert query.read(procurement_status='purchased', declaration_status='unsubmitted', has_waybill='yes')['total'] == 1
    change_fulfillment(center, create_unknown=True)
    assert query.read(declaration_status='unknown')['total'] == 1
    assert query.read(declaration_status='created')['total'] == 0
    change_fulfillment(center, create_unknown=False, crossborderbus_order_id=55, fulfillment_status='WAREHOUSE_RECEIVED')
    assert query.read(declaration_status='created', warehouse_status='WAREHOUSE_RECEIVED')['total'] == 1
    assert query.read(declaration_status='unsubmitted')['total'] == 0
    change_fulfillment(center, crossborderbus_order_id=None, fulfillment_status='NEW',
                       attempt_history=[{'crossborderbus_order_id': 55, 'order_number': 'OLD-55', 'archived_at': '2026-10-18T00:00:00Z'}])
    assert query.read(declaration_status='unsubmitted')['total'] == 1
    assert query.read(declaration_status='created')['total'] == 0
    assert query.read(q='OLD-55')['items'][0]['id'] == order.identity


def test_submitting_and_failed_declaration_do_not_count_as_created(center):
    query, order, fulfillment, _, _ = center
    change_fulfillment(center, operation='create')
    with fulfillment.store.orders.connect() as conn:
        conn.execute("UPDATE fulfillments SET claim='creating',lease_until=? WHERE id=?", (time.time() + 60, order.identity))
        conn.commit()
    assert query.read(declaration_status='submitting')['total'] == 1
    assert query.read(declaration_status='created')['total'] == 0
    with fulfillment.store.orders.connect() as conn:
        conn.execute("UPDATE fulfillments SET claim='',lease_until=0 WHERE id=?", (order.identity,))
        conn.commit()
    change_fulfillment(center, error_message='创建失败', fulfillment_status='EXCEPTION')
    assert query.read(declaration_status='error', exception_type='declaration')['total'] == 1
    assert query.read(warehouse_status='EXCEPTION')['total'] == 0
    assert query.read(exception_type='warehouse')['total'] == 0
    change_fulfillment(center, crossborderbus_order_id=55)
    assert query.read(warehouse_status='EXCEPTION', exception_type='warehouse')['total'] == 1


def test_logistics_filters_account_for_multiple_packages_and_manual_unknowns(center):
    query, _, fulfillment, _, run = center
    run()
    assert query.read(domestic_status='unknown')['total'] == 1
    assert query.read(domestic_carrier='圆通')['total'] == 1
    records = query.procurement.store.records_many([query.procurement.store.order(
        center[1].identity, query.procurement.accounts_provider())])[center[1].identity]
    record = records[0]
    data = record.progress['data']
    data['logistics'][0]['status'] = 'SIGN'
    with query.procurement.store.orders.connect() as conn:
        conn.execute('UPDATE purchase_progress SET value=? WHERE record_id=?',
                     (json.dumps(record.progress), record.id))
        conn.commit()
    assert query.read(domestic_status='signed')['total'] == 1
    parcels = fulfillment.store.get(center[1].identity)['parcels']
    change_fulfillment(center, parcels=parcels + [{**parcels[0], 'id': 'manual', 'tracking_number': 'SF-UNKNOWN', 'carrier': '顺丰'}])
    assert query.read(domestic_status='signed')['total'] == 0
    assert query.read(domestic_status='unknown', domestic_carrier='顺丰')['total'] == 1


def test_receiving_warehouse_carrier_and_exception_filters(center):
    query, order, _, _, _ = center
    snapshot = order.model_copy(update={'delivery': order.delivery.model_copy(update={'carrier': 'CEL'})})
    save_order(query.procurement, snapshot)
    with query.procurement.store.orders.connect() as conn:
        conn.execute('INSERT OR REPLACE INTO order_receiving_warehouses VALUES (?,?)',
                     (order.identity, '{"warehouse_name":"义乌一仓","section_name":"华东分区","carrier":"cel"}'))
        conn.commit()
    assert query.read(receiving_warehouse='义乌', platform_carrier='cel')['total'] == 1
    assert query.read(receiving_warehouse='深圳')['total'] == 0
    change_fulfillment(center, label_error='面单获取失败')
    assert query.read(exception_type='label')['total'] == 1
    assert query.read(exception_type='any')['total'] == 1
    assert query.read(exception_type='warehouse')['total'] == 0
    change_fulfillment(center, crossborderbus_order_id=55, error_message='仓库查询失败')
    assert query.read(exception_type='warehouse')['total'] == 1


@pytest.mark.parametrize(('deadline', 'condition', 'expected'), [
    ('2026-10-18T01:00:00Z', {'deadline_status': 'today'}, 1),
    ('2026-10-18T16:00:00Z', {'deadline_status': 'tomorrow'}, 1),
    ('2026-10-18T01:00:00Z', {'deadline_status': 'overdue'}, 1),
    ('2026-10-18', {'deadline_status': 'overdue'}, 0),
    ('2026-10-18T01:00:00', {'deadline_status': 'overdue'}, 0),
    ('2026-10-17', {'deadline_status': 'overdue'}, 1),
    ('2026-10-18T16:00:00Z', {'deadline_from': '2026-10-19', 'deadline_to': '2026-10-19'}, 1),
    ('', {'deadline_status': 'today'}, 0),
])
def test_deadline_filters_preserve_timezone_and_date_precision(center, deadline, condition, expected):
    query, order, _, _, _ = center
    save_order(query.procurement, order.model_copy(update={'shipment_deadline': deadline}))
    now = datetime(2026, 10, 18, 8, tzinfo=timezone.utc)
    assert query.read(now=now, time_zone='Asia/Shanghai', **condition)['total'] == expected
    save_order(query.procurement, order.model_copy(update={'shipment_deadline': deadline, 'state': 'shipped'}))
    if condition.get('deadline_status'):
        assert query.read(now=now, time_zone='Asia/Shanghai', **condition)['total'] == 0


def test_deadline_overdue_uses_actual_instant_across_dst_fallback(center):
    query, order, _, _, _ = center
    save_order(query.procurement, order.model_copy(update={'shipment_deadline': '2026-11-01T05:30:00Z'}))
    now = datetime(2026, 11, 1, 6, 15, tzinfo=timezone.utc)
    assert query.read(now=now, deadline_status='overdue', time_zone='America/New_York')['total'] == 1


def test_filtering_precedes_pagination_and_counts_and_respects_accounts(center):
    query, order, _, _, _ = center
    for index in range(125):
        cloned = order.model_copy(update={'order_id': f'bulk-{index:03}', 'title': '跨页检索',
                                          'state': 'shipped' if index % 2 else 'pending_shipment'})
        save_order(query.procurement, cloned)
    save_order(query.procurement, order.model_copy(update={'account_id': 'other-account', 'title': '跨页检索'}))
    result = query.read(q='跨页检索', state='pending_shipment', procurement_status='unpurchased', offset=50, limit=9)
    assert result['total'] == 63 and len(result['items']) == 9
    assert result['counts'] == {'pending_shipment': 63, 'shipped': 62}
    assert all(row['account_id'] == order.account_id for row in result['items'])
    assert query.read(platform='ozon', q='跨页检索')['total'] == 0
    assert query.read(platform='ozon')['counts'] == {}
    assert query.read(q='跨页检索', offset=125)['items'] == []


@pytest.mark.parametrize('filters', [
    {'declaration_status': 'made-up'}, {'has_waybill': 'maybe'}, {'deadline_from': '2026-02-30'},
    {'deadline_from': '2026-10-19', 'deadline_to': '2026-10-18'}, {'time_zone': 'bad/zone'},
    {'domestic_carrier': 'a' * 201}, {'unknown_field': 'x'},
])
def test_invalid_filters_are_rejected(filters):
    with pytest.raises(ValueError):
        validate_order_center_filters(filters)


def test_http_passes_filters_to_local_facade_without_platform_calls(center, monkeypatch):
    query, _, fulfillment, _, run = center
    run()
    monkeypatch.setattr(order_notification_facade, 'get_context', lambda: SimpleNamespace(
        order_notifications=query.notifications, order_procurement=query.procurement, fulfillment=fulfillment,
    ))
    monkeypatch.setattr(fulfillment, 'sync', lambda *a: pytest.fail('筛选不能查询仓库接口'))
    class Handler:
        def send_json(self, value, status=200):
            self.value, self.status = value, status
    handler = Handler()
    assert order_routes.handle_get(handler, urlsplit('/api/orders?has_waybill=yes&declaration_status=unsubmitted&q=YT1&limit=9'))
    assert handler.status == 200 and handler.value['total'] == 1
    assert handler.value['items'][0]['procurement_status'] == 'purchased'
    assert order_routes.handle_get(handler, urlsplit('/api/orders?deadline_from=bad-date'))
    assert handler.status == 400
