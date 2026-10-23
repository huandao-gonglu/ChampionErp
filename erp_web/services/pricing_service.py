"""发布目标独立核价的纯业务逻辑，不依赖 Web 或持久化层。"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from fractions import Fraction
from pydantic import ValidationError
import re
from typing import Any

from erp_web.schemas.shipping import ShippingResolution, ShippingResolver
from erp_web.schemas.commissions import CommissionQuote, CommissionResolver
from erp_web.services.pricing_solver import PricingGoal, fraction
from erp_web.schemas.draft_pricing import ReturnLossSettings
from erp_web.services.pricing_return_loss import return_loss
from erp_web.marketplace_registry import marketplace_site
from erp_web.product_model import normalize_mercadolibre_sites_to_sell


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


def _basis_number_text(value: Any) -> str:
    """无损序列化核价输入；物理量和费率不能按货币精度舍入。"""

    try:
        number = Decimal(str(value))
    except Exception:
        return "0"
    if not number.is_finite() or number == 0:
        return "0"
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _money(amount: Any, currency: str) -> dict[str, str]:
    return {
        "amount": _amount_text(amount),
        "currency": str(currency or "").strip().upper(),
    }


def _money_input(value: Any, expected_currency: str) -> float:
    # 手动售价金额始终以店铺发布币种计（输入框币种只是快照回显）；
    # 币种留空表示跟随发布币种，非空且不一致才视为真实错配。
    if not isinstance(value, dict):
        return 0.0
    currency = str(value.get("currency") or "").strip().upper()
    if currency and currency != str(expected_currency or "").strip().upper():
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
        "payment_fee_percent": number_value(first_value(source, "payment_fee_percent", "payment_fee_rate", default=0), 0),
        "usd_cny_rate": number_value(first_value(source, "usd_cny_rate", "usd_cny", "currency_rate", "rate")),
        "mxn_usd_rate": number_value(first_value(source, "mxn_usd_rate", "mxn_rate")),
        "rub_cny_rate": number_value(first_value(source, "rub_cny_rate", "rub_rate", default=12), 12),
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
    common_base = (
        values["cost_cny"]
        + values["freight_cny"]
        + values["international_freight_cny"]
        + values["other_cost_cny"]
        + values["warehousing_cost_cny"]
        + values["advertising_cost_cny"]
        + values["other_platform_fee_cny"]
    )
    return {
        "billable_kg": billable_kg,
        "volume_weight_kg": round((values["length_cm"] * values["width_cm"] * values["height_cm"]) / 6000, 4) if values["length_cm"] and values["width_cm"] and values["height_cm"] else 0.0,
        "common_base_cny": common_base,
    }


def pricing_target_key(platform: str, site: str) -> str:
    platform_key = str(platform or "").strip().lower()
    site_key = str(site or "").strip().upper()
    return f"{platform_key}:{site_key}" if site_key else platform_key


def _pricing_basis_digest(basis: dict[str, Any]) -> str:
    payload = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_pricing_numbers(value: Any) -> Any:
    """JSON 往返会把 18.0 写成 18；统一表示，不舍入实际数值或转换字符串。"""
    if isinstance(value, dict):
        return {key: _canonical_pricing_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_pricing_numbers(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("核价依据不能包含非有限数字")
        if value.is_integer():
            return int(value)
    return value


def pricing_calculation_fingerprint(basis: dict[str, Any]) -> str:
    return "v2:" + _pricing_basis_digest(_canonical_pricing_numbers(basis))


def pricing_calculation_fingerprint_matches(basis: dict[str, Any], fingerprint: str) -> bool:
    """新写入使用 v2；读取历史指纹时保留原摘要及 Yandex 原请求数字类型。"""
    if fingerprint.startswith("v2:"):
        return fingerprint == pricing_calculation_fingerprint(basis)
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        return False
    if fingerprint == _pricing_basis_digest(basis):
        return True
    # 旧 Yandex 费用请求明确以 float 写入这五个字段。只恢复已知协议的
    # 数字类型，仍比对完整原摘要；不能因迁移而认可实际改变的报价依据。
    evidence = basis.get("commission_evidence")
    if not isinstance(evidence, dict) or evidence.get("platform") != "yandex" or evidence.get("endpoint") != "/v2/tariffs/calculate":
        return False
    restored = deepcopy(basis)
    request = restored["commission_evidence"].get("request")
    offers = request.get("offers") if isinstance(request, dict) else None
    if not isinstance(offers, list):
        return False
    for offer in offers:
        if not isinstance(offer, dict):
            return False
        for key in ("price", "length", "width", "height", "weight"):
            if type(offer.get(key)) is int:
                offer[key] = float(offer[key])
    return fingerprint == _pricing_basis_digest(restored)


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def has_official_commission(basis: dict[str, Any], *, allow_loss_quote: bool = False) -> bool:
    """历史手填报价继续可读，但不能再作为已核验的发布价格。"""
    evidence = _record(basis.get("commission_evidence"))
    if (evidence.get("source") not in {"platform_api", "official_tariff", "official_fee_page"} or evidence.get("purpose") != "prelisting" or not evidence.get("endpoint")
            or not evidence.get("quoted_at") or not _record(evidence.get("price"))):
        return False
    if evidence.get("endpoint") in {"/v5/product/info/prices", "/v3/product/info/list"}:
        return False
    if evidence.get("source") == "official_tariff" and (not evidence.get("file_sha256") or not evidence.get("file_url") or not evidence.get("effective_from")):
        return False
    if evidence.get("source") == "official_fee_page" and (not evidence.get("source_sha256") or not evidence.get("response_sha256") or not evidence.get("category_page") or not evidence.get("pricing_page")):
        return False
    if evidence["price"].get("currency") != basis.get("listing_currency") or evidence.get("currency") != basis.get("listing_currency"):
        return False
    try:
        price = Decimal(str(evidence["price"]["amount"]))
        fee = Decimal(str(evidence["amount"]))
        percent = Decimal(str(basis["commission_percent"]))
        if not all(value.is_finite() for value in (price, fee, percent)) or price <= 0 or fee < 0 or percent < 0 or (percent >= 100 and not allow_loss_quote):
            return False
        if abs(fee / price * 100 - percent) > Decimal("0.000000001"):
            return False
    except (KeyError, ValueError, ArithmeticError):
        return False
    return all(has_official_commission(_record(item.get("basis"))) for item in basis.get("destination_shipping", []) if isinstance(item, dict))


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


def _target_defaults() -> dict[str, float]:
    return {"payment_fee_percent": 0.0, "target_margin_percent": 30.0}


def _destination_pricing_modes(value: Any) -> list[dict[str, str]]:
    """规范化受信 binding 投影；金额永远不从这里继承。"""

    rows = value if isinstance(value, list) else []
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        site_id = str(raw.get("site_id") or raw.get("siteId") or "").strip().upper()
        logistic_type = str(
            raw.get("logistic_type") or raw.get("logisticType") or ""
        ).strip().lower()
        pricing_model = str(
            raw.get("pricing_model") or raw.get("pricingModel") or ""
        ).strip().lower()
        operation = (site_id, logistic_type)
        if (
            not site_id
            or not logistic_type
            or pricing_model not in {"price", "net_proceeds"}
            or operation in seen
        ):
            continue
        seen.add(operation)
        normalized.append(
            {
                "site_id": site_id,
                "logistic_type": logistic_type,
                "pricing_model": pricing_model,
            }
        )
    return sorted(
        normalized,
        key=lambda item: (item["site_id"], item["logistic_type"]),
    )


def _pricing_cost(values, shipping_amount, shipping_currency):
    fixed = sum((fraction(values[key]) for key in (
        "cost_cny", "freight_cny", "international_freight_cny", "other_cost_cny",
        "warehousing_cost_cny", "advertising_cost_cny", "other_platform_fee_cny", "prep_fee_cny",
    )), Fraction(0))
    shipping = fraction(shipping_amount)
    if shipping_currency == "USD":
        shipping *= fraction(values["usd_cny_rate"])
    return fixed + shipping + return_loss(values, shipping_amount, shipping_currency)[1]


def _pricing_currency_rate(currency, source, values):
    """求价、费用换算和目标校验共用精确汇率，避免 1/7 的浮点误差多加一分。"""
    if currency == "CNY":
        return Fraction(1)
    if currency == "RUB" and values["rub_cny_rate"] > 0:
        return fraction(values["rub_cny_rate"])
    return fraction(_currency_per_usd(currency, source, values)) / fraction(values["usd_cny_rate"])


def _result_goal(result, common, target, shipping_quote):
    basis = result["calculation_basis"]
    values = result["input"]
    rate = _pricing_currency_rate(result["listing_currency"], {**common, **target}, values)
    cost = _pricing_cost(values, basis["shipping_amount"], basis["shipping_currency"]) * rate
    retained = 1 - fraction(basis["payment_fee_percent"]) / 100 - fraction(basis["other_fee_percent"]) / 100
    if result["pricing_mode"] == "margin":
        retained -= fraction(basis["target_margin_percent"]) / 100
    elif result["pricing_mode"] == "markup":
        cost *= 1 + fraction(basis["markup_percent"]) / 100
    floor = fraction(shipping_quote["minimum_price_cny"]) * rate if shipping_quote else Fraction(0)
    return PricingGoal(retained, cost, floor)


def _calculate_target_amounts(common: dict[str, Any], target: dict[str, Any], index: int = 0, *, shipping_quote: ShippingResolution | None = None, commission_quote: CommissionQuote | None = None, evaluated_price: Decimal | None = None) -> dict[str, Any]:
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
    defaults = _target_defaults()
    # 零费率仅用于内部求解的初始候选价；没有官方回执的候选不能作为成功结果。
    commission_percent = float(commission_quote["percent"]) if commission_quote else 0.0
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
            "payment_fee_percent": payment_fee_percent,
            "other_fee_percent": other_fee_percent,
            "target_margin_percent": target_margin_percent,
            "margin_percent": target_margin_percent,
        }
    )
    values = normalize_pricing_input(source)
    base = _base_values(values)
    errors: list[dict[str, str]] = []
    try:
        returns = ReturnLossSettings.model_validate({key: source[key] for key in ReturnLossSettings.model_fields if source.get(key) is not None})
    except ValidationError as exc:
        labels = {"return_rate_percent": "预计退货率必须不小于 0 且小于 100%", "return_disposition": "退货处理方式必须为弃货或退回再售", "return_handling_cost_cny": "每次退货额外费用必须是非负有限金额"}
        errors.extend({"field": str(error["loc"][0]), "message": labels[str(error["loc"][0])]} for error in exc.errors())
        returns = ReturnLossSettings()
    values.update(returns.model_dump())
    destination_pricing_modes = _destination_pricing_modes(
        target.get("destination_pricing_modes")
        if isinstance(target.get("destination_pricing_modes"), list)
        else target.get("destinationPricingModes")
    )
    if platform == "mercadolibre" and site.upper() == "CBT":
        selected_operations = {
            (item["site_id"], item["logistic_type"])
            for item in normalize_mercadolibre_sites_to_sell(
                target.get("sites_to_sell")
                if isinstance(target.get("sites_to_sell"), list)
                else target.get("sitesToSell")
            )
        }
        mode_operations = {
            (item["site_id"], item["logistic_type"])
            for item in destination_pricing_modes
        }
        if not selected_operations or mode_operations != selected_operations:
            errors.append(
                {
                    "field": "destination_pricing_modes",
                    "message": "CBT 销售目标缺少与账号 binding 一致的计价模式",
                }
            )
        elif len(
            {item["pricing_model"] for item in destination_pricing_modes}
        ) != 1:
            errors.append(
                {
                    "field": "destination_pricing_modes",
                    "message": "同一次 CBT 核价不能混用 price 与 net_proceeds",
                }
            )
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
    if target.get("shipping_selection") and (platform != "yandex" or shipping_mode != "auto"):
        errors.append({"field": "shipping_selection", "message": "指定仓库物流方式目前仅适用于 Yandex 自动运费"})
    raw_shipping_currency = str(_target_value(target, source, "shipping_currency", "shippingCurrency", default="") or "").strip().upper()
    shipping_currency = raw_shipping_currency if raw_shipping_currency in {"USD", "CNY"} else ("USD" if platform == "mercadolibre" else "CNY")
    shipping_amount = _target_number(target, source, "shipping_amount", "shippingAmount", default=0)

    shipping_usd = 0.0
    shipping_cny = 0.0
    shipping_source = "manual_quote"
    if shipping_mode == "auto":
        shipping_source = "international_shipping"
        if shipping_quote is None:
            errors.append({"field": "shipping_amount", "message": "自动物流报价尚未获取，请通过核价入口重新计算"})
        elif shipping_quote.get("error"):
            errors.append({"field": "shipping_amount", "message": shipping_quote["error"]})
        else:
            shipping_currency = shipping_quote["currency"]
            shipping_amount = float(shipping_quote["amount"])
            base["billable_kg"] = float(shipping_quote["billable_g"]) / 1000
            volume_g = shipping_quote["evidence"].get("volume_weight_g")
            base["volume_weight_kg"] = float(volume_g) / 1000 if volume_g is not None else None
    if shipping_mode != "auto" or (shipping_quote is not None and not shipping_quote.get("error")):
        if shipping_amount < 0 or not math.isfinite(shipping_amount) or (shipping_mode != "auto" and shipping_amount == 0):
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
    single_return_loss, return_loss_reserve = return_loss(values, shipping_amount, shipping_currency)
    total_cost_cny = fixed_cost_cny + shipping_cny + float(return_loss_reserve)

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
    if fee_denominator <= 0 and evaluated_price is None:
        errors.append({"field": "platform_fee_percent", "message": "平台佣金、支付手续费和其他平台费用合计必须小于 100%"})
    margin = target_margin_percent / 100
    if pricing_mode == "margin":
        if not math.isfinite(target_margin_percent) or target_margin_percent < 0 or target_margin_percent >= 100:
            errors.append({"field": "target_margin_percent", "message": "目标销售利润率必须在 0% 到 100% 之间"})
        elif fee_rate + margin >= 1 and evaluated_price is None:
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
        if evaluated_price is not None:
            revenue_cny = float(evaluated_price) / currency_rate_cny
        elif pricing_mode == "margin":
            revenue_cny = total_cost_cny / (1 - fee_rate - margin)
        elif pricing_mode == "markup":
            revenue_cny = total_cost_cny * (1 + markup_percent / 100) / fee_denominator
        else:
            revenue_cny = applied_price_input / currency_rate_cny
    if not errors and pricing_mode != "manual" and shipping_quote:
        revenue_cny = max(revenue_cny, float(shipping_quote["minimum_price_cny"]))
    suggested_price = revenue_cny * currency_rate_cny if revenue_cny > 0 and currency_rate_cny > 0 else 0.0
    if not errors and pricing_mode != "manual" and shipping_quote:
        floor_price = (Decimal(shipping_quote["minimum_price_cny"]) * Decimal(str(currency_rate_cny))).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        if Decimal(_amount_text(suggested_price)) < floor_price:
            suggested_price = float(floor_price)
            revenue_cny = suggested_price / currency_rate_cny
    if not errors:
        suggested_price = float(evaluated_price) if evaluated_price is not None else float(
            Decimal(str(suggested_price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP if pricing_mode == "manual" else ROUND_CEILING)
        )
        revenue_cny = suggested_price / currency_rate_cny
    suggested_price_usd = revenue_cny / values["usd_cny_rate"] if revenue_cny > 0 and values["usd_cny_rate"] > 0 else 0.0
    applied_price = (
        float(_amount_text(applied_price_input))
        if pricing_mode == "manual" and applied_price_input > 0 and not errors
        else suggested_price
    )
    actual_revenue_cny = applied_price / currency_rate_cny if applied_price > 0 and currency_rate_cny > 0 else 0.0
    commission_cny = actual_revenue_cny * commission
    payment_fee_cny = actual_revenue_cny * payment_fee
    other_fee_cny = actual_revenue_cny * other_fee
    profit_cny = actual_revenue_cny - commission_cny - payment_fee_cny - other_fee_cny - total_cost_cny if not errors else 0.0
    if not errors and commission_quote and evaluated_price is not None:
        # 最终利润用真正发布的分价与该价格的官方费用计算，不使用试算费率反推。
        exact_rate = _pricing_currency_rate(currency, source, values)
        revenue = fraction(evaluated_price) / exact_rate
        fee = fraction(commission_quote["evidence"]["amount"]) / exact_rate
        payment = revenue * fraction(payment_fee_percent) / 100
        other = revenue * fraction(other_fee_percent) / 100
        cost = _pricing_cost(values, shipping_amount, shipping_currency)
        actual_revenue_cny, commission_cny = float(revenue), float(fee)
        payment_fee_cny, other_fee_cny = float(payment), float(other)
        total_cost_cny = float(cost)
        profit_cny = float(revenue - fee - payment - other - cost)
    net_revenue_cny = actual_revenue_cny - commission_cny - payment_fee_cny - other_fee_cny
    seller_net_proceeds_cny = (
        net_revenue_cny - shipping_cny if not errors else 0.0
    )
    applied_net_proceeds = (
        seller_net_proceeds_cny * currency_rate_cny
        if seller_net_proceeds_cny > 0 and currency_rate_cny > 0
        else 0.0
    )
    profit_percent = (profit_cny / actual_revenue_cny * 100) if actual_revenue_cny else 0.0
    minimum_revenue_cny = total_cost_cny / fee_denominator if total_cost_cny > 0 and fee_denominator > 0 else 0.0
    minimum_price = minimum_revenue_cny * currency_rate_cny if currency_rate_cny > 0 else 0.0
    key = str(_target_value(target, source, "target_key", "targetKey", default=pricing_target_key(platform, site)) or "").strip() or pricing_target_key(platform, site)
    calculation_basis = {
        "platform": platform,
        "site": site,
        "listing_currency": currency,
        "currency_fingerprint": str(target.get("currency_fingerprint") or "").strip(),
        "cost_cny": _basis_number_text(values["cost_cny"]),
        "domestic_freight_cny": _basis_number_text(values["freight_cny"]),
        "packaging_cost_cny": _basis_number_text(packaging_cost_cny),
        "other_cost_cny": _basis_number_text(values["other_cost_cny"]),
        "weight_kg": _basis_number_text(values["weight_kg"]),
        "battery": source.get("battery", False),
        "liquid": source.get("liquid", False),
        "length_cm": _basis_number_text(values["length_cm"]),
        "width_cm": _basis_number_text(values["width_cm"]),
        "height_cm": _basis_number_text(values["height_cm"]),
        "usd_cny_rate": _basis_number_text(values["usd_cny_rate"]),
        "mxn_usd_rate": _basis_number_text(values["mxn_usd_rate"]),
        "rub_cny_rate": _basis_number_text(values["rub_cny_rate"]),
        "commission_percent": _basis_number_text(commission_percent),
        "payment_fee_percent": _basis_number_text(payment_fee_percent),
        "other_fee_percent": _basis_number_text(other_fee_percent),
        "pricing_mode": pricing_mode,
        "target_margin_percent": _basis_number_text(target_margin_percent),
        "markup_percent": _basis_number_text(markup_percent),
        "return_rate_percent": _basis_number_text(returns.return_rate_percent),
        "return_disposition": returns.return_disposition,
        "return_handling_cost_cny": _basis_number_text(returns.return_handling_cost_cny),
        "shipping_quote_mode": shipping_mode,
        "shipping_currency": shipping_currency,
        "shipping_amount": _basis_number_text(shipping_amount),
        "manual_price": _money(applied_price_input, currency) if pricing_mode == "manual" else None,
    }
    if target.get("shipping_selection"):
        calculation_basis["shipping_selection"] = target["shipping_selection"]
    if shipping_quote and not shipping_quote.get("error"):
        calculation_basis["shipping_evidence"] = shipping_quote["evidence"]
    if commission_quote:
        calculation_basis["commission_evidence"] = commission_quote["evidence"]
    if platform == "mercadolibre" and site.upper() == "CBT":
        calculation_basis["listing_model"] = str(
            target.get("listing_model") or ""
        ).strip()
        calculation_basis["sites_to_sell"] = (
            normalize_mercadolibre_sites_to_sell(
                target.get("sites_to_sell")
                if isinstance(target.get("sites_to_sell"), list)
                else target.get("sitesToSell")
            )
        )
        calculation_basis["destination_pricing_modes"] = destination_pricing_modes
    calculation_fingerprint = pricing_calculation_fingerprint(calculation_basis)
    exposes_net_proceeds = any(
        destination["pricing_model"] == "net_proceeds"
        for destination in destination_pricing_modes
    )
    destination_results: list[dict[str, Any]] = []
    for destination in destination_pricing_modes:
        destination_uses_net_proceeds = (
            destination["pricing_model"] == "net_proceeds"
        )
        destination_results.append(
            {
                **destination,
                "price": (
                    None
                    if destination_uses_net_proceeds
                    else _money(applied_price, currency)
                ),
                "net_proceeds": (
                    _money(applied_net_proceeds, currency)
                    if destination_uses_net_proceeds
                    else None
                ),
                "calculation_fingerprint": calculation_fingerprint,
            }
        )
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
        "applied_net_proceeds": (
            _money(applied_net_proceeds, currency)
            if exposes_net_proceeds
            else None
        ),
        "destination_results": destination_results,
        "minimum_price": _money(minimum_price, currency),
        "converted_prices": {
            "CNY": _amount_text(revenue_cny),
            "USD": _amount_text(suggested_price_usd),
        },
        "calculation_basis": calculation_basis,
        "calculation_fingerprint": calculation_fingerprint,
        "shipping_cost_usd": round(shipping_usd, 2),
        "shipping_cost_cny": round(shipping_cny, 2),
        "shipping_quote_mode": shipping_mode,
        "shipping_currency": shipping_currency,
        "shipping_amount": round(shipping_amount, 2),
        "shipping_source": shipping_source,
        "shipping_candidates": shipping_quote["candidates"] if shipping_quote else [],
        "total_cost_cny": round(total_cost_cny, 2),
        "return_loss_cny": round(float(return_loss_reserve), 2),
        "return_loss": {
            **returns.model_dump(),
            "single_loss_cny": round(float(single_return_loss), 2),
            "allocated_loss_cny": round(float(return_loss_reserve), 2),
            "source": "local_estimate",
        },
        "net_revenue_cny": round(net_revenue_cny, 2),
        "seller_net_proceeds_cny": round(seller_net_proceeds_cny, 2),
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
            "return_loss_cny": round(float(return_loss_reserve), 2),
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


def calculate_target_pricing(common: dict[str, Any], target: dict[str, Any], index: int = 0, *, shipping_quote: ShippingResolution | None = None, commission_resolver: CommissionResolver | None = None) -> dict[str, Any]:
    """官方费用与最终舍入售价校准；手填佣金只作为历史数据读取，不参与计算。"""
    result = _calculate_target_amounts(common, target, index, shipping_quote=shipping_quote)
    if not result["ok"]:
        return result
    try:
        if commission_resolver is None:
            raise ValueError("尚未取得官方佣金，请通过核价入口重新计算")
        goal = _result_goal(result, common, target, shipping_quote)
        manual = result["pricing_mode"] == "manual"
        price = dict(result["applied_price"])
        if not manual:
            price["amount"] = _amount_text(goal.proportional_price(Fraction(0)))
        seen = set()
        for _ in range(16):
            quote = commission_resolver(common, target, price)
            if quote.get("evidence", {}).get("source") == "local_calculation" and "rule" in quote:
                if not manual:
                    price = {**price, "amount": _amount_text(goal.solve_rule(quote["rule"]))}
                # 缓存规则只能省掉试算；最终售价仍须本轮官方批量回执。
                quote = commission_resolver(common, target, price, force_official=True)
            identity = (price["amount"], price["currency"])
            if identity in seen:
                raise ValueError("官方佣金与售价在价格档位间反复变化，未能确认有效报价")
            seen.add(identity)
            percent = Decimal(quote["percent"])
            evidence = quote["evidence"]
            evidence_price = _record(evidence.get("price"))
            basis = {**result["calculation_basis"], "commission_percent": str(percent), "commission_evidence": evidence}
            # 高额固定费可能超过初始候选价，不能误认为所有更高售价都无解。
            if (not has_official_commission(basis, allow_loss_quote=True)
                    or evidence_price.get("currency") != price["currency"]
                    or Decimal(str(evidence_price.get("amount"))) != Decimal(price["amount"])):
                raise ValueError("官方佣金金额或查询售价与回执不一致")
            amount = Decimal(price["amount"])
            meets_goal = goal.meets(amount, evidence["amount"])
            remainder = goal.retained * fraction(amount) - fraction(evidence["amount"]) - goal.required
            if manual or (meets_goal and remainder <= Fraction(1, 100)):
                # 未给出完整规则的回执，也允许保留已达标且误差仅在原币一分内的分价。
                # 这是取整后的真实利润校验，不再比较显示用的两位小数利润率。
                candidate = amount
            elif "rule" in quote:
                candidate = goal.solve_rule(quote["rule"])
            else:
                # 没有明确规则时只能查询候选的真实费用，不能把有效费率伪装成规则缓存。
                candidate = goal.proportional_price(fraction(percent) / 100)
            if candidate == amount:
                if not manual and not meets_goal:
                    raise ValueError("取整后的售价未达到定价利润目标，请重新获取费用规则")
                return _calculate_target_amounts(
                    common, target, index, shipping_quote=shipping_quote,
                    commission_quote=quote, evaluated_price=amount,
                )
            price = {**price, "amount": _amount_text(candidate)}
        raise ValueError("官方佣金与售价在 16 轮内未收敛，请调整定价条件后重试")
    except (ValueError, ArithmeticError, KeyError, TypeError) as exc:
        # 丢弃尚未收敛的售价与费用，不能把初始零费率或上轮试算结果留作可应用报价。
        error = {"field": "commission_percent", "code": "OFFICIAL_COMMISSION_UNAVAILABLE", "message": str(exc)}
        failed = _calculate_target_amounts(common, target, index, shipping_quote=shipping_quote)
        failed.update(ok=False, errors=[error], precheck_errors=[error], destination_results=[])
        for field in ("applied_price", "suggested_price", "minimum_price", "applied_net_proceeds"):
            if isinstance(failed.get(field), dict):
                failed[field] = _money(0, failed["listing_currency"])
        failed.update(profit_cny=0, profit_usd=0, margin_percent=0, net_revenue_cny=0, seller_net_proceeds_cny=0)
        failed["converted_prices"] = {"CNY": "0.00", "USD": "0.00"}
        return failed


def _calculate_with_shipping(common: dict, target: dict, index: int, resolver: ShippingResolver | None, commissions: CommissionResolver | None) -> dict:
    platform = str(target.get("platform") or "").lower()
    mode = target.get("shipping_quote_mode") or ("auto" if platform == "mercadolibre" else "manual")
    destinations = _destination_pricing_modes(target.get("destination_pricing_modes"))
    def calculate(selected):
        shipping = resolver(common, selected) if mode == "auto" and resolver is not None else None
        return calculate_target_pricing(common, selected, index, shipping_quote=shipping, commission_resolver=commissions)
    if platform != "mercadolibre" or str(target.get("site", "")).upper() != "CBT" or len(destinations) <= 1:
        return calculate(target)
    # 国家不是可互相替代的物流渠道；每个销售目标单独报价、计算售价。
    results = []
    for destination in destinations:
        operation = (destination["site_id"], destination["logistic_type"])
        selected = [item for item in normalize_mercadolibre_sites_to_sell(target.get("sites_to_sell", target.get("sitesToSell"))) if (item.get("site_id"), item.get("logistic_type")) == operation]
        child = {**target, "sites_to_sell": selected, "destination_pricing_modes": [destination]}
        results.append(calculate(child))
    result = dict(results[0])
    basis = dict(result["calculation_basis"])
    basis["sites_to_sell"] = normalize_mercadolibre_sites_to_sell(target.get("sites_to_sell", target.get("sitesToSell")))
    basis["destination_pricing_modes"] = destinations
    basis["destination_shipping"] = [
        {"site_id": destination["site_id"], "logistic_type": destination["logistic_type"], "basis": item["calculation_basis"]}
        for destination, item in zip(destinations, results)
    ]
    fingerprint = pricing_calculation_fingerprint(basis)
    result.update({
        "ok": all(item["ok"] for item in results),
        "errors": [error for item in results for error in item["errors"]],
        "is_loss": any(item["is_loss"] for item in results),
        "calculation_basis": basis, "calculation_fingerprint": fingerprint,
        "destination_results": [
            {**entry, "calculation_fingerprint": fingerprint, "shipping_amount": item["shipping_amount"], "shipping_currency": item["shipping_currency"],
             "commission_percent": item["commission_percent"], "commission_cny": item["commission_cny"]}
            for item in results for entry in item["destination_results"]
        ],
        "shipping_candidates": [candidate for item in results for candidate in item["shipping_candidates"]],
    })
    result["precheck_errors"] = result["errors"]
    return result


def calculate_pricing_batch(data: dict[str, Any], *, shipping_resolver: ShippingResolver | None = None, commission_resolver: CommissionResolver | None = None) -> dict[str, Any]:
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
    results = [_calculate_with_shipping(common_source, _record(target), index, shipping_resolver, commission_resolver) for index, target in enumerate(targets)]
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
                "applied_net_proceeds": primary.get("applied_net_proceeds"),
                "destination_results": primary.get("destination_results"),
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


def pricing_result(data: dict[str, Any], *, shipping_resolver: ShippingResolver | None = None, commission_resolver: CommissionResolver | None = None) -> dict[str, Any]:
    return calculate_pricing_batch(data, shipping_resolver=shipping_resolver, commission_resolver=commission_resolver)
