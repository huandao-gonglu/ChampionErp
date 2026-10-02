"""发布准备工具的持久化、回读、范围和只读边界回归。"""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from erp_web.runtime_units import publish_capabilities
from erp_web.runtime_units.publish_adapter import YandexPublishingAdapter
from erp_web.runtime_units.image_delivery_persistence import persist_delivered_image_pool
from tests.image_hosting_test_utils import configure, FakeS3
from erp_web.runtime_units.publish_helpers import _draft_images
from erp_web.schemas.publish_capabilities import (
    ProductPublishPrepareRequest, ProductPublishValidateRequest,
    ProductPublishRequest, PublishRequestConfirmation,
)
from erp_web.services.capability_errors import BusinessCapabilityError
from tests.runtime_test_utils import temp_app_context
from tests.test_publish_image_materialization import _yandex_product


class _ImageAdapter(YandexPublishingAdapter):
    """保留真实图片准备/持久化边界，隔离与此测试无关的类目、核价规则。"""

    def validate_draft(self, context, config, *, image_stage="final"):
        from erp_web.context import get_context
        issues = get_context().image_delivery.inspect_product(context.product, "yandex", stage=image_stage)
        return {"ok": not issues, "errors": [{**asdict(issue), "field": "images"} for issue in issues], "warnings": []}

    def build_payload(self, context, config):
        return {"pictures": _draft_images(context.product, "yandex", context.draft)}

    def validate_payload(self, payload, config):
        return []


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    image = tmp_path / "main.jpg"
    from PIL import Image
    Image.new("RGB", (2, 2)).save(image)
    remote = FakeS3(monkeypatch)
    monkeypatch.setattr(publish_capabilities, "publishing_adapter_for", lambda platform: _ImageAdapter())
    with temp_app_context(tmp_path / "app") as context:
        configure(context, public_base_url="https://first.example.test", key_prefix="")
        context.test_remote = remote
        monkeypatch.setattr(context.config, "load_store_config", lambda: {"yandex": {"business_id": "123", "campaign_id": "456"}})
        product = _yandex_product(image)
        product["source"]["image_pool"].append({"id": "unused", "url": "https://supplier.example/unused.jpg", "selected": True, "delivery_error": "无关历史错误"})
        saved = context.products.save_product(product)
        product_id = saved["product_id"]
        request = ProductPublishPrepareRequest(draft_id=saved["drafts"]["yandex"]["draft_id"], platform="yandex", site="global")
        yield context, request, product_id


def test_prepare_persists_then_readonly_validation_passes_and_refreshes_url(boundary, tmp_path, monkeypatch):
    context, request, product_id = boundary
    validate_request = ProductPublishValidateRequest(**request.model_dump(exclude={"check_public_access"}))
    before = context.db.load_product_model(product_id)
    before_draft = context.db.load_draft_model(request.draft_id)
    before_read = publish_capabilities.validate_product_publish(validate_request)
    assert [issue.code for issue in before_read.errors] == ["IMAGE_NOT_PREPARED"]
    assert context.db.load_product_model(product_id) == before
    assert context.db.load_draft_model(request.draft_id) == before_draft
    assert not (tmp_path / "public").exists()

    def no_probe(*args):
        pytest.fail("未请求公网诊断时不得探测图片 URL")

    monkeypatch.setattr(publish_capabilities, "check_image_public_access", no_probe)
    result = publish_capabilities.product_publish_prepare(request, publish_capabilities.PublishCapabilityScope(context, None), None)
    assert result.validation.passed
    assert not result.public_access_checks
    persisted = context.db.load_product_model(product_id)
    item, unused = persisted["source"]["image_pool"]
    assert unused == before["source"]["image_pool"][1]
    assert item["url"].startswith("https://first.example.test/assets/")
    assert len(context.test_remote.uploads) == 1
    assert publish_capabilities.validate_product_publish(validate_request).validation_digest == result.validation.validation_digest
    again = publish_capabilities.prepare_product_publish(request)
    assert again.validation.validation_digest == result.validation.validation_digest
    assert len(context.test_remote.uploads) == 1

    configure(context, public_base_url="https://second.example.test", key_prefix="")
    assert [issue.code for issue in publish_capabilities.validate_product_publish(validate_request).errors] == ["IMAGE_DELIVERY_STALE"]
    refreshed = publish_capabilities.prepare_product_publish(request)
    assert refreshed.validation.passed
    assert refreshed.validation.validation_digest != result.validation.validation_digest
    assert len(context.test_remote.uploads) == 1
    assert context.db.load_product_model(product_id)["source"]["image_pool"][0]["url"].startswith("https://second.example.test/assets/")
    assert context.db.load_draft_model(request.draft_id)["images"] == before_draft["images"]


