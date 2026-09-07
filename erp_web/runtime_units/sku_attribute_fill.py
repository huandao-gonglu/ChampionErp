"""逐 SKU 填写平台属性：限定事实与写入范围，复用公共属性的 Agent 和校验。"""

from copy import deepcopy
from typing import Any, Callable

from erp_web.product_model.sku_model import effective_sku, record, editable_selected_skus, sku_fingerprint, text
from erp_web.schemas.category import category_attribute_schema, category_attribute_value_is_valid
from erp_web.schemas.category_grouping import is_listing_grouping_attribute


def fill_sku_attributes(
    product: dict[str, Any], platform: str, category_record: dict[str, Any],
    sku_id: str, *, filler: Callable[..., tuple[dict[str, Any], dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """只补指定 SKU 的空值；临时单规格投影绝不作为整份商品保存。"""
    updated = deepcopy(product)
    draft = updated["drafts"][platform]
    key = f"{platform}:{draft.get('site', '')}".lower()
    row = next((item for item in draft.get("sku_items", []) if item["sku_id"] == sku_id), None)
    source = next((item for item in product.get("sku_items", []) if item["id"] == sku_id), None)
    if row is None or source is None or not source.get("active", True):
        raise ValueError("指定 SKU 不存在或已停用，请重新选择。")
    if text(category_record.get("category_id")) != text(draft.get("category_id")):
        raise ValueError("类目已变化，请重新加载当前类目的属性。")
    own = deepcopy(record(record(row.get("attributes_by_target")).get(key)))
    common = record(draft.get("attributes"))
    definitions = [item for item in category_attribute_schema(category_record)
                   if item.get("variation_role") == "variant" and not item.get("read_only")
                   and not is_listing_grouping_attribute(platform, item)]
    if not definitions:
        return updated, {"source": "rules", "sku_id": sku_id, "ai_filled": [], "need_review": [],
                         "warning": "当前类目未提供 SKU 差异属性，请先核对类目与发布组织方式。"}
    # 已填值即使无效也交由人工修改，不让自动填写覆盖用户的决定。
    missing = [item for item in definitions if not text(own.get(item["id"], common.get(item["id"])))]
    # 已有有效差异值时，AI 只补必填项；可选空值不构成发布错误。
    def signature(candidate):
        values = {**common, **record(record(candidate.get("attributes_by_target")).get(key))}
        return sku_fingerprint({item["id"]: values.get(item["id"]) for item in definitions})
    has_values = any(category_attribute_value_is_valid(item, own.get(item["id"], common.get(item["id"]))) for item in definitions)
    unique = all(other["sku_id"] == sku_id or signature(other) != signature(row)
                 for _, other in editable_selected_skus(product, draft))
    if has_values and unique:
        missing = [item for item in missing if item.get("required")]
    projected = deepcopy(product)
    fact = effective_sku(source, row)
    projected["sku_items"] = [fact]
    projected_draft = projected["drafts"][platform]
    # 单 SKU 的临时事实不能带上其他平台整组 SKU 引用。
    projected["drafts"] = {platform: projected_draft}
    projected_draft["sku_items"] = [{**deepcopy(row), "selected": True}]
    projected_draft["attributes"] = {**deepcopy(common), **own}
    projected_draft["package_dimensions"] = deepcopy(fact.get("package_dimensions") or {})
    for target in projected_draft.get("target_sites", []):
        target["attributes"] = deepcopy(projected_draft["attributes"])
    scoped_record = {**deepcopy(category_record), "attributes": {
        "required": [item for item in missing if item.get("required")],
        "optional": [item for item in missing if not item.get("required")],
    }}
    # SKU 来源选项经现有 sku_scope.common_options 提供证据，不拆字符串猜平台值。
    filled, meta = filler(projected, platform, scoped_record) if missing else (projected, {"source": "rules"})
    suggestions = record(filled.get("drafts", {}).get(platform, {}).get("attributes"))
    accepted = {item["id"]: deepcopy(suggestions[item["id"]]) for item in missing
                if category_attribute_value_is_valid(item, suggestions.get(item["id"]))}
    row.setdefault("attributes_by_target", {})[key] = {**own, **accepted}
    effective = {**common, **own, **accepted}
    unresolved = [item["id"] for item in definitions
                  if (item.get("required") or text(effective.get(item["id"])))
                  and not category_attribute_value_is_valid(item, effective.get(item["id"]))]
    return updated, {**meta, "sku_id": sku_id, "ai_filled": sorted(accepted), "need_review": unresolved}


__all__ = ["fill_sku_attributes"]
