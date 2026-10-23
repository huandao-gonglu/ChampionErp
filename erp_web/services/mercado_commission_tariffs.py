"""Mercado CBT 官方上架前类目费率页与标准低价商品收费规则。"""
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.parse import quote, urlencode

from erp_web.services.commission_sources import read_official_content


CATEGORY_PAGE = "https://global-selling.mercadolibre.com/landing/selling-fee"
PRICING_PAGE = "https://global-selling.mercadolibre.com/landing/pricing"
_SITE_CURRENCY = {"MLM": "MXN", "MLB": "BRL", "MLC": "CLP", "MCO": "COP"}


class _PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def _money(text):
    text = text.replace(",", "")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
        text = text.replace(".", "")
    value = Decimal(text)
    if not value.is_finite() or value <= 0:
        raise ValueError("Mercado 官方固定收费规则无效")
    return value


class MercadoCommissionTariffs:
    """读取官方网页自身使用的类目费率数据；不使用任何已创建商品。"""

    def __init__(self):
        self.loaded = False
        self.error = ""
        self.fees = {}

    def _load(self):
        if self.error:
            raise ValueError(self.error)
        if self.loaded:
            return
        try:
            pricing_bytes = read_official_content(PRICING_PAGE)
            text = _PageText()
            text.feed(pricing_bytes.decode("utf-8"))
            plain = " ".join(text.parts)
            pattern = r"Additional per unit fee\s+(MXN|BRL|CLP|COP)\s*\$\s*([\d.,]+)\s+For products up to\s+\1\s*\$\s*([\d.,]+)"
            fixed_rules = {}
            for unit, amount, maximum in re.findall(pattern, plain):
                rule = (_money(amount), _money(maximum))
                if unit in fixed_rules and fixed_rules[unit] != rule:
                    raise ValueError("Mercado 官方费用页存在不同的固定收费规则")
                fixed_rules[unit] = rule
            if not fixed_rules:
                raise ValueError("Mercado 官方费用页缺少完整固定收费规则")
            self.fixed_rules = fixed_rules
            self.source_hash = hashlib.sha256(pricing_bytes).hexdigest()
            self.loaded = True
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            self.error = str(exc) if isinstance(exc, ValueError) else "Mercado 官方费率资料格式无效"
            raise ValueError(self.error) from None

    def quote(self, category, domain, site, logistic, listing_type, amount, convert):
        self._load()
        if not re.fullmatch(r"CBT\d+", category) or not re.fullmatch(r"CBT-[A-Z0-9_]+", domain):
            raise ValueError("Mercado 官方佣金需要已确认的末级 CBT 类目和 Domain")
        if logistic not in {"remote", "fulfillment"} or listing_type not in {"gold_special", "gold_pro"}:
            raise ValueError("Mercado 当前官方类目费率页不支持该物流或刊登类型")
        unit = _SITE_CURRENCY.get(site)
        if not unit or unit not in self.fixed_rules:
            raise ValueError("Mercado 当前官方费用页未提供该国家的完整固定收费规则")
        params = {"sites": site}
        if logistic == "fulfillment":
            params["logistic_type"] = logistic
        endpoint = f"https://global-selling.mercadolibre.com/landing/api/categories/price/{quote(category)}/{quote(domain)}?{urlencode(params)}"
        if endpoint not in self.fees:
            try:
                self.fees[endpoint] = json.loads(read_official_content(endpoint, referer=CATEGORY_PAGE))
            except (ValueError, OSError):
                self.fees[endpoint] = ValueError("Mercado 官方类目费率读取失败，请稍后重新核价")
        body = self.fees[endpoint]
        if isinstance(body, ValueError):
            raise body
        rows = [row for row in body if isinstance(row, dict) and row.get("site_id") == site] if isinstance(body, list) else []
        if len(rows) != 1:
            raise ValueError("Mercado 官方类目费率缺少当前销售国家的唯一回执")
        rate = rows[0].get("classic" if listing_type == "gold_special" else "premium")
        if not isinstance(rate, dict) or rate.get("error") or rate.get("listing_type_id") != listing_type:
            raise ValueError("Mercado 官方类目费率未能确认当前刊登类型")
        try:
            percent = Decimal(str(rate.get("sale_fee_amount")))
        except ArithmeticError:
            raise ValueError("Mercado 官方类目费率缺失或无效") from None
        if not percent.is_finite() or not 0 <= percent < 100:
            raise ValueError("Mercado 官方类目费率无效")
        fixed, maximum = self.fixed_rules[unit]
        price_local = amount / convert(Decimal(1), unit)
        applied_fixed = convert(fixed, unit) if price_local <= maximum else Decimal(0)
        # 哥伦比亚低价消费品有类目豁免，当前公开费率页未返回机器可读的豁免判定。
        if site == "MCO" and applied_fixed:
            raise ValueError("Mercado 哥伦比亚低价商品需确认固定收费的类目豁免，暂不能完成上架前核价")
        return amount * percent / 100 + applied_fixed, {
            "source": "official_fee_page", "endpoint": endpoint, "source_sha256": self.source_hash,
            "response_sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
            "category_page": CATEGORY_PAGE, "pricing_page": PRICING_PAGE, "domain_id": domain,
            "site_id": site, "logistic_type": logistic, "listing_type_id": listing_type,
            "percentage_fee": str(percent), "fixed_fee": str(applied_fixed),
            "fixed_fee_rule": {"amount": str(fixed), "maximum_price": str(maximum), "currency": unit},
            "limitation": "官方公开 CBT 标准类目费率及低价固定收费；非公开 Seller API，页面结构变化时需重新核实，不含店铺促销与特殊协议",
        }


__all__ = ["MercadoCommissionTariffs"]
