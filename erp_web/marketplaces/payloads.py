from __future__ import annotations

import re
from typing import Any

from erp_web.services.mercadolibre_target_contract import (
    MERCADOLIBRE_CBT_CURRENCY_INVALID,
    mercadolibre_global_target_contract,
    mercadolibre_target_pricing_mode,
)
from erp_web.services.mercadolibre_listing_model import (
    MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS,
    MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS,
    require_mercadolibre_listing_model,
)

from .config_http import number_or_zero

def listing_for(plan: dict[str, Any], platform_key: str) -> dict[str, Any]:
    return plan["platforms"].get(platform_key, {}).get("listing", {})


def _format_number_unit(number: float, unit: str) -> str:
    if float(number).is_integer():
        value = str(int(number))
    else:
        value = str(round(number, 2)).rstrip("0").rstrip(".")
    return f"{value} {unit}"


def _normalize_mercadolibre_sale_terms(sale_terms: Any) -> list[dict[str, Any]]:
    if not isinstance(sale_terms, list):
        return []
    normalized: list[dict[str, Any]] = []
    warranty_type = ""
    for raw in sale_terms:
        if not isinstance(raw, dict):
            continue
        term = dict(raw)
        term_id = str(term.get("id") or "").strip()
        if term_id == "WARRANTY_TYPE":
            value = str(term.get("value_name") or term.get("name") or "").strip().lower()
            if "vendedor" in value or "seller" in value:
                term["value_id"] = "2230280"
                term["value_name"] = "Seller warranty"
            elif "fábrica" in value or "fabrica" in value or "factory" in value:
                term["value_id"] = "2230279"
                term["value_name"] = "Factory warranty"
            elif "sin" in value or "no warranty" in value or "no garantía" in value:
                term["value_id"] = "6150835"
                term["value_name"] = "No warranty"
            warranty_type = str(term.get("value_name") or "").strip().lower()
            normalized.append(term)
            continue
        if term_id == "WARRANTY_TIME":
            struct = term.get("value_struct") if isinstance(term.get("value_struct"), dict) else {}
            raw_number = struct.get("number")
            if raw_number in (None, ""):
                match = re.search(r"\d+(?:[,.]\d+)?", str(term.get("value_name") or ""))
                raw_number = match.group(0) if match else ""
            number = number_or_zero(raw_number)
            if number <= 0 or "sin garantía" in warranty_type or "no warranty" in warranty_type:
                continue
            raw_unit = str(struct.get("unit") or term.get("value_name") or "").strip().lower()
            if "mes" in raw_unit or "month" in raw_unit:
                unit = "months"
            elif "año" in raw_unit or "ano" in raw_unit or "year" in raw_unit:
                unit = "years"
            else:
                unit = "days"
            if unit == "days":
                number = max(3, round(number / 30))
                unit = "months"
            term["value_name"] = _format_number_unit(number, unit)
            term["value_struct"] = {"number": int(number) if float(number).is_integer() else number, "unit": unit}
            normalized.append(term)
            continue
        normalized.append(term)
    return normalized


