from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import patch

import pytest

from erp_web import marketplaces as marketplace_publish
from erp_web.marketplaces import publishing as marketplace_publishing
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.runtime_units import publish_helpers
from erp_web.runtime_units import publish_mercadolibre
from erp_web.runtime_units.publish_confirmation import canonical_publish_digest
from erp_web.runtime_units.publish_helpers import (
    validate_mercadolibre_publish_payload,
)
from erp_web.schemas.category_definition import CategoryDefinition


def _compiled_attributes(listing_model: str) -> list[dict[str, Any]]:
    condition = (
        {
            "id": "ITEM_CONDITION",
            "values": [{"id": "2230284", "name": "New"}],
        }
        if listing_model == "user_products"
        else {
            "id": "ITEM_CONDITION",
            "value_id": "2230284",
            "value_name": "New",
        }
    )
    return [
        {"id": "BRAND", "value_name": "Generic"},
        {"id": "MODEL", "value_name": "T-1"},
        {"id": "SELLER_SKU", "value_name": "SKU-1"},
        {"id": "EMPTY_GTIN_REASON", "value_id": "17055160", "value_name": "The product does not have registered code"},
        {"id": "PACKAGE_LENGTH", "value_name": "20 cm"},
        {"id": "PACKAGE_WIDTH", "value_name": "15 cm"},
        {"id": "PACKAGE_HEIGHT", "value_name": "10 cm"},
        {"id": "PACKAGE_WEIGHT", "value_name": "500 g"},
        condition,
    ]


def _empty_ml_definition(category_id: str = "") -> CategoryDefinition:
    return CategoryDefinition(
        platform="mercadolibre",
        site="CBT",
        category_id=category_id,
    )


def _user_products_config(
    *,
    sites_to_sell: list[dict[str, Any]] | None = None,
    bindings: list[dict[str, object]] | None = None,
    currency_id: str = "USD",
) -> dict:
    targets = sites_to_sell
    if targets is None:
        targets = [{"site_id": "MLM", "logistic_type": "remote"}]
    account_bindings = bindings
    if account_bindings is None:
        account_bindings = [
            {
                "seller_id": "991",
                "site_id": "MLM",
                "logistic_type": "remote",
                "business_model": "",
                "pricing_model": "price",
                "user_product": True,
            }
        ]
    return {
        "mercadolibre": {
            "site_id": "CBT",
            "account_site_id": "CBT",
            "listing_model": "user_products",
            "user_product_seller": True,
            "category_id": "CBT123",
            "marketplace_bindings": account_bindings,
        },
        "listing": {
            "price": "18",
            "currency_id": currency_id,
            "stock": "5",
            "sku": "SKU-1",
            "mercadolibre_title": "Test product",
            "mercadolibre_sites_to_sell": targets,
            "mercadolibre_sale_terms": [
                {
                    "id": "WARRANTY_TYPE",
                    "value_id": "6150835",
                    "value_name": "No warranty",
                }
            ],
        },
    }


def _user_products_payload() -> dict:
    return marketplace_publish.build_mercadolibre_payload(
        {
            "name": "Test product",
            "brand": "Generic",
            "model": "T-1",
            "category_id": "CBT123",
            "dimensions": "20 x 15 x 10 cm",
            "weight_kg": "0.5",
        },
        {
            "platforms": {
                "mercadolibre": {
                    "listing": {
                        "title": "Test product",
                        "description": "Description",
                    }
                }
            }
        },
        _user_products_config(),
        ["https://example.com/not-an-upload.jpg", "ml-id:123-CBT456"],
        category_attributes=_compiled_attributes("user_products"),
    )


def _traditional_global_items_config(
    *,
    pricing_model: str = "price",
    net_proceeds: str = "12.34",
) -> dict:
    sites_to_sell: list[dict[str, Any]] = [
        {"site_id": "MLM", "logistic_type": "remote"}
    ]
    if pricing_model == "net_proceeds":
        sites_to_sell[0]["net_proceeds"] = {
            "amount": net_proceeds,
            "currency": "USD",
        }
    config = _user_products_config(
        sites_to_sell=sites_to_sell,
        bindings=[
            {
                "seller_id": "3345546432",
                "site_id": "MLM",
                "logistic_type": "remote",
                "business_model": "CBT CN International Drop Shipping",
                "pricing_model": pricing_model,
                "user_product": False,
            }
        ]
    )
    config["mercadolibre"].update(
        {
            "access_token": "token",
            "user_id": "3344094721",
            "listing_model": "traditional_global_items",
            "user_product_seller": False,
        }
    )
    config["listing"].update(
        {
            "mercadolibre_title": "Artículo tradicional",
            "mercadolibre_global_title": "Traditional item",
        }
    )
    return config


def _traditional_global_items_payload(
    *,
    pricing_model: str = "price",
    net_proceeds: str = "12.34",
) -> dict:
    return marketplace_publish.build_mercadolibre_payload(
        {
            "name": "Traditional item",
            "brand": "Generic",
            "model": "T-1",
            "category_id": "CBT123",
            "dimensions": "20 x 15 x 10 cm",
            "weight_kg": "0.5",
        },
        {
            "platforms": {
                "mercadolibre": {
                    "listing": {
                        "title": "Traditional item",
                        "description": "Description",
                    }
                }
            }
        },
        _traditional_global_items_config(
            pricing_model=pricing_model,
            net_proceeds=net_proceeds,
        ),
        ["ml-id:123-CBT456"],
        category_attributes=_compiled_attributes("traditional_global_items"),
    )


def test_mercadolibre_payload_product_preserves_explicit_empty_draft_upc() -> None:
    prepared = publish_mercadolibre.mercadolibre_product_for_payload(
        {
            "name": "GTIN exempt product",
            "upc": "725272000243",
            "drafts": {
                "mercadolibre": {
                    "site": "CBT",
                    "upc": "",
                    "allow_gtin_exemption": True,
                }
            },
        }
    )

    assert prepared["upc"] == ""
    assert prepared["drafts"]["mercadolibre"]["upc"] == ""


def test_mercadolibre_user_products_payload_matches_siteless_contract() -> None:
    payload = _user_products_payload()

    assert payload["family_name"] == "Test product"
    assert payload["_listing_model"] == "user_products"
    assert payload["category_id"] == "CBT123"
    assert payload["currency_id"] == "USD"
    assert payload["price"] == 18
    assert payload["available_quantity"] == 5
    assert payload["pictures"] == [{"id": "123-CBT456"}]
    assert payload["sites_to_sell"] == [
        {
            "site_id": "MLM",
            "logistic_type": "remote",
            "price": 18,
            "listing_type_id": "gold_special",
        }
    ]
    assert {"title", "variations", "_global_selling", "_item_id"}.isdisjoint(
        payload
    )

    attributes = {attribute["id"]: attribute for attribute in payload["attributes"]}
    assert attributes["ITEM_CONDITION"] == {
        "id": "ITEM_CONDITION",
        "values": [{"id": "2230284", "name": "New"}],
    }
    assert attributes["PACKAGE_LENGTH"]["value_name"] == "20 cm"
    assert attributes["PACKAGE_WIDTH"]["value_name"] == "15 cm"
    assert attributes["PACKAGE_HEIGHT"]["value_name"] == "10 cm"
    assert attributes["PACKAGE_WEIGHT"]["value_name"] == "500 g"
    assert "SELLER_PACKAGE_LENGTH" not in attributes


def test_mercadolibre_payload_does_not_invent_warranty_when_draft_terms_are_empty() -> None:
    config = _user_products_config()
    config["listing"]["mercadolibre_sale_terms"] = []

    payload = marketplace_publish.build_mercadolibre_payload(
        {"name": "Test product"},
        {
            "platforms": {
                "mercadolibre": {"listing": {"description": "Description"}}
            }
        },
        config,
        ["ml-id:123-CBT456"],
        category_attributes=_compiled_attributes("user_products"),
    )

    assert payload["sale_terms"] == []
    assert "sale_terms / warranty 尚未配置完整" in (
        validate_mercadolibre_publish_payload(payload, config)
    )


def test_mercadolibre_payload_category_only_comes_from_platform_config() -> None:
    config = _user_products_config()
    config["mercadolibre"]["category_id"] = "CBT-DRAFT-CATEGORY"

    payload = marketplace_publish.build_mercadolibre_payload(
        {
            "name": "Source product",
            "category_id": "CBT-SOURCE-CATEGORY",
        },
        {
            "platforms": {
                "mercadolibre": {
                    "listing": {"description": "Description"},
                }
            }
        },
        config,
        ["ml-id:123-CBT456"],
        category_attributes=_compiled_attributes("user_products"),
    )

    assert payload["category_id"] == "CBT-DRAFT-CATEGORY"


def test_mercadolibre_traditional_payload_uses_strict_global_items_contract() -> None:
    config = _traditional_global_items_config()
    payload = _traditional_global_items_payload()

    assert payload["_listing_model"] == "traditional_global_items"
    assert payload["title"] == "Traditional item"
    assert payload["price"] == 18
    assert payload["currency_id"] == "USD"
    assert payload["sites_to_sell"] == [
        {
            "site_id": "MLM",
            "logistic_type": "remote",
            "price": 18,
            "listing_type_id": "gold_special",
            "title": "Artículo tradicional",
        }
    ]
    assert payload["pictures"] == [{"id": "123-CBT456"}]
    assert {"family_name", "global_net_proceeds", "variations"}.isdisjoint(payload)
    assert "net_proceeds" not in payload["sites_to_sell"][0]
    condition = next(
        item for item in payload["attributes"] if item["id"] == "ITEM_CONDITION"
    )
    assert condition == {
        "id": "ITEM_CONDITION",
        "value_id": "2230284",
        "value_name": "New",
    }
    assert validate_mercadolibre_publish_payload(payload, config) == []


