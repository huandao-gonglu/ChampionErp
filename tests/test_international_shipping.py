"""国际物流从模板/API 到核价结果的离线回归，不访问真实店铺。"""
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from erp_web.international_shipping.models import Package
from erp_web.international_shipping.platform_api import PlatformClient, discover_ozon, quote_mercadolibre
from erp_web.international_shipping.tables import ozon_shipping, quote_table, yandex_shipping
from erp_web.international_shipping.tariff_store import import_template, load_active
from erp_web.services.pricing_service import pricing_result
from erp_web.services.pricing_shipping import PricingShipping
from test_tariff_store import NOTE, OZ, YA, xlsx


COMMON = {'cost_cny': 35, 'weight_kg': '.367', 'length_cm': 20, 'width_cm': 10, 'height_cm': 5,
          'usd_cny_rate': 7, 'rub_cny_rate': 12, 'mxn_usd_rate': 20}
CONFIG = {'ozon': {'client_id': '1', 'api_key': 'test'},
          'mercadolibre': {'access_token': 'test', 'marketplace_bindings': [
              {'site_id': 'MLM', 'logistic_type': 'remote', 'seller_id': '11'},
              {'site_id': 'MLB', 'logistic_type': 'remote', 'seller_id': '12'},
          ]}}


def target(platform='ozon', **updates):
    return {'platform': platform, 'site': 'global', 'listing_currency': 'CNY',
            'shipping_quote_mode': 'auto', 'shipping_currency': 'CNY', **updates}


def calculate(root, common=None, selected=None, resolver=None):
    return pricing_result({'common': {**COMMON, **(common or {})}, 'targets': [selected or target()]},
                          shipping_resolver=resolver or PricingShipping(root, CONFIG))['results'][0]


@pytest.fixture
def rules(monkeypatch):
    with TemporaryDirectory(prefix='erp-shipping-tests-') as folder:
        root = Path(folder)
        for platform in ('ozon', 'yandex'):
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


def test_original_cent_precision_and_kg_conversion(rules):
    package = Package(367, 20, 10, 5)
    oz = load_active(rules, 'ozon')['content']['rules'][0]
    ya = load_active(rules, 'yandex')['content']['rules'][0]
    assert ozon_shipping(package, oz) == (Decimal('13.69'), Decimal(367))
    assert yandex_shipping(package, ya) == (Decimal('183.33'), Decimal(367))
    result = calculate(rules)
    assert result['ok'] and result['shipping_amount'] == 13.69
    assert result['billable_weight_kg'] == .367
    assert result['calculation_basis']['shipping_evidence']['original_amount'] == '13.69'
    assert result['breakdown']['volume_weight_kg'] == 0


def test_template_volume_divisor_reaches_pricing_breakdown(rules, monkeypatch):
    source = rules.parent / 'ozon.xlsx'
    xlsx(source, rows=[{**OZ, 'group': 'Big', 'name': 'Xingyuan Economy Big', 'max_g': 30000,
                       'dimensions': '边长总和 ≤ 310 cm, 长边 ≤ 150 cm', 'price_cny': '0.01 - 2000',
                       'weight_type': '最大实际重量与体积重量', 'divisor': '长×宽×高(cm)÷12000'}])
    import_template(source, 'ozon', rules)
    monkeypatch.setattr('erp_web.international_shipping.service.discover_ozon', lambda _: [
        {'id': 3, 'name': 'XY Economy Big 兴远', 'status': 'ACTIVE'},
    ])
    result = calculate(rules, {'weight_kg': 3, 'length_cm': 80, 'width_cm': 50, 'height_cm': 30})
    assert result['ok'] and result['billable_weight_kg'] == 10
    assert result['breakdown']['volume_weight_kg'] == 10


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
    assert not calculate(rules, {'cost_cny': 200})['ok']


def test_manual_sale_cannot_be_raised_to_channel_minimum(rules):
    source = rules.parent / 'ozon.xlsx'
    xlsx(source, rows=[{**OZ, 'price_cny': '100 - 135'}])
    import_template(source, 'ozon', rules)
    manual = target(pricing_mode='manual', manual_price={'amount': '50', 'currency': 'CNY'})
    assert not calculate(rules, selected=manual)['ok']
    automatic = calculate(rules)
    assert automatic['ok'] and automatic['applied_price']['amount'] == '100.00'


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


def test_policy_update_reaches_channel_selection_and_weight_step(rules):
    original = calculate(rules, {'weight_kg': '.601'}, target('yandex'))
    assert not original['ok']
    source = rules.parent / 'yandex.xlsx'
    xlsx(source, 'yandex', rows=[{**YA, 'step_g': 100}], note=NOTE.replace('500 克', '700 克').replace('1500 卢布', '2000 卢布'))
    import_template(source, 'yandex', rules)
    updated = calculate(rules, {'weight_kg': '.601'}, target('yandex'))
    assert updated['ok'] and updated['billable_weight_kg'] == .7


