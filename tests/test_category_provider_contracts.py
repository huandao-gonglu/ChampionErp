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
from erp_web.runtime_units.category_refresh import normalize_ml_attribute
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
        lambda category_id, access_token=None, http_client=None, **kwargs: attributes,
    )
    monkeypatch.setattr(
        category_providers,
        "mercadolibre_category_technical_specs",
        lambda category_id, access_token=None, http_client=None: {"groups": []},
    )
    monkeypatch.setattr(
        category_providers,
        "mercadolibre_catalog_attribute_top_values",
        lambda *args, **kwargs: [],
    )

    provider = category_providers.MercadoLibreCategoryProvider()
    monkeypatch.setattr(
        provider,
        "_store_config",
        lambda: {"site_id": "MLM", "access_token": "saved-token"},
    )
    definition = provider.attribute_definitions("MLM-100", site="MLM")

    assert definition.site == "MLM"
    assert definition.category_path.startswith("Electrodomésticos")
    brand = definition.required[0]
    assert brand.id == "BRAND"
    assert brand.value_mode == "open_enum"
    assert [option.value for option in brand.options][:2] == ["Genérica", "Otra"]
    _assert_canonical_shape(definition)


def test_mercadolibre_definition_merges_restricted_brand_top_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, str, Any]] = []
    top_values = [
        {"id": str(index), "name": f"Brand {index}", "metric": 100 - index}
        for index in range(51)
    ]

    def fake_http_json(
        url: str,
        access_token: str | None = None,
        *,
        timeout_seconds: float = 8,
        method: str = "GET",
        payload: dict[str, Any] | list[Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        del timeout_seconds
        calls.append((url, access_token, method, payload))
        if url.endswith("/categories/CBT455865"):
            return {
                "id": "CBT455865",
                "name": "Portable Fans",
                "settings": {"catalog_domain": "CBT-PORTABLE_FANS"},
                "path_from_root": [],
            }
        if url.endswith("/categories/CBT455865/technical_specs/input"):
            return {
                "groups": [
                    {
                        "components": [
                            {
                                "component": "TEXT_INPUT",
                                "ui_config": {"allow_custom_value": False},
                                "attributes": [{"id": "BRAND"}, {"id": "MODEL"}],
                            }
                        ]
                    }
                ]
            }
        if "/top_values?limit=51" in url:
            return top_values
        if url.endswith("/categories/CBT455865/attributes"):
            return [
                {
                    "id": "BRAND",
                    "name": "Brand",
                    "tags": {"required": True, "catalog_required": True},
                    "value_type": "string",
                    "value_max_length": 255,
                },
                {
                    "id": "MODEL",
                    "name": "Model",
                    "tags": {"required": True, "catalog_required": True},
                    "value_type": "string",
                    "value_max_length": 255,
                },
            ]
        raise AssertionError(f"未预期的 Mercado 请求：{url}")

    provider = category_providers.MercadoLibreCategoryProvider()
    monkeypatch.setattr(
        provider,
        "_store_config",
        lambda: {"site_id": "CBT", "access_token": "saved-token"},
    )
    monkeypatch.setattr(category_providers, "http_json", fake_http_json)
    monkeypatch.setattr(
        category_providers,
        "load_definition_through_cache",
        lambda **kwargs: kwargs["live_loader"](),
    )

    definition = provider.attribute_definitions("CBT455865", site="CBT")

    brand = definition.attribute_by_id("BRAND")
    assert brand is not None
    assert brand.allow_custom_values is False
    assert brand.value_mode == "strict_enum"
    assert brand.is_dictionary is True
    assert len(brand.options) == 50
    assert brand.options[0].dictionary_value_id == "0"
    assert brand.has_more_values is True
    model = definition.attribute_by_id("MODEL")
    assert model is not None
    assert model.allow_custom_values is False
    assert model.value_mode == "free_text"
    top_call = next(call for call in calls if "/top_values" in call[0])
    assert top_call == (
        "https://api.mercadolibre.com/catalog_domains/CBT-PORTABLE_FANS/"
        "attributes/BRAND/top_values?limit=51",
        "saved-token",
        "POST",
        None,
    )


def test_mercadolibre_brand_attribute_values_use_top_values_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http_json(
        url: str,
        access_token: str | None = None,
        *,
        timeout_seconds: float = 8,
        method: str = "GET",
        payload: dict[str, Any] | list[Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        del timeout_seconds, payload
        assert access_token == "saved-token"
        calls.append((url, method))
        if url.endswith("/categories/CBT455865/attributes"):
            return [{"id": "BRAND", "value_type": "string", "tags": {}}]
        if url.endswith("/categories/CBT455865"):
            return {
                "id": "CBT455865",
                "settings": {"catalog_domain": "CBT-PORTABLE_FANS"},
            }
        if url.endswith("/top_values?limit=1000"):
            return [
                {"id": str(index), "name": f"Brand {index}"}
                for index in range(125)
            ]
        raise AssertionError(f"未预期的 Mercado 请求：{url}")

    provider = category_providers.MercadoLibreCategoryProvider()
    monkeypatch.setattr(
        provider,
        "_store_config",
        lambda: {"site_id": "CBT", "access_token": "saved-token"},
    )
    monkeypatch.setattr(category_providers, "http_json", fake_http_json)

    first = provider.attribute_values(
        "CBT455865",
        "BRAND",
        site="CBT",
        limit=50,
    )
    second = provider.attribute_values(
        "CBT455865",
        "BRAND",
        site="CBT",
        limit=50,
        cursor=first.next_cursor,
    )
    searched = provider.attribute_values(
        "CBT455865",
        "BRAND",
        site="CBT",
        query="Brand 124",
    )

    assert len(first.values) == 50 and first.has_more is True
    assert first.next_cursor == "offset:50"
    assert second.values[0].dictionary_value_id == "50"
    assert searched.values[0].value == "Brand 124"
    assert (
        "https://api.mercadolibre.com/catalog_domains/CBT-PORTABLE_FANS/"
        "attributes/BRAND/top_values?limit=1000",
        "POST",
    ) in calls


def test_mercadolibre_definition_rejects_missing_token_before_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = category_providers.MercadoLibreCategoryProvider()
    monkeypatch.setattr(
        provider,
        "_store_config",
        lambda: {"site_id": "CBT", "access_token": ""},
    )
    monkeypatch.setattr(
        category_providers,
        "load_definition_through_cache",
        lambda **_kwargs: pytest.fail("缺少 Token 时不得读取定义缓存"),
    )

    with pytest.raises(RuntimeError, match="Access Token"):
        provider.attribute_definitions("CBT455865", site="CBT")


def test_mercadolibre_definition_without_brand_skips_top_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_http_json(
        url: str,
        access_token: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | list[Any]:
        del access_token, kwargs
        if "/top_values" in url:
            raise AssertionError("无 BRAND 的类目不得请求 BRAND top_values")
        if url.endswith("/categories/CBT-NO-BRAND"):
            return {
                "id": "CBT-NO-BRAND",
                "name": "No Brand Attribute",
                "settings": {"catalog_domain": "CBT-NO-BRAND"},
            }
        if url.endswith("/technical_specs/input"):
            return {"groups": []}
        if url.endswith("/attributes"):
            return [
                {
                    "id": "MODEL",
                    "name": "Model",
                    "value_type": "string",
                    "tags": {"required": True},
                }
            ]
        raise AssertionError(f"未预期的 Mercado 请求：{url}")

    provider = category_providers.MercadoLibreCategoryProvider()
    monkeypatch.setattr(
        provider,
        "_store_config",
        lambda: {"site_id": "CBT", "access_token": "saved-token"},
    )
    monkeypatch.setattr(category_providers, "http_json", fake_http_json)
    monkeypatch.setattr(
        category_providers,
        "load_definition_through_cache",
        lambda **kwargs: kwargs["live_loader"](),
    )

    definition = provider.attribute_definitions("CBT-NO-BRAND", site="CBT")

    assert definition.attribute_by_id("BRAND") is None
    assert definition.attribute_by_id("MODEL") is not None


def test_mercadolibre_normalization_preserves_wire_ids_units_and_read_only() -> None:
    normalized = [
        normalize_ml_attribute(
            {
                "id": "VOLTAGE",
                "name": "Voltage",
                "value_type": "string",
                "value_max_length": 20,
                "values": [
                    {"id": "198813", "name": "220V"},
                    {"id": "39205163", "name": "110/220V"},
                ],
            }
        ),
        normalize_ml_attribute(
            {
                "id": "WEIGHT",
                "name": "Weight",
                "value_type": "number_unit",
                "allowed_units": [
                    {"id": "g", "name": "g"},
                    {"id": "kg", "name": "kg"},
                    {"id": "lb", "name": "lb"},
                ],
                "default_unit": "kg",
            }
        ),
        normalize_ml_attribute(
            {
                "id": "EMPTY_GTIN_REASON",
                "name": "Empty GTIN reason",
                "value_type": "list",
                "values": [
                    {
                        "id": "17055160",
                        "name": "The product does not have registered code",
                    }
                ],
            }
        ),
        normalize_ml_attribute(
            {
                "id": "VERTICAL_TAGS",
                "name": "Vertical tags",
                "value_type": "string",
                "tags": {"read_only": True},
            }
        ),
    ]
    definition = definition_from_legacy_attributes(
        platform="mercadolibre",
        site="CBT",
        category_id="CBT455865",
        required=[],
        optional=normalized,
    )

    voltage = definition.attribute_by_id("VOLTAGE")
    assert voltage is not None
    assert {
        (option.dictionary_value_id, option.value)
        for option in voltage.options
    } == {
        ("198813", "220V"),
        ("39205163", "110/220V"),
    }
    assert voltage.constraints == {"max_length": "20"}
    weight = definition.attribute_by_id("WEIGHT")
    assert weight is not None
    assert weight.default_unit == "kg"
    assert [(unit.id, unit.name) for unit in weight.unit_options] == [
        ("g", "g"),
        ("kg", "kg"),
        ("lb", "lb"),
    ]
    empty_reason = definition.attribute_by_id("EMPTY_GTIN_REASON")
    assert empty_reason is not None
    assert empty_reason.options[0].dictionary_value_id == "17055160"
    vertical_tags = definition.attribute_by_id("VERTICAL_TAGS")
    assert vertical_tags is not None
    assert vertical_tags.read_only is True


def test_mercadolibre_exact_preview_limit_is_not_marked_incomplete() -> None:
    def definition_for(total: int) -> CategoryDefinition:
        attribute = normalize_ml_attribute(
            {
                "id": "COLOR",
                "name": "Color",
                "value_type": "list",
                "values": [
                    {"id": str(index), "name": f"Color {index}"}
                    for index in range(total)
                ],
            }
        )
        return definition_from_legacy_attributes(
            platform="mercadolibre",
            site="CBT",
            category_id="CBT455865",
            required=[],
            optional=[attribute],
        )

    exact = definition_for(50).attribute_by_id("COLOR")
    truncated = definition_for(51).attribute_by_id("COLOR")
    assert exact is not None and exact.has_more_values is False
    assert truncated is not None and truncated.has_more_values is True


def test_mercadolibre_cbt_search_uses_saved_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, float]] = []

    def fake_http_json(
        url: str,
        access_token: str | None = None,
        *,
        timeout_seconds: float = 8,
    ) -> list[dict[str, Any]]:
        calls.append((url, access_token, timeout_seconds))
        return [
            {
                "domain_id": "CBT-WOODWORKING_TOOLS",
                "domain_name": "Woodworking Tools",
                "category_id": "CBT407134",
                "category_name": "Other",
            }
        ]

    provider = category_providers.MercadoLibreCategoryProvider()
    monkeypatch.setattr(
        provider,
        "_store_config",
        lambda: {"site_id": "CBT", "access_token": "saved-token"},
    )
    monkeypatch.setattr(category_providers, "http_json", fake_http_json)

    candidates = provider.search_categories(
        "woodworking tool",
        site="CBT",
        limit=5,
        timeout_seconds=3,
    )

    assert calls[0][:2] == (
        "https://api.mercadolibre.com/marketplace/domain_discovery/search?q=woodworking%20tool&limit=5",
        "saved-token",
    )
    assert calls[0][2] == pytest.approx(3, abs=0.1)
    assert candidates[0]["category_id"] == "CBT407134"
    assert candidates[0]["site"] == "CBT"


def test_mercadolibre_cbt_search_rejects_missing_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = category_providers.MercadoLibreCategoryProvider()
    monkeypatch.setattr(
        provider,
        "_store_config",
        lambda: {"site_id": "CBT", "access_token": ""},
    )
    monkeypatch.setattr(
        category_providers,
        "http_json",
        lambda *_args, **_kwargs: pytest.fail("缺少 Token 时不应发送请求"),
    )

    with pytest.raises(RuntimeError, match="Access Token.*请先填写"):
        provider.search_categories("woodworking tool", site="CBT")


def test_mercadolibre_regional_search_remains_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_http_json(
        url: str,
        access_token: str | None = None,
        *,
        timeout_seconds: float = 8,
    ) -> list[dict[str, Any]]:
        del timeout_seconds
        calls.append((url, access_token))
        return []

    provider = category_providers.MercadoLibreCategoryProvider()
    monkeypatch.setattr(
        provider,
        "_store_config",
        lambda: {"site_id": "CBT", "access_token": "saved-token"},
    )
    monkeypatch.setattr(category_providers, "http_json", fake_http_json)

    assert provider.search_categories("necklace", site="MLM", limit=5) == []
    assert calls == [
        (
            "https://api.mercadolibre.com/sites/MLM/domain_discovery/search?q=necklace&limit=5",
            None,
        )
    ]


def test_definition_from_legacy_drops_unidentified_attributes() -> None:
    definition = definition_from_legacy_attributes(
        platform="ozon",
        site="global",
        category_id="1",
        required=[{"id": "", "name": "无 ID"}, {"id": "85", "name": "品牌"}],
        optional=[],
    )
    assert [attribute.id for attribute in definition.required] == ["85"]
