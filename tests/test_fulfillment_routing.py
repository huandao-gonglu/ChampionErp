"""采购收货仓决定报单仓；官方交货地址仅作为参考信息。"""
import pytest

from erp_web.schemas.fulfillment import FulfillmentError
from tests.test_fulfillment import domain, ready, command


def request(service, order, **overrides):
    value = service.detail(order.identity)
    return {'order_id': order.identity, 'revision': value['revision'], 'plan': value['plan'],
            'receiving_revision': value['receiving_warehouse']['revision'], **overrides}


def changed_address(order):
    result = order.model_copy(deep=True)
    result.handover.shipments[0].destination.address = '完全不同的官方交货点'
    return result


def test_official_address_changes_do_not_change_receiving_warehouse(domain):
    service, bus, order = ready(domain)
    domain[4](changed_address(order))
    assert service.detail(order.identity)['receiving_warehouse']['warehouse_id'] == 10
    assert not service.detail(order.identity)['blocked_reason']
    assert service.process_one(order)
    assert any(path.endswith('createOrder') for path, _ in bus.calls)


def test_missing_platform_carrier_blocks_new_report_without_changing_address(domain):
    service, bus, order = ready(domain)
    before = service.detail(order.identity)['receiving_warehouse']
    domain[4](order.model_copy(update={'handover': None}))
    current = service.detail(order.identity)
    assert current['blocked_reason']
    assert current['receiving_warehouse']['address'] == before['address']
    service.process_one(order)
    assert not any(path.endswith('createOrder') for path, _ in bus.calls)


def test_report_cannot_change_purchase_warehouse(domain):
    service, _, order, *_ = domain
    with pytest.raises(ValueError, match='采购收货仓一致'):
        service.command('plan', request(service, order, plan={'section_id': 1, 'warehouse_id': 20, 'service_ids': [2]}))


def test_services_choose_one_core_and_available_options_only(domain, monkeypatch):
    service, bus, order, *_ = domain
    monkeypatch.setattr(bus, 'services', lambda *args: {'core_data': [{'id': 2, 'name': '贴单'}, {'id': 4, 'name': '合包'}], 'optional_data': [{'id': 3, 'name': '拍照'}]})
    for ids in [[], [2, 4], [999], [2, 2]]:
        with pytest.raises(FulfillmentError): service.command('plan', request(service, order, plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': ids}))
    result = service.command('plan', request(service, order, plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [4, 3]}))
    assert [s['name'] for s in result['selected_services']] == ['合包', '拍照']


def test_delivery_change_during_services_rejects_plan(domain, monkeypatch):
    service, bus, order, _, snapshot, _ = domain
    def services(*args):
        changed = order.model_copy(deep=True)
        changed.delivery.method_id = 'new-service'
        snapshot(changed)
        return {'core_data': [{'id': 2, 'name': '贴单'}], 'optional_data': [{'id': 3, 'name': '拍照'}]}
    monkeypatch.setattr(bus, 'services', services)
    before = service.store.get(order.identity)
    with pytest.raises(ValueError, match='物流方案已变化'):
        service.command('plan', request(service, order))
    assert service.store.get(order.identity)['revision'] == before['revision']


def test_delivery_change_before_create_never_sends(domain, monkeypatch):
    service, bus, order = ready(domain)
    def services(*args):
        changed = order.model_copy(deep=True)
        changed.delivery.carrier = 'GUOO'
        domain[4](changed)
        return {'core_data': [{'id': 2, 'name': '贴单'}], 'optional_data': [{'id': 3, 'name': '拍照'}]}
    monkeypatch.setattr(bus, 'services', services)
    service.process_one(order)
    assert not bus.calls
    assert '资料已变化' in service.detail(order.identity)['error_message']


def test_legacy_address_links_never_authorize_new_report(domain):
    import json
    service, _, order = ready(domain)
    with service.store.orders.connect() as conn:
        conn.execute('DELETE FROM order_receiving_warehouses WHERE order_id=?', (order.identity,))
        conn.execute('INSERT OR REPLACE INTO fulfillment_warehouse_links VALUES (?,?,?)', ('old-address', 1, json.dumps({'section_id': 1, 'warehouse_id': 10})))
        conn.commit()
    assert service.detail(order.identity)['plan'] is None
    assert service.detail(order.identity)['blocked_reason']
    assert not service.process_one(order)


def test_local_receiving_validation_failure_is_not_unknown_remote_result(domain):
    service, bus, order = ready(domain)
    value = service.store.get(order.identity)
    changed = order.model_copy(deep=True)
    changed.delivery.carrier = 'GUOO'
    domain[4](changed)
    assert service._execute(order, value, 'create')
    result = service.store.get(order.identity)
    assert not bus.calls
    assert not result['create_unknown']
    assert '物流方案已变化' in result['error_message']


def test_existing_remote_report_remains_readable_without_receiving(domain):
    service, _, order = ready(domain)
    service.process_one(order)
    with service.store.orders.connect() as conn:
        conn.execute('DELETE FROM order_receiving_warehouses')
        conn.commit()
    value = service.detail(order.identity)
    assert value['crossborderbus_order_id'] == 99 and value['plan']['warehouse_id'] == 10
    assert not value['plan_editable']