def test_rate_update_changes_price_but_not_existing_snapshot(rules):
    resolver = PricingShipping(rules, CONFIG)
    before = calculate(rules, resolver=resolver)
    source = rules.parent / 'ozon.xlsx'
    xlsx(source, rows=[{**OZ, 'rate': '¥ 13.37 + ¥ .0281/1 g'}])
    import_template(source, 'ozon', rules)
    fixed = calculate(rules, resolver=resolver)
    after = calculate(rules)
    assert fixed['shipping_amount'] == before['shipping_amount']
    assert after['shipping_amount'] - before['shipping_amount'] == pytest.approx(10)
    assert Decimal(after['applied_price']['amount']) > Decimal(before['applied_price']['amount'])
    assert after['calculation_basis']['shipping_evidence']['tariff_version'] != before['calculation_basis']['shipping_evidence']['tariff_version']
    pinned = calculate(rules, selected=target(shipping_tariff_version=before['calculation_basis']['shipping_evidence']['tariff_version']))
    assert pinned['shipping_amount'] == before['shipping_amount']


def test_channel_minimum_is_respected_after_listing_currency_rounding(rules):
    source = rules.parent / 'ozon.xlsx'
    xlsx(source, rows=[{**OZ, 'price_cny': '100.01 - 135'}])
    import_template(source, 'ozon', rules)
    result = calculate(rules, {'rub_cny_rate': '12.0123'}, target(listing_currency='RUB'))
    assert result['ok']
    assert Decimal(result['applied_price']['amount']) / Decimal('12.0123') >= Decimal('100.01')


def test_distinct_sku_weights_are_not_given_first_sku_shipping(rules):
    first = calculate(rules)
    second = calculate(rules, {'weight_kg': '.5'})
    assert first['shipping_amount'] != second['shipping_amount']
    assert second['calculation_basis']['shipping_evidence']['billable_g'] == '500'


def test_disabled_ozon_methods_never_quote(rules, monkeypatch):
    monkeypatch.setattr('erp_web.international_shipping.service.discover_ozon', lambda client: [])
    result = calculate(rules)
    assert not result['ok'] and '已启用' in result['errors'][0]['message']


def test_manual_quote_does_not_access_module(rules, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('手动报价不得访问自动物流模块')
    monkeypatch.setattr('erp_web.international_shipping.service.ShippingModule.quote', forbidden)
    result = calculate(rules, selected=target(shipping_quote_mode='manual', shipping_amount=10))
    assert result['ok'] and result['shipping_amount'] == 10


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


def test_ozon_incomplete_pagination_is_rejected():
    class Client:
        def call(self, *args, **kwargs):
            return {'warehouses': [], 'has_next': True, 'cursor': ''}
    with pytest.raises(ValueError, match='分页不完整'):
        discover_ozon(Client())


def test_platform_client_blocks_unrelated_side_effects():
    with pytest.raises(ValueError, match='只允许'):
        PlatformClient('ozon', CONFIG['ozon']).call('/v3/product/import', body={})


@pytest.mark.parametrize('fixed,rate,divisor,weight,expected_fee,expected_g', [
    # 2026-09-22 核对的官方 Ozon 中国 rFBS 表：G29/G42/G69/G93/G119/G150。
    ('3.37', '.0281', None, '367', '13.69', '367'),
    ('25.83', '.0281', None, '501', '39.91', '501'),
    ('17.97', '.0393', None, '2000', '96.57', '2000'),
    ('24.71', '.0393', None, '5000', '221.21', '5000'),
    ('40.44', '.0281', '12000', '3000', '321.44', '10000'),
    ('69.64', '.0258', '12000', '5001', '327.64', '10000'),
    ('40.44', '.0281', '12000', '10000.01', '321.47', '10001'),
])
def test_official_ozon_rate_samples(fixed, rate, divisor, weight, expected_fee, expected_g):
    # 大体积输入同时验证：实际重渠道不能擅自启用体积重，体积重渠道取两者较大。
    route = {'fixed': fixed, 'per_g': rate, 'volumetric_divisor_kg': divisor}
    assert ozon_shipping(Package(weight, 80, 50, 30), route) == (Decimal(expected_fee), Decimal(expected_g))


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


@pytest.mark.parametrize('weight,accepted', [('500', True), ('500.01', False)])
def test_ozon_extra_small_weight_boundary_is_inclusive(rules, weight, accepted):
    result = calculate(rules, {'weight_kg': str(Decimal(weight) / 1000)})
    assert result['ok'] is accepted


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
