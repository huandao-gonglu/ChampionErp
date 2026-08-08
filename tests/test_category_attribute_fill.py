from __future__ import annotations

from erp_web.product_model import (
    build_ai_attribute_fill,
    default_product_model,
    validate_category_precheck,
)
from erp_web.runtime_units import category_attribute_ai_fill


def test_ai_attribute_fill_treats_attribute_id_value_as_missing() -> None:
    product = default_product_model()
    product["drafts"]["mercadolibre"]["model"] = "T-3A"
    product["drafts"]["mercadolibre"]["attributes"] = {
        "BRAND": "Generic",
        "MODEL": "T-3A",
        "AIR_CONDITIONER_TYPE": "AIR_CONDITIONER_TYPE",
    }
    category = {
        "category_id": "MLM459570",
        "attributes": {
            "required": [
                {"id": "BRAND", "name": "Marca", "required": True},
                {"id": "MODEL", "name": "Modelo", "required": True},
                {"id": "AIR_CONDITIONER_TYPE", "name": "Tipo de aire acondicionado", "required": True, "options": ["Split", "Window"]},
            ],
            "optional": [],
        },
    }

    result = build_ai_attribute_fill(product, "mercadolibre", category)

    assert result["attributes"]["BRAND"] == "Generic"
    assert result["attributes"]["MODEL"] == "T-3A"
    assert "AIR_CONDITIONER_TYPE" not in result["attributes"]
    assert "AIR_CONDITIONER_TYPE" in result["need_review"]


def test_ai_model_attribute_fill_uses_product_context_and_validates_options(monkeypatch) -> None:
    product = default_product_model()
    product["name"] = "Portable air conditioner"
    product["source"]["title"] = "Portable electric air conditioner with cooling"
    product["drafts"]["mercadolibre"]["brand"] = "Generic"
    product["drafts"]["mercadolibre"]["model"] = "T-3A"
    product["drafts"]["mercadolibre"]["attributes"] = {
        "BRAND": "Generic",
        "MODEL": "T-3A",
        "AIR_CONDITIONER_TYPE": "AIR_CONDITIONER_TYPE",
        "POWER_SUPPLY_TYPE": "POWER_SUPPLY_TYPE",
    }
    category = {
        "category_id": "MLM459570",
        "attributes": {
            "required": [
                {"id": "BRAND", "name": "Marca", "required": True},
                {"id": "MODEL", "name": "Modelo", "required": True},
                {"id": "AIR_CONDITIONER_TYPE", "name": "Tipo de aire acondicionado", "required": True, "options": ["Portable", "Split"]},
                {"id": "POWER_SUPPLY_TYPE", "name": "Tipo de alimentación", "required": True, "options": ["Electric", "Gas"]},
            ],
            "optional": [],
        },
    }
    captured = {}

    def fake_request_ai_fill(sent_product, platform, category_record, schema):
        captured["title"] = sent_product["source"]["title"]
        captured["schema_ids"] = [item["id"] for item in schema]
        return {
            "attributes": {
                "AIR_CONDITIONER_TYPE": "Portable",
                "POWER_SUPPLY_TYPE": "electric",
            },
            "need_review": [],
        }

    monkeypatch.setattr(category_attribute_ai_fill, "_request_ai_fill", fake_request_ai_fill)

    updated, meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(product, "mercadolibre", category)
    attrs = updated["drafts"]["mercadolibre"]["attributes"]

    assert captured["title"] == "Portable electric air conditioner with cooling"
    assert "AIR_CONDITIONER_TYPE" in captured["schema_ids"]
    assert meta["source"] == "ai_model"
    assert attrs["BRAND"] == "Generic"
    assert attrs["MODEL"] == "T-3A"
    assert attrs["AIR_CONDITIONER_TYPE"] == "Portable"
    assert attrs["POWER_SUPPLY_TYPE"] == "Electric"
    assert updated["drafts"]["mercadolibre"]["validation_errors"] == []


