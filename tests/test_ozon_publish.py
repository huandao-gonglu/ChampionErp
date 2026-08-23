from __future__ import annotations

from unittest.mock import patch

import pytest

from erp_web.runtime_units.publish_adapter import OzonPublishingAdapter
from erp_web.runtime_units.publish_ozon import (
    OZON_PRODUCT_IMPORT_INFO_URL,
    OZON_PRODUCT_IMPORT_URL,
    build_ozon_publish_payload,
    map_ozon_publish_error,
    poll_ozon_import_status,
    publish_ozon_payload,
    validate_ozon_publish_payload,
)
from erp_web.runtime_units.publish_validation import validate_ozon_draft
from erp_web.services.listing_currency_service import compute_currency_fingerprint
from erp_web.services.pricing_service import pricing_calculation_fingerprint

from tests.publish_category_support import record_from_schema
from tests.runtime_test_utils import seed_store_currency

#: 店铺授权配置是发布币种唯一事实源：测试显式创建 ready 店铺配置。
_STORE_CURRENCY_FINGERPRINT = compute_currency_fingerprint(
    "ozon", "client-id", "RUB", ["RUB"], "locked", "account_api"
)


@pytest.fixture(autouse=True)
def _ready_store_currency() -> None:
    seed_store_currency("ozon", "RUB", identity={"client_id": "client-id"})

#: 测试类目定义（当次临时规则，不再持久化进草稿）。
_CATEGORY_SCHEMA: dict = {
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
}


def _record(schema: dict | None = None) -> dict:
    return record_from_schema(
        platform="ozon",
        category_id="94765",
        schema=schema if schema is not None else _CATEGORY_SCHEMA,
        category_path="Категория / Тип",
        description_category_id="17027949",
    )


def _product() -> dict:
    basis = {
        "listing_currency": "RUB",
        "currency_fingerprint": _STORE_CURRENCY_FINGERPRINT,
        "length_cm": "12.3",
        "width_cm": "4.5",
        "height_cm": "6.7",
        "weight_kg": "0.25",
    }
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
                "target_sites": [{"platform": "ozon", "site": "global", "language": "ru-RU", "listing_currency": "RUB"}],
                "title": "Тестовый товар для Ozon",
                "description": "Подробное описание товара.",
                "category_id": "94765",
                "description_category_id": "17027949",
                "category_path": "Категория / Тип",
                "brand": "Champion",
                "model": "M1",
                "sku": "OZON-SKU-1",
                "upc": "123456789012",
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
                "pricing": {"targets": {"ozon:global": {
                    "listing_currency": "RUB",
                    "suggested_price": {"amount": "1999.90", "currency": "RUB"},
                    "applied_price": {"amount": "1999.90", "currency": "RUB"},
                    "calculation_basis": basis,
                    "calculation_fingerprint": pricing_calculation_fingerprint(basis),
                }}},
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
    payload = build_ozon_publish_payload(_product(), _config(), _record())

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


def test_build_ozon_publish_payload_accepts_dictionary_id_zero_as_free_text() -> None:
    product = _product()
    product["drafts"]["ozon"]["attributes"]["9048"] = "F30"
    schema = {
        "required": [
            *_CATEGORY_SCHEMA["required"],
            {
                "id": "9048",
                "name": "Название модели",
                "required": True,
                "dictionary_id": "0",
                "is_dictionary": True,
            },
        ],
        "optional": list(_CATEGORY_SCHEMA["optional"]),
    }

    payload = build_ozon_publish_payload(product, _config(), _record(schema))
    model_attribute = next(
        item for item in payload["items"][0]["attributes"] if item["id"] == 9048
    )

    assert model_attribute["values"] == [{"value": "F30"}]


def test_ozon_payload_prefers_delivery_url_over_local_preview(tmp_path) -> None:
    product = _product()
    local_image = tmp_path / "main.jpg"
    local_image.write_bytes(b"local-preview")
    pool_item = product["source"]["image_pool"][0]
    pool_item["path"] = str(local_image)
    pool_item["preview_url"] = f"/file?path={local_image}"

    payload = build_ozon_publish_payload(product, _config(), _record())

    assert payload["items"][0]["images"] == [
        "https://cdn.example.com/ozon-main.jpg"
    ]


def test_ozon_category_pair_does_not_fall_back_to_product_category_record() -> None:
    # 类目身份只来自草稿；description_category_id 缺失时必须显式报错，
    # 不存在任何商品级规则副本回退路径（副本字段已退役）。
    product = _product()
    product["drafts"]["ozon"]["description_category_id"] = ""

    result = validate_ozon_draft(product, _config(), _record())

    assert any(
        item["code"] == "CATEGORY_PAIR_MISSING"
        for item in result["errors"]
    )


