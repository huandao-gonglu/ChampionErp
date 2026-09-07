"""逐 SKU 属性填写必须复用真实枚举/证据校验，并保持商品、其他 SKU 与市场隔离。"""

from copy import deepcopy

import pytest

from erp_web.product_model import normalize_product_model
from erp_web.runtime_units import category_attribute_ai_fill as ai_fill
from erp_web.runtime_units.sku_attribute_fill import fill_sku_attributes
from erp_web.services.category_attribute_fill_agent_service import CategoryAttributeFillAgentRun
from tests.test_sku_workflow import product_fixture


def subject():
    product = product_fixture()
    draft = product["drafts"]["ozon"]
    draft["site"] = "global"
    draft["category_id"] = "face-mask"
    draft["attributes"] = {"brand": "品牌"}
    draft["target_sites"][0].update(category_id="face-mask", attributes={"brand": "品牌"})
    for fact, row, color in zip(product["sku_items"], draft["sku_items"], ("暗夜黑", "樱花粉")):
        fact["options"] = {"颜色": color, "尺码": "均码"}
        row["attributes_by_target"] = {"yandex:global": {"other-color": color}}
    return normalize_product_model(product)


def definition():
    return {"platform": "ozon", "site": "global", "category_id": "face-mask", "attributes": {
        "required": [{"id": "brand", "name": "品牌", "value_mode": "free_text"}],
        "optional": [{"id": "color", "name": "Цвет", "value_mode": "open_enum", "variation_role": "variant"},
                     {"id": "size", "name": "Размер", "value_mode": "open_enum", "variation_role": "variant"}],
    }}


def test_fill_uses_only_requested_sku_evidence_and_preserves_other_scopes(monkeypatch):
    product = subject()
    product["drafts"]["yandex"] = deepcopy(product["drafts"]["ozon"])
    original = deepcopy(product)

    def agent(payload, toolset, ledger):
        scope = payload["product_context"]["sku_scope"]
        assert scope == {"selected_count": 1, "common_options": {"尺码": "均码", "颜色": "暗夜黑"}, "varying_option_names": []}
        assert {item["id"] for item in payload["attributes"]} == {"color", "size"}
        return CategoryAttributeFillAgentRun({"assignments": [
            {"attribute_id": "color", "value": "Черный", "evidence": {
                "source_path": ["sku_scope", "common_options", "颜色"], "source_value": "暗夜黑", "reason": "来源声明黑色，俄语等义颜色。"}},
            {"attribute_id": "size", "value": "Розовый", "evidence": {
                "source_path": ["sku_scope", "common_options", "颜色"], "source_value": "樱花粉", "reason": "借用另一 SKU 的值。"}},
            {"attribute_id": "brand", "value": "被覆盖"},
        ], "need_review": []})

    monkeypatch.setattr(ai_fill, "run_category_attribute_fill_agent", agent)
    updated, meta = fill_sku_attributes(product, "ozon", definition(), "fact-0", filler=ai_fill.apply_ai_model_attribute_fill)
    assert product == original
    before, after = original["drafts"]["ozon"], updated["drafts"]["ozon"]
    assert after["attributes"] == before["attributes"]
    assert after["sku_items"][1] == before["sku_items"][1]
    assert after["sku_items"][0]["attributes_by_target"] == {
        "yandex:global": {"other-color": "暗夜黑"}, "ozon:global": {"color": "Черный"},
    }
    assert meta["need_review"] == []  # 未填写的可选字段不作为阻挡项


def test_manual_values_and_inherited_common_values_are_not_overwritten():
    product = subject()
    draft = product["drafts"]["ozon"]
    draft["sku_items"][0]["attributes_by_target"]["ozon:global"] = {"color": "人工颜色"}
    draft["attributes"]["size"] = "均码"

    def unexpected(*args):
        pytest.fail("已有值不应启动 AI 重新填写")

    updated, meta = fill_sku_attributes(product, "ozon", definition(), "fact-0", filler=unexpected)
    assert updated == product
    assert meta["ai_filled"] == []


def test_invalid_identity_and_category_are_rejected_before_model():
    for sku_id, record in [("unknown", definition()), ("fact-0", {**definition(), "category_id": "old-category"})]:
        with pytest.raises(ValueError):
            fill_sku_attributes(subject(), "ozon", record, sku_id, filler=lambda *args: pytest.fail("不应调用 AI"))


def test_variant_rules_do_not_take_first_color_from_product_summary(monkeypatch):
    product = subject()
    product["colors"] = ["错误的汇总颜色", "暗夜黑"]
    record = definition()
    color = record["attributes"]["optional"].pop(0)
    color["required"] = True
    record["attributes"]["required"].append(color)

    def agent(payload, toolset, ledger):
        assert "color" in {item["id"] for item in payload["attributes"]}
        return CategoryAttributeFillAgentRun({"assignments": [], "need_review": [{"id": "color", "reason": "无法确认"}]})

    monkeypatch.setattr(ai_fill, "run_category_attribute_fill_agent", agent)
    updated, meta = fill_sku_attributes(product, "ozon", record, "fact-0", filler=ai_fill.apply_ai_model_attribute_fill)
    assert updated["drafts"]["ozon"]["sku_items"][0]["attributes_by_target"]["ozon:global"] == {}
    assert "color" in meta["need_review"]


def test_http_sku_fill_persists_only_requested_sku_using_server_definition(tmp_path, monkeypatch):
    from erp_web.facades import category_facade
    from tests.runtime_test_utils import temp_app_context

    with temp_app_context(tmp_path) as app:
        app.products.save_product(subject())
        original = app.db.load_draft_model("sku-draft")
        monkeypatch.setattr(category_facade, "fetch_category_record", lambda *args, **kwargs: definition())

        def filler(product, platform, record):
            assert {item["id"] for item in record["attributes"]["optional"]} == {"color", "size"}
            product["drafts"][platform]["attributes"]["color"] = "Черный"
            return product, {"source": "ai_model"}

        monkeypatch.setattr(category_facade, "apply_ai_model_attribute_fill", filler)
        response, status = category_facade.category_ai_fill_payload({
            "draft_id": "sku-draft", "platform": "ozon", "site": "global", "sku_id": "fact-0",
            "category_id": "face-mask", "category_record": {"category_id": "forged", "attributes": {}},
        })
        assert status == 200
        saved = app.db.load_draft_model("sku-draft")
        assert saved["sku_items"][0]["attributes_by_target"]["ozon:global"] == {"color": "Черный"}
        assert saved["sku_items"][1] == original["sku_items"][1]
        assert saved["attributes"] == original["attributes"]
        assert response["need_review"] == []


def test_sku_evidence_prompt_offers_copyable_full_leaf_path():
    import json
    from erp_web.services.category_attribute_fill_agent_service import _prompt_payload
    from erp_web.schemas.category_attribute_evidence import evidence_reference_is_valid
    context = {"sku_scope": {"common_options": {"颜色": "【加长护颈款】暗夜黑-礼盒装"}}}
    payload = json.loads(_prompt_payload({"platform": "ozon", "product_context": context, "attributes": []}))
    reference = payload["attribute_evidence_sources"][0]
    assert reference == {"source_path": ["sku_scope", "common_options", "颜色"], "source_value": "【加长护颈款】暗夜黑-礼盒装"}
    assert evidence_reference_is_valid(reference, context)
    assert not evidence_reference_is_valid({**reference, "source_path": ["sku_scope", "common_options"]}, context)
