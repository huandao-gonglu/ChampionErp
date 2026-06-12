# -*- coding: utf-8 -*-
from __future__ import annotations

from .runtime_common import *

def list_presets() -> dict[str, Any]:
    return generator.load_json(APP_DIR / "presets" / "platforms.json")


def platform_to_preset_key(platform: str) -> str:
    platform = (platform or "").lower()
    if platform == "mercadolibre":
        return "mercadolibre"
    if platform in {"wildberries", "ozon"}:
        return "wildberries"
    return "mercadolibre"


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
    ai_cfg = config_service.ai_config_from_sources(APP_DIR, app_cfg).get("text_ai", {})
    provider_name = str(ai_cfg.get("platform") or "DeepSeek").lower()
    provider = "deepseek" if "deepseek" in provider_name else "openai"
    model = str(ai_cfg.get("model") or ("deepseek-chat" if provider == "deepseek" else "gpt-4.1-mini"))
    warning = ""
    old_env = {
        "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY"),
        "DEEPSEEK_BASE_URL": os.environ.get("DEEPSEEK_BASE_URL"),
        "DEEPSEEK_MODEL": os.environ.get("DEEPSEEK_MODEL"),
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
        "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL"),
    }
    try:
        if not str(ai_cfg.get("api_key") or "").strip():
            warning = f"当前未配置 {ai_cfg.get('platform') or '文本 AI'} API Key，已返回基础草稿。"
        else:
            if provider == "deepseek":
                os.environ["DEEPSEEK_API_KEY"] = str(ai_cfg.get("api_key") or "").strip()
                os.environ["DEEPSEEK_BASE_URL"] = str(ai_cfg.get("base_url") or "https://api.deepseek.com").strip()
                os.environ["DEEPSEEK_MODEL"] = model
            else:
                os.environ["OPENAI_API_KEY"] = str(ai_cfg.get("api_key") or "").strip()
                if str(ai_cfg.get("base_url") or "").strip():
                    os.environ["OPENAI_BASE_URL"] = str(ai_cfg.get("base_url") or "").strip()
            generator.refine_listing_copy(
                plan,
                model=model,
                provider=provider,
                deepseek_model=model,
            )
    except Exception as exc:
        warning = str(exc)
    finally:
        for key_name, value in old_env.items():
            if value is None:
                os.environ.pop(key_name, None)
            else:
                os.environ[key_name] = value
    key = platform_to_preset_key(platform)
    listing = plan.get("platforms", {}).get(key, {}).get("listing", {})
    if platform == "mercadolibre":
        listing["title"] = str(listing.get("title") or product.get("name") or "")[:60]
    if platform == "ozon" and not listing.get("description"):
        listing["description"] = "Ozon 俄语文案草稿。"
    return {"plan": plan, "listing": listing, "warning": warning}


