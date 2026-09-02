from __future__ import annotations

import pytest

from erp_web.product_model import (
    apply_category_selection,
    default_product_model,
)
from erp_web.product_model.attribute_matching import (
    infer_source_attribute_matches,
    source_package_dimensions,
)
from erp_web.product_model.common import parse_dimensions_text


@pytest.mark.parametrize(
    ("attributes", "expected_value", "expected_key"),
    [
        ({"商品类目": "手持风扇"}, "手持风扇", "商品类目"),
        ({"品类名称": "桌面收纳盒"}, "桌面收纳盒", "品类名称"),
        ({"风扇 分类": "八爪鱼风扇"}, "八爪鱼风扇", "风扇 分类"),
        ({"产品类型": "露营灯"}, "露营灯", "产品类型"),
        ({"商品类型": "硅胶冰格"}, "硅胶冰格", "商品类型"),
    ],
    ids=["类目", "品类", "分类", "产品类型", "商品类型"],
)
def test_matches_common_category_descriptions(attributes: dict[str, str], expected_value: str, expected_key: str) -> None:
    match = infer_source_attribute_matches(attributes)["category"]

    assert match["value"] == expected_value
    assert match["source_key"] == expected_key


@pytest.mark.parametrize(
    ("attributes", "expected_dimensions", "expected_scope", "expected_unit", "unit_inferred"),
    [
        ({"商品 外观-尺寸（mm）": "220*92*85"}, {"length_cm": "22", "width_cm": "9.2", "height_cm": "8.5"}, "product", "mm", False),
        ({"机身规格": "90×60×30毫米"}, {"length_cm": "9", "width_cm": "6", "height_cm": "3"}, "product", "mm", False),
        ({"产品长宽高": "18 x 12 x 6 cm"}, {"length_cm": "18", "width_cm": "12", "height_cm": "6"}, "product", "cm", False),
        ({"外箱尺寸": "0.4×0.3×0.2 m"}, {"length_cm": "40", "width_cm": "30", "height_cm": "20"}, "package", "m", False),
        ({"包装尺寸": "30*20*10cm"}, {"length_cm": "30", "width_cm": "20", "height_cm": "10"}, "package", "cm", False),
        ({"本体尺寸": "220*92*85"}, {"length_cm": "22", "width_cm": "9.2", "height_cm": "8.5"}, "product", "mm", True),
    ],
    ids=["外观毫米", "机身毫米", "长宽高厘米", "外箱米", "包装厘米", "本体默认毫米"],
)
def test_matches_common_dimension_descriptions(
    attributes: dict[str, str],
    expected_dimensions: dict[str, str],
    expected_scope: str,
    expected_unit: str,
    unit_inferred: bool,
) -> None:
    match = infer_source_attribute_matches(attributes)["dimensions"]

    assert match["normalized"] == expected_dimensions
    assert match["scope"] == expected_scope
    assert match["unit"] == expected_unit
    assert match["unit_inferred"] is unit_inferred


@pytest.mark.parametrize(
    "attributes",
    [
        {"电源线长度": "220*92*85"},
        {"产品体积": "220*92*85"},
        {"电池容量": "220*92*85"},
        {"产品尺寸": "20*10"},
        {"产品尺寸": "0*20*10cm"},
    ],
    ids=["电源线", "体积", "容量", "数字不足", "零值"],
)
def test_rejects_non_dimension_or_invalid_dimension_values(attributes: dict[str, str]) -> None:
    assert "dimensions" not in infer_source_attribute_matches(attributes)


def test_prefers_specific_dimension_candidate_over_generic_specification() -> None:
    match = infer_source_attribute_matches(
        {
            "商品规格": "40*30*20cm",
            "商品外观尺寸": "220*92*85mm",
        }
    )["dimensions"]

    assert match["source_key"] == "商品外观尺寸"
    assert match["normalized"] == {"length_cm": "22", "width_cm": "9.2", "height_cm": "8.5"}


