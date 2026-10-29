"""Yandex 运输费计算与准入规则；返回全部候选。"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING

from .models import Candidate, Package, PriceForShipping, QuoteResult, money, number


def yandex_shipping(package: Package, route: dict) -> tuple[Decimal, Decimal]:
    step = Decimal(route['step_g'])
    grams = (package.weight_g / step).to_integral_value(rounding=ROUND_CEILING) * step
    return money(Decimal(route['fixed']) + Decimal(route['per_g']) * grams), grams


def quote_table(
    snapshot: dict,
    package: Package,
    price_for_shipping: PriceForShipping,
    *,
    cny_per_rub: Decimal | None = None,
    route_ids: set[str] | None = None,
) -> QuoteResult:
    content = snapshot['content']
    platform = content['platform']
    if platform != 'yandex':
        raise ValueError('此报价表仅支持 Yandex，Ozon 使用 OGL')
    cny_per_rub = number(cny_per_rub, 'RUB 换算汇率', positive=True)
    result = QuoteResult()
    actual_g = package.weight_g.to_integral_value(rounding=ROUND_CEILING)
    for route in content['rules']:
        if route_ids is not None and route['id'] not in route_ids:
            result.reject('未配置可用合作仓与发布仓库'); continue
        minimum = Decimal(0)
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
        # 仅货值准入需要售价；普通 Yandex 渠道的运费只取决于包装。
        # 在此逐渠道核价会放大官方佣金请求，最终选中渠道仍由核价引擎校验。
        if route['group'] == 'Extra Small':
            price = price_for_shipping(amount, currency, minimum)
            if price / cny_per_rub > Decimal(policy['price_max_rub']):
                result.reject('售价超过轻小件货值上限'); continue
        evidence = {
            'source': 'tariff_template', 'tariff_version': snapshot['version'],
            'source_cell': route['source_cell'], 'group': route['group'],
            'carrier': route['carrier'], 'service': route['service'],
            'volume_weight_g': '0',
        }
        evidence['channel_basis'] = 'template_mainland_channels'
        if route['group'] == 'Other':
            evidence['limitation'] = '模板缺少普通件完整准入限制；本结果为运费试算'
        result.candidates.append(Candidate(route['id'], route['name'], amount, currency, grams, minimum, evidence))
    return result
