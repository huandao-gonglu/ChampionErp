from __future__ import annotations

"""图片路由所需的产品与图片领域入口。"""

from copy import deepcopy
from pathlib import Path
import hashlib
import json
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from erp_web.context import get_context
from erp_web.runtime_units.draft_image_prepare import prepare_draft_images
from erp_web.schemas.chat_conversations import is_global_id, reply_root
from erp_web.schemas.image_capabilities import DraftImagesPrepareRequest
from erp_web.schemas.requests import RequestValidationError
from erp_web.services import image_service, image_translate_service
from erp_web.services.image_batch_service import run_image_batch
from erp_web.runtime_units.image_processing import process_selected_images
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.desktop_connection_service import desktop_unavailable_reason
from erp_web.services.operation_availability import require_model_platform
from erp_web.schemas.image_sets import MAX_IMAGE_SET_REFERENCES, ImageSetPlan
from erp_web.services import image_set_service
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
    # 文件准备在锁外完成；合入最新素材池，不能覆盖另一草稿刚生成的资产。
    product_id = str(product["product_id"])
    with get_context().products.mutation_scope({"product_id": product_id}):
        latest = load_product_from_index(product_id, "")
        if latest.get("product_id") != product_id:
            raise RequestValidationError("商品已不存在。", status_code=404)
        saved = save_product(append_images_to_product_pool(latest, uploaded))
    return {**_saved_product_response(saved), "uploaded_image_ids": [item["id"] for item in uploaded]}, 200


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
        "draftsIndex": draft_result.get("draftsIndex") or load_drafts_index(scope="all"),
    }


def _run_images_payload(body: dict[str, Any], *, translate: bool, background: bool = False) -> ResponseWithStatus:
    product, error = _load_request_product(body)
    if error:
        return error
    context = get_context()
    result = process_selected_images(context, product, body, translate=translate, background=background)
    product_id = str(product["product_id"])
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


def continue_image_operation(body: dict[str, Any]) -> ResponseWithStatus:
    context = get_context()
    conversation_id, call_id = body["conversation_id"], body["tool_call_id"]
    root = reply_root(conversation_id) or conversation_id
    if not is_global_id(root) or context.conversation_titles.is_deleted(root):
        raise RequestValidationError("图片操作的原对话不可用。", status_code=404)
    receipt = context.agent_calls.receipt(conversation_id, call_id)
    output = json.loads(receipt["output_json"] or "null") if receipt else None
    error = (output or {}).get("error") or {} if isinstance(output, dict) else {}
    details = error.get("details") or {}
    reference = {"conversation_id": conversation_id, "tool_call_id": call_id}
    if (error.get("code") != "IMAGE_DESKTOP_REQUIRED" or details.get("desktop_image_action") != reference
            or receipt["tool_name"] not in {"image_translate", "image_edit", "draft_images_prepare"}):
        raise RequestValidationError("没有找到可继续的图片操作。", status_code=404)
    if reason := desktop_unavailable_reason():
        raise RequestValidationError(reason, status_code=409, error_code="DESKTOP_UNAVAILABLE")

    request = dict(details["request"])
    operation_key = f"{conversation_id}:{call_id}"
    if receipt["tool_name"] == "draft_images_prepare":
        fingerprint = request.pop("handoff_fingerprint")
        try:
            result = prepare_draft_images(DraftImagesPrepareRequest.model_validate(request), context=context,
                                          operation_key=operation_key, handoff_fingerprint=fingerprint)
        except BusinessCapabilityError as exc:
            raise RequestValidationError(str(exc), status_code=409, error_code=exc.code) from None
        return {"ok": result.status != "failed", "image_preparation": result.model_dump(), "message": result.message}, 200

    request["request_id"] = f"ai_image_{uuid5(NAMESPACE_URL, operation_key).hex}"
    product, error_response = _load_request_product(request)
    if error_response:
        return error_response
    result = process_selected_images(context, product, request, translate=receipt["tool_name"] == "image_translate",
                                     background=True, actor="ai")
    return {"ok": result["ok"], "image_batch": result["image_batch"], "message": result["message"]}, 200


