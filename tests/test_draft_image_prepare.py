"""草稿图片策略：手动保护、版本确认、套图复用与原素材保留。"""

from copy import deepcopy
from pathlib import Path
from time import monotonic, sleep
from threading import Event

import pytest

from erp_web.context import get_context
from erp_web.facades import image_facade, product_facade
from erp_web.product_model.image_preparation import image_preparation_status
from erp_web.release_guard import release_guard
from erp_web.runtime_units.draft_image_prepare import prepare_draft_images
from erp_web.schemas.image_capabilities import DraftImagesPrepareRequest
from erp_web.schemas.image_sets import ImageSetPlan
from erp_web.services import image_set_service
from erp_web.services.capability_errors import BusinessCapabilityError
from tests.test_image_sets import setup_sources, plan, image_model
from tests.test_draft_image_workbench import request as edit_request, finish


def prepare(draft, **kwargs):
    submitted = prepare_draft_images(DraftImagesPrepareRequest(draft_id=draft["draft_id"], count=3, **kwargs),
                                     context=get_context())
    return wait_result(submitted)


def wait_result(result):
    deadline = monotonic() + 10
    while result.status == "queued":
        assert monotonic() < deadline, "套图任务未结束"
        sleep(0.01)
        result = get_context().image_batches.preparation(result.batch_id)
    return result


def state(draft):
    context = get_context()
    draft = context.products.draft_record(draft["draft_id"])
    product = context.products.load_product_from_index(draft["product_id"], "")
    return image_preparation_status(product, draft)


def test_manual_replacement_is_protected_but_order_and_sku_selection_are_not(app_dir):
    product, draft, _ = setup_sources(app_dir)
    context = get_context()
    changed = deepcopy(draft)
    changed["images"].append({"asset_id": "pink", "role": "detail", "order": 1})
    # 夹具装配不是用户编辑。
    context.products.save_draft_content(changed)
    changed = context.products.draft_record(draft["draft_id"])
    changed["images"].reverse()
    for i, ref in enumerate(changed["images"]):
        ref.update(order=i, role="main" if i == 0 else "detail")
    changed["sku_items"][0]["selected"] = not changed["sku_items"][0]["selected"]
    result, status = product_facade.save_draft_payload({"draft": changed})
    assert status == 200 and not result["draft"].get("images_manually_modified_at")
    changed = result["draft"]
    changed["images"] = [{"asset_id": "red", "role": "main", "order": 0}]
    result, status = product_facade.save_draft_payload({"draft": changed})
    assert status == 200 and result["draft"]["images_manually_modified_at"]
    assert state(draft)["status"] == "needs_confirmation"
    assert prepare(draft).status == "needs_confirmation"
    # 不接受客户端/模型伪造完成或清除人工记录。
    forged = {"draft_id": draft["draft_id"], "images_manually_modified_at": "",
              "image_preparation": {"mode": "generated", "fingerprint": state(draft)["fingerprint"]}}
    context.products.save_draft_content(forged)
    assert state(draft)["status"] == "needs_confirmation"


def test_keep_is_bound_to_current_content_and_needs_no_models(app_dir):
    _, draft, _ = setup_sources(app_dir)
    fingerprint = state(draft)["fingerprint"]
    assert prepare(draft, decision="keep", expected_fingerprint=fingerprint).status == "kept"
    assert prepare(draft).status == "kept"
    context = get_context()
    latest = context.products.draft_record(draft["draft_id"])
    latest["images"] = [{"asset_id": "pink", "role": "main", "order": 0}]
    product_facade.save_draft_payload({"draft": latest})
    with pytest.raises(BusinessCapabilityError, match="确认时不一致"):
        prepare(draft, decision="regenerate", expected_fingerprint=fingerprint)
    assert prepare(draft).status == "needs_confirmation"


