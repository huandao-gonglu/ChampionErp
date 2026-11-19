"""草稿套图准备：保护人工内容、复用原生图片批次，并在完成后采用真实结果。"""

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid4, uuid5

from erp_web.product_model.draft_image_model import draft_image_refs_from_pool
from erp_web.product_model.image_preparation import image_bindings, image_content_fingerprint, image_preparation_status
from erp_web.release_guard import release_guard
from erp_web.runtime_units.image_pool import append_images_to_product_pool
from erp_web.runtime_units.market_capability_support import assert_target_mutable
from erp_web.schemas.image_capabilities import DraftImagesPrepareResult
from erp_web.schemas.image_sets import MAX_IMAGE_SET_REFERENCES
from erp_web.services import image_set_service, image_translate_service
from erp_web.services.ai_model_errors import safe_model_error_text
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.image_batch_service import image_request_slots, run_image_batch
from erp_web.services.operation_availability import require_model_platform
from erp_web.services.ai_run_cancellation import check_cancellation


def _common_references(app_dir, product, draft):
    pool = {item["id"]: item for item in product.get("source", {}).get("image_pool", [])}
    refs = draft.get("images") or []

    def usable(item):
        path = image_set_service.reference_path(app_dir, item)
        return path is not None and 0 < path.stat().st_size <= image_translate_service.MAX_PROVIDER_IMAGE_BYTES

    if refs:
        refs = [ref for ref in refs if ref["asset_id"] in pool and usable(pool[ref["asset_id"]])]
    else:
        sku_assets = {row.get("image_asset_id") for row in product.get("sku_items", [])}
        sku_assets.update(asset for _, asset in image_bindings(product, draft, selected_only=False)["skus"])
        candidates = [item for item in pool.values()
                      if item["id"] not in sku_assets and not item.get("is_sku") and not item.get("sku")
                      and (not item.get("platforms") or draft["platform"] in item["platforms"])
                      and usable(item)]
        refs = draft_image_refs_from_pool({"source": {"image_pool": candidates}}, draft["platform"])

    # 每种用途先选一张，余下名额按草稿顺序补齐。
    by_role = {}
    extras = []
    for ref in refs:
        item = pool[ref["asset_id"]]
        role = ref["role"]
        if role in by_role:
            extras.append(item)
        else:
            by_role[role] = item
    primary = [by_role.pop("main")] if "main" in by_role else []
    return [*primary, *by_role.values(), *extras][:MAX_IMAGE_SET_REFERENCES]


def _generation_inputs(product, draft, reference_ids):
    # 同一素材 ID 可以重新导入。只保护本批真正使用的素材、目标和已选规格。
    selected = {row["sku_id"] for row in draft.get("sku_items", []) if row.get("selected")}
    active = {sku["id"] for sku in product.get("sku_items", []) if sku["id"] in selected and sku.get("active", True)}
    locations = {
        item["id"]: (item.get("path") or item.get("local_path"), item.get("content_sha256"))
        for item in product.get("source", {}).get("image_pool", []) if item["id"] in reference_ids
    }
    return image_content_fingerprint(product, draft), draft.get("platform"), draft.get("language"), active, locations


def _load(products, draft_id):
    draft = products.draft_record(draft_id)
    if not draft:
        raise BusinessCapabilityError("DRAFT_NOT_FOUND", "草稿不存在。")
    return products.load_product_from_index(draft["product_id"], ""), draft


def _response(draft_id, fingerprint, status, message, batch=None):
    return DraftImagesPrepareResult(
        draft_id=draft_id, status=status, fingerprint=fingerprint, message=message,
        batch_id=batch.conversation_id if batch else "",
        generated_count=sum(item.status == "success" for item in batch.items) if batch else 0,
        failed_count=sum(item.status == "failed" for item in batch.items) if batch else 0,
    )


