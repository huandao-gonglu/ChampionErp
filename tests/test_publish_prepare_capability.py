"""发布准备工具的持久化、回读、范围和只读边界回归。"""

from copy import deepcopy
from dataclasses import asdict

import pytest

from erp_web.runtime_units import publish_capabilities
from erp_web.runtime_units.publish_adapter import YandexPublishingAdapter, persist_materialized_image_pool
from erp_web.runtime_units.publish_helpers import _draft_images
from erp_web.schemas.publish_capabilities import ProductPublishPrepareRequest, ProductPublishValidateRequest
from erp_web.services.capability_errors import BusinessCapabilityError
from tests.runtime_test_utils import temp_app_context
from tests.test_publish_image_materialization import _yandex_product


class _ImageAdapter(YandexPublishingAdapter):
    """保留真实图片准备/持久化边界，隔离与此测试无关的类目、核价规则。"""

    def validate_draft(self, context, config):
        from erp_web.context import get_context
        issues = get_context().image_delivery.inspect_product(context.product, "yandex")
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
    monkeypatch.setenv("ERP_IMAGE_HTTPS_PROVIDER", "local_static")
    monkeypatch.setenv("ERP_IMAGE_HTTPS_BASE_URL", "https://first.example.test")
    monkeypatch.setenv("ERP_IMAGE_HTTPS_ROOT", str(tmp_path / "public"))
    monkeypatch.setattr(publish_capabilities, "publishing_adapter_for", lambda platform: _ImageAdapter())
    with temp_app_context(tmp_path / "app") as context:
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
    destination = tmp_path / "public" / item["storage_key"]
    timestamp = destination.stat().st_mtime_ns
    assert publish_capabilities.validate_product_publish(validate_request).validation_digest == result.validation.validation_digest
    again = publish_capabilities.prepare_product_publish(request)
    assert again.validation.validation_digest == result.validation.validation_digest
    assert destination.stat().st_mtime_ns == timestamp

    monkeypatch.setenv("ERP_IMAGE_HTTPS_BASE_URL", "https://second.example.test")
    assert [issue.code for issue in publish_capabilities.validate_product_publish(validate_request).errors] == ["IMAGE_PUBLIC_URL_STALE"]
    refreshed = publish_capabilities.prepare_product_publish(request)
    assert refreshed.validation.passed
    assert refreshed.validation.validation_digest != result.validation.validation_digest
    assert destination.stat().st_mtime_ns == timestamp
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
    from erp_web.runtime_units import image_pool
    _, request, _ = boundary
    monkeypatch.setattr(image_pool, "save_image_pool_for_product", lambda *args: {"ok": False, "error": "测试写入失败"})
    with pytest.raises(BusinessCapabilityError, match="测试写入失败"):
        publish_capabilities.prepare_product_publish(request)


def test_prepare_result_uses_readback_even_if_adapter_did_not_persist(boundary, monkeypatch):
    context, request, _ = boundary
    monkeypatch.setattr(_ImageAdapter, "prepare_product", lambda self, product, config: context.image_delivery.prepare_product(product, "yandex"))
    result = publish_capabilities.prepare_product_publish(request)
    assert not result.validation.passed
    assert [issue.code for issue in result.validation.errors] == ["IMAGE_NOT_PREPARED"]


def test_materialization_merges_only_delivery_fields_into_current_product(boundary):
    context, _, product_id = boundary
    original = context.products.load_product_from_index(product_id, "")
    prepared = deepcopy(original)
    prepared["source"]["image_pool"][0].update(url="https://test.example/a.jpg", storage_key="assets/a.jpg", delivery_provider="local_static")
    current = deepcopy(original)
    current["source"]["image_pool"][0]["note"] = "用户刚刚修改的说明"
    current["source"]["image_pool"].append({"id": "new", "url": "https://supplier.example/new.jpg"})
    context.products.save_product(current)
    persist_materialized_image_pool(prepared, original)
    stored = context.db.load_product_model(product_id)["source"]["image_pool"]
    assert stored[0]["note"] == "用户刚刚修改的说明"
    assert stored[0]["url"] == "https://test.example/a.jpg"
    assert any(item["id"] == "new" for item in stored)