def test_manual_edit_success_is_visible_as_manual_not_whole_set_completed(monkeypatch, app_dir):
    product, draft, raw = setup_sources(app_dir)
    image_model(monkeypatch, raw)
    image_facade.process_draft_images_payload(edit_request(product, draft, ["common"]))
    result = finish(draft["draft_id"])
    assert result["image_batch"]["success"] == 1
    assert state(draft)["status"] == "needs_confirmation"
    assert state(draft)["manually_modified"]
    assert prepare(draft).status == "needs_confirmation"


def test_auto_set_generates_common_and_sku_adopts_and_reuses(monkeypatch, app_dir, release_guard_dir):
    product, draft, raw = setup_sources(app_dir)
    context = get_context()
    monkeypatch.setattr(image_set_service, "plan_image_set", lambda **_: ImageSetPlan.model_validate(plan()))
    calls = image_model(monkeypatch, raw)
    with release_guard.serving(release_guard_dir):
        result = prepare(draft)
    assert result.status == "generated" and result.generated_count > 3
    after = context.products.draft_record(draft["draft_id"])
    assert len(after["images"]) == 3 and after["images"] != draft["images"]
    assert after["sku_items"][0]["overrides"]["image_asset_id"] != "red"
    saved = context.products.load_product_from_index(product["product_id"], "")
    assert {"common", "red", "pink"}.issubset({item["id"] for item in saved["source"]["image_pool"]})
    for item in product["source"]["image_pool"]:
        assert Path(item["path"]).read_bytes() == raw
    count = len(calls)
    assert state(draft)["status"] == "generated"
    assert prepare(draft).status == "generated" and len(calls) == count


@pytest.mark.parametrize("use_draft_images", [True, False])
def test_auto_selects_references_and_completes_set(monkeypatch, app_dir, use_draft_images):
    product, draft, raw = setup_sources(app_dir)
    products = get_context().products
    refs = []
    for i in range(20):
        asset_id = f"ref-{i}"
        path = app_dir / f"{asset_id}.png"
        path.write_bytes(raw)
        role = {17: "main", 18: "size", 19: "scene"}.get(i, "detail")
        product["source"]["image_pool"].append({
            "id": asset_id, "path": str(path), "usage": role, "selected": True, "is_main": role == "main",
        })
        refs.append({"asset_id": asset_id, "role": role, "order": i})
    product["source"]["image_pool"][0]["selected"] = False
    products.save_product(product)
    draft = products.draft_record(draft["draft_id"])
    draft["images"] = refs if use_draft_images else []
    products.save_draft_content(draft)
    observed = []

    def planner(*, sources, **kwargs):
        observed.extend(item["id"] for item in sources)
        proposal = plan()
        for page in proposal["pages"]:
            page["source_image_ids"] = [observed[0]]
        return ImageSetPlan.model_validate(proposal)

    monkeypatch.setattr(image_set_service, "plan_image_set", planner)
    image_model(monkeypatch, raw)
    result = prepare(draft)
    assert result.status == "generated"
    assert len(observed) == len(set(observed)) == 12
    assert observed[0] == "ref-17" and {"ref-18", "ref-19"}.issubset(observed)
    assert set(observed).issubset(ref["asset_id"] for ref in refs)
    saved = products.load_product_from_index(product["product_id"], "")
    assert {ref["asset_id"] for ref in refs}.issubset(item["id"] for item in saved["source"]["image_pool"])


def test_partial_generation_keeps_originals_and_does_not_automatically_retry(monkeypatch, app_dir):
    _, draft, raw = setup_sources(app_dir)
    monkeypatch.setattr(image_set_service, "plan_image_set", lambda **_: ImageSetPlan.model_validate(plan()))
    calls = image_model(monkeypatch, raw, fail_scene=True)
    result = prepare(draft)
    assert result.status == "failed" and result.failed_count == 1
    after = get_context().products.draft_record(draft["draft_id"])
    assert after["images"] == draft["images"] and after["sku_items"] == draft["sku_items"]
    count = len(calls)
    repeated = prepare(draft)
    assert repeated.status == "failed" and repeated.batch_id == result.batch_id
    assert len(calls) == count