def test_mercadolibre_traditional_payload_uses_binding_net_proceeds_only_at_market_level() -> None:
    config = _traditional_global_items_config(pricing_model="net_proceeds")
    payload = _traditional_global_items_payload(pricing_model="net_proceeds")

    assert {"price", "global_net_proceeds"}.isdisjoint(payload)
    assert payload["sites_to_sell"] == [
        {
            "site_id": "MLM",
            "logistic_type": "remote",
            "net_proceeds": 12.34,
            "listing_type_id": "gold_special",
            "title": "Artículo tradicional",
        }
    ]
    assert "price" not in payload["sites_to_sell"][0]
    assert validate_mercadolibre_publish_payload(payload, config) == []


def test_mercadolibre_traditional_payload_rejects_site_only_pictures() -> None:
    payload = _traditional_global_items_payload()
    pictures = payload.pop("pictures")
    payload["sites_to_sell"][0]["pictures"] = pictures
    config = _traditional_global_items_config()

    assert validate_mercadolibre_publish_payload(payload, config) == [
        "传统 Global Items 根级 pictures 不能为空",
        "传统 Global Items pictures 只能位于 payload 根级",
    ]
    with (
        patch.object(
            marketplace_publishing,
            "request_json",
            side_effect=AssertionError("非法 payload 不应发出网络请求"),
        ),
        pytest.raises(RuntimeError, match="根级 pictures"),
    ):
        marketplace_publish.publish_mercadolibre(payload, "token")


def test_mercadolibre_traditional_payload_rejects_oversized_root_title() -> None:
    config = _traditional_global_items_config()
    payload = _traditional_global_items_payload()
    payload["title"] = "X" * 61

    assert "传统 Global Items 根 title 超过平台字符限制" in (
        validate_mercadolibre_publish_payload(payload, config)
    )
    with (
        patch.object(
            marketplace_publishing,
            "request_json",
            side_effect=AssertionError("非法 payload 不应发出网络请求"),
        ),
        pytest.raises(RuntimeError, match="MERCADOLIBRE_GLOBAL_TITLE_TOO_LONG"),
    ):
        marketplace_publish.publish_mercadolibre(payload, "token")


def test_mercadolibre_07d_diagnostic_accepts_traditional_payload(
    tmp_path,
) -> None:
    payload = _traditional_global_items_payload()
    config = _traditional_global_items_config()
    ctx = {
        "product": {},
        "config": config,
        "result": {},
    }

    with (
        patch.object(
            publish_mercadolibre,
            "build_mercadolibre_payload_preview",
            return_value=payload,
        ),
        patch.object(
            publish_mercadolibre,
            "_last_mercadolibre_payload_path",
            return_value=tmp_path / "payload.json",
        ),
        patch.object(publish_mercadolibre, "write_json"),
        patch.object(publish_mercadolibre, "append_ml_auth_test_log"),
    ):
        result = publish_mercadolibre._07d_payload_generate(ctx)

    assert result["ok"] is True
    assert result["missing_keys"] == []


def test_mercadolibre_traditional_payload_separates_global_and_local_titles() -> None:
    config = _traditional_global_items_config()
    config["listing"]["mercadolibre_sites_to_sell"] = [
        {"site_id": "MLM", "logistic_type": "remote"},
        {"site_id": "MLB", "logistic_type": "remote"},
    ]
    config["mercadolibre"]["marketplace_bindings"].append(
        {
            "seller_id": "3345546433",
            "site_id": "MLB",
            "logistic_type": "remote",
            "business_model": "CBT CN International Drop Shipping",
            "pricing_model": "price",
            "user_product": False,
        }
    )

    payload = marketplace_publish.build_mercadolibre_payload(
        {
            "name": "Traditional item",
            "category_id": "CBT123",
            "dimensions": "20 x 15 x 10 cm",
            "weight_kg": "0.5",
        },
        {
            "platforms": {
                "mercadolibre": {
                    "listing": {
                        "title": "Traditional item",
                        "description": "Description",
                    }
                }
            }
        },
        config,
        ["ml-id:123-CBT456"],
        category_attributes=_compiled_attributes("traditional_global_items"),
    )

    assert payload["title"] == "Traditional item"
    assert {
        item["site_id"]: item["title"]
        for item in payload["sites_to_sell"]
    } == {
        "MLB": "Artículo tradicional",
        "MLM": "Artículo tradicional",
    }
    assert "mercadolibre_marketplace_titles" not in config["listing"]


def test_traditional_payload_does_not_restore_parent_id_from_last_publish_task() -> None:
    draft = {
        "last_publish_task": {
            "item_id": "CBT-OLD-TASK",
            "external_id": "CBT-OLD-EXTERNAL",
        },
        "publication": {},
        "sites_to_sell": [],
        "sale_terms": [],
    }
    config = {
        "mercadolibre": {
            "listing_model": "traditional_global_items",
            "user_id": "3344094721",
            "marketplace_bindings": [],
        },
        "listing": {},
    }
    with (
        patch.object(publish_helpers, "build_plan_for_platform", return_value={}),
        patch.object(publish_helpers, "apply_product_drafts_to_plan", return_value={}),
        patch.object(publish_helpers, "_draft_for_selected_target", return_value=draft),
        patch.object(
            publish_helpers,
            "_selected_price_and_currency",
            return_value=("18", "USD"),
        ),
        patch.object(
            publish_helpers.publisher,
            "build_mercadolibre_payload",
            return_value={"_listing_model": "traditional_global_items"},
        ) as build_payload,
    ):
        payload = publish_helpers.build_mercadolibre_publish_payload(
            {},
            config,
            picture_refs=[],
            category_definition=_empty_ml_definition(),
        )

    assert payload["_publication"]["parent_item_id"] == ""
    assert "CBT-OLD-TASK" not in str(payload)
    assert "CBT-OLD-EXTERNAL" not in str(payload)
    assert "mercadolibre_marketplace_titles" not in build_payload.call_args.args[2][
        "listing"
    ]


def test_publish_helper_does_not_fall_back_to_stale_listing_values() -> None:
    draft = {
        "category_id": "CBT123",
        "site": "CBT",
        "title": "",
        "global_title": "",
        "language": "es",
        "stock": "",
        "sku": "",
        "upc": "",
        "model": "",
        "attributes": {},
        "publication": {},
        "sites_to_sell": [],
    }
    config = {
        "mercadolibre": {
            "listing_model": "traditional_global_items",
            "user_id": "3344094721",
            "marketplace_bindings": [],
        },
        "listing": {
            "price": "999",
            "currency_id": "USD",
            "stock": "99",
            "mercadolibre_title": "旧本地标题",
            "mercadolibre_global_title": "Stale global title",
            "mercadolibre_language": "pt-BR",
            "mercadolibre_sale_terms": [
                {"id": "WARRANTY_TYPE", "value_name": "Seller warranty"}
            ],
        },
    }
    with (
        patch.object(publish_helpers, "build_plan_for_platform", return_value={}),
        patch.object(publish_helpers, "apply_product_drafts_to_plan", return_value={}),
        patch.object(publish_helpers, "_draft_for_selected_target", return_value=draft),
        patch.object(
            publish_helpers,
            "_selected_price_and_currency",
            return_value=("", ""),
        ),
        patch.object(
            publish_helpers.publisher,
            "build_mercadolibre_payload",
            return_value={"_listing_model": "traditional_global_items"},
        ) as build_payload,
    ):
        publish_helpers.build_mercadolibre_publish_payload(
            {},
            config,
            picture_refs=[],
            category_definition=_empty_ml_definition("CBT123"),
        )

    mapped_listing = build_payload.call_args.args[2]["listing"]
    assert mapped_listing["price"] == ""
    assert mapped_listing["currency_id"] == ""
    assert mapped_listing["stock"] == ""
    assert mapped_listing["mercadolibre_title"] == ""
    assert mapped_listing["mercadolibre_global_title"] == ""
    assert mapped_listing["mercadolibre_language"] == "es"
    assert mapped_listing["mercadolibre_sale_terms"] == []


def test_traditional_remote_parent_without_owner_is_not_claimed_by_current_account() -> None:
    draft = {
        "publication": {
            "model": "traditional_global_items",
            "parent_item_id": "CBT4232215884",
            "markets": [],
        },
        "sites_to_sell": [],
    }
    config = {
        "mercadolibre": {
            "listing_model": "traditional_global_items",
            "user_id": "3344094721",
            "marketplace_bindings": [],
        },
        "listing": {},
    }
    with (
        patch.object(publish_helpers, "build_plan_for_platform", return_value={}),
        patch.object(publish_helpers, "apply_product_drafts_to_plan", return_value={}),
        patch.object(publish_helpers, "_draft_for_selected_target", return_value=draft),
        patch.object(
            publish_helpers,
            "_selected_price_and_currency",
            return_value=("18", "USD"),
        ),
        patch.object(
            publish_helpers.publisher,
            "build_mercadolibre_payload",
            return_value={"_listing_model": "traditional_global_items"},
        ),
    ):
        payload = publish_helpers.build_mercadolibre_publish_payload(
            {},
            config,
            picture_refs=[],
            category_definition=_empty_ml_definition(),
        )

    assert payload["_publication"]["parent_item_id"] == "CBT4232215884"
    assert payload["_publication"]["account_user_id"] == ""


