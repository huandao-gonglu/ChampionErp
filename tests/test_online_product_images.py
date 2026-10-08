"""在线图片编辑：来源身份、资产版本、平台写入与原生图集回读。"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from erp_web.context import get_context
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.runtime_units.online_mercadolibre import MercadoOnlineAdapter
from erp_web.runtime_units.online_mercadolibre_images import prepare_mercado_pictures
from erp_web.schemas.online_products import Capability, OnlineListing
from erp_web.services.online_product_service import OnlineProductService


class ImageRemote:
    account_id = "seller"

    def __init__(self):
        self.row = OnlineListing(id="online-1", platform="mercadolibre", account_id=self.account_id,
            remote_id="CBT1", model="traditional_global_items", content={"pictures": [{"id": "p1", "url": "https://images.example.com/old.jpg"}]},
            capabilities={"content": Capability(enabled=True, fields=["pictures"])})
        self.writes, self.preparations = [], []
        self.on_prepare = lambda: None

    def read(self, remote_id):
        assert remote_id == self.row.remote_id
        return self.row.model_copy(deep=True)

    def prepare_pictures(self, pictures, assets):
        self.preparations.append(deepcopy(assets))
        self.on_prepare()
        return [{"id": "uploaded-" + picture["asset_id"]} if "asset_id" in picture else picture for picture in pictures]

    def write(self, listing, operation, scope, changes):
        self.writes.append(deepcopy(changes))
        self.row.content.update(deepcopy(changes))
        return {"success": True}

    def read_confirmation(self, listing, request):
        return self.row.model_copy(deep=True)

    def confirmation_details(self, *_args):
        return {"pending": False, "errors": []}


@pytest.fixture
def online_images(monkeypatch):
    context = get_context()
    monkeypatch.setattr(context.config, "load_store_config", lambda: {
        "mercadolibre": {"user_id": "seller"}, "yandex": {"business_id": "business", "campaign_id": "campaign"},
    })
    remote = ImageRemote()
    service = OnlineProductService(context, adapter_factories={"mercadolibre": lambda config: remote, "yandex": lambda config: remote}, start_worker=False)
    service.store.save(remote.row)
    yield service, remote
    service.close()


def source_product(*, product_id="source-1", account="seller", platform="mercadolibre", local=False, sku=False):
    context = get_context()
    asset = {"id": "asset-1", "url": "https://images.example.com/new.jpg", "status": "ready"}
    if local:
        path = context.paths.app_dir / f"{product_id}.png"
        Image.new("RGB", (30, 30), "red").save(path)
        asset.update(path=str(path), url="")
    state = {"publication": {"model": "traditional_global_items", "parent_item_id": "CBT1", "account_user_id": account}}
    if platform == "yandex":
        state = {"offer_id": "offer-1", "business_id": "business", "campaign_id": "campaign"}
    site = "cbt" if platform == "mercadolibre" else "global"
    draft = {"draft_id": "draft-" + product_id, "platform": platform, "site": site, "title": "同名商品",
             "target_sites": [{"platform": platform, "site": site}],
             "publication": state.get("publication", {}), "last_publish_task": state,
             "images": [{"asset_id": "asset-1", "role": "main", "order": 0}]}
    if sku:
        draft.update(publication={}, last_publish_task={}, sku_items=[{"sku_id": "sku-1", "sku": "SELL-1", "selected": True,
            "publications": {f"{platform}:{site}": {"status": "published", "result": state}}}])
    saved = context.products.save_product({"product_id": product_id, "name": "同名商品", "source": {"image_pool": [asset]},
        "sku_items": [{"id": "sku-1", "name": "规格 1", "active": True}], "drafts": {platform: draft}})
    if sku:
        context.products.save_sku_publication(draft["draft_id"], "sku-1", f"{platform}:{site}", {"status": "published", "result": state})
    return context.db.load_product_model(saved["product_id"])


def request(service, pictures):
    return {"listing_id": "online-1", "version": service.store.get("online-1").version, "operation": "content",
            "changes": {"pictures": pictures}, "idempotency_key": uuid4().hex}


def chosen(service):
    option = service.source_images("online-1")["images"][0]
    return {key: option[key] for key in ("asset_id", "fingerprint")}


def test_source_association_requires_published_id_and_account(online_images):
    service, _ = online_images
    source_product(account="another-seller")
    selection = service.source_images("online-1")
    assert selection["images"] == [] and "未关联" in selection["reason"]
    source_product()
    selection = service.source_images("online-1")
    assert selection["local_draft_id"] == "draft-source-1"
    assert selection["images"][0]["asset_id"] == "asset-1"


def test_existing_ai_read_can_include_same_source_image_selection(online_images):
    from erp_web.runtime_units.online_product_capabilities import OnlineProductCapabilityScope, online_products_read
    from erp_web.schemas.online_product_capabilities import OnlineReadRequest
    service, _ = online_images
    source_product()
    result = online_products_read(OnlineReadRequest(id="online-1", include_source_images=True), OnlineProductCapabilityScope(lambda: service))
    assert result.item.id == "online-1"
    assert result.source_images.model_dump() == service.source_images("online-1")


def test_sku_publication_can_associate_and_ambiguous_drafts_are_blocked(online_images):
    service, _ = online_images
    source_product(sku=True)
    assert len(service.source_images("online-1")["images"]) == 1
    source_product(product_id="second-source", sku=True)
    selection = service.source_images("online-1")
    assert not selection["images"] and "多个源草稿" in selection["reason"]


def test_local_preview_uses_file_route_and_changes_only_online_images(online_images):
    service, remote = online_images
    product = source_product(local=True)
    original = get_context().db.load_product_model(product["product_id"])
    assert service.source_images("online-1")["images"][0]["preview_url"].startswith("/file?path=")
    body = request(service, [remote.row.content["pictures"][0], chosen(service)])
    job = service.change(body)["job"]
    assert service.run_once()
    assert remote.writes[0]["pictures"][1] == {"id": "uploaded-asset-1"}
    saved = service.store.job(job["id"])
    assert saved["result"]["prepared_changes"] == remote.writes[0]
    # 回读比较平台 ID，不比较本地引用；手动确认不再次上传。
    service.reconcile(job["id"])
    assert service.store.job(job["id"])["status"] == "confirmed"
    assert len(remote.preparations) == 1 and len(remote.writes) == 1
    assert get_context().db.load_product_model(product["product_id"]) == original


@pytest.mark.parametrize("change", ["bytes", "deleted"])
def test_queued_source_change_stops_before_upload_and_online_write(online_images, change):
    service, remote = online_images
    product = source_product(local=True)
    job = service.change(request(service, [chosen(service)]))["job"]
    if change == "bytes":
        Image.new("RGB", (30, 30), "blue").save(product["source"]["image_pool"][0]["path"])
    else:
        service.context.db.delete_product_model(product["product_id"])
    assert service.run_once()
    assert not remote.preparations and not remote.writes
    assert service.store.job(job["id"])["status"] == "failed"


def test_platform_change_during_preparation_stops_write(online_images):
    service, remote = online_images
    source_product()
    remote.on_prepare = lambda: setattr(remote.row, "title", "平台并发修改")
    job = service.change(request(service, [chosen(service)]))["job"]
    assert service.run_once()
    assert not remote.writes
    assert service.store.job(job["id"])["status"] == "failed"


@pytest.mark.parametrize("pictures", [[{"asset_id": "other", "fingerprint": "a" * 64}],
    [{"asset_id": "asset-1", "fingerprint": "changed"}], ["https://images.example.com/arbitrary.jpg"],
    [{"id": "not-current", "url": "https://images.example.com/fake.jpg"}], []])
def test_rejects_arbitrary_or_invalid_image_refs(online_images, pictures):
    service, _ = online_images
    source_product()
    with pytest.raises(ValueError):
        service.change(request(service, pictures))


def test_yandex_reuses_image_delivery_for_selected_assets_only(online_images):
    service, remote = online_images
    remote.account_id = remote.row.account_id = "business:campaign"
    remote.row.platform, remote.row.remote_id = "yandex", "offer-1"
    remote.row.content["pictures"] = ["https://images.example.com/old.jpg"]
    service.store.save(remote.row)
    original = source_product(platform="yandex", sku=True)
    body = request(service, [chosen(service), remote.row.content["pictures"][0]])
    job = service.change(body)["job"]
    assert service.run_once()
    assert remote.writes == [{"pictures": ["https://images.example.com/new.jpg", "https://images.example.com/old.jpg"]}]
    assert service.store.job(job["id"])["status"] == "submitted"
    assert get_context().db.load_product_model(original["product_id"])["source"]["image_pool"] == original["source"]["image_pool"]


def test_mercado_upload_uses_existing_client_and_cleans_temp_files(monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (30, 30), "red").save(source)
    paths = []
    def upload(path, token):
        assert token == "token" and path.read_bytes() == source.read_bytes()
        paths.append(path)
        return {"id": "uploaded-id"}
    monkeypatch.setattr("erp_web.runtime_units.online_mercadolibre_images.upload_mercadolibre_picture", upload)
    pictures = prepare_mercado_pictures(SimpleNamespace(token="token"), [{"asset_id": "asset"}], {"asset": {"path": str(source)}})
    assert pictures == [{"id": "uploaded-id"}]
    assert all(not path.exists() for path in paths) and source.exists()


@pytest.mark.parametrize("siteless", ["", "U123"])
def test_mercado_linking_respects_listing_model(monkeypatch, online_images, siteless):
    _, remote = online_images
    adapter = object.__new__(MercadoOnlineAdapter)
    adapter.token = "token"
    remote.row.snapshot = {"siteless_id": siteless}
    calls = []
    monkeypatch.setattr("erp_web.runtime_units.online_mercadolibre.request_json", lambda *args: calls.append(args) or {"success": True})
    adapter.write(remote.row, "content", "", {"pictures": [{"id": "p1"}, {"id": "new"}]})
    if siteless:
        assert len(calls) == 1 and "/global/user-products/U123" in calls[0][1]
    else:
        assert calls[0][:2] == ("POST", "https://api.mercadolibre.com/items/CBT1/pictures")
        assert calls[1][:2] == ("PUT", "https://api.mercadolibre.com/global/items/CBT1")


def test_linked_picture_followed_by_rejection_is_unknown_not_retryable(online_images):
    service, remote = online_images
    source_product()
    def write(*args):
        raise PublishAdapterError("REJECTED", "新图片已关联但后续修改失败", details={"http_status": 400, "remote_write_dispatched": True})
    remote.write = write
    job = service.change(request(service, [chosen(service)]))["job"]
    assert service.run_once()
    assert service.store.job(job["id"])["status"] == "outcome_unknown"
