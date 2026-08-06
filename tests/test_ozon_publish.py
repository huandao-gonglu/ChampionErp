from __future__ import annotations

from unittest.mock import patch

import pytest

from erp_web.runtime_units.publish_adapter import OzonPublishingAdapter
from erp_web.runtime_units.publish_ozon import (
    OZON_PRODUCT_IMPORT_INFO_URL,
    OZON_PRODUCT_IMPORT_URL,
    build_ozon_publish_payload,
    map_ozon_publish_error,
    publish_ozon_payload,
    validate_ozon_publish_payload,
)
from erp_web.runtime_units.publish_validation import validate_ozon_draft


def _product() -> dict:
    return {
        "product_id": "product-ozon",
        "name": "Тестовый товар",
        "sku": "OZON-SKU-1",
        "source": {
            "image_pool": [
                {
                    "id": "image-1",
                    "url": "https://cdn.example.com/ozon-main.jpg",
                    "selected": True,
                    "platforms": ["ozon"],
                    "is_main": True,
                }
            ]
        },
        "drafts": {
            "ozon": {
                "platform": "ozon",
                "site": "global",
                "currency": "RUB",
                "title": "Тестовый товар для Ozon",
                "description": "Подробное описание товара.",
                "category_id": "94765",
                "description_category_id": "17027949",
                "category_path": "Категория / Тип",
                "category_attribute_schema": {
                    "category_id": "94765",
                    "required": [
                        {
                            "id": "85",
                            "name": "Бренд",
                            "required": True,
                            "raw": {"dictionary_id": 1},
                        },
                        {
                            "id": "4191",
                            "name": "Аннотация",
                            "required": True,
                        },
                    ],
                    "optional": [
                        {
                            "id": "21841",
                            "name": "Видео",
                            "required": False,
                            "raw": {"attribute_complex_id": 100001},
                        }
                    ],
                },
                "brand": "Champion",
                "model": "M1",
                "sku": "OZON-SKU-1",
                "upc": "123456789012",
                "price": "1999.90",
                "stock": "5",
                "vat": "0",
                "attributes": {
                    "0": "不得发送零属性 ID",
                    "16": "不得发送来源数字键",
                    "85": {
                        "values": [
                            {
                                "dictionary_value_id": 5060050,
                                "value": "Champion",
                            }
                        ]
                    },
                    "21841": "https://example.com/video.mp4",
                    "99999": "不得发送非类目数字属性",
                    "BRAND": "不得发送跨平台辅助字段",
                },
                "images": [
                    {"asset_id": "image-1", "role": "main", "order": 0}
                ],
                "package_dimensions": {
                    "length_cm": "12.3",
                    "width_cm": "4.5",
                    "height_cm": "6.7",
                    "weight_kg": "0.25",
                },
                "pricing": {"suggested_price": "1999.90"},
                "validation_errors": [],
            }
        },
    }


def _config() -> dict:
    return {
        "ozon": {
            "client_id": "client-id",
            "api_key": "api-key",
            "auth_status": "success",
        },
        "listing": {},
    }


def test_build_ozon_publish_payload_uses_real_v3_contract() -> None:
    payload = build_ozon_publish_payload(_product(), _config())

    assert list(payload) == ["items"]
    item = payload["items"][0]
    assert item["description_category_id"] == 17027949
    assert item["type_id"] == 94765
    assert item["offer_id"] == "OZON-SKU-1"
    assert item["price"] == "1999.9"
    assert item["currency_code"] == "RUB"
    assert item["depth"] == 123
    assert item["width"] == 45
    assert item["height"] == 67
    assert item["weight"] == 250
    assert item["dimension_unit"] == "mm"
    assert item["weight_unit"] == "g"
    assert item["images"] == ["https://cdn.example.com/ozon-main.jpg"]
    assert {row["id"] for row in item["attributes"]} == {85, 4191}
    assert item["attributes"][0]["values"][0]["dictionary_value_id"] == 5060050
    assert item["complex_attributes"] == [
        {
            "attributes": [
                {
                    "complex_id": 100001,
                    "id": 21841,
                    "values": [{"value": "https://example.com/video.mp4"}],
                }
            ]
        }
    ]


def test_ozon_payload_prefers_delivery_url_over_local_preview(tmp_path) -> None:
    product = _product()
    local_image = tmp_path / "main.jpg"
    local_image.write_bytes(b"local-preview")
    pool_item = product["source"]["image_pool"][0]
    pool_item["path"] = str(local_image)
    pool_item["preview_url"] = f"/file?path={local_image}"

    payload = build_ozon_publish_payload(product, _config())

    assert payload["items"][0]["images"] == [
        "https://cdn.example.com/ozon-main.jpg"
    ]


