from __future__ import annotations

from erp_web.runtime_units.source_collect_1688_api import (
    build_1688_api_params,
    extract_1688_offer_id,
    normalize_1688_api_config,
    parse_1688_api_product,
)


def test_extract_1688_offer_id_from_detail_url() -> None:
    assert extract_1688_offer_id("https://detail.1688.com/offer/735716533861.html?spm=test") == "735716533861"
    assert extract_1688_offer_id("735716533861") == "735716533861"


def test_build_1688_api_params_signs_without_secret_leak() -> None:
    config = {
        "app_key": "app-key",
        "app_secret": "secret",
        "access_token": "token",
        "method": "alibaba.product.get",
        "api_version": "1.0",
        "sign_method": "md5",
    }

    params = build_1688_api_params(config, "735716533861")

    assert params["app_key"] == "app-key"
    assert params["productId"] == "735716533861"
    assert params["offerId"] == "735716533861"
    assert params["access_token"] == "token"
    assert "secret" not in params
    assert len(params["sign"]) == 32


def test_normalize_1688_api_config_accepts_direct_section() -> None:
    cfg = normalize_1688_api_config({"app_key": "key", "app_secret": "secret", "base_url": "https://example.test/api"})

    assert "enabled" not in cfg
    assert cfg["app_key"] == "key"
    assert cfg["app_secret"] == "secret"
    assert cfg["base_url"] == "https://example.test/api"


def test_parse_1688_api_product_maps_common_fields() -> None:
    raw = {
        "result": {
            "subject": "不锈钢厨房置物架",
            "price": "12.50",
            "description": "厨房收纳用品",
            "brandName": "OEM",
            "skuInfos": [
                {"skuId": "1", "skuName": "黑色", "price": "12.50", "stock": 99, "attributes": {"颜色": "黑色"}}
            ],
            "images": ["//cbu01.alicdn.com/img/test.jpg"],
            "attributes": [
                {"name": "材质", "value": "不锈钢"},
                {"name": "规格", "value": "30 x 20 x 10 cm"},
            ],
        }
    }

    source = parse_1688_api_product(raw, "https://detail.1688.com/offer/735716533861.html", "735716533861")

    assert source["title"] == "不锈钢厨房置物架"
    assert source["price"] == "12.50"
    assert source["currency"] == "CNY"
    assert source["images"] == ["https://cbu01.alicdn.com/img/test.jpg"]
    assert source["attributes"]["材质"] == "不锈钢"
    assert source["dimensions"]["length_cm"] == "30"
    assert source["skus"][0]["price"] == "12.50"
