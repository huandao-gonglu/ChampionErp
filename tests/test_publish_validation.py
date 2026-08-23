from __future__ import annotations

from unittest.mock import patch

import erp_web.runtime_units.publish_validation as publish_validation
from erp_web.runtime_units.publish_validation import validate_mercadolibre_draft
from erp_web.services.listing_currency_service import compute_currency_fingerprint


def test_mercadolibre_review_summary_restores_local_attribute_ids() -> None:
    category_record = {
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
    product = {
        "sku": "SKU-1",
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

    result = validate_mercadolibre_draft(
        product,
        {"mercadolibre": {"access_token": "x"}, "listing": {}},
        category_record,
    )
    fields = [item["field"] for item in result["errors"] if item["code"] == "NEED_REVIEW_ATTRIBUTES"]

    assert fields == [
        "attributes.RECOMMENDED_AGE_GROUP",
        "attributes.TRADING_CARD_GAME_ACCESSORY_TYPE",
    ]


def test_mercadolibre_precheck_rejects_cbt_target_with_local_category() -> None:
    product = {
        "sku": "SKU-CBT",
        "drafts": {
            "mercadolibre": {
                "site": "CBT",
                "title": "Sample title",
                "description": "Sample description",
                "category_id": "MLM1",
                "category_path": "Local category",
                "brand": "Brand",
                "model": "Model",
                "sku": "SKU-CBT",
                "price": "10",
                "stock": "1",
                "upc": "123456789012",
                "attributes": {"BRAND": "Brand", "MODEL": "Model"},
                "package_dimensions": {"length_cm": "1", "width_cm": "1", "height_cm": "1", "weight_kg": "0.1"},
                "pricing": {"suggested_price": "10"},
                "sale_terms": [{"id": "WARRANTY_TYPE", "value_name": "Seller warranty"}],
                "shipping": {"logistic_type": "drop_off"},
            }
        },
        "images": [{"url": "https://example.com/a.jpg", "selected": True, "platforms": ["mercadolibre"], "is_main": True}],
    }

    result = validate_mercadolibre_draft(product, {"mercadolibre": {"access_token": "x"}, "listing": {}})

    assert any(item["code"] == "CATEGORY_SITE_MISMATCH" for item in result["errors"])


def _ml_ready_config(currency: str) -> dict:
    fingerprint = compute_currency_fingerprint(
        "mercadolibre", "12345", currency, [currency], "locked", "site_api"
    )
    return {
        "mercadolibre": {
            "access_token": "x",
            "user_id": "12345",
            "listing_currency": currency,
            "allowed_currencies": [currency],
            "currency_mode": "locked",
            "currency_status": "ready",
            "currency_source": "site_api",
            "currency_fingerprint": fingerprint,
        },
        "listing": {},
    }


def _ml_product() -> dict:
    return {
        "sku": "SKU-CCY",
        "drafts": {
            "mercadolibre": {
                "site": "MLM",
                "title": "Sample title",
                "description": "Sample description",
                "category_id": "MLM1",
                "category_path": "Local category",
                "brand": "Brand",
                "model": "Model",
                "sku": "SKU-CCY",
                "stock": "1",
                "upc": "123456789012",
                "attributes": {"BRAND": "Brand", "MODEL": "Model"},
                "package_dimensions": {"length_cm": "1", "width_cm": "1", "height_cm": "1", "weight_kg": "0.1"},
                "sale_terms": [{"id": "WARRANTY_TYPE", "value_name": "Seller warranty"}],
                "shipping": {"logistic_type": "drop_off"},
            }
        },
        "images": [{"url": "https://example.com/a.jpg", "selected": True, "platforms": ["mercadolibre"], "is_main": True}],
    }


def test_mercadolibre_precheck_rejects_store_currency_outside_category_allowed_set() -> None:
    # §12 检查 5：类目返回允许币种时，店铺币种必须属于该允许集。
    with patch.object(
        publish_validation, "mercadolibre_category_allowed_currencies", return_value=["MXN"]
    ):
        result = validate_mercadolibre_draft(_ml_product(), _ml_ready_config("USD"))

    assert any(item["code"] == "CATEGORY_CURRENCY_MISMATCH" for item in result["errors"])


def test_mercadolibre_precheck_allows_store_currency_within_category_allowed_set() -> None:
    with patch.object(
        publish_validation, "mercadolibre_category_allowed_currencies", return_value=["MXN", "USD"]
    ):
        result = validate_mercadolibre_draft(_ml_product(), _ml_ready_config("USD"))

    assert not any(item["code"] == "CATEGORY_CURRENCY_MISMATCH" for item in result["errors"])


def test_mercadolibre_precheck_no_category_allowed_set_is_unconstrained() -> None:
    # 类目未返回允许集（或读取失败）视为无约束，不阻断。
    with patch.object(
        publish_validation, "mercadolibre_category_allowed_currencies", return_value=[]
    ):
        result = validate_mercadolibre_draft(_ml_product(), _ml_ready_config("USD"))

    assert not any(item["code"] == "CATEGORY_CURRENCY_MISMATCH" for item in result["errors"])


def test_mercadolibre_precheck_skips_category_currency_when_store_not_ready() -> None:
    # 店铺币种未 ready 时不触发类目币种读取。
    with patch.object(
        publish_validation, "mercadolibre_category_allowed_currencies"
    ) as fetch_allowed:
        validate_mercadolibre_draft(
            _ml_product(), {"mercadolibre": {"access_token": "x"}, "listing": {}}
        )

    fetch_allowed.assert_not_called()
