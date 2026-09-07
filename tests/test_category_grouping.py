"""刊登设置、属性填写与实际发布使用相同的分组值。"""

import pytest

from erp_web.product_model import default_product_model, validate_category_precheck
from erp_web.runtime_units import category_attribute_ai_fill
from erp_web.runtime_units.category_definition_support import project_attribute_page
from erp_web.runtime_units.publish_context import PreparedPublishContext
from erp_web.runtime_units.publish_ozon import _ozon_attributes, ozon_required_attributes_missing
from erp_web.runtime_units.sku_publish_projection import grouping_contract, sku_context
from erp_web.schemas.category_definition import CategoryAttributeDefinition, CategoryDefinition
from erp_web.services.category_attribute_fill_agent_service import CategoryAttributeFillAgentRun


GROUP = {"id": "8292", "name": "Объединить на одной карточке", "required": True, "value_mode": "free_text"}


def subject(mode="combined", *, required=True):
    product = default_product_model()
    product["product_id"] = "group-product"
    product["source"]["attributes"] = {"适用性别": "女"}
    product["sku_items"] = [{"id": f"fact-{i}", "name": f"颜色 {i}", "active": True} for i in range(2)]
    draft = product["drafts"]["ozon"]
    draft.update(title="共同标题", sku="PARENT", grouping={"mode": mode, "name": "Golovejoy XKZ42"})
    draft["sku_items"] = [{"sku_id": f"fact-{i}", "sku": f"SELL-{i}", "selected": True,
                           "attributes_by_target": {"ozon:global": {"8292": "旧 SKU 组名"}}} for i in range(2)]
    draft["target_sites"][0].update(category_id="970676618", attributes={"8292": "Два"}, validation_errors=["8292"])
    record = {"platform": "ozon", "site": "global", "category_id": "970676618", "attributes": {
        "required": [{**GROUP}] if required else [],
        "optional": [] if required else [{**GROUP, "required": False}],
    }}
    return product, record


@pytest.mark.parametrize("mode", ["combined", "separate"])
def test_grouping_only_fill_never_calls_ai_and_removes_obsolete_editable_value(monkeypatch, mode):
    product, record = subject(mode)
    monkeypatch.setattr(category_attribute_ai_fill, "run_category_attribute_fill_agent",
                        lambda *args: pytest.fail("系统分组字段不得启动 AI"))
    updated, meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(product, "ozon", record)
    assert "8292" not in updated["drafts"]["ozon"]["attributes"]
    assert "8292" not in updated["drafts"]["ozon"]["validation_errors"]
    assert meta["ai_filled"] == []
    assert validate_category_precheck(updated, "ozon", record) == []


@pytest.mark.parametrize("required", [False, True])
def test_grouping_is_excluded_from_ai_schema_and_unrequested_assignment_is_ignored(monkeypatch, required):
    product, record = subject(required=required)
    record["attributes"]["required"].append({"id": "GENDER", "required": True, "value_mode": "strict_enum"})

    def fake_agent(payload, toolset, ledger):
        assert [row["id"] for row in payload["attributes"]] == ["GENDER"]
        assert set(ledger.definitions) == {"GENDER"}
        ledger.add_values("GENDER", [{"id": "female", "value": "女"}])
        return CategoryAttributeFillAgentRun({"assignments": [
            {"attribute_id": "GENDER", "value": "女", "dictionary_value_id": "female"},
            {"attribute_id": "8292", "value": "Два"},
        ], "need_review": []})

    monkeypatch.setattr(category_attribute_ai_fill, "run_category_attribute_fill_agent", fake_agent)
    updated, meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(product, "ozon", record)
    assert set(updated["drafts"]["ozon"]["attributes"]) == {"GENDER"}
    assert meta["ai_filled"] == ["GENDER"]


@pytest.mark.parametrize("required", [False, True])
def test_public_attribute_page_marks_grouping_as_system_owned_without_changing_platform_required(required):
    attribute = CategoryAttributeDefinition(**{**GROUP, "required": required})
    definition = CategoryDefinition(platform="ozon", category_id="1", required=(attribute,))
    field = project_attribute_page(definition).attributes[0]
    assert field.managed_by == "listing_grouping"
    assert field.required is required
    assert not field.read_only  # ERP 系统填写不等于平台禁止提交。
    other = definition.model_copy(update={"platform": "mercadolibre"})
    assert project_attribute_page(other).attributes[0].managed_by == ""


@pytest.mark.parametrize("required", [False, True])
def test_changing_listing_mode_and_group_name_changes_real_wire_values(required):
    product, record = subject(required=required)
    draft = product["drafts"]["ozon"]
    definition = CategoryDefinition(platform="ozon", category_id="970676618", optional=(
        CategoryAttributeDefinition(**{**GROUP, "required": required}),
    ))
    context = PreparedPublishContext(product=product, draft=draft, target=draft["target_sites"][0],
                                     platform="ozon", category_definition=definition)

    def wire_values():
        values = []
        for fact, row in zip(product["sku_items"], draft["sku_items"]):
            projected = sku_context(context, fact, row, grouping_contract(context))
            regular, _ = _ozon_attributes(projected.product, projected.draft, category_record=record)
            fields = {str(attr["id"]): attr["values"][0]["value"] for attr in regular}
            values.append(fields.get("8292"))
            assert ozon_required_attributes_missing(projected.product, record) == []
        return values

    assert wire_values() == ["Golovejoy XKZ42"] * 2
    draft["grouping"]["name"] = "更新后的组名"
    assert wire_values() == ["更新后的组名"] * 2
    draft["grouping"]["mode"] = "separate"
    separate = wire_values()
    if required:
        assert all(separate) and separate[0] != separate[1]
        assert wire_values() == separate
    else:
        assert separate == [None, None]
    draft["grouping"]["mode"] = "combined"
    assert wire_values() == ["更新后的组名"] * 2


def test_missing_group_name_still_blocks_required_field_even_if_old_attribute_exists():
    product, record = subject()
    draft = product["drafts"]["ozon"]
    draft["grouping"]["name"] = ""
    draft["title"] = ""
    assert "attributes.8292" in validate_category_precheck(product, "ozon", record)
