from __future__ import annotations

"""文案生成、批量文案、图片提示词与文本翻译 Capability。

领域 AI 调用与持久化仍由 ``copy_generation`` / ``text_translation`` 拥有；
本模块只做类型化编排，与 HTTP facade 复用同一领域函数。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

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
from erp_web.runtime_units.text_translation import (
    TranslationRequestError,
    translate_texts,
)
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.content_capabilities import (
    CopyGenerateBatchRequest,
    CopyGenerateBatchResult,
    CopyGenerateRequest,
    CopyGenerateResult,
    ImagePromptsGenerateRequest,
    ImagePromptsGenerateResult,
    TextTranslateRequest,
    TextTranslateResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError


class ContentProductStore(Protocol):
    def load_required_product_from_body(
        self,
        body: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
        ...

    def load_draft_from_index(self, draft_id: str) -> dict[str, Any]:
        ...


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict_rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


@dataclass(frozen=True)
class ContentCapabilityScope:
    """文案与翻译能力的可信依赖边界。"""

    products: ContentProductStore
    app_config_loader: Callable[[], dict[str, Any]]


COPY_GENERATE_TOOL = "copy_generate"
COPY_GENERATE_BATCH_TOOL = "copy_generate_batch"
IMAGE_PROMPTS_GENERATE_TOOL = "image_prompts_generate"
TEXT_TRANSLATE_TOOL = "text_translate"


def _load_copy_subject(
    request: CopyGenerateRequest,
    scope: ContentCapabilityScope,
) -> tuple[dict[str, Any], str]:
    if request.draft_id:
        product = scope.products.load_draft_from_index(request.draft_id)
        if not _text(product.get("current_draft_id")):
            raise BusinessCapabilityError(
                "DRAFT_NOT_FOUND",
                "草稿不存在。",
            )
        requested_product_id = request.product_id
        loaded_product_id = _text(product.get("product_id"))
        if requested_product_id and requested_product_id != loaded_product_id:
            raise BusinessCapabilityError(
                "DRAFT_PRODUCT_MISMATCH",
                "草稿与商品不匹配。",
            )
        return product, request.draft_id
    product, error, _status = scope.products.load_required_product_from_body(
        {"product_id": request.product_id}
    )
    if error is not None:
        raise BusinessCapabilityError(
            _text(error.get("error_code")) or "PRODUCT_NOT_FOUND",
            _text(error.get("error")) or "商品不存在。",
        )
    return product, ""


def _copy_language(
    language: str,
    product: dict[str, Any],
    platform: str,
) -> str:
    if language:
        return language
    drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
    draft = drafts.get(platform) if isinstance(drafts.get(platform), dict) else {}
    draft_language = _text(draft.get("language"))
    return draft_language or _text(
        default_marketplace_site(platform).get("language")
    ) or "English"


@ai_tool(
    name=COPY_GENERATE_TOOL,
    description="为商品/草稿生成目标市场本地化文案并保存到草稿。",
    permission="content.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def copy_generate(
    request: CopyGenerateRequest,
    scope: Annotated[ContentCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> CopyGenerateResult:
    del execution
    product, draft_id = _load_copy_subject(request, scope)
    platform = _text(request.platform).lower()
    if platform not in PLATFORMS:
        raise BusinessCapabilityError(
            "COPY_PLATFORM_UNSUPPORTED",
            f"不支持的平台：{request.platform}",
        )
    result = generate_ai_copy_bundle(
        product,
        platform,
        platform,
        _copy_language(request.language, product, platform),
        request.mode or "rewrite",
        scope.app_config_loader(),
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise BusinessCapabilityError(
            "COPY_GENERATE_FAILED",
            _text(result.get("error") if isinstance(result, dict) else "")
            or "本地化文案生成失败。",
        )
    copy_record = {
        **result["copy"],
        "language": result["language"],
        "source_platform": result["source_platform"],
        "mode": result["mode"],
    }
    saved_product = save_copy_result(product, result["target_market"], copy_record)
    plan = apply_product_drafts_to_plan(
        saved_product,
        build_plan_for_platform(saved_product, platform),
    )
    listing = (
        plan.get("platforms", {})
        .get(platform_to_preset_key(platform), {})
        .get("listing", {})
    )
    return CopyGenerateResult(
        product_id=_text(saved_product.get("product_id")),
        draft_id=draft_id,
        platform=platform,
        target_market=_text(result.get("target_market")),
        language=_text(result.get("language")),
        mode=_text(result.get("mode")),
        copy_record=dict(copy_record),
        listing=dict(listing) if isinstance(listing, dict) else {},
    )


@ai_tool(
    name=COPY_GENERATE_BATCH_TOOL,
    description="批量为多个商品生成目标市场本地化文案。",
    permission="content.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def copy_generate_batch(
    request: CopyGenerateBatchRequest,
    scope: Annotated[ContentCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> CopyGenerateBatchResult:
    del execution
    result = batch_generate_copy_for_products(
        list(request.product_ids),
        request.platform or "mercadolibre",
        request.language,
        request.mode or "rewrite",
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise BusinessCapabilityError(
            "COPY_GENERATE_BATCH_FAILED",
            _text(result.get("error") if isinstance(result, dict) else "")
            or "批量文案生成失败。",
        )
    return CopyGenerateBatchResult(
        success_count=int(result.get("success_count") or 0),
        failed_count=int(result.get("failed_count") or 0),
        items=_dict_rows(result.get("items")),
    )


@ai_tool(
    name=IMAGE_PROMPTS_GENERATE_TOOL,
    description="基于商品文案与图片构建图片生成提示词包（纯计算）。",
    permission="content.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def image_prompts_generate(
    request: ImagePromptsGenerateRequest,
    scope: Annotated[ContentCapabilityScope, Injected()],
) -> ImagePromptsGenerateResult:
    product, error, _status = scope.products.load_required_product_from_body(
        {"product_id": request.product_id}
    )
    if error is not None:
        raise BusinessCapabilityError(
            _text(error.get("error_code")) or "PRODUCT_NOT_FOUND",
            _text(error.get("error")) or "商品不存在。",
        )
    prompt = build_image_prompt_pack(
        product,
        request.platform or "mercadolibre",
        list(request.selected_image_ids),
        request.include_bullets,
        request.include_description,
        request.target_language,
    )
    return ImagePromptsGenerateResult(
        prompt=_text(prompt),
        selected_image_ids=tuple(request.selected_image_ids),
    )


@ai_tool(
    name=TEXT_TRANSLATE_TOOL,
    description=(
        "把调用方明确提供的键值文本翻译为目标语言（不修改商品数据）；"
        "品牌、型号或标识符需要原样保留时放入 preserve_terms。"
    ),
    permission="content.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def text_translate(
    request: TextTranslateRequest,
    scope: Annotated[ContentCapabilityScope, Injected()],
) -> TextTranslateResult:
    del scope
    try:
        translations = translate_texts(
            request.target_language,
            dict(request.content),
            preserve_terms=request.preserve_terms,
        )
    except TranslationRequestError as exc:
        raise BusinessCapabilityError(
            "INVALID_TRANSLATION_REQUEST",
            str(exc) or "翻译请求无效。",
        ) from exc
    return TextTranslateResult(
        target_language=request.target_language,
        translations=dict(translations),
    )


CONTENT_AI_CAPABILITIES = (
    copy_generate,
    copy_generate_batch,
    image_prompts_generate,
    text_translate,
)


__all__ = [
    "CONTENT_AI_CAPABILITIES",
    "COPY_GENERATE_BATCH_TOOL",
    "COPY_GENERATE_TOOL",
    "ContentCapabilityScope",
    "ContentProductStore",
    "IMAGE_PROMPTS_GENERATE_TOOL",
    "TEXT_TRANSLATE_TOOL",
    "copy_generate",
    "copy_generate_batch",
    "image_prompts_generate",
    "text_translate",
]
