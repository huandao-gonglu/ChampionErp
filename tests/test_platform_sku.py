"""SKU 编码是草稿规格事实，不再生成草稿根级编码。"""
from erp_web.product_model import normalize_platform_draft, draft_has_remote_listing


def test_normalization_removes_retired_draft_sales_fields():
    draft = normalize_platform_draft({"draft_id": "draft-1", "sku": "old", "stock": "100", "upc": "old",
                                      "package_dimensions": {"weight_kg": "9"}, "publication": {"item_id": "old"}}, "ozon")
    assert not {"sku", "stock", "upc", "package_dimensions", "publication"}.intersection(draft)


def test_sku_publications_mark_remote_listing():
    assert draft_has_remote_listing({"sku_items": [{"publications": {"ozon:global": {"status": "pending_confirmation"}}}]})
    assert not draft_has_remote_listing({"sku_items": [{"publications": {}}]})
