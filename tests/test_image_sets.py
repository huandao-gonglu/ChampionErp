"""套图：真实原生规划、整组参考、批次恢复与共用图隔离。"""

import base64
from copy import deepcopy
from dataclasses import replace
import json
from threading import Event
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import BinaryContent, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.providers import Provider

from erp_web.context import get_context
from erp_web.facades import image_facade
from erp_web.schemas.image_sets import ImageSetPlan
from erp_web.schemas.requests import RequestValidationError, validate_request_payload
from erp_web.services import ai_direct_request_service, ai_model_probe_service, image_set_service, image_translate_service
from erp_web.services.ai_pydantic_image_model import OpenAIImagesModel
from tests.ai_function_model_streaming import streaming_function_model
from tests.test_ai_direct_request_service import _binding
from tests.test_draft_image_workbench import seeded, finish


def setup_sources(app_dir):
    product, draft = seeded()
    raw = ai_model_probe_service._image_edit_probe_bytes()
    for item in product["source"]["image_pool"]:
        source = app_dir / f"reference-{item['id']}.png"
        source.write_bytes(raw)
        item["path"] = str(source)
    product = get_context().products.save_product(product)
    draft = get_context().products.draft_record(draft["draft_id"])
    return product, draft, raw


def plan():
    return {"style": "简洁明亮，统一留白和字体", "pages": [{**page, "scope": "common", "sku_ids": []} for page in [
        {"key": "main", "title": "商品主图", "role": "main", "prompt": "根据全部参考素材重新设计主图", "source_image_ids": ["common", "red"]},
        {"key": "detail", "title": "材质细节", "role": "detail", "prompt": "展示已有材质纹理，不增加新结构", "source_image_ids": ["common"]},
        {"key": "scene", "title": "使用场景", "role": "scene", "prompt": "保留真实商品比例，设计使用环境", "source_image_ids": ["red"]},
    ]]}


def body(product, draft, request_id="set-first", **extra):
    return {"product_id": product["product_id"], "draft_id": draft["draft_id"],
            "updated_at": draft["updated_at"], "request_id": request_id, "plan": plan(), **extra}


def image_model(monkeypatch, raw, fail_scene=False):
    real_edit = getattr(image_translate_service.edit_images, "original", image_translate_service.edit_images)
    calls = []

    def fake(app, product, config, **kwargs):
        calls.append(kwargs)
        if fail_scene and "本页用途：scene" in kwargs["prompt"]:
            raise RuntimeError("服务繁忙")
        return real_edit(app, product, config, provider=lambda *_: [{"bytes": raw}], **kwargs)

    fake.original = real_edit
    monkeypatch.setattr(image_translate_service, "edit_images", fake)
    return calls


def test_native_planner_sees_all_images_and_retries_invalid_output(monkeypatch, app_dir):
    product, draft, _ = setup_sources(app_dir)
    requests = []

    def model(messages, info):
        requests.append(messages)
        payload = plan()
        if len(requests) == 1:
            payload["pages"][0]["source_image_ids"] = ["not-provided"]
        return ModelResponse(parts=[TextPart(json.dumps(payload, ensure_ascii=False))])

    factory = image_set_service.AiAgentFactory
    monkeypatch.setattr(image_set_service, "AiAgentFactory", lambda **kwargs: factory(
        **kwargs, model_binding_factory=lambda *_a, **_k: replace(_binding(), model=streaming_function_model(FunctionModel(model)))
    ))
    sources = image_translate_service.select_source_images(product, ["common", "red"])
    result = image_set_service.plan_image_set(app_dir=app_dir, config={}, messages=get_context().pydantic_messages,
        product=product, draft=draft, sources=sources, count=3, prompt="风格统一")
    assert result.model_dump() == plan()
    assert len(requests) == 2
    content = next(part.content for message in requests[0] for part in message.parts if isinstance(part, UserPromptPart))
    assert len([item for item in content if isinstance(item, BinaryContent)]) == 2
    assert "目标语言" in content[0]
    history = get_context().pydantic_messages.list()
    assert len(history) == 1
    assert len(get_context().pydantic_messages.get(history[0].conversation_id).model_messages()) >= 4


