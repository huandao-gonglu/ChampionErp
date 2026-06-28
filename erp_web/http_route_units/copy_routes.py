# -*- coding: utf-8 -*-
from __future__ import annotations

import logging

from typing import Callable

from .common import JsonRequestHandler
from ..runtime_units.auth_runtime import test_ai_model_config
from ..runtime_units.copy_generation import (
    apply_product_drafts_to_plan,
    batch_generate_copy_for_products,
    build_image_prompt_pack,
    build_plan_for_platform,
    generate_ai_copy_bundle,
    platform_to_preset_key,
    save_copy_result,
)
from ..runtime_units.product_store import load_app_config, load_product, load_products_index, normalize_product_fields


PostHandler = Callable[[JsonRequestHandler], None]
logger = logging.getLogger(__name__)

def handle_generate_copy(handler: JsonRequestHandler) -> None:
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
    return


def handle_generate_copy_batch(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    result = batch_generate_copy_for_products(
        body.get("product_ids") if isinstance(body.get("product_ids"), list) else [],
        str(body.get("platform") or "mercadolibre"),
        str(body.get("language") or ""),
        str(body.get("mode") or "rewrite"),
    )
    handler.send_json(result, 200 if result.get("ok") else 400)
    return


def handle_generate_image_prompts(handler: JsonRequestHandler) -> None:
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
    return


def handle_test_ai_model(handler: JsonRequestHandler) -> None:
    body = handler.read_body()
    try:
        model_config = body.get("model") if isinstance(body.get("model"), dict) else body.get("config")
        result = test_ai_model_config(model_config if isinstance(model_config, dict) else {})
        handler.send_json(result)
    except Exception as exc:
        model_config = body.get("model") if isinstance(body.get("model"), dict) else body.get("config")
        model_record = model_config if isinstance(model_config, dict) else {}
        logger.info(
            "AI model test failed trigger=%s model_id=%s provider=%s model=%s probe=%s error=%s",
            model_record.get("test_trigger"),
            model_record.get("id"),
            model_record.get("provider"),
            model_record.get("model"),
            model_record.get("probe_capabilities", True),
            exc,
        )
        handler.send_json({"ok": False, "error": str(exc)}, 400)
    return


POST_HANDLERS: dict[str, PostHandler] = {
    "/api/generate-copy": handle_generate_copy,
    "/api/generate-copy-batch": handle_generate_copy_batch,
    "/api/generate-image-prompts": handle_generate_image_prompts,
    "/api/test-ai-model": handle_test_ai_model,
}
HANDLED_PATHS = frozenset(POST_HANDLERS)


def handle_post(handler: JsonRequestHandler, parsed: object) -> bool:
    route_handler = POST_HANDLERS.get(parsed.path)
    if route_handler is None:
        return False
    route_handler(handler)
    return True
