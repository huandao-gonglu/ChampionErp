# -*- coding: utf-8 -*-
from __future__ import annotations


from .common import JsonRequestHandler
from .. import runtime as app
from ..runtime import *  # noqa: F403 - route units mirror legacy runtime globals.


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    if parsed.path == "/api/generate-copy":
        body = handler.read_body()
        product = normalize_product_fields(body.get("product") or load_product())
        platform = str(body.get("platform", "mercadolibre") or "mercadolibre")
        target_market = str(body.get("target_market") or platform or "mercadolibre")
        language = str(body.get("language") or ("Spanish (Mexico)" if target_market.strip().lower() == "mercadolibre" else "English"))
        mode = str(body.get("mode") or "rewrite")
        result = generate_ai_copy_bundle(product, platform, target_market, language, mode, load_app_config())
        product = save_copy_result(product, result["target_market"], {**result["copy"], "language": result["language"], "source_platform": result["source_platform"], "mode": result["mode"]})
        plan = apply_product_drafts_to_plan(product, build_plan_for_platform(product, platform))
        listing = plan.get("platforms", {}).get(platform_to_preset_key(platform), {}).get("listing", {})
        handler.send_json({"ok": True, **result, "product": product, "plan": plan, "listing": listing, "productsIndex": load_products_index()})
        return True
    if parsed.path == "/api/generate-copy-batch":
        body = handler.read_body()
        result = batch_generate_copy_for_products(
            body.get("product_ids") if isinstance(body.get("product_ids"), list) else [],
            str(body.get("platform") or "mercadolibre"),
            str(body.get("language") or ""),
            str(body.get("mode") or "rewrite"),
        )
        handler.send_json(result, 200 if result.get("ok") else 400)
        return True
    if parsed.path == "/api/generate-image-prompts":
        body = handler.read_body()
        product = normalize_product_fields(body.get("product") or load_product())
        prompt = build_image_prompt_pack(
            product,
            body.get("platform", "mercadolibre"),
            body.get("selected_image_ids") if isinstance(body.get("selected_image_ids"), list) else [],
            bool(body.get("include_bullets", True)),
            bool(body.get("include_description", True)),
            str(body.get("target_language") or body.get("language") or ""),
        )
        handler.send_json({"ok": True, "prompt": prompt, "selected_image_ids": body.get("selected_image_ids") or []})
        return True
    if parsed.path == "/api/test-ai-channel":
        body = handler.read_body()
        try:
            result = test_ai_channel(body.get("channel", "text"), body.get("config") or {})
            handler.send_json(result)
        except Exception as exc:
            handler.send_json({"ok": False, "error": str(exc)}, 400)
        return True
    return False
