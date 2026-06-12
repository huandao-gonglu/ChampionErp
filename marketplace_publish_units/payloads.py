from __future__ import annotations

from .common import *

from .category_cache import *
from .config_http import *
from .category_services import *

def required_wildberries_characteristics(subject_id: str, token: str) -> list[dict[str, Any]]:
    if not subject_id:
        return []
    data = request_json(
        "GET",
        f"https://content-api.wildberries.ru/content/v2/object/charcs/{subject_id}?locale=ru",
        token,
    )
    chars = data.get("data", []) if isinstance(data, dict) else []
    return [
        {
            "charcID": item.get("charcID"),
            "name": item.get("name"),
            "type": item.get("charcType") or item.get("type"),
            "required": item.get("required", False),
            "unitName": item.get("unitName"),
        }
        for item in chars
        if item.get("required")
    ]


def listing_for(plan: dict[str, Any], platform_key: str) -> dict[str, Any]:
    return plan["platforms"].get(platform_key, {}).get("listing", {})


def _format_number_unit(number: float, unit: str) -> str:
    if float(number).is_integer():
        value = str(int(number))
    else:
        value = str(round(number, 2)).rstrip("0").rstrip(".")
    return f"{value} {unit}"


def _normalize_mercadolibre_sale_terms(sale_terms: Any, is_global_selling: bool) -> list[dict[str, Any]]:
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
                term["value_name"] = "Seller warranty" if is_global_selling else "Garantía del vendedor"
            elif "fábrica" in value or "fabrica" in value or "factory" in value:
                term["value_id"] = "2230279"
                term["value_name"] = "Factory warranty" if is_global_selling else "Garantía de fábrica"
            elif "sin" in value or "no warranty" in value or "no garantía" in value:
                term["value_id"] = "6150835"
                term["value_name"] = "No warranty" if is_global_selling else "Sin garantía"
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
                unit = "months" if is_global_selling else "meses"
            elif "año" in raw_unit or "ano" in raw_unit or "year" in raw_unit:
                unit = "years" if is_global_selling else "años"
            else:
                unit = "days" if is_global_selling else "días"
            if unit in {"días", "days"}:
                number = max(3, round(number / 30))
                unit = "months" if is_global_selling else "meses"
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
) -> dict[str, Any]:
    listing = listing_for(plan, "mercadolibre")
    store = config["mercadolibre"]
    settings = config["listing"]
    mxn_rate = number_or_zero(settings.get("mxn_usd_rate")) or 18.0
    price_input = number_or_zero(settings.get("mercadolibre_price") or settings.get("price"))
    currency_id = str(settings.get("currency_id") or "MXN").upper()
    price_usd = price_input if currency_id == "USD" else round(price_input / mxn_rate, 2)
    logistic_type = str(settings.get("mercadolibre_logistic_type") or "remote").strip() or "remote"
    sku = settings.get("sku") or product.get("name") or "SKU-1"
    site_id = str(store.get("site_id") or "MLM").strip().upper() or "MLM"
    account_site_id = str(store.get("account_site_id") or store.get("user_site_id") or "").strip().upper()
    category_id = product.get("category_id") or store.get("category_id")
    category_id = str(category_id or "").strip()
    is_global_selling = account_site_id == "CBT" or category_id.startswith("CBT")
    if is_global_selling and not category_id.startswith("CBT"):
        match = re.search(r"(\d+)$", category_id)
        if match:
            category_id = f"CBT{match.group(1)}"
    attributes = [
        {"id": "BRAND", "value_name": product.get("brand") or "Generic"},
        {"id": "SELLER_SKU", "value_name": sku},
    ]
    model = settings.get("model") or product.get("model") or product.get("name") or sku
    if model:
        attributes.append({"id": "MODEL", "value_name": str(model)[:255]})
    if product.get("colors"):
        attributes.append({"id": "COLOR", "value_name": product["colors"][0]})
    dims = [
        float(x.replace(",", "."))
        for x in re.findall(r"\d+(?:[,.]\d+)?", str(product.get("dimensions", "")))
    ]
    length, width, height = (dims + [1, 1, 1])[:3]
    weight_kg = number_or_zero(product.get("weight_kg")) or 0.1
    length = number_or_zero(settings.get("package_length_cm")) or length
    width = number_or_zero(settings.get("package_width_cm")) or width
    height = number_or_zero(settings.get("package_height_cm")) or height
    weight_kg = number_or_zero(settings.get("package_weight_kg")) or weight_kg
    package_length = max(1, round(length, 1))
    package_width = max(1, round(width, 1))
    package_height = max(1, round(height, 1))
    package_weight = max(10, int(round(weight_kg * 1000)))
    title = (
        str(settings.get("mercadolibre_title") or "").strip()
        or str(listing.get("title") or "").strip()
        or str(product.get("name") or "").strip()
    )[:60]
    upc = str(settings.get("upc") or product.get("upc") or "").strip()
    if upc:
        attributes.append({"id": "GTIN", "value_name": upc})
    else:
        attributes.append({"id": "EMPTY_GTIN_REASON", "value_name": "The product does not have a registered code"})
    package_attr_prefix = "PACKAGE" if is_global_selling else "SELLER_PACKAGE"
    attributes.extend(
        [
            {"id": f"{package_attr_prefix}_LENGTH", "value_name": f"{package_length} cm"},
            {"id": f"{package_attr_prefix}_WIDTH", "value_name": f"{package_width} cm"},
            {"id": f"{package_attr_prefix}_HEIGHT", "value_name": f"{package_height} cm"},
            {"id": f"{package_attr_prefix}_WEIGHT", "value_name": f"{package_weight} g"},
        ]
    )
    extra_attributes = settings.get("mercadolibre_attributes") or {}
    if isinstance(extra_attributes, dict):
        for attr_id, value in extra_attributes.items():
            attr_id = str(attr_id or "").strip()
            if attr_id.startswith(("PACKAGE_", "SELLER_PACKAGE_")):
                continue
            value = str(value or "").strip()
            if attr_id and value:
                attributes.append({"id": attr_id, "value_name": value})
    product_attributes = product.get("attributes") or {}
    if isinstance(product_attributes, dict):
        for attr_id, value in product_attributes.items():
            attr_id = str(attr_id or "").strip()
            if attr_id.startswith(("PACKAGE_", "SELLER_PACKAGE_")):
                continue
            value = str(value or "").strip()
            if attr_id and value:
                attributes.append({"id": attr_id, "value_name": value})
    if is_global_selling:
        attributes.append({"id": "ITEM_CONDITION", "value_id": "2230284", "value_name": "New"})
        attributes = [
            attr
            for attr in attributes
            if str(attr.get("id") or "") not in {"CONDITION"}
        ]
    deduped: dict[str, dict[str, Any]] = {}
    for attr in attributes:
        attr_id = str(attr.get("id") or "").strip()
        if attr_id and attr.get("value_name"):
            deduped[attr_id] = attr
    attributes = list(deduped.values())
    pictures = [
        {"id": url.split(":", 1)[1]} if str(url).startswith("ml-id:") else {"source": url}
        for url in image_urls
        if url
    ]
    commission_rate = number_or_zero(settings.get("mercadolibre_commission_percent")) / 100
    shipping_usd = number_or_zero(settings.get("ml_shipping_usd"))
    net_proceeds = number_or_zero(settings.get("mercadolibre_net_proceeds_usd"))
    if not net_proceeds:
        net_proceeds = max(0.01, round(price_usd * (1 - commission_rate) - shipping_usd, 2))
    sale_terms = settings.get("mercadolibre_sale_terms")
    if not isinstance(sale_terms, list) or not sale_terms:
        sale_terms = [
            {
                "id": "WARRANTY_TYPE",
                "name": "Warranty type",
                "value_id": "6150835" if not is_global_selling else "",
                "value_name": "Sin garantía" if not is_global_selling else "No warranty",
            },
        ]
    sale_terms = _normalize_mercadolibre_sale_terms(sale_terms, is_global_selling)

    site_entry: dict[str, Any] = {
        "site_id": site_id if site_id != "CBT" else "MLM",
        "logistic_type": logistic_type,
        "price": price_usd,
        "listing_type_id": settings.get("listing_type_id") or "gold_special",
        "title": title,
    }

    payload = {
        "_global_selling": is_global_selling,
        "title": title,
        "category_id": category_id,
        "price": price_usd,
        "currency_id": "USD",
        "available_quantity": int(settings.get("stock") or 1),
        "buying_mode": "buy_it_now",
        "catalog_listing": False,
        "listing_type_id": settings.get("listing_type_id") or "gold_special",
        "condition": settings.get("condition") or "new",
        "attributes": attributes,
        "sale_terms": sale_terms,
        "description": {"plain_text": listing.get("description", "")},
    }
    if is_global_selling:
        payload["sites_to_sell"] = [site_entry]
        payload.pop("condition", None)
    if pictures:
        payload["pictures"] = pictures
    return payload


