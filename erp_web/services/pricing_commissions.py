"""本轮核价的官方佣金查询；不使用手填比例，也不借用其他商品的费率。"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from urllib.parse import quote as url_quote, urlencode

from erp_web.marketplaces.config_http import request_json
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.marketplaces.yandex_http import request_yandex_json
from erp_web.schemas.commissions import CommissionQuote, OzonCategoryLoader
from erp_web.services.ozon_commission_tariffs import OzonCommissionTariffs
from erp_web.services.mercado_commission_tariffs import MercadoCommissionTariffs
from erp_web.services.mercadolibre_target_contract import mercadolibre_binding_for_target
from erp_web.services import pricing_service
from erp_web.services.yandex_commission_batch import YandexCommissionBatch, parse_rule


def create_yandex_commission_batch(store_config: dict) -> YandexCommissionBatch:
    from erp_web.context import get_context
    return YandexCommissionBatch(store_config.get("yandex") or {}, request_yandex_json,
                                 rule_cache=get_context().pricing_cache.rules)


def decimal(value, label, *, positive=False):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"官方佣金{label}缺失或无效") from None
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError(f"官方佣金{label}缺失或无效")
    return number


class PricingCommissions:
    def __init__(self, store_config: dict, *, ozon_category_loader: OzonCategoryLoader | None = None, yandex_quote=None):
        self.store_config = store_config
        self.responses: dict = {}
        self.failures: dict[str, str] = {}
        self.ozon_category_loader = ozon_category_loader
        self.ozon_categories = None
        self.ozon_tariffs = OzonCommissionTariffs()
        self.mercado_tariffs = MercadoCommissionTariffs()
        self.yandex_quote = yandex_quote or create_yandex_commission_batch(store_config).quote_one

    def _read(self, platform, endpoint, payload, call):
        if platform in self.failures:
            raise ValueError(self.failures[platform])
        key = json.dumps([platform, endpoint, payload], sort_keys=True, ensure_ascii=False)
        if isinstance(self.responses.get(key), ValueError):
            raise self.responses[key]
        if key not in self.responses:
            try:
                self.responses[key] = call()
            except PublishAdapterError as exc:
                detail = str(exc)
                store = self.store_config.get(platform) or {}
                for field in ("api_token", "api_key", "access_token", "refresh_token", "client_secret"):
                    secret = str(store.get(field) or "")
                    if secret:
                        detail = detail.replace(secret, "***")
                message = f"{platform} 官方佣金查询失败（{exc.code}）：{detail[:500]}"
                self.responses[key] = ValueError(message)
                # 具体请求被拒绝不阻止其他类目；授权、限流和网络失败在本轮共用。
                if exc.retryable or any(part in exc.code for part in ("AUTH_FAILED", "RATE_LIMITED", "CREDENTIAL", "ACCOUNT_DISABLED")):
                    self.failures[platform] = message
                raise ValueError(message) from None
        return deepcopy(self.responses[key])

    def __call__(self, common: dict, target: dict, price: dict, *, force_official=False) -> CommissionQuote:
        platform = str(target.get("platform") or "").lower()
        store = self.store_config.get(platform) or {}
        category = str(target.get("category_id") or "").strip()
        if not category:
            raise ValueError("请先选择末级类目，再获取官方佣金")
        amount = decimal(price.get("amount"), "试算售价", positive=True)
        currency = str(price.get("currency") or "").upper()
        source = {**common, **target}
        values = pricing_service.normalize_pricing_input(source)

        def convert(value, unit):
            unit = "RUB" if unit == "RUR" else unit
            rate = decimal(pricing_service._currency_per_cny(unit, source, values), f" {unit} 汇率", positive=True)
            own = decimal(pricing_service._currency_per_cny(currency, source, values), f" {currency} 汇率", positive=True)
            return value / rate * own

        if platform == "yandex":
            fee, evidence = self._yandex(store, category, source, amount, currency, force_official=force_official)
        elif platform == "ozon":
            fee, evidence = self._ozon(store, category, target, amount, convert)
        elif platform == "mercadolibre":
            fee, evidence = self._mercado(store, category, target, source, amount, currency, convert)
        else:
            raise ValueError("当前平台没有已接入的官方佣金查询")
        percent = fee / amount * 100
        quote: CommissionQuote = {"percent": str(percent), "evidence": {
            **evidence, "source": evidence.get("source", "platform_api"), "purpose": "prelisting", "platform": platform, "category_id": category,
            "price": {"amount": str(amount), "currency": currency},
            "amount": str(fee), "currency": currency,
            "quoted_at": datetime.now(timezone.utc).isoformat(),
        }}
        if platform == "yandex":
            rule = evidence.get("cached_rule") or parse_rule(evidence["tariff"], amount)
            if rule is not None:
                quote["rule"] = rule
        if platform == "ozon":
            quote["price_bands"] = evidence["price_bands"]
        return quote

    def _ozon(self, store, category, target, amount, convert):
        if not store.get("client_id") or not store.get("api_key") or self.ozon_category_loader is None:
            raise ValueError("Ozon 上架前佣金需要当前店铺授权及官方商品类型资料")
        if "ozon" in self.failures:
            raise ValueError(self.failures["ozon"])
        if self.ozon_categories is None:
            try:
                self.ozon_categories = self.ozon_category_loader()
            except (RuntimeError, ValueError, OSError, PublishAdapterError):
                self.failures["ozon"] = "Ozon 官方商品类型读取失败，请检查授权后重新核价"
                raise ValueError(self.failures["ozon"]) from None
        matches = [row for row in self.ozon_categories if str(row.get("type_id")) == category]
        if len(matches) != 1:
            raise ValueError("Ozon 当前官方类目树未包含唯一的商品类型")
        # 表内价格档使用卢布，不把店铺的人民币价格直接与卢布档位比较。
        rub_price = amount / convert(Decimal(1), "RUB")
        rate, evidence = self.ozon_tariffs.quote(matches[0], target.get("brand_attribute"), rub_price)
        bands = []
        for _, lower, upper in self.ozon_tariffs.bands:
            band_rate, _ = self.ozon_tariffs.quote(matches[0], target.get('brand_attribute'), max(Decimal('.01'), lower))
            bands.append({'rate': str(band_rate),
                'lower': str(convert(max(Decimal(0), lower - Decimal('.005')), 'RUB')),
                'upper': str(convert(upper + Decimal('.005'), 'RUB')) if upper is not None else None})
        return amount * rate, {**evidence, "account_id": str(store["client_id"]), 'price_bands': bands}

    def _yandex(self, store, category, source, amount, currency, *, force_official=False):
        if not category.isdigit() or int(category) <= 0:
            raise ValueError("Yandex 官方佣金查询需要有效的末级类目 ID")
        campaign = str(source.get('_yandex_campaign_id') or store.get("campaign_id") or "")
        if not campaign.isdigit() or int(campaign) <= 0 or not store.get("api_token"):
            raise ValueError("Yandex 官方佣金查询需要当前店铺授权和 Campaign ID")
        # 按 Campaign 试算时售价和费用使用店铺币种，不能同时传 currency。
        offer = {"categoryId": int(category), "price": float(amount), "quantity": 1}
        for key in ("length", "width", "height", "weight"):
            value = source.get("weight_kg" if key == "weight" else f"{key}_cm")
            offer[key] = float(decimal(value, f"包装 {key}", positive=True))
        payload = {"parameters": {"campaignId": int(campaign)}, "offers": [offer]}
        endpoint = "/v2/tariffs/calculate"
        options = {'campaign_id': campaign} if source.get('_yandex_campaign_id') else {}
        if force_official:
            options['force_official'] = True
        fee = self.yandex_quote(offer, currency, **options)
        if "cached_rule" in fee:
            return decimal(fee.get("amount"), "金额"), {
                "source": "local_calculation", "cached_rule": fee["cached_rule"],
            }
        return decimal(fee.get("amount"), "金额"), {
            "endpoint": endpoint, "account_id": campaign, "request": payload,
            "tariff": fee, "limitation": "官方费用试算，最终按实际服务结算",
        }

    def _mercado(self, store, category, target, source, amount, currency, convert):
        if not store.get("access_token"):
            raise ValueError("Mercado 官方佣金查询需要有效的店铺访问令牌")
        selections = target.get("sites_to_sell") or []
        site = str(target.get("site") or "").upper()
        conditions = {}
        if site == "CBT":
            if len(selections) != 1:
                raise ValueError("Mercado 官方佣金必须按销售国家分别查询")
            conditions = selections[0]
            binding = mercadolibre_binding_for_target(conditions, store.get("marketplace_bindings"))
            if not binding:
                raise ValueError("Mercado 销售国家没有对应的店铺授权")
            site = str(binding.get("site_id") or "").upper()
            account = str(binding.get("seller_id") or "")
        else:
            account = str(store.get("user_id") or "")
        if not re.fullmatch(r"ML[A-Z]", site) or not account:
            raise ValueError("Mercado 官方佣金查询缺少销售站点或授权子账号")
        listing_type = str(conditions.get("listing_type_id") or target.get("listing_type_id") or self.store_config.get("listing", {}).get("listing_type_id") or "")
        logistic = str(conditions.get("logistic_type") or target.get("logistic_type") or "")
        if not listing_type or not logistic:
            raise ValueError("Mercado 官方佣金查询缺少刊登类型或物流方式")
        if category.startswith("CBT"):
            if currency != "USD":
                raise ValueError("Mercado CBT 官方上架前核价只支持 USD 发布币种")
            category_endpoint = "/categories/" + url_quote(category)
            detail = self._read("mercadolibre", category_endpoint, {}, lambda: request_json("GET", "https://api.mercadolibre.com" + category_endpoint, store["access_token"]))
            if not isinstance(detail, dict) or detail.get("id") != category or detail.get("children_categories") != []:
                raise ValueError("Mercado 官方类目资料未确认当前类目为末级类目")
            settings = detail.get("settings") if isinstance(detail.get("settings"), dict) else {}
            domain = str(settings.get("catalog_domain") or detail.get("domain_id") or "")
            fee, evidence = self.mercado_tariffs.quote(category, domain, site, logistic, listing_type, amount, convert)
            return fee, {**evidence, "account_id": account, "category_endpoint": category_endpoint}
        # 区域 Seller API 仅接收该国家的真实类目，不伪造 CBT→区域类目映射。
        if not category.startswith(site):
            raise ValueError("Mercado 官方佣金试算的类目与销售国家不一致")
        params = {"category_id": category, "price": str(amount), "currency_id": currency,
                  "listing_type_id": listing_type, "logistic_type": logistic,
                  "shipping_mode": "me2", "billable_weight": str(decimal(source.get("weight_kg"), "包装重量", positive=True) * 1000)}
        endpoint = f"/sites/{site}/listing_prices"
        body = self._read("mercadolibre", endpoint, params, lambda: request_json("GET", "https://api.mercadolibre.com" + endpoint + "?" + urlencode(params), store["access_token"]))
        rows = body if isinstance(body, list) else [body]
        fees = [item for item in rows if isinstance(item, dict) and item.get("listing_type_id") == listing_type]
        if len(fees) != 1 or fees[0].get("currency_id") != currency:
            raise ValueError("Mercado 官方佣金返回的刊登类型或币种不一致")
        fee = fees[0]
        return decimal(fee.get("sale_fee_amount"), "金额"), {
            "endpoint": endpoint, "account_id": account, "site_id": site,
            "logistic_type": logistic, "request": params, "sale_fee_details": fee.get("sale_fee_details", {}),
            "limitation": "官方销售费用已含固定收费，不重复相加",
        }



__all__ = ["PricingCommissions", "create_yandex_commission_batch"]