def test_manual_regenerate_does_not_ask_again_and_new_manual_edit_invalidates_result(monkeypatch, app_dir):
    _, draft, raw = setup_sources(app_dir)
    latest = deepcopy(draft)
    latest["images"] = [{"asset_id": "pink", "role": "main", "order": 0}]
    product_facade.save_draft_payload({"draft": latest})
    assert prepare(draft).status == "needs_confirmation"
    monkeypatch.setattr(image_set_service, "plan_image_set", lambda **_: ImageSetPlan.model_validate(plan()))
    image_model(monkeypatch, raw)
    result = prepare(draft, decision="regenerate", expected_fingerprint=state(draft)["fingerprint"])
    assert result.status == "generated" and prepare(draft).status == "generated"
    latest = get_context().products.draft_record(draft["draft_id"])
    latest["images"] = draft["images"]
    product_facade.save_draft_payload({"draft": latest})
    assert prepare(draft).status == "needs_confirmation"


def test_planning_concurrent_manual_edit_does_not_start_generation(monkeypatch, app_dir):
    _, draft, raw = setup_sources(app_dir)
    def planner(**kwargs):
        latest = get_context().products.draft_record(draft["draft_id"])
        latest["images"] = [{"asset_id": "pink", "role": "main", "order": 0}]
        product_facade.save_draft_payload({"draft": latest})
        return ImageSetPlan.model_validate(plan())
    monkeypatch.setattr(image_set_service, "plan_image_set", planner)
    calls = image_model(monkeypatch, raw)
    result = prepare(draft)
    assert result.status == "failed" and "规划期间已更新" in result.message
    assert calls == [] and get_context().image_batches.latest_for_draft(draft["draft_id"]) is None


def test_source_import_is_not_a_manual_edit_and_historical_processed_images_are_protected(app_dir):
    product, draft, _ = setup_sources(app_dir)
    for asset in product["source"]["image_pool"]:
        asset["origin"] = "manual"
    assert image_preparation_status(product, draft)["status"] == "unprocessed"
    product["source"]["image_pool"][0]["origin"] = "ai_generated"
    status = image_preparation_status(product, draft)
    assert status["status"] == "needs_confirmation" and not status["manually_modified"]


def test_three_drafts_queue_before_planning_finishes_and_preserve_other_edits(app_dir, monkeypatch):
    from erp_web.facades.agent_capability_facade import build_job_status_readers
    from erp_web.runtime_units.image_capabilities import DRAFT_IMAGE_JOB_TYPE, ImageCapabilityScope, draft_images_wait
    from erp_web.runtime_units.publish_capabilities import prepare_product_publish
    from erp_web.schemas.image_capabilities import DraftImagesWaitRequest
    from erp_web.schemas.publish_capabilities import ProductPublishPrepareRequest

    _, first, raw = setup_sources(app_dir)
    context = get_context()
    drafts = [first] + [context.products.duplicate_draft_from_index(first["draft_id"])[0]["draft"] for _ in range(2)]
    entered = {draft["draft_id"]: Event() for draft in drafts}
    release = Event()

    def planner(*, draft, **kwargs):
        entered[draft["draft_id"]].set()
        assert release.wait(10)
        return ImageSetPlan.model_validate(plan())

    monkeypatch.setattr(image_set_service, "plan_image_set", planner)
    image_model(monkeypatch, raw)
    reader = build_job_status_readers(context)[DRAFT_IMAGE_JOB_TYPE]
    jobs = []
    try:
        for draft in drafts:
            request = DraftImagesPrepareRequest(draft_id=draft["draft_id"], count=3)
            job = prepare_draft_images(request, context=context)
            jobs.append(job)
            assert job.status == "queued" and prepare_draft_images(request, context=context) == job
        assert len({job.batch_id for job in jobs}) == 3
        assert all(started.wait(3) for started in entered.values())
        for job in jobs:
            receipt = draft_images_wait(DraftImagesWaitRequest(draft_id=job.draft_id, batch_id=job.batch_id),
                                        ImageCapabilityScope(context))
            assert reader.read_job_state(receipt.job_id).status == "running"
            _, error, _ = context.products.save_draft_content({"draft_id": job.draft_id, "description": "生图期间完成的文案"})
            assert error is None
            with pytest.raises(BusinessCapabilityError, match="套图仍在准备"):
                prepare_product_publish(ProductPublishPrepareRequest(draft_id=job.draft_id, platform="ozon"))
    finally:
        release.set()
        results = [wait_result(job) for job in jobs]
    assert all(result.status == "generated" for result in results)
    for job in jobs:
        assert reader.read_job_state(job.batch_id).status == "success"
        assert context.products.draft_record(job.draft_id)["description"] == "生图期间完成的文案"


