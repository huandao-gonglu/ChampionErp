"""收货安排、采购与报单的边界，不调用真实下单接口。"""
import pytest

from erp_web.schemas.receiving_warehouse import delivery_carrier, delivery_key
from erp_web.schemas.order_handover import OrderHandoverSnapshot
from tests.test_order_procurement import domain, confirm, purchase_body
from tests.receiving_warehouse_support import receiving_service, assign_receiving, ADDRESS


def test_select_before_purchase_reuses_profile_but_not_official_address(domain):
    procurement, order, _ = domain
    service = receiving_service(procurement)
    value = assign_receiving(service, order)
    assert value['warehouse']['address'] == ADDRESS
    assert value['warehouse']['carrier'] == 'CEL'
    assert value['candidates'][0]['id'] == value['warehouse']['address_id']
    assert not value['locked']
    assert value['warehouse']['revision'] == 1
    other = order.model_copy(update={'order_id': 'other'})
    assert delivery_key(other) != delivery_key(order)  # 缺少物流方案时不能跨订单猜测。


def test_confirmation_cooperation_and_stale_revision_are_enforced(domain):
    procurement, order, _ = domain
    service = receiving_service(procurement)
    with pytest.raises(ValueError, match='确认'):
        assign_receiving(service, order, confirmed=False)
    with pytest.raises(ValueError, match='合作范围'):
        assign_receiving(service, order, warehouse_id=999)
    assign_receiving(service, order)
    with pytest.raises(ValueError, match='资料已更新'):
        assign_receiving(service, order, revision=0)


def test_manual_purchase_freezes_warehouse_and_allows_change_after_cancellation(domain):
    procurement, order, key = domain
    service = receiving_service(procurement)
    confirm(procurement, order, key)
    assign_receiving(service, order)
    result = procurement.record_purchase({**purchase_body(order, key), 'receiving_warehouse': service.store.public(service.store.get(order.identity))})
    record = result["purchases"][0]
    assert record['receiving_warehouse']['warehouse_id'] == 10
    assert service.detail(order.identity)['locked']
    with pytest.raises(ValueError, match='不能更换'):
        assign_receiving(service, order, warehouse_id=20)
    with pytest.raises(ValueError, match='不能改写收货地址'):
        assign_receiving(service, order, address={**ADDRESS, 'address': '其他路'})
    procurement.cancel_purchase({'order_id': order.identity, 'record_id': record['id']})
    assert assign_receiving(service, order, warehouse_id=20)['warehouse']['warehouse_id'] == 20
    assert procurement.detail(order.identity)["purchases"][0]['receiving_warehouse']['warehouse_id'] == 10


def test_existing_purchase_requires_confirmation_of_actual_destination(domain):
    procurement, order, key = domain
    service = receiving_service(procurement)
    confirm(procurement, order, key)
    procurement.record_purchase(purchase_body(order, key))
    with pytest.raises(ValueError, match='实际寄往'):
        assign_receiving(service, order, existing_purchase_confirmed=False)
    value = assign_receiving(service, order)
    assert value['locked'] and value['has_purchases']


def test_order_change_during_catalog_does_not_save_stale_assignment(domain, monkeypatch):
    procurement, order, _ = domain
    service = receiving_service(procurement)
    original = service.client.catalog
    def catalog():
        changed = order.model_copy(deep=True)
        changed.delivery.method_id = 'new-method'
        with procurement.store.orders.connect() as conn:
            conn.execute('UPDATE orders SET snapshot=? WHERE id=?', (changed.model_dump_json(), order.identity))
            conn.commit()
        return original()
    monkeypatch.setattr(service.client, 'catalog', catalog)
    with pytest.raises(ValueError, match='物流方案'):
        assign_receiving(service, order)
    assert service.store.get(order.identity) is None


def test_account_switch_blocks_old_receiving_and_isolates_profiles(domain):
    procurement, order, _ = domain
    service = receiving_service(procurement)
    assign_receiving(service, order)
    service.client.account = 'bus-other'
    assert service.detail(order.identity)['candidates'] == []
    with pytest.raises(ValueError, match='账号已变化'):
        service.checked(order.identity)
    with pytest.raises(ValueError, match='账号已变化'):
        assign_receiving(service, order)


def test_shipping_change_can_be_reconfirmed_without_changing_purchased_address(domain):
    procurement, order, key = domain
    service = receiving_service(procurement)
    confirm(procurement, order, key)
    assign_receiving(service, order)
    procurement.record_purchase({**purchase_body(order, key), 'receiving_warehouse': service.store.public(service.store.get(order.identity))})
    changed = order.model_copy(deep=True)
    changed.delivery.method_id = 'new-method'
    with procurement.store.orders.connect() as conn:
        conn.execute('UPDATE orders SET snapshot=? WHERE id=?', (changed.model_dump_json(), order.identity))
        conn.commit()
    assert '物流方案已变化' in service.detail(order.identity)['blocked_reason']
    value = assign_receiving(service, changed, transport_service='Yandex CEL 新方案')
    assert not value['blocked_reason'] and value['warehouse']['address'] == ADDRESS


