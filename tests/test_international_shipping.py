"""国际物流从模板/API 到核价结果的离线回归，不访问真实店铺。"""
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from erp_web.international_shipping.models import Package
from erp_web.international_shipping.platform_api import PlatformClient, quote_mercadolibre
from erp_web.international_shipping.tables import quote_table, yandex_shipping
from erp_web.international_shipping.tariff_store import import_template, load_active
from erp_web.services.pricing_service import pricing_result
from erp_web.services import pricing_service
from tests.pricing_test_utils import official_commission
from erp_web.services.pricing_shipping import PricingShipping as ShippingResolver
from test_tariff_store import NOTE, YA, xlsx


COMMON = {'cost_cny': 35, 'weight_kg': '.367', 'length_cm': 20, 'width_cm': 10, 'height_cm': 5,
          'usd_cny_rate': 7, 'rub_cny_rate': 12, 'mxn_usd_rate': 20}
CONFIG = {'ozon': {'client_id': '1', 'api_key': 'test'},
          'mercadolibre': {'access_token': 'test', 'marketplace_bindings': [
              {'site_id': 'MLM', 'logistic_type': 'remote', 'seller_id': '11'},
              {'site_id': 'MLB', 'logistic_type': 'remote', 'seller_id': '12'},
          ]}}


def PricingShipping(root, config):
    # 此文件验证运价计算；仓库可用性由专门的物流配置测试覆盖。
    return ShippingResolver(root, config, yandex_plans_loader=lambda: [
        {'route_id': row['id'], 'publish_warehouse_id': 31, 'fingerprint': row['id']}
        for row in load_active(root, 'yandex')['content']['rules']
    ])


def target(platform='ozon', **updates):
    return {'platform': platform, 'site': 'global', 'listing_currency': 'CNY',
            'shipping_quote_mode': 'auto', 'shipping_currency': 'CNY', **updates}


def calculate(root, common=None, selected=None, resolver=None):
    return pricing_result({'common': {**COMMON, **(common or {})}, 'targets': [selected or target()]},
                          shipping_resolver=resolver or PricingShipping(root, CONFIG), commission_resolver=official_commission)['results'][0]


@pytest.fixture
def rules(monkeypatch):
    calculate_target = pricing_service.calculate_target_pricing
    def with_commission(*args, **kwargs):
        if kwargs.get("commission_resolver") is None:
            kwargs["commission_resolver"] = official_commission
        return calculate_target(*args, **kwargs)
    monkeypatch.setattr(pricing_service, 'calculate_target_pricing', with_commission)
    with TemporaryDirectory(prefix='erp-shipping-tests-') as folder:
        root = Path(folder)
        for platform in ('yandex',):
            source = root / f'{platform}.xlsx'
            xlsx(source, platform)
            import_template(source, platform, root / 'rules')
        def call(_self, path, **kwargs):
            if path == '/v2/warehouse/list':
                return {'warehouses': [{'warehouse_id': 1, 'is_rfbs': True, 'status': 'created'}], 'has_next': False}
            if path == '/v2/delivery-method/list':
                return {'delivery_methods': [{'id': 2, 'warehouse_id': 1, 'status': 'ACTIVE', 'name': 'XY Economy Extra Small 兴远'}], 'has_next': False}
            if '/shipping_options/free' in path:
                cost = 100 if '/11/' in path else 200
                return {'coverage': {'all_country': {'list_cost': cost, 'currency_id': 'MXN', 'billable_weight': 367, 'discount': {'rate': .5}}}}
            raise AssertionError(path)
        monkeypatch.setattr(PlatformClient, 'call', call)
        yield root / 'rules'






@pytest.mark.parametrize('weight', [0, -1, 'bad', 'NaN', 'Infinity'])
def test_invalid_package_fails_as_field_error(rules, weight):
    result = calculate(rules, {'weight_kg': weight}, target('yandex'))
    assert not result['ok']
    assert result['errors'][0]['field'] == 'shipping_amount'
    assert result['shipping_candidates'] == []


def test_boolean_flags_are_not_coerced(rules):
    result = calculate(rules, {'battery': 'false'})
    assert not result['ok'] and '布尔' in result['errors'][0]['message']


def test_yandex_liquid_and_price_limit_are_checked(rules):
    assert not calculate(rules, {'liquid': True}, target('yandex'))['ok']
    assert not calculate(rules, {'cost_cny': 200}, target('yandex'))['ok']
    assert calculate(rules, {'cost_cny': 200})['ok']




@pytest.mark.parametrize('currency,expected', [('CNY', 15.28), ('USD', 2.18)])
def test_rub_is_converted_to_existing_shipping_currency(rules, currency, expected):
    result = calculate(rules, selected=target('yandex', shipping_currency=currency))
    assert result['ok'] and result['shipping_amount'] == expected
    assert result['shipping_currency'] == currency
    evidence = result['calculation_basis']['shipping_evidence']
    assert evidence['original_currency'] == 'RUB'
    assert evidence['original_amount'] == '183.33'
    assert result['shipping_cost_cny'] == 15.28


