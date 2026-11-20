"""页面与对话共用的指定图片处理及草稿替换。"""

from copy import deepcopy
from hashlib import sha256
import json

from .image_pool import (
    append_images_to_product_pool,
    apply_service_image_pool,
    current_image_pool,
)
from erp_web.schemas.requests import RequestValidationError
from erp_web.services import image_service, image_translate_service
from erp_web.services.image_batch_service import run_image_batch
from erp_web.services.operation_availability import require_model_platform


def _draft_bindings(draft, product):
    defaults = {sku["id"]: sku.get("image_asset_id", "") for sku in product.get("sku_items", [])}
    return deepcopy([draft.get("images", []),
                     [(row["sku_id"], row.get("overrides", {}).get("image_asset_id", defaults.get(row["sku_id"], "")))
                      for row in draft.get("sku_items", [])]])


def process_selected_images(context, product, body, *, translate, background=False, actor="user"):
    product_id = str(product["product_id"])
    draft_id = str(body.get("draft_id") or "") if body.get("apply_to_draft") else ""
    platform = str(body.get("platform") or "mercadolibre")
    language = str(body.get("language") or body.get("target_language") or "es")
    mode = str(body.get("mode") or "translate") if translate else "edit"
    strategy = str(body.get("draft_image_strategy") or "append")
    ids = list(dict.fromkeys(body.get("source_image_ids") or []))
    if not ids:
        raise RequestValidationError("请先选定要处理的图片。")

    store = context.image_batches
    request_id = str(body.get("request_id") or "")
    config = context.config.load_app_config()
    app_dir = context.paths.app_dir
    prompt = (image_translate_service.build_translate_prompt(product, language, app_dir=app_dir, app_config=config)
              if translate else str(body.get("prompt") or "").strip())
    if not prompt:
        raise RequestValidationError("图生图提示词不能为空。")

    with store.lock, context.products.mutation_scope({"product_id": product_id}):
        existing = store.get(request_id) if request_id else None
        if body.get("apply_to_draft") and not draft_id:
            raise RequestValidationError("请选择要更新图片的草稿。")
        draft = context.products.draft_record(draft_id) if draft_id else None
        if draft_id and (not draft or draft.get("product_id") != product_id):
            raise RequestValidationError("目标草稿不存在或不属于当前商品。")
        expected_bindings = _draft_bindings(draft, product) if draft else None
        if draft and not existing:
            if body.get("updated_at") and draft.get("updated_at") != body["updated_at"]:
                raise RequestValidationError("草稿已更新，请重新核对要处理的图片。", status_code=409)
            if strategy == "replace_selected":
                current_ids = {ref["asset_id"] for ref in expected_bindings[0]}
                current_ids.update(asset_id for _, asset_id in expected_bindings[1])
                if not set(ids).issubset(current_ids):
                    raise RequestValidationError("部分所选图片已不是草稿当前使用的图片。", status_code=409)

        if existing:
            # 旧批次可能已替换草稿引用，恢复时直接比较原图范围与业务请求。
            sources = [{"id": item.source_id} for item in existing.items]
            if set(ids) != {item["id"] for item in sources}:
                raise RequestValidationError("图片请求标识已用于其他图片范围。", status_code=409)
            prompt = existing.prompt if translate else prompt
        else:
            sources = image_translate_service.select_source_images(product, ids, platform)
            if {str(item["id"]) for item in sources} != set(ids):
                raise RequestValidationError("部分源图片不存在或未被选中，请重新选择。")
            if draft_id:
                store.assert_draft_idle(draft_id)

    applied = False
    main_image_id = ""

    def process(source):
        options = {"platform": platform, "image_ids": [source["id"]]}
        if translate:
            result = image_translate_service.translate_images(app_dir, product, config,
                target_language=language, mode=mode, **options)
            for index, item in enumerate(result.get("imagePoolItems") or []):
                item["is_main"] = source["id"] == sources[0]["id"] and index == 0
        else:
            result = image_translate_service.edit_images(app_dir, product, config, prompt=prompt, **options)
        # 请求一次只交给供应商一张确定的原图，逐张落库和替换都沿用该标识。
        for item in result.get("imagePoolItems") or []:
            item["derived_from_id"] = str(source["id"])
            item["raw"] = {**item.get("raw", {}), "image_content_actor": actor}
        return result

    def persist(result):
        nonlocal expected_bindings, applied, main_image_id
        items = result["imagePoolItems"]
        with context.products.mutation_scope({"product_id": product_id}):
            latest = context.products.load_product_from_index(product_id, "")
            if latest.get("product_id") != product_id:
                raise ValueError("商品已不存在，结果未写入素材池。")
            updated = append_images_to_product_pool(latest, items)
            current = context.products.draft_record(draft_id) if draft_id else None
            if draft_id and (not current or _draft_bindings(current, latest) != expected_bindings
                             or current["platform"] != draft["platform"] or current.get("language") != draft.get("language")):
                context.products.save_product(updated)
                raise ValueError("草稿图片或销售目标已更新，生成结果仅保留在素材池。")
            if body.get("set_as_main") and items[0]["derived_from_id"] == sources[0]["id"]:
                main_image_id = str(items[0]["id"])
                updated = apply_service_image_pool(updated, image_service.set_main_image(
                    current_image_pool(updated), main_image_id, app_dir))
            context.products.save_product(updated)
            if draft_id:
                _, error, _ = context.products.apply_image_assets_to_draft(
                    draft_id, items, "append" if strategy == "replace_all" and applied else strategy)
                if error:
                    raise ValueError(error["error"])
                applied = True
                expected_bindings = _draft_bindings(context.products.draft_record(draft_id), latest)

    signature = sha256(json.dumps(
        [mode, language if translate else "", platform, strategy, bool(body.get("set_as_main")), actor],
        ensure_ascii=False).encode()).hexdigest()

    def before_create():
        require_model_platform(config, "image.translate")
        if draft_id:
            with context.products.mutation_scope({"product_id": product_id}):
                store.assert_draft_idle(draft_id)
                current = context.products.draft_record(draft_id)
                if not current or current.get("updated_at") != draft.get("updated_at"):
                    raise RequestValidationError("草稿已更新，请重新核对要处理的图片。", status_code=409)

    result = run_image_batch(
        store=store, app_dir=app_dir, product_id=product_id, prompt=prompt, sources=sources,
        process=process, persist=persist, draft_id=draft_id, request_id=request_id, background=background,
        request_fingerprint=signature if not existing or existing.request_fingerprint else "",
        prepare_workers=lambda: image_translate_service.check_batch_connection(app_dir, config),
        before_create=before_create,
    )
    result.update(prompt=prompt, platform=platform, mode=mode, source_image_ids=[item["id"] for item in sources])
    if translate:
        result.update(language=language, target_language=language)
    if main_image_id:
        result["main_image_id"] = main_image_id
    return result
