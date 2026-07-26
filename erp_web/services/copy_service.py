"""AI copy generation helpers for marketplace listings."""

from __future__ import annotations

from typing import Any

from erp_web.marketplace_registry import default_marketplace_site, marketplace_options
from . import ai_gateway, ai_prompt_templates


def service_status() -> dict[str, str]:
    return {"service": "copy", "status": "ready"}


def normalize_list(value: Any, limit: int | None = None) -> list[str]:
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
        "Title": product.get("name") or product.get("title") or source.get("title"),
        "Brand": product.get("brand") or source.get("brand"),
        "Model": product.get("model") or source.get("model"),
        "Category": product.get("category") or source.get("category"),
        "Material": ", ".join(normalize_list(product.get("materials") or product.get("source_material"))),
        "Dimensions": product.get("dimensions") or source.get("dimensions"),
        "Weight": product.get("weight_kg") or product.get("source_weight_kg") or source.get("weight_kg"),
        "Colors": ", ".join(normalize_list(product.get("colors"))),
        "Selling points": "; ".join(normalize_list(product.get("selling_points"), 8)),
        "Package includes": "; ".join(normalize_list(product.get("package_includes"), 8)),
        "Source text": product.get("source_text") or product.get("supplemental_info") or source.get("description"),
    }
    return "\n".join(f"{key}: {value}" for key, value in fields.items() if value)


def _market_label(target_market: str) -> str:
    target = str(target_market or "").strip().lower()
    option = next((item for item in marketplace_options() if item["key"] == target), None)
    return str(option["label"] if option else target_market or "marketplace")


def _default_language(target_market: str) -> str:
    return str(default_marketplace_site(target_market).get("language") or "English").strip()


def build_copy_prompt_from_config(
    app_dir: str,
    app_config: dict[str, Any] | None,
    product: dict[str, Any],
    target_market: str,
    language: str,
    mode: str,
) -> dict[str, str]:
    title_limit = 60 if target_market == "mercadolibre" else 120
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
    return {
        "system": configured_system,
        "user": ai_prompt_templates.render_prompt_template(
            configured_user,
            {
                "language": language,
                "target_market": target_market,
                "market_label": market_label,
                "mode": mode,
                "title_limit": title_limit,
                "product_summary": product_summary(product),
            },
        ),
    }


def _normalized_generated_copy(parsed: Any, target_market: str) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise RuntimeError("AI 未返回 JSON 对象。")
    title_limit = 60 if target_market == "mercadolibre" else 120
    result = {
        "title": str(parsed.get("title") or "").strip()[:title_limit],
        "description": str(parsed.get("description") or "").strip(),
        "bullets": normalize_list(parsed.get("bullets") or parsed.get("selling_points"), 5),
        "alt_titles": normalize_list(parsed.get("alt_titles") or parsed.get("alternative_titles"), 3),
        "search_keywords": normalize_list(parsed.get("search_keywords") or parsed.get("keywords"), 20),
    }
    missing = [key for key in ("title", "description") if not result[key]]
    if missing:
        raise RuntimeError(f"AI 返回的文案缺少必要字段：{', '.join(missing)}。")
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
    model = {}
    try:
        prompt_pair = build_copy_prompt_from_config(app_dir, app_config, product, target, language, mode)
        model = ai_gateway.resolve_model_for_use_case(app_dir, app_config, "copy.generate")
        parsed = ai_gateway.chat_json(
            app_dir,
            app_config,
            "copy.generate",
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