def test_missing_fx_fails_without_demo_rate(rules):
    result = calculate(rules, {'rub_cny_rate': ''}, target('yandex'))
    assert not result['ok'] and 'RUB' in result['errors'][0]['message']


def test_all_candidates_are_returned_and_erp_selects_cheapest(rules):
    source = rules.parent / 'yandex.xlsx'
    rows = [{**YA, 'carrier': f'Carrier {i}', 'fixed': 100 - i} for i in range(15)]
    xlsx(source, 'yandex', rows=rows)
    import_template(source, 'yandex', rules)
    result = calculate(rules, selected=target('yandex'))
    assert result['ok'] and len(result['shipping_candidates']) == 15
    assert result['calculation_basis']['shipping_evidence']['carrier'] == 'Carrier 14'


def test_yandex_normal_routes_only_query_commission_for_final_price(rules):
    source = rules.parent / 'yandex.xlsx'
    xlsx(source, 'yandex', rows=[{**YA, 'group': 'Other', 'carrier': f'Carrier {i}', 'fixed': 100 - i} for i in range(30)])
    import_template(source, 'yandex', rules)
    calls = []
    def commission(common, selected, price):
        calls.append(price)
        return official_commission(common, selected, price)
    shipping = PricingShipping(rules, CONFIG)
    shipping.commission_resolver = commission
    result = pricing_result({'common': COMMON, 'targets': [target('yandex')]},
                            shipping_resolver=shipping, commission_resolver=commission)['results'][0]
    assert result['ok'] and len(result['shipping_candidates']) == 30
    assert result['calculation_basis']['shipping_evidence']['carrier'] == 'Carrier 29'
    assert 1 <= len(calls) <= 3
    assert calls[-1] == result['applied_price']
    assert pricing_service.has_official_commission(result['calculation_basis'])


def test_yandex_small_route_price_limit_still_rejects_and_normal_route_needs_no_price(rules):
    snapshot = load_active(rules, 'yandex')
    small = snapshot['content']['rules'][0]
    snapshot['content']['rules'] = [small, {**small, 'id': 'normal', 'group': 'Other'}]
    calls = []
    def expensive(*args):
        calls.append(args)
        return Decimal('125.01')
    result = quote_table(snapshot, Package(367, 20, 10, 5), expensive, cny_per_rub=Decimal(1) / 12)
    assert len(calls) == 1
    assert [item.route_id for item in result.candidates] == ['normal']
    assert result.rejected['售价超过轻小件货值上限'] == 1


def test_identical_small_route_costs_share_commission_queries(rules):
    source = rules.parent / 'yandex.xlsx'
    xlsx(source, 'yandex', rows=[{**YA, 'carrier': f'Carrier {i}'} for i in range(20)])
    import_template(source, 'yandex', rules)
    calls = []
    shipping = PricingShipping(rules, CONFIG)
    def commission(common, selected, price):
        calls.append(price)
        return official_commission(common, selected, price)
    shipping.commission_resolver = commission
    quote = shipping(COMMON, target('yandex'))
    assert 'error' not in quote and len(quote['candidates']) == 20
    assert 1 <= len(calls) <= 3


def test_policy_update_reaches_channel_selection_and_weight_step(rules):
    original = calculate(rules, {'weight_kg': '.601'}, target('yandex'))
    assert not original['ok']
    source = rules.parent / 'yandex.xlsx'
    xlsx(source, 'yandex', rows=[{**YA, 'step_g': 100}], note=NOTE.replace('500 克', '700 克').replace('1500 卢布', '2000 卢布'))
    import_template(source, 'yandex', rules)
    updated = calculate(rules, {'weight_kg': '.601'}, target('yandex'))
    assert updated['ok'] and updated['billable_weight_kg'] == .7






def test_distinct_sku_weights_are_not_given_first_sku_shipping(rules):
    first = calculate(rules)
    second = calculate(rules, {'weight_kg': '.5'})
    assert first['shipping_amount'] != second['shipping_amount']
    assert second['calculation_basis']['shipping_evidence']['billable_g'] == '500'






def ml_target(sites=('MLM',), **updates):
    return target('mercadolibre', site='CBT', listing_currency='USD', shipping_currency='USD',
                  sites_to_sell=[{'site_id': site, 'logistic_type': 'remote'} for site in sites],
                  destination_pricing_modes=[{'site_id': site, 'logistic_type': 'remote', 'pricing_model': 'net_proceeds'} for site in sites], **updates)