def test_chat_prepares_materials_before_native_wait_and_resumes_with_adopted_result(app_dir, tmp_path, monkeypatch):
    import asyncio
    import json
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel
    from erp_web.facades.agent_capability_facade import build_global_chat_toolset, build_job_status_readers
    from erp_web.services.agent_job_service import AgentJobService
    from tests.ai_code_mode_helpers import business_returns, discovering_tools
    from tests.test_native_agent_integration import CONVERSATION, body, service

    _, draft, raw = setup_sources(app_dir)
    release = Event()
    responses = []

    def planner(**kwargs):
        assert release.wait(10)
        return ImageSetPlan.model_validate(plan())

    monkeypatch.setattr(image_set_service, "plan_image_set", planner)
    image_model(monkeypatch, raw)

    @discovering_tools("draft_images_prepare", "draft_attributes_read", "draft_images_wait")
    async def model(messages, info):
        replies = {part.tool_name: part.content for part in business_returns(messages)}
        args = {"draft_id": draft["draft_id"]}
        if "draft_images_prepare" not in replies:
            name = "draft_images_prepare"
            args["count"] = 3
        elif "draft_attributes_read" not in replies:
            assert replies["draft_images_prepare"]["status"] == "queued"
            name = "draft_attributes_read"
            args["scope"] = "common"
        elif "draft_images_wait" not in replies:
            assert replies["draft_attributes_read"]["product"]
            name = "draft_images_wait"
            args["batch_id"] = replies["draft_images_prepare"]["batch_id"]
        else:
            responses.append(replies["draft_images_wait"])
            yield "套图已采用。"
            return
        yield {0: DeltaToolCall(name=name, tool_call_id=name, json_args=json.dumps(args))}

    context = get_context()
    ui = service(tmp_path / "chat", FunctionModel(stream_function=model), build_global_chat_toolset(context))
    worker = AgentJobService(ui_service=ui, job_readers=build_job_status_readers(context))
    try:
        asyncio.run(ui.prepare_run(body("准备选中草稿", page_context={"page": "drafts", "selected_draft_ids": [draft["draft_id"]]})).stream(lambda _: None))
        pending = ui.call_store.pending(CONVERSATION)
        assert [call.tool_name for call in pending[0].calls] == ["draft_images_wait"]
        row, = ui.call_store.work()
        worker.execute_or_reconcile(row)
        row, = ui.call_store.work()
        assert row["status"] == "waiting_job"
        worker.execute_or_reconcile(row)
        assert ui.call_store.work()[0]["status"] == "waiting_job"
        release.set()
        wait_result(context.image_batches.latest_preparation(draft["draft_id"]))
        worker.execute_or_reconcile(row)
        worker.scan()
        deadline = monotonic() + 5
        while ui.run_registry.is_active(CONVERSATION) and monotonic() < deadline:
            sleep(.01)
        assert responses[0]["job"]["status"] == "generated"
        assert responses[0]["ok"] and not ui.call_store.pending(CONVERSATION)
    finally:
        release.set()
        worker.close()

