# -*- coding: utf-8 -*-
"""统一 Provider 契约测试（类目 Schema 分离计划 Phase 1）。

覆盖：
1. Registry 中每个平台实现都继承 CategoryProvider ABC，抽象方法完整，
   平台键唯一，且与 marketplace capability 声明一致；
2. 三个平台输出相同的 CategoryDefinition 规范形状；
3. 定义序列化不含 raw、完整 values 或 raw.values；
4. Ozon platform_binding 归一化 attribute_complex_id。
"""

from __future__ import annotations

from typing import Any

import pytest

from erp_web.marketplace_registry import (
    CAP_CATEGORY_ATTRIBUTES,
    MARKETPLACE_SPECS,
)
from erp_web.marketplaces.category_provider import CategoryProvider
from erp_web.runtime_units import category_providers
from erp_web.runtime_units.category_catalog import CategoryCatalog
from erp_web.runtime_units.category_definition_support import (
    definition_from_legacy_attributes,
)
from erp_web.schemas.category_definition import CategoryDefinition


# -- Registry 契约 ------------------------------------------------------------


def test_registry_contract_enforced_by_builder() -> None:
    registry = category_providers.build_category_provider_registry()
    assert set(registry) == {"mercadolibre", "ozon", "yandex"}
    for spec in MARKETPLACE_SPECS:
        has_capability = CAP_CATEGORY_ATTRIBUTES in spec.capabilities
        assert (spec.key in registry) is has_capability


def test_all_registered_providers_inherit_abc() -> None:
    registry = category_providers.build_category_provider_registry()
    for platform, provider in registry.items():
        assert isinstance(provider, CategoryProvider)
        assert provider.platform == platform
        for method in (
            "resolve_site",
            "category_detail",
            "attribute_definitions",
            "attribute_values",
        ):
            assert callable(getattr(provider, method)), f"{platform} 缺少 {method}"


def test_abstract_provider_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        CategoryProvider()  # type: ignore[abstract]


def test_catalog_rejects_non_provider_and_mismatched_keys() -> None:
    with pytest.raises(RuntimeError):
        CategoryCatalog({"ozon": object()})  # type: ignore[dict-item]
    registry = category_providers.build_category_provider_registry()
    with pytest.raises(RuntimeError):
        CategoryCatalog({"yandex": registry["ozon"]})


def test_registry_platform_keys_unique() -> None:
    providers = (
        category_providers.MercadoLibreCategoryProvider(),
        category_providers.OzonCategoryProvider(),
        category_providers.YandexCategoryProvider(),
    )
    keys = [provider.platform for provider in providers]
    assert len(keys) == len(set(keys))


# -- 规范形状 -----------------------------------------------------------------


def _assert_canonical_shape(definition: CategoryDefinition) -> None:
    payload = definition.model_dump(mode="json")
    text_keys = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            text_keys.update(str(key) for key in value)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    assert "raw" not in text_keys
    assert "values" not in text_keys
    for attribute in (*definition.required, *definition.optional):
        assert len(attribute.options) <= 50


def test_yandex_definition_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    leaf_record = {
        "platform": "yandex",
        "site": "global",
        "category_id": "16088928",
        "name_original": "Футболки",
        "category_path": "Одежда / Футболки",
        "is_leaf": True,
        "attributes": {
            "required": [
                {
                    "parameter_id": "10096",
                    "name": "Цвет",
                    "required": True,
                    "parameter_type": "ENUM",
                    "is_collection": True,
                    "allow_custom_values": False,
                    "max_value_count": 10,
                    "unit": "",
                    "unit_options": [],
                    "units": [],
                    "default_unit": "",
                    "default_unit_id": "",
                    "constraints": {},
                    "values": [
                        {"value_id": 30072093, "value": "синий"},
                        {"value_id": 30072094, "value": "красный"},
                    ],
                    "raw": {"parameterId": 10096},
                }
            ],
            "optional": [
                {
                    "parameter_id": "4389",
                    "name": "Состав",
                    "required": False,
                    "parameter_type": "TEXT",
                    "is_collection": False,
                    "allow_custom_values": True,
                    "max_value_count": 1,
                    "unit": "г",
                    "unit_options": ["г", "кг"],
                    "units": [{"id": 111, "name": "г"}, {"id": 222, "name": "кг"}],
                    "default_unit": "г",
                    "default_unit_id": "111",
                    "constraints": {"max_length": "255"},
                    "values": [],
                    "raw": {"parameterId": 4389},
                }
            ],
        },
    }
    monkeypatch.setattr(
        category_providers,
        "fetch_yandex_leaf_record",
        lambda category_id, include_attributes=False, timeout_seconds=None: leaf_record,
    )
    monkeypatch.setattr(
        category_providers,
        "yandex_credential_scope_hash",
        lambda: "sha256:test",
    )

    provider = category_providers.YandexCategoryProvider()
    definition = provider.attribute_definitions("16088928")

    assert definition.platform == "yandex"
    assert definition.category_id == "16088928"
    assert definition.fingerprint
    color = definition.required[0]
    assert color.value_mode == "strict_enum"
    assert color.is_dictionary is True
    assert color.dictionary_id == "yandex-parameter-10096"
    assert color.max_value_count == 10
    assert color.has_more_values is False
    assert {option.dictionary_value_id for option in color.options} == {
        "30072093",
        "30072094",
    }
    composition = definition.optional[0]
    assert composition.value_mode == "free_text"
    assert composition.unit_ids == ("111", "222")
    assert composition.default_unit == "г"
    _assert_canonical_shape(definition)