def build_mercadolibre_payload(
    product: dict[str, Any],
    plan: dict[str, Any],
    config: dict[str, Any],
    image_urls: list[str],
    *,
    category_attributes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    listing = listing_for(plan, "mercadolibre")
    store = config["mercadolibre"]
    settings = config["listing"]
    price_input = number_or_zero(settings.get("mercadolibre_price") or settings.get("price"))
    currency_id = str(settings.get("currency_id") or "").upper()
    if not currency_id:
        raise RuntimeError("Mercado Libre 发布币种尚未解析。")
    site_id = str(store.get("site_id") or "CBT").strip().upper()
    account_site_id = str(store.get("account_site_id") or "").strip().upper()
    listing_model = require_mercadolibre_listing_model(store.get("listing_model"))
    if site_id != "CBT" or (account_site_id and account_site_id != "CBT"):
        raise RuntimeError(
            "MERCADOLIBRE_CBT_ACCOUNT_REQUIRED: Mercado Libre 只允许从 CBT "
            "Global Selling 父账号创建全局刊登"
        )
    if (
        listing_model == MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS
        and store.get("user_product_seller") is not True
    ):
        raise RuntimeError(
            "MERCADOLIBRE_USER_PRODUCTS_REQUIRED: 当前账号未开通 User Products"
        )
    # 类目身份已经由上层从当前 Mercado 草稿写入 store 副本。商品根字段属于
    # 来源商品，不能在平台 payload 边界覆盖当前草稿类目。
    category_id = store.get("category_id")
    category_id = str(category_id or "").strip()
    category_id_upper = category_id.upper()
    if not category_id_upper.startswith("CBT"):
        raise RuntimeError("CBT 发布必须使用真实 CBT 类目 ID，请在草稿的类目/属性里重新实时选择 CBT 类目。")
    if currency_id != "USD":
        raise RuntimeError(
            f"{MERCADOLIBRE_CBT_CURRENCY_INVALID}: "
            "标准 CBT Global Selling 刊登币种必须为 USD"
        )
    global_targets, target_issues = mercadolibre_global_target_contract(
        settings.get("mercadolibre_sites_to_sell"),
        store.get("marketplace_bindings"),
        listing_model=listing_model,
        require_user_products=(
            listing_model == MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS
        ),
        language=str(settings.get("mercadolibre_language") or "").strip(),
    )
    if target_issues:
        issue = target_issues[0]
        raise RuntimeError(f"{issue['code']}: {issue['message']}")
    pricing_modes = {
        mercadolibre_target_pricing_mode(
            target,
            store.get("marketplace_bindings"),
        )
        for target in global_targets
    }
    uses_net_proceeds = pricing_modes == {"net_proceeds"}
    if uses_net_proceeds and any(
        target.get("net_proceeds") in (None, "")
        for target in global_targets
    ):
        raise RuntimeError(
            "MERCADOLIBRE_PRICING_AMOUNT_REQUIRED: "
            "net_proceeds 计价必须先核价并应用每个市场的期望到账额"
        )
    if uses_net_proceeds and any(
        number_or_zero(
            (
                target["net_proceeds"].get("amount")
                if isinstance(target.get("net_proceeds"), dict)
                else target.get("net_proceeds")
            )
        )
        <= 0
        for target in global_targets
    ):
        raise RuntimeError(
            "MERCADOLIBRE_PRICING_AMOUNT_REQUIRED: "
            "每个市场的 net_proceeds 必须大于 0"
        )
    if category_attributes is None:
        raise RuntimeError(
            "MERCADOLIBRE_CATEGORY_DEFINITION_REQUIRED: "
            "Mercado Libre payload 必须先按当前类目定义编译属性"
        )
    attributes = [dict(item) for item in category_attributes]
    localized_title = str(settings.get("mercadolibre_title") or "").strip()
    global_title = str(settings.get("mercadolibre_global_title") or "").strip()
    pictures = [
        {"id": str(url).split(":", 1)[1].strip()}
        for url in image_urls
        if str(url).startswith("ml-id:")
        and str(url).split(":", 1)[1].strip()
    ]
    sale_terms = settings.get("mercadolibre_sale_terms")
    # 保修条款只来自当前平台草稿投影。空值必须保持为空并由预检/最终
    # payload 校验阻断，不能静默合成一个用户从未确认的“No warranty”。
    if not isinstance(sale_terms, list):
        sale_terms = []
    sale_terms = _normalize_mercadolibre_sale_terms(sale_terms)

    sites_to_sell: list[dict[str, Any]] = []
    default_listing_type_id = settings.get("listing_type_id") or "gold_special"
    for target in global_targets:
        site_payload: dict[str, Any] = {
            "site_id": target["site_id"],
            "logistic_type": target["logistic_type"],
        }
        # Mercado 的 sales conditions 属于具体 marketplace。显式配置优先；
        # 未配置时才继承草稿的全局默认值。
        if uses_net_proceeds:
            raw_net_proceeds = target["net_proceeds"]
            site_payload["net_proceeds"] = number_or_zero(
                raw_net_proceeds.get("amount")
                if isinstance(raw_net_proceeds, dict)
                else raw_net_proceeds
            )
        else:
            raw_price = (
                target.get("price")
                if target.get("price") not in (None, "")
                else price_input
            )
            site_payload["price"] = number_or_zero(
                raw_price.get("amount")
                if isinstance(raw_price, dict)
                else raw_price
            )
            if site_payload["price"] <= 0:
                raise RuntimeError(
                    "MERCADOLIBRE_PRICING_AMOUNT_REQUIRED: "
                    f"销售目标 {target['site_id']} 的 price 必须大于 0"
                )
        site_payload["listing_type_id"] = (
            target.get("listing_type_id") or default_listing_type_id
        )
        if target.get("status") not in (None, ""):
            site_payload["status"] = target["status"]
        if target.get("free_shipping") not in (None, ""):
            site_payload["free_shipping"] = bool(target["free_shipping"])
        if isinstance(target.get("sale_terms"), list):
            site_payload["sale_terms"] = target["sale_terms"]
        if listing_model == MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS:
            site_payload["title"] = localized_title
        sites_to_sell.append(site_payload)

    if listing_model == MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS:
        payload = {
            "_listing_model": listing_model,
            "family_name": localized_title,
            "category_id": category_id,
            "currency_id": currency_id,
            "available_quantity": int(settings.get("stock") or 1),
            "attributes": attributes,
            "sale_terms": sale_terms,
            "description": {"plain_text": listing.get("description", "")},
            "sites_to_sell": sites_to_sell,
        }
        if uses_net_proceeds:
            payload["global_net_proceeds"] = sites_to_sell[0]["net_proceeds"]
        else:
            payload["price"] = price_input
    else:
        payload = {
            "_listing_model": listing_model,
            "title": global_title,
            "category_id": category_id,
            "currency_id": currency_id,
            "available_quantity": int(settings.get("stock") or 1),
            "buying_mode": "buy_it_now",
            "catalog_listing": False,
            "listing_type_id": default_listing_type_id,
            "sites_to_sell": sites_to_sell,
            "attributes": attributes,
            "sale_terms": sale_terms,
            "description": {"plain_text": listing.get("description", "")},
        }
        if not uses_net_proceeds:
            payload["price"] = price_input
    # 当前传统 /global/items 的真实校验会在根级 pictures 缺失时返回
    # body.required_fields（cause_id=5141）。User Products 同样把图片作为
    # Siteless 商品信息放在根级；两种模型都只提交已上传的 picture ID。
    if pictures:
        payload["pictures"] = pictures
    return payload
