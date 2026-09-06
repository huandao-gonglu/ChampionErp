"""SKU 图片从采集、持久化、草稿覆盖到发布主图的回归。"""

from copy import deepcopy

from erp_web.product_model.merge_model import merge_source_partial_result, normalize_product_model
from erp_web.product_model.draft_image_model import draft_image_refs_from_pool
from erp_web.product_model.sku_model import collected_skus, selected_skus
from erp_web.product_model.sku_image_model import ensure_source_image_asset, replace_draft_sku_images
from erp_web.runtime_units.collect_helpers import normalize_collect_source_images
from erp_web.runtime_units.sku_publish_adapter import SkuGroupPublishingAdapter
from erp_web.runtime_units.sku_publish_projection import grouping_contract, sku_context
from erp_web.services import image_service
from tests.runtime_test_utils import temp_app_context
from tests.test_sku_workflow import ItemBoundary, context_for, product_fixture


def test_collection_materializes_all_sku_images_beyond_gallery_limit(tmp_path, monkeypatch):
    downloaded = []

    def download(_app, url, _product, index):
        downloaded.append(url)
        return {"id": f"download-{index}", "url": url, "status": "ready"}

    monkeypatch.setattr(image_service, "download_remote_image", download)
    raw = {"source_url": "https://example.test/item", "currency": "CNY",
           "images": [f"https://example.test/main-{index}.jpg" for index in range(8)],
           "skus": [{"id": str(index), "image": f"https://example.test/sku-{index}.jpg"} for index in range(29)]}
    raw["skus"].append({"id": "shared", "image": raw["skus"][0]["image"]})
    with temp_app_context(tmp_path):
        source = normalize_collect_source_images(raw, "1688", "browser")
        skus = collected_skus(source)
        assert len(downloaded) == 34
        assert len(source["image_pool"]) == 34
        assert len(draft_image_refs_from_pool({"source": source})) == 5
        assert skus[0]["image_asset_id"] == skus[-1]["image_asset_id"]
        assert all(sku["image_asset_id"] for sku in skus)
        assert all(sku["image_asset_id"] != source["skus"][index]["image"] for index, sku in enumerate(skus))
        reordered = {**raw, "skus": list(reversed(raw["skus"]))}
        again = collected_skus(normalize_collect_source_images(reordered, "1688", "browser"))
        assert {sku["source_sku_id"]: sku["image_asset_id"] for sku in skus} == {sku["source_sku_id"]: sku["image_asset_id"] for sku in again}


def test_materializing_single_sku_asset_preserves_its_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(image_service, "download_remote_image", lambda *_args: {
        "id": "downloaded", "url": "https://example.test/red.jpg", "status": "ready",
        "is_main": True, "selected": True, "usage": "main",
    })
    pool = []
    asset_id = ensure_source_image_asset(pool, "https://example.test/red.jpg")
    [asset] = image_service.materialize_image_values(tmp_path, pool)
    assert asset["id"] == asset_id
    assert asset["is_sku"] is True
    assert asset["is_main"] is False
    assert asset["selected"] is False
    assert asset["usage"] == "other"
    assert draft_image_refs_from_pool({"source": {"image_pool": [asset]}}) == []


def test_persisted_addresses_migrate_once_without_network():
    product = product_fixture()
    url = "https://example.test/red.jpg"
    product["sku_items"][0].update({"image": url, "source_snapshot": {"image": url}})
    product["drafts"]["ozon"]["sku_items"][0]["overrides"] = {"image": "https://example.test/edited.jpg"}
    migrated = normalize_product_model(product)
    sku = migrated["sku_items"][0]
    assert "image" not in sku and "image" not in sku["source_snapshot"]
    assert sku["image_asset_id"] == sku["source_snapshot"]["image_asset_id"]
    assert len(migrated["source"]["image_pool"]) == 2
    assert migrated["source"]["image_pool"][0]["status"] == "pending_download"
    assert normalize_product_model(migrated) == migrated
    assert product["sku_items"][0]["image"] == url


