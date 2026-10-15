"""物流配置的筛选、版本隔离和采购快照回归；不调用真实采购接口。"""
import json
import pytest

from erp_web.schemas.logistics import LogisticsAddressRequest
from erp_web.stores.logistics_store import LogisticsStore
from tests.test_order_procurement import domain, confirm, purchase_body
from tests.test_receiving_warehouse import with_handover
from tests.receiving_warehouse_support import receiving_service, ADDRESS


def configure(service, **updates):
    body = {'revision': 0, 'warehouse_id': 10, 'name': '义乌收货点', 'address': ADDRESS,
            'rules': [{'carrier': 'CEL'}], **updates}
    result = service.logistics.save(body)
    return next(a for a in result['addresses'] if a['id'] == body.get('id') or not body.get('id') and a['name'] == body['name'])


def edit(service, configured, **updates):
    body = {k: v for k, v in configured.items() if k in LogisticsAddressRequest.model_fields}
    return configure(service, **{**body, **updates})


def select(service, order, address, **updates):
    view = service.detail(order.identity)
    return service.save({'order_id': order.identity, 'revision': view['revision'], 'section_id': 1,
                         'address_id': address['id'], 'address_revision': address['revision'],
                         'delivery_key': view['delivery_key'], 'confirmed': True, **updates})


def replace_order(procurement, order):
    with procurement.store.orders.connect() as conn:
        conn.execute('UPDATE orders SET snapshot=? WHERE id=?', (order.model_dump_json(), order.identity))
        conn.commit()


def test_multiple_addresses_filtered_by_platform_carrier_only(domain):
    procurement, order, _ = domain
    service = receiving_service(procurement)
    cel = configure(service, is_default=True)
    uni = configure(service, name='深圳收货点', rules=[{'carrier': 'UNI'}])
    configure(service, name='未配置', rules=[])
    assert service.detail(order.identity)['candidates'] == []
    assert not service.detail(order.identity)['can_confirm']
    with pytest.raises(ValueError, match='尚未识别'):
        select(service, order, cel)
    order = with_handover(order, 'CEL Yiwu Extra Small')
    replace_order(procurement, order)
    assert [a['id'] for a in service.detail(order.identity)['candidates']] == [cel['id']]
    assert select(service, order, cel)['warehouse']['carrier'] == 'CEL'
    with pytest.raises(ValueError, match='承接规则'):
        select(service, order, uni)
    # 订单节点或运输方式变化不让采购者重新选择运输方式，只按平台物流商筛选。
    changed = with_handover(order, 'CEL Shenzhen Express')
    replace_order(procurement, changed)
    assert [a['id'] for a in service.detail(order.identity)['candidates']] == [cel['id']]
    assert service.checked(order.identity)['warehouse_id'] == 10
    replace_order(procurement, with_handover(order, 'UNI Jinhua'))
    assert [a['id'] for a in service.detail(order.identity)['candidates']] == [uni['id']]
    with pytest.raises(ValueError, match='物流方案'):
        service.checked(order.identity)


def test_purchase_requests_cannot_override_platform_carrier(domain):
    procurement, order, _ = domain
    replace_order(procurement, with_handover(order, 'CEL Yiwu'))
    service = receiving_service(procurement)
    address = configure(service)
    for field, value in [('carrier', 'UNI'), ('shipping_service', 'EXPRESS')]:
        with pytest.raises(ValueError):
            select(service, order, address, **{field: value})


def test_default_is_per_warehouse_and_stale_edits_are_rejected(domain):
    procurement, _, _ = domain
    service = receiving_service(procurement)
    first = configure(service, is_default=True)
    second = configure(service, name='第二地址', is_default=True)
    third = configure(service, warehouse_id=20, name='另一合作仓', is_default=True)
    current = service.logistics.detail()['addresses']
    assert {a['id'] for a in current if a['is_default']} == {second['id'], third['id']}
    with pytest.raises(ValueError, match='已更新'):
        edit(service, first, name='过期修改')
    moved = edit(service, second, warehouse_id=20)
    assert moved['warehouse_id'] == 20 and moved['id'] == second['id']
    assert moved['warehouse_name'] == 'B仓'
    assert {a['id'] for a in service.logistics.detail()['addresses'] if a['is_default']} == {second['id']}
    service.client.account = 'bus-other'
    assert service.logistics.detail()['addresses'] == []
    with pytest.raises(ValueError, match='当前账号'):
        edit(service, second, name='越权修改')


