"""本地化与类目预检后，继续保存同一张草稿的回归。"""

from copy import deepcopy

from erp_web.context import get_context
from erp_web.facades import category_facade, copy_facade


def seed_drafts():
    app = get_context()
    app.products.save_product({
        "product_id": "save-actions-product", "name": "钻石画套装",
        "drafts": {"mercadolibre": {
            "draft_id": "draft-original", "enabled": True, "title": "原草稿",
            "target_sites": [{"platform": "mercadolibre", "site": "CBT", "category_id": "CBT455516"}],
        }},
    })
    original = app.db.load_draft_model("draft-original")
    duplicate = {**deepcopy(original), "draft_id": "draft-duplicate", "title": "另一张草稿"}
    app.db.upsert_draft_model(original["product_id"], "mercadolibre", duplicate)
    return app, original


def test_localized_copy_stays_on_selected_draft_and_can_be_saved(monkeypatch):
    app, original = seed_drafts()
    duplicate_before = app.db.load_draft_model("draft-duplicate")
    monkeypatch.setattr(copy_facade, "generate_ai_copy_bundle", lambda *a, **k: {
        "ok": True, "target_market": "mercadolibre", "source_platform": "mercadolibre",
        "language": "es", "mode": "rewrite", "copy": {"title": "Kit de mosaico", "description": "文案已本地化"},
    })
    result, status = copy_facade.generate_copy_payload({
        "draft_id": original["draft_id"], "product_id": original["product_id"], "platform": "mercadolibre",
    })
    assert status == 200
    assert result["draft"]["draft_id"] == original["draft_id"]
    assert result["draft"]["title"] == "Kit de mosaico"
    assert result["draft"]["updated_at"] != original["updated_at"]
    assert app.db.load_draft_model("draft-duplicate") == duplicate_before
    result["draft"]["description"] += "；继续手工修改"
    saved, error, status = app.products.save_draft_detail(result["draft"])
    assert status == 200 and error is None
    assert saved["draft"]["draft_id"] == original["draft_id"]
    assert "继续手工修改" in saved["draft"]["description"]


def test_category_precheck_returns_saved_version_for_next_save():
    app, original = seed_drafts()
    result, status = category_facade.category_precheck_payload({
        "draft_id": original["draft_id"], "platform": "mercadolibre", "site": "CBT",
        "category_id": "CBT455516", "category_record": {"category_id": "CBT455516", "attributes": {}},
    })
    assert status == 200
    assert result["draft"]["draft_id"] == original["draft_id"]
    assert result["draft"]["updated_at"] != original["updated_at"]
    saved, error, status = app.products.save_draft_detail({**result["draft"], "title": "检查后继续修改"})
    assert status == 200 and error is None
    assert saved["draft"]["title"] == "检查后继续修改"
    # 真正的旧版本仍须拒绝，避免覆盖其他操作的新结果。
    _, error, status = app.products.save_draft_detail(original)
    assert status == 409 and error["error_code"] == "DRAFT_CHANGED"
