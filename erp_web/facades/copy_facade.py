"""文案 HTTP 输入适配器。

这里负责把请求对象转换为已有领域函数的参数，并将领域结果统一为
``(response, status)``。持久化、AI 请求和图片提示词生成仍由各自领域模块拥有。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from erp_web.context import get_context
from erp_web.marketplace_registry import default_marketplace_site
from erp_web.product_model import PLATFORMS
from erp_web.runtime_units.copy_generation import (
    apply_product_drafts_to_plan,
    batch_generate_copy_for_products,
    build_image_prompt_pack,
    build_plan_for_platform,
    generate_ai_copy_bundle,
    platform_to_preset_key,
    save_copy_result,
)
from erp_web.runtime_units.store_credentials import test_ai_model_config
from erp_web.schemas.api import ApiResponse

if TYPE_CHECKING:
    from erp_web.stores.product_store import ProductStore

ResponseWithStatus = tuple[ApiResponse, int]
logger = logging.getLogger(__name__)


def _load_copy_product(
    body: dict[str, Any],
    products: ProductStore,
) -> tuple[dict[str, Any], ApiResponse | None, int, str]:
    draft_id = str(body.get("draft_id") or "").strip()
    if not draft_id:
        product, error, status = (
            products.load_required_product_from_body(body)
        )
        return product, error, status, ""

    product = products.load_draft_from_index(draft_id)
    if not str(product.get("current_draft_id") or "").strip():
        error: ApiResponse = {
            "ok": False,
            "error": "草稿不存在",
            "draft_id": draft_id,
        }
        return product, error, 404, draft_id

    requested_product_id = str(body.get("product_id") or "").strip()
    loaded_product_id = str(product.get("product_id") or "").strip()
    if requested_product_id and requested_product_id != loaded_product_id:
        error = {"ok": False, "error": "草稿与商品不匹配", "draft_id": draft_id}
        return product, error, 400, draft_id
    return product, None, 200, draft_id


def _copy_language(body: dict[str, Any], product: dict[str, Any], platform: str) -> str:
    explicit = str(body.get("language") or "").strip()
    if explicit:
        return explicit
    drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
    draft = drafts.get(platform) if isinstance(drafts.get(platform), dict) else {}
    draft_language = str(draft.get("language") or "").strip()
    return draft_language or str(
        default_marketplace_site(platform).get("language") or "English"
    )


def _log_copy_failure(
    product: dict[str, Any],
    draft_id: str,
    platform: str,
    result: dict[str, Any],
) -> None:
    logger.warning(
        "本地化文案生成失败 product_id=%s draft_id=%s platform=%s error=%s",
        str(product.get("product_id") or ""),
        draft_id,
        platform,
        str(result.get("error") or ""),
    )


def _draft_detail_payload(
    product: dict[str, Any],
    requested_draft_id: str,
    products: ProductStore,
) -> ApiResponse:
    draft_id = str(product.get("current_draft_id") or requested_draft_id or "")
    if not draft_id:
        return {}
    detail, error, _ = products.load_draft_detail_from_index(draft_id)
    return {} if error else detail


def _copy_success_payload(
    product: dict[str, Any],
    result: dict[str, Any],
    platform: str,
    requested_draft_id: str,
    products: ProductStore,
) -> ApiResponse:
    copy_record = {
        **result["copy"],
        "language": result["language"],
        "source_platform": result["source_platform"],
        "mode": result["mode"],
    }
    product = save_copy_result(product, result["target_market"], copy_record)
    plan = apply_product_drafts_to_plan(
        product,
        build_plan_for_platform(product, platform),
    )
    listing = (
        plan.get("platforms", {})
        .get(platform_to_preset_key(platform), {})
        .get("listing", {})
    )
    return {
        "ok": True,
        **result,
        "product": product,
        "plan": plan,
        "listing": listing,
        "productsIndex": products.load_products_index(),
        **_draft_detail_payload(
            product,
            requested_draft_id,
            products,
        ),
    }


def generate_copy_payload(body: dict[str, Any]) -> ResponseWithStatus:
    context = get_context()
    products = context.products
    product, error, status, draft_id = _load_copy_product(
        body,
        products,
    )
    if error:
        return error, status

    platform = str(body.get("platform") or "mercadolibre").strip().lower()
    if platform not in PLATFORMS:
        return {"ok": False, "error": "不支持的平台"}, 400

    result = generate_ai_copy_bundle(
        product,
        platform,
        platform,
        _copy_language(body, product, platform),
        str(body.get("mode") or "rewrite"),
        context.config.load_app_config(),
    )
    if not result.get("ok"):
        _log_copy_failure(product, draft_id, platform, result)
        return result, 400
    return _copy_success_payload(
        product,
        result,
        platform,
        draft_id,
        products,
    ), 200


def generate_copy_batch_payload(body: dict[str, Any]) -> ResponseWithStatus:
    result = batch_generate_copy_for_products(
        body.get("product_ids") if isinstance(body.get("product_ids"), list) else [],
        str(body.get("platform") or "mercadolibre"),
        str(body.get("language") or ""),
        str(body.get("mode") or "rewrite"),
    )
    return result, 200 if result.get("ok") else 400


def generate_image_prompts_payload(body: dict[str, Any]) -> ResponseWithStatus:
    products = get_context().products
    product, error, status = (
        products.load_required_product_from_body(body)
    )
    if error:
        return error, status
    selected_ids = (
        body.get("selected_image_ids")
        if isinstance(body.get("selected_image_ids"), list)
        else []
    )
    prompt = build_image_prompt_pack(
        product,
        body.get("platform", "mercadolibre"),
        selected_ids,
        bool(body.get("include_bullets", True)),
        bool(body.get("include_description", True)),
        str(body.get("target_language") or body.get("language") or ""),
    )
    return {
        "ok": True,
        "prompt": prompt,
        "selected_image_ids": body.get("selected_image_ids") or [],
    }, 200


def _model_config_from_body(body: dict[str, Any]) -> dict[str, Any]:
    model_config = (
        body.get("model") if isinstance(body.get("model"), dict) else body.get("config")
    )
    return model_config if isinstance(model_config, dict) else {}


def test_ai_model_payload(body: dict[str, Any]) -> ResponseWithStatus:
    model_config = _model_config_from_body(body)
    try:
        return test_ai_model_config(model_config), 200
    except Exception as exc:
        logger.info(
            "AI model test failed trigger=%s model_id=%s provider=%s model=%s probe=%s error=%s",
            model_config.get("test_trigger"),
            model_config.get("id"),
            model_config.get("provider"),
            model_config.get("model"),
            model_config.get("probe_capabilities", True),
            exc,
        )
        return {"ok": False, "error": str(exc)}, 400


__all__ = [
    "ResponseWithStatus",
    "generate_copy_batch_payload",
    "generate_copy_payload",
    "generate_image_prompts_payload",
    "test_ai_model_payload",
]
