"""OGL 单货件计费：官方六档运价与电子服务费，金额统一使用 Decimal。"""
from decimal import Decimal, ROUND_CEILING

from .models import Candidate, Package, QuoteResult, number, money


def electronic_fee(value_cny, rub_per_cny):
    """先按卢布计算每货件 2%，限额 15–200 RUB，再折回人民币。"""
    value = number(value_cny, '货件价值', positive=True)
    rate = number(rub_per_cny, 'RUB/CNY 汇率', positive=True)
    return min(Decimal(200), max(Decimal(15), value * rate * Decimal('.02'))) / rate


def shipping(package: Package, route: dict):
    grams = package.weight_g.to_integral_value(rounding=ROUND_CEILING)
    volume = Decimal(0)
    if route.get('volume_divisor_g'):
        volume = (package.length_cm * package.width_cm * package.height_cm / Decimal(route['volume_divisor_g'])).to_integral_value(rounding=ROUND_CEILING)
        grams = max(grams, volume)
    return money(Decimal(route['fixed']) + Decimal(route['per_g']) * grams), grams, volume


def quote_ogl(snapshot, package, price_for_shipping, *, cny_per_rub):
    rate = Decimal(1) / number(cny_per_rub, 'RUB 换算汇率', positive=True)
    result = QuoteResult()
    actual = package.weight_g.to_integral_value(rounding=ROUND_CEILING)
    # 分别求保底、比例、封顶区间，避免以单次估计的服务费循环反推售价。
    segments = [(Decimal(0), Decimal(750) / rate, '0', Decimal(15) / rate),
                (Decimal(750) / rate, Decimal(10000) / rate, '.02', Decimal(0)),
                (Decimal(10000) / rate, None, '0', Decimal(200) / rate)]
    for route in snapshot['content']['rules']:
        if not Decimal(route['min_g']) <= actual <= Decimal(route['max_g']):
            result.reject('实际重量不在 OGL 分组范围'); continue
        if max(package.edges) > Decimal(route['max_edge_cm']):
            result.reject('超过 OGL 最长边限制'); continue
        if (package.battery and not route['battery']) or (package.liquid and not route['liquid']):
            result.reject('OGL 分组不支持当前运输属性'); continue
        amount, grams, volume = shipping(package, route)
        for lower, upper, percent, fixed in segments:
            route_lower = Decimal(route['price_min_cny'])
            exclusive = route_lower > Decimal('.01')
            boundary = route_lower - Decimal('.01') if exclusive else route_lower
            minimum = max(boundary, lower)
            exclusive = exclusive and minimum == boundary
            maximum = min(Decimal(route['price_max_cny']), upper) if upper is not None else Decimal(route['price_max_cny'])
            if minimum > maximum:
                continue
            conditions = {'maximum_price_cny': str(maximum), 'minimum_price_exclusive': exclusive, 'electronic_service': {
                'rate': percent, 'fixed_cny': str(fixed), 'rub_per_cny': str(rate),
            }}
            price = price_for_shipping(amount, 'CNY', minimum, conditions)
            if price is None or not minimum <= price <= maximum or (exclusive and price == minimum):
                result.reject('售价不在 OGL 货值及服务费区间'); continue
            service = electronic_fee(price, rate)
            # 每个候选必须按真实限额回算，分段仅是求解过程。
            if abs(service - (price * Decimal(percent) + fixed)) > Decimal('1e-20'):
                result.reject('OGL 电子服务费区间不一致'); continue
            evidence = {
                'source': 'ogl_tariff', 'tariff_version': snapshot['version'],
                'effective_date': snapshot['content']['effective_date'],
                'source_sha256': snapshot['content']['source_sha256'],
                'source_cell': route['source_cell'], 'group': route['group'],
                'carrier': 'OGL', 'service': 'OGL', 'volume_weight_g': str(volume),
                'price_basis_currency': 'CNY', 'electronic_service_policy': 'reference-2026-08-24',
                'shipment_quantity': 1, 'shipment_value_cny': str(price),
                'electronic_service_fee_cny': str(service),
                'electronic_service_fee_rub': str(service * rate),
                'conditions': conditions,
                'limitation': '单 SKU、数量 1、单独成包预估；以包装后重量和尺寸核价，实际费用按发运结算。',
            }
            result.candidates.append(Candidate(route['id'], 'OGL · ' + route['group'], amount, 'CNY', grams, minimum, evidence))
    return result