def test_generation_partial_success_preview_recovery_and_single_retry(monkeypatch, app_dir):
    product, draft, raw = setup_sources(app_dir)
    original_skus = deepcopy(draft["sku_items"])
    calls = image_model(monkeypatch, raw, fail_scene=True)
    started, status = image_facade.generate_draft_image_set_payload(body(product, draft))
    assert status == 200 and started["image_batch"]["image_set"] == plan()
    done = finish(draft["draft_id"])
    assert (done["image_batch"]["success"], done["image_batch"]["failed"]) == (2, 1)
    assert done["draft"]["images"] == draft["images"]
    assert done["draft"]["sku_items"] == original_skus
    assert calls[0]["combine_references"]
    assert any(call["image_ids"] == ["common", "red"] for call in calls)
    restored, _ = image_facade.draft_image_state_payload({"draft_id": draft["draft_id"]})
    assert restored["image_batch"]["image_set"] == plan()
    retained = {item["source_id"]: item["result_asset_ids"] for item in done["image_batch"]["items"] if item["status"] == "success"}
    for ids in retained.values():
        assert len(ids) == 1
        asset = next(item for item in done["product"]["source"]["image_pool"] if item["id"] == ids[0])
        assert not asset.get("derived_from_id")
        assert len(asset["raw"]["image_set"]["source_image_ids"]) >= 1
        assert (app_dir / asset["path"]).read_bytes() == raw
    retry_calls = image_model(monkeypatch, raw)
    retry = body(product, done["draft"], "set-retry", base_request_id="set-first", retry_page_ids=["scene"])
    image_facade.generate_draft_image_set_payload(retry)
    done_retry = finish(draft["draft_id"])
    assert done_retry["image_batch"]["success"] == 3
    assert all(item["result_asset_ids"] == retained[item["source_id"]] for item in done_retry["image_batch"]["items"] if item["source_id"] in retained)
    assert done_retry["draft"]["images"] == draft["images"]
    assert len(retry_calls) == 1 and "本页用途：scene" in retry_calls[0]["prompt"]


def test_adopted_set_can_regenerate_one_page_without_touching_sku_refs(monkeypatch, app_dir):
    product, draft, raw = setup_sources(app_dir)
    image_model(monkeypatch, raw)
    image_facade.generate_draft_image_set_payload(body(product, draft))
    done = finish(draft["draft_id"])
    updated = deepcopy(done["draft"])
    updated["images"] = [{"asset_id": item["result_asset_ids"][0], "role": "main" if index == 0 else "detail", "order": index}
                         for index, item in enumerate(done["image_batch"]["items"])]
    get_context().products.save_draft_content(updated)
    adopted = get_context().products.draft_record(draft["draft_id"])
    calls = image_model(monkeypatch, raw)
    image_facade.generate_draft_image_set_payload(body(product, adopted, "after-adoption", base_request_id="set-first", retry_page_ids=["main"]))
    retried = finish(draft["draft_id"])
    assert len(calls) == 1
    assert retried["draft"]["images"] == adopted["images"]
    assert retried["draft"]["sku_items"] == adopted["sku_items"]


def test_duplicate_request_is_read_only_and_busy_draft_is_protected(monkeypatch, app_dir):
    product, draft, raw = setup_sources(app_dir)
    entered, release = Event(), Event()
    calls = []
    real_generate = image_set_service.generate_page
    image_model(monkeypatch, raw)

    def generate(**kwargs):
        calls.append(kwargs["page"].key)
        entered.set()
        assert release.wait(10)
        return real_generate(**kwargs)

    monkeypatch.setattr(image_set_service, "generate_page", generate)
    request = body(product, draft)
    try:
        image_facade.generate_draft_image_set_payload(request)
        assert entered.wait(5)
        image_facade.generate_draft_image_set_payload(request)
        with pytest.raises(RequestValidationError, match="正在处理"):
            image_facade.generate_draft_image_set_payload(body(product, draft, "other"))
        with pytest.raises(RequestValidationError, match="其他操作"):
            image_facade.generate_draft_image_set_payload({**request, "plan": {**plan(), "style": "另一套"}})
        changed = deepcopy(draft); changed["images"] = []
        with pytest.raises(RequestValidationError, match="正在处理"):
            get_context().products.save_draft_content(changed)
    finally:
        release.set()
    done = finish(draft["draft_id"])
    assert done["image_batch"]["success"] == 3
    assert sorted(calls) == ["detail", "main", "scene"]


def test_invalid_plan_reference_and_stale_draft_do_not_start_models(monkeypatch, app_dir):
    product, draft, _ = setup_sources(app_dir)
    calls = []
    monkeypatch.setattr(image_set_service, "generate_page", lambda **kw: calls.append(kw))
    with pytest.raises(RequestValidationError, match="已更新"):
        image_facade.generate_draft_image_set_payload(body(product, draft, updated_at="old"))
    invalid = plan(); invalid["pages"][0]["source_image_ids"] = ["foreign"]
    with pytest.raises(RequestValidationError, match="不是当前"):
        image_facade.generate_draft_image_set_payload(body(product, draft, plan=invalid))
    invalid = plan(); invalid["pages"][1]["key"] = "main"
    with pytest.raises(RequestValidationError, match="方案无效"):
        image_facade.generate_draft_image_set_payload(body(product, draft, plan=invalid))
    assert calls == [] and get_context().image_batches.latest_for_draft(draft["draft_id"]) is None


