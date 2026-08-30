from __future__ import annotations

from unittest.mock import patch

import pytest

import erp_web.runtime_units.publish_validation as publish_validation
from erp_web.context import get_context
from erp_web.runtime_units.category_definition_support import (
    definition_from_legacy_attributes,
)
from erp_web.runtime_units.category_refresh import normalize_ml_attribute
from erp_web.runtime_units.publish_validation import validate_mercadolibre_draft
from erp_web.schemas.category_definition import CategoryAttributeOptionPreview
from erp_web.services.listing_currency_service import compute_currency_fingerprint
from erp_web.services.pricing_service import pricing_calculation_fingerprint


def _ml_ready_config(
    currency: str = "USD",
    *,
    bindings: list[dict] | None = None,
    user_product_seller: bool = True,
    listing_model: str = "user_products",
) -> dict:
    fingerprint = compute_currency_fingerprint(
        "mercadolibre",
        "12345",
        currency,
        [currency],
        "locked",
        "global_selling_contract",
    )
    return {
        "mercadolibre": {
            "access_token": "x",
            "user_id": "12345",
            "site_id": "CBT",
            "account_site_id": "CBT",
            "listing_model": listing_model,
            "user_product_seller": user_product_seller,
            "marketplace_bindings": bindings
            if bindings is not None
            else [
                {
                    "seller_id": "991",
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "business_model": "",
                    "pricing_model": "price",
                    "user_product": True,
                }
            ],
            "listing_currency": currency,
            "allowed_currencies": [currency],
            "currency_mode": "locked",
            "currency_status": "ready",
            "currency_source": "global_selling_contract",
            "currency_fingerprint": fingerprint,
        },
        "listing": {},
    }


def _ml_cbt_product(
    sites_to_sell: list[dict[str, str]] | None = None,
    *,
    site: str = "CBT",
    category_id: str = "CBT1",
    language: str = "es",
) -> dict:
    targets = sites_to_sell
    if targets is None:
        targets = [{"site_id": "MLM", "logistic_type": "remote"}]
    return {
        "sku": "SKU-CBT",
        "drafts": {
            "mercadolibre": {
                "site": site,
                "language": language,
                "title": "Sample family name",
                "description": "Sample description",
                "category_id": category_id,
                "category_path": "Global category",
                "brand": "Brand",
                "model": "Model",
                "sku": "SKU-CBT",
                "stock": "1",
                "upc": "123456789012",
                "attributes": {
                    "BRAND": "Brand",
                    "MODEL": "Model",
                    "GTIN": "123456789012",
                },
                "package_dimensions": {
                    "length_cm": "1",
                    "width_cm": "1",
                    "height_cm": "1",
                    "weight_kg": "0.1",
                },
                "sale_terms": [
                    {"id": "WARRANTY_TYPE", "value_name": "No warranty"}
                ],
                "shipping": {"logistic_type": "remote"},
                "sites_to_sell": targets,
                "target_sites": [
                    {
                        "platform": "mercadolibre",
                        "site": site,
                        "language": language,
                        "listing_currency": "USD",
                        "category_id": category_id,
                        "category_path": "Global category",
                        "sites_to_sell": targets,
                    }
                ],
            }
        },
        "images": [
            {
                "url": "https://example.com/a.jpg",
                "selected": True,
                "platforms": ["mercadolibre"],
                "is_main": True,
            }
        ],
    }


def _ml_category_definition():
    raw = [
        {
            "id": attribute_id,
            "name": attribute_id,
            "tags": {"required": True},
            "value_type": "number_unit" if attribute_id.startswith("PACKAGE_") else "string",
            "allowed_units": (
                [{"id": "g", "name": "g"}]
                if attribute_id == "PACKAGE_WEIGHT"
                else [{"id": "cm", "name": "cm"}]
                if attribute_id.startswith("PACKAGE_")
                else []
            ),
            "default_unit": (
                "g"
                if attribute_id == "PACKAGE_WEIGHT"
                else "cm"
                if attribute_id.startswith("PACKAGE_")
                else ""
            ),
            "values": (
                [{"id": "39205163", "name": "110/220V"}]
                if attribute_id == "VOLTAGE"
                else []
            ),
        }
        for attribute_id in (
            "BRAND",
            "MODEL",
            "VOLTAGE",
            "PACKAGE_LENGTH",
            "PACKAGE_WIDTH",
            "PACKAGE_HEIGHT",
            "PACKAGE_WEIGHT",
        )
    ]
    raw.extend(
        [
            {
                "id": "WEIGHT",
                "name": "Weight",
                "value_type": "number_unit",
                "allowed_units": [
                    {"id": "g", "name": "g"},
                    {"id": "kg", "name": "kg"},
                ],
                "default_unit": "kg",
            },
            {"id": "GTIN", "name": "GTIN", "value_type": "string"},
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
            },
            {
                "id": "ITEM_CONDITION",
                "name": "Item condition",
                "value_type": "list",
                "values": [{"id": "2230284", "name": "New"}],
            },
            {"id": "SELLER_SKU", "name": "SKU", "value_type": "string"},
        ]
    )
    normalized = [normalize_ml_attribute(item) for item in raw]
    return definition_from_legacy_attributes(
        platform="mercadolibre",
        site="CBT",
        category_id="CBT1",
        required=[item for item in normalized if item["required"]],
        optional=[item for item in normalized if not item["required"]],
    )


