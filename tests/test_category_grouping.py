"""刊登设置、属性填写与实际发布使用相同的分组值。"""

import pytest

from erp_web.product_model import default_product_model, validate_category_precheck
from erp_web.runtime_units.category_definition_support import project_attribute_page
from erp_web.runtime_units.publish_context import PreparedPublishContext
from erp_web.runtime_units.publish_ozon import _ozon_attributes, ozon_required_attributes_missing
from erp_web.runtime_units.sku_publish_projection import grouping_contract, sku_context
from erp_web.schemas.category_definition import CategoryAttributeDefinition, CategoryDefinition
from erp_web.schemas.category_grouping import is_listing_grouping_attribute


GROUP = {"id": "8292", "name": "Объединить на одной карточке", "required": True, "value_mode": "free_text"}
MODEL_GROUP = {**GROUP, "id": "9048", "name": "Название модели (для объединения в одну карточку)"}


def subject(mode="combined", *, required=True, group=GROUP):
    product = default_product_model()
    product["product_id"] = "group-product"
    product["source"]["attributes"] = {"适用性别": "女"}
    product["sku_items"] = [{"id": f"fact-{i}", "name": f"颜色 {i}", "active": True} for i in range(2)]
    draft = product["drafts"]["ozon"]
    draft.update(title="共同标题", sku="PARENT", grouping={"mode": mode, "name": "Golovejoy XKZ42"})
    draft["sku_items"] = [{"sku_id": f"fact-{i}", "sku": f"SELL-{i}", "selected": True,
                           "attributes_by_target": {"ozon:global": {group["id"]: "旧 SKU 组名"}}} for i in range(2)]
    draft["target_sites"][0].update(category_id="970676618", attributes={group["id"]: "Два"}, validation_errors=[group["id"]])
    record = {"platform": "ozon", "site": "global", "category_id": "970676618", "attributes": {
        "required": [{**group}] if required else [],
        "optional": [] if required else [{**group, "required": False}],
    }}
    return product, record






@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("group", [GROUP, MODEL_GROUP])
def test_public_attribute_page_marks_grouping_as_system_owned_without_changing_platform_required(required, group):
    attribute = CategoryAttributeDefinition(**{**group, "required": required})
    definition = CategoryDefinition(platform="ozon", category_id="1", required=(attribute,))
    field = project_attribute_page(definition).attributes[0]
    assert field.managed_by == "listing_grouping"
    assert field.required is required
    assert not field.read_only  # ERP 系统填写不等于平台禁止提交。
    other = definition.model_copy(update={"platform": "mercadolibre"})
    assert project_attribute_page(other).attributes[0].managed_by == ""


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("group", [GROUP, MODEL_GROUP])
def test_changing_listing_mode_and_group_name_changes_real_wire_values(required, group):
    product, record = subject(required=required, group=group)
    draft = product["drafts"]["ozon"]
    definition = CategoryDefinition(platform="ozon", category_id="970676618", optional=(
        CategoryAttributeDefinition(**{**group, "required": required}),
    ))
    context = PreparedPublishContext(product=product, draft=draft, target=draft["target_sites"][0],
                                     platform="ozon", category_definition=definition)

    def wire_values():
        values = []
        for fact, row in zip(product["sku_items"], draft["sku_items"]):
            projected = sku_context(context, fact, row, grouping_contract(context))
            regular, _ = _ozon_attributes(projected.product, projected.draft, category_record=record)
            fields = {str(attr["id"]): attr["values"][0]["value"] for attr in regular}
            values.append(fields.get(group["id"]))
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


@pytest.mark.parametrize("attribute", [
    {"id": "22390", "name": "Объединить в похожие товары"},
    {"id": "9048", "name": "Название модели"},
])
def test_unrelated_attributes_are_not_managed_as_listing_groups(attribute):
    assert not is_listing_grouping_attribute("ozon", attribute)


def test_missing_group_name_still_blocks_required_field_even_if_old_attribute_exists():
    product, record = subject()
    draft = product["drafts"]["ozon"]
    draft["grouping"]["name"] = ""
    draft["title"] = ""
    assert "attributes.8292" in validate_category_precheck(product, "ozon", record)
