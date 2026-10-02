"""图片交付的来源分类、目标切换与只读边界。"""
from pathlib import Path

import pytest

from erp_web.context import AppPaths
from erp_web.schemas.image_hosting import ImageHostingError
from erp_web.services.image_delivery_service import ImageDeliveryService
from tests.image_hosting_test_utils import FakeS3, image_bytes, profile


def _product(path: Path) -> dict:
    return {"source": {"image_pool": [{"id": "main", "path": str(path), "selected": True}]},
            "drafts": {"ozon": {"images": [{"asset_id": "main"}]}}}


def service(tmp_path, config):
    return ImageDeliveryService(AppPaths.from_app_dir(tmp_path), lambda: config)


def test_source_stage_accepts_local_and_readonly_inspection_never_uploads(tmp_path, monkeypatch):
    remote = FakeS3(monkeypatch)
    path = tmp_path / "wrong-extension.jpg"
    path.write_bytes(image_bytes())
    config = {"image_hosting": {"default_profile_id": "images-main", "profiles": [profile()]}}
    delivery = service(tmp_path, config)
    product = _product(path)
    assert delivery.inspect_product(product, "ozon", stage="source") == []
    assert [issue.code for issue in delivery.inspect_product(product, "ozon")] == ["IMAGE_NOT_PREPARED"]
    assert not remote.calls
    prepared = delivery.prepare_product(product, "ozon")
    asset = prepared["source"]["image_pool"][0]
    assert asset["storage_key"].endswith(".png")
    assert asset["url"].startswith("https://images.example.test/products/assets/")
    assert not delivery.inspect_product(prepared, "ozon")
    assert delivery.prepare_product(prepared, "ozon") == prepared
    assert len(remote.uploads) == 1


def test_target_change_requires_upload_and_cannot_use_missing_source(tmp_path, monkeypatch):
    remote = FakeS3(monkeypatch)
    path = tmp_path / "main.png"; path.write_bytes(image_bytes())
    config = {"image_hosting": {"default_profile_id": "images-main", "profiles": [profile()]}}
    delivery = service(tmp_path, config)
    prepared = delivery.prepare_product(_product(path), "ozon")
    config["image_hosting"]["profiles"][0]["bucket"] = "another-bucket"
    assert delivery.inspect_product(prepared, "ozon")[0].code == "IMAGE_DELIVERY_STALE"
    changed = delivery.prepare_product(prepared, "ozon")
    assert len(remote.uploads) == 2
    path.unlink()
    config["image_hosting"]["profiles"][0]["public_base_url"] = "https://another.example.test"
    with pytest.raises(ImageHostingError) as error:
        delivery.prepare_product(changed, "ozon")
    assert error.value.code == "IMAGE_SOURCE_MISSING"
    assert len(remote.uploads) == 2


def test_external_links_do_not_need_storage_but_managed_or_local_urls_do(tmp_path):
    delivery = service(tmp_path, {})
    product = _product(tmp_path / "missing.png")
    item = product["source"]["image_pool"][0]
    item.pop("path"); item["url"] = "https://supplier.example.test/a.png"
    assert not delivery.inspect_product(product, "ozon")
    assert delivery.prepare_product(product, "ozon")["source"]["image_pool"][0]["url"] == item["url"]
    item["delivery_provider"] = "retired-provider"
    assert delivery.inspect_product(product, "ozon")[0].code == "IMAGE_HOSTING_NOT_CONFIGURED"
    with pytest.raises(ImageHostingError, match="需要配置图片托管"):
        delivery.prepare_product(product, "ozon")


def test_source_and_config_mutation_during_upload_are_stale(tmp_path, monkeypatch):
    remote = FakeS3(monkeypatch)
    path = tmp_path / "main.png"; path.write_bytes(image_bytes())
    config = {"image_hosting": {"default_profile_id": "images-main", "profiles": [profile()]}}
    delivery = service(tmp_path, config)
    remote.on_put = lambda: path.write_bytes(image_bytes(color="blue"))
    with pytest.raises(ImageHostingError) as error:
        delivery.prepare_product(_product(path), "ozon")
    assert error.value.code == "IMAGE_DELIVERY_STALE"
    remote.objects.clear()
    remote.on_put = lambda: config["image_hosting"]["profiles"][0].update(bucket="changed-bucket")
    with pytest.raises(ImageHostingError) as error:
        delivery.prepare_product(_product(path), "ozon")
    assert error.value.code == "IMAGE_DELIVERY_STALE"


@pytest.mark.parametrize("field,value", [("storage_key", "products/assets/forged.png"), ("content_sha256", "invalid-hash")])
def test_readonly_validation_rejects_forged_delivery_identity_without_network(tmp_path, monkeypatch, field, value):
    remote = FakeS3(monkeypatch)
    path = tmp_path / "main.png"
    path.write_bytes(image_bytes())
    config = {"image_hosting": {"default_profile_id": "images-main", "profiles": [profile()]}}
    delivery = service(tmp_path, config)
    prepared = delivery.prepare_product(_product(path), "ozon")
    path.unlink()
    asset = prepared["source"]["image_pool"][0]
    asset[field] = value
    from erp_web.services.image_hosting_config import public_url
    asset["url"] = public_url(profile(), asset["storage_key"])
    calls = len(remote.calls)
    assert delivery.inspect_product(prepared, "ozon")[0].code == "IMAGE_DELIVERY_STALE"
    assert len(remote.calls) == calls