def test_config_change_invalidates_both_unpurchased_and_purchased_binding(domain):
    procurement, order, key = domain
    order = with_handover(order, 'CEL Yiwu')
    replace_order(procurement, order)
    service = receiving_service(procurement)
    address = configure(service)
    select(service, order, address)
    changed = edit(service, address, address={**ADDRESS, 'address': '新地址'})
    with pytest.raises(ValueError, match='重新选择'):
        service.checked(order.identity)
    with pytest.raises(ValueError, match='已更新'):
        select(service, order, address)
    select(service, order, changed)
    confirm(procurement, order, key)
    purchase = procurement.record_purchase({**purchase_body(order, key), 'receiving_revision': 2, 'receiving_confirmed': True})
    frozen = service.store.get(order.identity)
    edit(service, changed, enabled=False, address={**ADDRESS, 'address': '再次修改'}, rules=[])
    with pytest.raises(ValueError, match='原绑定已失效'):
        service.checked(order.identity)
    assert service.store.get(order.identity) == frozen
    view = service.detail(order.identity)
    assert view['binding_stale'] and not view['locked'] and view['can_confirm']
    assert view['candidates'] == []
    assert purchase['lines'][0]['records'][0]['receiving_warehouse']['address']['address'] == '新地址'


@pytest.mark.parametrize('changes', [
    {'warehouse_id': 20},
    {'address': {**ADDRESS, 'address': '福州新收货地址', 'cityText': '福州市'}},
    {'warehouse_id': 20, 'address': {**ADDRESS, 'address': '福州新收货地址', 'cityText': '福州市'}},
])
def test_purchased_order_can_rebind_changed_configuration_without_rewriting_purchase(domain, changes):
    procurement, order, key = domain
    replace_order(procurement, with_handover(order, 'CEL Yiwu'))
    service = receiving_service(procurement)
    address = configure(service)
    original = select(service, order, address)['warehouse']
    confirm(procurement, order, key)
    purchase = procurement.record_purchase({**purchase_body(order, key), 'receiving_revision': 1, 'receiving_confirmed': True})
    record = purchase['lines'][0]['records'][0]
    changed = edit(service, address, **changes)
    view = service.detail(order.identity)
    assert view['binding_stale'] and not view['locked']
    assert view['warehouse'] == original
    candidate = view['candidates'][0]
    assert not candidate['frozen'] and candidate['address'] == changed['address']
    assert candidate['warehouse_id'] == changed['warehouse_id']
    with pytest.raises(ValueError, match='收货安排'):
        select(service, order, changed)
    with pytest.raises(ValueError, match='资料已更新'):
        select(service, order, address, existing_purchase_confirmed=True)
    result = select(service, order, changed, existing_purchase_confirmed=True)
    assert not result['binding_stale'] and not result['blocked_reason'] and result['locked']
    assert result['revision'] == original['revision'] + 1
    assert service.checked(order.identity)['address'] == changed['address']
    assert service.checked(order.identity)['warehouse_id'] == changed['warehouse_id']
    assert procurement.detail(order.identity)['lines'][0]['records'][0] == record


def test_disabled_configuration_can_be_replaced_but_unrelated_edit_does_not_unlock(domain):
    procurement, order, key = domain
    replace_order(procurement, with_handover(order, 'CEL Yiwu'))
    service = receiving_service(procurement)
    address = configure(service)
    replacement = configure(service, name='新收货点', warehouse_id=20)
    select(service, order, address)
    confirm(procurement, order, key)
    procurement.record_purchase({**purchase_body(order, key), 'receiving_revision': 1, 'receiving_confirmed': True})
    replacement = edit(service, replacement, address={**ADDRESS, 'address': '新收货点地址'})
    assert not service.detail(order.identity)['binding_stale']
    with pytest.raises(ValueError, match='不能更换'):
        select(service, order, replacement, existing_purchase_confirmed=True)
    edit(service, address, enabled=False)
    result = select(service, order, replacement, existing_purchase_confirmed=True)
    assert result['warehouse']['address_id'] == replacement['id']
    assert not result['binding_stale']


