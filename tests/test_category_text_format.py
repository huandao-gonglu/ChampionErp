"""平台文本格式在定义、AI 属性写入及公共编辑摘要之间保持一致。"""

import pytest

from erp_web.runtime_units.category_attribute_updates import AttributeUpdateValidation
from erp_web.runtime_units.category_definition_support import (
    definition_from_legacy_attributes,
    definition_to_legacy_attribute,
    public_attribute_summary,
)
from erp_web.runtime_units.category_providers import _yandex_parameter_definition
from erp_web.schemas.category import (
    category_attribute_text_format_error,
    category_attribute_value_is_valid,
)
from erp_web.services.capability_errors import BusinessCapabilityError


def other_attribute():
    return _yandex_parameter_definition({
        "parameter_id": "57046341", "name": "Прочие характеристики",
        "parameter_type": "TEXT", "required": False,
        "constraints": {"max_length": 3000},
    })


@pytest.mark.parametrize("value, message", [
    ("一段商品描述；部分款式有发声器", "第 1 行缺少英文冒号"),
    ("Материал: плюш\n用途说明", "第 2 行缺少英文冒号"),
    ("Материал：плюш", "第 1 行缺少英文冒号"),
    (" : плюш", "属性名和属性值均不能为空"),
    ({"value": "Материал: "}, "属性名和属性值均不能为空"),
    ({"values": [{"value": "Материал: плюш"}]}, "不使用枚举集合"),
    (["Материал: плюш"], "文本"),
])
def test_rejects_invalid_formatted_text(value, message):
    assert message in category_attribute_text_format_error(other_attribute(), value)
    assert not category_attribute_value_is_valid(other_attribute(), value)


@pytest.mark.parametrize("value", [
    "Материал: плюш\nНазначение: для игр",
    {"value": "Источник: https://example.com/a:b"},
    "\nМатериал: плюш\r\n\r\nНазначение: для игр\n",
])
def test_accepts_multiline_text_and_preserves_colons_inside_values(value):
    assert category_attribute_value_is_valid(other_attribute(), value)


def test_optional_empty_and_other_text_fields_do_not_gain_this_requirement():
    assert category_attribute_text_format_error(other_attribute(), None) == ""
    assert category_attribute_text_format_error(other_attribute(), "") == ""
    ordinary = _yandex_parameter_definition({"parameter_id": "7351754", "parameter_type": "TEXT"})
    assert category_attribute_value_is_valid(ordinary, "普通描述；允许标点")


def test_definition_roundtrip_exposes_format_to_ai_and_editor_without_internal_constraints():
    definition = definition_from_legacy_attributes(
        platform="yandex", site="global", category_id="76508560", required=[], optional=[other_attribute()],
    )
    attr = definition.optional[0]
    assert attr.constraints["max_length"] == "3000"
    projected = definition_to_legacy_attribute(attr)
    assert not category_attribute_value_is_valid(projected, "一段描述")
    summary = public_attribute_summary(attr, platform="yandex").model_dump()
    assert summary["text_format"] == "name_value_lines"
    assert "属性名:属性值" in summary["format_hint"]
    assert "当前商品适用的事实" in summary["format_hint"]
    assert "constraints" not in summary


def test_ai_attribute_write_rejects_format_before_persisting_and_allows_clear(monkeypatch):
    monkeypatch.setattr(
        "erp_web.runtime_units.category_attribute_updates.fetch_category_record",
        lambda *args, **kwargs: {"category_id": "76508560", "attributes": {"optional": [other_attribute()]}},
    )
    validator = AttributeUpdateValidation("yandex", "global", "76508560")
    with pytest.raises(BusinessCapabilityError, match="第 2 行缺少英文冒号") as exc:
        validator.validate({"57046341": "Материал: плюш\n一段描述"}, sku_scope=False)
    assert exc.value.code == "ATTRIBUTE_TEXT_FORMAT_INVALID"
    validator.validate({"57046341": "Назначение: для игр"}, sku_scope=False)
    validator.validate({"57046341": None}, sku_scope=False)
