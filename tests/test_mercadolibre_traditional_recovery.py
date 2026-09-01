from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import patch

import pytest

from erp_web import marketplaces as marketplace_publish
from erp_web.marketplaces import publishing as marketplace_publishing
from erp_web.marketplaces.publisher import PublishAdapterError


PARENT_ID = "CBT5113781132"
PARENT_SELLER_ID = "3344094721"
MARKET_SELLERS = {
    "MCO": "3344101349",
    "MLC": "3345546438",
    "MLU": "3345546428",
}


def _site(site_id: str) -> dict[str, Any]:
    return {
        "site_id": site_id,
        "logistic_type": "remote",
        "price": 80.79 if site_id == "MCO" else 77.55,
        "listing_type_id": "gold_special",
        "title": "Casa de madera para mascotas",
    }


def _market_seed(site_id: str) -> dict[str, Any]:
    return {
        "site_id": site_id,
        "logistic_type": "remote",
        "seller_id": MARKET_SELLERS[site_id],
    }


def _payload(
    *site_ids: str,
    category_id: str = "CBT100001",
    publication: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not site_ids:
        site_ids = ("MCO", "MLC")
    return {
        "_listing_model": "traditional_global_items",
        "_publication": publication
        if publication is not None
        else {
            "model": "traditional_global_items",
            "account_user_id": PARENT_SELLER_ID,
            "markets": [_market_seed(site_id) for site_id in site_ids],
        },
        "title": "Wooden pet house",
        "category_id": category_id,
        "currency_id": "USD",
        "available_quantity": 10,
        "buying_mode": "buy_it_now",
        "listing_type_id": "gold_special",
        "price": 80.79,
        "pictures": [{"id": "123-CBT456"}],
        "attributes": [
            {
                "id": "ITEM_CONDITION",
                "value_id": "2230284",
                "value_name": "New",
            },
            {"id": "SELLER_SKU", "value_name": "DOG-HOUSE-RECOVERY-1"},
        ],
        "sites_to_sell": [_site(site_id) for site_id in site_ids],
    }


def _successful_site_item(site_id: str, item_id: str) -> dict[str, Any]:
    return {
        "site_id": site_id,
        "logistic_type": "remote",
        "seller_id": MARKET_SELLERS[site_id],
        "item_id": item_id,
        "success": True,
    }


def _failed_site_item(
    site_id: str,
    *,
    code: str,
    message: str,
    status: int = 422,
) -> dict[str, Any]:
    return {
        "site_id": site_id,
        "logistic_type": "remote",
        "error": {
            "status": status,
            "error": "validation_error",
            "message": "Validation error",
            "cause": [{"code": code, "message": message}],
        },
    }


def _partial_publication() -> dict[str, Any]:
    original_payload = _payload("MCO", "MLC")
    confirmed_root = {
        key: deepcopy(value)
        for key, value in original_payload.items()
        if key not in {"_listing_model", "_publication", "sites_to_sell"}
    }
    mco = _market_seed("MCO")
    mco.update(
        {
            "status": "failed",
            "error": {
                "status": 422,
                "cause": [
                    {
                        "code": "item.shipping.mode.not_supported",
                        "message": "Remote shipping is not supported in Colombia",
                    }
                ],
            },
            "last_operation": {"status": "failed"},
        }
    )
    mlc = _market_seed("MLC")
    mlc.update(
        {
            "item_id": "MLC2205790991",
            "status": "active",
            "last_operation": {"status": "succeeded"},
        }
    )
    return {
        "model": "traditional_global_items",
        "account_user_id": PARENT_SELLER_ID,
        "parent_item_id": PARENT_ID,
        "status": "partial",
        "confirmed_payload": {
            **confirmed_root,
            "contract_version": 1,
            "sites_to_sell": [deepcopy(original_payload["sites_to_sell"][1])],
        },
        "markets": [mco, mlc],
    }


def test_first_publish_posts_one_complete_combined_global_item_body() -> None:
    payload = _payload("MCO", "MLC")
    expected_wire_payload = deepcopy(payload)
    expected_wire_payload.pop("_listing_model")
    expected_wire_payload.pop("_publication")
    response = {
        "item_id": PARENT_ID,
        "seller_id": PARENT_SELLER_ID,
        "site_items": [
            _successful_site_item("MCO", "MCO4395789764"),
            _successful_site_item("MLC", "MLC2205794515"),
        ],
    }

    with patch.object(
        marketplace_publishing,
        "request_json",
        return_value=response,
    ) as request:
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    request.assert_called_once_with(
        "POST",
        "https://api.mercadolibre.com/global/items",
        "token",
        expected_wire_payload,
        extra_headers={"parent-item-info": "true"},
    )
    assert result["ok"] is True
    assert result["publication"]["parent_item_id"] == PARENT_ID
    confirmed = result["publication"]["confirmed_payload"]
    assert confirmed["category_id"] == "CBT100001"
    assert confirmed["currency_id"] == "USD"
    assert confirmed["contract_version"] == 1
    assert {site["site_id"] for site in confirmed["sites_to_sell"]} == {
        "MCO",
        "MLC",
    }
    assert {
        market["site_id"]: market["item_id"]
        for market in result["publication"]["markets"]
    } == {
        "MCO": "MCO4395789764",
        "MLC": "MLC2205794515",
    }


def test_first_partial_publish_keeps_parent_success_and_failed_market() -> None:
    payload = _payload("MCO", "MLC")
    response = {
        "item_id": PARENT_ID,
        "seller_id": PARENT_SELLER_ID,
        "site_items": [
            _failed_site_item(
                "MCO",
                code="site.not_operable",
                message="Marketplace is temporarily unavailable",
                status=400,
            ),
            _successful_site_item("MLC", "MLC2205790991"),
        ],
    }

    with patch.object(
        marketplace_publishing,
        "request_json",
        return_value=response,
    ):
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    assert result["ok"] is False
    publication = result["publication"]
    assert publication["parent_item_id"] == PARENT_ID
    assert publication["status"] == "partial"
    confirmed = publication["confirmed_payload"]
    assert confirmed["category_id"] == "CBT100001"
    assert confirmed["currency_id"] == "USD"
    assert confirmed["contract_version"] == 1
    assert [site["site_id"] for site in confirmed["sites_to_sell"]] == ["MLC"]
    markets = {market["site_id"]: market for market in publication["markets"]}
    assert markets["MLC"]["item_id"] == "MLC2205790991"
    assert markets["MLC"]["last_operation"]["status"] == "succeeded"
    assert not markets["MCO"].get("item_id")
    assert markets["MCO"]["last_operation"]["status"] == "failed"
    assert markets["MCO"]["error"] == response["site_items"][0]["error"]


def test_existing_parent_posts_only_failed_or_missing_marketplaces() -> None:
    payload = _payload("MCO", "MLC", publication=_partial_publication())
    expected_mco = deepcopy(payload["sites_to_sell"][0])
    response = {
        "seller_id": PARENT_SELLER_ID,
        "site_items": [_successful_site_item("MCO", "MCO4395789764")],
    }

    with patch.object(
        marketplace_publishing,
        "request_json",
        return_value=response,
    ) as request:
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    request.assert_called_once_with(
        "POST",
        f"https://api.mercadolibre.com/global/items/{PARENT_ID}",
        "token",
        {"sites_to_sell": [expected_mco]},
    )
    assert result["ok"] is True
    assert result["operation"] == "marketplaces_added"
    markets = {market["site_id"]: market for market in result["publication"]["markets"]}
    assert markets["MCO"]["item_id"] == "MCO4395789764"
    assert markets["MCO"]["status"] == "active"
    assert markets["MCO"]["last_operation"]["status"] == "succeeded"
    assert "error" not in markets["MCO"]
    assert markets["MLC"]["item_id"] == "MLC2205790991"
    assert all(
        site["site_id"] != "MLC"
        for site in request.call_args.args[3]["sites_to_sell"]
    )
    assert {
        site["site_id"]
        for site in result["publication"]["confirmed_payload"]["sites_to_sell"]
    } == {"MCO", "MLC"}


def test_existing_item_id_is_never_readded_after_failed_update() -> None:
    publication = _partial_publication()
    mco = next(
        market
        for market in publication["markets"]
        if market["site_id"] == "MCO"
    )
    mco["item_id"] = "MCO4395789764"
    mco["last_operation"] = {"status": "failed"}
    publication["confirmed_payload"]["sites_to_sell"].append(
        deepcopy(_payload("MCO")["sites_to_sell"][0])
    )
    payload = _payload("MCO", "MLC", publication=publication)

    with patch.object(marketplace_publishing, "request_json") as request:
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    request.assert_not_called()
    assert result["ok"] is True
    assert result["operation"] == "already_published"


def test_removing_uncreated_failed_market_converges_without_remote_write() -> None:
    payload = _payload("MLC", publication=_partial_publication())

    with patch.object(marketplace_publishing, "request_json") as request:
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    request.assert_not_called()
    assert result["ok"] is True
    assert result["operation"] == "already_published"
    assert result["publication"]["status"] == "active"
    assert [
        market["site_id"] for market in result["publication"]["markets"]
    ] == ["MLC"]


def test_existing_parent_rejects_root_payload_change_before_remote_write() -> None:
    payload = _payload("MCO", "MLC", publication=_partial_publication())
    payload["attributes"].append(
        {"id": "PACKAGE_WEIGHT", "value_name": "450 g"}
    )

    with patch.object(marketplace_publishing, "request_json") as request:
        with pytest.raises(PublishAdapterError) as captured:
            marketplace_publish.publish_mercadolibre(payload, "token")

    request.assert_not_called()
    assert captured.value.code == "MERCADOLIBRE_PARENT_PAYLOAD_IMMUTABLE"
    assert "attributes" in captured.value.details["field_errors"]


def test_existing_parent_rejects_created_market_change_before_remote_write() -> None:
    payload = _payload("MCO", "MLC", publication=_partial_publication())
    mlc = next(
        site for site in payload["sites_to_sell"] if site["site_id"] == "MLC"
    )
    mlc["price"] = 88.88

    with patch.object(marketplace_publishing, "request_json") as request:
        with pytest.raises(PublishAdapterError) as captured:
            marketplace_publish.publish_mercadolibre(payload, "token")

    request.assert_not_called()
    assert captured.value.code == "MERCADOLIBRE_PARENT_PAYLOAD_IMMUTABLE"
    assert "sites_to_sell" in captured.value.details["field_errors"]


def test_add_marketplaces_rejects_wrong_parent_response() -> None:
    payload = _payload("MCO", "MLC", publication=_partial_publication())
    response = {
        "item_id": "CBT-WRONG",
        "seller_id": PARENT_SELLER_ID,
        "site_items": [_successful_site_item("MCO", "MCO4395789764")],
    }

    with patch.object(
        marketplace_publishing,
        "request_json",
        return_value=response,
    ):
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    assert result["ok"] is False
    assert result["status"] == "outcome_unknown"
    assert result["error_code"] == "MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID"
    assert "publication" not in result


def test_shipping_mode_not_supported_is_non_retryable_neutral_error() -> None:
    payload = _payload("MCO")
    response = {
        "item_id": PARENT_ID,
        "seller_id": PARENT_SELLER_ID,
        "site_items": [
            _failed_site_item(
                "MCO",
                code="item.shipping.mode.not_supported",
                message=(
                    "You can't send the product in this kind of shipment in "
                    "Colombia."
                ),
            )
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
        == "MERCADOLIBRE_SHIPPING_MODE_NOT_SUPPORTED"
    )
    assert result["error_map"]["retryable"] is False
    assert "检查店铺是否已开通" in result["error_map"]["next_action"]
    assert result["error_map"]["site_item_errors"][0]["site_id"] == "MCO"
    assert result["publication"]["markets"][0]["last_operation"]["status"] == (
        "failed"
    )


def test_shipping_mode_message_without_code_is_non_retryable_neutral_error() -> None:
    payload = _payload("MLC")
    response = {
        "item_id": PARENT_ID,
        "seller_id": PARENT_SELLER_ID,
        "site_items": [
            _failed_site_item(
                "MLC",
                code="",
                message=(
                    "You can't send the product in this kind of shipment in "
                    "Chile."
                ),
            )
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
        == "MERCADOLIBRE_SHIPPING_MODE_NOT_SUPPORTED"
    )
    assert result["error_map"]["retryable"] is False
    assert "检查店铺是否已开通" in result["error_map"]["next_action"]


def test_currently_unavailable_market_is_mapped_from_remote_response() -> None:
    payload = _payload("MLU")
    response = {
        "item_id": PARENT_ID,
        "seller_id": PARENT_SELLER_ID,
        "site_items": [
            _failed_site_item(
                "MLU",
                code="site.not_operable",
                message=(
                    "Listing in Uruguay is currently unavailable for "
                    "international dropshipping"
                ),
            )
        ],
    }

    with patch.object(
        marketplace_publishing,
        "request_json",
        return_value=response,
    ):
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    assert result["error_code"] == "MERCADOLIBRE_MARKET_NOT_OPERABLE"
    assert result["error_map"]["retryable"] is False
    assert result["error_map"]["next_action"] == (
        "该市场当前暂不接受国际直发，请移除后重新发布。"
    )


@pytest.mark.parametrize(
    ("remote_code", "remote_message", "expected_code", "retryable"),
    [
        (
            "item.package_weight.over_max",
            "Package weight exceeds the carrier limit",
            "MERCADOLIBRE_PACKAGE_CARRIER_LIMIT_EXCEEDED",
            False,
        ),
        (
            "local_rate_limited",
            "Too many local requests",
            "MERCADOLIBRE_LOCAL_RATE_LIMITED",
            True,
        ),
    ],
)
def test_known_market_failures_have_stable_actionable_contracts(
    remote_code: str,
    remote_message: str,
    expected_code: str,
    retryable: bool,
) -> None:
    payload = _payload("MCO")
    response = {
        "item_id": PARENT_ID,
        "seller_id": PARENT_SELLER_ID,
        "site_items": [
            _failed_site_item(
                "MCO",
                code=remote_code,
                message=remote_message,
            )
        ],
    }

    with patch.object(
        marketplace_publishing,
        "request_json",
        return_value=response,
    ):
        result = marketplace_publish.publish_mercadolibre(payload, "token")

    assert result["ok"] is False
    assert result["error_code"] == expected_code
    assert result["error_map"]["retryable"] is retryable
    assert result["error_map"]["next_action"]
    if expected_code == "MERCADOLIBRE_PACKAGE_CARRIER_LIMIT_EXCEEDED":
        assert "父级刊登已经创建" in result["error_map"]["next_action"]


def test_existing_parent_rejects_category_change_before_remote_write() -> None:
    payload = _payload(
        "MCO",
        "MLC",
        category_id="CBT200002",
        publication=_partial_publication(),
    )

    with patch.object(marketplace_publishing, "request_json") as request:
        with pytest.raises(PublishAdapterError) as captured:
            marketplace_publish.publish_mercadolibre(payload, "token")

    request.assert_not_called()
    assert captured.value.code == "MERCADOLIBRE_PARENT_CATEGORY_IMMUTABLE"
    assert captured.value.retryable is False