@pytest.mark.parametrize("parent_key", ["item_id", "id"])
@pytest.mark.parametrize(
    ("pricing_model", "expected_market_pricing_field"),
    [
        ("price", "price"),
        ("net_proceeds", "net_proceeds"),
    ],
)
def test_mercadolibre_traditional_publish_posts_global_items_and_persists_publication(
    parent_key: str,
    pricing_model: str,
    expected_market_pricing_field: str,
) -> None:
    payload = _traditional_global_items_payload(pricing_model=pricing_model)
    payload["_publication"] = {
        "model": "traditional_global_items",
        "account_user_id": "3344094721",
        "markets": [
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "seller_id": "3345546432",
            }
        ],
    }
    response = {
        parent_key: "CBT4232215884",
        "seller_id": "3344094721",
        "site_items": [
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "seller_id": "3345546432",
                "item_id": "MLM5490706828",
                "success": True,
            }
        ],
    }

    with patch.object(
        marketplace_publishing,
        "request_json",
        return_value=response,
    ) as request:
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    request.assert_called_once()
    method, endpoint, token, wire_payload = request.call_args.args
    assert (method, endpoint, token) == (
        "POST",
        "https://api.mercadolibre.com/global/items",
        "token",
    )
    assert request.call_args.kwargs == {"extra_headers": {"parent-item-info": "true"}}
    assert {"_listing_model", "_publication"}.isdisjoint(wire_payload)
    market_pricing_fields = {"price", "net_proceeds"}.intersection(
        wire_payload["sites_to_sell"][0]
    )
    assert market_pricing_fields == {expected_market_pricing_field}
    if pricing_model == "net_proceeds":
        assert {"price", "global_net_proceeds"}.isdisjoint(wire_payload)
        assert wire_payload["sites_to_sell"][0]["net_proceeds"] == 12.34
    else:
        assert wire_payload["price"] == 18
        assert "global_net_proceeds" not in wire_payload
    assert result["ok"] is True
    publication = result["publication"]
    assert publication["model"] == "traditional_global_items"
    assert publication["parent_item_id"] == "CBT4232215884"
    assert publication["markets"][0]["item_id"] == "MLM5490706828"


def test_mercadolibre_traditional_create_surfaces_complete_site_failures() -> None:
    payload = _traditional_global_items_payload()
    sellers = {
        "MCO": "3344101349",
        "MLA": "3345546426",
        "MLC": "3345546438",
        "MLM": "3345546432",
        "MLU": "3345546428",
    }
    payload["sites_to_sell"] = [
        {
            "site_id": site_id,
            "logistic_type": "remote",
            "price": 18,
            "listing_type_id": "gold_special",
            "title": "Artículo tradicional",
        }
        for site_id in sellers
    ]
    payload["_publication"] = {
        "model": "traditional_global_items",
        "account_user_id": "3344094721",
        "markets": [
            {
                "site_id": site_id,
                "logistic_type": "remote",
                "seller_id": seller_id,
            }
            for site_id, seller_id in sellers.items()
        ],
    }
    response = {
        "site_id": "CBT",
        "site_items": [
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "error": {
                    "status": 429,
                    "message": "local_rate_limited",
                    "cause": None,
                },
            },
            {
                "site_id": "MLU",
                "logistic_type": "remote",
                "error": {
                    "status": 400,
                    "error": "validation_error",
                    "message": "Validation error",
                    "cause": [
                        {
                            "code": "site.not_operable",
                            "message": (
                                "Listing in Uruguay is currently unavailable "
                                "for international dropshipping"
                            ),
                        }
                    ],
                },
            },
            {
                "site_id": "MCO",
                "logistic_type": "remote",
                "error": {
                    "status": 422,
                    "error": "validation_error",
                    "message": "Validation error",
                    "cause": [
                        {
                            "code": "item.shipping.mode.not_supported",
                            "message": (
                                "You can't send the product in this kind of "
                                "shipment in Colombia."
                            ),
                        }
                    ],
                },
            },
            {
                "site_id": "MLA",
                "logistic_type": "remote",
                "error": {"status": 429, "message": "local_rate_limited"},
            },
            {
                "site_id": "MLC",
                "logistic_type": "remote",
                "error": {"status": 429, "message": "local_rate_limited"},
            },
        ],
    }

    with patch.object(
        marketplace_publishing,
        "request_json",
        return_value=response,
    ):
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    assert result["ok"] is False
    assert (
        result["error_code"]
        == "MERCADOLIBRE_TRADITIONAL_SITE_ITEMS_FAILED"
    )
    assert "MLM/remote：local_rate_limited" in result["error"]
    assert (
        "MLU/remote：Listing in Uruguay is currently unavailable"
        in result["error"]
    )
    assert "MCO/remote：You can't send the product" in result["error"]
    assert result["site_items"] == response["site_items"]
    assert result["error_map"]["raw"] == response
    assert len(result["error_map"]["field_errors"]["sites_to_sell"]) == 5
    assert result["error_map"]["site_item_errors"][0]["error"] == (
        response["site_items"][0]["error"]
    )
    assert result["error_map"]["next_action"]
    assert "publication" not in result
    assert "outcome_unknown" not in result


def test_mercadolibre_traditional_partial_create_persists_parent_publication() -> None:
    payload = _traditional_global_items_payload()
    payload["sites_to_sell"].append(
        {
            "site_id": "MLU",
            "logistic_type": "remote",
            "price": 18,
            "listing_type_id": "gold_special",
            "title": "Artículo tradicional",
        }
    )
    payload["_publication"] = {
        "model": "traditional_global_items",
        "account_user_id": "3344094721",
        "markets": [
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "seller_id": "3345546432",
            },
            {
                "site_id": "MLU",
                "logistic_type": "remote",
                "seller_id": "3345546429",
            },
        ],
    }
    response = {
        "item_id": "CBT4232215884",
        "seller_id": "3344094721",
        "site_items": [
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "seller_id": "3345546432",
                "item_id": "MLM5490706828",
                "success": True,
            },
            {
                "site_id": "MLU",
                "logistic_type": "remote",
                "error": {
                    "status": 400,
                    "error": "validation_error",
                    "cause": [
                        {
                            "code": "site.not_operable",
                            "message": (
                                "Listing in Uruguay is currently unavailable "
                                "for international dropshipping"
                            ),
                        }
                    ],
                },
            },
        ],
    }

    with patch.object(
        marketplace_publishing,
        "request_json",
        return_value=response,
    ):
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    assert result["ok"] is False
    assert (
        result["error_code"]
        == "MERCADOLIBRE_TRADITIONAL_SITE_ITEMS_FAILED"
    )
    assert "Listing in Uruguay is currently unavailable" in result["error"]
    publication = result["publication"]
    assert publication["parent_item_id"] == "CBT4232215884"
    assert publication["status"] == "partial"
    markets = {item["site_id"]: item for item in publication["markets"]}
    assert markets["MLM"]["item_id"] == "MLM5490706828"
    assert markets["MLU"]["last_operation"]["status"] == "failed"

    response_without_parent = deepcopy(response)
    response_without_parent.pop("item_id")
    with patch.object(
        marketplace_publishing,
        "request_json",
        return_value=response_without_parent,
    ):
        unknown = marketplace_publish.publish_mercadolibre(payload, "token")

    assert unknown["error_code"] == "MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID"
    assert unknown["status"] == "outcome_unknown"
    assert "MLU/remote：Listing in Uruguay" in unknown["error"]
    assert "缺少远端 parent item_id" in unknown["error"]
    assert "publication" not in unknown


def test_mercadolibre_traditional_successful_sites_without_parent_are_unknown() -> None:
    payload = _traditional_global_items_payload()
    payload["_publication"] = {
        "model": "traditional_global_items",
        "account_user_id": "3344094721",
        "markets": [
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "seller_id": "3345546432",
            }
        ],
    }
    response = {
        "site_items": [
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "seller_id": "3345546432",
                "item_id": "MLM5490706828",
                "success": True,
            }
        ]
    }

    with patch.object(
        marketplace_publishing,
        "request_json",
        return_value=response,
    ):
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    assert result["ok"] is False
    assert result["error_code"] == "MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID"
    assert result["status"] == "outcome_unknown"
    assert result["outcome_unknown"] is True
    assert result["remote_write_dispatched"] is True
    assert "缺少远端 parent item_id" in result["error"]
    assert "publication" not in result


@pytest.mark.parametrize(
    "response",
    [
        {},
        {
            "item_id": "CBT4232215884",
            "errors": [{"message": "rejected"}],
            "site_items": [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "seller_id": "3345546432",
                    "item_id": "MLM5490706828",
                }
            ],
        },
        {
            "item_id": "CBT4232215884",
            "site_items": [
                {
                    "site_id": "MLM",
                    "seller_id": "3345546432",
                    "item_id": "MLM5490706828",
                }
            ],
        },
        {
            "item_id": "CBT4232215884",
            "site_items": [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "seller_id": "unexpected-seller",
                    "item_id": "MLM5490706828",
                }
            ],
        },
        {
            "item_id": "CBT4232215884",
            "site_items": [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "seller_id": "3345546432",
                    "item_id": "MLM5490706828",
                },
                {
                    "site_id": "MLB",
                    "logistic_type": "remote",
                    "seller_id": "other",
                    "item_id": "MLB1",
                },
            ],
        },
    ],
)
def test_mercadolibre_traditional_create_rejects_unverified_raw_response(
    response: dict,
) -> None:
    payload = _traditional_global_items_payload()
    payload["_publication"] = {
        "model": "traditional_global_items",
        "account_user_id": "3344094721",
        "markets": [
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "seller_id": "3345546432",
            }
        ],
    }

    with patch.object(
        marketplace_publishing,
        "request_json",
        return_value=response,
    ) as request:
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    request.assert_called_once()
    assert result["ok"] is False
    assert result["error_code"] == "MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID"
    assert "publication" not in result