def test_ozon_payload_validation_requires_credentials_and_public_images() -> None:
    payload = build_ozon_publish_payload(_product(), _config(), _record())
    payload["items"][0]["images"] = ["/file?path=/tmp/local.jpg"]

    errors = validate_ozon_publish_payload(payload, {"ozon": {}})

    assert "Ozon Client ID" in errors
    assert "Ozon API Key" in errors
    assert "图片必须是 Ozon 可访问的 HTTP(S) 公网 URL" in errors


def test_ozon_payload_validation_rejects_invalid_and_duplicate_attribute_ids() -> None:
    payload = build_ozon_publish_payload(_product(), _config(), _record())
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


def test_publish_ozon_returns_pending_confirmation_when_local_wait_expires() -> None:
    responses = [
        {"result": {"task_id": 172549793}},
        {
            "result": {
                "items": [
                    {
                        "offer_id": "OZON-SKU-1",
                        "status": "pending",
                        "errors": [],
                    }
                ]
            }
        },
    ]
    with patch(
        "erp_web.runtime_units.publish_ozon.request_ozon_json",
        side_effect=responses,
    ), patch(
        "erp_web.runtime_units.publish_ozon.time.monotonic",
        side_effect=[0.0, 1.0],
    ):
        result = publish_ozon_payload(
            {"items": [{"offer_id": "OZON-SKU-1"}]},
            "client-id",
            "api-key",
            timeout_seconds=0.1,
        )

    assert result["ok"] is True
    assert result["status"] == "pending_confirmation"
    assert result["task_id"] == 172549793
    assert result["offer_id"] == "OZON-SKU-1"
    assert "external_id" not in result


def test_poll_ozon_import_status_rejects_failed_terminal_without_error_rows() -> None:
    response = {
        "result": {
            "items": [
                {
                    "offer_id": "OZON-SKU-1",
                    "status": "failed",
                    "errors": [],
                }
            ]
        }
    }
    with patch(
        "erp_web.runtime_units.publish_ozon.request_ozon_json",
        return_value=response,
    ), pytest.raises(RuntimeError, match="Ozon 商品导入失败"):
        poll_ozon_import_status(172549793, "client-id", "api-key")


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
                                "code": "ATTRIBUTE_WARNING",
                                "attribute_id": 23102,
                                "level": "warning",
                                "description": "可选属性值不在列表中",
                            },
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


def test_publish_ozon_does_not_treat_item_warning_as_failure() -> None:
    responses = [
        {"result": {"task_id": 11}},
        {
            "result": {
                "items": [
                    {
                        "offer_id": "OZON-SKU-1",
                        "product_id": 22,
                        "status": "imported",
                        "errors": [
                            {
                                "code": "ATTRIBUTE_WARNING",
                                "attribute_id": 23102,
                                "level": "warning",
                                "description": "可选属性值不在列表中",
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
    ):
        result = publish_ozon_payload(
            {"items": [{"offer_id": "OZON-SKU-1"}]},
            "client-id",
            "api-key",
        )

    assert result["ok"] is True
    assert result["product_id"] == 22


def test_ozon_precheck_rejects_free_text_dictionary_attribute() -> None:
    product = _product()
    product["drafts"]["ozon"]["attributes"]["85"] = "Champion"

    result = validate_ozon_draft(product, _config(), _record())

    assert any(
        item["code"] == "ATTRIBUTE_DICTIONARY_VALUE_REQUIRED"
        and item["field"] == "attributes.85"
        for item in result["errors"]
    )
    with pytest.raises(ValueError, match="必须从平台选项中选择"):
        build_ozon_publish_payload(product, _config(), _record())


def test_ozon_adapter_and_draft_precheck_are_publish_ready() -> None:
    from erp_web.runtime_units.publish_context import PreparedPublishContext

    from tests.publish_category_support import definition_from_record

    product = _product()
    adapter = OzonPublishingAdapter()
    record = _record()
    context = PreparedPublishContext(
        product=product,
        draft=product["drafts"]["ozon"],
        target=product["drafts"]["ozon"]["target_sites"][0],
        category_definition=definition_from_record(record),
        platform="ozon",
    )

    assert adapter.required_attributes_missing(context, _config()) == []
    assert adapter.validate_draft(context, _config())["ok"] is True
    assert (
        adapter.validate_payload(adapter.build_payload(context, _config()), _config())
        == []
    )
