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


def test_source_checks_readability_and_defers_decoding_to_prepare(tmp_path, monkeypatch):
    remote = FakeS3(monkeypatch)
    path = tmp_path / "broken.png"
    path.write_bytes(b"readable but not an image")
    delivery = service(tmp_path, {"image_hosting": {"default_profile_id": "images-main", "profiles": [profile()]}})
    assert delivery.inspect_product(_product(path), "ozon", stage="source") == []
    with pytest.raises(ImageHostingError, match="内容损坏"):
        delivery.prepare_product(_product(path), "ozon")
    assert not remote.uploads
    path.write_bytes(b"")
    assert delivery.inspect_product(_product(path), "ozon", stage="source")[0].code == "IMAGE_SOURCE_MISSING"


def test_inspection_reuses_shared_assets_only_within_one_validation(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from copy import deepcopy
    from erp_web.services.image_delivery_service import ImageInspectionCache

    FakeS3(monkeypatch)
    path = tmp_path / "main.png"
    path.write_bytes(image_bytes())
    config = {"image_hosting": {"default_profile_id": "images-main", "profiles": [profile()]}}
    delivery = service(tmp_path, config)
    prepared = delivery.prepare_product(_product(path), "ozon")
    inspect = Mock(wraps=delivery._inspect_image)
    monkeypatch.setattr(delivery, "_inspect_image", inspect)
    cache = ImageInspectionCache()
    for _ in range(10):
        assert delivery.inspect_product(prepared, "ozon", cache=cache) == []
    assert inspect.call_count == 1

    # 同一 ID 的交付资料被改写时，不能使用原资产的成功结果。
    changed = deepcopy(prepared)
    changed["source"]["image_pool"][0]["url"] = "https://wrong.example.test/image.png"
    assert delivery.inspect_product(changed, "ozon", cache=cache)[0].code == "IMAGE_DELIVERY_STALE"
    assert inspect.call_count == 2

    # 下一次校验重读文件及配置，不能沿用上一次成功结果。
    path.write_bytes(image_bytes(color="blue"))
    assert delivery.inspect_product(prepared, "ozon")[0].code == "IMAGE_DELIVERY_STALE"
    path.write_bytes(image_bytes())
    config["image_hosting"]["profiles"][0]["bucket"] = "changed"
    assert delivery.inspect_product(prepared, "ozon")[0].code == "IMAGE_DELIVERY_STALE"


def test_prepare_uses_bounded_parallelism_and_keeps_request_context(tmp_path):
    from contextvars import ContextVar
    from threading import Barrier, Lock, get_ident
    from erp_web.services.image_content import image_content
    from erp_web.services.image_delivery_service import IMAGE_DELIVERY_WORKERS
    from erp_web.schemas.image_hosting import DeliveredImage
    from erp_web.services.image_hosting_config import content_storage_key, public_url, target_fingerprint

    request = ContextVar("image_test_request", default="missing")
    token = request.set("same-request")
    config = {"image_hosting": {"default_profile_id": "images-main", "profiles": [profile()]}}
    barrier, lock = Barrier(IMAGE_DELIVERY_WORKERS), Lock()
    active, peak = 0, 0
    seen, owners = [], []

    class Provider:
        def __init__(self, config):
            self.owner = get_ident()
            owners.append(self.owner)
            self.config = config

        def deliver(self, *, source_path, **_):
            nonlocal active, peak
            assert get_ident() == self.owner
            assert request.get() == "same-request"
            with lock:
                active += 1
                peak = max(peak, active)
                seen.append(source_path.name)
            barrier.wait(timeout=5)
            digest, suffix, _ = image_content(source_path.read_bytes())
            key = content_storage_key(self.config, digest, suffix)
            with lock:
                active -= 1
            return DeliveredImage(key, public_url(self.config, key), digest, "s3_compatible", self.config["id"], target_fingerprint(self.config))

    product = {"source": {"image_pool": []}, "drafts": {"ozon": {"images": []}}}
    for index in range(8):
        path = tmp_path / f"{index}.png"
        path.write_bytes(image_bytes(color=(index, 0, 0)))
        product["source"]["image_pool"].append({"id": str(index), "path": str(path)})
        product["drafts"]["ozon"]["images"].append({"asset_id": str(index)})
    delivery = ImageDeliveryService(AppPaths.from_app_dir(tmp_path), lambda: config, Provider)
    progress, reporting_threads = [], []
    caller_thread = get_ident()
    def report(event):
        progress.append(event)
        reporting_threads.append(get_ident())
    try:
        result = delivery.prepare_product(product, "ozon", on_progress=report)
    finally:
        request.reset(token)
    assert [event["completed"] for event in progress] == list(range(9))
    assert {event["total"] for event in progress} == {8}
    assert set(reporting_threads) == {caller_thread}
    assert peak == IMAGE_DELIVERY_WORKERS
    assert len(set(owners)) == IMAGE_DELIVERY_WORKERS
    assert len(seen) == 8
    assert [item["id"] for item in result["source"]["image_pool"]] == [str(i) for i in range(8)]
    assert all(item["content_sha256"] for item in result["source"]["image_pool"])
    assert not any(item.get("content_sha256") for item in product["source"]["image_pool"])


def test_prepare_failure_stops_scheduling_remaining_images(tmp_path):
    from threading import Lock
    from erp_web.services.image_delivery_service import IMAGE_DELIVERY_WORKERS

    lock = Lock()
    started = []
    class Provider:
        def __init__(self, _):
            pass

        def deliver(self, *, source_path, **_):
            with lock:
                started.append(source_path)
            raise ImageHostingError("IMAGE_UPLOAD_OUTCOME_UNKNOWN", "上传结果未知")

    config = {"image_hosting": {"default_profile_id": "images-main", "profiles": [profile()]}}
    product = {"source": {"image_pool": []}, "drafts": {"ozon": {"images": []}}}
    for index in range(12):
        path = tmp_path / f"{index}.png"
        path.write_bytes(image_bytes())
        product["source"]["image_pool"].append({"id": str(index), "path": str(path)})
        product["drafts"]["ozon"]["images"].append({"asset_id": str(index)})
    delivery = ImageDeliveryService(AppPaths.from_app_dir(tmp_path), lambda: config, Provider)
    with pytest.raises(ImageHostingError) as error:
        delivery.prepare_product(product, "ozon")
    assert error.value.code == "IMAGE_UPLOAD_OUTCOME_UNKNOWN"
    assert 1 <= len(started) <= IMAGE_DELIVERY_WORKERS
    assert not any(item.get("content_sha256") for item in product["source"]["image_pool"])


def test_parallel_storage_delivery_keeps_objects_reusable(tmp_path, monkeypatch):
    remote = FakeS3(monkeypatch)
    config = {"image_hosting": {"default_profile_id": "images-main", "profiles": [profile()]}}
    delivery = service(tmp_path, config)
    product = {"source": {"image_pool": []}, "drafts": {"ozon": {"images": []}}}
    for index in range(5):
        path = tmp_path / f"{index}.png"
        path.write_bytes(image_bytes(color=(index, 0, 0)))
        product["source"]["image_pool"].append({"id": str(index), "path": str(path)})
        product["drafts"]["ozon"]["images"].append({"asset_id": str(index)})
    prepared = delivery.prepare_product(product, "ozon")
    assert len(remote.uploads) == 5
    assert not delivery.inspect_product(prepared, "ozon")
    assert delivery.prepare_product(prepared, "ozon") == prepared
    assert len(remote.uploads) == 5


def test_progress_counts_unique_images_including_reuse_and_external(tmp_path, monkeypatch):
    remote = FakeS3(monkeypatch)
    path = tmp_path / "main.png"
    path.write_bytes(image_bytes())
    delivery = service(tmp_path, {"image_hosting": {"default_profile_id": "images-main", "profiles": [profile()]}})
    product = _product(path)
    product["source"]["image_pool"].append({"id": "external", "url": "https://supplier.example/image.png"})
    product["drafts"]["ozon"]["images"].extend([{"asset_id": "main"}, {"asset_id": "external"}])
    for _ in range(2):
        events = []
        product = delivery.prepare_product(product, "ozon", on_progress=events.append)
        assert [event["completed"] for event in events] == [0, 1, 2]
        assert {event["total"] for event in events} == {2}
    assert len(remote.uploads) == 1