def image_batch_payload(conversation_id: str) -> dict:
    batch = get_context().image_batches.get(conversation_id)
    return {"ok": True, "image_batch": batch.snapshot() if batch else None}


def draft_image_state_payload(body: dict[str, Any]) -> ResponseWithStatus:
    context = get_context()
    draft_id = str(body.get("draft_id") or "")
    batch = context.image_batches.latest_for_draft(draft_id)
    request_id = str(body.get("request_id") or "")
    if request_id:
        requested = context.image_batches.get(request_id)
        if requested and requested.draft_id != draft_id:
            return {"ok": False, "error": "图片任务不属于当前草稿。"}, 404
        # 若另一窗口先提交，返回仍在执行的批次，使当前窗口也进入只读状态。
        if requested or not (batch and context.image_batches.blocks_changes(batch)):
            batch = requested
    # 先固定进度，再读取草稿；终态回执不能搭配最后一次写入前的旧草稿。
    snapshot = batch.snapshot() if batch else None
    if known_version := body.get("known_updated_at"):
        if context.db.draft_updated_at(draft_id) == known_version:
            return {"ok": True, "unchanged": True, "image_batch": snapshot}, 200
        detail, error, status = context.products.load_draft_detail_from_index(draft_id, editor_only=True)
        return error or {**detail, "image_batch": snapshot}, status
    draft = context.products.draft_record(draft_id)
    if not draft:
        return {"ok": False, "error": "草稿不存在。"}, 404
    product = context.products.load_product_from_index(draft["product_id"], "")
    return {**_saved_product_response(product), "draft": draft,
            "productContext": context.products.draft_product_context(product),
            "image_batch": snapshot}, 200


def process_draft_images_payload(body: dict[str, Any]) -> ResponseWithStatus:
    if body.get("operation") not in ("translate", "edit"):
        raise RequestValidationError("请选择翻译图片或 AI 修改。")
    context = get_context()
    # 同一草稿的查询、保存和注册使用短锁；不在锁中等待模型。
    with context.image_batches.lock, context.products.mutation_scope({"draft_id": body.get("draft_id")}):
        draft = context.products.draft_record(str(body.get("draft_id") or ""))
        if not draft or draft.get("product_id") != body.get("product_id"):
            raise RequestValidationError("目标草稿不存在或不属于当前商品。")
        existing = context.image_batches.get(str(body.get("request_id") or ""))
        if existing:
            if existing.draft_id != draft["draft_id"]:
                raise RequestValidationError("图片请求标识已用于其他草稿。", status_code=409)
            return draft_image_state_payload(body)
        if body.get("updated_at") != draft.get("updated_at"):
            raise RequestValidationError("草稿已更新，请重新读取后处理。", status_code=409)
        product = context.products.load_product_from_index(draft["product_id"], "")
        defaults = {sku["id"]: sku.get("image_asset_id", "") for sku in product.get("sku_items", [])}
        current_ids = {ref["asset_id"] for ref in draft.get("images", [])}
        current_ids.update(row.get("overrides", {}).get("image_asset_id", defaults.get(row["sku_id"], ""))
                           for row in draft.get("sku_items", []))
        if not set(body.get("source_image_ids") or []).issubset(current_ids):
            raise RequestValidationError("所选图片已不是当前使用的图片，请重新选择。", status_code=409)
        return _run_images_payload({**body, "apply_to_draft": True, "draft_image_strategy": "replace_selected",
                                    "platform": draft["platform"], "language": draft.get("language")},
                                   translate=body["operation"] == "translate", background=True)


def _image_set_target(body):
    context = get_context()
    draft = context.products.draft_record(str(body.get("draft_id") or ""))
    if not draft or draft.get("product_id") != body.get("product_id"):
        raise RequestValidationError("目标草稿不存在或不属于当前商品。", status_code=404)
    if body.get("updated_at") != draft.get("updated_at"):
        raise RequestValidationError("草稿已更新，请重新读取后生成套图。", status_code=409)
    context.image_batches.assert_draft_idle(draft["draft_id"])
    product = context.products.load_product_from_index(draft["product_id"], "")
    return product, draft


