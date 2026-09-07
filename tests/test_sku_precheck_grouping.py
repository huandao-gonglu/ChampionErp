"""同类预检问题汇总后仍能定位每个 SKU，且不改变发布阻断。"""

from copy import deepcopy
from dataclasses import replace

import pytest

from erp_web.runtime_units import sku_publish_adapter
from erp_web.runtime_units.publish_context import PreparedPublishContext
from erp_web.runtime_units.publish_helpers import compact_precheck
from erp_web.schemas.category_definition import CategoryAttributeDefinition, CategoryDefinition
from tests.test_sku_workflow import ItemBoundary, context_for, product_fixture


def issue(field="attributes.10096", message="缺少必填属性：商品颜色", severity="error", next_action="填写 SKU 属性"):
    return {"code": "REQUIRED_ATTRIBUTE_MISSING", "field": field, "message": message, "severity": severity, "next_action": next_action}


@pytest.mark.parametrize("platform", ["ozon", "yandex", "mercadolibre"])
def test_repeated_precheck_issues_group_with_all_selected_skus(platform, monkeypatch):
    product = product_fixture()
    draft = product["drafts"].pop("ozon")
    product["drafts"][platform] = draft
    draft["target_sites"][0]["platform"] = platform
    fact_template, row_template = deepcopy(product["sku_items"][0]), deepcopy(draft["sku_items"][0])
    quote = row_template["pricing"]["targets"].pop("ozon:global")
    row_template["pricing"]["targets"][f"{platform}:global"] = quote
    product["sku_items"], draft["sku_items"] = [], []
    for index in range(30):
        product["sku_items"].append({**deepcopy(fact_template), "id": f"fact-{index}", "name": "同名规格"})
        draft["sku_items"].append({**deepcopy(row_template), "sku_id": f"fact-{index}", "sku": f"SELL-{index}", "selected": index < 29})
    draft["grouping"]["mode"] = "combined"
    definition = CategoryDefinition(platform=platform, category_id="test", required=(
        CategoryAttributeDefinition(id="10096", name="颜色", required=True, variation_role="variant"),
    ), optional=(
        CategoryAttributeDefinition(id="200" if platform == "yandex" else "group", name="Объединить на одной карточке"),
        CategoryAttributeDefinition(id="size", name="尺寸", variation_role="variant"),
    ))
    context = PreparedPublishContext(product=product, draft=draft, target=draft["target_sites"][0], platform=platform, category_definition=definition)
    leaf = ItemBoundary()
    leaf.platform = platform
    check = {"ok": False, "errors": [issue(), issue()], "warnings": [issue(message="属性待复核", severity="warning")]}
    original = deepcopy(check)
    monkeypatch.setattr(leaf, "validate_draft", lambda *_: check)

    result = sku_publish_adapter.SkuGroupPublishingAdapter(leaf).validate_draft(context, {"mercadolibre": {"listing_model": "user_products"}})

    assert result["ok"] is False
    assert len(result["errors"]) == 1
    assert len(result["warnings"]) == 1
    assert len(result["sku_results"]) == 29
    expected = [{"sku_id": f"fact-{index}", "sku": f"SELL-{index}", "name": "同名规格"} for index in range(29)]
    assert result["errors"][0]["affected_skus"] == expected
    assert result["errors"][0]["field"] == "attributes.10096"
    assert "颜色" in result["errors"][0]["message"]
    related = result["errors"][0]["related_issues"]
    assert len(related) == 1
    assert related[0]["code"] == "SKU_VARIATION_ATTRIBUTES_EMPTY"
    assert "无需填满所有可选字段" in related[0]["message"]
    assert result["warnings"][0]["affected_skus"] == expected
    assert check == original


def combination_context(required_id="color"):
    product = product_fixture()
    context = context_for(product)
    context.draft["grouping"]["mode"] = "combined"
    attributes = [
        CategoryAttributeDefinition(id="color", name="颜色", required=required_id == "color", variation_role="variant"),
        CategoryAttributeDefinition(id="brand", name="品牌", required=required_id == "brand"),
        CategoryAttributeDefinition(id="size", name="尺寸", variation_role="variant"),
        CategoryAttributeDefinition(id="group", name="Объединить на одной карточке"),
    ]
    return replace(context, category_definition=CategoryDefinition(
        platform="ozon", category_id="test",
        required=tuple(attr for attr in attributes if attr.required),
        optional=tuple(attr for attr in attributes if not attr.required),
    ))