def test_ai_model_attribute_fill_resolves_ozon_dictionary_values(monkeypatch) -> None:
    product = default_product_model()
    product["name"] = "共田 F30 手持风扇"
    product["source"]["title"] = "F30 手持充电风扇"
    category = {
        "category_id": "91443",
        "site": "global",
        "attributes": {
            "required": [
                {
                    "id": "8229",
                    "name": "Тип",
                    "required": True,
                    "dictionary_id": "1960",
                    "is_dictionary": True,
                },
                {
                    "id": "85",
                    "name": "Бренд",
                    "required": True,
                    "dictionary_id": "28732849",
                    "is_dictionary": True,
                },
                {
                    "id": "9048",
                    "name": "Название модели",
                    "required": True,
                    "dictionary_id": "0",
                    "is_dictionary": True,
                },
            ],
            "optional": [],
        },
    }

    monkeypatch.setattr(
        category_attribute_ai_fill,
        "_request_ai_fill",
        lambda *args: {
            "attributes": {
                "8229": "Вентилятор",
                "85": "Нет бренда",
                "9048": "F30",
            },
            "need_review": [],
        },
    )

    def fake_values(platform, category_id, attribute_id, **kwargs):
        assert platform == "ozon"
        assert category_id == "91443"
        assert kwargs["site"] == "global"
        values = {
            "8229": [{"id": "91443", "value": "Вентилятор"}],
            "85": [{"id": "126745801", "value": "Нет бренда"}],
        }
        return {"values": values[attribute_id]}

    monkeypatch.setattr(
        category_attribute_ai_fill,
        "fetch_category_attribute_values",
        fake_values,
    )

    updated, meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(
        product,
        "ozon",
        category,
    )
    draft = updated["drafts"]["ozon"]

    assert draft["attributes"]["8229"] == {
        "values": [{"dictionary_value_id": 91443, "value": "Вентилятор"}]
    }
    assert draft["attributes"]["85"] == {
        "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]
    }
    assert draft["attributes"]["9048"] == "F30"
    assert draft["validation_errors"] == []
    assert meta["ai_filled"] == ["8229", "85", "9048"]


def test_unresolved_optional_dictionary_attribute_is_not_a_blocking_error(
    monkeypatch,
) -> None:
    product = default_product_model()
    category = {
        "category_id": "91443",
        "site": "global",
        "attributes": {
            "required": [
                {
                    "id": "9048",
                    "name": "Название модели",
                    "required": True,
                    "dictionary_id": "0",
                }
            ],
            "optional": [
                {
                    "id": "20210",
                    "name": "Вид вентилятора",
                    "required": False,
                    "dictionary_id": "1234",
                }
            ],
        },
    }
    monkeypatch.setattr(
        category_attribute_ai_fill,
        "_request_ai_fill",
        lambda *args: {
            "attributes": {"9048": "F30", "20210": "Ручной"},
            "need_review": [],
        },
    )
    monkeypatch.setattr(
        category_attribute_ai_fill,
        "fetch_category_attribute_values",
        lambda *args, **kwargs: {"values": []},
    )

    updated, meta = category_attribute_ai_fill.apply_ai_model_attribute_fill(
        product,
        "ozon",
        category,
    )
    draft = updated["drafts"]["ozon"]

    assert draft["attributes"]["9048"] == "F30"
    assert "20210" not in draft["attributes"]
    assert draft["validation_errors"] == []
    assert meta["ai_filled"] == ["9048"]


def test_category_precheck_only_reports_missing_required_category_attributes() -> None:
    product = default_product_model()
    draft = product["drafts"]["mercadolibre"]
    draft["category_id"] = "MLM123"
    draft["brand"] = ""
    draft["model"] = ""
    draft["package_dimensions"] = {
        "length_cm": "21",
        "width_cm": "",
        "height_cm": "",
        "weight_kg": "",
    }
    draft["attributes"] = {"REQUIRED_VALUE": "filled"}
    category = {
        "category_id": "MLM123",
        "attributes": {
            "required": [
                {"id": "REQUIRED_VALUE", "required": True},
                {"id": "PACKAGE_LENGTH", "required": True},
                {"id": "MISSING_REQUIRED", "required": True},
            ],
            "optional": [
                {"id": "OPTIONAL_VALUE", "required": False},
            ],
        },
    }

    result = validate_category_precheck(product, "mercadolibre", category)

    assert result == ["attributes.MISSING_REQUIRED"]


def test_dictionary_attribute_requires_a_selected_platform_value() -> None:
    product = default_product_model()
    draft = product["drafts"]["ozon"]
    draft["category_id"] = "94765"
    draft["attributes"] = {"85": "中性"}
    category = {
        "category_id": "94765",
        "attributes": {
            "required": [
                {
                    "id": "85",
                    "name": "Бренд",
                    "required": True,
                    "dictionary_id": "28732849",
                    "is_dictionary": True,
                }
            ],
            "optional": [],
        },
    }

    filled = build_ai_attribute_fill(product, "ozon", category)

    assert "85" not in filled["attributes"]
    assert filled["need_review"] == ["85"]
    assert validate_category_precheck(product, "ozon", category) == [
        "attributes.85"
    ]

    draft["attributes"]["85"] = {
        "values": [
            {
                "dictionary_value_id": 126745801,
                "value": "Нет бренда",
            }
        ]
    }
    assert validate_category_precheck(product, "ozon", category) == []