def _validate(product: dict, config: dict, *, category_definition=None) -> dict:
    with patch.object(
        publish_validation,
        "mercadolibre_category_allowed_currencies",
        return_value=[],
    ):
        return validate_mercadolibre_draft(
            product,
            config,
            category_definition=category_definition,
        )


def test_mercadolibre_precheck_requires_cbt_parent_and_user_products_tag() -> None:
    regional_config = _ml_ready_config()
    regional_config["mercadolibre"]["account_site_id"] = "MLM"
    result = _validate(_ml_cbt_product(), regional_config)
    assert any(
        item["code"] == "MERCADOLIBRE_CBT_ACCOUNT_REQUIRED"
        for item in result["errors"]
    )

    no_user_products = _ml_ready_config(user_product_seller=False)
    result = _validate(_ml_cbt_product(), no_user_products)
    assert any(
        item["code"] == "MERCADOLIBRE_USER_PRODUCTS_REQUIRED"
        for item in result["errors"]
    )


def test_mercadolibre_traditional_precheck_accepts_net_proceeds_binding() -> None:
    config = _ml_ready_config(
        user_product_seller=False,
        listing_model="traditional_global_items",
        bindings=[
            {
                "seller_id": "991",
                "site_id": "MLM",
                "logistic_type": "remote",
                "business_model": "CBT CN International Drop Shipping",
                "pricing_model": "net_proceeds",
                "user_product": False,
            }
        ],
    )

    result = _validate(_ml_cbt_product(), config)

    codes = {item["code"] for item in result["errors"]}
    assert "MERCADOLIBRE_USER_PRODUCTS_REQUIRED" not in codes
    assert "MERCADOLIBRE_PRICING_MODEL_MISMATCH" not in codes
    assert "MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED" not in codes


def test_mercadolibre_precheck_does_not_restore_product_upc_for_exempt_draft() -> None:
    product = _ml_cbt_product()
    product["upc"] = "725272000243"
    draft = product["drafts"]["mercadolibre"]
    draft["upc"] = ""
    draft["allow_gtin_exemption"] = True
    draft["attributes"].pop("GTIN", None)

    result = _validate(product, _ml_ready_config())

    assert not any(item["code"] == "UPC_MISSING" for item in result["errors"])
    assert any(item["code"] == "UPC_MISSING" for item in result["warnings"])


def test_mercadolibre_precheck_rejects_brand_outside_restricted_candidates() -> None:
    product = _ml_cbt_product()
    product["drafts"]["mercadolibre"]["brand"] = "蔚小电"
    definition = _ml_category_definition()
    brand = definition.attribute_by_id("BRAND")
    assert brand is not None
    definition = definition.model_copy(
        update={
            "required": tuple(
                brand.model_copy(
                    update={
                        "allow_custom_values": False,
                        "value_mode": "strict_enum",
                        "is_dictionary": True,
                        "options": (
                            CategoryAttributeOptionPreview(
                                value="Generic",
                                dictionary_value_id="35977846",
                            ),
                        ),
                    }
                )
                if item.id == "BRAND"
                else item
                for item in definition.required
            )
        }
    )

    result = _validate(
        product,
        _ml_ready_config(),
        category_definition=definition,
    )

    assert any(
        item["code"] == "ATTRIBUTE_ENUM_VALUE_INVALID"
        and item["field"] == "attributes.BRAND"
        for item in result["errors"]
    )


def test_mercadolibre_traditional_precheck_requires_global_english_title() -> None:
    result = _validate(
        _ml_cbt_product(),
        _ml_ready_config(listing_model="traditional_global_items"),
    )

    assert any(
        item["code"] == "GLOBAL_TITLE_MISSING"
        for item in result["errors"]
    )


def test_mercadolibre_precheck_uses_same_attribute_contract_as_payload() -> None:
    product = _ml_cbt_product()
    draft = product["drafts"]["mercadolibre"]
    draft["attributes"].update(
        {
            "VOLTAGE": "110/220V",
            "WEIGHT": "182",
            "产地": "广东",
        }
    )

    result = _validate(
        product,
        _ml_ready_config(),
        category_definition=_ml_category_definition(),
    )
    issues = {(item["code"], item["field"]) for item in result["errors"]}

    assert ("ATTRIBUTE_UNIT_REQUIRED", "attributes.WEIGHT") in issues
    assert ("ATTRIBUTE_NOT_IN_CATEGORY", "attributes.产地") in issues


