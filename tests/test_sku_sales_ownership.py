"""SKU 销售资料唯一来源及 UPC 整批分配的业务回归。"""

from copy import deepcopy

import pytest

from erp_web.context import get_context
from erp_web.product_model import normalize_product_model
from erp_web.product_model.sku_model import single_sku_publish_draft
from erp_web.runtime_units.product_write_capabilities import _ai_draft_read_view


def sales_product():
    return {
        "product_id": "sales-owner", "name": "销售资料测试",
        "sku_items": [{"id": "first", "name": "黑色", "active": True, "barcode": "123456789012",
                       "supplier_stock": "9709839", "cost_cny": "7",
                       "package_dimensions": {"length_cm": "13", "width_cm": "10", "height_cm": "5", "weight_kg": "0.65"}}],
        "drafts": {"ozon": {
            "draft_id": "draft-sales-owner", "platform": "ozon", "site": "global", "title": "销售资料测试",
            "target_sites": [{"platform": "ozon", "site": "global"}],
            "sku": "OLD-CODE", "stock": "100", "upc": "OLD-BARCODE",
            "package_dimensions": {"length_cm": "99"}, "publication": {"item_id": "OLD-ID"},
            "sku_items": [{"sku_id": "first", "selected": True, "sku": "SELL-FIRST", "stock": "9709839",
                           "overrides": {"package_dimensions": {"length_cm": "26"}},
                           "pricing": {"applied": True, "targets": {"ozon:global": {
                               "listing_currency": "RUB", "applied_price": {"amount": "500", "currency": "RUB"}}}},
                           "publications": {"ozon:global": {"status": "success", "result": {"publication": {"item_id": "SKU-ID"}}}}}],
        }},
    }


def test_retired_fields_are_removed_without_altering_sku_sales_facts():
    raw = sales_product()
    product = normalize_product_model(raw)
    draft = product["drafts"]["ozon"]
    assert not {"stock", "sku", "upc", "package_dimensions", "publication"}.intersection(draft)
    assert draft["sku_items"][0]["stock"] == "9709839"
    assert draft["sku_items"][0]["sku"] == "SELL-FIRST"
    assert draft["sku_items"][0]["pricing"]["targets"]["ozon:global"]["applied_price"]["amount"] == "500"
    assert draft["sku_items"][0]["publications"]["ozon:global"]["result"]["publication"] == {"item_id": "SKU-ID"}
    before = deepcopy(product)
    view = single_sku_publish_draft(product, draft)
    assert view["stock"] == "9709839" and view["sku"] == "SELL-FIRST"
    assert view["upc"] == "123456789012" and view["price"] == "500"
    assert view["package_dimensions"]["length_cm"] == "26"
    assert view["publication"] == {"item_id": "SKU-ID"}
    assert product == before


def test_agent_price_summary_uses_selected_sku_quotes():
    draft = normalize_product_model(sales_product())["drafts"]["ozon"]
    draft["pricing"] = {"targets": {"ozon:global": {"applied_price": {"amount": "999", "currency": "CNY"}}}}
    draft["sku_items"].append({**deepcopy(draft["sku_items"][0]), "sku_id": "other", "selected": False})
    summary = _ai_draft_read_view(draft).pricing_summary
    assert summary == {"ozon:global": [{"sku_id": "first", "sku": "SELL-FIRST", "amount": "500", "currency": "RUB", "applied": True}]}


def upc_product():
    return get_context().products.save_product({
        "product_id": "upc-batch", "name": "多规格 UPC",
        "sku_items": [
            {"id": "first", "name": "黑色", "active": True, "barcode": ""},
            {"id": "second", "name": "白色", "active": True, "barcode": ""},
            {"id": "existing", "name": "已有条码", "active": True, "barcode": "KEEP"},
            {"id": "inactive", "name": "已停用", "active": False, "barcode": ""},
        ],
    })


def test_upcs_are_unique_per_sku_and_existing_barcodes_are_preserved():
    app = get_context()
    product = upc_product()
    app.db.import_upcs(["100000000001", "100000000002"])
    assignments, saved = app.products.assign_upcs_to_product(product)
    assert assignments == [{"sku_id": "first", "upc": "100000000001"}, {"sku_id": "second", "upc": "100000000002"}]
    assert [row["barcode"] for row in saved["sku_items"]] == ["100000000001", "100000000002", "KEEP", ""]
    assert app.db.upc_pool_stats() == {"total": 2, "free": 0, "used": 2}
    again, unchanged = app.products.assign_upcs_to_product(product)
    assert again == [] and unchanged == saved


def test_upc_shortage_rolls_back_the_whole_batch():
    app = get_context()
    product = upc_product()
    app.db.import_upcs(["100000000001"])
    with pytest.raises(ValueError, match="数量不足"):
        app.products.assign_upcs_to_product(product)
    assert app.products.load_product_from_index(product["product_id"]) == product
    assert app.db.upc_pool_stats() == {"total": 1, "free": 1, "used": 0}
    assignments, saved = app.products.assign_upcs_to_product(product, ("second",))
    assert assignments == [{"sku_id": "second", "upc": "100000000001"}]
    assert saved["sku_items"][0]["barcode"] == ""


@pytest.mark.parametrize("sku_ids", [("missing",), ("inactive",), ("first", "first")])
def test_invalid_upc_scope_never_consumes_pool_or_changes_product(sku_ids):
    app = get_context()
    product = upc_product()
    app.db.import_upcs(["100000000001", "100000000002"])
    with pytest.raises(ValueError, match="启用 SKU"):
        app.products.assign_upcs_to_product(product, sku_ids)
    assert app.products.load_product_from_index(product["product_id"]) == product
    assert app.db.upc_pool_stats() == {"total": 2, "free": 2, "used": 0}


def test_shared_category_resolution_handles_multiple_selected_skus():
    from erp_web.runtime_units.publish_adapter import OzonPublishingAdapter

    product = sales_product()
    draft = product["drafts"]["ozon"]
    draft["target_sites"][0].update({"category_id": "123", "description_category_id": "456"})
    product["sku_items"].append({**deepcopy(product["sku_items"][0]), "id": "second", "name": "白色"})
    draft["sku_items"].append({**deepcopy(draft["sku_items"][0]), "sku_id": "second", "sku": "SELL-SECOND"})
    resolved = OzonPublishingAdapter().resolve_category(product, {})
    assert resolved["drafts"]["ozon"]["category_id"] == "123"
    assert len(resolved["drafts"]["ozon"]["sku_items"]) == 2
