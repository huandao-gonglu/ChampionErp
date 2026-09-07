"""将草稿选中的规格投影为平台单品输入；不持久化临时单品视图。"""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from erp_web.schemas.category import category_attribute_value_is_valid
from erp_web.schemas.publish_capabilities import PublishRelatedIssue
from erp_web.schemas.sku_custom_attributes import custom_attribute_entries, custom_attribute_key
from erp_web.schemas.category_grouping import (
    apply_listing_grouping_attributes, is_listing_grouping_attribute, listing_group_name,
)
from erp_web.product_model.sku_model import PACKAGE_FIELDS, record, selected_skus, sku_fingerprint, text
from erp_web.product_model.sku_image_model import sku_image_asset
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
    result = {"mode": grouping.get("mode", "combined"), "name": listing_group_name(context.draft), "attribute_id": "", "variation_ids": [], "parent_ids": []}
    definition = context.category_definition
    if definition:
        attrs = (*definition.required, *definition.optional)
        result["variation_ids"] = [attr.id for attr in attrs if attr.variation_role == "variant"]
        result["parent_ids"] = [attr.id for attr in attrs if attr.variation_role == "parent"]
        result["attribute_id"] = next((
            attr.id for attr in attrs
            if is_listing_grouping_attribute(context.platform, {"id": attr.id, "name": attr.name})
        ), "")
    return result


def sku_context(context: PreparedPublishContext, fact: dict[str, Any], row: dict[str, Any], grouping: dict[str, Any]) -> PreparedPublishContext:
    product = deepcopy(context.product)
    draft = product["drafts"][context.platform]
    key = target_key(context)
    pricing = deepcopy(record(row.get("pricing")))
    quote = record(record(pricing.get("targets")).get(key))
    attrs = {**record(context.draft.get("attributes")), **record(record(row.get("attributes_by_target")).get(key))}
    definition = context.category_definition
    if definition:
        attrs = apply_listing_grouping_attributes(
            attrs, context.draft, context.platform,
            [attr.model_dump() for attr in (*definition.required, *definition.optional)],
            seller_sku=row.get("sku", ""),
        )
    reviews = deepcopy(context.draft.get("validation_errors") or [])
    if definition:
        variants = {attr.id: attr.model_dump() for attr in (*definition.required, *definition.optional)
                    if attr.variation_role == "variant"}
        def still_needs_review(item):
            field = text(item.get("field")) if isinstance(item, dict) and item.get("code") == "NEED_REVIEW_ATTRIBUTES" else text(item) if isinstance(item, str) else ""
            attr_id = field.removeprefix("attributes.")
            return attr_id not in variants or not category_attribute_value_is_valid(variants[attr_id], attrs.get(attr_id))
        # 公共页留下的旧缺失提示不能在每个已补齐的 SKU 上继续重复出现。
        reviews = [item for item in reviews if still_needs_review(item)]
    publication = record(record(row.get("publications")).get(key))
    remote = record(publication.get("result"))
    draft.update({"sku": row["sku"], "stock": row.get("stock", ""), "upc": fact.get("barcode", ""),
                  "package_dimensions": deepcopy(record(fact.get("package_dimensions"))), "pricing": pricing,
                  "sku_custom_attributes": deepcopy(record(row.get("custom_attributes_by_target")).get(key, [])),
                  "attributes": attrs, "validation_errors": reviews, "publication": deepcopy(record(remote.get("publication"))),
                  "last_publish_task": deepcopy(publication)})
    # 普通字段修改不能携带整组远端身份；每次投影只关联这一 SKU。
    for target in draft.get("target_sites", []):
        target.update({"attributes": deepcopy(attrs), "validation_errors": deepcopy(reviews), "publication": deepcopy(record(remote.get("publication"))), "last_publish_task": deepcopy(publication)})
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
    asset = sku_image_asset(product, fact)
    if asset:
        refs = [ref for ref in draft.get("images", []) if ref.get("asset_id") != asset["id"]]
        draft["images"] = [{"asset_id": asset["id"], "role": "main", "order": 0}, *[{**ref, "role": "detail" if ref.get("role") == "main" else ref.get("role", "detail"), "order": index + 1} for index, ref in enumerate(refs)]]
    return context.with_product(product)


def _variant_value(value: Any) -> Any:
    """比较平台值身份，枚举展示文本与集合顺序不构成规格差异。"""
    if isinstance(value, dict):
        if "values" in value:
            values = [_variant_value(item) for item in value.get("values") or []]
            values = sorted((item for item in values if item is not None), key=str)
            return values[0] if len(values) == 1 else values or None
        if text(value.get("dictionary_value_id")):
            return ("dictionary", text(value["dictionary_value_id"]))
        raw = _variant_value(value.get("value"))
        unit = text(value.get("unit"))
        return (raw, unit) if raw is not None and unit else raw
    return text(value) or None