def test_interrupted_planning_keeps_receipt_without_resubmission(app_dir, monkeypatch):
    from erp_web.release_guard import release_guard
    from erp_web.stores.image_batch_store import ImageBatchStore

    _, draft, _ = setup_sources(app_dir)
    scheduled = []
    monkeypatch.setattr(release_guard, "start_thread", lambda *args: scheduled.append(args))
    request = DraftImagesPrepareRequest(draft_id=draft["draft_id"])
    context = get_context()
    queued = prepare_draft_images(request, context=context)
    reopened = ImageBatchStore(context.image_batches.path)
    interrupted = reopened.preparation(queued.batch_id)
    assert interrupted.status == "failed" and "待核对" in interrupted.message
    assert prepare_draft_images(request, context=context) == interrupted
    assert len(scheduled) == 1


def test_unavailable_canvas_stops_batch_before_planning(monkeypatch, app_dir):
    from erp_web.services import image_translate_service

    _, draft, raw = setup_sources(app_dir)
    checks = []

    def unavailable(app_dir, model):
        checks.append(model)
        raise RuntimeError("未找到 Gemini Canvas 原生模型通道")

    monkeypatch.setattr(image_translate_service.gemini_canvas_runtime, "is_canvas_model", lambda _: True)
    monkeypatch.setattr(image_translate_service.gemini_canvas_runtime, "check_canvas_connection", unavailable)
    monkeypatch.setattr(image_set_service, "plan_image_set", lambda **_: pytest.fail("通道不可用时不应规划"))
    calls = image_model(monkeypatch, raw)
    result = prepare(draft)
    assert result.status == "failed" and "Canvas" in result.message
    assert len(checks) == 1 and not calls and not get_context().image_batches.get(result.batch_id)


def test_replacing_source_under_same_id_during_planning_stops_generation(monkeypatch, app_dir):
    product, draft, raw = setup_sources(app_dir)
    context = get_context()

    def planner(**kwargs):
        latest = context.products.load_product_from_index(product["product_id"], "")
        asset = next(item for item in latest["source"]["image_pool"] if item["id"] == "common")
        replacement = app_dir / "replacement.png"
        replacement.write_bytes(raw)
        asset["path"] = str(replacement)
        context.products.save_product(latest)
        return ImageSetPlan.model_validate(plan())

    monkeypatch.setattr(image_set_service, "plan_image_set", planner)
    calls = image_model(monkeypatch, raw)
    result = prepare(draft)
    assert result.status == "failed" and "规划期间已更新" in result.message and not calls


def test_saved_page_allows_versioned_edit_before_adoption(monkeypatch, app_dir):
    _, draft, raw = setup_sources(app_dir)
    context = get_context()
    monkeypatch.setattr(image_set_service, "plan_image_set", lambda **_: ImageSetPlan.model_validate(plan()))
    image_model(monkeypatch, raw)
    generate_page = image_set_service.generate_page
    release = Event()
    saved_page = Event()
    update_item = context.image_batches.update_item

    def hold_later_pages(**kwargs):
        if kwargs["page"].key != "main":
            assert release.wait(10)
        return generate_page(**kwargs)

    def record(*args, **changes):
        update_item(*args, **changes)
        if changes.get("status") == "success":
            saved_page.set()

    monkeypatch.setattr(image_set_service, "generate_page", hold_later_pages)
    monkeypatch.setattr(context.image_batches, "update_item", record)
    job = prepare_draft_images(DraftImagesPrepareRequest(draft_id=draft["draft_id"], count=3), context=context)
    try:
        assert saved_page.wait(5)
        _, error, _ = context.products.save_draft_content({
            "draft_id": draft["draft_id"], "updated_at": draft["updated_at"], "description": "核对来源后的文案",
        })
        assert error is None
    finally:
        release.set()
        result = wait_result(job)
    assert result.status == "generated"
    assert context.products.draft_record(draft["draft_id"])["description"] == "核对来源后的文案"