def test_catalog_change_before_selection_rejects_uncooperative_warehouse(domain, monkeypatch):
    procurement, order, _ = domain
    order = with_handover(order, 'CEL Yiwu')
    replace_order(procurement, order)
    service = receiving_service(procurement)
    address = configure(service)
    monkeypatch.setattr(service.client, 'catalog', lambda: {'identity': service.client.identity(), 'sections': []})
    with pytest.raises(ValueError, match='合作范围'):
        select(service, order, address)


def test_old_profiles_migrate_once_without_promoting_free_text_to_rules(domain):
    procurement, order, _ = domain
    service = receiving_service(procurement)
    scope = json.dumps([order.platform, order.account_id, service.client.identity(), 1, 10])
    old = {'warehouse_id': 10, 'warehouse_name': '旧仓', 'address': ADDRESS, 'supported': {'old-key': 'CEL 普货'}}
    with procurement.store.orders.connect() as conn:
        conn.execute('INSERT INTO receiving_warehouse_profiles VALUES (?,?)', (scope, json.dumps(old)))
        conn.commit()
    imported = LogisticsStore(procurement.store.orders).list(service.client.identity())
    assert len(imported) == 1 and imported[0]['rules'] == [] and imported[0]['migrated']
    assert imported[0]['address'] == ADDRESS
    assert LogisticsStore(procurement.store.orders).list(service.client.identity()) == imported


@pytest.mark.parametrize('rule', [
    {'carrier': '随意填写'}, {'carrier': 'CEL', 'platform': 'ozon'},
    {'carrier': 'CEL', 'service_scope': 'selected'},
    {'carrier': 'CEL', 'services': ['EXPRESS']},
    {'carrier': 'CEL', 'node_scope': 'selected'},
])
def test_rules_reject_free_text_or_ambiguous_scopes(domain, rule):
    procurement, _, _ = domain
    with pytest.raises(ValueError):
        configure(receiving_service(procurement), rules=[rule])


def test_legacy_unpurchased_selection_is_readable_but_requires_configured_address(domain):
    procurement, order, _ = domain
    order = with_handover(order, 'CEL Yiwu')
    replace_order(procurement, order)
    service = receiving_service(procurement)
    address = configure(service)
    select(service, order, address)
    legacy = service.store.get(order.identity)
    for field in ('address_id', 'address_revision', 'address_name', 'carrier', 'shipping_service', 'support_rule'):
        legacy.pop(field, None)
    with procurement.store.orders.connect() as conn:
        conn.execute('UPDATE order_receiving_warehouses SET value=? WHERE order_id=?', (json.dumps(legacy), order.identity))
        conn.commit()
    view = service.detail(order.identity)
    assert view['warehouse']['address'] == ADDRESS
    assert '重新选择' in view['blocked_reason']
    with pytest.raises(ValueError, match='重新选择'):
        service.checked(order.identity)
    assert not select(service, order, address)['blocked_reason']


def test_persisted_rule_migration_keeps_addresses_and_confirmed_carriers(domain):
    procurement, order, _ = domain
    service = receiving_service(procurement)
    configured = configure(service)
    old = {**configured, 'rules': [
        {'platform': 'yandex', 'carrier': 'CEL', 'confirmed': True, 'service_scope': 'selected', 'services': ['EXPRESS'], 'node_scope': 'selected', 'node_keys': ['old-node']},
        {'platform': 'yandex', 'carrier': 'UNI', 'confirmed': False, 'service_scope': 'unknown', 'services': [], 'node_scope': 'unknown', 'node_keys': []},
    ]}
    with procurement.store.orders.connect() as conn:
        conn.execute('UPDATE logistics_addresses SET value=? WHERE id=?', (json.dumps(old), configured['id']))
        conn.commit()
    loaded = service.logistics.detail()['addresses'][0]
    assert loaded['address'] == configured['address']
    assert loaded['rules'] == [{'platform': 'yandex', 'carrier': 'CEL'}]
    replace_order(procurement, with_handover(order, 'CEL Yiwu'))
    assert service.detail(order.identity)['candidates'][0]['id'] == configured['id']