def test_manually_edited_voltage_clears_need_review_during_precheck() -> None:
    product = _ml_cbt_product()
    draft = product["drafts"]["mercadolibre"]
    draft["attributes"]["VOLTAGE"] = "110/220V"
    draft["validation_errors"] = [
        {
            "code": "NEED_REVIEW_ATTRIBUTES",
            "field": "attributes.VOLTAGE",
            "message": "AI 暂无法从商品信息判断，请人工确认。",
        }
    ]

    result = _validate(
        product,
        _ml_ready_config(),
        category_definition=_ml_category_definition(),
    )

    assert not any(
        item["code"] == "NEED_REVIEW_ATTRIBUTES"
        and item["field"] == "attributes.VOLTAGE"
        for item in result["errors"]
    )


@pytest.mark.parametrize(
    ("language", "site_id"),
    [("es", "MLB"), ("pt-BR", "MLM"), ("zh-CN", "MLM")],
)
def test_mercadolibre_precheck_rejects_market_outside_copy_language(
    language: str,
    site_id: str,
) -> None:
    binding = {
        "seller_id": "991",
        "site_id": site_id,
        "logistic_type": "remote",
        "business_model": "",
        "pricing_model": "price",
        "user_product": True,
    }
    result = _validate(
        _ml_cbt_product(
            [{"site_id": site_id, "logistic_type": "remote"}],
            language=language,
        ),
        _ml_ready_config(bindings=[binding]),
    )

    assert any(
        item["code"] == "MERCADOLIBRE_SALES_TARGET_LANGUAGE_MISMATCH"
        for item in result["errors"]
    )


def test_mercadolibre_precheck_rejects_local_draft_target() -> None:
    with pytest.raises(ValueError, match="只允许 CBT/Siteless 一级草稿"):
        _validate(
            _ml_cbt_product(site="MLM", category_id="MLM1"),
            _ml_ready_config(),
        )


def test_mercadolibre_precheck_rejects_cbt_target_with_local_category() -> None:
    result = _validate(
        _ml_cbt_product(category_id="MLM1"),
        _ml_ready_config(),
    )

    assert any(
        item["code"] == "CATEGORY_SITE_MISMATCH"
        for item in result["errors"]
    )


def test_mercadolibre_precheck_requires_sales_targets() -> None:
    result = _validate(_ml_cbt_product([]), _ml_ready_config())

    assert any(
        item["code"] == "MERCADOLIBRE_SITES_TO_SELL_REQUIRED"
        for item in result["errors"]
    )


def test_mercadolibre_precheck_rejects_binding_without_user_products() -> None:
    config = _ml_ready_config(
        bindings=[
            {
                "seller_id": "991",
                "site_id": "MLM",
                "logistic_type": "remote",
                "user_product": False,
            }
        ]
    )
    result = _validate(_ml_cbt_product(), config)

    assert any(
        item["code"] == "MERCADOLIBRE_USER_PRODUCTS_REQUIRED"
        and item["field"] == "sites_to_sell[0]"
        for item in result["errors"]
    )


def test_mercadolibre_precheck_rejects_unbound_sales_operation() -> None:
    result = _validate(
        _ml_cbt_product(
            [{"site_id": "MLM", "logistic_type": "drop_off"}]
        ),
        _ml_ready_config(),
    )

    assert any(
        item["code"] == "MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED"
        for item in result["errors"]
    )


def test_mercadolibre_precheck_blocks_fully_managed_standard_price_flow() -> None:
    config = _ml_ready_config(
        bindings=[
            {
                "seller_id": "991",
                "site_id": "MLM",
                "logistic_type": "remote",
                "business_model": "standard",
                "pricing_model": "price",
                "user_product": True,
            },
            {
                "seller_id": "992",
                "site_id": "MCO",
                "logistic_type": "fulfillment",
                "business_model": "CBT CN Fulfillment Managed",
                "pricing_model": "net_proceeds",
                "user_product": True,
            }
        ]
    )
    result = _validate(
        _ml_cbt_product(
            [{"site_id": "MLM", "logistic_type": "remote"}]
        ),
        config,
    )

    assert any(
        item["code"] == "MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED"
        for item in result["errors"]
    )


def test_mercadolibre_precheck_requires_usd_for_standard_cbt() -> None:
    result = _validate(_ml_cbt_product(), _ml_ready_config("MXN"))

    assert any(
        item["code"] == "MERCADOLIBRE_CBT_CURRENCY_INVALID"
        for item in result["errors"]
    )


