"""草稿图片策略：手动保护、版本确认、套图复用与原素材保留。"""

from copy import deepcopy
from pathlib import Path

import pytest

from erp_web.context import get_context
from erp_web.facades import image_facade, product_facade
from erp_web.product_model.image_preparation import image_preparation_status
from erp_web.runtime_units.draft_image_prepare import prepare_draft_images
from erp_web.schemas.image_capabilities import DraftImagesPrepareRequest
from erp_web.schemas.image_sets import ImageSetPlan
from erp_web.services import image_set_service
from erp_web.services.capability_errors import BusinessCapabilityError
from tests.test_image_sets import setup_sources, plan, image_model
from tests.test_draft_image_workbench import request as edit_request, finish


def prepare(draft, **kwargs):
    return prepare_draft_images(DraftImagesPrepareRequest(draft_id=draft["draft_id"], count=3, **kwargs),
                                context=get_context())


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


def test_auto_set_generates_common_and_sku_adopts_and_reuses(monkeypatch, app_dir):
    product, draft, raw = setup_sources(app_dir)
    context = get_context()
    monkeypatch.setattr(image_set_service, "plan_image_set", lambda **_: ImageSetPlan.model_validate(plan()))
    calls = image_model(monkeypatch, raw)
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
    with pytest.raises(BusinessCapabilityError, match="规划期间已更新"):
        prepare(draft)
    assert calls == [] and get_context().image_batches.latest_for_draft(draft["draft_id"]) is None


def test_source_import_is_not_a_manual_edit_and_historical_processed_images_are_protected(app_dir):
    product, draft, _ = setup_sources(app_dir)
    for asset in product["source"]["image_pool"]:
        asset["origin"] = "manual"
    assert image_preparation_status(product, draft)["status"] == "unprocessed"
    product["source"]["image_pool"][0]["origin"] = "ai_generated"
    status = image_preparation_status(product, draft)
    assert status["status"] == "needs_confirmation" and not status["manually_modified"]