def test_ozon_definition_shape_and_complex_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = {
        "platform": "ozon",
        "site": "global",
        "category_id": "94765",
        "type_id": "94765",
        "description_category_id": "17027949",
        "name_original": "Вентиляторы",
        "category_path": "Бытовая техника / Вентиляторы",
        "attributes": {
            "required": [
                {
                    "id": "85",
                    "name": "Бренд",
                    "required": True,
                    "value_type": "String",
                    "dictionary_id": "28732849",
                    "is_dictionary": True,
                    "is_collection": False,
                    "max_value_count": 1,
                    "category_dependent": True,
                    "options": [],
                    "description": "",
                    "raw": {"attribute_complex_id": 0, "type": "String"},
                },
                {
                    "id": "9048",
                    "name": "Название модели",
                    "required": True,
                    "value_type": "String",
                    "dictionary_id": "0",
                    "is_dictionary": False,
                    "is_collection": False,
                    "max_value_count": 0,
                    "category_dependent": False,
                    "options": [],
                    "description": "",
                    "raw": {"attribute_complex_id": 4191, "type": "String"},
                },
            ],
            "optional": [],
        },
    }
    monkeypatch.setattr(
        category_providers,
        "fetch_ozon_category_record",
        lambda category_id, include_attributes=False, timeout_seconds=None: record,
    )
    monkeypatch.setattr(
        category_providers,
        "ozon_credential_scope_hash",
        lambda: "sha256:test",
    )

    provider = category_providers.OzonCategoryProvider()
    definition = provider.attribute_definitions("94765")

    assert definition.description_category_id == "17027949"
    brand = definition.attribute_by_id("85")
    assert brand is not None
    assert brand.is_dictionary is True
    assert brand.category_dependent is True
    assert brand.platform_binding.complex_id == "0"
    assert brand.has_more_values is True, "字典属性预览截断后必须提示分页读取"
    model_name = definition.attribute_by_id("9048")
    assert model_name is not None
    assert model_name.is_dictionary is False
    assert model_name.platform_binding.complex_id == "4191"
    _assert_canonical_shape(definition)


def test_mercadolibre_definition_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = {
        "id": "MLM-100",
        "name": "Ventiladores",
        "path_from_root": [{"id": "MLM-1", "name": "Electrodomésticos"}],
    }
    attributes = {
        "required": [
            {
                "id": "BRAND",
                "name": "Marca",
                "required": True,
                "value_type": "string",
                "options": ["Genérica", "Otra"],
                "raw": {"id": "BRAND", "values": [{"id": "1", "name": "Genérica"}]},
            }
        ],
        "optional": [],
    }
    monkeypatch.setattr(
        category_providers,
        "mercadolibre_category_detail",
        lambda category_id, access_token=None, http_client=None: detail,
    )
    monkeypatch.setattr(
        category_providers,
        "mercadolibre_category_attributes",
        lambda category_id, access_token=None, http_client=None: attributes,
    )

    provider = category_providers.MercadoLibreCategoryProvider()
    definition = provider.attribute_definitions("MLM-100", site="MLM")

    assert definition.site == "MLM"
    assert definition.category_path.startswith("Electrodomésticos")
    brand = definition.required[0]
    assert brand.id == "BRAND"
    assert brand.value_mode == "open_enum"
    assert [option.value for option in brand.options][:2] == ["Genérica", "Otra"]
    _assert_canonical_shape(definition)


def test_definition_from_legacy_drops_unidentified_attributes() -> None:
    definition = definition_from_legacy_attributes(
        platform="ozon",
        site="global",
        category_id="1",
        required=[{"id": "", "name": "无 ID"}, {"id": "85", "name": "品牌"}],
        optional=[],
    )
    assert [attribute.id for attribute in definition.required] == ["85"]