def build_wildberries_payload(
    product: dict[str, Any],
    plan: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    listing = listing_for(plan, "wildberries")
    store = config["wildberries"]
    settings = config["listing"]
    sku = settings.get("sku") or product.get("name") or "SKU-1"
    chars = []
    if product.get("brand"):
        chars.append({"id": 0, "name": "Бренд", "value": product.get("brand")})
    if product.get("colors"):
        chars.append({"id": 0, "name": "Цвет", "value": product["colors"][0]})
    dims = [float(x.replace(",", ".")) for x in __import__("re").findall(r"\d+(?:[,.]\d+)?", str(product.get("dimensions", "")))]
    length, width, height = (dims + [1, 1, 1])[:3]
    weight = float(str(product.get("weight_kg") or "0.1").replace(",", ".") or 0.1)

    return [
        {
            "subjectID": int(store.get("subject_id") or 0),
            "variants": [
                {
                    "vendorCode": sku,
                    "title": listing.get("title") or product.get("name"),
                    "description": listing.get("description", ""),
                    "brand": product.get("brand") or "Нет бренда",
                    "dimensions": {
                        "length": int(round(length)),
                        "width": int(round(width)),
                        "height": int(round(height)),
                        "weightBrutto": max(0.01, round(weight, 3)),
                    },
                    "characteristics": chars,
                    "sizes": [{"techSize": "0", "wbSize": "", "price": int(float(settings.get("wildberries_price") or settings.get("price") or 0)), "skus": [sku]}],
                }
            ],
        }
    ]


def build_ozon_payload(product: dict[str, Any], plan: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    listing = listing_for(plan, "wildberries") or listing_for(plan, "mercadolibre")
    settings = config["listing"]
    return {
        "items": [
            {
                "name": listing.get("title") or product.get("name"),
                "description": listing.get("description", ""),
                "category_id": config.get("ozon", {}).get("category_id", ""),
                "offer_id": settings.get("sku") or product.get("name") or "SKU-1",
                "price": settings.get("ozon_price") or "",
                "currency_code": "RUB",
                "vat": "0",
                "attributes": [],
                "images": [],
            }
        ]
    }