def test_keeps_product_and_package_dimensions_as_separate_matches() -> None:
    matches = infer_source_attribute_matches(
        {
            "外观尺寸": "60*55*155",
            "包装体积": "55*60*160mm",
        }
    )

    assert matches["dimensions"]["scope"] == "product"
    assert matches["dimensions"]["normalized"] == {
        "length_cm": "6",
        "width_cm": "5.5",
        "height_cm": "15.5",
    }
    assert matches["package_dimensions"]["scope"] == "package"
    assert matches["package_dimensions"]["normalized"] == {
        "length_cm": "5.5",
        "width_cm": "6",
        "height_cm": "16",
    }


def test_source_package_dimensions_does_not_fall_back_to_product_dimensions() -> None:
    matches = infer_source_attribute_matches({"外观尺寸": "60*55*155"})
    source = {
        "dimensions": {"length_cm": "6", "width_cm": "5.5", "height_cm": "15.5"},
        "attribute_matches": matches,
    }

    assert source_package_dimensions(source) == {
        "length_cm": "",
        "width_cm": "",
        "height_cm": "",
    }


@pytest.mark.parametrize(
    ("value", "default_unit", "expected"),
    [
        ("60*55*155", "mm", {"length_cm": "6", "width_cm": "5.5", "height_cm": "15.5"}),
        ("6×5.5×15.5 cm", "mm", {"length_cm": "6", "width_cm": "5.5", "height_cm": "15.5"}),
        ("60mm × 55mm × 155mm", "cm", {"length_cm": "6", "width_cm": "5.5", "height_cm": "15.5"}),
    ],
)
def test_parse_dimensions_text_normalizes_source_unit(
    value: str,
    default_unit: str,
    expected: dict[str, str],
) -> None:
    assert parse_dimensions_text(value, default_unit=default_unit) == expected


def test_rejects_generic_category_and_category_code() -> None:
    matches = infer_source_attribute_matches({"商品分类": "其他", "分类编码": "FAN-001"})

    assert "category" not in matches


def test_category_selection_keeps_only_current_platform_schema_values() -> None:
    product = {
        "source": {
            "attributes": {
                "产地": "广东",
                "包装体积": "55*60*160mm",
            },
            "weight_kg": "0.182",
        },
        "drafts": {
            "mercadolibre": {
                "site": "CBT",
                "target_sites": [
                    {
                        "platform": "mercadolibre",
                        "site": "CBT",
                        "attributes": {
                            "VOLTAGE": "110/220V",
                            "WEIGHT": {"value": "182", "unit": "g"},
                            "PACKAGE_LENGTH": "5.5",
                            "VERTICAL_TAGS": "平台推导值",
                            "产地": "广东",
                        },
                    }
                ],
            }
        },
    }
    category = {
        "category_id": "CBT455865",
        "category_path": "Portable Fans",
        "attributes": {
            "required": [
                {"id": "VOLTAGE", "required": True},
                {"id": "PACKAGE_LENGTH", "required": True},
            ],
            "optional": [
                {"id": "WEIGHT"},
                {"id": "VERTICAL_TAGS", "read_only": True},
            ],
        },
    }

    selected = apply_category_selection(product, "mercadolibre", category)
    draft = selected["drafts"]["mercadolibre"]

    assert draft["attributes"] == {
        "VOLTAGE": "110/220V",
        "WEIGHT": {"value": "182", "unit": "g"},
    }
    assert selected["source"]["attributes"]["产地"] == "广东"
    assert draft["package_dimensions"] == {
        "length_cm": "5.5",
        "width_cm": "6",
        "height_cm": "16",
        "weight_kg": "0.182",
    }


def test_ozon_category_change_drops_previous_description_category_id() -> None:
    product = default_product_model()
    draft = product["drafts"]["ozon"]
    draft["target_sites"][0].update(
        {
            "category_id": "OLD-TYPE",
            "description_category_id": "OLD-DESCRIPTION",
            "category_path": "Old category",
        }
    )

    selected = apply_category_selection(
        product,
        "ozon",
        {
            "platform": "ozon",
            "site": "global",
            "category_id": "NEW-TYPE",
            "category_path": "New category",
            "attributes": {"required": [], "optional": []},
        },
    )

    target = selected["drafts"]["ozon"]["target_sites"][0]
    assert target["category_id"] == "NEW-TYPE"
    assert target["description_category_id"] == ""
    assert target["category_path"] == "New category"