def test_mercadolibre_traditional_update_only_puts_global_item_without_fallback() -> None:
    payload = _traditional_global_items_payload()
    payload["_publication"] = {
        "model": "traditional_global_items",
        "account_user_id": "3344094721",
        "parent_item_id": "CBT4232215884",
        "markets": [
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "seller_id": "3345546432",
                "item_id": "MLM5490706828",
            }
        ],
    }
    with patch.object(
        marketplace_publishing,
        "request_json",
        return_value={
            "site_items": [
                {"item_id": "MLM5490706828", "success": True}
            ]
        },
    ) as request:
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    request.assert_called_once()
    assert request.call_args.args[:3] == (
        "PUT",
        "https://api.mercadolibre.com/global/items/CBT4232215884",
        "token",
    )
    assert request.call_args.kwargs == {}
    assert result["ok"] is True
    assert result["operation"] == "updated"
    assert result["publication"]["parent_item_id"] == "CBT4232215884"


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"success": True},
        {"errors": [{"message": "update rejected"}]},
        {"item_id": "CBT-WRONG"},
        {"site_items": [{"item_id": "MLM-UNKNOWN", "success": True}]},
    ],
)
def test_mercadolibre_traditional_update_does_not_inject_success_identity(
    response: dict,
) -> None:
    payload = _traditional_global_items_payload()
    payload["_publication"] = {
        "model": "traditional_global_items",
        "account_user_id": "3344094721",
        "parent_item_id": "CBT4232215884",
        "markets": [
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "seller_id": "3345546432",
                "item_id": "MLM5490706828",
            }
        ],
    }

    with patch.object(
        marketplace_publishing,
        "request_json",
        return_value=response,
    ) as request:
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    request.assert_called_once()
    assert result["ok"] is False
    assert result["error_code"] == "MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID"
    assert "publication" not in result


def test_mercadolibre_payload_preserves_explicit_market_sales_conditions() -> None:
    config = _user_products_config(
        sites_to_sell=[
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "price": "21.50",
                "listing_type_id": "gold_pro",
                "free_shipping": False,
                "sale_terms": [{"id": "WARRANTY_TYPE", "value_id": "1"}],
            },
            {
                "site_id": "MLB",
                "logistic_type": "remote",
                "price": "22.00",
                "listing_type_id": "gold_special",
                "free_shipping": True,
                "sale_terms": [],
            },
        ],
        bindings=[
            {
                "seller_id": "991",
                "site_id": site_id,
                "logistic_type": "remote",
                "business_model": "",
                "pricing_model": "price",
                "user_product": True,
            }
            for site_id in ("MLM", "MLB")
        ],
    )

    payload = marketplace_publish.build_mercadolibre_payload(
        {
            "name": "Test product",
            "category_id": "CBT123",
            "dimensions": "20 x 15 x 10 cm",
            "weight_kg": "0.5",
        },
        {"platforms": {"mercadolibre": {"listing": {}}}},
        config,
        ["ml-id:123-CBT456"],
        category_attributes=_compiled_attributes("user_products"),
    )

    assert payload["sites_to_sell"] == [
        {
            "site_id": "MLB",
            "logistic_type": "remote",
            "price": 22.0,
            "listing_type_id": "gold_special",
            "free_shipping": True,
            "sale_terms": [],
        },
        {
            "site_id": "MLM",
            "logistic_type": "remote",
            "price": 21.5,
            "listing_type_id": "gold_pro",
            "free_shipping": False,
            "sale_terms": [{"id": "WARRANTY_TYPE", "value_id": "1"}],
        },
    ]
    assert payload["price"] == 18
    assert "global_net_proceeds" not in payload


def test_mercadolibre_payload_uses_one_net_proceeds_pricing_mode() -> None:
    config = _user_products_config(
        sites_to_sell=[
            {
                "site_id": "MLM",
                "logistic_type": "remote",
                "net_proceeds": "14.25",
            },
            {
                "site_id": "MLB",
                "logistic_type": "remote",
                "net_proceeds": "15.50",
            },
        ],
        bindings=[
            {
                "seller_id": "991",
                "site_id": site_id,
                "logistic_type": "remote",
                "business_model": "standard",
                "pricing_model": "net_proceeds",
                "user_product": True,
            }
            for site_id in ("MLM", "MLB")
        ],
    )

    payload = marketplace_publish.build_mercadolibre_payload(
        {
            "name": "Test product",
            "category_id": "CBT123",
            "dimensions": "20 x 15 x 10 cm",
            "weight_kg": "0.5",
        },
        {"platforms": {"mercadolibre": {"listing": {}}}},
        config,
        ["ml-id:123-CBT456"],
        category_attributes=_compiled_attributes("user_products"),
    )

    assert "price" not in payload
    assert payload["global_net_proceeds"] == 15.5
    assert payload["sites_to_sell"] == [
        {
            "site_id": "MLB",
            "logistic_type": "remote",
            "net_proceeds": 15.5,
            "listing_type_id": "gold_special",
        },
        {
            "site_id": "MLM",
            "logistic_type": "remote",
            "net_proceeds": 14.25,
            "listing_type_id": "gold_special",
        },
    ]


@pytest.mark.parametrize(
    ("sites_to_sell", "bindings", "error_code"),
    [
        (
            [],
            [{"site_id": "MLM", "logistic_type": "remote", "user_product": True}],
            "MERCADOLIBRE_SITES_TO_SELL_REQUIRED",
        ),
        (
            [{"site_id": "CBT", "logistic_type": "remote"}],
            [{"site_id": "CBT", "logistic_type": "remote", "user_product": True}],
            "MERCADOLIBRE_SALES_TARGET_CBT_INVALID",
        ),
        (
            [{"site_id": "MLM", "logistic_type": "drop_off"}],
            [{"site_id": "MLM", "logistic_type": "remote", "user_product": True}],
            "MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED",
        ),
        (
            [{"site_id": "MLM", "logistic_type": "remote"}],
            [{"site_id": "MLM", "logistic_type": "remote", "user_product": False}],
            "MERCADOLIBRE_USER_PRODUCTS_REQUIRED",
        ),
        (
            [
                {"site_id": "MLM", "logistic_type": "remote"},
                {"site_id": "MLM", "logistic_type": "drop_off"},
            ],
            [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "pricing_model": "price",
                    "user_product": True,
                }
            ],
            "MERCADOLIBRE_MARKET_OPERATION_AMBIGUOUS",
        ),
        (
            [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "price": "20",
                    "net_proceeds": "15",
                }
            ],
            [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "pricing_model": "price",
                    "user_product": True,
                }
            ],
            "MERCADOLIBRE_PRICE_NET_PROCEEDS_CONFLICT",
        ),
        (
            [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "net_proceeds": "15",
                }
            ],
            [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "pricing_model": "price",
                    "user_product": True,
                }
            ],
            "MERCADOLIBRE_PRICING_MODEL_MISMATCH",
        ),
        (
            [{"site_id": "MLM", "logistic_type": "remote"}],
            [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "pricing_model": "net_proceeds",
                    "user_product": True,
                }
            ],
            "MERCADOLIBRE_PRICING_AMOUNT_REQUIRED",
        ),
    ],
)
def test_mercadolibre_user_products_payload_rejects_invalid_sales_targets(
    sites_to_sell: list[dict[str, str]],
    bindings: list[dict[str, object]],
    error_code: str,
) -> None:
    with pytest.raises(RuntimeError, match=error_code):
        marketplace_publish.build_mercadolibre_payload(
            {"name": "Test", "category_id": "CBT123"},
            {"platforms": {"mercadolibre": {"listing": {}}}},
            _user_products_config(
                sites_to_sell=sites_to_sell,
                bindings=bindings,
            ),
            ["ml-id:123-CBT456"],
        )


def test_mercadolibre_raw_payload_rejects_cross_level_pricing_mode() -> None:
    payload = _user_products_payload()
    payload["sites_to_sell"][0].pop("price")
    payload["sites_to_sell"][0]["net_proceeds"] = "15"

    with patch(
        "erp_web.marketplaces.publishing.request_json",
    ) as request, pytest.raises(
        RuntimeError,
        match="MERCADOLIBRE_PRICING_MODEL_MISMATCH",
    ):
        marketplace_publish.publish_mercadolibre(payload, "token")

    request.assert_not_called()


def test_mercadolibre_raw_payload_rejects_market_price_net_proceeds_conflict() -> None:
    payload = _user_products_payload()
    payload["sites_to_sell"][0]["net_proceeds"] = "15"

    with patch(
        "erp_web.marketplaces.publishing.request_json",
    ) as request, pytest.raises(
        RuntimeError,
        match="MERCADOLIBRE_PRICE_NET_PROCEEDS_CONFLICT",
    ):
        marketplace_publish.publish_mercadolibre(payload, "token")

    request.assert_not_called()


def test_mercadolibre_raw_payload_rejects_duplicate_market_operations() -> None:
    payload = _user_products_payload()
    payload["sites_to_sell"].append(
        {
            "site_id": "MLM",
            "logistic_type": "drop_off",
            "price": 19,
            "listing_type_id": "gold_special",
        }
    )

    with patch(
        "erp_web.marketplaces.publishing.request_json",
    ) as request, pytest.raises(
        RuntimeError,
        match="MERCADOLIBRE_MARKET_OPERATION_AMBIGUOUS",
    ):
        marketplace_publish.publish_mercadolibre(payload, "token")

    request.assert_not_called()


