"""Yandex 交货点对应与本单服务：不混用 ID，不依赖目的国和默认方案。"""

import pytest

from erp_web.schemas.fulfillment import FulfillmentError
from erp_web.services.fulfillment_routing import handover_target
from tests.test_fulfillment import domain, ready, command


def request(service, order, **overrides):
    value = service.detail(order.identity)
    return {"order_id": order.identity, "revision": value["revision"], "plan": value["plan"],
            "handover_key": value["handover_target"]["key"],
            "warehouse_link_revision": (value["warehouse_link"] or {}).get("revision", 0), **overrides}


def changed_address(order, address="义乌市其他路 2 号"):
    result = order.model_copy(deep=True)
    result.handover.shipments[0].destination.address = address
    return result


def test_target_has_own_id_and_reuses_across_shipments_but_not_accounts(domain):
    _, bus, order, *_ = domain
    target = handover_target(order, bus.identity())
    assert target['warehouse_id'] == 'handover-1'
    other = order.model_copy(deep=True)
    other.handover.shipments[0].shipment_id = 'another-batch'
    other.order_id = 'another-order'
    assert handover_target(other, bus.identity())['key'] == target['key']
    assert handover_target(order.model_copy(update={'account_id': 'other'}), bus.identity())['key'] != target['key']
    assert handover_target(order, 'another-bus')['key'] != target['key']
    assert handover_target(changed_address(order), bus.identity())['key'] != target['key']


def test_missing_id_uses_exact_address_and_multiple_destinations_block(domain):
    _, bus, order, *_ = domain
    order = order.model_copy(deep=True)
    order.handover.shipments[0].destination.id = ''
    assert handover_target(order, bus.identity())['key']
    second = order.handover.shipments[0].model_copy(deep=True)
    second.destination.address = '另一个地址'
    order.handover.shipments.append(second)
    assert '不唯一' in handover_target(order, bus.identity())['reason']


def test_withdraw_uses_origin_and_ignores_cancelled_batch(domain):
    _, bus, order, *_ = domain
    order = order.model_copy(deep=True)
    shipment = order.handover.shipments[0]
    shipment.origin = shipment.destination.model_copy(update={'id': 'pickup'})
    shipment.shipment_type = 'WITHDRAW'
    assert handover_target(order, bus.identity())['warehouse_id'] == 'pickup'
    shipment.status = 'ERROR'
    assert not handover_target(order, bus.identity())['key']


def test_binding_needs_explicit_confirmation_and_atomic_version(domain):
    service, _, order, _, snapshot, _ = domain
    changed = changed_address(order)
    snapshot(changed)
    body = request(service, changed)
    with pytest.raises(FulfillmentError, match='确认'): service.command('plan', body)
    result = service.command('plan', {**body, 'confirm_warehouse': True})
    assert result['warehouse_link'] == {'section_id': 1, 'warehouse_id': 10, 'revision': 1}
    body = request(service, changed, warehouse_link_revision=0)
    with pytest.raises(ValueError, match='对应关系已变化'): service.command('plan', body)


def test_services_choose_one_core_and_available_options_only(domain, monkeypatch):
    service, bus, order, *_ = domain
    monkeypatch.setattr(bus, 'services', lambda *args: {'core_data': [{'id': 2, 'name': '贴单'}, {'id': 4, 'name': '合包'}], 'optional_data': [{'id': 3, 'name': '拍照'}]})
    for ids in [[], [2, 4], [999], [2, 2]]:
        with pytest.raises(FulfillmentError): service.command('plan', request(service, order, plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': ids}))
    result = service.command('plan', request(service, order, plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [4, 3]}))
    assert [s['name'] for s in result['selected_services']] == ['合包', '拍照']


def test_non_cooperating_warehouse_rejected_even_when_confirmed(domain):
    service, _, order, *_ = domain
    with pytest.raises(FulfillmentError, match='合作范围'):
        service.command('plan', request(service, order, confirm_warehouse=True, plan={'section_id': 1, 'warehouse_id': 999, 'service_ids': [2]}))


def test_snapshot_change_during_service_query_rolls_back_binding(domain, monkeypatch):
    service, bus, order, _, snapshot, _ = domain
    body = request(service, order)
    def services(*args):
        snapshot(changed_address(order))
        return {'core_data': [{'id': 2, 'name': '贴单'}], 'optional_data': [{'id': 3, 'name': '拍照'}]}
    monkeypatch.setattr(bus, 'services', services)
    before = service.store.get(order.identity)
    with pytest.raises(FulfillmentError, match='地址或账号已变化'): service.command('plan', body)
    assert service.store.get(order.identity)['revision'] == before['revision']
    assert not service.detail(order.identity)['warehouse_link']


def test_order_plan_without_default_country_or_inventory_warehouse_can_submit(domain):
    service, bus, order, parcel, snapshot, _ = domain
    order = order.model_copy(update={'delivery': order.delivery.model_copy(update={'country': '', 'warehouse_id': '', 'method_id': ''})})
    snapshot(order)
    for rule in service.store.rules(): service.store.delete_rule(rule['id'])
    command(service, order, 'label', label={'url': 'https://example.com/label.pdf', 'tracking_number': 'BOX1'})
    command(service, order, 'parcels', parcels=[parcel])
    assert not service.detail(order.identity)['blocked_reason']
    assert not service.process_one(order)
    command(service, order, 'submit')
    assert service.process_one(order)
    payload = next(body for path, body in bus.calls if path.endswith('createOrder'))
    assert payload['sid'] == 10 and payload['section_id'] == 1
    assert 'country' not in payload['order_data'][0]
    assert payload['order_data'][0]['add_service'] == [{'id': 2}, {'id': 3}]


def test_address_changed_after_submit_never_creates_in_old_warehouse(domain):
    service, bus, order = ready(domain)
    command(service, order, 'submit')
    domain[4](changed_address(order))
    service.process_one(order)
    assert not bus.calls


def test_address_changed_while_refreshing_services_never_sends_create(domain, monkeypatch):
    service, bus, order = ready(domain)
    def services(*args):
        domain[4](changed_address(order))
        return {'core_data': [{'id': 2, 'name': '贴单'}], 'optional_data': [{'id': 3, 'name': '拍照'}]}
    monkeypatch.setattr(bus, 'services', services)
    service.process_one(order)
    assert not bus.calls
    assert '资料已变化' in service.detail(order.identity)['error_message']
