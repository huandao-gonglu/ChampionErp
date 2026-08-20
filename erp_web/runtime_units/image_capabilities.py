from __future__ import annotations

"""图片池维护与图片翻译/编辑 Capability（本地写入）。

领域逻辑仍由 ``image_service`` / ``image_translate_service`` /
``image_pool`` 拥有；Capability 只做类型化编排，与 HTTP facade 复用同一
领域函数。
"""

from dataclasses import dataclass
from typing import Annotated, Any

from erp_web.context import AppContext
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.image_capabilities import (
    ImageEditRequest,
    ImageGenerationResult,
    ImagePoolActionRequest,
    ImagePoolActionResult,
    ImagePoolSaveRequest,
    ImagePoolSaveResult,
    ImagePoolSyncGeneratedRequest,
    ImagePoolSyncGeneratedResult,
    ImagePoolUploadRequest,
    ImagePoolUploadResult,
    ImageTranslateRequest,
)
from erp_web.services import image_service, image_translate_service
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.runtime_units.image_pool import (
    append_images_to_product_pool,
    apply_service_image_pool,
    current_image_pool,
    save_image_pool_for_product,
    sync_generated_images_into_pool,
)
from erp_web.stores.product_store import normalize_product_fields


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict_rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _main_image_ids(product: dict[str, Any]) -> list[str]:
    """从可信商品图片池选择主图；无主图标记时按选择状态与顺序回退。"""

    pool = [dict(item) for item in current_image_pool(product)]
    candidates = [
        item
        for item in pool
        if _text(item.get("id"))
        and _text(item.get("status")).lower() != "empty"
    ]
    if not candidates:
        return []
    candidates.sort(
        key=lambda item: (
            0 if bool(item.get("is_main")) else 1,
            0 if bool(item.get("selected")) else 1,
            int(item.get("order") or 0),
        )
    )
    return [_text(candidates[0].get("id"))]


@dataclass(frozen=True)
class ImageCapabilityScope:
    """图片能力的可信依赖边界。"""

    context: AppContext


def _load_product(
    scope: ImageCapabilityScope,
    product_id: str,
) -> dict[str, Any]:
    product = scope.context.products.load_product_from_index(product_id, "")
    loaded_id = _text(product.get("product_id"))
    if loaded_id != product_id:
        raise BusinessCapabilityError(
            "PRODUCT_NOT_FOUND",
            f"商品不存在：{product_id}",
        )
    return normalize_product_fields(product)


def _save_product(
    scope: ImageCapabilityScope,
    product: dict[str, Any],
) -> dict[str, Any]:
    return scope.context.products.save_product(product)


IMAGE_POOL_UPLOAD_TOOL = "image_pool_upload"
IMAGE_POOL_SAVE_TOOL = "image_pool_save"
IMAGE_POOL_ACTION_TOOL = "image_pool_action"
IMAGE_POOL_SYNC_GENERATED_TOOL = "image_pool_sync_generated"
IMAGE_TRANSLATE_TOOL = "image_translate"
IMAGE_EDIT_TOOL = "image_edit"


