# -*- coding: utf-8 -*-
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from typing import Any

from erp_web import listing_planner as generator
from erp_web.context import get_context
from erp_web.marketplace_registry import (
    default_marketplace_site,
    platform_preset_key,
    platform_title_limit,
)
from erp_web.product_model import PLATFORMS
from erp_web.services import copy_service

from .image_pool_core import _source_only_pool_items, _source_pool_items

def list_presets() -> dict[str, Any]:
    return generator.load_json(get_context().paths.config_dir / "presets" / "platforms.json")


def platform_to_preset_key(platform: str) -> str:
    return platform_preset_key(platform)


def build_plan_for_platform(product: dict[str, Any], platform: str) -> dict[str, Any]:
    presets = list_presets()
    preset_key = platform_to_preset_key(platform)
    keys = [preset_key]
    platforms = [generator.PlatformPlan(key=key, preset=presets[key]) for key in keys if key in presets]
    if not platforms:
        raise RuntimeError("平台预设缺失，无法生成计划")
    plan = generator.build_plan(product, platforms)
    overrides = product.get("listing_overrides", {})
    if isinstance(overrides, dict):
        for platform_key, override in overrides.items():
            if platform_key not in plan.get("platforms", {}):
                continue
            if not isinstance(override, dict):
                continue
            listing = plan["platforms"][platform_key].get("listing", {})
            if not isinstance(listing, dict):
                continue
            for field in ["title", "description", "alt_titles", "search_keywords", "language"]:
                value = override.get(field)
                if value:
                    listing[field] = value
    return apply_product_drafts_to_plan(product, plan)


def build_copy_preview(product: dict[str, Any], platform: str, app_cfg: dict[str, Any]) -> dict[str, Any]:
    plan = apply_product_drafts_to_plan(product, build_plan_for_platform(product, platform))
    warning = ""
    key = platform_to_preset_key(platform)
    listing = plan.get("platforms", {}).get(key, {}).get("listing", {})
    try:
        result = copy_service.generate_copy(
            str(get_context().paths.app_dir),
            product,
            app_cfg,
            target_market=key,
            language=str(listing.get("language") or ""),
            mode="preview",
        )
        warning = str(result.get("error") or "")
        if result.get("ok") and isinstance(result.get("copy"), dict):
            listing.update(result["copy"])
    except Exception as exc:
        warning = str(exc)
    listing["title"] = str(listing.get("title") or product.get("name") or "")[
        : platform_title_limit(platform)
    ]
    return {"plan": plan, "listing": listing, "warning": warning}


def generate_ai_copy_bundle(
    product: dict[str, Any],
    source_platform: str,
    target_market: str,
    language: str,
    mode: str,
    app_cfg: dict[str, Any],
    *,
    app_dir: str | Path | None = None,
) -> dict[str, Any]:
    source_key = platform_to_preset_key(source_platform)
    result = copy_service.generate_copy(
        str(app_dir or get_context().paths.app_dir),
        product,
        app_cfg,
        target_market=(target_market or source_key),
        language=language,
        mode=mode,
    )
    result["source_platform"] = source_key
    return result


def save_copy_result(
    product: dict[str, Any],
    target_market: str,
    copy: dict[str, Any],
) -> dict[str, Any]:
    target_key = (target_market or "").strip().lower() or "mercadolibre"
    return get_context().products.save_draft_copy_result(
        product,
        target_key,
        copy,
    )


_BATCH_COPY_MAX_WORKERS = 16


def _resolve_item_language(
    requested_language: str,
    product: dict[str, Any],
    platform: str,
) -> str:
    if requested_language:
        return requested_language
    drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
    draft = drafts.get(platform) if isinstance(drafts.get(platform), dict) else {}
    draft_language = str(draft.get("language") or "").strip()
    return (
        draft_language
        or str(default_marketplace_site(platform).get("language") or "").strip()
        or "English"
    )