def openai_client_from_config(app_cfg: dict[str, Any]):
    text_ai = config_service.ai_config_from_sources(APP_DIR, app_cfg).get("text_ai", {})
    api_key = str(text_ai.get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("请先在系统设置的“生文案通道”填写 API Key。")
    base_url = str(text_ai.get("base_url") or "").strip().rstrip("/")
    kwargs: dict[str, Any] = {"api_key": api_key, "timeout": AI_TEXT_REQUEST_TIMEOUT_SECONDS}
    if base_url:
        kwargs["base_url"] = base_url
    from openai import OpenAI

    return OpenAI(**kwargs)


def target_market_label(target_market: str) -> str:
    mapping = {
        "amazon": "Amazon",
        "mercadolibre": "Mercado Libre",
        "wildberries": "Wildberries",
        "ozon": "Ozon",
    }
    return mapping.get((target_market or "").lower(), (target_market or "marketplace").title())


def build_web_copy_prompt(
    product: dict[str, Any],
    source_listing: dict[str, Any],
    source_platform: str,
    target_market: str,
    language: str,
    mode: str,
) -> str:
    target_label = target_market_label(target_market)
    source_label = target_market_label(source_platform)
    language = (language or "English").strip() or "English"
    title_limit = 180 if target_market.lower() == "amazon" else 60 if target_market.lower() == "mercadolibre" else 120
    return f"""You are an ecommerce copywriter.

Return only valid JSON with these keys:
title: string
description: string
bullets: array of 5 short strings
alt_titles: array of 2-3 strings
search_keywords: array of 10-20 strings

Requirements:
- Write in {language}.
- Target market: {target_label}.
- Mode: {mode}.
- Keep the title under {title_limit} characters if possible.
- Do not invent certifications, accessories, or specs not supported by the product data.
- Make the copy conversion-oriented but truthful.
- If the target is Amazon, use Amazon-style bullets and a concise product description.
- If the target is Mercado Libre, keep the title and description natural for that marketplace.

Source draft from {source_label}:
Title: {source_listing.get("title", "")}
Description: {source_listing.get("description", "")}

Product summary:
{generator.product_summary(product)}
"""


def generate_ai_copy_bundle(
    product: dict[str, Any],
    source_platform: str,
    target_market: str,
    language: str,
    mode: str,
    app_cfg: dict[str, Any],
) -> dict[str, Any]:
    source_key = platform_to_preset_key(source_platform)
    result = copy_service.generate_copy(
        str(APP_DIR),
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
    product = normalize_product_fields(product)
    target_key = (target_market or "").strip().lower() or "mercadolibre"
    copy_results = product.setdefault("copy_results", {})
    if isinstance(copy_results, dict):
        copy_results[target_key] = copy
    drafts = product.setdefault("drafts", {})
    if isinstance(drafts, dict):
        draft = drafts.setdefault(target_key, {})
        if isinstance(draft, dict):
            draft.update(
                {
                    "title": copy.get("title", ""),
                    "description": copy.get("description", ""),
                    "bullets": copy.get("bullets", []),
                    "search_terms": copy.get("search_keywords", []),
                    "language": copy.get("language", draft.get("language", "")),
                    "copy_source": "ai",
                    "copy_generated_at": collect_time_iso(),
                }
            )
            draft["status"] = draft_workflow_status(product, target_key)
    if target_key == "mercadolibre":
        overrides = product.setdefault("listing_overrides", {})
        if isinstance(overrides, dict):
            overrides["mercadolibre"] = {
                "title": copy.get("title", ""),
                "description": copy.get("description", ""),
                "alt_titles": copy.get("alt_titles", []),
                "search_keywords": copy.get("search_keywords", []),
                "language": copy.get("language", "English"),
            }
    return save_product(product)


def batch_generate_copy_for_products(
    product_ids: list[str],
    platform: str = "mercadolibre",
    language: str = "",
    mode: str = "rewrite",
) -> dict[str, Any]:
    target_platform = str(platform or "mercadolibre").strip().lower() or "mercadolibre"
    if target_platform not in PLATFORMS:
        return {"ok": False, "success_count": 0, "failed_count": 0, "items": [], "error": "不支持的平台"}
    language = str(language or ("Spanish" if target_platform == "mercadolibre" else "Russian")).strip()
    app_cfg = load_app_config()
    items: list[dict[str, Any]] = []
    for product_id in [str(item or "").strip() for item in product_ids if str(item or "").strip()]:
        row = {
            "product_id": product_id,
            "platform": target_platform,
            "ok": False,
            "status": "failed",
            "title": "",
            "warning": "",
            "error": "",
        }
        try:
            product = load_product_from_index(product_id, "")
            if not product:
                raise RuntimeError("商品不存在")
            source_platform = str((product.get("source") or {}).get("source_platform") or product.get("source_platform") or target_platform)
            result = generate_ai_copy_bundle(product, source_platform, target_platform, language, mode, app_cfg)
            if result.get("warning"):
                raise RuntimeError(str(result.get("warning")))
            copy_payload = {**(result.get("copy") or {}), "language": result.get("language", language), "source_platform": result.get("source_platform", source_platform), "mode": result.get("mode", mode)}
            saved = save_copy_result(product, result.get("target_market") or target_platform, copy_payload)
            draft = ((saved.get("drafts") or {}).get(target_platform) or {}) if isinstance(saved.get("drafts"), dict) else {}
            row.update(
                {
                    "ok": True,
                    "status": draft.get("status") or "copy_ready",
                    "title": draft.get("title") or "",
                    "warning": result.get("warning") or "",
                    "product": saved,
                }
            )
        except Exception as exc:
            row["error"] = str(exc)
        items.append(row)
    return {
        "ok": True,
        "platform": target_platform,
        "language": language,
        "total": len(items),
        "success_count": sum(1 for item in items if item.get("ok")),
        "failed_count": sum(1 for item in items if not item.get("ok")),
        "items": items,
        "message": f"成功 {sum(1 for item in items if item.get('ok'))}/{len(items)}，失败 {sum(1 for item in items if not item.get('ok'))}。",
        "productsIndex": load_products_index(),
    }


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
        if draft.get("bullets"):
            listing["bullets"] = draft.get("bullets")
        if draft.get("search_terms"):
            listing["search_keywords"] = draft.get("search_terms")
            listing["attribute_keywords"] = draft.get("search_terms")
    return plan


def build_image_prompt_pack(
    product: dict[str, Any],
    platform: str,
    selected_image_ids: list[str] | None = None,
    include_bullets: bool = True,
    include_description: bool = True,
    target_language: str = "",
) -> str:
    copy = build_copy_preview(product, platform, load_app_config())
    listing = copy.get("listing", {})
    pool = _source_pool_items(product)
    selected_ids = {str(item).strip() for item in (selected_image_ids or []) if str(item).strip()}
    images = [item for item in pool if str(item.get("id") or "").strip() in selected_ids] if selected_ids else pool
    if not images:
        images = _source_only_pool_items(product)
    bullets = normalize_list(product.get("selling_points")) if include_bullets else []
    description = str(listing.get("description") or product.get("description") or "").strip() if include_description else ""
    language = str(target_language or listing.get("language") or "").strip()
    lines = [
        "ChatGPT 生图提示词包",
        f"产品名: {product.get('name', '')}",
        f"品牌: {product.get('brand', '')}",
        f"品类: {product.get('category', '')}",
        f"目标语言: {language or '按目标平台'}",
        f"核心卖点: {'，'.join(bullets[:6])}",
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