@ai_tool(
    name=IMAGE_POOL_UPLOAD_TOOL,
    description="解码上传的图片数据并加入商品图片池。",
    permission="image.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def image_pool_upload(
    request: ImagePoolUploadRequest,
    scope: Annotated[ImageCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ImagePoolUploadResult:
    del execution
    product = _load_product(scope, request.product_id)
    app_dir = scope.context.paths.app_dir
    uploaded = image_service.upload_images(
        app_dir,
        [dict(item) for item in request.uploads],
        request.product_id,
    )
    if not uploaded:
        raise BusinessCapabilityError(
            "IMAGE_UPLOAD_DECODE_FAILED",
            "上传图片失败，未解码成功。",
        )
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    pool = image_service.add_images(
        source.get("image_pool") if isinstance(source.get("image_pool"), list) else [],
        uploaded,
        app_dir,
    )
    saved = _save_product(scope, apply_service_image_pool(product, pool))
    return ImagePoolUploadResult(
        product_id=request.product_id,
        uploaded_count=len(uploaded),
        image_pool=_dict_rows(current_image_pool(saved)),
    )


@ai_tool(
    name=IMAGE_POOL_SAVE_TOOL,
    description="用给定图片池整体覆盖商品图片池。",
    permission="image.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def image_pool_save(
    request: ImagePoolSaveRequest,
    scope: Annotated[ImageCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ImagePoolSaveResult:
    del execution
    _load_product(scope, request.product_id)
    pool = image_service.normalize_pool(
        [dict(item) for item in request.image_pool],
        scope.context.paths.app_dir,
    )
    result = save_image_pool_for_product(request.product_id, pool)
    if not isinstance(result, dict) or not result.get("ok"):
        raise BusinessCapabilityError(
            "IMAGE_POOL_SAVE_FAILED",
            _text(result.get("error") if isinstance(result, dict) else "")
            or "图片池保存失败。",
        )
    saved_product = result.get("product") if isinstance(result.get("product"), dict) else {}
    return ImagePoolSaveResult(
        product_id=request.product_id,
        image_pool=_dict_rows(result.get("imagePool") or current_image_pool(saved_product)),
    )


@ai_tool(
    name=IMAGE_POOL_ACTION_TOOL,
    description="对商品图片池执行动作（选择、删除、过滤等）。",
    permission="image.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def image_pool_action(
    request: ImagePoolActionRequest,
    scope: Annotated[ImageCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ImagePoolActionResult:
    del execution
    product = _load_product(scope, request.product_id)
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    action = _text(request.action).lower()
    updated = image_service.apply_image_action(
        scope.context.paths.app_dir,
        pool,
        action,
        {**dict(request.params), "product_id": request.product_id},
    )
    if action == "filter":
        return ImagePoolActionResult(
            product_id=request.product_id,
            action=action,
            persisted=False,
            image_pool=_dict_rows(updated),
        )
    saved = _save_product(scope, apply_service_image_pool(product, updated))
    return ImagePoolActionResult(
        product_id=request.product_id,
        action=action,
        persisted=True,
        image_pool=_dict_rows(current_image_pool(saved)),
    )


@ai_tool(
    name=IMAGE_POOL_SYNC_GENERATED_TOOL,
    description="把本地生成目录中的图片同步进商品图片池。",
    permission="image.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def image_pool_sync_generated(
    request: ImagePoolSyncGeneratedRequest,
    scope: Annotated[ImageCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ImagePoolSyncGeneratedResult:
    del execution
    product = _load_product(scope, request.product_id)
    saved = _save_product(scope, sync_generated_images_into_pool(product))
    return ImagePoolSyncGeneratedResult(
        product_id=request.product_id,
        image_pool=_dict_rows(current_image_pool(saved)),
    )


def _apply_generated_result(
    scope: ImageCapabilityScope,
    product: dict[str, Any],
    request: ImageTranslateRequest | ImageEditRequest,
    result: dict[str, Any],
) -> ImageGenerationResult:
    if not result.get("ok"):
        raise BusinessCapabilityError(
            _text(result.get("error_code")) or "IMAGE_GENERATE_FAILED",
            _text(result.get("error") or result.get("message"))
            or "图片生成失败。",
        )
    items = _dict_rows(result.get("imagePoolItems"))
    updated = append_images_to_product_pool(product, list(items))
    main_image_id = ""
    if isinstance(request, ImageEditRequest) and request.set_as_main and items:
        main_image_id = _text(items[0].get("id"))
        if not main_image_id:
            raise BusinessCapabilityError(
                "IMAGE_GENERATED_ID_MISSING",
                "生成图片缺少稳定 ID，不能设为主图。",
            )
        updated = apply_service_image_pool(
            updated,
            image_service.set_main_image(
                [dict(item) for item in current_image_pool(updated)],
                main_image_id,
                scope.context.paths.app_dir,
            ),
        )
    saved = _save_product(scope, updated)
    draft_id = ""
    if request.apply_to_draft:
        draft_result, draft_error, _status = scope.context.products.apply_image_assets_to_draft(
            request.draft_id,
            list(items),
            request.draft_image_strategy or "append",
        )
        if draft_error is not None:
            raise BusinessCapabilityError(
                _text(draft_error.get("error_code")) or "IMAGE_DRAFT_APPLY_FAILED",
                _text(draft_error.get("error")) or "图片写入草稿失败。",
            )
        draft = (
            draft_result.get("draft")
            if isinstance(draft_result.get("draft"), dict)
            else {}
        )
        draft_id = _text(draft.get("draft_id")) or request.draft_id
    return ImageGenerationResult(
        product_id=_text(saved.get("product_id")) or request.product_id,
        generated_count=len(items),
        image_pool_items=items,
        draft_id=draft_id,
        main_image_id=main_image_id,
        message=_text(result.get("message")),
    )


@ai_tool(
    name=IMAGE_TRANSLATE_TOOL,
    description="翻译/重绘商品图片文案并写回图片池（AI 生成）。",
    permission="image.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def image_translate(
    request: ImageTranslateRequest,
    scope: Annotated[ImageCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ImageGenerationResult:
    del execution
    product = _load_product(scope, request.product_id)
    result = image_translate_service.translate_images(
        scope.context.paths.app_dir,
        product,
        scope.context.config.load_app_config(),
        target_language=request.target_language or "es",
        platform=request.platform or "mercadolibre",
        image_ids=list(request.source_image_ids),
        mode=request.mode or "translate",
    )
    return _apply_generated_result(scope, product, request, result)


@ai_tool(
    name=IMAGE_EDIT_TOOL,
    description=(
        "按提示词编辑商品图片并写回图片池（AI 生成）；"
        "source_image_ids 省略时自动使用商品主图；用户要求替换或设为主图时"
        "必须传 set_as_main=true。"
    ),
    permission="image.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def image_edit(
    request: ImageEditRequest,
    scope: Annotated[ImageCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ImageGenerationResult:
    del execution
    product = _load_product(scope, request.product_id)
    source_image_ids = (
        list(request.source_image_ids)
        if request.source_image_ids
        else _main_image_ids(product)
    )
    result = image_translate_service.edit_images(
        scope.context.paths.app_dir,
        product,
        scope.context.config.load_app_config(),
        prompt=request.prompt,
        platform=request.platform or "mercadolibre",
        image_ids=source_image_ids,
    )
    return _apply_generated_result(scope, product, request, result)


IMAGE_AI_CAPABILITIES = (
    image_pool_upload,
    image_pool_save,
    image_pool_action,
    image_pool_sync_generated,
    image_translate,
    image_edit,
)


__all__ = [
    "IMAGE_AI_CAPABILITIES",
    "IMAGE_EDIT_TOOL",
    "IMAGE_POOL_ACTION_TOOL",
    "IMAGE_POOL_SAVE_TOOL",
    "IMAGE_POOL_SYNC_GENERATED_TOOL",
    "IMAGE_POOL_UPLOAD_TOOL",
    "IMAGE_TRANSLATE_TOOL",
    "ImageCapabilityScope",
    "image_edit",
    "image_pool_action",
    "image_pool_save",
    "image_pool_sync_generated",
    "image_pool_upload",
    "image_translate",
]
