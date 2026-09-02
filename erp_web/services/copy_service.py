"""AI copy generation helpers for marketplace listings."""

from __future__ import annotations

from typing import Any

from erp_web.schemas.copy import (
    LocalizedCopyOutput,
    MercadoLibreCbtLocalizedCopyOutput,
)
from erp_web.marketplace_registry import (
    default_marketplace_site,
    marketplace_options,
    platform_title_limit,
)
from . import ai_gateway, ai_prompt_templates


def service_status() -> dict[str, str]:
    return {"service": "copy", "status": "ready"}


def normalize_copy_list(value: Any, limit: int | None = None) -> list[str]:
    if isinstance(value, str):
        items = [line.strip() for line in value.replace("；", "\n").replace(";", "\n").splitlines()]
    elif isinstance(value, list):
        items = [str(item or "").strip() for item in value]
    else:
        items = []
    result = []
    seen = set()
    for item in items:
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if limit and len(result) >= limit:
            break
    return result


def product_summary(product: dict[str, Any]) -> str:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    fields = {
        "Title": product.get("name") or source.get("title"),
        "Brand": product.get("brand") or source.get("brand"),
        "Model": product.get("model") or source.get("model"),
        "Category": product.get("category") or source.get("category"),
        "Material": ", ".join(normalize_copy_list(product.get("materials") or product.get("source_material"))),
        "Dimensions": product.get("dimensions") or source.get("dimensions"),
        "Weight": product.get("weight_kg") or product.get("source_weight_kg") or source.get("weight_kg"),
        "Colors": ", ".join(normalize_copy_list(product.get("colors"))),
        "Selling points": "; ".join(normalize_copy_list(product.get("selling_points"), 8)),
        "Package includes": "; ".join(normalize_copy_list(product.get("package_includes"), 8)),
        "Source text": product.get("source_text") or product.get("supplemental_info") or source.get("description"),
    }
    return "\n".join(f"{key}: {value}" for key, value in fields.items() if value)


def _market_label(target_market: str) -> str:
    target = str(target_market or "").strip().lower()
    option = next((item for item in marketplace_options() if item["key"] == target), None)
    return str(option["label"] if option else target_market or "marketplace")


def _default_language(target_market: str) -> str:
    return str(default_marketplace_site(target_market).get("language") or "English").strip()


def _requires_cbt_global_title(
    product: dict[str, Any],
    target_market: str,
) -> bool:
    """CBT 草稿除本地化标题外，还需要独立的英文根标题。"""

    if str(target_market or "").strip().lower() != "mercadolibre":
        return False
    drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
    draft = drafts.get("mercadolibre") if isinstance(drafts.get("mercadolibre"), dict) else {}
    candidates = [product, draft]
    for candidate in candidates:
        platform = str(candidate.get("platform") or "").strip().lower()
        site = str(candidate.get("site") or "").strip().upper()
        if site == "CBT" and platform in {"", "mercadolibre"}:
            return True
        target_sites = candidate.get("target_sites")
        if not isinstance(target_sites, list):
            continue
        if any(
            isinstance(target, dict)
            and str(target.get("platform") or "mercadolibre").strip().lower()
            == "mercadolibre"
            and str(target.get("site") or "").strip().upper() == "CBT"
            for target in target_sites
        ):
            return True
    return False


def build_copy_prompt_from_config(
    app_dir: str,
    app_config: dict[str, Any] | None,
    product: dict[str, Any],
    target_market: str,
    language: str,
    mode: str,
) -> dict[str, str]:
    title_limit = platform_title_limit(target_market)
    market_label = _market_label(target_market)
    pair = ai_prompt_templates.load_ai_use_case_prompt_pair(app_dir, app_config, "copy.generate")
    configured_user = pair["user"]
    required_context_markers = (
        ("{$language}", "{language}"),
        ("{$market_label}", "{market_label}"),
        ("{$product_summary}", "{product_summary}"),
    )
    has_required_context = all(any(marker in configured_user for marker in alternatives) for alternatives in required_context_markers)
    configured_system = pair["system"].strip()
    if not configured_system or configured_system == "System from settings":
        raise RuntimeError("功能绑定“文案生成”的系统提示词未配置，请在 AI 设置中维护 copy.generate 提示词。")
    if not has_required_context:
        raise RuntimeError("功能绑定“文案生成”的用户提示词必须包含 language、market_label 和 product_summary 上下文。")
    user_prompt = ai_prompt_templates.render_prompt_template(
        configured_user,
        {
            "language": language,
            "target_market": target_market,
            "market_label": market_label,
            "mode": mode,
            "title_limit": title_limit,
            "product_summary": product_summary(product),
        },
    )
    return {
        "system": configured_system,
        "user": user_prompt,
    }


def _normalized_generated_copy(
    parsed: LocalizedCopyOutput,
    target_market: str,
) -> dict[str, Any]:
    values = parsed.model_dump(mode="json")
    title_limit = platform_title_limit(target_market)
    title = str(values.get("title") or "").strip()
    if len(title) > title_limit:
        raise RuntimeError(
            f"AI 返回的标题超过 {title_limit} 个字符，请重新生成更短的标题。"
        )
    result = {
        "title": title,
        "description": str(values.get("description") or "").strip(),
        "bullets": normalize_copy_list(values.get("bullets"), 5),
        "alt_titles": normalize_copy_list(values.get("alt_titles"), 3),
        "search_keywords": normalize_copy_list(values.get("search_keywords"), 20),
    }
    if isinstance(parsed, MercadoLibreCbtLocalizedCopyOutput):
        global_title = parsed.global_title
        if len(global_title) > title_limit:
            raise RuntimeError(
                f"AI 返回的 CBT 根英文标题超过 {title_limit} 个字符，请重新生成更短的标题。"
            )
        result["global_title"] = global_title
    return result


def generate_copy(
    app_dir: str,
    product: dict[str, Any],
    app_config: dict[str, Any] | None = None,
    target_market: str = "mercadolibre",
    language: str = "",
    mode: str = "rewrite",
) -> dict[str, Any]:
    target = str(target_market or "mercadolibre").strip().lower()
    language = language or _default_language(target)
    require_global_title = _requires_cbt_global_title(product, target)
    model = {}
    try:
        prompt_pair = build_copy_prompt_from_config(app_dir, app_config, product, target, language, mode)
        model = ai_gateway.resolve_model_for_use_case(app_dir, app_config, "copy.generate")
        output_type = (
            MercadoLibreCbtLocalizedCopyOutput
            if require_global_title
            else LocalizedCopyOutput
        )
        parsed = ai_gateway.chat_structured(
            app_dir,
            app_config,
            "copy.generate",
            output_type=output_type,
            messages=[
                {"role": "system", "content": prompt_pair["system"]},
                {"role": "user", "content": prompt_pair["user"]},
            ],
            temperature=0.35,
        )
        result = _normalized_generated_copy(parsed, target)
    except Exception as exc:
        return {
            "ok": False,
            "provider": str(model.get("provider") or ""),
            "ai_model_id": str(model.get("id") or ""),
            "target_market": target,
            "language": language,
            "mode": mode,
            "copy": {},
            "error": f"本地化文案生成失败：{exc}",
        }
    return {
        "ok": True,
        "provider": str(model.get("provider") or ""),
        "ai_model_id": str(model.get("id") or ""),
        "target_market": target,
        "language": language,
        "mode": mode,
        "copy": result,
    }
