"""大草稿错误在生成、保存、旧数据读取和类型化回执中保持同一份合并格式。"""

from copy import deepcopy
import json

from erp_web.product_model.precheck_model import normalize_precheck
from erp_web.runtime_units.sku_publish_adapter import SkuGroupPublishingAdapter
from erp_web.schemas.publish_capabilities import PublishValidationIssue
from tests.test_draft_save_after_actions import seed_drafts
from tests.test_sku_workflow import ItemBoundary, context_for, product_fixture


def image_error(asset_id):
    return {"code": "IMAGE_NOT_PREPARED", "field": f"images.{asset_id}",
            "message": "图片尚未准备", "severity": "error", "next_action": "准备图片"}


def assert_compact(value):
    encoded = json.dumps(value, ensure_ascii=False)
    assert '"sku_results"' not in encoded
    assert '"affected_skus"' not in encoded
    assert "不应复制的规格名称" not in encoded
    assert len(encoded.encode()) < 128 * 1024


def test_large_group_emits_one_image_error_without_per_sku_copies(monkeypatch):
    product = product_fixture()
    draft = product["drafts"]["ozon"]
    fact, row = deepcopy(product["sku_items"][0]), deepcopy(draft["sku_items"][0])
    product["sku_items"] = [{**deepcopy(fact), "id": f"fact-{i}", "name": "不应复制的规格名称"} for i in range(74)]
    draft["sku_items"] = [{**deepcopy(row), "sku_id": f"fact-{i}", "sku": f"SELL-{i}"} for i in range(74)]
    leaf = ItemBoundary()
    monkeypatch.setattr(leaf, "validate_draft", lambda *_: {
        "ok": False, "errors": [image_error(f"asset-{i}") for i in range(115)], "warnings": [],
    })

    result = SkuGroupPublishingAdapter(leaf).validate_draft(context_for(product), {})

    assert result["ok"] is False
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert error["field"] == "images"
    assert len(error["affected_sku_ids"]) == 74
    assert len(error["affected_asset_ids"]) == 115
    assert PublishValidationIssue.model_validate(error).model_dump()["affected_asset_ids"] == error["affected_asset_ids"]
    assert_compact(result)


def legacy_precheck():
    return {"ok": False, "errors": [], "warnings": [], "sku_results": [
        {"sku_id": f"fact-{i}", "sku": f"SELL-{i}", "precheck": {
            "ok": False, "errors": [image_error(f"asset-{j}") for j in range(115)], "warnings": [],
        }} for i in range(74)
    ]}


def test_old_per_sku_only_errors_are_merged_without_losing_failures():
    raw = legacy_precheck()
    original = deepcopy(raw)
    normalized = normalize_precheck(raw)
    assert raw == original
    assert normalized["ok"] is False
    assert len(normalized["errors"]) == 1
    assert len(normalized["errors"][0]["affected_sku_ids"]) == 74
    assert len(normalized["errors"][0]["affected_asset_ids"]) == 115
    assert normalize_precheck(normalized) == normalized
    assert_compact(normalized)


def test_store_reads_old_records_and_only_persists_merged_errors():
    app, draft = seed_drafts()
    raw = legacy_precheck()
    old_issue = {**image_error("asset-0"), "affected_skus": [
        {"sku_id": "fact-0", "sku": "SELL-0", "name": "不应复制的规格名称"},
    ]}
    draft.update(last_precheck=raw, validation_errors=[old_issue, old_issue])
    draft["target_sites"][0].update(last_precheck=raw, validation_errors=[old_issue, old_issue])
    # 直接写入历史格式，验证正式数据库读取边界，而不是只测纯函数。
    with app.db._connect() as conn:
        conn.execute("UPDATE platform_drafts SET draft_json=? WHERE draft_id=?", (json.dumps(draft), draft["draft_id"]))
        conn.commit()
    loaded = app.db.load_draft_model(draft["draft_id"])
    assert_compact(loaded)
    assert loaded["last_precheck"]["ok"] is False
    assert len(loaded["last_precheck"]["errors"]) == 1
    saved, error, status = app.products.save_draft_publish_state(
        draft["draft_id"], "mercadolibre", "CBT",
        {"last_precheck": raw, "validation_errors": [old_issue, old_issue], "publish_status": "not_ready"},
    )
    assert error is None and status == 200
    assert_compact(saved["draft"])
    with app.db._connect() as conn:
        stored = json.loads(conn.execute("SELECT draft_json FROM platform_drafts WHERE draft_id=?", (draft["draft_id"],)).fetchone()[0])
    assert_compact(stored)
    assert stored["last_precheck"]["ok"] is False
    assert stored["target_sites"][0]["last_precheck"]["errors"] == stored["last_precheck"]["errors"]


def test_different_image_failures_and_markets_remain_separate():
    missing = {**image_error("missing"), "code": "IMAGE_SOURCE_MISSING", "message": "图片源文件不存在"}
    raw = {"ok": False, "errors": [image_error("one"), image_error("two"), missing],
           "markets": [{"site_id": site, "ok": False, "errors": [image_error("one")]} for site in ("MLM", "MLB")]}
    result = normalize_precheck(raw)
    assert len(result["errors"]) == 2
    assert result["errors"][0]["affected_asset_ids"] == ["one", "two"]
    assert result["errors"][1]["affected_asset_ids"] == ["missing"]
    assert [market["site_id"] for market in result["markets"]] == ["MLM", "MLB"]
    assert all(len(market["errors"]) == 1 for market in result["markets"])