def _image_set_sources(product, draft, ids, *, require_current=True, limit=MAX_IMAGE_SET_REFERENCES):
    if not ids or (limit is not None and len(ids) > limit) or len(set(ids)) != len(ids):
        raise RequestValidationError("请选择 1 至 12 张不重复的参考素材。")
    defaults = {sku["id"]: sku.get("image_asset_id", "") for sku in product.get("sku_items", [])}
    current = {ref["asset_id"] for ref in draft.get("images", [])}
    for row in draft.get("sku_items", []):
        override = row.get("overrides", {}).get("image_asset_id")
        current.add(defaults.get(row["sku_id"], "") if override is None else override)
    sources = image_translate_service.select_source_images(product, ids, draft["platform"])
    if (require_current and not set(ids).issubset(current)) or {item["id"] for item in sources} != set(ids):
        raise RequestValidationError("参考素材已不是当前使用的图片，请重新选择。", status_code=409)
    return sources


def plan_draft_image_set_payload(body: dict[str, Any]) -> ResponseWithStatus:
    context = get_context()
    with context.image_batches.lock, context.products.mutation_scope({"draft_id": body.get("draft_id")}):
        product, draft = _image_set_target(body)
        reference_ids = body.get("source_image_ids") or []
        sources = _image_set_sources(product, draft, reference_ids) if reference_ids else []
        try:
            sku_targets = image_set_service.sku_image_targets(product, draft, body.get("sku_ids") or [])
        except ValueError as exc:
            raise RequestValidationError(str(exc)) from None
        sku_sources = (_image_set_sources(product, draft, [target["source_id"] for target in sku_targets], limit=None)
                       if sku_targets else [])
    # 共用图由识图模型规划，独立 SKU 图直接使用草稿中的规格与原图关联。
    if sources:
        config = load_app_config()
        require_model_platform(config, "image.plan")
    try:
        for source in sku_sources:
            image_set_service.reference_content(_app_dir(), [source])
        prompt = (body.get("prompt") or "").strip()
        common_plan = None
        if sources:
            common_plan = image_set_service.plan_image_set(
                app_dir=_app_dir(), config=config,
                product=product, draft=draft, sources=sources, count=body.get("count", 6), prompt=prompt,
            )
        plan = image_set_service.assemble_image_set(common_plan, sku_targets, sku_prompt="" if common_plan else prompt)
    except ValueError as exc:
        raise RequestValidationError(str(exc)) from None
    with context.image_batches.lock, context.products.mutation_scope({"draft_id": draft["draft_id"]}):
        _image_set_target(body)
    return {"ok": True, "plan": plan.model_dump(), "updated_at": draft["updated_at"]}, 200


