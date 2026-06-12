from __future__ import annotations

import marketplace_publish


def test_global_mercadolibre_payload_includes_top_level_pictures() -> None:
    payload = marketplace_publish.build_mercadolibre_payload(
        {
            "name": "Test product",
            "brand": "Generic",
            "model": "T-1",
            "category_id": "CBT123",
            "dimensions": "20 x 15 x 10 cm",
            "weight_kg": "0.5",
        },
        {"platforms": {"mercadolibre": {"listing": {"title": "Test product", "description": "Description"}}}},
        {
            "mercadolibre": {"site_id": "CBT", "category_id": "CBT123"},
            "listing": {"price": "18", "currency_id": "USD", "stock": "5", "sku": "SKU-1"},
        },
        ["ml-id:123-CBT456"],
    )

    assert payload["pictures"] == [{"id": "123-CBT456"}]
    assert "pictures" not in payload["sites_to_sell"][0]
    assert "sale_terms" not in payload["sites_to_sell"][0]
    assert "package_length" not in payload
    assert "package_width" not in payload
    assert "package_height" not in payload
    assert "package_weight" not in payload
    attributes = {attribute["id"]: attribute["value_name"] for attribute in payload["attributes"]}
    assert attributes["PACKAGE_LENGTH"] == "20.0 cm"
    assert attributes["PACKAGE_WIDTH"] == "15.0 cm"
    assert attributes["PACKAGE_HEIGHT"] == "10.0 cm"
    assert attributes["PACKAGE_WEIGHT"] == "500 g"
    assert "SELLER_PACKAGE_LENGTH" not in attributes
    assert attributes["ITEM_CONDITION"] == "New"


def test_site_mercadolibre_payload_does_not_force_global_endpoint() -> None:
    payload = marketplace_publish.build_mercadolibre_payload(
        {
            "name": "Site product",
            "brand": "Generic",
            "model": "T-2",
            "category_id": "MLM455865",
            "dimensions": "20 x 15 x 10 cm",
            "weight_kg": "0.5",
        },
        {"platforms": {"mercadolibre": {"listing": {"title": "Site product", "description": "Description"}}}},
        {
            "mercadolibre": {"site_id": "MLM", "category_id": "MLM455865"},
            "listing": {"price": "18", "currency_id": "USD", "stock": "5", "sku": "SKU-2"},
        },
        ["ml-id:123-MLM456"],
    )

    assert payload["_global_selling"] is False
    assert payload["category_id"] == "MLM455865"
    assert "sites_to_sell" not in payload
    assert "package_length" not in payload
    assert "package_width" not in payload
    assert "package_height" not in payload
    assert "package_weight" not in payload
    assert payload["pictures"] == [{"id": "123-MLM456"}]
    attributes = {attribute["id"]: attribute["value_name"] for attribute in payload["attributes"]}
    assert attributes["SELLER_PACKAGE_LENGTH"] == "20.0 cm"
    assert attributes["SELLER_PACKAGE_WIDTH"] == "15.0 cm"
    assert attributes["SELLER_PACKAGE_HEIGHT"] == "10.0 cm"
    assert attributes["SELLER_PACKAGE_WEIGHT"] == "500 g"
    assert "PACKAGE_LENGTH" not in attributes


def test_cbt_account_maps_site_category_to_global_payload() -> None:
    payload = marketplace_publish.build_mercadolibre_payload(
        {
            "name": "Portable fan",
            "brand": "Generic",
            "model": "T-3",
            "category_id": "MLM455865",
            "dimensions": "20 x 15 x 10 cm",
            "weight_kg": "0.5",
        },
        {"platforms": {"mercadolibre": {"listing": {"title": "Portable fan", "description": "Description"}}}},
        {
            "mercadolibre": {"site_id": "MLM", "account_site_id": "CBT", "category_id": "MLM455865"},
            "listing": {
                "price": "18",
                "currency_id": "USD",
                "stock": "5",
                "sku": "SKU-3",
                "mercadolibre_sale_terms": [
                    {"id": "WARRANTY_TYPE", "value_id": "2230280", "value_name": "Garantía del vendedor"},
                    {"id": "WARRANTY_TIME", "value_name": "30 días"},
                ],
            },
        },
        ["ml-id:123-CBT456"],
    )

    assert payload["_global_selling"] is True
    assert payload["category_id"] == "CBT455865"
    assert payload["sites_to_sell"][0]["site_id"] == "MLM"
    assert "pictures" not in payload["sites_to_sell"][0]
    attributes = {attribute["id"]: attribute["value_name"] for attribute in payload["attributes"]}
    assert attributes["PACKAGE_LENGTH"] == "20.0 cm"
    assert attributes["ITEM_CONDITION"] == "New"
    assert payload["sale_terms"] == [
        {"id": "WARRANTY_TYPE", "value_id": "2230280", "value_name": "Seller warranty"},
        {"id": "WARRANTY_TIME", "value_name": "3 months", "value_struct": {"number": 3, "unit": "months"}},
    ]