def test_planning_rejects_concurrent_draft_change(monkeypatch, app_dir):
    product, draft, _ = setup_sources(app_dir)

    def changed(**kwargs):
        latest = get_context().products.draft_record(draft["draft_id"])
        latest["images"] = []
        get_context().products.save_draft_content(latest)
        return ImageSetPlan.model_validate(plan())

    monkeypatch.setattr(image_set_service, "plan_image_set", changed)
    with pytest.raises(RequestValidationError, match="已更新"):
        image_facade.plan_draft_image_set_payload({**body(product, draft), "source_image_ids": ["common"], "count": 3})
    assert get_context().image_batches.latest_for_draft(draft["draft_id"]) is None


def test_combined_references_use_one_native_image_request(monkeypatch, app_dir):
    _, _, raw = setup_sources(app_dir)
    calls = []

    class Images:
        async def edit(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(raw).decode(), url=None)])

    class FakeProvider(Provider):
        @property
        def name(self): return "test"
        @property
        def base_url(self): return "https://example.com/v1"
        @property
        def client(self): return SimpleNamespace(images=Images())

    monkeypatch.setattr(ai_direct_request_service, "create_pydantic_model_binding", lambda *_a, **_k: replace(
        _binding(required=("image_edit",)), model=OpenAIImagesModel("test-image", provider=FakeProvider())))
    kwargs = dict(app_dir=app_dir, use_case_id="image.translate", model={}, required_capabilities=("image_edit",),
                  prompt="重新构图", mode="image_set", size="1024x1024", quality="medium", count=1,
                  timeout_seconds=30, combine_references=True)
    result = ai_direct_request_service.edit_images(**kwargs, images=[{"id": "a", "path": "reference-common.png"}, {"id": "b", "path": "reference-red.png"}])
    assert len(result) == 1 and len(calls) == 1 and len(calls[0]["image"]) == 2
    with pytest.raises(ValueError, match="无法读取"):
        ai_direct_request_service.edit_images(**kwargs, images=[{"id": "a", "path": "reference-common.png"}, {"id": "b", "path": "missing.png"}])
    assert len(calls) == 1


def test_request_contract_validates_count_and_retry_shape():
    with pytest.raises(RequestValidationError):
        validate_request_payload({"product_id": "p", "draft_id": "d", "updated_at": "v", "source_image_ids": ["a"], "count": 20}, endpoint="/api/draft-images/plan-set")
    with pytest.raises(RequestValidationError):
        validate_request_payload({"product_id": "p", "draft_id": "d", "updated_at": "v", "request_id": "r", "retry_page_ids": "page"}, endpoint="/api/draft-images/generate-set")


def test_sku_only_plans_and_generates_exactly_one_sku_without_common_planning(monkeypatch, app_dir):
    product, draft, raw = setup_sources(app_dir)
    sku_id = draft["sku_items"][0]["sku_id"]
    request = {"product_id": product["product_id"], "draft_id": draft["draft_id"], "updated_at": draft["updated_at"],
               "source_image_ids": [], "sku_ids": [sku_id], "prompt": "使用浅蓝色背景"}
    endpoint = "/api/draft-images/plan-set"
    with pytest.raises(RequestValidationError):
        validate_request_payload({**request, "sku_ids": []}, endpoint=endpoint)
    monkeypatch.setattr(image_set_service, "plan_image_set", lambda **_: pytest.fail("只生成 SKU 图时不应规划共用图"))
    result, status = image_facade.plan_draft_image_set_payload(validate_request_payload(request, endpoint=endpoint))
    assert status == 200
    [page] = result["plan"]["pages"]
    assert page["scope"] == "sku" and page["sku_ids"] == [sku_id] and page["source_image_ids"] == ["red"]
    calls = image_model(monkeypatch, raw)
    image_facade.generate_draft_image_set_payload(body(product, draft, plan=result["plan"]))
    done = finish(draft["draft_id"])
    assert done["image_batch"]["success"] == 1 and len(calls) == 1
    assert calls[0]["image_ids"] == ["red"] and request["prompt"] in calls[0]["prompt"]
    assert done["draft"]["images"] == draft["images"]