def test_mercadolibre_precheck_allows_local_images_pending_automatic_upload() -> None:
    product = _ml_cbt_product()
    product["images"][0] = {
        **product["images"][0],
        "url": "",
        "path": "data/images/local-main.jpg",
    }

    result = _validate(product, _ml_ready_config())

    codes = {item["code"] for item in result["warnings"]}
    assert "IMAGE_NOT_UPLOADED" not in codes


def test_mercadolibre_precheck_does_not_restore_cleared_sale_terms_from_config() -> None:
    product = _ml_cbt_product()
    product["drafts"]["mercadolibre"]["sale_terms"] = []
    config = _ml_ready_config()
    config["listing"]["mercadolibre_sale_terms"] = [
        {"id": "WARRANTY_TYPE", "value_name": "Seller warranty"}
    ]

    result = _validate(product, config)

    assert any(
        item["code"] == "SALE_TERMS_MISSING"
        and item["field"] == "sale_terms"
        for item in result["errors"]
    )


def test_mercadolibre_precheck_requires_an_explicit_warranty_type() -> None:
    product = _ml_cbt_product()
    product["drafts"]["mercadolibre"]["sale_terms"] = [
        {
            "id": "WARRANTY_TIME",
            "value_name": "3 months",
            "value_struct": {"number": 3, "unit": "months"},
        }
    ]

    result = _validate(product, _ml_ready_config())

    assert any(
        item["code"] == "SALE_TERMS_MISSING"
        and item["field"] == "sale_terms"
        for item in result["errors"]
    )


def test_mercadolibre_precheck_invalidates_pricing_when_sales_targets_change() -> None:
    config = _ml_ready_config(
        bindings=[
            {
                "seller_id": "991",
                "site_id": "MLM",
                "logistic_type": "remote",
                "user_product": True,
            },
            {
                "seller_id": "992",
                "site_id": "MLB",
                "logistic_type": "remote",
                "user_product": True,
            },
        ]
    )
    product = _ml_cbt_product(
        [{"site_id": "MLM", "logistic_type": "remote"}]
    )
    basis = {
        "listing_currency": "USD",
        "currency_fingerprint": config["mercadolibre"]["currency_fingerprint"],
        "length_cm": "1",
        "width_cm": "1",
        "height_cm": "1",
        "weight_kg": "0.1",
        "sites_to_sell": [
            {"site_id": "MLB", "logistic_type": "remote"}
        ],
    }
    product["drafts"]["mercadolibre"]["pricing"] = {
        "targets": {
            "mercadolibre:cbt": {
                "listing_currency": "USD",
                "applied_price": {"amount": "18.00", "currency": "USD"},
                "calculation_basis": basis,
                "calculation_fingerprint": pricing_calculation_fingerprint(basis),
            }
        }
    }

    context = get_context()
    with (
        patch.object(context.config, "load_store_config", return_value=config),
        patch.object(context.config, "load_app_config", return_value={}),
        patch.object(
            publish_validation,
            "mercadolibre_category_allowed_currencies",
            return_value=[],
        ),
    ):
        result = validate_mercadolibre_draft(product, config)

    assert any(
        item["code"] == "PRICING_STALE"
        and "销售国家或物流方式已变化" in item["message"]
        for item in result["errors"]
    )


def test_mercadolibre_review_summary_uses_current_cbt_attribute_ids() -> None:
    category_record = {
        "category_id": "CBT1",
        "category_path": "Protectors",
        "attributes": {
            "required": [
                {"id": "GTIN", "name": "GTIN", "required": True},
                {
                    "id": "RECOMMENDED_AGE_GROUP",
                    "name": "Recommended age",
                    "required": True,
                },
                {
                    "id": "ACCESSORY_TYPE",
                    "name": "Accessory type",
                    "required": True,
                },
            ],
            "optional": [],
        },
    }
    product = _ml_cbt_product()
    product["drafts"]["mercadolibre"]["validation_errors"] = [
        {
            "code": "NEED_REVIEW_ATTRIBUTES",
            "field": "attributes",
            "message": "仍有属性待复核",
            "severity": "error",
        }
    ]

    with patch.object(
        publish_validation,
        "mercadolibre_category_allowed_currencies",
        return_value=[],
    ):
        result = validate_mercadolibre_draft(
            product,
            _ml_ready_config(),
            category_record,
        )

    fields = [
        item["field"]
        for item in result["errors"]
        if item["code"] == "NEED_REVIEW_ATTRIBUTES"
    ]
    assert fields == [
        "attributes.ACCESSORY_TYPE",
        "attributes.RECOMMENDED_AGE_GROUP",
    ]