@pytest.mark.parametrize(
    ("binding_pricing_model", "payload_pricing_model", "expected_message"),
    [
        (
            "price",
            "net_proceeds",
            "pricing_model=price",
        ),
        (
            "net_proceeds",
            "price",
            "pricing_model=net_proceeds",
        ),
    ],
)
def test_mercadolibre_approved_payload_revalidates_binding_pricing_model(
    binding_pricing_model: str,
    payload_pricing_model: str,
    expected_message: str,
) -> None:
    config = _user_products_config(
        bindings=[
            {
                "seller_id": "991",
                "site_id": "MLM",
                "logistic_type": "remote",
                "business_model": "standard",
                "pricing_model": binding_pricing_model,
                "user_product": True,
            }
        ]
    )
    config["mercadolibre"].update(
        {
            "access_token": "token",
            "user_id": "991",
        }
    )
    payload = _user_products_payload()
    if payload_pricing_model == "net_proceeds":
        payload.pop("price")
        payload["global_net_proceeds"] = "15"
        payload["sites_to_sell"][0].pop("price")
        payload["sites_to_sell"][0]["net_proceeds"] = "15"

    errors = validate_mercadolibre_publish_payload(payload, config)

    assert any(expected_message in error for error in errors)


def test_mercadolibre_approved_payload_allows_inherited_global_net_proceeds() -> None:
    config = _user_products_config(
        bindings=[
            {
                "seller_id": "991",
                "site_id": "MLM",
                "logistic_type": "remote",
                "business_model": "standard",
                "pricing_model": "net_proceeds",
                "user_product": True,
            }
        ]
    )
    config["mercadolibre"].update(
        {
            "access_token": "token",
            "user_id": "991",
        }
    )
    payload = _user_products_payload()
    payload.pop("price")
    payload["global_net_proceeds"] = "15"
    payload["sites_to_sell"][0].pop("price")

    errors = validate_mercadolibre_publish_payload(payload, config)

    assert errors == []


def test_mercadolibre_payload_requires_user_products_parent_account() -> None:
    config = _user_products_config()
    config["mercadolibre"]["account_site_id"] = "MLM"
    with pytest.raises(RuntimeError, match="MERCADOLIBRE_CBT_ACCOUNT_REQUIRED"):
        marketplace_publish.build_mercadolibre_payload(
            {"name": "Test", "category_id": "CBT123"},
            {"platforms": {"mercadolibre": {"listing": {}}}},
            config,
            ["ml-id:123-CBT456"],
        )

    config = _user_products_config()
    config["mercadolibre"]["user_product_seller"] = False
    with pytest.raises(RuntimeError, match="MERCADOLIBRE_USER_PRODUCTS_REQUIRED"):
        marketplace_publish.build_mercadolibre_payload(
            {"name": "Test", "category_id": "CBT123"},
            {"platforms": {"mercadolibre": {"listing": {}}}},
            config,
            ["ml-id:123-CBT456"],
        )


def test_mercadolibre_payload_rejects_local_target_and_non_cbt_category() -> None:
    local_config = _user_products_config()
    local_config["mercadolibre"]["site_id"] = "MLM"
    with pytest.raises(RuntimeError, match="MERCADOLIBRE_CBT_ACCOUNT_REQUIRED"):
        marketplace_publish.build_mercadolibre_payload(
            {"name": "Test", "category_id": "MLM123"},
            {"platforms": {"mercadolibre": {"listing": {}}}},
            local_config,
            ["ml-id:123-CBT456"],
        )

    non_cbt_config = _user_products_config()
    non_cbt_config["mercadolibre"]["category_id"] = "MLM123"
    with pytest.raises(RuntimeError, match="CBT 发布必须使用真实 CBT 类目 ID"):
        marketplace_publish.build_mercadolibre_payload(
            {"name": "Test", "category_id": "CBT-SOURCE"},
            {"platforms": {"mercadolibre": {"listing": {}}}},
            non_cbt_config,
            ["ml-id:123-CBT456"],
            category_attributes=[],
        )


def test_mercadolibre_payload_rejects_non_usd_currency() -> None:
    with pytest.raises(RuntimeError, match="MERCADOLIBRE_CBT_CURRENCY_INVALID"):
        marketplace_publish.build_mercadolibre_payload(
            {"name": "Test", "category_id": "CBT123"},
            {"platforms": {"mercadolibre": {"listing": {}}}},
            _user_products_config(currency_id="MXN"),
            ["ml-id:123-CBT456"],
        )


def test_mercadolibre_initial_publish_posts_one_family_array() -> None:
    payload = _user_products_payload()
    response = [
        {
            "item_id": "CBT100",
            "siteless_user_product_id": "UP100",
            "siteless_family_id": "FAMILY100",
            "site_items": [
                {
                    "site_id": "MLM",
                    "item_id": "MLM100",
                    "user_product_id": "UP-MLM100",
                }
            ],
        }
    ]
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value=response,
    ) as request:
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    wire_payload = dict(payload)
    wire_payload.pop("_listing_model")
    request.assert_called_once_with(
        "POST",
        "https://api.mercadolibre.com/global/user-products/families",
        "token",
        [wire_payload],
    )
    assert result["operation"] == "created"
    assert result["siteless_user_product_id"] == "UP100"
    assert result["publication"]["parent_item_id"] == "CBT100"
    market = result["publication"]["markets"][0]
    assert {
        "site_id": market["site_id"],
        "logistic_type": market["logistic_type"],
        "item_id": market["item_id"],
        "user_product_id": market["user_product_id"],
        "price": market["price"],
        "listing_type_id": market["listing_type_id"],
    } == {
        "site_id": "MLM",
        "logistic_type": "remote",
        "item_id": "MLM100",
        "user_product_id": "UP-MLM100",
        "price": 18,
        "listing_type_id": "gold_special",
    }


def test_mercadolibre_existing_publication_updates_then_adds_new_market() -> None:
    base_payload = _user_products_payload()
    confirmed_payload = {
        key: value
        for key, value in base_payload.items()
        if key not in {"sites_to_sell", "category_id", "currency_id"}
    }
    confirmed_payload["family_name"] = "Old family name"
    payload = {
        **base_payload,
        "_publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP100",
            "siteless_family_id": "FAMILY100",
            "parent_item_id": "CBT100",
            "family_name": "Old family name",
            "confirmed_payload": confirmed_payload,
            "markets": [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "item_id": "MLM100",
                    "user_product_id": "UP-MLM100",
                }
            ],
        },
    }
    payload["sites_to_sell"] = [
        {
            "site_id": "MLM",
            "logistic_type": "remote",
            "price": 18,
            "listing_type_id": "gold_special",
        },
        {
            "site_id": "MLB",
            "logistic_type": "remote",
            "price": 18,
            "listing_type_id": "gold_special",
        },
    ]

    with patch(
        "erp_web.marketplaces.publishing.request_json",
        side_effect=[
            {
                "siteless_user_product_id": "UP100",
                "site_items": [
                    {
                        "site_id": "MLB",
                        "item_id": "MLB200",
                        "user_product_id": "UP-MLB200",
                    }
                ],
            },
            {
                "siteless_user_product_id": "UP100",
                "listing_sites": [
                    {
                        "site_id": "MLM",
                        "listing_id": "MLM100",
                        "user_product_id": "UP-MLM100",
                    },
                    {
                        "site_id": "MLB",
                        "listing_id": "MLB200",
                        "user_product_id": "UP-MLB200",
                    },
                ],
            },
        ],
    ) as request:
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    assert request.call_count == 2
    add_call, update_call = request.call_args_list
    assert update_call.args[:3] == (
        "PUT",
        "https://api.mercadolibre.com/global/user-products/UP100",
        "token",
    )
    assert update_call.args[3] == {
        "family_name": "Test product",
        "listing_sites": [
        {
            "listing_id": "MLM100",
            "price": 18,
            "listing_type_id": "gold_special",
        },
        ],
    }
    assert {"sites_to_sell", "category_id", "currency_id"}.isdisjoint(
        update_call.args[3]
    )
    assert add_call.args == (
        "POST",
        "https://api.mercadolibre.com/global/user-products/UP100",
        "token",
        {
            "sites_to_sell": [
                {
                    "site_id": "MLB",
                    "logistic_type": "remote",
                    "price": 18,
                    "listing_type_id": "gold_special",
                }
            ]
        },
    )
    assert result["operation"] == "marketplaces_added"
    assert result["added_sites"] == ["MLB"]
    assert {item["site_id"] for item in result["publication"]["markets"]} == {
        "MLB",
        "MLM",
    }