def test_sku_plan_deduplicates_shared_source_and_generates_separate_assets(monkeypatch, app_dir):
    product, draft, raw = setup_sources(app_dir)
    product["sku_items"][1]["image_asset_id"] = "red"
    product = get_context().products.save_product(product)
    draft = get_context().products.draft_record(draft["draft_id"])
    ids = [row["sku_id"] for row in draft["sku_items"]]
    monkeypatch.setattr(image_set_service, "plan_image_set", lambda **_: ImageSetPlan.model_validate(plan()))
    planned, status = image_facade.plan_draft_image_set_payload({**body(product, draft), "source_image_ids": ["common"], "sku_ids": ids, "count": 3})
    assert status == 200
    spec = planned["plan"]
    assert len(spec["pages"]) == 4
    sku_page = spec["pages"][-1]
    assert sku_page["scope"] == "sku" and sku_page["sku_ids"] == ids and sku_page["source_image_ids"] == ["red"]
    calls = image_model(monkeypatch, raw)
    image_facade.generate_draft_image_set_payload(body(product, draft, plan=spec))
    done = finish(draft["draft_id"])
    assert done["image_batch"]["success"] == 4 and len(calls) == 4
    assert done["draft"]["images"] == draft["images"]
    assert done["draft"]["sku_items"] == draft["sku_items"]
    sku_result = next(item for item in done["image_batch"]["items"] if item["source_id"] == sku_page["key"])
    asset_id = sku_result["result_asset_ids"][0]
    asset = next(item for item in done["product"]["source"]["image_pool"] if item["id"] == asset_id)
    assert asset["is_sku"] and asset["raw"]["image_set"]["sku_ids"] == ids
    sku_call = next(call for call in calls if "当前页仅用于绑定 SKU" in call["prompt"])
    assert sku_call["image_ids"] == ["red"]
    adopted = deepcopy(done["draft"])
    adopted["images"] = [{"asset_id": item["result_asset_ids"][0], "role": "main" if index == 0 else "detail", "order": index}
                         for index, item in enumerate(done["image_batch"]["items"][:-1])]
    for row in adopted["sku_items"]:
        row.setdefault("overrides", {})["image_asset_id"] = asset_id
    _, error, status = get_context().products.save_draft_content(adopted)
    assert status == 200 and error is None
    saved = get_context().products.draft_record(draft["draft_id"])
    assert asset_id not in {ref["asset_id"] for ref in saved["images"]}
    assert all(row["overrides"]["image_asset_id"] == asset_id for row in saved["sku_items"])
    retry_calls = image_model(monkeypatch, raw)
    image_facade.generate_draft_image_set_payload(body(product, saved, "sku-retry", base_request_id="set-first", retry_page_ids=[sku_page["key"]]))
    retried = finish(draft["draft_id"])
    assert len(retry_calls) == 1 and retry_calls[0]["image_ids"] == ["red"]
    assert retried["draft"]["sku_items"] == saved["sku_items"]
    assert retried["image_batch"]["success"] == 4


def test_sku_target_scope_cannot_be_guessed_or_expanded(monkeypatch, app_dir):
    product, draft, raw = setup_sources(app_dir)
    sku_id = draft["sku_items"][0]["sku_id"]
    targets = image_set_service.sku_image_targets(product, draft, [sku_id])
    spec = image_set_service.assemble_image_set(ImageSetPlan.model_validate(plan()), targets).model_dump()
    spec["pages"][-1]["source_image_ids"] = ["common"]
    with pytest.raises(RequestValidationError, match="关联已变化"):
        image_facade.generate_draft_image_set_payload(body(product, draft, plan=spec))
    with pytest.raises(ValueError, match="未启用"):
        image_set_service.sku_image_targets(product, draft, ["foreign"])
    unselected = deepcopy(draft); unselected["sku_items"][0]["selected"] = False
    with pytest.raises(ValueError, match="未启用"):
        image_set_service.sku_image_targets(product, unselected, [sku_id])
    missing = deepcopy(draft); missing["sku_items"][0].setdefault("overrides", {})["image_asset_id"] = ""
    with pytest.raises(ValueError, match="缺少独立图片"):
        image_set_service.sku_image_targets(product, missing, [sku_id])
    assert get_context().image_batches.latest_for_draft(draft["draft_id"]) is None
    calls = image_model(monkeypatch, raw)
    scoped = image_set_service.assemble_image_set(ImageSetPlan.model_validate(plan()), targets)
    image_set_service.generate_page(app_dir=app_dir, config={}, product=product, draft=draft, plan=scoped, page=scoped.pages[-1])
    assert product["sku_items"][0]["name"] in calls[0]["prompt"]
    assert product["sku_items"][1]["name"] not in calls[0]["prompt"]


def test_older_common_plan_still_loads_and_sku_pages_do_not_consume_common_count():
    old = plan()
    for page in old["pages"]:
        page.pop("scope"); page.pop("sku_ids")
    parsed = ImageSetPlan.model_validate(old)
    assert all(page.scope == "common" and not page.sku_ids for page in parsed.pages)
    combined = image_set_service.assemble_image_set(parsed, [
        {"source_id": f"image-{i}", "sku_ids": [f"sku-{i}"], "names": [f"规格 {i}"]} for i in range(13)
    ])
    assert len(combined.pages) == 16
