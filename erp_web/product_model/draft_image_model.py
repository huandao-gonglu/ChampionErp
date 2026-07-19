from __future__ import annotations

from copy import deepcopy
from typing import Any

from .common import IMAGE_USAGES, text_or_empty
from .image_pool_model import normalize_image_pool

DRAFT_IMAGE_ROLES = ("main", "detail", "size", "scene", "package", "selling_point", "material", "other")


def default_draft_image_ref() -> dict[str, Any]:
    return {
        "asset_id": "",
        "role": "detail",
        "order": 0,
    }


def normalize_draft_image_role(value: Any, order: int = 0) -> str:
    role = text_or_empty(value).lower()
    if role in DRAFT_IMAGE_ROLES:
        return role
    usage = text_or_empty(value).lower()
    if usage in IMAGE_USAGES:
        return usage if usage in DRAFT_IMAGE_ROLES else "other"
    return "main" if order == 0 else "detail"


def normalize_draft_image_ref(item: Any, order: int = 0) -> dict[str, Any]:
    raw = item if isinstance(item, dict) else {}
    asset_id = text_or_empty(raw.get("asset_id") or raw.get("assetId") or raw.get("id"))
    if not asset_id:
        return {}
    ref = default_draft_image_ref()
    ref["asset_id"] = asset_id
    ref["role"] = normalize_draft_image_role(raw.get("role") or raw.get("usage"), order)
    try:
        ref["order"] = int(raw.get("order", order))
    except Exception:
        ref["order"] = order
    for key in ("label", "note", "alt_text", "source_asset_id"):
        value = raw.get(key)
        if value not in (None, ""):
            ref[key] = deepcopy(value)
    return ref


def normalize_draft_image_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        ref = normalize_draft_image_ref(raw, index)
        asset_id = text_or_empty(ref.get("asset_id"))
        if not asset_id or asset_id in seen:
            continue
        seen.add(asset_id)
        refs.append(ref)
    refs.sort(key=lambda item: int(item.get("order") or 0))
    for index, ref in enumerate(refs):
        ref["order"] = index
    main_seen = False
    for ref in refs:
        if ref.get("role") != "main":
            continue
        if main_seen:
            ref["role"] = "detail"
        else:
            main_seen = True
    if refs and not main_seen:
        refs[0]["role"] = "main"
    return refs


def draft_image_asset_ids(value: Any) -> list[str]:
    return [text_or_empty(ref.get("asset_id")) for ref in normalize_draft_image_refs(value) if text_or_empty(ref.get("asset_id"))]


def draft_image_refs_from_pool(product: dict[str, Any], platform: str = "") -> list[dict[str, Any]]:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    pool = normalize_image_pool(source.get("image_pool") if isinstance(source.get("image_pool"), list) else [], [], "source")
    platform_key = text_or_empty(platform).lower()
    if platform_key:
        platform_items = [
            item
            for item in pool
            if platform_key in [text_or_empty(value).lower() for value in (item.get("platforms") or [])]
            and text_or_empty(item.get("status")).lower() != "empty"
        ]
        if platform_items:
            pool = platform_items
    selected_items = [item for item in pool if bool(item.get("selected"))]
    items = selected_items or pool
    items = sorted(items, key=lambda item: (0 if item.get("is_main") else 1, int(item.get("order") or 0)))
    refs = []
    for index, item in enumerate(items):
        asset_id = text_or_empty(item.get("id") or item.get("asset_id"))
        if not asset_id:
            continue
        refs.append(
            {
                "asset_id": asset_id,
                "role": "main" if bool(item.get("is_main")) or index == 0 else normalize_draft_image_role(item.get("usage"), index),
                "order": index,
            }
        )
    return normalize_draft_image_refs(refs)


def draft_image_refs_from_assets(items: list[dict[str, Any]], start_order: int = 0) -> list[dict[str, Any]]:
    refs = []
    for offset, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        asset_id = text_or_empty(item.get("id") or item.get("asset_id"))
        if not asset_id:
            continue
        order = start_order + offset
        ref = {
            "asset_id": asset_id,
            "role": "main" if bool(item.get("is_main")) and start_order == 0 else normalize_draft_image_role(item.get("usage"), order),
            "order": order,
        }
        source_asset_id = text_or_empty(item.get("derived_from_id") or item.get("source_asset_id"))
        if source_asset_id:
            ref["source_asset_id"] = source_asset_id
        refs.append(ref)
    return normalize_draft_image_refs(refs)


def apply_created_image_refs_to_draft(
    existing_refs: Any,
    created_items: list[dict[str, Any]],
    strategy: str = "append",
) -> list[dict[str, Any]]:
    strategy = text_or_empty(strategy).lower() or "append"
    existing = normalize_draft_image_refs(existing_refs)
    created = draft_image_refs_from_assets(created_items, start_order=0 if strategy == "replace_all" else len(existing))
    if not created:
        return existing
    if strategy == "replace_all" or not existing:
        return normalize_draft_image_refs(created)
    if strategy == "replace_selected":
        created_by_source = {
            text_or_empty(ref.get("source_asset_id")): ref
            for ref in created
            if text_or_empty(ref.get("source_asset_id"))
        }
        used_created_ids: set[str] = set()
        replaced: list[dict[str, Any]] = []
        for index, ref in enumerate(existing):
            replacement = created_by_source.get(text_or_empty(ref.get("asset_id")))
            if replacement:
                next_ref = {**replacement, "role": ref.get("role") or replacement.get("role"), "order": index}
                replaced.append(next_ref)
                used_created_ids.add(text_or_empty(replacement.get("asset_id")))
            else:
                replaced.append(ref)
        for ref in created:
            if text_or_empty(ref.get("asset_id")) not in used_created_ids:
                replaced.append({**ref, "order": len(replaced)})
        return normalize_draft_image_refs(replaced)
    appended = existing + [{**ref, "order": len(existing) + index} for index, ref in enumerate(created)]
    return normalize_draft_image_refs(appended)


__all__ = [
    "DRAFT_IMAGE_ROLES",
    "apply_created_image_refs_to_draft",
    "default_draft_image_ref",
    "draft_image_asset_ids",
    "draft_image_refs_from_assets",
    "draft_image_refs_from_pool",
    "normalize_draft_image_ref",
    "normalize_draft_image_refs",
    "normalize_draft_image_role",
]