def test_mercadolibre_existing_user_product_projects_sale_terms_to_listing_sites() -> None:
    base_payload = _user_products_payload()
    previous_sale_terms = [
        {
            "id": "WARRANTY_TYPE",
            "value_id": "2230279",
            "value_name": "Factory warranty",
        }
    ]
    root_sale_terms = list(base_payload["sale_terms"])
    target_sale_terms = [
        {
            "id": "WARRANTY_TYPE",
            "value_id": "2230280",
            "value_name": "Seller warranty",
        },
        {
            "id": "WARRANTY_TIME",
            "value_name": "12 months",
            "value_struct": {"number": 12, "unit": "months"},
        },
    ]
    confirmed_payload = {
        key: value
        for key, value in base_payload.items()
        if key not in {"sites_to_sell", "category_id", "currency_id"}
    }
    confirmed_payload["sale_terms"] = previous_sale_terms
    payload = {
        **base_payload,
        "_publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP100",
            "family_name": base_payload["family_name"],
            "confirmed_payload": confirmed_payload,
            "markets": [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "item_id": "MLM100",
                    "user_product_id": "MLMU100",
                    "price": 18,
                    "listing_type_id": "gold_special",
                },
                {
                    "site_id": "MLB",
                    "logistic_type": "remote",
                    "item_id": "MLB100",
                    "user_product_id": "MLBU100",
                    "price": 18,
                    "listing_type_id": "gold_special",
                },
            ],
        },
    }
    payload["sites_to_sell"] = [
        {
            "site_id": "MLM",
            "logistic_type": "remote",
            "price": 18,
            "listing_type_id": "gold_special",
            "sale_terms": target_sale_terms,
        },
        {
            "site_id": "MLB",
            "logistic_type": "remote",
            "price": 18,
            "listing_type_id": "gold_special",
        },
    ]

    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={
            "id": "UP100",
            "success": True,
            "listing_sites": [
                {"id": "MLM100", "success": True},
                {"id": "MLB100", "success": True},
            ],
        },
    ) as request:
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    update_payload = request.call_args.args[3]
    assert "sale_terms" not in update_payload
    assert update_payload == {
        "listing_sites": [
            {
                "listing_id": "MLM100",
                "sale_terms": [
                    {
                        "id": "WARRANTY_TYPE",
                        "values": [
                            {"id": "2230280", "name": "Seller warranty"}
                        ],
                    },
                    {
                        "id": "WARRANTY_TIME",
                        "values": [{"name": "12 months"}],
                    },
                ],
            },
            {
                "listing_id": "MLB100",
                "sale_terms": [
                    {
                        "id": "WARRANTY_TYPE",
                        "values": [
                            {"id": "6150835", "name": "No warranty"}
                        ],
                    }
                ],
            },
        ]
    }
    markets = {
        item["site_id"]: item for item in result["publication"]["markets"]
    }
    assert markets["MLM"]["sale_terms"] == target_sale_terms
    assert markets["MLB"]["sale_terms"] == root_sale_terms

    unchanged_payload = {
        **base_payload,
        "sites_to_sell": deepcopy(payload["sites_to_sell"]),
        "_publication": result["publication"],
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json"
    ) as unchanged_request:
        marketplace_publish.publish_mercadolibre(unchanged_payload, "token")
    unchanged_request.assert_not_called()


def test_mercadolibre_existing_user_product_uses_update_attribute_values() -> None:
    base_payload = _user_products_payload()
    confirmed_payload = {
        key: deepcopy(value)
        for key, value in base_payload.items()
        if key not in {"sites_to_sell", "category_id", "currency_id"}
    }
    next(
        item
        for item in confirmed_payload["attributes"]
        if item["id"] == "MODEL"
    )["value_name"] = "Old model"
    payload = {
        **base_payload,
        "_publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP100",
            "family_name": base_payload["family_name"],
            "confirmed_payload": confirmed_payload,
            "markets": [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "item_id": "MLM100",
                    "user_product_id": "MLMU100",
                    "price": 18,
                    "listing_type_id": "gold_special",
                }
            ],
        },
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={"id": "UP100", "success": True},
    ) as request:
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    update_payload = request.call_args.args[3]
    attributes = {
        item["id"]: item["values"] for item in update_payload["attributes"]
    }
    assert attributes["PACKAGE_LENGTH"] == [{"name": "20 cm"}]
    assert attributes["BRAND"] == [{"name": "Generic"}]
    assert attributes["ITEM_CONDITION"] == [
        {"id": "2230284", "name": "New"}
    ]
    assert result["publication"]["confirmed_payload"]["attributes"] == (
        update_payload["attributes"]
    )

    unchanged_payload = {
        **base_payload,
        "_publication": result["publication"],
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json"
    ) as unchanged_request:
        unchanged = marketplace_publish.publish_mercadolibre(
            unchanged_payload,
            "token",
        )

    unchanged_request.assert_not_called()
    assert unchanged["ok"] is True


def test_mercadolibre_existing_user_product_projects_description_to_sites() -> None:
    base_payload = _user_products_payload()
    confirmed_payload = {
        key: deepcopy(value)
        for key, value in base_payload.items()
        if key not in {"sites_to_sell", "category_id", "currency_id"}
    }
    confirmed_payload["description"] = {"plain_text": "Old description"}
    payload = {
        **base_payload,
        "_publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP100",
            "family_name": base_payload["family_name"],
            "confirmed_payload": confirmed_payload,
            "markets": [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "item_id": "MLM100",
                    "user_product_id": "MLMU100",
                    "price": 18,
                    "listing_type_id": "gold_special",
                }
            ],
        },
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={
            "id": "UP100",
            "success": True,
            "listing_sites": [{"id": "MLM100", "success": True}],
        },
    ) as request:
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    update_payload = request.call_args.args[3]
    assert "description" not in update_payload
    assert update_payload["listing_sites"] == [
        {
            "listing_id": "MLM100",
            "description": {"plain_text": "Description"},
        }
    ]
    assert result["publication"]["markets"][0]["description"] == {
        "plain_text": "Description"
    }

    unchanged_payload = {
        **base_payload,
        "_publication": result["publication"],
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json"
    ) as unchanged_request:
        marketplace_publish.publish_mercadolibre(unchanged_payload, "token")
    unchanged_request.assert_not_called()


def test_mercadolibre_async_listing_updates_apply_only_succeeded_sites() -> None:
    base_payload = _user_products_payload()
    old_sale_terms = [
        {
            "id": "WARRANTY_TYPE",
            "value_id": "2230279",
            "value_name": "Factory warranty",
        }
    ]
    confirmed_payload = {
        key: deepcopy(value)
        for key, value in base_payload.items()
        if key not in {"sites_to_sell", "category_id", "currency_id"}
    }
    confirmed_payload["description"] = {"plain_text": "Old description"}
    confirmed_payload["sale_terms"] = deepcopy(old_sale_terms)
    markets = [
        {
            "site_id": site_id,
            "logistic_type": "remote",
            "item_id": f"{site_id}100",
            "user_product_id": f"{site_id}U100",
            "status": "active",
            "price": 12,
            "listing_type_id": "gold_pro",
            "description": {"plain_text": "Old description"},
            "sale_terms": deepcopy(old_sale_terms),
        }
        for site_id in ("MLM", "MLB")
    ]
    payload = {
        **base_payload,
        "sites_to_sell": [
            {
                "site_id": site_id,
                "logistic_type": "remote",
                "price": 18,
                "listing_type_id": "gold_special",
            }
            for site_id in ("MLM", "MLB")
        ],
        "_publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP100",
            "family_name": base_payload["family_name"],
            "confirmed_payload": confirmed_payload,
            "markets": markets,
        },
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={
            "id": "UP100",
            "listing_sites": [
                {"id": "MLM100", "task_id": "task-mlm"},
                {"id": "MLB100", "task_id": "task-mlb"},
            ],
        },
    ):
        pending = marketplace_publish.publish_mercadolibre(payload, "token")

    pending_markets = {
        item["site_id"]: item for item in pending["publication"]["markets"]
    }
    for market in pending_markets.values():
        assert market["price"] == 12
        assert market["listing_type_id"] == "gold_pro"
        assert market["description"] == {"plain_text": "Old description"}
        assert market["sale_terms"] == old_sale_terms
        assert market["last_operation"]["status"] == "pending"
    pending_updates = pending["continuation"]["pending_listing_updates"]
    assert {item["task_id"] for item in pending_updates} == {
        "task-mlm",
        "task-mlb",
    }
    assert all(
        item["sale_terms"]
        == [
            {
                "id": "WARRANTY_TYPE",
                "values": [
                    {"id": "6150835", "name": "No warranty"}
                ],
            }
        ]
        for item in pending_updates
    )

    with patch(
        "erp_web.marketplaces.publishing.request_json",
        side_effect=[
            {
                "task_id": "task-mlm",
                "status": "finished",
                "user_products": [
                    {"id": "MLMU100", "status": "succeeded"}
                ],
            },
            {
                "task_id": "task-mlb",
                "status": "finished",
                "user_products": [
                    {
                        "id": "MLBU100",
                        "status": "failed",
                        "reasons": [{"code": "SITE_REJECTED"}],
                    }
                ],
            },
        ],
    ):
        result = marketplace_publishing.poll_mercadolibre_publish_status(
            pending,
            "token",
        )

    result_markets = {
        item["site_id"]: item for item in result["publication"]["markets"]
    }
    succeeded = result_markets["MLM"]
    assert succeeded["price"] == 18
    assert succeeded["listing_type_id"] == "gold_special"
    assert succeeded["description"] == {"plain_text": "Description"}
    assert succeeded["sale_terms"] == [
        {
            "id": "WARRANTY_TYPE",
            "values": [{"id": "6150835", "name": "No warranty"}],
        }
    ]
    assert succeeded["last_operation"]["status"] == "succeeded"
    failed = result_markets["MLB"]
    assert failed["price"] == 12
    assert failed["listing_type_id"] == "gold_pro"
    assert failed["description"] == {"plain_text": "Old description"}
    assert failed["sale_terms"] == old_sale_terms
    assert failed["last_operation"]["status"] == "failed"
    assert result["status"] == "partial"


