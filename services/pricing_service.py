"""Pure pricing logic extracted from the desktop ERP.

This service has no Tkinter or Web dependency. Mercado Libre Mexico is the
primary target in stage 3, while WB/Ozon placeholders remain lightweight.
"""

from __future__ import annotations

import re
from typing import Any


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
        "cost_cny": number_value(first_value(source, "cost_cny", "cost", "purchase_cost", "source_price_cny_for_cost", "source_price_cny", "detected_price")),
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
        "usd_cny_rate": number_value(first_value(source, "usd_cny_rate", "usd_cny", "currency_rate", "rate", default=7.2), 7.2),
        "mxn_usd_rate": number_value(first_value(source, "mxn_usd_rate", "mxn_rate", default=18), 18),
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


def calculate_pricing(data: dict[str, Any]) -> dict[str, Any]:
    values = normalize_pricing_input(data)
    base = _base_values(values)
    margin = values["margin_percent"] / 100
    ml_fee = values["ml_commission_percent"] / 100
    payment_fee = values["payment_fee_percent"] / 100
    wb_fee = values["wb_commission_percent"] / 100

    errors: list[dict[str, str]] = []
    if values["cost_cny"] <= 0:
        errors.append({"field": "cost_cny", "message": "采购成本缺失"})
    if base["billable_kg"] <= 0:
        errors.append({"field": "weight_or_dimensions", "message": "重量或尺寸缺失"})
    if values["usd_cny_rate"] <= 0:
        errors.append({"field": "usd_cny_rate", "message": "USD/CNY 汇率缺失"})
    if values["mxn_usd_rate"] <= 0:
        errors.append({"field": "mxn_usd_rate", "message": "MXN/USD 汇率缺失"})
    if 1 - ml_fee - payment_fee - margin <= 0:
        errors.append({"field": "margin_percent", "message": "目标利润率 + Mercado Libre 佣金 + 支付手续费不能大于等于 100%"})

    ml_denominator = max(1 - ml_fee - payment_fee - margin, 0.01)
    suggested_price_usd = base["ml_base_cny"] / ml_denominator / values["usd_cny_rate"] if values["usd_cny_rate"] else 0.0
    suggested_price_mxn = suggested_price_usd * values["mxn_usd_rate"]
    price_usd = values["sale_price_usd"] or (values["sale_price_mxn"] / values["mxn_usd_rate"] if values["sale_price_mxn"] else suggested_price_usd)
    price_mxn = values["sale_price_mxn"] or price_usd * values["mxn_usd_rate"]
    revenue_cny = price_usd * values["usd_cny_rate"]
    commission_cny = revenue_cny * ml_fee
    payment_fee_cny = revenue_cny * payment_fee
    profit_cny = revenue_cny - commission_cny - payment_fee_cny - base["ml_base_cny"]
    net_proceeds_cny = revenue_cny - commission_cny - payment_fee_cny - base["ml_shipping_cny"]
    profit_percent = (profit_cny / revenue_cny * 100) if revenue_cny else 0.0

    wb_base = base["common_base_cny"] + base["billable_kg"] * values["russia_freight_rate"]
    wb_denominator = max(1 - wb_fee - margin, 0.01)
    wb_price_rub = wb_base / wb_denominator * values["rub_cny_rate"] if values["rub_cny_rate"] else 0.0

    return {
        "ok": not errors,
        "platform": values["platform"],
        "site": values["site"],
        "currency": "MXN",
        "suggested_price": round(suggested_price_mxn, 2),
        "suggested_price_mxn": round(suggested_price_mxn, 2),
        "suggested_price_usd": round(suggested_price_usd, 2),
        "sale_price_mxn": round(price_mxn, 2),
        "sale_price_usd": round(price_usd, 2),
        "reverse_price_mxn": round(suggested_price_mxn, 2),
        "reverse_price_usd": round(suggested_price_usd, 2),
        "shipping_cost_usd": round(values["ml_shipping_usd"], 2),
        "shipping_cost_cny": round(base["ml_shipping_cny"], 2),
        "commission_cny": round(commission_cny, 2),
        "payment_fee_cny": round(payment_fee_cny, 2),
        "total_cost_cny": round(base["ml_base_cny"], 2),
        "net_revenue_cny": round(net_proceeds_cny, 2),
        "net_proceeds_usd": round(net_proceeds_cny / values["usd_cny_rate"], 2) if values["usd_cny_rate"] else 0.0,
        "profit_cny": round(profit_cny, 2),
        "profit_usd": round(profit_cny / values["usd_cny_rate"], 2) if values["usd_cny_rate"] else 0.0,
        "profit_percent": round(profit_percent, 2),
        "is_loss": profit_cny < 0,
        "wb_price_rub": int(round(wb_price_rub, 0)) if wb_price_rub else 0,
        "errors": errors,
        "precheck_errors": errors,
        "input": values,
        "breakdown": {
            "billable_weight_kg": base["billable_kg"],
            "billable_weight_g": int(round(base["billable_kg"] * 1000)) if base["billable_kg"] else 0,
            "actual_weight_kg": values["weight_kg"],
            "volume_weight_kg": base["volume_weight_kg"],
            "common_base_cny": round(base["common_base_cny"], 2),
            "ml_base_cny": round(base["ml_base_cny"], 2),
            "cost_cny": round(values["cost_cny"], 2),
            "freight_cny": round(values["freight_cny"], 2),
            "prep_fee_cny": round(values["prep_fee_cny"], 2),
            "international_freight_cny": round(values["international_freight_cny"], 2),
            "other_cost_cny": round(values["other_cost_cny"], 2),
            "warehousing_cost_cny": round(values["warehousing_cost_cny"], 2),
            "advertising_cost_cny": round(values["advertising_cost_cny"], 2),
            "other_platform_fee_cny": round(values["other_platform_fee_cny"], 2),
            "ml_shipping_usd": round(values["ml_shipping_usd"], 2),
            "ml_shipping_cny": round(base["ml_shipping_cny"], 2),
            "commission_cny": round(commission_cny, 2),
            "commission_percent": round(values["ml_commission_percent"], 2),
            "payment_fee_cny": round(payment_fee_cny, 2),
            "payment_fee_percent": round(values["payment_fee_percent"], 2),
            "target_margin_percent": round(values["margin_percent"], 2),
            "usd_cny_rate": round(values["usd_cny_rate"], 4),
            "mxn_usd_rate": round(values["mxn_usd_rate"], 4),
            "desktop_formula": "price_usd = total_cost_cny / (1 - commission - payment_fee - margin) / USD_CNY",
        },
        "formula": "建议售价 = 总成本 / (1 - 平台佣金 - 支付手续费 - 目标利润率)",
    }