def batch_generate_copy_for_products(
    product_ids: list[str] | tuple[str, ...] = (),
    platform: str = "mercadolibre",
    language: str = "",
    mode: str = "rewrite",
    *,
    draft_ids: list[str] | tuple[str, ...] | None = None,
    include_products: bool = True,
    include_index: bool = True,
    max_workers: int = _BATCH_COPY_MAX_WORKERS,
) -> dict[str, Any]:
    normalized_product_ids = [str(item or "").strip() for item in (product_ids or ()) if str(item or "").strip()]
    normalized_draft_ids = [str(item or "").strip() for item in (draft_ids or ()) if str(item or "").strip()]
    if normalized_product_ids and normalized_draft_ids:
        return {
            "ok": False,
            "success_count": 0,
            "failed_count": 0,
            "items": [],
            "error": "product_ids 与 draft_ids 不能同时传入",
        }
    by_draft = bool(normalized_draft_ids)
    requested_platform = str(platform or "").strip().lower()
    if requested_platform:
        if requested_platform not in PLATFORMS:
            return {"ok": False, "success_count": 0, "failed_count": 0, "items": [], "error": "不支持的平台"}
        default_platform = requested_platform
    else:
        default_platform = "" if by_draft else "mercadolibre"
    requested_language = str(language or "").strip()
    app_cfg = get_context().config.load_app_config()
    target_ids = normalized_draft_ids if by_draft else normalized_product_ids

    def _generate_one(target_id: str) -> dict[str, Any]:
        row: dict[str, Any] = {
            "product_id": "" if by_draft else target_id,
            "draft_id": target_id if by_draft else "",
            "platform": default_platform,
            "ok": False,
            "status": "failed",
            "title": "",
            "description_length": 0,
            "warning": "",
            "error": "",
        }
        try:
            products_store = get_context().products
            if by_draft:
                product = products_store.find_draft_from_index(target_id)
                loaded_draft_id = str(product.get("current_draft_id") or "").strip()
                if not product or loaded_draft_id != target_id:
                    raise RuntimeError("草稿不存在")
                row["product_id"] = str(product.get("product_id") or "").strip()
                row["draft_id"] = loaded_draft_id
                draft_platform = str(product.get("current_draft_platform") or "").strip().lower()
                if requested_platform and draft_platform and draft_platform != requested_platform:
                    raise RuntimeError(
                        f"草稿属于 {draft_platform} 平台，与请求的 {requested_platform} 不匹配"
                    )
                item_platform = requested_platform or draft_platform or "mercadolibre"
                if item_platform not in PLATFORMS:
                    raise RuntimeError(f"不支持的平台：{item_platform}")
                row["platform"] = item_platform
            else:
                product = products_store.find_indexed_product(target_id, "")
                if not product or str(product.get("product_id") or "").strip() != target_id:
                    raise RuntimeError(
                        "商品不存在；请确认传入的是 product_id（按草稿批量生成请传 draft_ids）"
                    )
                item_platform = default_platform
            item_language = _resolve_item_language(requested_language, product, item_platform)
            source_platform = str((product.get("source") or {}).get("source_platform") or item_platform)
            result = generate_ai_copy_bundle(
                product,
                source_platform,
                item_platform,
                item_language,
                mode,
                app_cfg,
            )
            if not result.get("ok"):
                if result.get("error_code"):
                    row["error_code"] = str(result.get("error_code") or "")
                if isinstance(result.get("error_details"), dict) and result.get("error_details"):
                    row["error_details"] = dict(result["error_details"])
                raise RuntimeError(str(result.get("error") or "本地化文案生成失败"))
            copy_payload = {
                **(result.get("copy") or {}),
                "language": result.get("language", item_language),
                "source_platform": result.get("source_platform", source_platform),
                "mode": result.get("mode", mode),
            }
            saved = save_copy_result(product, result.get("target_market") or item_platform, copy_payload)
            draft = ((saved.get("drafts") or {}).get(item_platform) or {}) if isinstance(saved.get("drafts"), dict) else {}
            row.update(
                {
                    "ok": True,
                    "product_id": str(saved.get("product_id") or row["product_id"]),
                    "draft_id": str(saved.get("current_draft_id") or draft.get("draft_id") or row["draft_id"]),
                    "status": draft.get("status") or "copy_ready",
                    "title": draft.get("title") or "",
                    "description_length": len(str(draft.get("description") or "")),
                    "warning": "",
                }
            )
            if include_products:
                row["product"] = saved
        except Exception as exc:
            row["error"] = str(exc)
        return row

    worker_count = min(max(1, int(max_workers or 1)), len(target_ids))
    # 互不重叠的目标并发生成文案（复制 ContextVar 保持当前账号空间隔离），保留原始顺序。
    if worker_count > 1 and len(set(target_ids)) == len(target_ids):
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(copy_context().run, _generate_one, tid) for tid in target_ids]
            items = [future.result() for future in futures]
    else:
        items = [_generate_one(tid) for tid in target_ids]

    success_count = sum(1 for item in items if item.get("ok"))
    failed_count = len(items) - success_count
    resolved_platform = default_platform or (
        str(items[0].get("platform") or "") if items else ""
    )
    resolved_language = requested_language or (
        str(default_marketplace_site(resolved_platform).get("language") or "").strip()
        if resolved_platform in PLATFORMS
        else ""
    ) or "English"
    payload: dict[str, Any] = {
        "ok": True,
        "platform": resolved_platform,
        "language": resolved_language,
        "total": len(items),
        "success_count": success_count,
        "failed_count": failed_count,
        "items": items,
        "message": f"成功 {success_count}/{len(items)}，失败 {failed_count}。",
    }
    if include_index:
        payload["productsIndex"] = get_context().products.load_products_index()
    return payload