def prepare_draft_images(request, *, context, operation_key=""):
    products = context.products
    draft_id = request.draft_id
    store = context.image_batches
    batch_id = "draft_image_prepare_" + (uuid5(NAMESPACE_URL, operation_key).hex if operation_key else uuid4().hex)
    with store.lock, products.mutation_scope({"draft_id": draft_id}):
        product, draft = _load(products, draft_id)
        for target in [draft, *draft.get("target_sites", [])]:
            assert_target_mutable(target)
        recorded = store.preparation(batch_id, arguments=request.model_dump_json())
        if recorded:
            return recorded
        state = image_preparation_status(product, draft)
        fingerprint = state["fingerprint"]
        if request.decision != "auto" and request.expected_fingerprint != fingerprint:
            raise BusinessCapabilityError("DRAFT_IMAGES_CHANGED", "当前图片与确认时不一致，请重新读取图片状态后决定。")
        previous_job = store.latest_preparation(draft_id)
        if previous_job and previous_job.status == "queued":
            if request.decision == "auto" and previous_job.fingerprint == fingerprint:
                return previous_job
            raise BusinessCapabilityError("DRAFT_IMAGES_BUSY", "草稿有套图任务正在处理，请先核对原批次。")
        if request.decision == "auto" and state["status"] in {"kept", "generated"}:
            return _response(draft_id, fingerprint, state["status"], "继续使用当前图片，内容准备无需重复执行；发布资源仍需独立检查。")
        if request.decision == "auto" and state["status"] == "needs_confirmation":
            return _response(draft_id, fingerprint, "needs_confirmation", "当前图片有手动修改或已有加工结果。请确认保留当前图片，还是重新生成共用主图和 SKU 整套图片；默认保留，其他草稿准备可继续。")
        context.image_batches.assert_draft_idle(draft_id)
        if request.decision == "keep":
            _, error, _ = products.save_draft_content(
                {"draft_id": draft_id, "updated_at": draft["updated_at"]},
                image_preparation={"mode": "kept", "fingerprint": fingerprint},
            )
            if error:
                raise BusinessCapabilityError("DRAFT_SAVE_FAILED", error["error"])
            return _response(draft_id, fingerprint, "kept", "已采用当前图片；后续仅检查上传、水印与发布链接。")
        if (request.decision == "auto" and previous_job and previous_job.fingerprint == fingerprint
                and previous_job.status == "failed"):
            return previous_job
        previous = context.image_batches.latest_for_draft(draft_id)
        if request.decision == "auto" and previous and previous.preparation_fingerprint == fingerprint:
            return _response(draft_id, fingerprint, "failed", "已有本组图片的套图任务，请核对原结果或明确重做；不会自动重新生成或计费。", batch=previous)
        bindings = image_bindings(product, draft)
        # 每组 SKU 只使用当前独立图片；没有独立图片的规格沿用共用套图。
        sku_ids = [id for id, asset in bindings["skus"] if asset]
        targets = image_set_service.sku_image_targets(product, draft, sku_ids)
        queued = DraftImagesPrepareResult(draft_id=draft_id, status="queued", batch_id=batch_id,
                                          fingerprint=fingerprint, message="套图已提交，可以继续准备其他资料。")
        store.start_preparation(queued, request.model_dump_json())

    def generate():
        try:
            result = _generate(request, context, product, draft, targets, fingerprint, batch_id)
        except Exception as exc:
            result = _response(draft_id, fingerprint, "failed", safe_model_error_text(str(exc)) or "图片准备失败。",
                               batch=store.get(batch_id))
        store.finish_preparation(result.model_copy(update={"batch_id": batch_id}))

    try:
        release_guard.start_thread("草稿套图", "draft-image-prepare", generate)
    except Exception:
        store.finish_preparation(queued.model_copy(update={"status": "failed", "message": "套图任务未能启动。"}))
        raise
    return queued


