"""预检问题的唯一合并格式；生成、旧数据读取及保存共用，不保留逐 SKU 错误副本。"""

from copy import deepcopy
from typing import Any, Iterable


class PrecheckIssues:
    """边校验边合并，同一原因只保存一次，受影响对象仅保留去重身份。"""

    def __init__(self) -> None:
        self._items: dict[tuple, dict[str, Any]] = {}

    def add(self, raw: Any, *, sku_id: str = "", severity: str = "error") -> dict[str, Any]:
        source = raw if isinstance(raw, dict) else {"message": str(raw or "")}
        item = {key: str(source.get(key) or (severity if key == "severity" else ""))
                for key in ("code", "field", "message", "severity", "next_action")}
        sku_ids = list(source.get("affected_sku_ids") or [])
        sku_ids.extend(sku.get("sku_id", "") for sku in source.get("affected_skus") or [] if isinstance(sku, dict))
        if sku_id:
            sku_ids.append(sku_id)
        asset_ids = list(source.get("affected_asset_ids") or [])
        if source.get("asset_id"):
            asset_ids.append(source["asset_id"])
        # 图片身份属于影响范围，不是错误种类；属性 ID 等真实规则字段不能折叠。
        if item["field"].startswith("images."):
            asset_ids.append(item["field"].removeprefix("images."))
            item["field"] = "images"
        key = tuple(item.values()) + (bool(sku_ids),)
        # 历史人工属性复核可省略级别和建议；不要给它补出新的阻断语义。
        value = {field: value for field, value in item.items() if field in source}
        if severity == "warning":
            value["severity"] = item["severity"]
        group = self._items.setdefault(key, value)
        for field, values in (("affected_sku_ids", sku_ids), ("affected_asset_ids", asset_ids)):
            if values:
                existing = group.setdefault(field, [])
                seen = set(existing)
                for value in values:
                    if value and value not in seen:
                        existing.append(value)
                        seen.add(value)
        if source.get("related_issues"):
            related = group.setdefault("related_issues", [])
            for value in merge_precheck_issues(source["related_issues"]):
                if value not in related:
                    related.append(value)
        return group

    def extend(self, items: Iterable[Any], *, sku_id: str = "", severity: str = "error") -> None:
        for item in items:
            self.add(item, sku_id=sku_id, severity=severity)

    def values(self) -> list[dict[str, Any]]:
        return list(self._items.values())


def merge_precheck_issues(items: Iterable[Any], *, severity: str = "error") -> list[dict[str, Any]]:
    grouped = PrecheckIssues()
    grouped.extend(items, severity=severity)
    return grouped.values()


def normalize_validation_errors(items: Iterable[Any]) -> list[Any]:
    """旧草稿还用字符串标记人工属性复核；保留其读取语义和原有顺序。"""
    grouped = PrecheckIssues()
    result = []
    seen_groups = set()
    for item in items:
        if isinstance(item, dict):
            group = grouped.add(item)
            if id(group) not in seen_groups:
                result.append(group)
                seen_groups.add(id(group))
        elif item not in result:
            result.append(deepcopy(item))
    return result


def normalize_precheck(raw: Any) -> dict[str, Any]:
    """读取旧预检时合并逐 SKU 副本，新保存只使用同一份合并结果。

    不改变业务是否通过；逐 SKU 独有错误先并入顶层，不能因移除旧副本丢失阻断。
    发布执行的 sku_results 是远端回执，不能传入本函数。
    """
    if not isinstance(raw, dict) or not raw:
        return {}
    result = {key: deepcopy(value) for key, value in raw.items()
              if key not in {"errors", "warnings", "sku_results", "error_count", "warning_count"}}
    errors, warnings = PrecheckIssues(), PrecheckIssues()
    errors.extend(raw.get("errors") or [])
    warnings.extend(raw.get("warnings") or [], severity="warning")
    for row in raw.get("sku_results") or []:
        check = row.get("precheck") or {}
        errors.extend(check.get("errors") or [], sku_id=row.get("sku_id", ""))
        warnings.extend(check.get("warnings") or [], sku_id=row.get("sku_id", ""), severity="warning")
    if "errors" in raw or "sku_results" in raw:
        result["errors"] = errors.values()
    if "warnings" in raw or "sku_results" in raw:
        result["warnings"] = warnings.values()
    # 多市场预检仍按真实站点隔离，不跨市场合并原因。
    if isinstance(result.get("parent"), dict):
        result["parent"] = normalize_precheck(result["parent"])
    if isinstance(result.get("markets"), list):
        result["markets"] = [normalize_precheck(market) for market in result["markets"]]
    return result


__all__ = ["PrecheckIssues", "merge_precheck_issues", "normalize_precheck", "normalize_validation_errors"]