def _grouping_issue(message: str, code: str = "SKU_GROUPING_INVALID") -> PublishRelatedIssue:
    return PublishRelatedIssue(
        code=code,
        field="sku_items",
        message=message,
        severity="error",
        next_action="前往 SKU → 属性 / 详情，填写真实规格差异；若类目无法表达这些差异，请调整类目或刊登方式。",
    )


def validate_grouping(context: PreparedPublishContext, grouping: dict[str, Any], projections: list[PreparedPublishContext]) -> list[PublishRelatedIssue]:
    if len(projections) < 2 or grouping["mode"] != "combined":
        return []
    if not grouping["name"]:
        return [_grouping_issue("组合展示需要填写平台组名")]
    if context.platform in {"yandex", "ozon"} and not grouping["attribute_id"]:
        return [_grouping_issue("当前类目没有提供组合属性，无法组合展示；请更换类目或明确选择独立刊登")]
    keys = grouping["variation_ids"]
    custom_values = []
    if context.platform == "mercadolibre":
        definition = context.category_definition
        definitions = (*definition.required, *definition.optional) if definition else ()
        for projection in projections:
            entries, errors = custom_attribute_entries(projection.draft.get("sku_custom_attributes"), definitions)
            if errors:
                return [_grouping_issue(message) for message in errors]
            custom_values.append({"custom:" + custom_attribute_key(item["name"]): item["values"][0]["name"] for item in entries})
        if len({tuple(sorted(item)) for item in custom_values}) > 1:
            return [_grouping_issue("组合内所有 SKU 必须使用相同的自定义属性名称，并分别填写真实规格值")]
    if not keys and not any(custom_values):
        return [_grouping_issue("当前类目尚未提供可区分变体的属性，请刷新类目定义并确认规格属性")]
    values = [{key: _variant_value(p.draft.get("attributes", {}).get(key)) for key in keys} for p in projections]
    for value, custom in zip(values, custom_values):
        value.update(custom)
    definition = context.category_definition
    fields = "、".join(f"{definition.attribute_by_id(key).name}（{key}）" if definition and definition.attribute_by_id(key) else key for key in keys)
    if custom_values and custom_values[0]:
        fields = "、".join(filter(None, [fields, *[key.removeprefix("custom:") for key in custom_values[0]]]))
    if all(all(value is None for value in item.values()) for item in values):
        return [_grouping_issue(
            f"所选 {len(projections)} 个 SKU 的平台差异属性全部为空，当前无法区分组合内的规格。可用于区分规格的字段：{fields}。请填写必填项及真实存在的差异，无需填满所有可选字段；补齐后仍需检查规格组合是否重复。",
            "SKU_VARIATION_ATTRIBUTES_EMPTY",
        )]
    combos = [sku_fingerprint(item) for item in values]
    if len(combos) != len(set(combos)):
        groups: dict[str, list[str]] = {}
        names = {fact["id"]: text(fact.get("name")) for fact in context.product.get("sku_items", [])}
        sellers = {text(row.get("sku")): names.get(row["sku_id"], row["sku_id"]) for row in context.draft.get("sku_items", [])}
        for combo, projection in zip(combos, projections):
            seller = text(projection.draft.get("sku"))
            groups.setdefault(combo, []).append(sellers.get(seller) or seller)
        duplicates = "；".join("、".join(names) for names in groups.values() if len(names) > 1)
        return [_grouping_issue(
            f"以下 SKU 的平台属性组合相同：{duplicates}。可区分字段：{fields}。请填写真实差异；若该类目无法表达这些差异，请调整组合方式。",
            "SKU_VARIATION_COMBINATION_DUPLICATE",
        )]
    attrs = [record(p.draft.get("attributes")) for p in projections]
    parent_keys = set(grouping["parent_ids"])
    if context.platform == "yandex":
        parent_keys = set().union(*(set(item) for item in attrs)) - set(keys)
    for key in parent_keys:
        if len({sku_fingerprint(item.get(key)) for item in attrs}) > 1:
            return [_grouping_issue(f"组合内共同属性 {key} 不一致，请调整或选择独立刊登")]
    return []


__all__ = ["grouping_contract", "same_number", "sku_context", "sku_quote_errors", "target_key", "validate_grouping"]
