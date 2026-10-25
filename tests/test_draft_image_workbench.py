"""V2 草稿图片：隔离数据验证批次恢复、范围、当前输入与互斥。"""
from copy import deepcopy
from threading import Event
import time

import pytest

from erp_web.context import get_context
from erp_web.facades import image_facade, product_facade
from erp_web.schemas.requests import RequestValidationError
from erp_web.services import image_translate_service, ai_model_probe_service
from tests.test_sku_workflow import product_fixture


def seeded():
    context = get_context()
    product = product_fixture()
    product["source"]["image_pool"] = [
        {"id": id, "url": f"https://example.com/{id}.png", "origin": "source", "order": index,
         "selected": id == "common", "platforms": ["yandex"] if id == "red" else []}
        for index, id in enumerate(("common", "red", "pink"))
    ]
    product["sku_items"][0]["image_asset_id"] = "red"
    product["drafts"]["ozon"]["images"] = [{"asset_id": "common", "role": "main", "order": 0}]
    product = context.products.save_product(product)
    draft = context.products.draft_record(product["drafts"]["ozon"]["draft_id"])
    return product, draft


def request(product, draft, ids, request_id="v2-test"):
    return {"product_id": product["product_id"], "draft_id": draft["draft_id"], "updated_at": draft["updated_at"],
            "request_id": request_id, "operation": "edit", "prompt": "修改标题", "source_image_ids": ids}


def finish(draft_id):
    until = time.monotonic() + 10
    while time.monotonic() < until:
        result, status = image_facade.draft_image_state_payload({"draft_id": draft_id})
        assert status == 200
        if result["image_batch"]["terminal"]:
            return result
        time.sleep(.01)
    pytest.fail("隔离图片批次未结束")


def test_explicit_selection_ignores_old_pool_flags_and_platform_tags():
    product, _ = seeded()
    assert {item["id"] for item in image_translate_service.select_source_images(product, ["common", "red"], "mercadolibre")} == {"common", "red"}


def test_async_batch_recovery_partial_success_and_second_edit_uses_current(monkeypatch, app_dir):
    product, draft = seeded()
    context = get_context()
    before_product = deepcopy(product)
    entered, release = Event(), Event()
    raw = ai_model_probe_service._image_edit_probe_bytes()
    real_edit = image_translate_service.edit_images
    inputs = []

    def process(app, source_product, config, *, image_ids, **kwargs):
        inputs.extend(image_ids)
        entered.set()
        assert release.wait(10)
        if image_ids == ["common"]:
            raise RuntimeError("服务繁忙")
        return real_edit(app, source_product, config, image_ids=image_ids,
                         provider=lambda *_: [{"bytes": raw, "source_id": "provider-wrong-parent"}], **kwargs)

    monkeypatch.setattr(image_translate_service, "edit_images", process)
    body = request(product, draft, ["red", "common"])
    try:
        started, code = image_facade.process_draft_images_payload(body)
        assert code == 200 and not started["image_batch"]["terminal"]
        assert entered.wait(5)
        # 同标识读取原任务，即使调用方响应丢失也不能再次计费。
        repeated, _ = image_facade.process_draft_images_payload(body)
        assert repeated["image_batch"]["conversation_id"] == body["request_id"]
        with pytest.raises(RequestValidationError, match="正在处理"):
            image_facade.process_draft_images_payload({**body, "request_id": "duplicate"})
        with pytest.raises(RequestValidationError, match="正在处理"):
            product_facade.save_draft_payload({"draft": draft})
        changed = deepcopy(draft)
        changed["images"] = []
        with pytest.raises(RequestValidationError, match="正在处理"):
            context.products.save_draft_content(changed)
        polled, _ = image_facade.draft_image_state_payload({"draft_id": draft["draft_id"]})
        assert polled["image_batch"]["conversation_id"] == body["request_id"]
    finally:
        release.set()
    finished = finish(draft["draft_id"])
    assert (finished["image_batch"]["success"], finished["image_batch"]["failed"]) == (1, 1)
    assert finished["draft"]["images"] == draft["images"]
    result_id = finished["draft"]["sku_items"][0]["overrides"]["image_asset_id"]
    assert result_id != "red"
    result_asset = next(item for item in finished["imagePool"] if item["id"] == result_id)
    assert result_asset["derived_from_id"] == "red"
    old_file = app_dir / result_asset["path"]
    assert old_file.read_bytes() == raw
    # 默认商品及其他草稿引用未改；SKU 结果不进入共用区。
    assert finished["product"]["sku_items"] == before_product["sku_items"]
    image_facade.process_draft_images_payload(request(product, finished["draft"], [result_id], "v2-second"))
    second = finish(draft["draft_id"])
    second_id = second["draft"]["sku_items"][0]["overrides"]["image_asset_id"]
    second_asset = next(item for item in second["imagePool"] if item["id"] == second_id)
    assert inputs.count("red") == 1
    assert inputs[-1] == result_id
    assert second_asset["derived_from_id"] == result_id
    assert second_asset["path"] != result_asset["path"]
    assert old_file.read_bytes() == raw
    assert second["draft"]["images"] == draft["images"]
    with pytest.raises(RequestValidationError, match="不是当前使用"):
        image_facade.process_draft_images_payload(request(product, second["draft"], ["red"], "old-input"))


def test_state_does_not_return_another_drafts_batch(monkeypatch):
    product, draft = seeded()
    from erp_web.schemas.image_batches import ImageBatch, ImageBatchItem
    from erp_web.stores.image_batch_store import utc_now
    context = get_context()
    context.image_batches.create(ImageBatch(conversation_id="foreign", product_id=product["product_id"], draft_id="another",
        prompt="编辑", created_at=utc_now(), updated_at=utc_now(), items=[ImageBatchItem(index=0, source_id="red")]))
    result, status = image_facade.draft_image_state_payload({"draft_id": draft["draft_id"], "request_id": "foreign"})
    assert status == 404 and not result["ok"]
    result, status = image_facade.draft_image_state_payload({"draft_id": draft["draft_id"]})
    assert status == 200 and result["image_batch"] is None


def test_upload_merges_latest_pool_and_returns_only_own_asset_ids(monkeypatch):
    product, draft = seeded()
    context = get_context()
    from erp_web.services import image_service

    def upload(*args):
        latest = context.products.load_product_from_index(product['product_id'], '')
        latest['source']['image_pool'].append({'id': 'other-draft-result', 'url': 'https://example.com/other.png'})
        context.products.save_product(latest)
        return [{'id': 'uploaded', 'url': 'https://example.com/uploaded.png'}]

    monkeypatch.setattr(image_service, 'upload_images', upload)
    result, status = image_facade.upload_images_payload({'product_id': product['product_id'], 'uploads': [{}]})
    assert status == 200
    assert result['uploaded_image_ids'] == ['uploaded']
    assert {'uploaded', 'other-draft-result', 'red', 'common'} <= {item['id'] for item in result['imagePool']}
    assert context.products.draft_record(draft['draft_id'])['images'] == draft['images']
