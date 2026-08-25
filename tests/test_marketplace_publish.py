from __future__ import annotations

import pytest
from unittest.mock import patch

from erp_web import marketplaces as marketplace_publish
from erp_web.runtime_units.publish_confirmation import canonical_publish_digest


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
            "mercadolibre": {
                "site_id": "CBT",
                "category_id": "CBT123",
                "marketplace_bindings": [
                    {"site_id": "MLM", "logistic_type": "remote"}
                ],
            },
            "listing": {
                "price": "18",
                "currency_id": "USD",
                "stock": "5",
                "sku": "SKU-1",
                "mercadolibre_sites_to_sell": [
                    {"site_id": "mlm", "logistic_type": "REMOTE"}
                ],
            },
        },
        ["ml-id:123-CBT456"],
    )

    assert payload["pictures"] == [{"id": "123-CBT456"}]
    assert payload["sites_to_sell"][0]["site_id"] == "MLM"
    assert payload["sites_to_sell"][0]["logistic_type"] == "remote"
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


@pytest.mark.parametrize(
    ("sites_to_sell", "bindings", "error_code"),
    [
        ([], [{"site_id": "MLM", "logistic_type": "remote"}], "MERCADOLIBRE_SITES_TO_SELL_REQUIRED"),
        ([{"site_id": "CBT", "logistic_type": "remote"}], [{"site_id": "CBT", "logistic_type": "remote"}], "MERCADOLIBRE_SALES_TARGET_CBT_INVALID"),
        ([{"site_id": "MLM", "logistic_type": "drop_off"}], [{"site_id": "MLM", "logistic_type": "remote"}], "MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED"),
    ],
)
def test_global_mercadolibre_payload_rejects_invalid_sales_targets(
    sites_to_sell: list[dict[str, str]],
    bindings: list[dict[str, str]],
    error_code: str,
) -> None:
    with pytest.raises(RuntimeError, match=error_code):
        marketplace_publish.build_mercadolibre_payload(
            {"name": "Test", "category_id": "CBT123"},
            {"platforms": {"mercadolibre": {"listing": {}}}},
            {
                "mercadolibre": {
                    "site_id": "CBT",
                    "marketplace_bindings": bindings,
                },
                "listing": {
                    "price": "18",
                    "currency_id": "USD",
                    "mercadolibre_sites_to_sell": sites_to_sell,
                },
            },
            ["https://example.com/a.jpg"],
        )


def test_global_mercadolibre_payload_rejects_non_usd_currency() -> None:
    with pytest.raises(RuntimeError, match="MERCADOLIBRE_CBT_CURRENCY_INVALID"):
        marketplace_publish.build_mercadolibre_payload(
            {"name": "Test", "category_id": "CBT123"},
            {"platforms": {"mercadolibre": {"listing": {}}}},
            {
                "mercadolibre": {
                    "site_id": "CBT",
                    "marketplace_bindings": [
                        {"site_id": "MLM", "logistic_type": "remote"}
                    ],
                },
                "listing": {
                    "price": "18",
                    "currency_id": "MXN",
                    "mercadolibre_sites_to_sell": [
                        {"site_id": "MLM", "logistic_type": "remote"}
                    ],
                },
            },
            ["https://example.com/a.jpg"],
        )


def test_global_mercadolibre_payload_blocks_fully_managed_price_flow() -> None:
    with pytest.raises(RuntimeError, match="MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED"):
        marketplace_publish.build_mercadolibre_payload(
            {"name": "Test", "category_id": "CBT123"},
            {"platforms": {"mercadolibre": {"listing": {}}}},
            {
                "mercadolibre": {
                    "site_id": "CBT",
                    "marketplace_bindings": [
                        {
                            "site_id": "MLM",
                            "logistic_type": "remote",
                            "business_model": "standard",
                        },
                        {
                            "site_id": "MLB",
                            "logistic_type": "remote",
                            "business_model": "CBT CN Fulfillment Managed",
                        }
                    ],
                },
                "listing": {
                    "price": "18",
                    "currency_id": "USD",
                    "mercadolibre_sites_to_sell": [
                        {"site_id": "MLM", "logistic_type": "remote"}
                    ],
                },
            },
            ["https://example.com/a.jpg"],
        )


