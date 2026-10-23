"""商品事实的唯一归属及旧草稿副本迁移；不修改历史报价或发布回执。"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any


SKU_SOURCE_FIELDS = frozenset({"name", "options", "cost_cny", "supplier_stock", "barcode", "package_dimensions", "battery", "liquid"})


def _identity(value: Any) -> str:
    try:
        return str(Decimal(str(value)).normalize())
    except InvalidOperation:
        return str(value)


def clean_draft_facts(draft: dict) -> dict:
    result = deepcopy(draft)
    for field in ("brand", "model", "upc", "package_dimensions", "cost", "cost_cny", "battery", "liquid", "materials", "package_includes", "supplier_stock", "options"):
        result.pop(field, None)
    common = (result.get("pricing") or {}).get("common") or {}
    for field in ("battery", "liquid"):
        common.pop(field, None)
    for row in result.get("sku_items", []):
        row["overrides"] = {key: value for key, value in (row.get("overrides") or {}).items() if key == "image_asset_id"}
    return result


def validate_draft_facts(draft: dict) -> None:
    if {"brand", "model"}.intersection(draft):
        raise ValueError("品牌和型号由商品维护，请修改商品资料。")
    if {"battery", "liquid"}.intersection((draft.get("pricing") or {}).get("common") or {}):
        raise ValueError("电池和液体属性由商品 SKU 维护，请修改商品资料。")
    for row in draft.get("sku_items", []):
        fields = set(row.get("overrides") or {}) - {"image_asset_id"}
        if fields:
            raise ValueError("草稿不能覆盖商品资料，请修改商品 SKU：" + "、".join(sorted(fields)))


def migrate_source_facts(product: dict, drafts: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    """商品已有值优先；空值只接收一致的草稿值，冲突副本删除并列入报告。"""
    if product.get("schema_version") not in (4, 5):
        raise ValueError("不支持迁移该商品版本，未修改数据。")
    result = deepcopy(product)
    facts = {row["id"]: row for row in result.get("sku_items", [])}
    candidates: dict[tuple, list] = {}
    report = []
    for draft in drafts:
        for field in ("brand", "model"):
            value = draft.get(field)
            if value not in (None, "", "Generic", "General"):
                candidates.setdefault(("", field, ""), []).append(value)
        common = (draft.get("pricing") or {}).get("common") or {}
        for row in draft.get("sku_items", []):
            sku_id = row.get("sku_id")
            if sku_id not in facts:
                continue
            values = {**{key: common[key] for key in ("battery", "liquid") if key in common}, **(row.get("overrides") or {})}
            for field in SKU_SOURCE_FIELDS.intersection(values):
                value = values[field]
                parts = value.items() if isinstance(value, dict) else [("", value)]
                for part, value in parts:
                    if value not in (None, ""):
                        candidates.setdefault((sku_id, field, part), []).append(value)
    for (sku_id, field, part), values in candidates.items():
        owner = facts[sku_id] if sku_id else result
        container = owner.setdefault(field, {}) if part else owner
        key = part or field
        current = container.get(key)
        if current not in (None, ""):
            action = "保留商品值" if any(_identity(current) != _identity(v) for v in values) else "删除相同副本"
        elif len({_identity(v) for v in values}) == 1:
            container[key] = deepcopy(values[0])
            action = "补充商品空值"
        else:
            action = "删除冲突草稿值，商品保持为空"
        report.append({"sku_id": sku_id, "field": f"{field}.{part}" if part else field, "action": action})
    legacy_cost = result.pop("cost", None)
    if legacy_cost not in (None, ""):
        result.setdefault("source", {}).setdefault("legacy_product_cost_cny", legacy_cost)
    result["schema_version"] = 5
    return result, [clean_draft_facts(draft) for draft in drafts], report


def invalidate_source_changes(previous: dict, current: dict, draft: dict) -> dict:
    """来源变化停用受影响的报价和预检；历史计算依据和远端记录保持原样。"""
    before = {row["id"]: row for row in previous.get("sku_items", [])}
    after = {row["id"]: row for row in current.get("sku_items", [])}
    shared_changed = any(previous.get(key) != current.get(key) for key in ("brand", "model", "materials", "package_includes", "attributes"))
    result = deepcopy(draft)
    changed = False
    review_ids = set()
    for row in result.get("sku_items", []):
        old, new = before.get(row["sku_id"], {}), after.get(row["sku_id"], {})
        pricing_changed = any(old.get(key) != new.get(key) for key in ("cost_cny", "package_dimensions", "battery", "liquid"))
        source_changed = any(old.get(key) != new.get(key) for key in ("name", "options", "barcode", "package_dimensions", "battery", "liquid", "active"))
        if pricing_changed and row.get("pricing"):
            row["pricing"]["applied"] = False
        if (source_changed or shared_changed) and row.get("selected"):
            review_ids.add(row["sku_id"])
        changed |= row.get("selected", False) and (pricing_changed or source_changed or shared_changed)
    if changed:
        for target in [result, *result.get("target_sites", [])]:
            target["last_precheck"] = {}
            target["last_precheck_target"] = {}
            target["category_precheck"] = {}
            if review_ids:
                key = f"{target.get('platform', '')}:{target.get('site', '')}".lower()
                attributes = set(target.get("attributes") or {})
                for row in result.get("sku_items", []):
                    if row["sku_id"] in review_ids:
                        attributes.update((row.get("attributes_by_target") or {}).get(key) or {})
                issues = target.setdefault("validation_errors", [])
                present = {item.get("field") for item in issues if isinstance(item, dict)}
                issues.extend({"code": "NEED_REVIEW_ATTRIBUTES", "field": f"attributes.{field}",
                    "message": "商品资料已变化，请核对对应的平台属性。"}
                    for field in sorted(attributes) if f"attributes.{field}" not in present)
    return result


__all__ = ["SKU_SOURCE_FIELDS", "clean_draft_facts", "validate_draft_facts", "migrate_source_facts", "invalidate_source_changes"]
