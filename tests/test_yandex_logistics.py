"""远端发布仓约束核价；本地报单仓绝不扩大或缩小核价候选。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from erp_web.context import get_context
from erp_web.services.yandex_logistics_service import YandexLogisticsService
from erp_web.services.pricing_shipping import PricingShipping
from erp_web.international_shipping.tariff_store import import_template
from erp_web.http_route_units import pricing_workspace_routes
from erp_web.schemas.requests import validate_request_payload
from tests.test_tariff_store import YA, xlsx
from tests.test_international_shipping import COMMON, target
from tests.pricing_test_utils import official_commission
from tests.test_order_procurement import domain  # noqa: F401
from tests.receiving_warehouse_support import receiving_service, ADDRESS


@pytest.fixture
def routing(tmp_path, monkeypatch):
    config = {'yandex': {'api_token': 'test-token', 'business_id': '222', 'campaign_id': '111',
                         'placement_type': 'FBS', 'stock_update_mode': 'business'}}
    store = get_context().config
    monkeypatch.setattr(store, 'load_store_config', lambda: deepcopy(config))
    rows = [{'id': ident, 'name': name, 'models': [{'placementType': 'FBS', 'apiAvailability': 'AVAILABLE'}]}
            for ident, name in [(31, 'CEL 发布仓'), (32, 'GUOO 发布仓')]]
    calls = []
    def fetch(*args):
        calls.append(args)
        return deepcopy(rows)
    monkeypatch.setattr('erp_web.services.yandex_logistics_service.fetch_yandex_partner_warehouses', fetch)
    source = tmp_path / 'yandex.xlsx'
    xlsx(source, 'yandex', rows=[{**YA, 'group': 'Other', 'fixed': 200},
                               {**YA, 'group': 'Other', 'carrier': 'GUOO(GTA)', 'fixed': 100}])
    root = tmp_path / 'tariffs'
    import_template(source, 'yandex', root)
    service = YandexLogisticsService(store, root)
    monkeypatch.setattr(get_context(), '_yandex_logistics', service)
    options = service.detail(refresh=True)
    body = {'revision': 0, 'business_id': '222', 'campaign_id': '111',
            'bindings': [{'warehouse_id': 31, 'carrier': 'CEL', 'shipping_mode': 'Economy', 'enabled': True}]}
    return service, body, config, rows, calls


def shipping(service, config):
    resolver = PricingShipping(service.rules_dir, config, yandex_plans_loader=service.pricing_plans)
    resolver.commission_resolver = official_commission
    return resolver


def test_only_confirmed_remote_warehouse_routes_can_quote(routing):
    service, body, config, _, calls = routing
    service.save(body)
    resolver = shipping(service, config)
    quote = resolver(COMMON, target('yandex'))
    assert 'error' not in quote and len(quote['candidates']) == 1
    assert quote['evidence']['carrier'] == 'CEL'
    plan = quote['evidence']['fulfillment_plan']
    assert plan['publish_warehouse_id'] == 31
    assert plan['shipping_mode'] == 'Economy'
    assert 'address_id' not in plan and 'receiving_warehouse_id' not in plan
    assert get_context()._fulfillment is None
    before = len(calls)
    resolver({**COMMON, 'weight_kg': '.5'}, target('yandex'))
    assert len(calls) == before
    assert service.validate(plan) == plan


def test_explicit_shipping_option_uses_requested_warehouse_even_when_more_expensive(routing):
    service, body, config, _, calls = routing
    body['bindings'].append({'warehouse_id': 32, 'carrier': 'GUOO_GTA', 'shipping_mode': 'Economy', 'enabled': True})
    service.save(body)
    before = len(calls)
    options = service.detail()['pricing_options']
    assert len(calls) == before  # 下拉列表只读取本地已同步配置。
    assert {p['publish_warehouse_id'] for p in options} == {31, 32}
    selected = next(p for p in options if p['publish_warehouse_id'] == 31)
    resolver = shipping(service, config)
    automatic = resolver(COMMON, target('yandex'))
    explicit = resolver(COMMON, target('yandex', shipping_selection={
        'warehouse_id': 31, 'route_id': selected['route_id'],
    }))
    assert automatic['evidence']['fulfillment_plan']['publish_warehouse_id'] == 32
    assert explicit['evidence']['fulfillment_plan']['publish_warehouse_id'] == 31
    assert len(explicit['candidates']) == 1
    assert float(explicit['amount']) > float(automatic['amount'])
    # 恢复全部比价不受前一次过滤污染，同一批只同步一次仓库。
    assert resolver(COMMON, target('yandex'))['evidence']['fulfillment_plan']['publish_warehouse_id'] == 32
    assert len(calls) == before + 1


def test_unavailable_or_ineligible_selected_shipping_never_falls_back(routing):
    service, body, config, _, _ = routing
    service.save(body)
    plan = service.plans()[0]
    resolver = shipping(service, config)
    resolver.commission_resolver = lambda *_: pytest.fail('不可用物流不应查询佣金')
    for selection in ({'warehouse_id': 999, 'route_id': plan['route_id']},
                      {'warehouse_id': 31, 'route_id': 'removed'}):
        assert '指定的发布仓库或物流方式已不可用' in resolver(COMMON, target('yandex', shipping_selection=selection))['error']
    result = resolver({**COMMON, 'liquid': True}, target('yandex', shipping_selection={
        'warehouse_id': 31, 'route_id': plan['route_id'],
    }))
    assert '没有可用的国际物流报价' in result['error']


def test_local_declaration_warehouses_never_change_price_candidates(routing, domain):
    service, body, config, _, _ = routing
    service.save(body)
    plan = service.plans()[0]
    before = service.configuration_fingerprint()
    procurement, _, _ = domain
    local = receiving_service(procurement).logistics
    result = local.save({'revision': 0, 'warehouse_id': 10, 'name': '本地报单 GUOO', 'address': ADDRESS, 'rules': [{'carrier': 'GUOO_GTA'}]})
    quote = shipping(service, config)(COMMON, target('yandex'))
    assert quote['evidence']['carrier'] == 'CEL' and len(quote['candidates']) == 1
    address = result['addresses'][0]
    from erp_web.schemas.logistics import LogisticsAddressRequest
    local.save({**{k: v for k, v in address.items() if k in LogisticsAddressRequest.model_fields}, 'enabled': False})
    assert service.validate(plan) == plan
    assert service.configuration_fingerprint() == before


def test_no_mapping_or_unconfirmed_service_blocks_without_remote_query(routing):
    service, body, config, _, calls = routing
    for bindings in ([], [{**body['bindings'][0], 'shipping_mode': '', 'enabled': False}]):
        body['revision'] = service.configuration()['revision']
        service.save({**body, 'bindings': bindings})
        before = len(calls)
        resolver = shipping(service, config)
        resolver.commission_resolver = lambda *_: pytest.fail('没有可用物流不能查询佣金')
        assert '暂无可用物流方案' in resolver(COMMON, target('yandex'))['error']
        assert len(calls) == before


@pytest.mark.parametrize('change', ['disabled', 'account', 'warehouse', 'models', 'group'])
def test_stale_remote_plan_cannot_be_reused(routing, change):
    service, body, config, rows, _ = routing
    service.save(body)
    old = service.plans()[0]
    if change == 'disabled':
        body['revision'] = 1
        body['bindings'][0]['enabled'] = False
        service.save(body)
    elif change == 'account': config['yandex']['business_id'] = 'other'
    elif change == 'warehouse':
        rows.clear()
        service.warehouse_catalog(refresh=True)
    elif change == 'models':
        rows[0]['models'][0]['apiAvailability'] = 'NOT_AVAILABLE'
        service.warehouse_catalog(refresh=True)
    else: config['yandex']['stock_update_mode'] = 'campaign_warehouses'
    assert service.plans() == []
    with pytest.raises(ValueError, match='已变化'):
        service.validate(old)


@pytest.mark.parametrize('update', [{'warehouse_id': 999}, {'carrier': 'UNI'}, {'shipping_mode': 'missing'}, {'shipping_mode': ''}])
def test_mapping_requires_available_remote_warehouse_and_matching_carrier(routing, update):
    service, body, _, _, _ = routing
    body['bindings'][0].update(update)
    with pytest.raises(ValueError): service.save(body)
    assert service.configuration()['bindings'] == []


def test_revision_and_account_are_checked_at_commit(routing):
    service, body, config, _, _ = routing
    service.save(body)
    with pytest.raises(ValueError, match='已更新'): service.save(body)
    config['yandex']['business_id'] = 'other'
    assert service.configuration() == {'revision': 0, 'bindings': []}
    with pytest.raises(ValueError, match='不属于当前'): service.save(body)
    config['yandex']['business_id'] = '222'
    assert service.configuration()['bindings'] == body['bindings']


def test_sync_failure_invalidates_cache_and_is_reused_for_batch(routing, monkeypatch):
    service, body, config, _, _ = routing
    service.save(body)
    calls = []
    def fail(*_args):
        calls.append(1)
        raise RuntimeError('仓库接口超时')
    monkeypatch.setattr('erp_web.services.yandex_logistics_service.fetch_yandex_partner_warehouses', fail)
    resolver = shipping(service, config)
    for _ in range(3): assert '仓库接口超时' in resolver(COMMON, target('yandex'))['error']
    assert len(calls) == 1 and service.plans() == []


def test_account_change_during_sync_cannot_save_mapping(routing, monkeypatch):
    service, body, config, _, _ = routing
    def changed(*_args):
        config['yandex']['campaign_id'] = 'new'
        return []
    monkeypatch.setattr('erp_web.services.yandex_logistics_service.fetch_yandex_partner_warehouses', changed)
    with pytest.raises(ValueError): service.save(body)
    assert service.configuration()['bindings'] == []


def test_cheapest_supported_route_carries_its_own_remote_warehouse(routing):
    service, body, config, _, _ = routing
    body['bindings'].append({'warehouse_id': 32, 'carrier': 'GUOO_GTA', 'shipping_mode': 'Economy', 'enabled': True})
    service.save(body)
    quote = shipping(service, config)({**COMMON, 'weight_kg': '1.8', 'length_cm': 33, 'width_cm': 30.5, 'height_cm': 7}, target('yandex'))
    assert len(quote['candidates']) == 2 and quote['billable_g'] == '1800'
    assert quote['evidence']['fulfillment_plan']['publish_warehouse_id'] == 32
    assert quote['evidence']['carrier'] == 'GUOO(GTA)'


def test_warehouse_has_one_mode_and_mode_change_invalidates_quote(routing):
    service, body, _, _, _ = routing
    source = service.rules_dir.parent / 'yandex.xlsx'
    xlsx(source, 'yandex', rows=[{**YA, 'group': 'Other'}, {**YA, 'group': 'Other', 'transport': '陆空联运', 'service': 'Express'}])
    import_template(source, 'yandex', service.rules_dir)
    service.save(body)
    old = service.plans()[0]
    body['revision'] = 1
    body['bindings'][0]['shipping_mode'] = 'Express'
    service.save(body)
    assert len(service.plans()) == 1
    assert service.plans()[0]['shipping_mode'] == 'Express'
    with pytest.raises(ValueError, match='已变化'):
        service.validate(old)
    body['bindings'].append({**body['bindings'][0], 'shipping_mode': 'Economy'})
    with pytest.raises(ValueError, match='同一发布仓库'):
        service.save(body)


def test_unlinked_cheaper_service_of_same_carrier_cannot_quote(routing):
    service, body, config, _, _ = routing
    source = service.rules_dir.parent / 'yandex.xlsx'
    xlsx(source, 'yandex', rows=[{**YA, 'group': 'Other', 'fixed': 200},
                               {**YA, 'group': 'Other', 'transport': '陆空联运', 'service': 'Express', 'fixed': 1}])
    import_template(source, 'yandex', service.rules_dir)
    land = next(r for r in service.routes() if r['service'] == 'Economy')
    service.save(body)
    quote = shipping(service, config)(COMMON, target('yandex'))
    assert 'error' not in quote and len(quote['candidates']) == 1
    assert quote['evidence']['fulfillment_plan']['route_id'] == land['id']
    assert quote['evidence']['fulfillment_plan']['publish_warehouse_id'] == 31


def test_extra_small_is_separate_mode_not_cheaper_fallback(routing):
    service, body, config, _, _ = routing
    source = service.rules_dir.parent / 'yandex.xlsx'
    xlsx(source, 'yandex', rows=[{**YA, 'group': 'Other', 'fixed': 200},
                               {**YA, 'group': 'Extra Small', 'fixed': 1}])
    import_template(source, 'yandex', service.rules_dir)
    result = service.save(body)
    assert {m['value'] for m in result['modes']} == {'Economy', 'Economy Extra Small'}
    quote = shipping(service, config)({**COMMON, 'weight_kg': '.2'}, target('yandex'))
    assert len(quote['candidates']) == 1
    assert quote['evidence']['fulfillment_plan']['shipping_mode'] == 'Economy'
    assert quote['evidence']['fulfillment_plan']['route_id'] == next(r['id'] for r in service.routes() if r['group'] == 'Other')


@pytest.mark.parametrize('selection', ['single', 'mixed', 'missing'])
def test_stored_route_configuration_is_read_without_guessing_mode(routing, selection):
    service, body, _, _, _ = routing
    source = service.rules_dir.parent / 'yandex.xlsx'
    xlsx(source, 'yandex', rows=[{**YA, 'group': 'Other'},
                               {**YA, 'group': 'Other', 'service': 'Express', 'transport': '陆空联运'}])
    import_template(source, 'yandex', service.rules_dir)
    ids = [r['id'] for r in service.routes() if selection == 'mixed' or r['service'] == 'Economy']
    if selection == 'missing':
        ids.append('removed-route')
    stored = {'warehouse_id': 31, 'carrier': 'CEL', 'route_ids': ids, 'enabled': True}
    config = service.config_store.load_app_config()
    config['yandex_warehouses']['accounts']['222:111'] = {'revision': 7, 'bindings': [stored]}
    service.config_store.save_app_config(config)
    result = service.detail()
    assert service.configuration()['bindings'] == [stored]
    assert result['revision'] == 7
    if selection == 'single':
        assert result['bindings'][0]['shipping_mode'] == 'Economy'
        assert len(service.plans()) == 1
        service.save({**body, 'revision': 7, 'bindings': result['bindings']})
        assert 'route_ids' not in service.configuration()['bindings'][0]
    else:
        assert result['bindings'][0]['shipping_mode'] == ''
        assert result['binding_warnings'] and service.pricing_plans() == []


def test_missing_remote_warehouse_binding_can_be_disabled_or_removed(routing):
    service, body, _, rows, _ = routing
    service.save(body)
    old = service.plans()[0]
    rows.clear()
    body['revision'] = 1
    body['bindings'][0]['enabled'] = False
    service.save(body)
    with pytest.raises(ValueError, match='已变化'):
        service.validate(old)
    assert service.save({**body, 'revision': 2, 'bindings': []})['bindings'] == []


def test_generic_settings_cannot_overwrite_remote_bindings(routing):
    service, body, _, _, _ = routing
    store = service.config_store
    old = store.load_app_config()
    service.save(body)
    current = store.load_app_config()
    assert store.merge_app_config_fields(current, old)['yandex_warehouses'] == current['yandex_warehouses']


def test_http_contract_reaches_real_owner_and_preserves_bindings(routing):
    service, body, _, _, _ = routing
    path = '/api/yandex/warehouses/logistics'
    assert validate_request_payload(body, endpoint=path) == body
    responses = []
    handler = SimpleNamespace(path=path, read_body=lambda: body, send_json=lambda *args: responses.append(args))
    assert pricing_workspace_routes.handle_post(handler, SimpleNamespace(path=path))
    result, status = responses[0]
    assert status == 200 and result['bindings'] == body['bindings'] and result['revision'] == 1
    assert service.plans()[0]['publish_warehouse_id'] == 31


def test_warehouse_http_endpoints_are_registered(backend_server):
    import requests
    read = requests.get(backend_server + '/api/yandex/warehouses', timeout=5)
    assert read.status_code == 200 and 'warehouses' in read.json()
    invalid = requests.post(backend_server + '/api/yandex/warehouses/logistics', json={'revision': 0}, timeout=5)
    assert invalid.status_code == 400
