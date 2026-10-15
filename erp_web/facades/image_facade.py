from __future__ import annotations

"""图片路由所需的产品与图片领域入口。"""

from pathlib import Path
from typing import Any, Callable

from erp_web.context import get_context
from erp_web.schemas.requests import RequestValidationError
from erp_web.services import image_service, image_translate_service
from erp_web.services.image_batch_service import run_image_batch
from erp_web.runtime_units.image_pool import (
    append_images_to_product_pool,
    apply_service_image_pool,
    current_image_pool,
    current_source_images,
    save_image_pool_for_product,
    sync_generated_images_into_pool,
)
from erp_web.stores.product_store import normalize_product_fields

ResponseWithStatus = tuple[dict[str, Any], int]


def load_app_config() -> dict[str, Any]:
    return get_context().config.load_app_config()


def load_drafts_index(
    scope: str = "active",
) -> list[dict[str, Any]]:
    return get_context().products.load_drafts_index(scope)


def load_product_from_index(
    product_id: str = "",
    file_path: str = "",
) -> dict[str, Any]:
    return get_context().products.load_product_from_index(
        product_id,
        file_path,
    )


def load_products_index() -> list[dict[str, Any]]:
    return get_context().products.load_products_index()


def save_product(product: dict[str, Any]) -> dict[str, Any]:
    return get_context().products.save_product(product)


def apply_image_assets_to_draft(
    draft_id: str,
    created_items: list[dict[str, Any]],
    strategy: str = "append",
) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    return get_context().products.apply_image_assets_to_draft(
        draft_id,
        created_items,
        strategy,
    )


def _app_dir() -> Path:
    """在调用边界读取当前上下文，避免测试或嵌入场景中的路径快照。"""

    return get_context().paths.app_dir


def _load_request_product(
    body: dict[str, Any],
) -> tuple[dict[str, Any], ResponseWithStatus | None]:
    product_id = str(body.get("product_id") or "").strip()
    if not product_id:
        return {}, ({"ok": False, "error": "product_id 不能为空"}, 400)
    product = load_product_from_index(product_id, "")
    loaded_id = str(product.get("product_id") or "").strip()
    if loaded_id != product_id:
        return {}, (
            {"ok": False, "error": "商品不存在", "product_id": product_id},
            404,
        )
    return normalize_product_fields(product), None


def _saved_product_response(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "product": product,
        "imagePool": current_image_pool(product),
        "sourceImages": current_source_images(product),
        "productsIndex": load_products_index(),
    }


def upload_images_payload(body: dict[str, Any]) -> ResponseWithStatus:
    product, error = _load_request_product(body)
    if error:
        return error
    uploads = body.get("uploads") or []
    uploads = [uploads] if isinstance(uploads, dict) else uploads
    if not isinstance(uploads, list) or not uploads:
        raise RequestValidationError("缺少上传图片")
    app_dir = _app_dir()
    uploaded = image_service.upload_images(
        app_dir,
        uploads,
        str(product.get("product_id") or ""),
    )
    if not uploaded:
        raise RequestValidationError("上传图片失败，未解码成功")
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    pool = image_service.add_images(
        source.get("image_pool") if isinstance(source.get("image_pool"), list) else [],
        uploaded,
        app_dir,
    )
    saved = save_product(apply_service_image_pool(product, pool))
    return _saved_product_response(saved), 200


def save_image_pool_payload(body: dict[str, Any]) -> ResponseWithStatus:
    product, error = _load_request_product(body)
    if error:
        return error
    pool = image_service.normalize_pool(
        body.get("image_pool") if isinstance(body.get("image_pool"), list) else [],
        _app_dir(),
    )
    result = save_image_pool_for_product(
        str(product.get("product_id") or ""),
        pool,
    )
    return result, 200 if result.get("ok") else 400


def apply_image_action_payload(body: dict[str, Any]) -> ResponseWithStatus:
    product, error = _load_request_product(body)
    if error:
        return error
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    action = str(body.get("action") or "").strip().lower()
    updated = image_service.apply_image_action(
        _app_dir(),
        pool,
        action,
        {**body, "product_id": product.get("product_id")},
    )
    if action == "filter":
        return {"ok": True, "imagePool": updated}, 200
    saved = save_product(apply_service_image_pool(product, updated))
    return _saved_product_response(saved), 200


def sync_generated_images_payload(body: dict[str, Any]) -> ResponseWithStatus:
    product, error = _load_request_product(body)
    if error:
        return error
    saved = save_product(sync_generated_images_into_pool(product))
    return _saved_product_response(saved), 200


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _apply_result_to_product(
    product: dict[str, Any],
    body: dict[str, Any],
    result: dict[str, Any],
) -> ResponseWithStatus:
    if not result.get("ok"):
        return {
            "ok": False,
            "product": product,
            "imagePool": current_image_pool(product),
            **result,
        }, 200
    items = result.get("imagePoolItems")
    items = items if isinstance(items, list) else []
    saved = save_product(append_images_to_product_pool(product, items))
    draft_result: dict[str, Any] = {}
    if _truthy(body.get("apply_to_draft")):
        draft_result, draft_error, draft_status = apply_image_assets_to_draft(
            str(body.get("draft_id") or body.get("draftId") or ""),
            items,
            str(
                body.get("draft_image_strategy")
                or body.get("draftImageStrategy")
                or "append"
            ),
        )
        if draft_error:
            return {
                **result,
                **draft_error,
                "product": saved,
                "productsIndex": load_products_index(),
            }, draft_status
    return _generated_image_response(saved, result, draft_result), 200