def test_image_asset_identity_survives_delivery_url_change_and_draft_override():
    product = product_fixture()
    product["source"]["image_pool"] = [{"id": "source-red", "url": "https://new-cdn.test/red.jpg"}, {"id": "edited", "url": "https://new-cdn.test/edited.jpg"}]
    product["sku_items"][0]["image_asset_id"] = "source-red"
    draft = product["drafts"]["ozon"]
    draft["images"] = [{"asset_id": "source-red", "role": "main", "order": 0}]
    draft["sku_items"][0]["overrides"] = {"image_asset_id": "edited"}
    context = context_for(product)
    fact, row = selected_skus(product, draft)[0]
    result = sku_context(context, fact, row, grouping_contract(context))
    assert result.draft["images"][0]["asset_id"] == "edited"
    assert product["sku_items"][0]["image_asset_id"] == "source-red"
    assert row["pricing"]["applied"] is True
    assert draft["images"][0]["asset_id"] == "source-red"


def test_precheck_reports_every_missing_image_with_sku_and_keeps_stock_errors():
    product = product_fixture()
    for sku, row in zip(product["sku_items"], product["drafts"]["ozon"]["sku_items"]):
        sku["image_asset_id"] = "deleted"
        row["stock"] = ""
    result = SkuGroupPublishingAdapter(ItemBoundary()).validate_draft(context_for(product), {})
    errors = result["errors"]
    assert len(errors) == 4
    assert {issue["field"] for issue in errors if issue["field"].endswith("image_asset_id")} == {"sku_items.fact-0.image_asset_id", "sku_items.fact-1.image_asset_id"}
    assert sum("库存" in issue["message"] for issue in errors) == 2


def test_recollection_preserves_manually_chosen_asset_and_updates_source_snapshot(tmp_path):
    source = {"source_url": "https://example.test/item", "currency": "CNY", "skus": [{"id": "red", "image": "https://example.test/red.jpg"}]}
    original = {"source": source, "sku_items": collected_skus(source)}
    original["source"]["image_pool"].append({"id": "uploaded", "url": "https://example.test/uploaded.jpg", "origin": "local_upload"})
    original["sku_items"][0]["image_asset_id"] = "uploaded"
    incoming = deepcopy(source)
    incoming["skus"][0]["image"] = "https://example.test/red-new.jpg"
    incoming["image_pool"] = []
    new_sku = collected_skus(incoming)[0]
    updated = merge_source_partial_result(original, incoming, {"success": True})
    assert updated["sku_items"][0]["image_asset_id"] == "uploaded"
    assert updated["sku_items"][0]["source_snapshot"]["image_asset_id"] == new_sku["image_asset_id"]
    assert any(asset["id"] == "uploaded" for asset in updated["source"]["image_pool"])


def test_explicit_processed_image_replacement_only_changes_current_draft():
    product = product_fixture()
    product["sku_items"][0]["image_asset_id"] = "red"
    draft = deepcopy(product["drafts"]["ozon"])
    replace_draft_sku_images(product, draft, [{"id": "translated", "derived_from_id": "red"}])
    assert draft["sku_items"][0]["overrides"]["image_asset_id"] == "translated"
    assert draft["sku_items"][0]["pricing"]["applied"] is True
    assert product["sku_items"][0]["image_asset_id"] == "red"
    assert "overrides" not in product["drafts"]["ozon"]["sku_items"][0]


def test_saved_sku_image_override_survives_reload_without_changing_product(tmp_path):
    with temp_app_context(tmp_path) as app:
        product = product_fixture()
        product["source"]["image_pool"] = [{"id": "original", "url": "https://example.test/original.jpg"}, {"id": "edit", "url": "https://example.test/edit.jpg"}]
        product["sku_items"][0]["image_asset_id"] = "original"
        saved = app.products.save_product(product)
        draft = saved["drafts"]["ozon"]
        draft["sku_items"][0]["overrides"] = {"image_asset_id": "edit"}
        _, error, status = app.products.save_draft_detail(draft)
        assert error is None and status == 200
        loaded = app.products.load_product_from_index(saved["product_id"], "")
        assert loaded["sku_items"][0]["image_asset_id"] == "original"
        assert app.db.load_draft_model("sku-draft")["sku_items"][0]["overrides"]["image_asset_id"] == "edit"