@pytest.mark.parametrize("case", ["共同属性缺失", "缺失范围不同", "没有必填项缺失"])
def test_unrelated_missing_attributes_do_not_hide_combination_error(case, monkeypatch):
    context = combination_context("brand" if case == "共同属性缺失" else "color")
    leaf = ItemBoundary()

    def validate(projected, _config):
        field = "attributes.brand" if case == "共同属性缺失" else "attributes.color"
        missing = case != "没有必填项缺失" and (case != "缺失范围不同" or projected.draft["sku"] == "SELL-0")
        return {"ok": not missing, "errors": [issue(field=field, next_action="前往类目属性页补齐必填属性")] if missing else [], "warnings": []}

    monkeypatch.setattr(leaf, "validate_draft", validate)
    result = sku_publish_adapter.SkuGroupPublishingAdapter(leaf).validate_draft(context, {})

    assert result["ok"] is False
    assert len(result["errors"]) == (1 if case == "没有必填项缺失" else 2)
    assert result["errors"][-1]["code"] == "SKU_VARIATION_ATTRIBUTES_EMPTY"
    assert not any(item.get("related_issues") for item in result["errors"])
    if case == "共同属性缺失":
        assert "类目属性页" in result["errors"][0]["next_action"]


def test_filled_variants_still_require_distinct_combinations(monkeypatch):
    context = combination_context()
    leaf = ItemBoundary()

    def validate(projected, _config):
        missing = not projected.draft["attributes"].get("color")
        return {"ok": not missing, "errors": [issue(field="attributes.color", next_action="前往类目属性页补齐必填属性")] if missing else [], "warnings": []}

    monkeypatch.setattr(leaf, "validate_draft", validate)
    adapter = sku_publish_adapter.SkuGroupPublishingAdapter(leaf)
    missing = adapter.validate_draft(context, {})
    assert len(missing["errors"]) == 1
    assert "SKU → 属性 / 详情" in missing["errors"][0]["next_action"]
    assert "类目属性页" not in missing["errors"][0]["next_action"]
    with pytest.raises(ValueError, match="平台差异属性全部为空"):
        adapter.build_payload(context, {})

    for row in context.draft["sku_items"]:
        row["attributes_by_target"] = {"ozon:global": {"color": "红"}}
    duplicate = adapter.validate_draft(context, {})
    assert duplicate["ok"] is False
    assert duplicate["errors"][0]["code"] == "SKU_VARIATION_COMBINATION_DUPLICATE"
    assert not duplicate["errors"][0].get("related_issues")
    with pytest.raises(ValueError, match="平台属性组合相同"):
        adapter.build_payload(context, {})

    context.draft["sku_items"][1]["attributes_by_target"]["ozon:global"]["color"] = "蓝"
    passed = adapter.validate_draft(context, {})
    assert passed["ok"] is True
    assert passed["errors"] == []
    assert len(adapter.build_payload(context, {})["items"]) == 2


def test_duplicate_combinations_remain_independent_of_missing_required_variant(monkeypatch):
    context = combination_context()
    for row in context.draft["sku_items"]:
        row["attributes_by_target"] = {"ozon:global": {"size": "均码"}}
    leaf = ItemBoundary()
    monkeypatch.setattr(leaf, "validate_draft", lambda *_: {"ok": False, "errors": [issue(field="attributes.color")], "warnings": []})

    result = sku_publish_adapter.SkuGroupPublishingAdapter(leaf).validate_draft(context, {})

    assert [item["code"] for item in result["errors"]] == ["REQUIRED_ATTRIBUTE_MISSING", "SKU_VARIATION_COMBINATION_DUPLICATE"]
    assert not any(item.get("related_issues") for item in result["errors"])


def test_different_fields_reasons_and_actions_remain_separate(monkeypatch):
    product = product_fixture()
    leaf = ItemBoundary()
    variants = [issue(), issue(field="attributes.4295"), issue(message="颜色值不受支持"), issue(next_action="刷新类目后重新选择")]

    def validate(context, _config):
        return {"ok": False, "errors": variants if context.draft["sku"] == "SELL-0" else [issue()], "warnings": []}

    monkeypatch.setattr(leaf, "validate_draft", validate)
    result = sku_publish_adapter.SkuGroupPublishingAdapter(leaf).validate_draft(context_for(product), {})

    assert len(result["errors"]) == 4
    assert [len(item["affected_skus"]) for item in result["errors"]] == [2, 1, 1, 1]


def test_compacted_failure_keeps_affected_sku_details():
    first = {"sku_id": "first", "sku": "SELL-1", "name": "红色"}
    second = {"sku_id": "second", "sku": "SELL-2", "name": "蓝色"}
    related = {**issue(field="sku_items", message="组合属性为空"), "code": "SKU_VARIATION_ATTRIBUTES_EMPTY"}
    raw = {"ok": False, "errors": [{**issue(), "affected_skus": [first], "related_issues": [related]}, {**issue(), "affected_skus": [first, second], "related_issues": [related]}], "warnings": []}
    original = deepcopy(raw)

    result = compact_precheck(raw)

    assert result["errors"][0]["affected_skus"] == [first, second]
    assert result["errors"][0]["related_issues"] == [related]
    assert raw == original