def test_mercadolibre_mixed_sync_and_async_site_response_delays_only_task_site() -> None:
    base_payload = _user_products_payload()
    confirmed_payload = {
        key: deepcopy(value)
        for key, value in base_payload.items()
        if key not in {"sites_to_sell", "category_id", "currency_id"}
    }
    payload = {
        **base_payload,
        "sites_to_sell": [
            {
                "site_id": site_id,
                "logistic_type": "remote",
                "price": 18,
                "listing_type_id": "gold_special",
            }
            for site_id in ("MLM", "MLB")
        ],
        "_publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP100",
            "family_name": base_payload["family_name"],
            "confirmed_payload": confirmed_payload,
            "markets": [
                {
                    "site_id": site_id,
                    "logistic_type": "remote",
                    "item_id": f"{site_id}100",
                    "user_product_id": f"{site_id}U100",
                    "status": "active",
                    "price": 12,
                    "listing_type_id": "gold_special",
                }
                for site_id in ("MLM", "MLB")
            ],
        },
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={
            "id": "UP100",
            "listing_sites": [
                {"id": "MLM100", "task_id": "task-mlm"},
                {"id": "MLB100", "success": True},
            ],
        },
    ):
        pending = marketplace_publish.publish_mercadolibre(payload, "token")

    markets = {
        item["site_id"]: item for item in pending["publication"]["markets"]
    }
    assert markets["MLM"]["price"] == 12
    assert markets["MLM"]["last_operation"]["status"] == "pending"
    assert markets["MLB"]["price"] == 18
    assert markets["MLB"]["last_operation"]["status"] == "succeeded"
    assert pending["continuation"]["pending_listing_updates"] == [
        {
            "task_id": "task-mlm",
            "item_id": "MLM100",
            "user_product_id": "MLMU100",
            "site_id": "MLM",
            "seller_id": "",
            "logistic_type": "remote",
            "price": 18,
        }
    ]


def test_mercadolibre_only_adds_market_without_unrelated_put() -> None:
    base_payload = _user_products_payload()
    confirmed_payload = {
        key: value
        for key, value in base_payload.items()
        if key not in {"sites_to_sell", "category_id", "currency_id"}
    }
    payload = {
        **base_payload,
        "_publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP100",
            "family_name": base_payload["family_name"],
            "confirmed_payload": confirmed_payload,
            "markets": [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "item_id": "MLM100",
                    "user_product_id": "MLMU100",
                    "status": "active",
                    "price": 18,
                    "listing_type_id": "gold_special",
                }
            ],
        },
    }
    payload["sites_to_sell"].append(
        {
            "site_id": "MLB",
            "logistic_type": "remote",
            "price": 18,
            "listing_type_id": "gold_special",
        }
    )
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={
            "id": "UP100",
            "site_items": [
                {
                    "site_id": "MLB",
                    "item_id": "MLB200",
                    "user_product_id": "MLBU100",
                }
            ],
        },
    ) as request:
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    request.assert_called_once()
    assert request.call_args.args[0] == "POST"
    assert result["operation"] == "marketplaces_added"
    assert result["added_sites"] == ["MLB"]


def test_mercadolibre_async_finished_without_user_products_is_outcome_unknown() -> None:
    payload = {
        **_user_products_payload(),
        "_publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP100",
            "family_name": "Confirmed family",
            "markets": [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "item_id": "MLM100",
                    "user_product_id": "MLMU100",
                }
            ],
        },
    }
    payload["sites_to_sell"].append(
        {
            "site_id": "MLB",
            "logistic_type": "remote",
            "price": 18,
            "listing_type_id": "gold_special",
        }
    )
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        side_effect=[
            {
                "id": "UP100",
                "site_items": [
                    {
                        "site_id": "MLB",
                        "item_id": "MLB200",
                        "user_product_id": "MLBU100",
                    }
                ],
            },
            {"id": "UP100", "task_id": "task-empty"},
        ],
    ):
        pending = marketplace_publish.publish_mercadolibre(payload, "token")

    assert pending["publication"]["family_name"] == "Confirmed family"
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={
            "task_id": "task-empty",
            "status": "finished",
            "user_products": [],
        },
    ) as request:
        result = marketplace_publishing.poll_mercadolibre_publish_status(
            pending,
            "token",
        )

    assert request.call_count == 1
    assert result["status"] == "outcome_unknown"
    assert result["outcome_unknown"] is True
    assert result["publication"]["family_name"] == "Confirmed family"


def test_mercadolibre_async_mixed_processing_snapshot_stays_pending() -> None:
    pending = {
        "ok": True,
        "status": "pending_confirmation",
        "task_id": "task-mixed",
        "task_ids": ["task-mixed"],
        "publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP100",
            "markets": [
                {
                    "site_id": "MLM",
                    "item_id": "MLM100",
                    "user_product_id": "MLMU100",
                }
            ],
        },
        "continuation": {
            "siteless_user_product_id": "UP100",
            "family_name": "Test product",
            "requested_sites": [],
            "additions": [],
        },
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={
            "task_id": "task-mixed",
            "status": "processing",
            "user_products": [
                {"id": "MLMU100", "status": "failed", "reasons": ["x"]},
                {"id": "MLBU100", "status": "pending"},
            ],
        },
    ):
        result = marketplace_publishing.poll_mercadolibre_publish_status(
            pending,
            "token",
        )

    assert result["status"] == "pending_confirmation"
    assert result["confirmation_poll_count"] == 1


def test_mercadolibre_async_finished_failed_is_confirmed_failure() -> None:
    pending = {
        "ok": True,
        "status": "pending_confirmation",
        "task_id": "task-failed",
        "task_ids": ["task-failed"],
        "publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP100",
            "family_name": "Confirmed family",
            "markets": [
                {
                    "site_id": "MLM",
                    "item_id": "MLM100",
                    "user_product_id": "MLMU100",
                    "status": "active",
                    "price": 12,
                    "listing_type_id": "gold_pro",
                    "free_shipping": True,
                    "sale_terms": [{"id": "WARRANTY_TYPE", "value_id": "1"}],
                }
            ],
        },
        "continuation": {
            "siteless_user_product_id": "UP100",
            "family_name": "Desired family",
            "confirmed_family_name": "Confirmed family",
            "requested_sites": [],
            "additions": [],
        },
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={
            "task_id": "task-failed",
            "status": "finished",
            "user_products": [
                {
                    "id": "MLMU100",
                    "status": "failed",
                    "reasons": [{"code": "INVALID_FAMILY"}],
                }
            ],
        },
    ):
        result = marketplace_publishing.poll_mercadolibre_publish_status(
            pending,
            "token",
        )

    assert result["status"] == "partial"
    assert result.get("outcome_unknown") is not True
    assert result["publication"]["family_name"] == "Confirmed family"
    market = result["publication"]["markets"][0]
    assert market["status"] == "active"
    assert {
        key: market[key]
        for key in (
            "price",
            "listing_type_id",
            "free_shipping",
            "sale_terms",
        )
    } == {
        "price": 12,
        "listing_type_id": "gold_pro",
        "free_shipping": True,
        "sale_terms": [{"id": "WARRANTY_TYPE", "value_id": "1"}],
    }
    assert market["last_operation"]["status"] == "failed"
    assert market["error"] == [{"code": "INVALID_FAMILY"}]


def test_mercadolibre_async_ignores_failed_sibling_user_product() -> None:
    pending = {
        "task_id": "task-family",
        "task_ids": ["task-family"],
        "publication": {
            "model": "user_products",
            "siteless_user_product_id": "U100",
            "family_name": "Confirmed family",
            "confirmed_payload": {"family_name": "Confirmed family"},
            "markets": [
                {
                    "site_id": "MLM",
                    "item_id": "MLM100",
                    "user_product_id": "MLMU100",
                    "status": "active",
                }
            ],
        },
        "continuation": {
            "siteless_user_product_id": "U100",
            "family_name": "Desired family",
            "confirmed_family_name": "Confirmed family",
            "pending_confirmed_fields": {"family_name": "Desired family"},
            "requested_sites": [],
            "additions": [],
        },
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={
            "task_id": "task-family",
            "status": "finished",
            "user_products": [
                {"id": "MLMU100", "status": "succeeded"},
                {
                    "id": "MLMU999",
                    "status": "failed",
                    "reasons": [{"code": "SIBLING_FAILED"}],
                },
            ],
        },
    ):
        result = marketplace_publishing.poll_mercadolibre_publish_status(
            pending,
            "token",
        )

    assert result["status"] == "published"
    assert result["publication"]["family_name"] == "Desired family"
    assert result["publication"]["confirmed_payload"]["family_name"] == (
        "Desired family"
    )
    market = result["publication"]["markets"][0]
    assert market["status"] == "active"
    assert "error" not in market


def test_mercadolibre_async_only_sibling_result_is_outcome_unknown() -> None:
    pending = {
        "task_id": "task-sibling-only",
        "task_ids": ["task-sibling-only"],
        "publication": {
            "model": "user_products",
            "siteless_user_product_id": "U100",
            "family_name": "Confirmed family",
            "markets": [
                {
                    "site_id": "MLM",
                    "user_product_id": "MLMU100",
                    "status": "active",
                }
            ],
        },
        "continuation": {
            "siteless_user_product_id": "U100",
            "family_name": "Desired family",
            "confirmed_family_name": "Confirmed family",
            "requested_sites": [],
            "additions": [],
        },
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={
            "task_id": "task-sibling-only",
            "status": "finished",
            "user_products": [
                {"id": "MLMU999", "status": "succeeded"},
            ],
        },
    ):
        result = marketplace_publishing.poll_mercadolibre_publish_status(
            pending,
            "token",
        )

    assert result["status"] == "outcome_unknown"
    assert result["publication"]["family_name"] == "Confirmed family"
    assert result["publication"]["markets"][0]["status"] == "active"


