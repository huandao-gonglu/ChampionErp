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
    assert payload["sites_to_sell"][0]["pictures"] == payload["pictures"]
    assert payload["condition"] == "new"
    assert "package_length" not in payload
    assert "package_width" not in payload
    assert "package_height" not in payload
    assert "package_weight" not in payload
    assert all(attribute["id"] != "ITEM_CONDITION" for attribute in payload["attributes"])
