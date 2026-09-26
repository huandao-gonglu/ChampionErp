"""SKU 预检问题的纯汇总：合并重复项、关联缺失原因并定位填写入口。"""

from copy import deepcopy
from typing import Any

from erp_web.schemas.category_definition import CategoryDefinition
from erp_web.schemas.publish_capabilities import PublishValidationIssue


def _group_issues(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in items:
        affected = item.get("affected_skus") or []
        key = tuple(item.get(field, "") for field in ("code", "field", "message", "severity", "next_action")) + (bool(affected),)
        if key not in grouped:
            grouped[key] = deepcopy(item)
            continue
        if affected:
            existing = grouped[key]["affected_skus"]
            seen = {sku["sku_id"] for sku in existing}
            for sku in affected:
                if sku["sku_id"] not in seen:
                    existing.append(deepcopy(sku))
                    seen.add(sku["sku_id"])
    return list(grouped.values())


def _describe_attribute_issue(issue: dict[str, Any], definition: CategoryDefinition | None) -> dict[str, Any]:
    item = deepcopy(issue)
    field = str(item.get("field") or "")
    attribute = definition.attribute_by_id(field.removeprefix("attributes.")) if definition and field.startswith("attributes.") else None
    if attribute is None:
        return item
    if item.get("code") == "REQUIRED_ATTRIBUTE_MISSING" and attribute.name:
        item["message"] = f"{item['message']}（{attribute.name}）"
    if attribute.variation_role == "variant":
        suggestion = str(item.get("next_action") or "")
        item["next_action"] = suggestion.replace("前往类目属性页", "前往 SKU → 属性 / 详情（当前目标市场）") or "前往 SKU → 属性 / 详情，在当前目标市场下核对并填写该属性的真实规格值。"
    return item


def summarize_sku_precheck(
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    grouping_issues: list[PublishValidationIssue],
    *,
    definition: CategoryDefinition | None,
    selected_sku_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """仅将覆盖同一批 SKU 的必填差异属性缺失与整组空值校验关联。"""
    # 先按原始原因和建议汇总，避免改写填写入口后合并本应独立的处理步骤。
    grouped_errors = [_describe_attribute_issue(item, definition) for item in _group_issues(errors)]
    grouped_warnings = [_describe_attribute_issue(item, definition) for item in _group_issues(warnings)]
    variant_fields = {
        f"attributes.{attribute.id}"
        for attribute in (*definition.required, *definition.optional)
        if attribute.variation_role == "variant"
    } if definition else set()
    for issue in grouping_issues:
        primary = None
        if issue.code == "SKU_VARIATION_ATTRIBUTES_EMPTY" and len(selected_sku_ids) > 1:
            primary = next((item for item in grouped_errors if (
                item.get("code") == "REQUIRED_ATTRIBUTE_MISSING"
                and item.get("field") in variant_fields
                and {sku["sku_id"] for sku in item.get("affected_skus") or []} == selected_sku_ids
            )), None)
        if primary is None:
            grouped_errors.append(issue.model_dump())
        else:
            primary.setdefault("related_issues", []).append(issue.model_dump(exclude={"affected_skus", "related_issues"}))
    return grouped_errors, grouped_warnings


__all__ = ["summarize_sku_precheck"]
