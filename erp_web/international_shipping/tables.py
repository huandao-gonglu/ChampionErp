"""从独立实验移入的 Ozon/Yandex 计算与准入规则；返回全部候选。"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_CEILING

from .models import Candidate, Package, PriceForShipping, QuoteResult, money, number


def ozon_shipping(package: Package, route: dict) -> tuple[Decimal, Decimal]:
    grams = package.weight_g.to_integral_value(rounding=ROUND_CEILING)
    divisor = route.get('volumetric_divisor_kg')
    if divisor:
        volume = package.length_cm * package.width_cm * package.height_cm / Decimal(divisor) * 1000
        grams = max(grams, volume.to_integral_value(rounding=ROUND_CEILING))
    return money(Decimal(route['fixed']) + Decimal(route['per_g']) * grams), grams


def yandex_shipping(package: Package, route: dict) -> tuple[Decimal, Decimal]:
    step = Decimal(route['step_g'])
    grams = (package.weight_g / step).to_integral_value(rounding=ROUND_CEILING) * step
    return money(Decimal(route['fixed']) + Decimal(route['per_g']) * grams), grams


def ozon_method_match(route: dict, method: dict) -> bool:
    """保留实验中验证的名称映射；结果证据明确标记，未匹配的渠道不报价。"""
    if method.get('status') != 'ACTIVE':
        return False
    name = ' '.join(str(method.get('name', '')).split())
    carrier = route['carrier']
    carrier_ok = carrier == 'Xingyuan' and (name.startswith('XY ') or '兴远' in name)
    carrier_ok = carrier_ok or bool(re.search(r'\b' + re.escape(carrier) + r'\b', name, re.I))
    groups = ('Premium Small', 'Premium Big', 'Extra Small', 'Budget', 'Small', 'Big')
    group = next((item for item in groups if item in name), None)
    return carrier_ok and group == route['group'] and bool(re.search(r'\b' + re.escape(route['service']) + r'\b', name))


def quote_table(
    snapshot: dict,
    package: Package,
    price_for_shipping: PriceForShipping,
    *,
    cny_per_rub: Decimal | None = None,
    methods: list[dict] | None = None,
) -> QuoteResult:
    content = snapshot['content']
    platform = content['platform']
    if platform not in ('ozon', 'yandex'):
        raise ValueError('模板仅支持 ozon/yandex')
    if platform == 'ozon' and methods is None:
        raise ValueError('Ozon 报价需要店铺已启用的仓库配送方式')
    if platform == 'yandex':
        cny_per_rub = number(cny_per_rub, 'RUB 换算汇率', positive=True)
    result = QuoteResult()
    actual_g = package.weight_g.to_integral_value(rounding=ROUND_CEILING)
    for route in content['rules']:
        matches = []
        minimum = Decimal(0)
        if platform == 'ozon':
            matches = [method for method in methods if ozon_method_match(route, method)]
            if not matches:
                result.reject('无对应已启用仓库渠道'); continue
            if not Decimal(route['min_g']) <= actual_g <= Decimal(route['max_g']):
                result.reject('实际重量不在渠道范围'); continue
            if max(package.edges) > Decimal(route['max_edge_cm']) or sum(package.edges) > Decimal(route['max_sum_cm']):
                result.reject('超过渠道尺寸限制'); continue
            if package.liquid and not route['liquid']:
                result.reject('渠道不允许液体'); continue
            amount, grams = ozon_shipping(package, route)
            currency = 'CNY'
            minimum = Decimal(route['price_min_cny'])
        else:
            policy = content['policies']['extra_small']
            if package.liquid:
                result.reject('模板没有液体准入规则，无法确认'); continue
            if route['group'] == 'Extra Small' and (
                actual_g > Decimal(policy['max_g'])
                or max(package.edges) > Decimal(policy['max_edge_cm'])
                or sum(package.edges) > Decimal(policy['max_sum_cm'])
            ):
                result.reject('不符合轻小件重量或尺寸'); continue
            amount, grams = yandex_shipping(package, route)
            currency = 'RUB'
        if package.battery and not route['battery']:
            result.reject('渠道不允许电池'); continue
        price = price_for_shipping(amount, currency, minimum)
        if platform == 'ozon' and not minimum <= price <= Decimal(route['price_max_cny']):
            result.reject('售价不在渠道货值档内'); continue
        if platform == 'yandex' and route['group'] == 'Extra Small' and price / cny_per_rub > Decimal(policy['price_max_rub']):
            result.reject('售价超过轻小件货值上限'); continue
        evidence = {
            'source': 'tariff_template', 'tariff_version': snapshot['version'],
            'source_cell': route['source_cell'], 'group': route['group'],
            'carrier': route['carrier'], 'service': route['service'],
            'volume_weight_g': '0',
        }
        if platform == 'ozon':
            if route.get('volumetric_divisor_kg'):
                volume = package.length_cm * package.width_cm * package.height_cm / Decimal(route['volumetric_divisor_kg']) * 1000
                evidence['volume_weight_g'] = str(volume.to_integral_value(rounding=ROUND_CEILING))
            evidence.update({'channel_basis': 'active_method_name_mapping', 'method_ids': [item['id'] for item in matches]})
        else:
            evidence['channel_basis'] = 'template_mainland_channels'
            if route['group'] == 'Other':
                evidence['limitation'] = '模板缺少普通件完整准入限制；本结果为运费试算'
        result.candidates.append(Candidate(route['id'], route['name'], amount, currency, grams, minimum, evidence))
    return result