def test_new_route_requests_are_validated_by_http_contract():
    from erp_web.schemas.requests import validate_request_payload
    body = {'order_id': 'order', 'revision': 0, 'section_id': 143, 'address_id': 'address',
            'address_revision': 1, 'delivery_key': 'key', 'confirmed': True}
    assert validate_request_payload(body, endpoint='/api/orders/receiving-warehouse') == body
    with pytest.raises(ValueError):
        validate_request_payload({**body, 'address_revision': -1}, endpoint='/api/orders/receiving-warehouse')


def with_handover(order, *names):
    changed = order.model_copy(deep=True)
    changed.delivery.carrier = 'YANDEX_MARKET'
    changed.delivery.method_name = 'Самовывоз'
    changed.handover = OrderHandoverSnapshot(state='ready', shipments=[
        {'shipment_id': str(i), 'shipment_type': 'IMPORT',
         'destination': {'id': str(i), 'name': name, 'address': '平台交货地址'}}
        for i, name in enumerate(names, 1)
    ])
    return changed


@pytest.mark.parametrize(('name', 'carrier'), [
    ('CEL Yiwu Extra Small', 'CEL'), ('GUOO(GTA)-YIWU02', 'GUOO(GTA)'),
    ('UNI  Jinhua Futian', 'UNI'), ('cel Yiwu', 'CEL'),
    ('GBS(GUOO) Yiwu', 'GBS(GUOO)'), ('GUOO Yiwu', 'GUOO'),
    ('YANDEX_MARKET / Самовывоз', ''), ('UNION Yiwu', ''),
    ('CELERY Yiwu', ''), ('卖家 UNI 仓', ''), ('', ''),
])
def test_carrier_comes_from_explicit_handover_name_not_delivery_partner(domain, name, carrier):
    procurement, order, _ = domain
    changed = with_handover(order, name)
    with procurement.store.orders.connect() as conn:
        conn.execute('UPDATE orders SET snapshot=? WHERE id=?', (changed.model_dump_json(), order.identity))
        conn.commit()
    value = receiving_service(procurement).detail(order.identity)
    assert value['delivery_name'] == carrier
    assert value['delivery_evidence'] == ([name] if name else [])


@pytest.mark.parametrize('names', [('CEL Yiwu', 'UNI Jinhua'), ('CEL Yiwu', ''), ('CEL Yiwu', '未知交货点')])
def test_conflicting_or_incomplete_handover_does_not_guess_carrier(domain, names):
    _, order, _ = domain
    assert delivery_carrier(with_handover(order, *names))[0] == ''


def test_seller_warehouse_and_unavailable_handover_are_not_carrier_evidence(domain):
    _, order, _ = domain
    changed = with_handover(order, '未知交货点')
    shipment = changed.handover.shipments[0]
    shipment.origin = shipment.destination.model_copy(update={'name': 'CEL Yiwu'})
    assert delivery_carrier(changed)[0] == ''
    shipment.destination.name = 'UNI Jinhua'
    shipment.shipment_type = 'WITHDRAW'
    assert delivery_carrier(changed)[0] == ''
    shipment.shipment_type = 'IMPORT'
    shipment.status = 'ERROR'
    assert delivery_carrier(changed) == ('', [])
    shipment.status = ''
    changed.handover.state = 'unavailable'
    assert delivery_carrier(changed) == ('', [])


def test_confirmation_is_per_order_and_changes_only_for_carrier_not_address(domain):
    _, order, _ = domain
    cel = with_handover(order, 'CEL Yiwu Extra Small')
    uni = with_handover(order, 'UNI Jinhua Futian')
    assert delivery_key(cel) != delivery_key(uni)
    moved = cel.model_copy(deep=True)
    moved.handover.shipments[0].destination.address = '另一官方地址'
    moved.handover.shipments[0].destination.id = '999'
    assert delivery_key(cel) == delivery_key(moved)
    # 同一承运商名称不能证明其他订单的具体运输服务可被仓库承接。
    assert delivery_key(cel) != delivery_key(cel.model_copy(update={'order_id': 'other'}))
    generic = with_handover(order)
    assert delivery_key(generic) != delivery_key(generic.model_copy(update={'order_id': 'other'}))


def test_old_generic_confirmation_keeps_address_but_requires_reconfirmation(domain):
    import json
    procurement, order, _ = domain
    service = receiving_service(procurement)
    assign_receiving(service, order)
    old = service.store.get(order.identity)
    old['delivery_key'] = 'old-generic-key'
    old['transport_service'] = 'YANDEX_MARKET / Самовывоз'
    with procurement.store.orders.connect() as conn:
        conn.execute('UPDATE order_receiving_warehouses SET value=? WHERE order_id=?', (json.dumps(old), order.identity))
        conn.commit()
    value = service.detail(order.identity)
    assert value['warehouse']['address'] == ADDRESS
    assert value['can_confirm'] and value['blocked_reason']
    with pytest.raises(ValueError, match='物流方案'):
        service.checked(order.identity)
    assert not assign_receiving(service, order)['blocked_reason']
