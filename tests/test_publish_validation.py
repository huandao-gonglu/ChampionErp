from __future__ import annotations

from erp_web.runtime_units.publish_validation import validate_mercadolibre_draft


def test_mercadolibre_review_summary_restores_local_attribute_ids() -> None:
    product = {
        "sku": "SKU-1",
        "local_platform_categories": {
            "mercadolibre": {
                "category_id": "MLM1",
                "category_path": "Protectores y Folios",
                "attributes": {
                    "required": [
                        {"id": "GTIN", "name": "Código universal de producto", "required": True},
                        {"id": "RECOMMENDED_AGE_GROUP", "name": "Edad recomendada", "required": True},
                        {"id": "TRADING_CARD_GAME_ACCESSORY_TYPE", "name": "Tipo de accesorio", "required": True},
                        {"id": "EMPTY_GTIN_REASON", "name": "Motivo de GTIN vacío", "required": True},
                    ],
                    "optional": [],
                },
            }
        },
        "drafts": {
            "mercadolibre": {
                "title": "Sample title",
                "description": "Sample description",
                "category_id": "MLM1",
                "category_path": "Protectores y Folios",
                "brand": "Brand",
                "model": "Model",
                "sku": "SKU-1",
                "price": "10",
                "stock": "1",
                "upc": "123456789012",
                "attributes": {"GTIN": "123456789012", "BRAND": "Brand", "MODEL": "Model"},
                "package_dimensions": {"length_cm": "1", "width_cm": "1", "height_cm": "1", "weight_kg": "0.1"},
                "pricing": {"suggested_price": "10"},
                "validation_errors": [
                    {
                        "code": "NEED_REVIEW_ATTRIBUTES",
                        "field": "attributes",
                        "message": "仍有 1 个属性待复核",
                        "severity": "error",
                    }
                ],
                "sale_terms": [{"id": "WARRANTY_TYPE", "value_name": "Seller warranty"}],
                "shipping": {"logistic_type": "drop_off"},
            }
        },
        "images": [{"url": "https://example.com/a.jpg", "selected": True, "platforms": ["mercadolibre"], "is_main": True}],
    }

    result = validate_mercadolibre_draft(product, {"mercadolibre": {"access_token": "x"}, "listing": {}})
    fields = [item["field"] for item in result["errors"] if item["code"] == "NEED_REVIEW_ATTRIBUTES"]

    assert fields == [
        "attributes.RECOMMENDED_AGE_GROUP",
        "attributes.TRADING_CARD_GAME_ACCESSORY_TYPE",
    ]