def reverse_price_from_profit(data: dict[str, Any]) -> dict[str, Any]:
    values = normalize_pricing_input(data)
    base = _base_values(values)
    ml_fee = values["ml_commission_percent"] / 100
    payment_fee = values["payment_fee_percent"] / 100
    denominator = max(1 - ml_fee - payment_fee, 0.01)
    revenue_cny = (base["ml_base_cny"] + values["target_profit_cny"]) / denominator
    price_usd = revenue_cny / values["usd_cny_rate"] if values["usd_cny_rate"] else 0.0
    price_mxn = price_usd * values["mxn_usd_rate"]
    result = calculate_pricing({**values, "sale_price_usd": price_usd, "sale_price_mxn": price_mxn})
    result["mode"] = "reverse_profit"
    return result


def reverse_price_from_net_proceeds(data: dict[str, Any]) -> dict[str, Any]:
    values = normalize_pricing_input(data)
    base = _base_values(values)
    ml_fee = values["ml_commission_percent"] / 100
    payment_fee = values["payment_fee_percent"] / 100
    denominator = max(1 - ml_fee - payment_fee, 0.01)
    price_usd = (values["target_net_proceeds_usd"] + values["ml_shipping_usd"]) / denominator
    price_mxn = price_usd * values["mxn_usd_rate"]
    result = calculate_pricing({**values, "sale_price_usd": price_usd, "sale_price_mxn": price_mxn})
    result["mode"] = "reverse_net_proceeds"
    result["reverse_price_mxn"] = round(price_mxn, 2)
    result["reverse_price_usd"] = round(price_usd, 2)
    result["breakdown"]["reverse_net_formula"] = "price_usd = (target_net_income_usd + shipping_usd) / (1 - commission - payment_fee)"
    return result


def pricing_result(data: dict[str, Any]) -> dict[str, Any]:
    mode = str((data or {}).get("mode") or "").strip().lower()
    if mode in {"reverse_profit", "profit"} or number_value((data or {}).get("target_profit_cny")) > 0:
        return reverse_price_from_profit(data)
    if mode in {"reverse_net", "reverse_net_proceeds", "net"} or number_value((data or {}).get("target_net_proceeds_usd")) > 0:
        return reverse_price_from_net_proceeds(data)
    return calculate_pricing(data)
