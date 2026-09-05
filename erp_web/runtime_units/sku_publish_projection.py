"""将草稿选中的规格投影为平台单品输入；不持久化临时单品视图。"""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from erp_web.product_model.sku_model import PACKAGE_FIELDS, record, selected_skus, sku_fingerprint, text
from .publish_context import PreparedPublishContext


def target_key(context: PreparedPublishContext) -> str:
    return f"{context.platform}:{context.target.get('site') or context.draft.get('site')}".lower()


def same_number(left: Any, right: Any) -> bool:
    try:
        a, b = Decimal(str(left)), Decimal(str(right))
        return a.is_finite() and b.is_finite() and a == b
    except (InvalidOperation, ValueError):
        return False


def sku_quote_errors(fact: dict[str, Any], row: dict[str, Any], draft: dict[str, Any], key: str) -> list[str]:
    pricing = record(row.get("pricing"))
    quote = record(record(pricing.get("targets")).get(key))
    basis = record(quote.get("calculation_basis"))
    if pricing.get("applied") is not True or not basis or quote.get("errors") or quote.get("is_loss"):
        return ["请重新核价并应用此 SKU 的售价"]
    expected = {"cost_cny": fact.get("cost_cny"), **record(fact.get("package_dimensions"))}
    shared = record(record(draft.get("pricing")).get("common"))
    own = record(record(row.get("pricing_overrides")).get("common"))
    for field in ("domestic_freight_cny", "packaging_cost_cny", "other_cost_cny"):
        expected[field] = own.get(field, shared.get(field, 0))
    if shared.get("exchange_rate_mode") == "manual":
        for field in ("usd_cny_rate", "mxn_usd_rate", "rub_cny_rate"):
            if field in shared:
                expected[field] = shared[field]
    target_template = record(record(record(draft.get("pricing")).get("targets")).get(key))
    own_target = record(record(record(row.get("pricing_overrides")).get("targets")).get(key))
    for field in ("commission_percent", "payment_fee_percent", "other_fee_percent", "target_margin_percent", "markup_percent", "shipping_amount"):
        if field in target_template or field in own_target:
            expected[field] = own_target.get(field, target_template.get(field))
    for field in ("pricing_mode", "shipping_quote_mode", "shipping_currency"):
        if field in target_template:
            desired = "manual" if field == "pricing_mode" and own_target.get("manual_price") else target_template[field]
            if field == "shipping_quote_mode" and "shipping_amount" in own_target:
                desired = "manual"
            if text(desired) != text(basis.get(field)):
                return ["核价规则已变化，请重新计算并应用此 SKU 的售价"]
    manual = record(own_target.get("manual_price")) or (record(target_template.get("applied_price")) if target_template.get("pricing_mode") == "manual" else {})
    quoted_manual = record(basis.get("manual_price"))
    if manual and (not same_number(manual.get("amount"), quoted_manual.get("amount")) or text(manual.get("currency")).upper() != text(quoted_manual.get("currency")).upper()):
        return ["手动售价已变化，请重新应用核价"]
    if key.startswith("mercadolibre:"):
        target = next((item for item in draft.get("target_sites", []) if f"{item.get('platform')}:{item.get('site')}".lower() == key), {})
        def destinations(items: Any) -> set[tuple[str, str]]:
            return {(text(item.get("site_id")).upper(), text(item.get("logistic_type")).lower()) for item in items if isinstance(item, dict)} if isinstance(items, list) else set()
        if destinations(target.get("sites_to_sell")) != destinations(quote.get("sites_to_sell")):
            return ["销售国家或物流方式已变化，请重新核价并应用此 SKU 的售价"]
    changed = [field for field, value in expected.items() if not same_number(value, basis.get(field))]
    if changed:
        return ["采购成本、包装资料或费用已变化，请重新核价：" + "、".join(changed)]
    return []


def grouping_contract(context: PreparedPublishContext) -> dict[str, Any]:
    grouping = record(context.draft.get("grouping"))
    result = {"mode": grouping.get("mode", "combined"), "name": text(grouping.get("name") or context.draft.get("title")), "attribute_id": "", "variation_ids": [], "parent_ids": []}
    definition = context.category_definition
    if definition:
        attrs = (*definition.required, *definition.optional)
        result["variation_ids"] = [attr.id for attr in attrs if attr.variation_role == "variant"]
        result["parent_ids"] = [attr.id for attr in attrs if attr.variation_role == "parent"]
        if context.platform == "yandex" and definition.attribute_by_id("200"):
            result["attribute_id"] = "200"
        if context.platform == "ozon":
            # 属性编号随平台类目定义解析，不把某个类目的编号写死在发布流程中。
            result["attribute_id"] = next((attr.id for attr in attrs if attr.name.strip().casefold() == "объединить на одной карточке"), "")
    return result


