import pytest
from pydantic_ai import ModelRetry

from erp_web.runtime_units.category_attribute_ai_fill import (
    _product_context, _validated_agent_attributes,
)
from erp_web.schemas.category import normalize_category_attribute_definition
from erp_web.schemas.category_attribute import CategoryAttributeValueLedger
from erp_web.services.category_attribute_fill_agent_service import (
    CategoryAttributeFillAgentOutput, CategoryAttributeFillOutputValidator,
)


SEASON = {"id": "SEASON", "value_mode": "strict_enum", "required": True}
CONTEXT = {"source": {"attributes": {"适合季节": "夏季,春季"}}}
REFERENCE = {"source_path": ["source", "attributes", "适合季节"],
             "source_value": "夏季,春季", "reason": "原文描述春夏季，映射平台相应季节"}


@pytest.mark.parametrize("reference", [
    {**REFERENCE, "source_value": "夏季"},
    {**REFERENCE, "source_value": "冬季"},
    {**REFERENCE, "source_path": ["source", "attributes", "不存在"]},
    {**REFERENCE, "source_path": ["draft", "title"]},
])
def test_invalid_or_partial_evidence_is_rejected_by_native_validator(reference):
    ledger = CategoryAttributeValueLedger.from_schema([SEASON])
    ledger.add_values("SEASON", [{"id": "summer", "value": "лето"}])
    output = CategoryAttributeFillAgentOutput.model_validate({"assignments": [{
        "attribute_id": "SEASON", "value": "лето", "dictionary_value_id": "summer",
        "evidence": reference,
    }], "need_review": []})
    validator = CategoryAttributeFillOutputValidator(ledger, product_context=CONTEXT)
    with pytest.raises(ModelRetry, match="完整原文"):
        validator(None, output)


def test_translation_reference_cannot_authorize_invented_numeric_specification():
    schema = [{"id": "VOLTAGE", "value_mode": "strict_enum"}]
    ledger = CategoryAttributeValueLedger.from_schema(schema)
    ledger.add_values("VOLTAGE", [{"id": "220", "value": "220 В"}])
    attrs, rejected = _validated_agent_attributes({"assignments": [{
        "attribute_id": "VOLTAGE", "value": "220 В", "dictionary_value_id": "220",
        "evidence": REFERENCE,
    }]}, schema, ledger, CONTEXT, platform="yandex", category_id="123", category_path="")
    assert attrs == {}
    assert rejected == {"VOLTAGE"}


def sku_product():
    return {
        "source": {"attributes": {"颜色": "黑色,灰色", "尺码": "均码"}},
        "sku_items": [
            {"id": "black", "options": {"颜色": "黑色", "尺码": "均码"}},
            {"id": "gray", "options": {"颜色": "灰色", "尺码": "均码"}},
        ],
        "drafts": {"yandex": {"sku_items": [
            {"sku_id": "black", "selected": True},
            {"sku_id": "gray", "selected": True},
        ]}},
    }


def test_mixed_sku_color_is_not_a_shared_attribute_or_evidence():
    product = sku_product()
    context = _product_context(product, "yandex")
    assert context["sku_scope"] == {
        "selected_count": 2, "common_options": {"尺码": "均码"},
        "varying_option_names": ["颜色"],
    }
    assert "颜色" not in context["source"]["attributes"]
    assert product["source"]["attributes"]["颜色"] == "黑色,灰色"
    color = normalize_category_attribute_definition({
        "id": "COLOR", "value_mode": "open_enum", "variation_role": "variant",
    })
    # 即使标题里出现同色，平台声明的变体属性也不能写成多 SKU 公共值。
    context["draft"]["title"] = "серый"
    attrs, rejected = _validated_agent_attributes({"assignments": [{
        "attribute_id": "COLOR", "value": "серый",
    }]}, [color], CategoryAttributeValueLedger.from_schema([color]), context,
        platform="yandex", category_id="123", category_path="")
    assert attrs == {}
    assert rejected == {"COLOR"}


def test_selected_sku_override_is_the_only_shared_option_evidence():
    product = sku_product()
    product["drafts"]["yandex"]["sku_items"][1]["selected"] = False
    product["drafts"]["yandex"]["sku_items"][0]["overrides"] = {"options": {"颜色": "红色"}}
    context = _product_context(product, "yandex")
    assert context["sku_scope"]["common_options"]["颜色"] == "红色"
    assert "颜色" not in context["source"]["attributes"]
    color = {"id": "COLOR", "value_mode": "open_enum", "variation_role": "variant"}
    attrs, rejected = _validated_agent_attributes({"assignments": [{
        "attribute_id": "COLOR", "value": "красный", "evidence": {
            "source_path": ["sku_scope", "common_options", "颜色"],
            "source_value": "红色", "reason": "所选 SKU 的覆盖颜色为红色",
        },
    }]}, [color], CategoryAttributeValueLedger.from_schema([color]), context,
        platform="yandex", category_id="123", category_path="")
    assert attrs == {"COLOR": "красный"}
    assert rejected == set()


def test_sku_count_is_not_packaging_quantity_evidence():
    schema = [{"id": "COUNT", "value_mode": "free_text", "value_type": "integer"}]
    attrs, rejected = _validated_agent_attributes({"assignments": [{
        "attribute_id": "COUNT", "value": "1",
    }]}, schema, CategoryAttributeValueLedger.from_schema(schema),
        {"sku_scope": {"selected_count": 1}}, platform="yandex", category_id="123", category_path="")
    assert attrs == {}
    assert rejected == {"COUNT"}


def test_optional_review_does_not_add_manual_work_or_model_retry():
    ledger = CategoryAttributeValueLedger.from_schema([
        {"id": "SIZE_TABLE", "required": False, "value_mode": "strict_enum"},
        {"id": "GENDER", "required": True, "value_mode": "strict_enum"},
    ])
    output = CategoryAttributeFillAgentOutput.model_validate({
        "assignments": [],
        "need_review": [
            {"id": "SIZE_TABLE", "reason": "无法确定品牌对应的尺码表"},
            {"id": "GENDER", "reason": "缺少适用性别事实"},
        ],
    })
    validated = CategoryAttributeFillOutputValidator(ledger)(None, output)
    assert [review.id for review in validated.need_review] == ["GENDER"]