def test_prepare_diagnostics_use_persisted_actual_image_urls(boundary, monkeypatch):
    context, request, product_id = boundary
    calls = []
    monkeypatch.setattr(publish_capabilities, "check_image_public_access", lambda urls: calls.extend(urls) or [])
    publish_capabilities.prepare_product_publish(request.model_copy(update={"check_public_access": True}))
    item = context.db.load_product_model(product_id)["source"]["image_pool"][0]
    assert calls == [item["url"]]
    assert "/assets/" in calls[0]


def test_prepare_does_not_claim_persistence_if_save_failed(boundary, monkeypatch):
    from erp_web.runtime_units import image_delivery_persistence as image_pool
    _, request, _ = boundary
    monkeypatch.setattr(image_pool, "save_image_pool_for_product", lambda *args: {"ok": False, "error": "测试写入失败"})
    with pytest.raises(BusinessCapabilityError, match="交付字段写回失败"):
        publish_capabilities.prepare_product_publish(request)


def test_prepare_result_uses_readback_even_if_adapter_did_not_persist(boundary, monkeypatch):
    context, request, _ = boundary
    monkeypatch.setattr(_ImageAdapter, "prepare_product", lambda self, product, config: context.image_delivery.prepare_product(product, "yandex"))
    result = publish_capabilities.prepare_product_publish(request)
    assert not result.validation.passed
    assert [issue.code for issue in result.validation.errors] == ["IMAGE_NOT_PREPARED"]


def test_delivery_merges_only_delivery_fields_into_current_product(boundary):
    context, _, product_id = boundary
    original = context.products.load_product_from_index(product_id, "")
    prepared = deepcopy(original)
    prepared = context.image_delivery.prepare_product(original, "yandex")
    current = deepcopy(original)
    current["source"]["image_pool"][0]["note"] = "用户刚刚修改的说明"
    current["source"]["image_pool"].append({"id": "new", "url": "https://supplier.example/new.jpg"})
    context.products.save_product(current)
    persist_delivered_image_pool(prepared, original, "yandex")
    stored = context.db.load_product_model(product_id)["source"]["image_pool"]
    assert stored[0]["note"] == "用户刚刚修改的说明"
    assert stored[0]["url"] == prepared["source"]["image_pool"][0]["url"]
    assert any(item["id"] == "new" for item in stored)


@pytest.mark.parametrize("change", ["source", "selection", "config"])
def test_upload_concurrent_change_is_not_marked_prepared(boundary, change):
    context, request, product_id = boundary
    def concurrent_change():
        if change == "config":
            configure(context, bucket="another-bucket", key_prefix="")
        elif change == "source":
            from tests.image_hosting_test_utils import image_bytes
            original = context.db.load_product_model(product_id)
            from pathlib import Path
            Path(original["source"]["image_pool"][0]["path"]).write_bytes(image_bytes(color="blue"))
        else:
            draft = context.db.load_draft_model(request.draft_id)
            draft["images"] = [{"asset_id": "unused", "role": "main", "order": 0}]
            _, error, _ = context.products.save_draft_content(draft)
            assert error is None
    context.test_remote.on_put = concurrent_change
    with pytest.raises(BusinessCapabilityError) as error:
        publish_capabilities.prepare_product_publish(request)
    assert error.value.code == "IMAGE_DELIVERY_STALE"
    assert not context.db.load_product_model(product_id)["source"]["image_pool"][0].get("storage_key")


def test_upload_occurs_outside_product_write_lock(boundary):
    from concurrent.futures import ThreadPoolExecutor
    context, request, product_id = boundary
    def during_upload():
        def edit():
            with context.products.mutation_scope({"product_id": product_id}):
                current = context.db.load_product_model(product_id)
                current["source"]["image_pool"][0]["note"] = "上传期间编辑"
                context.products.save_product(current)
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(edit).result(timeout=2)
    context.test_remote.on_put = during_upload
    assert publish_capabilities.prepare_product_publish(request).validation.passed
    assert context.db.load_product_model(product_id)["source"]["image_pool"][0]["note"] == "上传期间编辑"


class _CaptureBus:
    def __init__(self):
        self.calls = []

    def recover_publish_job(self, **facts):
        return None

    def enqueue(self, product, platforms, **facts):
        self.calls.append({"product": deepcopy(product), "platforms": platforms, **deepcopy(facts)})
        return {"job_id": "test-frozen-job", "status": "queued"}


