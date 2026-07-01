from __future__ import annotations

from erp_web.product_model import build_ai_attribute_fill, default_product_model
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
        "attributes_cache": {
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
        "attributes_cache": {
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