def sku_context(context: PreparedPublishContext, fact: dict[str, Any], row: dict[str, Any], grouping: dict[str, Any]) -> PreparedPublishContext:
    product = deepcopy(context.product)
    draft = product["drafts"][context.platform]
    key = target_key(context)
    pricing = deepcopy(record(row.get("pricing")))
    quote = record(record(pricing.get("targets")).get(key))
    attrs = {**record(context.draft.get("attributes")), **record(record(row.get("attributes_by_target")).get(key))}
    if grouping["mode"] == "combined" and grouping["attribute_id"]:
        attrs[grouping["attribute_id"]] = grouping["name"]
    publication = record(record(row.get("publications")).get(key))
    remote = record(publication.get("result"))
    draft.update({"sku": row["sku"], "stock": row.get("stock", ""), "upc": fact.get("barcode", ""),
                  "package_dimensions": deepcopy(record(fact.get("package_dimensions"))), "pricing": pricing,
                  "attributes": attrs, "publication": deepcopy(record(remote.get("publication"))),
                  "last_publish_task": deepcopy(publication)})
    # 普通字段修改不能携带整组远端身份；每次投影只关联这一 SKU。
    for target in draft.get("target_sites", []):
        target.update({"attributes": deepcopy(attrs), "publication": deepcopy(record(remote.get("publication"))), "last_publish_task": deepcopy(publication)})
        target["listing_currency"] = quote.get("listing_currency", "")
        target["currency_fingerprint"] = quote.get("currency_fingerprint", "")
        if context.platform == "mercadolibre":
            target["sites_to_sell"] = deepcopy(quote.get("sites_to_sell", []))
    draft["listing_currency"] = quote.get("listing_currency", "")
    draft["currency_fingerprint"] = quote.get("currency_fingerprint", "")
    if context.platform == "mercadolibre":
        draft["sites_to_sell"] = deepcopy(quote.get("sites_to_sell", []))
    product["cost"] = fact.get("cost_cny", "")
    product["stock"] = row.get("stock", "")
    image = text(fact.get("image"))
    if image:
        pool = record(product.get("source")).get("image_pool", [])
        asset = next((item for item in pool if image in {text(item.get(field)) for field in ("id", "url", "path", "preview_url")}), None)
        if not asset:
            raise ValueError("SKU 图片尚未加入商品图片池，请在图片页添加该图片")
        refs = [ref for ref in draft.get("images", []) if ref.get("asset_id") != asset["id"]]
        draft["images"] = [{"asset_id": asset["id"], "role": "main", "order": 0}, *[{**ref, "role": "gallery", "order": index + 1} for index, ref in enumerate(refs)]]
    return context.with_product(product)


def validate_grouping(context: PreparedPublishContext, grouping: dict[str, Any], projections: list[PreparedPublishContext]) -> list[str]:
    if len(projections) < 2 or grouping["mode"] != "combined":
        return []
    if not grouping["name"]:
        return ["组合展示需要填写平台组名"]
    if context.platform in {"yandex", "ozon"} and not grouping["attribute_id"]:
        return ["当前类目没有提供组合属性，无法组合展示；请更换类目或明确选择独立刊登"]
    keys = grouping["variation_ids"]
    if not keys:
        return ["当前类目尚未提供可区分变体的属性，请刷新类目定义并确认规格属性"]
    combos = [sku_fingerprint({key: p.draft.get("attributes", {}).get(key) for key in keys}) for p in projections]
    if len(combos) != len(set(combos)):
        return ["多个 SKU 的平台变体属性相同，请在 SKU 页填写不同的颜色、尺寸等平台属性"]
    attrs = [record(p.draft.get("attributes")) for p in projections]
    parent_keys = set(grouping["parent_ids"])
    if context.platform == "yandex":
        parent_keys = set().union(*(set(item) for item in attrs)) - set(keys)
    for key in parent_keys:
        if len({sku_fingerprint(item.get(key)) for item in attrs}) > 1:
            return [f"组合内共同属性 {key} 不一致，请调整或选择独立刊登"]
    return []


__all__ = ["grouping_contract", "same_number", "sku_context", "sku_quote_errors", "target_key", "validate_grouping"]
