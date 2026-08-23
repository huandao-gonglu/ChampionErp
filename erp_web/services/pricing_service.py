"""发布目标独立核价的纯业务逻辑，不依赖 Web 或持久化层。"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Any

from erp_web.marketplace_registry import marketplace_site


ML_SHIPPING_FALLBACK_TABLE = [
    {"max_g": 100, "usd": 1.70},
    {"max_g": 300, "usd": 2.70},
    {"max_g": 500, "usd": 3.40},
    {"max_g": 1000, "usd": 4.60},
    {"max_g": 2000, "usd": 7.20},
    {"max_g": 3000, "usd": 9.90},
    {"max_g": 5000, "usd": 14.80},
    {"max_g": 10000, "usd": 26.50},
    {"max_g": 15000, "usd": 37.80},
    {"max_g": 20000, "usd": 50.40},
    {"max_g": 30000, "usd": 73.80},
]


def service_status() -> dict[str, str]:
    return {"service": "pricing", "status": "ready"}


def number_value(value: Any, default: float = 0.0) -> float:
    text = str(value if value is not None else "").strip().replace(",", ".")
    if not text:
        return default
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return default
    try:
        return float(match.group(0))
    except ValueError:
        return default


def first_value(data: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def _amount_text(value: Any) -> str:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        amount = Decimal("0.00")
    return format(amount, "f")


def _money(amount: Any, currency: str) -> dict[str, str]:
    return {
        "amount": _amount_text(amount),
        "currency": str(currency or "").strip().upper(),
    }


def _money_input(value: Any, expected_currency: str) -> float:
    if not isinstance(value, dict):
        return 0.0
    currency = str(value.get("currency") or "").strip().upper()
    if currency != str(expected_currency or "").strip().upper():
        return 0.0
    return number_value(value.get("amount"))


def parse_dimensions(value: Any) -> tuple[float, float, float]:
    text = str(value or "")
    nums = [float(item.replace(",", ".")) for item in re.findall(r"\d+(?:[,.]\d+)?", text)]
    if len(nums) < 3:
        return 0.0, 0.0, 0.0
    factor = 2.54 if any(mark in text.lower() for mark in ("inch", "inches", "in ")) else 1.0
    return nums[0] * factor, nums[1] * factor, nums[2] * factor


def billable_weight_kg(length_cm: Any = 0, width_cm: Any = 0, height_cm: Any = 0, weight_kg: Any = 0) -> float:
    length = number_value(length_cm)
    width = number_value(width_cm)
    height = number_value(height_cm)
    weight = number_value(weight_kg)
    volume_kg = (length * width * height) / 6000 if length and width and height else 0.0
    return round(max(weight, volume_kg), 4)


def estimate_ml_shipping_usd(billable_kg: float, tiers: list[dict[str, Any]] | None = None) -> float:
    billable_g = max(1, int(round(number_value(billable_kg) * 1000)))
    table = tiers or ML_SHIPPING_FALLBACK_TABLE
    for tier in table:
        limit_g = int(number_value(tier.get("max_g")))
        cost = number_value(tier.get("usd"))
        if limit_g and billable_g <= limit_g:
            return round(cost, 2)
    last = table[-1]
    last_usd = number_value(last.get("usd"))
    last_kg = number_value(last.get("max_g"), 30000) / 1000
    extra_kg = max(0.0, number_value(billable_kg) - last_kg)
    return round(last_usd + extra_kg * 2.5, 2)


def normalize_pricing_input(data: dict[str, Any]) -> dict[str, Any]:
    source = data if isinstance(data, dict) else {}
    length = first_value(source, "length_cm", "package_length_cm", "length", default="")
    width = first_value(source, "width_cm", "package_width_cm", "width", default="")
    height = first_value(source, "height_cm", "package_height_cm", "height", default="")
    dimensions = first_value(source, "dimensions", "dimension_text", default="")
    if (not length or not width or not height) and dimensions:
        parsed_length, parsed_width, parsed_height = parse_dimensions(dimensions)
        length = length or parsed_length
        width = width or parsed_width
        height = height or parsed_height
    return {
        "platform": str(first_value(source, "platform", default="mercadolibre") or "mercadolibre").lower(),
        "site": str(first_value(source, "site", "site_id", default="MLM") or "MLM").upper(),
        "cost_cny": number_value(first_value(source, "cost_cny", "cost", "purchase_cost", "source_price_cny_for_cost", "source_price_cny")),
        "freight_cny": number_value(first_value(source, "freight_cny", "domestic_freight", "freight", "shipping_price_cny")),
        "prep_fee_cny": number_value(first_value(source, "ml_prep_fee_cny", "prep_fee_cny", "packaging_cost", "packaging")),
        "international_freight_cny": number_value(first_value(source, "international_freight_cny", "international_freight", "international_shipping")),
        "other_cost_cny": number_value(first_value(source, "other_cost_cny", "other_cost")),
        "warehousing_cost_cny": number_value(first_value(source, "warehousing_cost_cny", "warehousing_cost")),
        "advertising_cost_cny": number_value(first_value(source, "advertising_cost_cny", "advertising_cost")),
        "other_platform_fee_cny": number_value(first_value(source, "other_platform_fee_cny", "other_platform_fee")),
        "margin_percent": number_value(first_value(source, "margin_percent", "target_margin_percent", "target_margin", default=30), 30),
        "ml_commission_percent": number_value(first_value(source, "ml_commission_percent", "mercadolibre_commission_percent", "commission_percent", "commission_rate", default=16), 16),
        "payment_fee_percent": number_value(first_value(source, "payment_fee_percent", "payment_fee_rate", default=0), 0),
        "wb_commission_percent": number_value(first_value(source, "wb_commission_percent", default=20), 20),
        "usd_cny_rate": number_value(first_value(source, "usd_cny_rate", "usd_cny", "currency_rate", "rate")),
        "mxn_usd_rate": number_value(first_value(source, "mxn_usd_rate", "mxn_rate")),
        "rub_cny_rate": number_value(first_value(source, "rub_cny_rate", "rub_rate", default=12), 12),
        "ml_shipping_usd": number_value(first_value(source, "ml_shipping_usd", "shipping_usd", "shipping_cost")),
        "russia_freight_rate": number_value(first_value(source, "russia_freight_rate", default=0)),
        "sale_price_mxn": number_value(first_value(source, "sale_price_mxn", "mx_price", "mercadolibre_price")),
        "sale_price_usd": number_value(first_value(source, "sale_price_usd", "price_usd")),
        "target_profit_cny": number_value(first_value(source, "target_profit_cny", "target_profit")),
        "target_net_proceeds_usd": number_value(first_value(source, "target_net_proceeds_usd", "target_net_usd")),
        "length_cm": number_value(length),
        "width_cm": number_value(width),
        "height_cm": number_value(height),
        "weight_kg": number_value(first_value(source, "weight_kg", "package_weight_kg", "source_weight_kg")),
        "stock": int(number_value(first_value(source, "stock", "available_quantity", default=0))),
    }


def _base_values(values: dict[str, Any]) -> dict[str, float]:
    billable_kg = billable_weight_kg(values["length_cm"], values["width_cm"], values["height_cm"], values["weight_kg"])
    if values["ml_shipping_usd"] <= 0 and billable_kg > 0:
        values["ml_shipping_usd"] = estimate_ml_shipping_usd(billable_kg)
    common_base = (
        values["cost_cny"]
        + values["freight_cny"]
        + values["international_freight_cny"]
        + values["other_cost_cny"]
        + values["warehousing_cost_cny"]
        + values["advertising_cost_cny"]
        + values["other_platform_fee_cny"]
    )
    ml_shipping_cny = values["ml_shipping_usd"] * values["usd_cny_rate"]
    ml_base = common_base + values["prep_fee_cny"] + ml_shipping_cny
    return {
        "billable_kg": billable_kg,
        "volume_weight_kg": round((values["length_cm"] * values["width_cm"] * values["height_cm"]) / 6000, 4) if values["length_cm"] and values["width_cm"] and values["height_cm"] else 0.0,
        "common_base_cny": common_base,
        "ml_shipping_cny": ml_shipping_cny,
        "ml_base_cny": ml_base,
    }


def pricing_target_key(platform: str, site: str) -> str:
    platform_key = str(platform or "").strip().lower()
    site_key = str(site or "").strip().upper()
    return f"{platform_key}:{site_key}" if site_key else platform_key


def pricing_calculation_fingerprint(basis: dict[str, Any]) -> str:
    payload = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _target_value(target: dict[str, Any], source: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for container in (target, source):
        for key in keys:
            value = container.get(key)
            if value not in (None, ""):
                return value
    return default


def _target_number(target: dict[str, Any], source: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    return number_value(_target_value(target, source, *keys, default=default), default)


def _currency_usd_rates(source: dict[str, Any]) -> dict[str, float]:
    raw = source.get("currency_usd_rates") or source.get("currencyUsdRates")
    rates_record = _record(source.get("rates"))
    if not raw and isinstance(rates_record.get("currency_usd_rates"), dict):
        raw = rates_record.get("currency_usd_rates")
    rates: dict[str, float] = {"USD": 1.0}
    if isinstance(raw, dict):
        for key, value in raw.items():
            currency = str(key or "").strip().upper()
            rate = number_value(value)
            if currency and rate > 0:
                rates[currency] = rate
    for key, currency in (
        ("mxn_usd_rate", "MXN"),
        ("rub_usd_rate", "RUB"),
        ("brl_usd_rate", "BRL"),
        ("clp_usd_rate", "CLP"),
        ("cop_usd_rate", "COP"),
        ("ars_usd_rate", "ARS"),
    ):
        rate = number_value(source.get(key))
        if rate > 0:
            rates[currency] = rate
    return rates


def _currency_per_usd(currency: str, source: dict[str, Any], values: dict[str, Any]) -> float:
    currency_key = str(currency or "").strip().upper() or "USD"
    if currency_key == "USD":
        return 1.0
    if currency_key == "CNY":
        return values["usd_cny_rate"]
    rates = _currency_usd_rates(source)
    if rates.get(currency_key, 0) > 0:
        return rates[currency_key]
    if currency_key == "MXN":
        return values["mxn_usd_rate"]
    if currency_key == "RUB" and values["rub_cny_rate"] > 0 and values["usd_cny_rate"] > 0:
        return values["rub_cny_rate"] * values["usd_cny_rate"]
    return 0.0


def _currency_per_cny(currency: str, source: dict[str, Any], values: dict[str, Any]) -> float:
    currency_key = str(currency or "").strip().upper() or "USD"
    if currency_key == "CNY":
        return 1.0
    if currency_key == "RUB" and values["rub_cny_rate"] > 0:
        return values["rub_cny_rate"]
    rate_per_usd = _currency_per_usd(currency_key, source, values)
    return rate_per_usd / values["usd_cny_rate"] if rate_per_usd > 0 and values["usd_cny_rate"] > 0 else 0.0


def _target_defaults(platform: str) -> dict[str, float]:
    platform_key = str(platform or "").strip().lower()
    if platform_key in {"yandex", "ozon"}:
        return {"commission_percent": 20.0, "payment_fee_percent": 0.0, "target_margin_percent": 30.0}
    return {"commission_percent": 16.0, "payment_fee_percent": 0.0, "target_margin_percent": 30.0}


def calculate_target_pricing(common: dict[str, Any], target: dict[str, Any], index: int = 0) -> dict[str, Any]:
    source = {**common, **target}
    platform = str(_target_value(target, source, "platform", default="mercadolibre") or "mercadolibre").strip().lower()
    site_config = marketplace_site(platform, str(_target_value(target, source, "site", "site_id", default="")))
    site = str(_target_value(target, source, "site", "site_id", default=site_config.get("code") or "") or "").strip() or site_config.get("code", "")
    currency = str(
        _target_value(
            target,
            source,
            "listing_currency",
            "listingCurrency",
            default="",
        )
        or ""
    ).strip().upper()
    defaults = _target_defaults(platform)
    commission_percent = _target_number(
        target,
        source,
        "commission_percent",
        "commissionPercent",
        "ml_commission_percent",
        "wb_commission_percent",
        default=defaults["commission_percent"],
    )
    payment_fee_percent = _target_number(target, source, "payment_fee_percent", "paymentFeePercent", default=defaults["payment_fee_percent"])
    other_fee_percent = _target_number(target, source, "other_fee_percent", "otherFeePercent", default=0)
    target_margin_percent = _target_number(target, source, "target_margin_percent", "targetMarginPercent", "margin_percent", default=defaults["target_margin_percent"])
    markup_percent = _target_number(target, source, "markup_percent", "markupPercent", default=30)
    pricing_mode = str(_target_value(target, source, "pricing_mode", "pricingMode", default="margin") or "margin").strip().lower()
    if pricing_mode not in {"margin", "markup", "manual"}:
        pricing_mode = "margin"
    source.update(
        {
            "platform": platform,
            "site": site,
            "commission_percent": commission_percent,
            "ml_commission_percent": commission_percent,
            "payment_fee_percent": payment_fee_percent,
            "other_fee_percent": other_fee_percent,
            "target_margin_percent": target_margin_percent,
            "margin_percent": target_margin_percent,
        }
    )
    values = normalize_pricing_input(source)
    base = _base_values(values)
    errors: list[dict[str, str]] = []
    if values["cost_cny"] <= 0:
        errors.append({"field": "cost_cny", "message": "采购成本缺失"})
    for field, label, value in (
        ("domestic_freight", "国内物流", values["freight_cny"]),
        ("packaging_cost", "包装耗材", values["prep_fee_cny"]),
        ("other_cost", "其他固定成本", values["other_cost_cny"]),
    ):
        if not math.isfinite(value) or value < 0:
            errors.append({"field": field, "message": f"{label}不能小于 0"})

    shipping_mode = str(_target_value(target, source, "shipping_quote_mode", "shippingQuoteMode", default="") or "").strip().lower()
    if shipping_mode not in {"auto", "manual"}:
        shipping_mode = "auto" if platform == "mercadolibre" else "manual"
    raw_shipping_currency = str(_target_value(target, source, "shipping_currency", "shippingCurrency", default="") or "").strip().upper()
    legacy_shipping_usd = _target_number(target, source, "shipping_cost_usd", "shippingCostUsd", "ml_shipping_usd", "shipping_usd", default=0)
    legacy_shipping_cny = _target_number(target, source, "shipping_cost_cny", "shippingCostCny", default=0)
    shipping_currency = raw_shipping_currency if raw_shipping_currency in {"USD", "CNY"} else ("USD" if legacy_shipping_usd > 0 or platform == "mercadolibre" else "CNY")
    shipping_amount = _target_number(target, source, "shipping_amount", "shippingAmount", default=0)
    if shipping_amount <= 0:
        shipping_amount = legacy_shipping_usd if shipping_currency == "USD" else legacy_shipping_cny

    shipping_usd = 0.0
    shipping_cny = 0.0
    shipping_source = "manual_quote"
    if shipping_mode == "auto":
        shipping_source = "system_estimate"
        if platform != "mercadolibre":
            errors.append({"field": "shipping_quote_mode", "message": "当前平台没有自动物流报价，请填写物流商报价"})
        elif base["billable_kg"] <= 0:
            errors.append({"field": "weight_or_dimensions", "message": "自动估算物流费需要重量或尺寸"})
        elif values["usd_cny_rate"] <= 0:
            errors.append({"field": "usd_cny_rate", "message": "自动估算物流费需要 USD/CNY 汇率"})
        else:
            shipping_currency = "USD"
            shipping_usd = values["ml_shipping_usd"]
            shipping_amount = shipping_usd
            shipping_cny = shipping_usd * values["usd_cny_rate"]
    else:
        if shipping_amount <= 0 or not math.isfinite(shipping_amount):
            errors.append({"field": "shipping_amount", "message": "物流报价金额必须大于 0"})
        elif shipping_currency == "USD":
            if values["usd_cny_rate"] <= 0:
                errors.append({"field": "usd_cny_rate", "message": "USD 物流报价需要 USD/CNY 汇率"})
            else:
                shipping_usd = shipping_amount
                shipping_cny = shipping_amount * values["usd_cny_rate"]
        else:
            shipping_cny = shipping_amount
            shipping_usd = shipping_amount / values["usd_cny_rate"] if values["usd_cny_rate"] > 0 else 0.0

    packaging_cost_cny = values["prep_fee_cny"]
    fixed_cost_cny = base["common_base_cny"] + packaging_cost_cny
    total_cost_cny = fixed_cost_cny + shipping_cny

    for field, label, value in (
        ("commission_percent", "平台佣金", commission_percent),
        ("payment_fee_percent", "支付/结算手续费", payment_fee_percent),
        ("other_fee_percent", "其他平台费用", other_fee_percent),
    ):
        if not math.isfinite(value) or value < 0 or value >= 100:
            errors.append({"field": field, "message": f"{label}必须在 0% 到 100% 之间"})

    commission = commission_percent / 100
    payment_fee = payment_fee_percent / 100
    other_fee = other_fee_percent / 100
    fee_rate = commission + payment_fee + other_fee
    fee_denominator = 1 - fee_rate
    if fee_denominator <= 0:
        errors.append({"field": "platform_fee_percent", "message": "平台佣金、支付手续费和其他平台费用合计必须小于 100%"})
    margin = target_margin_percent / 100
    if pricing_mode == "margin":
        if not math.isfinite(target_margin_percent) or target_margin_percent < 0 or target_margin_percent >= 100:
            errors.append({"field": "target_margin_percent", "message": "目标销售利润率必须在 0% 到 100% 之间"})
        elif fee_rate + margin >= 1:
            errors.append({"field": "target_margin_percent", "message": "平台费用合计 + 目标销售利润率必须小于 100%"})
    elif pricing_mode == "markup" and (not math.isfinite(markup_percent) or markup_percent < 0):
        errors.append({"field": "markup_percent", "message": "成本加价率不能小于 0%"})

    currency_rate_cny = _currency_per_cny(currency, source, values) if currency else 0.0
    if not currency:
        errors.append({"field": "listing_currency", "message": "店铺发布币种尚未核验"})
    elif currency_rate_cny <= 0:
        errors.append({"field": "currency_rate", "message": f"{currency}/CNY 汇率缺失"})
    manual_price = _target_value(
        target,
        source,
        "manual_price",
        "manualPrice",
        default={},
    )
    applied_price_input = _money_input(manual_price, currency)
    if pricing_mode == "manual" and applied_price_input <= 0:
        errors.append({"field": "manual_price", "message": "手动售价必须大于 0 且币种必须与发布币种一致"})

    revenue_cny = 0.0
    if not errors and total_cost_cny > 0:
        if pricing_mode == "margin":
            revenue_cny = total_cost_cny / (1 - fee_rate - margin)
        elif pricing_mode == "markup":
            revenue_cny = total_cost_cny * (1 + markup_percent / 100) / fee_denominator
        else:
            revenue_cny = applied_price_input / currency_rate_cny
    suggested_price = revenue_cny * currency_rate_cny if revenue_cny > 0 and currency_rate_cny > 0 else 0.0
    suggested_price_usd = revenue_cny / values["usd_cny_rate"] if revenue_cny > 0 and values["usd_cny_rate"] > 0 else 0.0
    applied_price = (
        applied_price_input
        if pricing_mode == "manual" and applied_price_input > 0 and not errors
        else suggested_price
    )
    actual_revenue_cny = applied_price / currency_rate_cny if applied_price > 0 and currency_rate_cny > 0 else 0.0
    commission_cny = actual_revenue_cny * commission
    payment_fee_cny = actual_revenue_cny * payment_fee
    other_fee_cny = actual_revenue_cny * other_fee
    profit_cny = actual_revenue_cny - commission_cny - payment_fee_cny - other_fee_cny - total_cost_cny if not errors else 0.0
    net_revenue_cny = actual_revenue_cny - commission_cny - payment_fee_cny - other_fee_cny
    profit_percent = (profit_cny / actual_revenue_cny * 100) if actual_revenue_cny else 0.0
    minimum_revenue_cny = total_cost_cny / fee_denominator if total_cost_cny > 0 and fee_denominator > 0 else 0.0
    minimum_price = minimum_revenue_cny * currency_rate_cny if currency_rate_cny > 0 else 0.0
    key = str(_target_value(target, source, "target_key", "targetKey", default=pricing_target_key(platform, site)) or "").strip() or pricing_target_key(platform, site)
    calculation_basis = {
        "platform": platform,
        "site": site,
        "listing_currency": currency,
        "currency_fingerprint": str(target.get("currency_fingerprint") or "").strip(),
        "cost_cny": _amount_text(values["cost_cny"]),
        "domestic_freight_cny": _amount_text(values["freight_cny"]),
        "packaging_cost_cny": _amount_text(packaging_cost_cny),
        "other_cost_cny": _amount_text(values["other_cost_cny"]),
        "weight_kg": _amount_text(values["weight_kg"]),
        "length_cm": _amount_text(values["length_cm"]),
        "width_cm": _amount_text(values["width_cm"]),
        "height_cm": _amount_text(values["height_cm"]),
        "usd_cny_rate": _amount_text(values["usd_cny_rate"]),
        "mxn_usd_rate": _amount_text(values["mxn_usd_rate"]),
        "rub_cny_rate": _amount_text(values["rub_cny_rate"]),
        "commission_percent": _amount_text(commission_percent),
        "payment_fee_percent": _amount_text(payment_fee_percent),
        "other_fee_percent": _amount_text(other_fee_percent),
        "pricing_mode": pricing_mode,
        "target_margin_percent": _amount_text(target_margin_percent),
        "markup_percent": _amount_text(markup_percent),
        "shipping_quote_mode": shipping_mode,
        "shipping_currency": shipping_currency,
        "shipping_amount": _amount_text(shipping_amount),
        "manual_price": _money(applied_price_input, currency) if pricing_mode == "manual" else None,
    }
    return {
        "ok": not errors,
        "target_key": key,
        "platform": platform,
        "site": site,
        "listing_currency": currency,
        "currency_fingerprint": str(target.get("currency_fingerprint") or "").strip(),
        "index": index,
        "suggested_price": _money(suggested_price, currency),
        "applied_price": _money(applied_price, currency),
        "minimum_price": _money(minimum_price, currency),
        "converted_prices": {
            "CNY": _amount_text(revenue_cny),
            "USD": _amount_text(suggested_price_usd),
        },
        "calculation_basis": calculation_basis,
        "calculation_fingerprint": pricing_calculation_fingerprint(calculation_basis),
        "shipping_cost_usd": round(shipping_usd, 2),
        "shipping_cost_cny": round(shipping_cny, 2),
        "shipping_quote_mode": shipping_mode,
        "shipping_currency": shipping_currency,
        "shipping_amount": round(shipping_amount, 2),
        "shipping_source": shipping_source,
        "total_cost_cny": round(total_cost_cny, 2),
        "net_revenue_cny": round(net_revenue_cny, 2),
        "profit_cny": round(profit_cny, 2),
        "profit_usd": round(profit_cny / values["usd_cny_rate"], 2) if values["usd_cny_rate"] > 0 else 0.0,
        "margin_percent": round(profit_percent, 2),
        "commission_percent": round(commission_percent, 2),
        "payment_fee_percent": round(payment_fee_percent, 2),
        "other_fee_percent": round(other_fee_percent, 2),
        "pricing_mode": pricing_mode,
        "target_margin_percent": round(target_margin_percent, 2),
        "markup_percent": round(markup_percent, 2),
        "commission_cny": round(commission_cny, 2),
        "payment_fee_cny": round(payment_fee_cny, 2),
        "other_fee_cny": round(other_fee_cny, 2),
        "billable_weight_kg": base["billable_kg"],
        "usd_cny_rate": round(values["usd_cny_rate"], 4),
        "mxn_usd_rate": round(values["mxn_usd_rate"], 4),
        "rub_cny_rate": round(values["rub_cny_rate"], 6),
        "currency_per_cny": round(currency_rate_cny, 6),
        "is_loss": profit_cny < 0,
        "errors": errors,
        "precheck_errors": errors,
        "input": {**values, "listing_currency": currency, "target_key": key},
        "breakdown": {
            "billable_weight_kg": base["billable_kg"],
            "billable_weight_g": int(round(base["billable_kg"] * 1000)) if base["billable_kg"] else 0,
            "actual_weight_kg": values["weight_kg"],
            "volume_weight_kg": base["volume_weight_kg"],
            "common_base_cny": round(base["common_base_cny"], 2),
            "packaging_cost_cny": round(packaging_cost_cny, 2),
            "shipping_cny": round(shipping_cny, 2),
            "cost_cny": round(values["cost_cny"], 2),
            "freight_cny": round(values["freight_cny"], 2),
            "commission_cny": round(commission_cny, 2),
            "payment_fee_cny": round(payment_fee_cny, 2),
            "other_fee_cny": round(other_fee_cny, 2),
            "target_margin_percent": round(target_margin_percent, 2),
            "markup_percent": round(markup_percent, 2),
            "minimum_price": round(minimum_price, 2),
        },
        "formula": {
            "margin": "建议售价 = 总成本 / (1 - 平台费用合计 - 目标销售利润率)",
            "markup": "建议售价 = 总成本 × (1 + 成本加价率) / (1 - 平台费用合计)",
            "manual": "实际利润率 = (售价 - 平台费用 - 总成本) / 售价",
        }[pricing_mode],
    }


def calculate_pricing_batch(data: dict[str, Any]) -> dict[str, Any]:
    source = data if isinstance(data, dict) else {}
    common = _record(source.get("common"))
    targets = source.get("targets")
    if not isinstance(targets, list) or not targets:
        return {"ok": False, "error": "核价必须指定至少一个发布目标。", "results": []}
    common_source = {**source, **common}
    for key, value in source.items():
        if key in {"common", "targets"}:
            continue
        if value not in (None, "", [], {}):
            common_source[key] = value
    common_source.pop("targets", None)
    results = [calculate_target_pricing(common_source, _record(target), index) for index, target in enumerate(targets)]
    primary = results[0] if results else {}
    errors = [error for result in results for error in result.get("errors", []) if isinstance(error, dict)]
    response: dict[str, Any] = {
        "ok": bool(results) and all(bool(result.get("ok")) for result in results),
        "mode": "batch",
        "results": results,
        "targets": results,
        "errors": errors,
        "precheck_errors": errors,
        "input": {"common": common_source, "targets": targets},
    }
    if primary:
        response.update(
            {
                "platform": primary.get("platform"),
                "site": primary.get("site"),
                "listing_currency": primary.get("listing_currency"),
                "suggested_price": primary.get("suggested_price"),
                "applied_price": primary.get("applied_price"),
                "minimum_price": primary.get("minimum_price"),
                "converted_prices": primary.get("converted_prices"),
                "shipping_cost_usd": primary.get("shipping_cost_usd"),
                "shipping_cost_cny": primary.get("shipping_cost_cny"),
                "total_cost_cny": primary.get("total_cost_cny"),
                "net_revenue_cny": primary.get("net_revenue_cny"),
                "profit_cny": primary.get("profit_cny"),
                "profit_percent": primary.get("margin_percent"),
                "margin_percent": primary.get("margin_percent"),
                "is_loss": primary.get("is_loss"),
                "breakdown": primary.get("breakdown"),
                "formula": primary.get("formula"),
            }
        )
    return response


def pricing_result(data: dict[str, Any]) -> dict[str, Any]:
    return calculate_pricing_batch(data)
