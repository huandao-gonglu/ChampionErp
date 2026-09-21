"""ERP 的薄物流适配：组织参数、复用核价汇率和售价计算、选最低运费。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from erp_web.international_shipping.models import Package, number
from erp_web.international_shipping.service import ShippingModule
from erp_web.schemas.shipping import ShippingResolution
from erp_web.services import pricing_service
from erp_web.services.mercadolibre_target_contract import mercadolibre_binding_for_target


class PricingShipping:
    def __init__(self, rules_dir: Path, store_config: dict):
        self.store_config = store_config
        self.module = ShippingModule(rules_dir, store_config)

    def __call__(self, common: dict, target: dict) -> ShippingResolution:
        currency = str(target.get('shipping_currency') or ('USD' if target.get('platform') == 'mercadolibre' else 'CNY'))
        failure: ShippingResolution = {
            'amount': '0', 'currency': currency, 'minimum_price_cny': '0',
            'billable_g': '0', 'evidence': {}, 'candidates': [],
        }
        try:
            return self.resolve(common, target, currency)
        except (ValueError, FileNotFoundError) as exc:
            return {**failure, 'error': str(exc)}

    def resolve(self, common: dict, target: dict, currency: str) -> ShippingResolution:
        if currency not in ('CNY', 'USD'):
            raise ValueError('国际物流报价币种仅支持 CNY/USD')
        source = {**common, **target}
        values = pricing_service.normalize_pricing_input(source)

        def per_cny(unit: str) -> Decimal:
            # 使用项目核价已有的汇率表示和换算方式，不获取另一份汇率。
            if unit == 'RUB' and source.get('rub_cny_rate') in (None, ''):
                rates = pricing_service._currency_usd_rates(source)
                if not rates.get('RUB'):
                    raise ValueError('国际物流报价缺少 RUB 汇率')
                return number(rates['RUB'], 'RUB/USD 汇率', positive=True) / number(values['usd_cny_rate'], 'USD/CNY 汇率', positive=True)
            return number(pricing_service._currency_per_cny(unit, source, values), f'{unit}/CNY 汇率', positive=True)

        target_rate = per_cny(currency)
        package = Package(
            number(source.get('weight_kg'), '包装重量', positive=True) * 1000,
            source.get('length_cm'), source.get('width_cm'), source.get('height_cm'),
            source.get('battery', False), source.get('liquid', False),
        )

        def converted(amount: Decimal, unit: str) -> Decimal:
            return amount / per_cny(unit) * target_rate

        def price_for_shipping(amount: Decimal, unit: str, minimum: Decimal) -> Decimal:
            quote: ShippingResolution = {
                'amount': str(converted(amount, unit)), 'currency': currency,
                'minimum_price_cny': str(minimum), 'billable_g': str(package.weight_g),
                'evidence': {}, 'candidates': [],
            }
            calculation = pricing_service.calculate_target_pricing(common, target, shipping_quote=quote)
            if not calculation['ok']:
                raise ValueError('；'.join(error['message'] for error in calculation['errors']))
            # 准入与 API 迭代必须使用最终会发布的舍入售价，不能拿未舍入中间值跨货值档。
            return Decimal(calculation['applied_price']['amount']) / per_cny(calculation['listing_currency'])

        # 在访问物流接口前先确认成本、利润模式和汇率有效。
        price_for_shipping(Decimal(0), currency, Decimal(0))
        platform = target['platform']
        binding = None
        conditions = {}
        if platform == 'mercadolibre':
            config = self.store_config.get(platform, {})
            destinations = target.get('sites_to_sell') or []
            if str(target.get('site', '')).upper() == 'CBT':
                if len(destinations) != 1:
                    raise ValueError('Mercado 运费必须按销售国家分别试算')
                binding = mercadolibre_binding_for_target(destinations[0], config.get('marketplace_bindings'))
                if not binding:
                    raise ValueError('当前销售国家没有对应的店铺授权')
                binding = {**binding, 'user_id': binding.get('seller_id')}
                conditions = destinations[0]
            else:
                binding = {'site_id': target.get('site'), 'user_id': config.get('user_id'), 'logistic_type': 'local'}
        result = self.module.quote(
            platform, package, price_for_shipping,
            cny_per_rub=Decimal(1) / per_cny('RUB') if platform == 'yandex' else None,
            cny_per_usd=Decimal(1) / per_cny('USD') if platform == 'mercadolibre' else None,
            binding=binding, category_id=str(target.get('category_id') or ''),
            listing_type_id=str(conditions.get('listing_type_id') or 'gold_pro'),
            free_shipping=conditions.get('free_shipping', True),
            tariff_version=target.get('shipping_tariff_version'),
        )
        if not result.candidates:
            reasons = '；'.join(f'{reason}（{count}）' for reason, count in result.rejected.items())
            raise ValueError('没有可用的国际物流报价' + (f'：{reasons}' if reasons else '，请检查店铺渠道与包装参数'))
        # 先按未舍入的换算金额排序，最后由既有核价金额输出统一保留两位。
        candidates = sorted(result.candidates, key=lambda item: (converted(item.amount, item.currency), item.route_id))
        selected = candidates[0]
        factor = target_rate / per_cny(selected.currency)
        evidence = {
            **selected.evidence, 'route': selected.route, 'route_id': selected.route_id,
            'original_amount': str(selected.amount), 'original_currency': selected.currency,
            'currency': currency, 'exchange_rate': str(factor), 'billable_g': str(selected.billable_g),
            'quoted_at': datetime.now(timezone.utc).isoformat(),
        }
        return {
            'amount': str(converted(selected.amount, selected.currency)), 'currency': currency,
            'minimum_price_cny': str(selected.minimum_price_cny), 'billable_g': str(selected.billable_g),
            'evidence': evidence,
            'candidates': [{**item.to_dict(), 'converted_amount': str(converted(item.amount, item.currency)), 'converted_currency': currency} for item in candidates],
        }