def test_publish_confirmation_digest_changes_with_cbt_sales_targets() -> None:
    identity = {
        "product_id": "product-1",
        "draft_id": "draft-1",
        "platform": "mercadolibre",
        "site": "CBT",
        "store_identity": "mercadolibre:test-seller",
    }

    first = canonical_publish_digest(
        **identity,
        payload={
            "category_id": "CBT123",
            "sites_to_sell": [
                {"site_id": "MLM", "logistic_type": "remote"}
            ],
        },
    )
    second = canonical_publish_digest(
        **identity,
        payload={
            "category_id": "CBT123",
            "sites_to_sell": [
                {"site_id": "MLB", "logistic_type": "remote"}
            ],
        },
    )

    assert first != second


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


def test_cbt_payload_requires_real_cbt_category() -> None:
    with pytest.raises(RuntimeError, match="CBT 发布必须使用真实 CBT 类目 ID"):
        marketplace_publish.build_mercadolibre_payload(
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
                "mercadolibre": {"site_id": "CBT", "account_site_id": "CBT", "category_id": "MLM455865"},
                "listing": {"price": "18", "currency_id": "USD", "stock": "5", "sku": "SKU-3"},
            },
            ["ml-id:123-CBT456"],
        )


def test_cbt_account_no_longer_turns_local_category_into_global_payload() -> None:
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

    assert payload["_global_selling"] is False
    assert payload["category_id"] == "MLM455865"
    assert "sites_to_sell" not in payload
    attributes = {attribute["id"]: attribute["value_name"] for attribute in payload["attributes"]}
    assert attributes["SELLER_PACKAGE_LENGTH"] == "20.0 cm"
    assert "ITEM_CONDITION" not in attributes
    assert payload["sale_terms"] == [
        {"id": "WARRANTY_TYPE", "value_id": "2230280", "value_name": "Garantía del vendedor"},
        {"id": "WARRANTY_TIME", "value_name": "3 meses", "value_struct": {"number": 3, "unit": "meses"}},
    ]


def test_local_site_payload_rejects_cbt_category() -> None:
    with pytest.raises(RuntimeError, match="MLM 发布必须使用 MLM 类目 ID"):
        marketplace_publish.build_mercadolibre_payload(
            {
                "name": "Site product",
                "brand": "Generic",
                "model": "T-4",
                "category_id": "CBT123",
                "dimensions": "20 x 15 x 10 cm",
                "weight_kg": "0.5",
            },
            {"platforms": {"mercadolibre": {"listing": {"title": "Site product", "description": "Description"}}}},
            {
                "mercadolibre": {"site_id": "MLM", "category_id": "CBT123"},
                "listing": {"price": "18", "currency_id": "USD", "stock": "5", "sku": "SKU-4"},
            },
            ["ml-id:123-MLM456"],
        )


def test_mercadolibre_republishes_same_draft_by_item_id() -> None:
    payload = {
        "_global_selling": False,
        "_item_id": "MLM123",
        "title": "Updated title",
        "category_id": "MLM1",
        "description": {"plain_text": "Updated description"},
    }

    with patch(
        "erp_web.marketplaces.publishing.request_json",
        side_effect=[{}, {}],
    ) as request:
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    assert result == {"id": "MLM123", "operation": "updated"}
    assert request.call_args_list[0].args[:3] == (
        "PUT",
        "https://api.mercadolibre.com/items/MLM123",
        "token",
    )
    assert "_item_id" not in request.call_args_list[0].args[3]
    assert request.call_args_list[1].args[:3] == (
        "PUT",
        "https://api.mercadolibre.com/items/MLM123/description",
        "token",
    )
