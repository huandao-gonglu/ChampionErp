from __future__ import annotations

"""草稿保存前的 Ozon 类目对（type_id ↔ description_category_id）解析。

HTTP facade 与 ``draft_save`` Capability 共用同一实现，保证两条入口的
类目解析行为完全一致。
"""

from typing import Any

from erp_web.runtime_units.category_store import fetch_category_record


def _text(value: Any) -> str:
    return str(value or "").strip()


def resolve_ozon_category_pair(target: dict[str, Any]) -> dict[str, Any]:
    """把 Ozon 目标的类目 ID 解析为实时有效的类目对。"""

    resolved = dict(target)
    type_id = _text(resolved.get("category_id") or resolved.get("type_id"))
    if not type_id:
        resolved["description_category_id"] = ""
        return resolved
    record = fetch_category_record(
        "ozon",
        type_id,
        site=_text(resolved.get("site")),
        include_attributes=True,
    )
    resolved_type_id = _text(record.get("type_id") or record.get("category_id"))
    description_category_id = _text(record.get("description_category_id"))
    if resolved_type_id != type_id or not description_category_id:
        raise ValueError("Ozon 类目 ID 无效或已下架，请重新选择实时类目")
    resolved["category_id"] = resolved_type_id
    resolved["description_category_id"] = description_category_id
    return resolved


def resolve_draft_category_pairs(draft: dict[str, Any]) -> dict[str, Any]:
    """解析草稿全部 Ozon 目标市场的类目对。"""

    resolved = dict(draft)
    raw_targets = (
        resolved.get("target_sites")
        if isinstance(resolved.get("target_sites"), list)
        else resolved.get("targetSites")
    )
    targets: list[dict[str, Any]] = []
    for raw_target in raw_targets if isinstance(raw_targets, list) else []:
        target = dict(raw_target) if isinstance(raw_target, dict) else {}
        if _text(target.get("platform")).lower() == "ozon":
            target = resolve_ozon_category_pair(target)
        targets.append(target)
    if targets:
        resolved["target_sites"] = targets
    elif _text(resolved.get("platform")).lower() == "ozon":
        resolved = resolve_ozon_category_pair(resolved)
    return resolved


__all__ = [
    "resolve_draft_category_pairs",
    "resolve_ozon_category_pair",
]
