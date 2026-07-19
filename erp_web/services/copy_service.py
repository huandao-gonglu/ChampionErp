"""AI copy generation helpers for marketplace listings."""

from __future__ import annotations

from typing import Any


from . import ai_gateway, ai_prompt_templates


DEFAULT_COPY_USER_PROMPT = """You are an ecommerce listing copywriter.

Return only valid JSON with:
title: string
description: string
bullets: array of 5 short strings
alt_titles: array of 2-3 strings
search_keywords: array of 10-20 strings

Rules:
- Language: {$language}.
- Target marketplace: {$market_label}.
- Mode: {$mode}.
- Keep title under {$title_limit} characters when possible.
- Do not invent certifications, compatibility, accessories, brand claims, or specs.
- For Mercado Libre Mexico, use natural Mexican Spanish and avoid medical or exaggerated claims.

Product data:
{$product_summary}"""

DEFAULT_LANGUAGE_BY_TARGET = {
    "mercadolibre": "es",
    "yandex": "ru-RU",
    "ozon": "ru-RU",
}


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


def fallback_copy(product: dict[str, Any], target_market: str = "mercadolibre") -> dict[str, Any]:
    title = str(product.get("name") or product.get("title") or "").strip()
    bullets = normalize_list(product.get("selling_points"), 5)
    if not bullets:
        bullets = ["Diseno practico para uso diario", "Materiales seleccionados", "Facil de instalar y mantener"]
    description = str(product.get("description") or product.get("source_text") or product.get("supplemental_info") or "").strip()
    if not description:
        description = "Producto pensado para compradores que buscan una opcion practica, clara y confiable."
    title_limit = 60 if target_market == "mercadolibre" else 120
    return {
        "title": title[:title_limit],
        "description": description,
        "bullets": bullets[:5],
        "alt_titles": normalize_list([title], 3),
        "search_keywords": normalize_list([product.get("category"), product.get("brand"), product.get("model"), *bullets], 20),
    }


def build_copy_prompt(product: dict[str, Any], target_market: str, language: str, mode: str) -> str:
    title_limit = 60 if target_market == "mercadolibre" else 120
    market_label = "Mercado Libre Mexico" if target_market == "mercadolibre" else target_market.title()
    return ai_prompt_templates.render_prompt_template(
        DEFAULT_COPY_USER_PROMPT,
        {
            "language": language,
            "target_market": target_market,
            "market_label": market_label,
            "mode": mode,
            "title_limit": title_limit,
            "product_summary": product_summary(product),
        },
    )


def build_copy_prompt_from_config(
    app_dir: str,
    app_config: dict[str, Any] | None,
    product: dict[str, Any],
    target_market: str,
    language: str,
    mode: str,
) -> dict[str, str]:
    title_limit = 60 if target_market == "mercadolibre" else 120
    market_label = "Mercado Libre Mexico" if target_market == "mercadolibre" else target_market.title()
    pair = ai_prompt_templates.load_ai_use_case_prompt_pair(app_dir, app_config, "copy.generate")
    return {
        "system": pair["system"],
        "user": ai_prompt_templates.render_prompt_template(
            pair["user"],
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


def generate_copy(
    app_dir: str,
    product: dict[str, Any],
    app_config: dict[str, Any] | None = None,
    target_market: str = "mercadolibre",
    language: str = "",
    mode: str = "rewrite",
) -> dict[str, Any]:
    target = str(target_market or "mercadolibre").strip().lower()
    language = language or DEFAULT_LANGUAGE_BY_TARGET.get(target, "English")
    result = fallback_copy(product, target)
    warning = ""
    prompt_pair = build_copy_prompt_from_config(app_dir, app_config, product, target, language, mode)
    model = {}
    try:
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
        if isinstance(parsed, dict):
            result.update(
                {
                    "title": str(parsed.get("title") or result["title"]).strip()[: (60 if target == "mercadolibre" else 120)],
                    "description": str(parsed.get("description") or result["description"]).strip(),
                    "bullets": normalize_list(parsed.get("bullets") or result["bullets"], 5),
                    "alt_titles": normalize_list(parsed.get("alt_titles") or result["alt_titles"], 3),
                    "search_keywords": normalize_list(parsed.get("search_keywords") or result["search_keywords"], 20),
                }
            )
    except Exception as exc:
        warning = str(exc)
    return {
        "ok": True,
        "provider": str(model.get("provider") or ""),
        "ai_model_id": str(model.get("id") or ""),
        "target_market": target,
        "language": language,
        "mode": mode,
        "copy": result,
        "warning": warning,
    }