def _generated_image_response(
    saved: dict[str, Any],
    result: dict[str, Any],
    draft_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        **result,
        "product": saved,
        "imagePool": current_image_pool(saved),
        "sourceImages": current_source_images(saved),
        "productsIndex": load_products_index(),
        "draft": draft_result.get("draft"),
        "productContext": draft_result.get("productContext"),
        "draftsIndex": draft_result.get("draftsIndex") or load_drafts_index(),
    }


def _run_images_payload(body: dict[str, Any], *, translate: bool) -> ResponseWithStatus:
    product, error = _load_request_product(body)
    if error:
        return error
    context = get_context()
    platform = str(body.get("platform") or "mercadolibre")
    ids = body.get("source_image_ids") or []
    if not ids:
        raise RequestValidationError("请先勾选要处理的图片。")
    sources = image_translate_service.select_source_images(product, ids, platform)
    if len({str(item["id"]) for item in sources}) != len(set(ids)):
        raise RequestValidationError("部分源图片不存在或未被选中，请重新选择。")
    config = load_app_config()
    language = str(body.get("language") or body.get("target_language") or "es")
    prompt = (image_translate_service.build_translate_prompt(product, language, app_dir=_app_dir(), app_config=config)
              if translate else str(body.get("prompt") or "").strip())
    if not prompt:
        raise RequestValidationError("图生图提示词不能为空。")
    product_id = str(product["product_id"])
    applied_items: list[dict[str, Any]] = []
    if _truthy(body.get("apply_to_draft")):
        draft = context.products.draft_record(str(body.get("draft_id") or ""))
        if not draft or draft.get("product_id") != product_id:
            raise RequestValidationError("目标草稿不存在或不属于当前商品。")

    def process(source):
        options = {"platform": platform, "image_ids": [source["id"]]}
        if translate:
            result = image_translate_service.translate_images(_app_dir(), product, config,
                target_language=language, mode=str(body.get("mode") or "translate"), **options)
            # 单张调用内部编号从 0 开始；主图身份仍按原批次确定，不能每张都变主图。
            for index, item in enumerate(result.get("imagePoolItems") or []):
                item["is_main"] = source["id"] == sources[0]["id"] and index == 0
            return result
        return image_translate_service.edit_images(_app_dir(), product, config, prompt=prompt, **options)

    def persist(result):
        with context.products.mutation_scope({"product_id": product_id}):
            latest = context.products.load_product_from_index(product_id, "")
            if latest.get("product_id") != product_id:
                raise ValueError("商品已不存在，结果未写入素材池。")
            apply_body = dict(body)
            if body.get("draft_image_strategy") == "replace_all" and applied_items:
                apply_body["draft_image_strategy"] = "append"
            payload, status = _apply_result_to_product(latest, apply_body, result)
            if status >= 400 or not payload.get("ok"):
                raise RuntimeError(payload.get("error") or "图片保存失败。")
            applied_items.extend(result["imagePoolItems"])

    result = run_image_batch(
        store=context.image_batches, messages=context.pydantic_messages,
        app_dir=_app_dir(), product_id=product_id, prompt=prompt,
        sources=sources, process=process, persist=persist,
    )
    result.update(prompt=prompt, platform=platform, mode=str(body.get("mode") or "translate") if translate else "edit",
                  source_image_ids=[item["id"] for item in sources])
    if translate:
        result.update(language=language, target_language=language)
    saved = context.products.load_product_from_index(product_id, "")
    draft = context.products.draft_record(str(body.get("draft_id") or "")) if _truthy(body.get("apply_to_draft")) else None
    response = _generated_image_response(saved, result, {
        "draft": draft, "productContext": context.products.draft_product_context(saved) if draft else None,
    })
    # HTTP 200 保留已落库成功项；业务 ok 和计数准确表达全部失败/部分成功。
    return {**response, "ok": result["ok"], "error": "" if result["ok"] else result["message"]}, 200


def translate_images_payload(body: dict[str, Any]) -> ResponseWithStatus:
    return _run_images_payload(body, translate=True)


def edit_images_payload(body: dict[str, Any]) -> ResponseWithStatus:
    return _run_images_payload(body, translate=False)


def image_batch_payload(conversation_id: str) -> dict:
    batch = get_context().image_batches.get(conversation_id)
    return {"ok": True, "image_batch": batch.snapshot() if batch else None}


ImagePayloadHandler = Callable[[dict[str, Any]], ResponseWithStatus]
IMAGE_PAYLOAD_HANDLERS: dict[str, ImagePayloadHandler] = {
    "/api/image-pool/upload": upload_images_payload,
    "/api/image-pool/save": save_image_pool_payload,
    "/api/image-pool/action": apply_image_action_payload,
    "/api/image-pool/sync-generated": sync_generated_images_payload,
    "/api/image-translate": translate_images_payload,
    "/api/image-edit": edit_images_payload,
}


def handle_image_payload(
    path: str,
    body: dict[str, Any],
) -> ResponseWithStatus | None:
    handler = IMAGE_PAYLOAD_HANDLERS.get(path)
    return handler(body) if handler else None


__all__ = [
    "append_images_to_product_pool",
    "apply_image_assets_to_draft",
    "apply_service_image_pool",
    "current_image_pool",
    "current_source_images",
    "load_app_config",
    "load_drafts_index",
    "load_product_from_index",
    "load_products_index",
    "normalize_product_fields",
    "save_image_pool_for_product",
    "save_product",
    "sync_generated_images_into_pool",
    "IMAGE_PAYLOAD_HANDLERS",
    "apply_image_action_payload",
    "edit_images_payload",
    "image_batch_payload",
    "handle_image_payload",
    "save_image_pool_payload",
    "sync_generated_images_payload",
    "translate_images_payload",
    "upload_images_payload",
]