def _generate(request, context, product, draft, targets, fingerprint, batch_id):
    products = context.products
    draft_id = request.draft_id
    check_cancellation()
    sources = _common_references(context.paths.app_dir, product, draft)
    if not sources:
        return _response(draft_id, fingerprint, "needs_selection", "没有可用的共用参考图，请采集或上传一张可读取且不超过 20 MB 的商品图。")
    config = context.config.load_app_config()
    require_model_platform(config, "image.plan")
    require_model_platform(config, "image.translate")
    # 模型、文件读取、并发图片生成均在商品锁之外。
    pool = {item["id"]: item for item in product.get("source", {}).get("image_pool", [])}
    for target in targets:
        if target["source_id"] not in pool:
            raise BusinessCapabilityError("IMAGE_SOURCE_MISSING", "部分 SKU 原图缺失，请重新选择。")
        image_set_service.reference_content(context.paths.app_dir, [pool[target["source_id"]]])
    reference_ids = {item["id"] for item in sources} | {target["source_id"] for target in targets}
    inputs = _generation_inputs(product, draft, reference_ids)
    with image_request_slots:
        check_cancellation()
        workers = image_translate_service.check_batch_connection(context.paths.app_dir, config)
        check_cancellation()
        common_plan = image_set_service.plan_image_set(
            app_dir=context.paths.app_dir, config=config, messages=context.pydantic_messages,
            product=product, draft=draft, sources=sources, count=request.count, prompt=request.prompt,
        )
    plan = image_set_service.assemble_image_set(common_plan, targets)
    pages = {page.key: page for page in plan.pages}

    def process(source):
        result = image_set_service.generate_page(
            app_dir=context.paths.app_dir, config=config, product=product, draft=draft,
            plan=plan, page=pages[source["id"]],
        )
        for item in result.get("imagePoolItems", []):
            item["raw"] = {**item.get("raw", {}), "image_content_actor": "agent"}
        return result

    def persist(result):
        with products.mutation_scope({"product_id": product["product_id"]}):
            latest, _ = _load(products, draft_id)
            products.save_product(append_images_to_product_pool(latest, result["imagePoolItems"]))

    def before_create():
        check_cancellation()
        with products.mutation_scope({"draft_id": draft_id}):
            latest_product, latest = _load(products, draft_id)
            for target in [latest, *latest.get("target_sites", [])]:
                assert_target_mutable(target)
            if _generation_inputs(latest_product, latest, reference_ids) != inputs:
                raise BusinessCapabilityError("DRAFT_IMAGES_CHANGED", "草稿图片在规划期间已更新，未启动图片生成，请重新读取。")
            context.image_batches.assert_draft_idle(draft_id)

    run_image_batch(
        store=context.image_batches, messages=context.pydantic_messages, app_dir=context.paths.app_dir,
        product_id=product["product_id"], draft_id=draft_id, request_id=batch_id,
        prompt=f"为草稿生成共用主图和 SKU 套图：{plan.style}",
        sources=[{"id": page.key} for page in plan.pages], process=process, persist=persist,
        image_set=plan, preparation_fingerprint=fingerprint, before_create=before_create, max_workers=workers,
    )
    batch = context.image_batches.get(batch_id)
    if any(item.status != "success" or item.outcome_unknown for item in batch.items):
        reason = next((item.error for item in batch.items if item.error), "")
        return _response(draft_id, fingerprint, "failed", f"套图未全部生成，已保留当前图片。{reason}", batch=batch)
    check_cancellation()
    with context.image_batches.lock, products.mutation_scope({"draft_id": draft_id}):
        check_cancellation()
        latest_product, latest = _load(products, draft_id)
        for target in [latest, *latest.get("target_sites", [])]:
            assert_target_mutable(target)
        if _generation_inputs(latest_product, latest, reference_ids) != inputs:
            return _response(draft_id, fingerprint, "failed", "图片在生成期间已变化，生成结果已保留在素材池，未替换当前图片。", batch=batch)
        changed = deepcopy(latest)
        results = {item.source_id: item.result_asset_ids[0] for item in batch.items}
        changed["images"] = [{"asset_id": results[page.key], "role": page.role, "order": index}
                             for index, page in enumerate(page for page in plan.pages if page.scope == "common")]
        sku_assets = {id: results[page.key] for page in plan.pages if page.scope == "sku" for id in page.sku_ids}
        for row in changed["sku_items"]:
            if row["sku_id"] in sku_assets:
                row.setdefault("overrides", {})["image_asset_id"] = sku_assets[row["sku_id"]]
        prepared = image_content_fingerprint(latest_product, changed)
        _, error, _ = products.save_draft_content(changed, image_preparation={
            "mode": "generated", "fingerprint": prepared, "batch_id": batch_id,
        })
        if error:
            raise BusinessCapabilityError("DRAFT_SAVE_FAILED", error["error"])
    fingerprint = prepared
    return _response(draft_id, fingerprint, "generated", "已生成并采用共用主图及 SKU 套图，原图保留在素材池；发布资源仍需独立准备。", batch=batch)