def test_mercadolibre_async_matches_equivalent_siteless_id_at_root_only() -> None:
    pending = {
        "task_id": "task-root",
        "task_ids": ["task-root"],
        "publication": {
            "model": "user_products",
            "siteless_user_product_id": "U100",
            "family_name": "Confirmed family",
            "markets": [
                {
                    "site_id": "MLM",
                    "user_product_id": "MLMU100",
                    "status": "active",
                    "price": 18,
                }
            ],
        },
        "continuation": {
            "siteless_user_product_id": "U100",
            "family_name": "Desired family",
            "confirmed_family_name": "Confirmed family",
            "requested_sites": [],
            "additions": [],
        },
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={
            "task_id": "task-root",
            "status": "finished",
            "user_products": [
                {
                    "id": "CBTU100",
                    "status": "failed",
                    "reasons": [{"code": "ROOT_FAILED"}],
                }
            ],
        },
    ):
        result = marketplace_publishing.poll_mercadolibre_publish_status(
            pending,
            "token",
        )

    assert result["status"] == "partial"
    assert result["publication"]["last_operation"]["status"] == "failed"
    assert result["publication"]["markets"] == [
        {
            "site_id": "MLM",
            "user_product_id": "MLMU100",
            "status": "active",
            "price": 18,
        }
    ]


def test_mercadolibre_rejected_listing_update_preserves_confirmed_price() -> None:
    payload = {
        **_user_products_payload(),
        "_publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP100",
            "markets": [
                {
                    "site_id": "MLM",
                    "seller_id": "991",
                    "logistic_type": "remote",
                    "item_id": "MLM100",
                    "user_product_id": "MLMU100",
                    "price": 12,
                    "listing_type_id": "gold_pro",
                    "status": "active",
                }
            ],
        },
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={
            "id": "UP100",
            "success": True,
            "listing_sites": [
                {
                    "id": "MLM100",
                    "success": False,
                    "errors": [{"code": "PRICE_REJECTED"}],
                }
            ],
        },
    ):
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    market = result["publication"]["markets"][0]
    assert market["price"] == 12
    assert market["listing_type_id"] == "gold_pro"
    assert market["status"] == "active"
    assert market["error"] == [{"code": "PRICE_REJECTED"}]
    assert market["last_operation"]["status"] == "failed"


def test_mercadolibre_rejects_mutation_response_identity_drift() -> None:
    payload = {
        **_user_products_payload(),
        "_publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP100",
            "markets": [
                {
                    "site_id": "MLM",
                    "seller_id": "991",
                    "logistic_type": "remote",
                    "item_id": "MLM100",
                }
            ],
        },
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={"id": "UP999", "success": True},
    ), pytest.raises(PublishAdapterError) as raised:
        marketplace_publish.publish_mercadolibre(payload, "token")

    assert raised.value.code == "MERCADOLIBRE_RESPONSE_IDENTITY_MISMATCH"
    assert raised.value.details["outcome_unknown"] is True


def test_mercadolibre_rejects_market_seller_identity_drift() -> None:
    payload = {
        **_user_products_payload(),
        "_publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP100",
            "markets": [
                {
                    "site_id": "MLM",
                    "seller_id": "991",
                    "logistic_type": "remote",
                    "item_id": "MLM100",
                }
            ],
        },
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={
            "id": "UP100",
            "success": True,
            "listing_sites": [
                {
                    "id": "MLM100",
                    "site_id": "MLM",
                    "seller_id": "999",
                    "success": True,
                }
            ],
        },
    ), pytest.raises(PublishAdapterError) as raised:
        marketplace_publish.publish_mercadolibre(payload, "token")

    assert raised.value.code == "MERCADOLIBRE_MARKET_SELLER_MISMATCH"


def test_mercadolibre_create_requires_exactly_one_response_entry() -> None:
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value=[
            {"id": "UP100", "site_items": []},
            {"id": "UP200", "site_items": []},
        ],
    ), pytest.raises(PublishAdapterError) as raised:
        marketplace_publish.publish_mercadolibre(
            _user_products_payload(),
            "token",
        )

    assert raised.value.code == "MERCADOLIBRE_CREATE_CARDINALITY_MISMATCH"
    assert raised.value.details["outcome_unknown"] is True


def test_mercadolibre_create_rejects_duplicate_market_mapping() -> None:
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value=[
            {
                "id": "U100",
                "site_items": [
                    {
                        "site_id": "MLM",
                        "item_id": "MLM100",
                        "user_product_id": "MLMU100",
                    },
                    {
                        "site_id": "MLM",
                        "item_id": "MLM101",
                        "user_product_id": "MLMU101",
                    },
                ],
            }
        ],
    ), pytest.raises(PublishAdapterError) as raised:
        marketplace_publish.publish_mercadolibre(
            _user_products_payload(),
            "token",
        )

    assert raised.value.code == "MERCADOLIBRE_MARKET_RESPONSE_DUPLICATED"


def test_mercadolibre_accepts_equivalent_u_and_cbtu_siteless_ids() -> None:
    payload = {
        **_user_products_payload(),
        "_publication": {
            "model": "user_products",
            "siteless_user_product_id": "U100",
            "markets": [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "item_id": "MLM100",
                    "user_product_id": "MLMU100",
                }
            ],
        },
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={"id": "CBTU100", "success": True},
    ):
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    assert result["ok"] is True
    assert result["publication"]["siteless_user_product_id"] == "U100"


def test_mercadolibre_update_maps_listing_error_by_item_id_without_site() -> None:
    payload = {
        **_user_products_payload(),
        "_publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP100",
            "markets": [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "item_id": "MLM100",
                    "user_product_id": "MLMU100",
                    "status": "active",
                }
            ],
        },
    }
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={
            "id": "UP100",
            "success": True,
            "errors": None,
            "listing_sites": [
                {
                    "id": "MLM100",
                    "success": False,
                    "errors": [
                        {"code": 5014, "message": "sale_terms is not modifiable"}
                    ],
                }
            ],
        },
    ):
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    assert result["ok"] is False
    assert result["status"] == "partial"
    market = result["publication"]["markets"][0]
    assert market["site_id"] == "MLM"
    assert market["status"] == "active"
    assert market["error"][0]["code"] == 5014
    assert market["last_operation"]["status"] == "failed"


def test_mercadolibre_initial_publish_rejects_missing_market_mapping() -> None:
    payload = _user_products_payload()
    payload["sites_to_sell"].append(
        {
            "site_id": "MLB",
            "logistic_type": "remote",
            "price": 18,
            "listing_type_id": "gold_special",
        }
    )
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value=[
            {
                "item_id": "CBT100",
                "siteless_user_product_id": "UP100",
                "site_items": [
                    {
                        "site_id": "MLM",
                        "item_id": "MLM100",
                        "user_product_id": "MLMU100",
                    }
                ],
            }
        ],
    ), pytest.raises(PublishAdapterError) as raised:
        marketplace_publish.publish_mercadolibre(payload, "token")

    assert (
        raised.value.code
        == "MERCADOLIBRE_MARKET_RESPONSE_CARDINALITY_MISMATCH"
    )
    assert raised.value.details["outcome_unknown"] is True


def test_mercadolibre_async_update_polls_before_adding_marketplace() -> None:
    payload = {
        **_user_products_payload(),
        "_publication": {
            "model": "user_products",
            "siteless_user_product_id": "UP100",
            "markets": [
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "item_id": "MLM100",
                    "user_product_id": "MLMU100",
                }
            ],
        },
    }
    payload["sites_to_sell"].append(
        {
            "site_id": "MLB",
            "logistic_type": "remote",
            "price": 18,
            "listing_type_id": "gold_special",
        }
    )
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        side_effect=[
            {
                "id": "UP100",
                "site_items": [
                    {
                        "site_id": "MLB",
                        "item_id": "MLB200",
                        "user_product_id": "MLBU100",
                    }
                ],
            },
            {"id": "UP100", "task_id": "task-1"},
        ],
    ):
        pending = marketplace_publish.publish_mercadolibre(payload, "token")

    assert pending["status"] == "pending_confirmation"
    assert pending["task_id"] == "task-1"

    with patch(
        "erp_web.marketplaces.publishing.request_json",
        return_value={
            "task_id": "task-1",
            "status": "finished",
            "user_products": [
                {"id": "MLMU100", "status": "succeeded", "reasons": None}
            ],
        },
    ) as request:
        result = marketplace_publishing.poll_mercadolibre_publish_status(
            pending,
            "token",
        )

    assert request.call_args_list[0].args[:2] == (
        "GET",
        "https://api.mercadolibre.com/user-products-families/tasks/task-1",
    )
    assert request.call_count == 1
    assert result["ok"] is True
    assert result["operation"] == "marketplaces_added"
    assert result["added_sites"] == ["MLB"]
    assert result["task_ids"] == ["task-1"]
    assert result["task_results"][0]["status"] == "finished"
    assert result["confirmed_at"]


def test_mercadolibre_write_failure_is_never_auto_retryable() -> None:
    with patch(
        "erp_web.marketplaces.publishing.request_json",
        side_effect=PublishAdapterError(
            "MERCADOLIBRE_TIMEOUT",
            "POST failed: timeout",
            retryable=True,
        ),
    ), pytest.raises(PublishAdapterError) as raised:
        marketplace_publish.publish_mercadolibre(
            _user_products_payload(),
            "token",
        )

    assert raised.value.retryable is False
    assert raised.value.details["remote_write_dispatched"] is True
    assert raised.value.details["outcome_unknown"] is True


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