def generate_draft_image_set_payload(body: dict[str, Any]) -> ResponseWithStatus:
    context = get_context()
    fingerprint = hashlib.sha256(json.dumps({key: value for key, value in body.items() if key != "request_id"},
                                           sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with context.image_batches.lock, context.products.mutation_scope({"draft_id": body.get("draft_id")}):
        existing = context.image_batches.get(str(body.get("request_id") or ""))
        if existing:
            if (existing.draft_id != body.get("draft_id") or existing.product_id != body.get("product_id")
                    or not existing.image_set or existing.request_fingerprint != fingerprint):
                raise RequestValidationError("图片请求标识已用于其他操作。", status_code=409)
            return draft_image_state_payload(body)
        product, draft = _image_set_target(body)
        retained = {}
        base_id = str(body.get("base_request_id") or "")
        if base_id:
            base = context.image_batches.get(base_id)
            if not base or base.draft_id != draft["draft_id"] or not base.image_set:
                raise RequestValidationError("原套图任务不存在或不属于当前草稿。")
            if context.image_batches.latest_for_draft(draft["draft_id"]).conversation_id != base_id:
                raise RequestValidationError("套图任务已更新，请重新读取最新结果。", status_code=409)
            if context.image_batches.blocks_changes(base):
                raise RequestValidationError("原套图正在生成，请等任务结束后再重做。", status_code=409)
            plan = base.image_set
            retry_ids = body.get("retry_page_ids") or []
            if not retry_ids or not set(retry_ids).issubset({page.key for page in plan.pages}):
                raise RequestValidationError("请选择要重做的套图页面。")
            # 未选择的页面保留真实回执，不重新执行模型或虚构模型历史。
            retained = {item.source_id: item for item in base.items if item.source_id not in retry_ids}
        else:
            if body.get("retry_page_ids"):
                raise RequestValidationError("重做套图需要提供原任务。")
            try:
                plan = ImageSetPlan.model_validate(body.get("plan"))
            except ValidationError:
                raise RequestValidationError("套图方案无效，请检查统一风格、主图和每页参考素材。") from None
        ids = list(dict.fromkeys(id for page in plan.pages for id in page.source_image_ids))
        try:
            image_set_service.validate_sku_pages(plan, product, draft, retry=bool(base_id))
        except ValueError as exc:
            raise RequestValidationError(str(exc), status_code=409) from None
        sources = _image_set_sources(product, draft, ids, require_current=not base_id, limit=None)
    # 文件预检与配置读取在锁外；注册时再次核对草稿版本和任务归属。
    try:
        for source in sources:
            image_set_service.reference_content(_app_dir(), [source])
    except ValueError as exc:
        raise RequestValidationError(str(exc)) from None
    config = load_app_config()
    pages = {page.key: page for page in plan.pages}

    def process(source):
        result = image_set_service.generate_page(app_dir=_app_dir(), config=config, product=product,
                                                 draft=draft, plan=plan, page=pages[source["id"]])
        for item in result.get("imagePoolItems", []):
            item["raw"] = {**item.get("raw", {}), "image_content_actor": "user"}
        return result

    def persist(result):
        with context.products.mutation_scope({"product_id": product["product_id"]}):
            latest = context.products.load_product_from_index(product["product_id"], "")
            if latest.get("product_id") != product["product_id"]:
                raise ValueError("商品已不存在，结果未写入素材池。")
            save_product(append_images_to_product_pool(latest, result["imagePoolItems"]))

    with context.image_batches.lock, context.products.mutation_scope({"draft_id": draft["draft_id"]}):
        existing = context.image_batches.get(body["request_id"])
        if existing:
            if existing.draft_id != draft["draft_id"] or existing.request_fingerprint != fingerprint:
                raise RequestValidationError("图片请求标识已用于其他操作。", status_code=409)
            return draft_image_state_payload(body)
        _image_set_target(body)
        if base_id and context.image_batches.latest_for_draft(draft["draft_id"]).conversation_id != base_id:
            raise RequestValidationError("套图任务已更新，请重新读取最新结果。", status_code=409)
        run_image_batch(
            store=context.image_batches, app_dir=_app_dir(),
            product_id=product["product_id"], draft_id=draft["draft_id"],
            request_id=body["request_id"], request_fingerprint=fingerprint,
            prompt=f"生成 {len(plan.pages)} 张套图：{plan.style}",
            sources=[{"id": page.key} for page in plan.pages], process=process, persist=persist,
            background=True, image_set=plan, retained_items=retained,
            prepare_workers=lambda: image_translate_service.check_batch_connection(context.paths.app_dir, config),
            before_create=lambda: require_model_platform(config, "image.translate"),
        )
        return draft_image_state_payload(body)


ImagePayloadHandler = Callable[[dict[str, Any]], ResponseWithStatus]
IMAGE_PAYLOAD_HANDLERS: dict[str, ImagePayloadHandler] = {
    "/api/draft-images/plan-set": plan_draft_image_set_payload,
    "/api/draft-images/generate-set": generate_draft_image_set_payload,
    "/api/draft-images/state": draft_image_state_payload,
    "/api/draft-images/process": process_draft_images_payload,
    "/api/image-processing/continue": continue_image_operation,
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