def test_ozon_category_pair_does_not_fall_back_to_product_category_record() -> None:
    product = _product()
    product["drafts"]["ozon"]["description_category_id"] = ""
    product["local_platform_categories"] = {
        "ozon": {
            "type_id": "94765",
            "description_category_id": "17027949",
        }
    }

    result = validate_ozon_draft(product, _config())

    assert any(
        item["code"] == "CATEGORY_PAIR_MISSING"
        for item in result["errors"]
    )


def test_ozon_payload_validation_requires_credentials_and_public_images() -> None:
    payload = build_ozon_publish_payload(_product(), _config())
    payload["items"][0]["images"] = ["/file?path=/tmp/local.jpg"]

    errors = validate_ozon_publish_payload(payload, {"ozon": {}})

    assert "Ozon Client ID" in errors
    assert "Ozon API Key" in errors
    assert "图片必须是 Ozon 可访问的 HTTP(S) 公网 URL" in errors


def test_ozon_payload_validation_rejects_invalid_and_duplicate_attribute_ids() -> None:
    payload = build_ozon_publish_payload(_product(), _config())
    item = payload["items"][0]
    item["attributes"].extend(
        [
            {"id": 0, "values": [{"value": "invalid"}]},
            {"id": 85, "values": [{"value": "duplicate"}]},
            {"id": 123, "values": []},
        ]
    )

    errors = validate_ozon_publish_payload(payload, _config())

    assert "Ozon 属性 ID 必须是正整数" in errors
    assert "Ozon 属性 ID 重复：85" in errors
    assert "Ozon 属性 123 缺少值" in errors


def test_publish_ozon_waits_for_imported_terminal_state() -> None:
    calls: list[tuple[str, dict]] = []

    def request(method, url, client_id, api_key, payload):
        calls.append((url, payload))
        if url == OZON_PRODUCT_IMPORT_URL:
            return {"result": {"task_id": 172549793}}
        assert url == OZON_PRODUCT_IMPORT_INFO_URL
        return {
            "result": {
                "items": [
                    {
                        "offer_id": "OZON-SKU-1",
                        "product_id": 137285792,
                        "status": "imported",
                        "errors": [],
                    }
                ],
                "total": 1,
            }
        }

    with patch(
        "erp_web.runtime_units.publish_ozon.request_ozon_json",
        side_effect=request,
    ):
        result = publish_ozon_payload(
            {"items": [{"offer_id": "OZON-SKU-1"}]},
            "client-id",
            "api-key",
        )

    assert result["ok"] is True
    assert result["status"] == "imported"
    assert result["task_id"] == 172549793
    assert result["external_id"] == "137285792"
    assert calls == [
        (OZON_PRODUCT_IMPORT_URL, {"items": [{"offer_id": "OZON-SKU-1"}]}),
        (OZON_PRODUCT_IMPORT_INFO_URL, {"task_id": 172549793}),
    ]


def test_publish_ozon_rejects_item_level_errors_and_maps_attribute() -> None:
    responses = [
        {"result": {"task_id": 10}},
        {
            "result": {
                "items": [
                    {
                        "offer_id": "OZON-SKU-1",
                        "status": "failed",
                        "errors": [
                            {
                                "code": "ATTRIBUTE_INVALID",
                                "attribute_id": 85,
                                "description": "Значение бренда не найдено",
                            }
                        ],
                    }
                ]
            }
        },
    ]
    with patch(
        "erp_web.runtime_units.publish_ozon.request_ozon_json",
        side_effect=responses,
    ), pytest.raises(RuntimeError) as caught:
        publish_ozon_payload(
            {"items": [{"offer_id": "OZON-SKU-1"}]},
            "client-id",
            "api-key",
        )

    mapped = map_ozon_publish_error(caught.value)
    assert mapped["error_code"] == "ATTRIBUTE_INVALID"
    assert mapped["field_errors"] == {
        "attributes.85": ["Значение бренда не найдено"]
    }


def test_ozon_adapter_and_draft_precheck_are_publish_ready() -> None:
    product = _product()
    adapter = OzonPublishingAdapter()

    assert adapter.required_attributes_missing(product, _config()) == []
    assert validate_ozon_draft(product, _config())["ok"] is True
    assert adapter.validate_payload(adapter.build_payload(product, _config()), _config()) == []
