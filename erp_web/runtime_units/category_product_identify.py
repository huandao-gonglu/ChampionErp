# -*- coding: utf-8 -*-
"""用文本 AI 提炼商品主体，并生成各目标站点的类目检索词。"""

from __future__ import annotations

import json
from typing import Any

from erp_web.services import ai_gateway, ai_prompt_templates

from .product_store import load_app_config, normalize_product_fields
from .runtime_common import APP_DIR


def _text(value: Any, limit: int = 1600) -> str:
    return str(value or "").strip()[:limit]


def _string_list(value: Any, limit: int = 12, item_limit: int = 240) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item, item_limit) for item in value if _text(item, item_limit)][:limit]


def _attributes(value: Any, limit: int = 30) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        _text(key, 100): _text(item, 300)
        for key, item in list(value.items())[:limit]
        if _text(key, 100) and _text(item, 300)
    }


def _target_key(platform: Any, site: Any) -> str:
    return f"{str(platform or '').strip().lower()}:{str(site or '').strip().lower()}"


def _normalized_targets(targets: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in targets or []:
        target = raw if isinstance(raw, dict) else {}
        platform = _text(target.get("platform"), 80).lower()
        site = _text(target.get("site") or target.get("site_id"), 80)
        key = _target_key(platform, site)
        if not platform or not site or key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "platform": platform,
                "site": site,
                "language": _text(target.get("language"), 80),
                "currency": _text(target.get("currency"), 20),
            }
        )
    return result


def _identity_input(product: dict[str, Any], draft: dict[str, Any], targets: list[dict[str, str]]) -> dict[str, Any]:
    product = normalize_product_fields(product)
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    draft = draft if isinstance(draft, dict) else {}
    return {
        "source_product": {
            "title": _text(source.get("title") or product.get("name")),
            "description": _text(source.get("description"), 3000),
            "bullets": _string_list(source.get("bullets") or product.get("selling_points")),
            "attributes": _attributes(source.get("attributes") or product.get("attributes")),
            "brand": _text(source.get("brand") or product.get("brand"), 160),
            "model": _text(source.get("model") or product.get("model"), 160),
            "source_category": _text(product.get("category"), 240),
        },
        "draft": {
            "title": _text(draft.get("title")),
            "description": _text(draft.get("description"), 2400),
            "bullets": _string_list(draft.get("bullets")),
        },
        "target_sites": targets,
    }


def _number_between_zero_and_one(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _queries_by_target(raw: Any) -> dict[str, str]:
    queries: dict[str, str] = {}
    rows = raw if isinstance(raw, list) else []
    for item in rows:
        record = item if isinstance(item, dict) else {}
        key = _target_key(record.get("platform"), record.get("site") or record.get("site_id"))
        query = _text(record.get("query"), 240)
        if key and query:
            queries[key] = query
    return queries


def identify_product_for_category(
    product: dict[str, Any],
    draft: dict[str, Any],
    targets: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """识别商品主体，并为草稿的每个目标站点生成本地化检索词。"""

    normalized_targets = _normalized_targets(targets)
    if not normalized_targets:
        raise RuntimeError("当前草稿没有可识别类目的目标站点。")
    app_cfg = load_app_config()
    payload = _identity_input(product, draft, normalized_targets)
    prompt_pair = ai_prompt_templates.load_ai_use_case_prompt_pair(APP_DIR, app_cfg, "category.product_identify")
    messages = [
        {"role": "system", "content": prompt_pair["system"]},
        {
            "role": "user",
            "content": ai_prompt_templates.render_prompt_template(
                prompt_pair["user"],
                {"input_json": json.dumps(payload, ensure_ascii=False)},
            ),
        },
    ]
    parsed = ai_gateway.chat_json(
        APP_DIR,
        app_cfg,
        "category.product_identify",
        messages,
        temperature=0,
        max_tokens=700,
    )
    if not isinstance(parsed, dict):
        raise RuntimeError("AI 未返回可用的商品主体识别结果。")
    product_name = _text(parsed.get("product_name"), 160)
    if not product_name:
        raise RuntimeError("AI 未识别出商品主体，请改用手动类目搜索。")
    queries = _queries_by_target(parsed.get("target_queries"))
    target_queries = [
        {
            **target,
            "query": queries.get(_target_key(target["platform"], target["site"]), ""),
        }
        for target in normalized_targets
    ]
    return {
        "ok": True,
        "identity": {
            "name": product_name,
            "product_type": _text(parsed.get("product_type"), 120),
            "confidence": _number_between_zero_and_one(parsed.get("confidence")),
            "reason": _string_list(parsed.get("reason"), limit=4, item_limit=220),
        },
        "targets": target_queries,
        "source": "ai_model",
    }


__all__ = ["identify_product_for_category"]