def test_mercado_quote_iterates_and_does_not_double_deduct_discount(rules):
    result = calculate(rules, selected=ml_target())
    assert result['ok'] and result['shipping_amount'] == 5
    evidence = result['calculation_basis']['shipping_evidence']
    assert evidence['original_currency'] == 'MXN' and evidence['original_amount'] == '100'
    assert evidence['iterations'] == 2
    assert evidence['request']['dimensions'] == '5x10x20,367'
    assert 'test' not in str(evidence)


def test_mercado_countries_keep_distinct_shipping_and_prices(rules):
    result = calculate(rules, selected=ml_target(('MLM', 'MLB')))
    assert result['ok']
    destinations = {row['site_id']: row for row in result['destination_results']}
    assert destinations['MLM']['shipping_amount'] == 5
    assert destinations['MLB']['shipping_amount'] == 10
    assert destinations['MLM']['net_proceeds'] != destinations['MLB']['net_proceeds']
    assert len(result['calculation_basis']['destination_shipping']) == 2
    assert all(item['calculation_fingerprint'] == result['calculation_fingerprint'] for item in destinations.values())


@pytest.mark.parametrize('amount', [0, -1, 'bad'])
def test_mercado_invalid_quote_never_falls_back_to_builtin_table(rules, monkeypatch, amount):
    monkeypatch.setattr(PlatformClient, 'call', lambda *args, **kwargs: {'coverage': {'all_country': {'list_cost': amount, 'currency_id': 'MXN'}}})
    result = calculate(rules, selected=ml_target())
    assert not result['ok'] and not result['shipping_candidates']


def test_mercado_nonconvergence_does_not_apply_quote(rules, monkeypatch):
    calls = []
    def call(*args, **kwargs):
        calls.append(kwargs)
        return {'coverage': {'all_country': {'list_cost': Decimal(kwargs['params']['item_price']) * 100, 'currency_id': 'USD'}}}
    monkeypatch.setattr(PlatformClient, 'call', call)
    result = calculate(rules, selected=ml_target())
    assert not result['ok'] and '未收敛' in result['errors'][0]['message']
    assert len(calls) == 8




def test_platform_client_blocks_unrelated_side_effects():
    with pytest.raises(ValueError, match='只允许'):
        PlatformClient('mercadolibre', CONFIG['mercadolibre'], opener=lambda *a, **k: pytest.fail('不得联网')).call('/v3/product/import', body={})




@pytest.mark.parametrize('fixed,rate,step,weight,expected_fee,expected_g', [
    # 官方 Logistics calculator 费率列；不使用有单位错误或缓存失效的 AA 示例总价。
    ('198', '.506', '100', '367', '400.40', '400'),
    ('198', '.506', '100', '400', '400.40', '400'),
    ('198', '.506', '100', '400.01', '451.00', '500'),
    ('161', '.715', '100', '367', '447.00', '400'),
    ('78', '.287', '1', '367', '183.33', '367'),
    ('78', '.287', '1', '367.01', '183.62', '368'),
])
def test_official_yandex_rate_and_step_samples(fixed, rate, step, weight, expected_fee, expected_g):
    route = {'fixed': fixed, 'per_g': rate, 'step_g': step}
    assert yandex_shipping(Package(weight, 20, 10, 5), route) == (Decimal(expected_fee), Decimal(expected_g))




def test_mercado_destination_conditions_and_sku_units_reach_api(rules, monkeypatch):
    requests = []
    def call(_client, path, **kwargs):
        requests.append((path, kwargs['params']))
        return {'coverage': {'all_country': {'list_cost': 100, 'currency_id': 'MXN', 'billable_weight': 901}}}
    monkeypatch.setattr(PlatformClient, 'call', call)
    selected = ml_target()
    selected['sites_to_sell'][0].update(listing_type_id='gold_special', free_shipping=False)
    result = calculate(rules, {'weight_kg': '.9001', 'length_cm': '30.1', 'width_cm': '20.2', 'height_cm': '10.3'}, selected)
    assert result['ok']
    assert all(path == '/users/11/shipping_options/free' for path, _ in requests)
    assert all(params['dimensions'] == '11x21x31,901' for _, params in requests)
    assert all(params['listing_type_id'] == 'gold_special' and params['free_shipping'] == 'false' for _, params in requests)
    assert all(params['currency_id'] == 'USD' for _, params in requests)


def test_yandex_exclusions_preserve_measured_dimensions_and_rule_limits(rules):
    snapshot = load_active(rules, 'yandex')
    result = quote_table(snapshot, Package('400', '61', '10', '5'), lambda *_: Decimal('10'), cny_per_rub=Decimal('.1'))
    assert result.exclusions
    exclusion = next(item for item in result.exclusions if item['reason'] == '不符合轻小件重量或尺寸')
    assert exclusion['longest_cm'] == '61' and exclusion['limits']['max_edge_cm'] == '60'
    assert exclusion['route_id'] and exclusion['route']
