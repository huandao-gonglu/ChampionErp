"""公共类目属性填充的 SKU 事实范围；不写入或生成 SKU 属性。"""

from typing import Any

from erp_web.product_model.sku_model import selected_skus


def attribute_sku_scope(product: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    selected = selected_skus(product, draft)
    options = [fact.get("options") or {} for fact, _ in selected]
    keys = sorted({str(key) for item in options for key in item})
    common: dict[str, str] = {}
    varying: list[str] = []
    for key in keys:
        values = {str(item.get(key) or "").strip() for item in options}
        if len(values) == 1 and "" not in values:
            common[key] = next(iter(values))
        else:
            varying.append(key)
    return {"selected_count": len(selected), "common_options": common, "varying_option_names": varying}


def exclude_aggregate_sku_facts(context: dict[str, Any]) -> None:
    """原商品所有 SKU 的汇总字段不能代替本次所选 SKU 的共同事实。"""
    scope = context.get("sku_scope") or {}
    option_names = set(scope.get("varying_option_names") or []) | set(scope.get("common_options") or {})
    if not option_names:
        return
    for section in ("source", "product"):
        facts = context.get(section) or {}
        attributes = facts.get("attributes") or {}
        facts["attributes"] = {
            key: value for key, value in attributes.items()
            if key not in option_names
        }
        facts.pop("colors", None)


def attribute_needs_sku_scope(definition: dict[str, Any], context: dict[str, Any]) -> bool:
    scope = context.get("sku_scope") or {}
    return (
        definition.get("variation_role") == "variant"
        and int(scope.get("selected_count") or 0) > 1
    )