def _confirmed_request(request, validation):
    return ProductPublishRequest(
        draft_id=request.draft_id, platform=request.platform, site=request.site,
        idempotency_key="test-image-delivery-confirmation",
        confirmation=PublishRequestConfirmation(
            conversation_id="test", tool_call_id="test-call",
            validation_digest=validation.validation_digest,
            confirmed_at=datetime.now(timezone.utc),
        ),
    )


@pytest.mark.parametrize("change", ["config", "selection", "bytes"])
def test_queue_admission_rechecks_delivery_after_evaluation(boundary, monkeypatch, change):
    context, request, product_id = boundary
    validation = publish_capabilities.prepare_product_publish(request).validation
    bus = _CaptureBus()
    original_check = context.products.publish_queue_platforms
    def check_then_change(*args, **kwargs):
        eligible = original_check(*args, **kwargs)
        if change == "config":
            configure(context, bucket="other-images", key_prefix="")
        elif change == "selection":
            draft = context.db.load_draft_model(request.draft_id)
            draft["images"] = [{"asset_id": "unused", "role": "main", "order": 0}]
            _, error, _ = context.products.save_draft_content(draft)
            assert error is None
        else:
            from pathlib import Path
            from tests.image_hosting_test_utils import image_bytes
            item = context.db.load_product_model(product_id)["source"]["image_pool"][0]
            Path(item["path"]).write_bytes(image_bytes(color="blue"))
        return eligible
    monkeypatch.setattr(context.products, "publish_queue_platforms", check_then_change)
    with pytest.raises(BusinessCapabilityError) as error:
        publish_capabilities.request_product_publish(_confirmed_request(request, validation), publishing_bus=bus)
    assert error.value.code == "PUBLISH_CONFIRMATION_STALE"
    assert not bus.calls
    assert len(context.test_remote.uploads) == 1


def test_worker_uses_frozen_url_after_default_hosting_changes(boundary, monkeypatch):
    from types import SimpleNamespace
    from erp_web.runtime_units.publishing_bus_core import PublishingBus
    context, request, _ = boundary
    validation = publish_capabilities.prepare_product_publish(request).validation
    bus = _CaptureBus()
    result = publish_capabilities.request_product_publish(_confirmed_request(request, validation), publishing_bus=bus)
    assert result.job_id == "test-frozen-job"
    queued = bus.calls[0]
    approval = queued["approved_publications"]["yandex"]
    original_pictures = deepcopy(approval["payload"]["pictures"])
    configure(context, public_base_url="https://changed.example.test", bucket="changed-images", key_prefix="")
    def no_prepare(*args, **kwargs):
        pytest.fail("worker 不得重新准备素材或编译 payload")
    monkeypatch.setattr(context.image_delivery, "prepare_product", no_prepare)
    adapter = _ImageAdapter()
    monkeypatch.setattr(adapter, "prepare_product", no_prepare)
    monkeypatch.setattr(adapter, "build_payload", no_prepare)
    sent = []
    monkeypatch.setattr(adapter, "publish_payload", lambda payload, config: sent.append(payload) or {"ok": True})
    worker = SimpleNamespace(
        _approved_payload_for_worker=PublishingBus._approved_payload_for_worker,
        _set_platform=lambda *args, **kwargs: None,
    )
    state = {"product": queued["product"], "platforms": {"yandex": queued["targets"]["yandex"]}}
    PublishingBus._publish_approved_payload(
        worker, job_id=result.job_id, platform="yandex", state=state,
        approval=approval, adapter=adapter, config=context.config.load_store_config(), attempts=1,
    )
    assert sent[0]["pictures"] == original_pictures
    assert original_pictures[0].startswith("https://first.example.test/")
    assert len(context.test_remote.uploads) == 1


def test_queue_precheck_does_not_overwrite_edit_after_evaluation(boundary, monkeypatch):
    context, request, _ = boundary
    validation = publish_capabilities.prepare_product_publish(request).validation
    original_evaluate = publish_capabilities.evaluate_publish_validation
    def evaluate_then_edit(*args, **kwargs):
        result = original_evaluate(*args, **kwargs)
        draft = context.db.load_draft_model(request.draft_id)
        draft["title"] = "用户在校验后修改的标题"
        _, error, _ = context.products.save_draft_content(draft)
        assert error is None
        return result
    monkeypatch.setattr(publish_capabilities, "evaluate_publish_validation", evaluate_then_edit)
    bus = _CaptureBus()
    with pytest.raises(BusinessCapabilityError) as error:
        publish_capabilities.request_product_publish(_confirmed_request(request, validation), publishing_bus=bus)
    assert error.value.code == "PUBLISH_CONFIRMATION_STALE"
    assert not bus.calls
    assert context.db.load_draft_model(request.draft_id)["title"] == "用户在校验后修改的标题"