def apply_product_drafts_to_plan(product: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    overrides = product.get("listing_overrides", {}) if isinstance(product.get("listing_overrides"), dict) else {}
    drafts = product.get("drafts", {}) if isinstance(product.get("drafts"), dict) else {}
    for platform_key, platform_state in plan.get("platforms", {}).items():
        listing = platform_state.get("listing", {})
        if not isinstance(listing, dict):
            continue
        draft = drafts.get(platform_key) if isinstance(drafts.get(platform_key), dict) else {}
        override = overrides.get(platform_key) if isinstance(overrides.get(platform_key), dict) else {}
        for field in ["title", "description", "language"]:
            value = draft.get(field) or override.get(field)
            if value:
                listing[field] = value
        if draft.get("search_terms"):
            listing["search_keywords"] = draft.get("search_terms")
            listing["attribute_keywords"] = draft.get("search_terms")
    return plan


def build_image_prompt_pack(
    product: dict[str, Any],
    platform: str,
    selected_image_ids: list[str] | None = None,
    include_description: bool = True,
    target_language: str = "",
) -> str:
    copy = build_copy_preview(
        product,
        platform,
        get_context().config.load_app_config(),
    )
    listing = copy.get("listing", {})
    pool = _source_pool_items(product)
    selected_ids = {str(item).strip() for item in (selected_image_ids or []) if str(item).strip()}
    images = [item for item in pool if str(item.get("id") or "").strip() in selected_ids] if selected_ids else pool
    if not images:
        images = _source_only_pool_items(product)
    description = str(listing.get("description") or "").strip() if include_description else ""
    language = str(target_language or listing.get("language") or "").strip()
    lines = [
        "ChatGPT 生图提示词包",
        f"产品名: {product.get('name', '')}",
        f"品牌: {product.get('brand', '')}",
        f"品类: {product.get('category', '')}",
        f"目标语言: {language or '按目标平台'}",
        f"平台文案: {listing.get('title', '')}",
        f"平台描述: {description}",
        "原图:",
    ]
    for item in images[:8]:
        lines.append(
            "- "
            + " | ".join(
                [
                    str(item.get("id") or ""),
                    str(item.get("origin") or ""),
                    str(item.get("usage") or ""),
                    str(item.get("path") or item.get("url") or item.get("preview_url") or ""),
                ]
            )
        )
    lines.extend(
        [
            "",
            "目标：生成适合海外电商平台的本地化商品图。",
            "要求：保持真实外观、材质、比例和核心卖点，不要加入原图没有的配件或虚假认证。",
        ]
    )
    if copy.get("warning"):
        lines.append(f"提示: {copy['warning']}")
    return "\n".join(lines)


__all__ = [
    "apply_product_drafts_to_plan",
    "batch_generate_copy_for_products",
    "build_copy_preview",
    "build_image_prompt_pack",
    "build_plan_for_platform",
    "generate_ai_copy_bundle",
    "list_presets",
    "platform_to_preset_key",
    "save_copy_result",
]
