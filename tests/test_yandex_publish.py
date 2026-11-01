# -*- coding: utf-8 -*-
"""Yandex Market 发布状态机、payload 构造与错误契约的单元测试。"""

from __future__ import annotations

from erp_web.stores.product_description_migration import migrate_draft_description

import json
import time
from copy import deepcopy
from typing import Any

import pytest

from erp_web.marketplaces.yandex_http import YandexApiError
from erp_web.services.listing_currency_service import compute_currency_fingerprint
from erp_web.services.pricing_service import pricing_calculation_fingerprint
from erp_web.runtime_units import publish_yandex
from erp_web.runtime_units.publish_yandex import (
    build_yandex_publish_payload,
    map_yandex_publish_error,
    poll_yandex_publish_status,
    advance_yandex_submission,
    publish_yandex_payload,
    validate_yandex_publish_payload,
    yandex_invalid_dictionary_attributes,
    yandex_invalid_unit_attributes,
    yandex_offer_identity_conflict,
    yandex_required_attributes_missing,
)

from tests.yandex_logistics_support import PLAN, install_logistics

from tests.publish_category_support import record_from_schema
from tests.runtime_test_utils import seed_store_currency, sku_publish_fixture

API_TOKEN = "secret-api-token-123"

#: 店铺授权配置是发布币种唯一事实源：测试显式创建 ready 店铺配置，
#: 指纹与 _config() 的稳定身份（business_id=222）保持一致。
_STORE_CURRENCY_FINGERPRINT = compute_currency_fingerprint(
    "yandex", "222", "RUB", ["RUB"], "locked", "account_api"
)


@pytest.fixture(autouse=True)
def _ready_store_currency(monkeypatch) -> None:
    install_logistics(monkeypatch)
    seed_store_currency(
        "yandex",
        "RUB",
        identity={"campaign_id": "111", "business_id": "222"},
    )

#: 测试类目定义（当次临时规则，不再持久化进草稿）。
_CATEGORY_SCHEMA: dict[str, Any] = {
    "required": [
        {
            "id": "85",
            "name": "类型",
            "required": True,
            "dictionary_id": "1234",
        },
    ],
    "optional": [
        {
            "id": "9048",
            "name": "重量",
            "required": False,
            "unit_options": ["г", "кг"],
        },
    ],
}


def _record(schema: dict[str, Any] | None = None, category_id: str = "91596") -> dict[str, Any]:
    return record_from_schema(
        platform="yandex",
        category_id=category_id,
        schema=schema if schema is not None else _CATEGORY_SCHEMA,
        category_path="Тестовая категория",
    )


def _config(**store_overrides: Any) -> dict[str, Any]:
    store: dict[str, Any] = {
        "api_token": API_TOKEN,
        "campaign_id": "111",
        "business_id": "222",
        "shop_name": "示例店铺",
        "placement_type": "FBS",
        "stock_update_mode": "business",
        "warehouse_ids": ["9", "7"],
        "only_default_price": False,
        "auth_status": "测试成功",
    }
    store.update(store_overrides)
    return {"yandex": store}


def _draft(**overrides: Any) -> dict[str, Any]:
    basis = {
        "listing_currency": "RUB",
        "currency_fingerprint": _STORE_CURRENCY_FINGERPRINT,
        "shipping_evidence": {"fulfillment_plan": deepcopy(PLAN)},
        "cost_cny": "35",
        "weight_kg": "0.5",
        "length_cm": "20",
        "width_cm": "15",
        "height_cm": "10",
    }
    draft: dict[str, Any] = {
        "draft_id": "draft-y1",
        "product_id": "product-y1",
        "platform": "yandex",
        "site": "global",
        "title": "便携风扇",
        "brand": "BrandX",
        "description": "桌面便携风扇",
        "sku": "SKU-001",
        "category_id": "91596",
        "stock": "5",
        "images": [{"asset_id": "img-1", "role": "main", "order": 0}],
        "package_dimensions": {
            "weight_kg": "0.5",
            "length_cm": "20",
            "width_cm": "15",
            "height_cm": "10",
        },
        "pricing": {
            "targets": {
                "yandex:global": {
                    "applied_price": {"amount": "1299", "currency": "RUB"},
                    "listing_currency": "RUB",
                    "calculation_basis": basis,
                    "calculation_fingerprint": pricing_calculation_fingerprint(basis),
                }
            }
        },
        "attributes": {
            "85": {"values": [{"dictionary_value_id": "61573", "value": "настольный"}]},
        },
    }
    draft.update(overrides)
    if "target_sites" not in overrides:
        draft["target_sites"] = [
            {
                "platform": "yandex",
                "site": "global",
                "language": "ru-RU",
                "listing_currency": "RUB",
                "category_id": str(draft.get("category_id") or ""),
                "description_category_id": str(
                    draft.get("description_category_id") or ""
                ),
                "category_path": str(draft.get("category_path") or ""),
                "attributes": deepcopy(draft.get("attributes") or {}),
            }
        ]
    return draft


def _product(**draft_overrides: Any) -> dict[str, Any]:
    return sku_publish_fixture({
        "product_id": "product-y1",
        "name": "便携风扇",
        "brand": "BrandX",
        "drafts": {"yandex": _draft(**draft_overrides)},
        "source": {
            "image_pool": [
                {
                    "id": "img-1",
                    "url": "https://cdn.example.com/fan.jpg",
                    "origin": "source",
                    "platforms": ["yandex"],
                    "is_main": True,
                    "selected": True,
                    "order": 0,
                }
            ]
        },
    }, "yandex")


def _allow_poll(result: dict[str, Any]) -> dict[str, Any]:
    """清除 checkpoint 退避时间，便于测试立即推进下一步。"""

    result["result"]["checkpoint"]["next_poll_at"] = 0.0
    return result


def _inner(result: dict[str, Any]) -> dict[str, Any]:
    return result["result"]


def _checkpoint(result: dict[str, Any]) -> dict[str, Any]:
    return _inner(result)["checkpoint"]


class _Recorder:
    """记录各远端 mutation / 回读调用次数，并提供可配置响应。"""

    def __init__(self) -> None:
        self.calls: dict[str, list[tuple[Any, ...]]] = {}
        self.mapping_status = "PUBLISHED"
        # 官方 OfferCardStatusType 中不存在 "PUBLISHED"；HAS_CARD_CAN_UPDATE
        # 表示卡片可编辑、本次变更被接受。
        self.card_status = "HAS_CARD_CAN_UPDATE"
        self.quarantine: list[dict[str, Any]] = []

    def count(self, name: str) -> int:
        return len(self.calls.get(name, []))

    def _record(self, name: str, *args: Any) -> None:
        self.calls.setdefault(name, []).append(args)

    def update_offer_mapping(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._record("update_yandex_offer_mapping", *args)
        return {"status": "OK"}

    def update_campaign_offer(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._record("update_yandex_campaign_offer", *args)
        return {"status": "OK", "warnings": [{"message": " vat ok"}]}

    def update_price(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._record("update_yandex_price", *args)
        return {"status": "OK"}

    def update_stock(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._record("update_yandex_stock", *args)
        return {"status": "OK"}

    def fetch_mapping(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self._record("fetch_yandex_offer_mapping", *args)
        # 官方响应：cardStatus 位于 offerMappings[].offer.cardStatus。
        return [
            {
                "offer": {"offerId": "SKU-001", "cardStatus": self.card_status},
                "mapping": {"marketSku": 1},
            }
        ]

    def fetch_campaign_offer(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self._record("fetch_yandex_campaign_offer", *args)
        return [{"offerId": "SKU-001", "status": self.mapping_status}]

    def fetch_quarantine(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self._record("fetch_yandex_price_quarantine", *args)
        return deepcopy(self.quarantine)


@pytest.fixture()
def remote(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(publish_yandex, "update_yandex_offer_mapping", recorder.update_offer_mapping)
    monkeypatch.setattr(publish_yandex, "update_yandex_campaign_offer", recorder.update_campaign_offer)
    monkeypatch.setattr(publish_yandex, "update_yandex_price", recorder.update_price)
    monkeypatch.setattr(publish_yandex, "update_yandex_stock", recorder.update_stock)
    monkeypatch.setattr(publish_yandex, "fetch_yandex_offer_mapping", recorder.fetch_mapping)
    monkeypatch.setattr(publish_yandex, "fetch_yandex_campaign_offer", recorder.fetch_campaign_offer)
    monkeypatch.setattr(publish_yandex, "fetch_yandex_price_quarantine", recorder.fetch_quarantine)
    return recorder


def _drive_to_terminal(
    result: dict[str, Any],
    config: dict[str, Any],
    *,
    max_steps: int = 12,
) -> dict[str, Any]:
    current = _allow_poll(deepcopy(result))
    for _ in range(max_steps):
        current = (advance_yandex_submission if current.get("status") == "pending_submission" else poll_yandex_publish_status)(current, config)
        if str(current.get("status") or "") != "pending_submission":
            return poll_yandex_publish_status(current, config) if current.get("status") == "publish_pending_confirmation" else current
        current = _allow_poll(current)
    raise AssertionError("状态机未在限定步数内到达终态")


# ---------------------------------------------------------------- payload


def test_build_payload_is_deterministic_and_grouped() -> None:
    product = _product()
    first = build_yandex_publish_payload(product, _config(), _record())
    second = build_yandex_publish_payload(deepcopy(product), _config(), _record())
    assert json.dumps(first, sort_keys=True, ensure_ascii=False) == json.dumps(
        second, sort_keys=True, ensure_ascii=False
    )

    assert first["platform"] == "yandex"
    assert first["offer_id"] == "SKU-001"
    assert first["campaign_id"] == "111"
    assert first["business_id"] == "222"

    offer = first["catalog"]["offer"]
    assert offer["offerId"] == "SKU-001"
    assert offer["marketCategoryId"] == 91596
    assert offer["pictures"] == ["https://cdn.example.com/fan.jpg"]
    assert offer["vendor"] == "BrandX"
    # 官方 weightDimensions：重量千克、尺寸厘米，number 且允许小数
    assert offer["weightDimensions"] == {
        "weight": 0.5,
        "length": 20.0,
        "width": 15.0,
        "height": 10.0,
    }
    # 官方 parameterValues：value 为字符串，平台枚举值携带 valueId
    assert offer["parameterValues"] == [
        {"parameterId": 85, "valueId": 61573, "value": "настольный"}
    ]
    # 价格仅由独立步骤写入，不在目录接口重复设置账号级价格。
    assert "basicPrice" not in offer

    assert first["offer_conditions"]["offer"]["offerId"] == "SKU-001"
    assert first["price"]["level"] == "campaign"
    assert first["price"]["offers"] == [
        {
            "offerId": "SKU-001",
            "price": {"value": 1299, "currencyId": "RUR"},
        }
    ]
    # 只写入报价绑定仓库，不使用授权时的默认仓库
    assert first["stock"] == {
        "mode": "business",
        "warehouse_ids": ["31"],
        "fulfillment_plan": PLAN,
        "count": 5,
    }


def test_build_payload_business_price_level_when_only_default_price() -> None:
    payload = build_yandex_publish_payload(
        _product(),
        _config(only_default_price=True),
    _record(),)
    assert payload["price"]["level"] == "business"


@pytest.mark.parametrize("unique_count", [3, 31])
def test_payload_deduplicates_image_urls_before_limit_without_changing_draft(unique_count):
    product = _product()
    urls = [f"https://cdn.example.com/{index}.jpg" for index in range(unique_count)]
    # 两个不同素材引用同一上传结果；重复项位于首图后，不能挤占数量上限。
    source_urls = [urls[0], urls[0], *urls[1:]]
    product["source"]["image_pool"] = [
        {"id": f"img-{index}", "url": url, "origin": "source", "platforms": ["yandex"],
         "selected": True, "is_main": index == 0, "order": index}
        for index, url in enumerate(source_urls)
    ]
    product["drafts"]["yandex"]["images"] = [
        {"asset_id": f"img-{index}", "role": "main" if index == 0 else "gallery", "order": index}
        for index in range(len(source_urls))
    ]
    product["sku_items"][0]["image_asset_id"] = "img-0"
    original = deepcopy(product)

    payload = build_yandex_publish_payload(product, _config(), _record())

    assert payload["catalog"]["offer"]["pictures"] == urls[:30]
    assert product == original


def test_build_payload_multivalue_rows_and_unit_id() -> None:
    schema = {
        "required": [
            {
                "id": "31",
                "name": "Теги",
                "required": True,
                "dictionary_id": "yandex-parameter-31",
                "is_collection": True,
            }
        ],
        "optional": [
            {
                "id": "9048",
                "name": "Вес",
                "required": False,
                "unit_options": ["г", "кг"],
                "default_unit": "г",
                "unit_ids": {"г": "1", "кг": "2"},
            }
        ],
    }
    product = _product(
        attributes={
            "31": {
                "values": [
                    {"dictionary_value_id": "7", "value": "USB"},
                    {"dictionary_value_id": "8", "value": "тихий"},
                ]
            },
            "9048": {"value": "500", "unit": "кг"},
        },
    )

    payload = build_yandex_publish_payload(product, _config(), _record(schema))
    rows = payload["catalog"]["offer"]["parameterValues"]

    # 官方多值：多个共享 parameterId 的对象；单位携带 wire unitId。
    assert rows == [
        {"parameterId": 31, "valueId": 7, "value": "USB"},
        {"parameterId": 31, "valueId": 8, "value": "тихий"},
        {"parameterId": 9048, "value": "500", "unitId": 2},
    ]


def test_build_payload_open_enum_custom_value_and_default_unit() -> None:
    schema = {
        "required": [],
        "optional": [
            {"id": "44", "name": "Особенности", "required": False, "value_mode": "open_enum"},
            {
                "id": "9048",
                "name": "Вес",
                "required": False,
                "unit_options": ["г", "кг"],
                "default_unit": "г",
                "unit_ids": {"г": "1", "кг": "2"},
            },
        ],
    }
    product = _product(
        attributes={
            "44": "собственное значение",
            "9048": {"value": "500", "unit": "г"},
        },
    )

    payload = build_yandex_publish_payload(product, _config(), _record(schema))
    rows = payload["catalog"]["offer"]["parameterValues"]

    # 开放枚举自定义值只携带 value（不带 valueId）；默认单位也显式携带 unitId。
    assert rows == [
        {"parameterId": 44, "value": "собственное значение"},
        {"parameterId": 9048, "value": "500", "unitId": 1},
    ]


def test_build_payload_open_enum_collection_emits_one_row_per_value() -> None:
    schema = {
        "required": [],
        "optional": [
            {
                "id": "700001",
                "name": "Supported labels",
                "required": False,
                "value_mode": "open_enum",
                "is_collection": True,
                "options": ["Alpha", "Beta"],
            }
        ],
    }
    product = _product(
        attributes={
            "700001": {
                "values": [{"value": "Alpha"}, {"value": "Beta"}],
            }
        },
    )

    payload = build_yandex_publish_payload(product, _config(), _record(schema))

    assert payload["catalog"]["offer"]["parameterValues"] == [
        {"parameterId": 700001, "value": "Alpha"},
        {"parameterId": 700001, "value": "Beta"},
    ]


def test_build_payload_blocks_stale_non_default_unit() -> None:
    schema = {
        "required": [],
        "optional": [
            {
                "id": "9048",
                "name": "Вес",
                "required": False,
                "unit_options": ["г", "кг"],
                "default_unit": "г",
                # 缺少单位 ID 映射：非默认单位不得静默回落默认单位。
                "unit_ids": {},
            }
        ],
    }
    product = _product(
        attributes={"9048": {"value": "500", "unit": "кг"}},
    )

    with pytest.raises(ValueError, match="单位 ID"):
        build_yandex_publish_payload(product, _config(), _record(schema))


def test_build_payload_enforces_numeric_constraints() -> None:
    schema = {
        "required": [],
        "optional": [
            {
                "id": "9048",
                "name": "Вес",
                "required": False,
                "value_type": "numeric",
                "constraints": {"min_value": 1, "max_value": 100},
            }
        ],
    }
    product = _product(
        attributes={"9048": {"value": "500"}},
    )

    with pytest.raises(ValueError, match="不能大于"):
        build_yandex_publish_payload(product, _config(), _record(schema))


def test_build_payload_decimal_price_is_number() -> None:
    product = _product()
    product["drafts"]["yandex"]["sku_items"][0]["pricing"]["targets"]["yandex:global"][
        "applied_price"
    ] = {"amount": "1299.50", "currency": "RUB"}

    payload = build_yandex_publish_payload(product, _config(), _record())

    # 官方 price.value 为 JSON number（小数保持 float）。
    assert "basicPrice" not in payload["catalog"]["offer"]
    assert payload["price"]["offers"][0]["price"]["value"] == 1299.5
    serialized = json.dumps(payload, ensure_ascii=False)
    assert '"value": 1299.5' in serialized
    assert '"value": "1299.5"' not in serialized


def test_build_payload_fby_stock_mode_is_none() -> None:
    payload = build_yandex_publish_payload(
        _product(),
        _config(placement_type="FBY", stock_update_mode="", warehouse_ids=[]),
    _record(),)
    assert payload["stock"]["mode"] == "none"


def test_build_payload_rejects_invalid_inputs() -> None:
    # offerId 身份变化
    with pytest.raises(ValueError, match="SKU 已变化"):
        build_yandex_publish_payload(
            _product(last_publish_task={"offer_id": "OLD-SKU"}),
            _config(),
        _record(),)
    # 空 SKU 会被规范化为稳定 offerId（平台身份不允许为空），
    # 且多次规范化结果一致。
    generated_first = build_yandex_publish_payload(_product(sku=""), _config(), _record())
    generated_second = build_yandex_publish_payload(_product(sku=""), _config(), _record())
    assert generated_first["offer_id"] == generated_second["offer_id"]
    assert generated_first["offer_id"].startswith("SKU-")
    # 类目不是正整数
    with pytest.raises(ValueError, match="类目 ID"):
        build_yandex_publish_payload(_product(category_id="abc"), _config(), _record())
    # 缺图片：草稿引用与 canonical 图片池同时为空才构成缺图
    no_images = _product(images=[])
    no_images["source"]["image_pool"] = []
    with pytest.raises(ValueError, match="图片"):
        build_yandex_publish_payload(no_images, _config(), _record())
    # 库存写入方式不受支持
    with pytest.raises(ValueError, match="绑定不一致"):
        build_yandex_publish_payload(
            _product(),
            _config(stock_update_mode="warehouse_direct"),
        _record(),)
    # 仓库组不能落实报价中的单仓选择。
    with pytest.raises(ValueError, match="绑定不一致"):
        build_yandex_publish_payload(_product(), _config(stock_update_mode="campaign_warehouses"), _record())


def test_build_payload_requires_credentials() -> None:
    with pytest.raises(YandexApiError) as exc_info:
        build_yandex_publish_payload(_product(), _config(api_token=""), _record())
    assert exc_info.value.code == "YANDEX_CREDENTIALS_MISSING"
    assert exc_info.value.retryable is False

    with pytest.raises(YandexApiError) as exc_info:
        build_yandex_publish_payload(_product(), _config(business_id=""), _record())
    assert exc_info.value.code == "YANDEX_BUSINESS_ID_MISSING"


def test_validate_payload_flags_binding_and_picture_issues() -> None:
    payload = build_yandex_publish_payload(_product(), _config(), _record())
    assert validate_yandex_publish_payload(payload, _config()) == []

    # 店铺绑定变化
    tampered = deepcopy(payload)
    tampered["campaign_id"] = "999"
    errors = validate_yandex_publish_payload(tampered, _config())
    assert any("店铺身份已变化" in item for item in errors)

    # 非公网图片
    bad_picture = deepcopy(payload)
    bad_picture["catalog"]["offer"]["pictures"] = ["data:image/png;base64,xx"]
    errors = validate_yandex_publish_payload(bad_picture, _config())
    assert any("HTTPS 公网 URL" in item for item in errors)


# ------------------------------------------------------------ state machine


@pytest.mark.parametrize("only_default_price", [True, False])
def test_publish_writes_price_only_to_selected_price_endpoint(remote, monkeypatch, only_default_price):
    from erp_web.marketplaces import yandex_http

    requests = []

    def request(method, path, token, payload, **kwargs):
        requests.append((method, path, deepcopy(payload)))
        return {"status": "OK"}

    monkeypatch.setattr(yandex_http, "request_yandex_json", request)
    monkeypatch.setattr(publish_yandex, "update_yandex_offer_mapping", yandex_http.update_yandex_offer_mapping)
    monkeypatch.setattr(publish_yandex, "update_yandex_price", yandex_http.update_yandex_price)
    config = _config(only_default_price=only_default_price)
    payload = build_yandex_publish_payload(_product(), config, _record())
    assert validate_yandex_publish_payload(payload, config) == []

    result = _drive_to_terminal(publish_yandex_payload(payload, config), config)

    assert result["status"] == "real_publish_success"
    scope = "businesses/222" if only_default_price else "campaigns/111"
    assert requests == [
        ("POST", "/v2/businesses/222/offer-mappings/update",
         {"offerMappings": [{"offer": payload["catalog"]["offer"]}]}),
        ("POST", f"/v2/{scope}/offer-prices/updates",
         {"offers": [{"offerId": "SKU-001", "price": {"value": 1299, "currencyId": "RUR"}}]}),
    ]
    assert "basicPrice" not in requests[0][2]["offerMappings"][0]["offer"]


def test_saved_payload_with_basic_price_resumes_without_replaying_catalog(remote):
    config = _config(only_default_price=True)
    payload = build_yandex_publish_payload(_product(), config, _record())
    payload["catalog"]["offer"]["basicPrice"] = {"value": 1299, "currencyId": "RUR"}
    assert validate_yandex_publish_payload(payload, config) == []
    result = publish_yandex_payload(payload, config)
    saved = json.loads(json.dumps(result))
    restored = publish_yandex.restore_yandex_publish_result(saved)

    terminal = _drive_to_terminal(restored, config)

    assert terminal["status"] == "real_publish_success"
    assert remote.count("update_yandex_offer_mapping") == 1
    assert remote.count("update_yandex_price") == 1
    assert saved["result"]["plan"]["catalog_offer"]["basicPrice"] == payload["catalog"]["offer"]["basicPrice"]


def test_publish_executes_only_first_mutation(remote) -> None:
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())

    result = publish_yandex_payload(payload, config)

    assert result["ok"] is True
    assert result["status"] == "pending_submission"
    assert _inner(result)["status"] == "pending_submission"
    checkpoint = _checkpoint(result)
    assert checkpoint["completed_steps"] == ["offer_mapping"]
    assert checkpoint["phase"] == "campaign_offer"
    assert checkpoint["next_poll_at"] > time.time()
    assert remote.count("update_yandex_offer_mapping") == 1
    assert remote.count("update_yandex_campaign_offer") == 0
    assert remote.count("update_yandex_price") == 0
    assert remote.count("update_yandex_stock") == 0


def test_full_flow_success_without_repeating_steps(remote) -> None:
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())

    result = publish_yandex_payload(payload, config)
    result = _allow_poll(result)
    result = _allow_poll(advance_yandex_submission(result, config))
    assert _checkpoint(result)["completed_steps"] == ["offer_mapping", "campaign_offer"]

    # 模拟重启：直接拿持久化的 pending result 继续，不回退已完成步骤。
    resumed = deepcopy(result)
    terminal = _drive_to_terminal(resumed, config)

    assert terminal["ok"] is True
    assert terminal["status"] == "real_publish_success"
    assert terminal["offer_id"] == "SKU-001"
    assert terminal["external_id"] == "SKU-001"
    assert terminal["campaign_status"] == "PUBLISHED"
    assert remote.count("update_yandex_offer_mapping") == 1
    assert remote.count("update_yandex_campaign_offer") == 1
    assert remote.count("update_yandex_price") == 1
    assert remote.count("update_yandex_stock") == 1
    assert terminal['checkpoint']['evidence']['stock']['fulfillment_plan'] == PLAN
    assert terminal['checkpoint']['evidence']['stock']['warehouse_ids'] == ['31']
    # confirmation 是只读回读
    assert remote.count("fetch_yandex_offer_mapping") >= 1
    assert remote.count("fetch_yandex_campaign_offer") >= 1
    # warnings 从远端响应透传到终态
    assert terminal["warnings"] == [{"message": " vat ok"}]


def test_fby_stock_step_is_skipped_with_evidence(remote) -> None:
    config = _config(placement_type="FBY", stock_update_mode="", warehouse_ids=[])
    payload = build_yandex_publish_payload(_product(), config, _record())

    result = publish_yandex_payload(payload, config)
    terminal = _drive_to_terminal(result, config)

    assert terminal["status"] == "real_publish_success"
    assert remote.count("update_yandex_stock") == 0
    checkpoint = terminal["checkpoint"]
    assert checkpoint["evidence"]["stock"]["skipped"]
    assert "stock" in checkpoint["completed_steps"]


def test_retryable_error_backs_off_then_continues(remote, monkeypatch) -> None:
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())

    def flaky_mapping(*args: Any, **kwargs: Any) -> dict[str, Any]:
        recorder_calls.append(args)
        raise YandexApiError(
            "YANDEX_RATE_LIMITED",
            "Yandex 限流（420）",
            retryable=True,
            http_status=420,
        )

    recorder_calls: list[Any] = []
    monkeypatch.setattr(publish_yandex, "update_yandex_offer_mapping", flaky_mapping)

    result = publish_yandex_payload(payload, config)
    assert result["status"] == "pending_submission"
    checkpoint = _checkpoint(result)
    assert checkpoint["retries"] == 1
    assert checkpoint["next_poll_at"] > time.time()
    assert checkpoint["completed_steps"] == []

    # 退避窗口内轮询不发起远端请求
    before = len(recorder_calls)
    unchanged = advance_yandex_submission(result, config)
    assert len(recorder_calls) == before
    assert unchanged["status"] == "pending_submission"

    # 退避结束后重试仍然失败，继续累积退避
    retried = advance_yandex_submission(_allow_poll(deepcopy(result)), config)
    assert len(recorder_calls) == before + 1
    assert _checkpoint(retried)["retries"] == 2

    # 重试上限后转为终态失败
    exhausted = deepcopy(_allow_poll(retried))
    exhausted["result"]["checkpoint"]["retries"] = 8
    terminal = advance_yandex_submission(exhausted, config)
    assert terminal["status"] == "real_publish_failed"
    assert terminal["error_code"] == "YANDEX_RATE_LIMITED"


def test_deterministic_error_becomes_terminal_failure(remote, monkeypatch) -> None:
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())

    def denied(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise YandexApiError(
            "YANDEX_AUTH_INVALID",
            "Yandex API-Key 无效",
            retryable=False,
            http_status=401,
        )

    monkeypatch.setattr(publish_yandex, "update_yandex_offer_mapping", denied)

    terminal = publish_yandex_payload(payload, config)
    assert terminal["ok"] is False
    assert terminal["status"] == "real_publish_failed"
    assert terminal["error_code"] == "YANDEX_AUTH_INVALID"
    assert terminal["error_map"]["retryable"] is False


def test_store_binding_change_blocks_publish(remote) -> None:
    payload = build_yandex_publish_payload(_product(), _config(), _record())
    with pytest.raises(YandexApiError) as exc_info:
        publish_yandex_payload(payload, _config(campaign_id="999"))
    assert exc_info.value.code == "YANDEX_STORE_BINDING_CHANGED"

    result = publish_yandex_payload(payload, _config())
    with pytest.raises(YandexApiError) as exc_info:
        poll_yandex_publish_status(result, _config(business_id="888"))
    assert exc_info.value.code == "YANDEX_STORE_BINDING_CHANGED"


def test_confirmation_no_stocks_is_not_success(remote) -> None:
    remote.mapping_status = "NO_STOCKS"
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())

    terminal = _drive_to_terminal(publish_yandex_payload(payload, config), config)

    assert terminal["ok"] is False
    assert terminal["status"] == "real_publish_failed"
    assert terminal["error_code"] == "YANDEX_CAMPAIGN_NO_STOCKS"


def test_confirmation_rejected_status_is_failure(remote) -> None:
    remote.mapping_status = "REJECTED_BY_MARKET"
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())

    terminal = _drive_to_terminal(publish_yandex_payload(payload, config), config)

    assert terminal["status"] == "real_publish_failed"
    assert terminal["error_code"] == "YANDEX_CAMPAIGN_REJECTED_BY_MARKET"


def test_confirmation_quarantine_blocks_success(remote) -> None:
    # 官方 QuarantineOfferDTO：offerId 位于顶层，隔离原因在 verdicts[].params。
    remote.quarantine = [
        {
            "offerId": "SKU-001",
            "verdicts": [
                {
                    "type": "PRICE_CHANGE",
                    "params": [
                        {"name": "CURRENT_PRICE", "value": "1299"},
                        {"name": "LAST_VALID_PRICE", "value": "12990"},
                    ],
                }
            ],
        }
    ]
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())

    terminal = _drive_to_terminal(publish_yandex_payload(payload, config), config)

    assert terminal["ok"] is False
    assert terminal["error_code"] == "YANDEX_PRICE_QUARANTINED"
    assert terminal["checkpoint"]["evidence"]["quarantine"]["offerId"] == "SKU-001"


def test_confirmation_pending_remains_pending(remote) -> None:
    remote.mapping_status = "CHECKING"
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())

    terminal = _drive_to_terminal(publish_yandex_payload(payload, config), config)

    assert terminal["status"] == "publish_pending_confirmation"
    assert "error_code" not in terminal


def test_confirmation_edit_rejected_by_card_status_despite_published(remote) -> None:
    """编辑存量商品：Campaign 保持 PUBLISHED，但官方 cardStatus
    HAS_CARD_CAN_UPDATE_ERRORS 表示“修改未接受”，必须判定失败。"""

    remote.mapping_status = "PUBLISHED"
    remote.card_status = "HAS_CARD_CAN_UPDATE_ERRORS"
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())

    terminal = _drive_to_terminal(publish_yandex_payload(payload, config), config)

    assert terminal["ok"] is False
    assert terminal["status"] == "real_publish_failed"
    assert terminal["error_code"] == "YANDEX_CARD_UPDATE_REJECTED"
    assert (
        terminal["checkpoint"]["evidence"]["confirmation"]["card_status"]
        == "HAS_CARD_CAN_UPDATE_ERRORS"
    )


def test_confirmation_no_card_errors_is_failure(remote) -> None:
    remote.mapping_status = "CHECKING"
    remote.card_status = "NO_CARD_ERRORS"
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())

    terminal = _drive_to_terminal(publish_yandex_payload(payload, config), config)

    assert terminal["status"] == "real_publish_failed"
    assert terminal["error_code"] == "YANDEX_CARD_UPDATE_REJECTED"


def test_confirmation_card_processing_remains_pending(remote) -> None:
    """卡片变更审核中（HAS_CARD_CAN_UPDATE_PROCESSING）：即使 Campaign 已
    PUBLISHED 也不能确认本次变更生效，保持待确认，不能按等待次数判定失败。"""

    remote.mapping_status = "PUBLISHED"
    remote.card_status = "HAS_CARD_CAN_UPDATE_PROCESSING"
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())

    terminal = _drive_to_terminal(publish_yandex_payload(payload, config), config)

    assert terminal["status"] == "publish_pending_confirmation"
    assert "error_code" not in terminal


def test_confirmation_published_with_action_required_card_fails(remote) -> None:
    """Campaign PUBLISHED 但卡片处于“需在店铺放置商品”等非接受态。"""

    remote.mapping_status = "PUBLISHED"
    remote.card_status = "NO_CARD_ADD_TO_CAMPAIGN"
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())

    terminal = _drive_to_terminal(publish_yandex_payload(payload, config), config)

    assert terminal["status"] == "real_publish_failed"
    assert terminal["error_code"] == "YANDEX_CARD_STATUS_UNEXPECTED"


def test_confirmation_success_records_official_card_status(remote) -> None:
    remote.mapping_status = "PUBLISHED"
    remote.card_status = "HAS_CARD_CAN_UPDATE"
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())

    terminal = _drive_to_terminal(publish_yandex_payload(payload, config), config)

    assert terminal["ok"] is True
    assert terminal["status"] == "real_publish_success"
    assert (
        terminal["checkpoint"]["evidence"]["confirmation"]["card_status"]
        == "HAS_CARD_CAN_UPDATE"
    )


def test_build_payload_business_stock_requires_single_warehouse() -> None:
    """无分组仓库模式：单一库存数只能写入一个发布仓库。"""

    multiple = _config(
        stock_update_mode="business", warehouse_ids=["31", "32"]
    )
    assert build_yandex_publish_payload(_product(), multiple, _record())["stock"]["warehouse_ids"] == ["31"]
    empty = _config(stock_update_mode="business", warehouse_ids=[])
    assert build_yandex_publish_payload(_product(), empty, _record())["stock"]["warehouse_ids"] == ["31"]

    single = _config(stock_update_mode="business", warehouse_ids=["31"])
    payload = build_yandex_publish_payload(_product(), single, _record())
    assert payload["stock"] == {
        "mode": "business",
        "warehouse_ids": ["31"],
        "fulfillment_plan": PLAN,
        "count": 5,
    }
    assert validate_yandex_publish_payload(payload, single) == []

    # payload 级别同样拒绝多仓库 business 库存。
    broken = deepcopy(payload)
    broken["stock"]["warehouse_ids"] = ["31", "32"]
    errors = validate_yandex_publish_payload(broken, single)
    assert any("发布仓库" in item for item in errors)


def test_pending_result_never_leaks_credentials(remote) -> None:
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())
    result = publish_yandex_payload(payload, config)
    terminal = _drive_to_terminal(result, config)

    for blob in (
        json.dumps(result, ensure_ascii=False),
        json.dumps(terminal, ensure_ascii=False),
    ):
        assert API_TOKEN not in blob


def test_poll_requires_checkpoint() -> None:
    with pytest.raises(RuntimeError, match="checkpoint"):
        poll_yandex_publish_status({"result": {}}, _config())


# ---------------------------------------------------------------- helpers


def test_offer_identity_conflict_message() -> None:
    assert yandex_offer_identity_conflict(_draft()) == ""
    message = yandex_offer_identity_conflict(
        _draft(last_publish_task={"offer_id": "OLD"})
    )
    assert "OLD" in message and "SKU-001" in message


def test_required_and_invalid_attribute_helpers() -> None:
    product = _product()
    assert yandex_required_attributes_missing(product, _record()) == []

    missing = yandex_required_attributes_missing(
        _product(attributes={}),
        _record(),
    )
    assert missing == ["attributes.85"]

    invalid_enum = yandex_invalid_dictionary_attributes(
        _product(attributes={"85": {"values": [{"value": "手动输入"}]}}),
        _record(),
    )
    assert invalid_enum == ["85"]

    invalid_unit = yandex_invalid_unit_attributes(
        _product(attributes={"9048": {"value": "10", "unit": "磅"}}),
        _record(),
    )
    assert invalid_unit == ["9048"]
    assert yandex_invalid_unit_attributes(
        _product(attributes={"9048": {"value": "10", "unit": "кг"}}),
        _record(),
    ) == []


def test_map_yandex_publish_error() -> None:
    typed = YandexApiError(
        "YANDEX_VALIDATION_FAILED",
        "Yandex 校验失败",
        retryable=False,
        errors=[{"field": "offer.name", "message": "名称过短"}],
        details={"next_action": "修改标题后重试"},
    )
    mapped = map_yandex_publish_error(typed)
    assert mapped["error_code"] == "YANDEX_VALIDATION_FAILED"
    assert mapped["field_errors"] == {"offer.name": ["名称过短"]}
    assert mapped["retryable"] is False
    assert mapped["next_action"] == "修改标题后重试"

    generic = map_yandex_publish_error(RuntimeError("boom"))
    assert generic["error_code"] == "YANDEX_PUBLISH_FAILED"
    assert generic["retryable"] is False
    assert generic["summary"] == "boom"


# ------------------------------------------- PublishingBus 端到端集成


class _MemoryPublishJobStore:
    def __init__(self) -> None:
        import threading

        self.states: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create_publish_job(self, state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        with self._lock:
            self.states[str(state["job_id"])] = deepcopy(state)
        return deepcopy(state), True

    def save_publish_job(self, state: dict[str, Any]) -> None:
        with self._lock:
            self.states[str(state["job_id"])] = deepcopy(state)

    def load_publish_job(self, job_id: str) -> dict[str, Any]:
        return deepcopy(self.states.get(job_id, {}))

    def load_publish_job_by_idempotency_key(self, key: str) -> dict[str, Any]:
        return deepcopy(
            next(
                (state for state in self.states.values() if state.get("idempotency_key") == key),
                {},
            )
        )

    def list_pending_publish_jobs(self) -> list[dict[str, Any]]:
        return []

    def list_publish_jobs(self, **_kwargs: Any) -> tuple[list[dict[str, Any]], str]:
        return [], ""


def test_yandex_adapter_end_to_end_through_publishing_bus(remote) -> None:
    """真实适配器 + PublishingBus：确认绑定 → 状态机推进 → 终态回读。"""

    from erp_web.runtime_units.publish_adapter import YandexPublishingAdapter
    from erp_web.runtime_units.publish_confirmation import (
        canonical_publish_digest,
        resolve_publish_store_binding,
    )
    from erp_web.runtime_units.publishing_bus_core import PublishingBus

    config = _config(publish_step_interval_seconds=0.5)
    product = _product()
    payload = build_yandex_publish_payload(product, config, _record())
    binding = resolve_publish_store_binding("yandex", config)
    digest = canonical_publish_digest(
        product_id="product-y1",
        draft_id="draft-y1",
        platform="yandex",
        site="global",
        store_identity=binding.identity,
        payload=payload,
    )
    bus = PublishingBus(
        _MemoryPublishJobStore(),
        adapters={"yandex": YandexPublishingAdapter()},
        config_provider=lambda: deepcopy(config),
        auto_resume_pending=False,
    )
    try:
        queued = bus.enqueue(
            product,
            ["yandex"],
            targets={
                "yandex": {
                    "draft_id": "draft-y1",
                    "site": "global",
                    "product_id": "product-y1",
                }
            },
            idempotency_key="yandex-e2e:approved-1",
            approved_publications={
                "yandex": {
                    "payload": payload,
                    "validation_digest": digest,
                    "store_identity": binding.identity,
                }
            },
        )
        bus.wait(queued["job_id"], timeout=30)
        assert bus.get_status(queued["job_id"])["status"] == "pending_confirmation"
        bus.confirmation.check(queued["job_id"], "yandex")
        state = bus.get_status(queued["job_id"])
    finally:
        bus.close()

    platform_state = state["platforms"]["yandex"]
    assert platform_state["status"] == "success"
    result = platform_state["result"]
    assert result["status"] == "real_publish_success"
    assert result["offer_id"] == "SKU-001"
    assert result["external_id"] == "SKU-001"
    assert result["campaign_status"] == "PUBLISHED"
    # 每个 mutation 恰好一次，confirmation 只读
    assert remote.count("update_yandex_offer_mapping") == 1
    assert remote.count("update_yandex_campaign_offer") == 1
    assert remote.count("update_yandex_price") == 1
    assert remote.count("update_yandex_stock") == 1
    # 持久化 job 状态不允许包含凭据
    assert API_TOKEN not in json.dumps(state, ensure_ascii=False)


def test_yandex_bus_end_to_end_through_real_http_layer(monkeypatch) -> None:
    """真实贯通：builder → 真实 yandex_http wire 层（官方 fixture 路由）
    → PublishingBus 终态。除 urllib.urlopen 外不替换任何 HTTP wrapper，
    任何请求方法/路径/请求体不符合官方契约都会直接断言失败。"""

    import urllib.parse

    from erp_web.marketplaces import yandex_http
    from erp_web.runtime_units.publish_adapter import YandexPublishingAdapter
    from erp_web.runtime_units.publish_confirmation import (
        canonical_publish_digest,
        resolve_publish_store_binding,
    )
    from erp_web.runtime_units.publishing_bus_core import PublishingBus

    class _Response:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        def read(self, size: int = -1) -> bytes:
            return self._data

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *exc: Any) -> bool:
            return False

    routes: dict[str, Any] = {
        "POST /v2/businesses/222/offer-mappings/update": {"status": "OK"},
        "POST /v2/campaigns/111/offers/update": {"status": "OK"},
        "POST /v2/campaigns/111/offer-prices/updates": {
            "status": "OK",
            "result": {"status": "OK"},
        },
        "POST /v3/businesses/222/offers/stocks/update": {"status": "OK"},
        # 官方嵌套响应：cardStatus 位于 offerMappings[].offer.cardStatus，
        # 且使用官方枚举值（不存在 "PUBLISHED"）。
        "POST /v2/businesses/222/offer-mappings": {
            "status": "OK",
            "result": {
                "offerMappings": [
                    {
                        "offer": {
                            "offerId": "SKU-001",
                            "cardStatus": "HAS_CARD_CAN_UPDATE",
                        },
                        "mapping": {"marketSku": 90101},
                    }
                ]
            },
        },
        "POST /v2/campaigns/111/offers": {
            "status": "OK",
            "result": {
                "offers": [{"offerId": "SKU-001", "status": "PUBLISHED"}],
                "paging": {},
            },
        },
        # Campaign 级价格 → Campaign 级隔离区；空结果表示未命中隔离。
        "POST /v2/campaigns/111/price-quarantine": {
            "status": "OK",
            "result": {"offers": []},
        },
    }
    requests_made: list[Any] = []

    def fake_urlopen(request: Any, timeout: float | None = None) -> _Response:
        requests_made.append(request)
        parsed = urllib.parse.urlparse(request.full_url)
        key = f"{request.get_method()} {parsed.path}"
        assert key in routes, f"未声明的 Yandex 外发请求：{key}"
        return _Response(routes[key])

    monkeypatch.setattr(yandex_http.urllib.request, "urlopen", fake_urlopen)

    config = _config(publish_step_interval_seconds=0.5)
    product = _product()
    payload = build_yandex_publish_payload(product, config, _record())
    binding = resolve_publish_store_binding("yandex", config)
    digest = canonical_publish_digest(
        product_id="product-y1",
        draft_id="draft-y1",
        platform="yandex",
        site="global",
        store_identity=binding.identity,
        payload=payload,
    )
    bus = PublishingBus(
        _MemoryPublishJobStore(),
        adapters={"yandex": YandexPublishingAdapter()},
        config_provider=lambda: deepcopy(config),
        auto_resume_pending=False,
    )
    try:
        queued = bus.enqueue(
            product,
            ["yandex"],
            targets={
                "yandex": {
                    "draft_id": "draft-y1",
                    "site": "global",
                    "product_id": "product-y1",
                }
            },
            idempotency_key="yandex-e2e:wire-1",
            approved_publications={
                "yandex": {
                    "payload": payload,
                    "validation_digest": digest,
                    "store_identity": binding.identity,
                }
            },
        )
        bus.wait(queued["job_id"], timeout=30)
        assert bus.get_status(queued["job_id"])["status"] == "pending_confirmation"
        bus.confirmation.check(queued["job_id"], "yandex")
        state = bus.get_status(queued["job_id"])
    finally:
        bus.close()

    platform_state = state["platforms"]["yandex"]
    assert platform_state["status"] == "success"
    result = platform_state["result"]
    assert result["status"] == "real_publish_success"
    assert result["campaign_status"] == "PUBLISHED"

    # wire contract：按请求断言 method/path/query/body。
    def _request(key: str) -> Any:
        method, path = key.split(" ", 1)
        found = [
            request
            for request in requests_made
            if request.get_method() == method
            and urllib.parse.urlparse(request.full_url).path == path
        ]
        assert found, f"未发出预期的 Yandex 请求：{key}"
        return found[0]

    def _body(request: Any) -> dict[str, Any]:
        return json.loads(request.data.decode("utf-8")) if request.data else {}

    mapping_update = _request("POST /v2/businesses/222/offer-mappings/update")
    assert _body(mapping_update)["offerMappings"][0]["offer"]["offerId"] == "SKU-001"

    price_update = _request("POST /v2/campaigns/111/offer-prices/updates")
    assert _body(price_update)["offers"][0]["price"] == {
        "value": 1299,
        "currencyId": "RUR",
    }

    stocks = _request("POST /v3/businesses/222/offers/stocks/update")
    assert _body(stocks) == {
        "skuItems": [{"sku": "SKU-001", "partnerWarehouseId": 31, "count": 5}]
    }

    readback = _request("POST /v2/campaigns/111/offers")
    assert _body(readback) == {"offerIds": ["SKU-001"]}
    # 指定 SKU 回读不得携带分页参数。
    assert urllib.parse.urlparse(readback.full_url).query == ""

    quarantine = _request("POST /v2/campaigns/111/price-quarantine")
    assert _body(quarantine) == {"offerIds": ["SKU-001"]}

    evidence = result["checkpoint"]["evidence"]["confirmation"]
    assert evidence["card_status"] == "HAS_CARD_CAN_UPDATE"
    assert API_TOKEN not in json.dumps(state, ensure_ascii=False)


def test_yandex_bus_rejects_confirmation_without_business_id(remote) -> None:
    """store_binding_fields 契约：缺 business_id 时确认绑定必须失败。"""

    from erp_web.runtime_units.publish_confirmation import (
        resolve_publish_store_binding,
    )

    with pytest.raises(ValueError, match="business_id"):
        resolve_publish_store_binding("yandex", _config(business_id=""))


# ------------------------------------------------- 草稿预检 validate_yandex_draft


def _validatable_product(**draft_overrides: Any) -> dict[str, Any]:
    return _product(model="XF-01", language="ru", **draft_overrides)


def test_validate_yandex_draft_passes_for_complete_draft() -> None:
    from erp_web.runtime_units.publish_validation import validate_yandex_draft

    result = validate_yandex_draft(_validatable_product(), _config(), _record())
    assert result["platform"] == "yandex"
    assert result["ok"] is True, result["errors"]
    assert result["errors"] == []


@pytest.mark.parametrize("image_stage", ["source", "final"])
def test_forbidden_category_blocks_precheck_and_final_validation(image_stage) -> None:
    from erp_web.runtime_units.publish_validation import validate_yandex_draft
    from tests.test_forbidden_categories import save_list

    product = _validatable_product()
    assert validate_yandex_draft(product, _config(), _record(), image_stage=image_stage)["ok"] is True
    save_list()
    result = validate_yandex_draft(product, _config(), _record(), image_stage=image_stage)
    assert result["ok"] is False
    issue = next(item for item in result["errors"] if item["code"] == "CATEGORY_FORBIDDEN")
    assert issue["field"] == "category_id" and "91596" in issue["message"] and "禁售" in issue["message"]
    save_list("123", "另一个类目")
    assert validate_yandex_draft(product, _config(), _record(), image_stage=image_stage)["ok"] is True


@pytest.mark.parametrize("image_stage", ["source", "final"])
def test_forbidden_category_blocks_all_selected_skus_with_one_list_read(image_stage, monkeypatch) -> None:
    from erp_web.context import get_context
    from erp_web.runtime_units.publish_adapter import publishing_adapter_for
    from erp_web.runtime_units.publish_context import PreparedPublishContext
    from erp_web.schemas.category_definition import CategoryDefinition
    from tests.test_forbidden_categories import save_list

    save_list()
    product = _validatable_product()
    draft = product["drafts"]["yandex"]
    product["sku_items"].append({**deepcopy(product["sku_items"][0]), "id": "second-sku"})
    draft["sku_items"].append({**deepcopy(draft["sku_items"][0]), "sku_id": "second-sku", "sku": "SECOND-OFFER"})
    read = get_context().config.forbidden_categories
    calls = []
    def load():
        calls.append(True)
        return read()
    monkeypatch.setattr(get_context().config, "forbidden_categories", load)
    context = PreparedPublishContext(product=product, draft=draft, target=draft["target_sites"][0], platform="yandex",
                                     category_definition=CategoryDefinition(platform="yandex", category_id="91596"))
    result = publishing_adapter_for("yandex").validate_draft(context, _config(), image_stage=image_stage)
    assert result["ok"] is False
    errors = [item for item in result["errors"] if item["code"] == "CATEGORY_FORBIDDEN"]
    assert len(errors) == 1
    assert len(errors[0]["affected_sku_ids"]) == 2
    assert len(calls) == 1


def _error_codes(result: dict[str, Any]) -> list[str]:
    return [str(item.get("code") or "") for item in result.get("errors") or []]


def test_validate_yandex_draft_blocks_unverified_auth_states() -> None:
    from erp_web.runtime_units.publish_validation import validate_yandex_draft

    # (额外 store 覆盖, 期望错误码)。auth_status 经 _auth_status_label 归纳：
    # “Token 过期/权限不足/被限流”由 auth_status=测试失败 + error_code/message 派生。
    cases = [
        ({"api_token": "", "auth_status": ""}, "AUTH_NOT_CONFIGURED"),
        ({"auth_status": "已保存，未测试"}, "AUTH_NOT_CONFIGURED"),
        ({"auth_status": "测试失败"}, "AUTH_NOT_CONFIGURED"),
        ({"auth_status": "测试失败", "auth_error_code": "token_expired"}, "AUTH_TOKEN_EXPIRED"),
        ({"auth_status": "测试失败", "auth_error_message": "401 unauthorized"}, "AUTH_NOT_CONFIGURED"),
        ({"auth_status": "测试失败", "auth_error_code": "420"}, "AUTH_NOT_CONFIGURED"),
    ]
    for overrides, expected_code in cases:
        result = validate_yandex_draft(
            _validatable_product(),
            _config(**overrides),
        _record(),)
        codes = _error_codes(result)
        assert result["ok"] is False, overrides
        assert expected_code in codes, (overrides, codes)


def test_validate_yandex_draft_requires_business_id_after_auth_success() -> None:
    from erp_web.runtime_units.publish_validation import validate_yandex_draft

    result = validate_yandex_draft(
        _validatable_product(),
        _config(business_id=""),
    _record(),)
    assert "AUTH_DETAIL_MISSING" in _error_codes(result)


def test_validate_yandex_draft_flags_category_schema_and_identity() -> None:
    from erp_web.runtime_units.publish_validation import validate_yandex_draft

    invalid_category = validate_yandex_draft(
        _validatable_product(category_id="yandex-category-1"),
        _config(),
    _record(),)
    assert "CATEGORY_INVALID" in _error_codes(invalid_category)

    identity = validate_yandex_draft(
        _validatable_product(last_publish_task={"offer_id": "OLD-SKU"}),
        _config(),
    _record(),)
    assert "OFFER_IDENTITY_CHANGED" in _error_codes(identity)


def test_validate_yandex_draft_dedups_required_and_invalid_enum() -> None:
    from erp_web.runtime_units.publish_validation import validate_yandex_draft

    # 必填属性给了手动输入值：只报 ATTRIBUTE_DICTIONARY_VALUE_REQUIRED，
    # 不再重复报 REQUIRED_ATTRIBUTE_MISSING。
    result = validate_yandex_draft(
        _validatable_product(
            attributes={"85": {"values": [{"value": "手动输入"}]}}
        ),
        _config(),
    _record(),)
    codes = _error_codes(result)
    assert "ATTRIBUTE_DICTIONARY_VALUE_REQUIRED" in codes
    assert "REQUIRED_ATTRIBUTE_MISSING" not in codes

    missing = validate_yandex_draft(_validatable_product(attributes={}), _config(), _record())
    assert "REQUIRED_ATTRIBUTE_MISSING" in _error_codes(missing)

    invalid_unit = validate_yandex_draft(
        _validatable_product(
            attributes={"9048": {"value": "10", "unit": "磅"}}
        ),
        _config(),
    _record(),)
    assert "ATTRIBUTE_UNIT_INVALID" in _error_codes(invalid_unit)


def test_validate_yandex_draft_rejects_non_public_images() -> None:
    from erp_web.runtime_units.publish_validation import validate_yandex_draft

    product = _validatable_product()
    product["source"]["image_pool"][0]["url"] = "data:image/png;base64,xx"
    result = validate_yandex_draft(product, _config(), _record())
    assert "IMAGE_NOT_PUBLIC" in _error_codes(result)


def test_validate_yandex_draft_requires_package_dimensions_and_price() -> None:
    from erp_web.runtime_units.publish_validation import validate_yandex_draft

    no_dimensions = validate_yandex_draft(
        _validatable_product(
            package_dimensions={"weight_kg": "0", "length_cm": "", "width_cm": "", "height_cm": ""}
        ),
        _config(),
    _record(),)
    codes = _error_codes(no_dimensions)
    assert "PACKAGE_DIMENSIONS_MISSING" in codes
    assert "WEIGHT_MISSING" in codes

    # 核价指纹缺失 → PRICING_STALE
    stale_pricing = {
        "targets": {
            "yandex:global": {
                "applied_price": {"amount": "1299", "currency": "RUB"},
                "listing_currency": "RUB",
            }
        }
    }
    pricing_result = validate_yandex_draft(
        _validatable_product(pricing=stale_pricing),
        _config(),
    _record(),)
    assert "PRICING_STALE" in _error_codes(pricing_result)


def test_yandex_optional_parameters_can_be_omitted_but_required_attributes_still_block() -> None:
    from erp_web.runtime_units.publish_validation import validate_yandex_draft

    optional_record = _record({"required": [], "optional": [{"id": "85", "name": "类型", "required": False}]})
    for attrs in ({}, {"主营": "宠物用品"}):
        product = _validatable_product(attributes=attrs)
        check = validate_yandex_draft(product, _config(), optional_record)
        assert check["ok"] is True, check["errors"]
        payload = build_yandex_publish_payload(product, _config(), optional_record)
        assert "parameterValues" not in payload["catalog"]["offer"]
        assert validate_yandex_publish_payload(payload, _config()) == []
        payload["catalog"]["offer"]["parameterValues"] = []
        assert any("parameterValues" in message for message in validate_yandex_publish_payload(payload, _config()))

        required = validate_yandex_draft(product, _config(), _record())
        assert "REQUIRED_ATTRIBUTE_MISSING" in _error_codes(required)

    payload = build_yandex_publish_payload(_product(), _config(), _record())
    assert payload["catalog"]["offer"]["parameterValues"]


def test_yandex_model_is_required_only_by_category() -> None:
    from erp_web.runtime_units.publish_validation import validate_yandex_draft

    product = _product()
    product["model"] = ""
    assert validate_yandex_draft(product, _config(), _record())["ok"] is True
    record = _record({"required": [{"id": "123", "name": "Модель", "required": True}], "optional": []})
    check = validate_yandex_draft(product, _config(), record)
    assert any(item["field"] == "attributes.123" for item in check["errors"])
    assert "MODEL_MISSING" not in _error_codes(check)


def test_other_characteristics_format_blocks_precheck_and_payload_and_allows_omission():
    from erp_web.runtime_units.category_providers import _yandex_parameter_definition
    from erp_web.runtime_units.publish_validation import validate_yandex_draft

    schema = deepcopy(_CATEGORY_SCHEMA)
    schema["optional"].append(_yandex_parameter_definition({
        "parameter_id": "57046341", "parameter_type": "TEXT", "name": "Прочие характеристики",
    }))
    record = _record(schema)
    attrs = {"85": {"values": [{"dictionary_value_id": "123", "value": "风扇"}]},
             "57046341": "Материал: плюш\n整组款式概括描述"}
    checked = validate_yandex_draft(_validatable_product(attributes=attrs), _config(), record)
    issue = next(item for item in checked["errors"] if item["code"] == "ATTRIBUTE_TEXT_FORMAT_INVALID")
    assert issue["field"] == "attributes.57046341"
    assert "第 2 行" in issue["message"]
    with pytest.raises(ValueError, match="第 2 行缺少英文冒号"):
        build_yandex_publish_payload(_product(attributes=attrs), _config(), record)

    for invalid in ({"values": [{"value": "用途: 玩耍"}]}, ["用途: 玩耍"]):
        attrs["57046341"] = invalid
        with pytest.raises(ValueError, match="每行一个"):
            build_yandex_publish_payload(_product(attributes=attrs), _config(), record)

    attrs["57046341"] = "Назначение: для игр\nИсточник: https://example.com/a:b"
    payload = build_yandex_publish_payload(_product(attributes=attrs), _config(), record)
    wire = next(v for v in payload["catalog"]["offer"]["parameterValues"] if v["parameterId"] == 57046341)
    assert wire["value"] == attrs["57046341"]
    assert "ATTRIBUTE_TEXT_FORMAT_INVALID" not in _error_codes(validate_yandex_draft(_validatable_product(attributes=attrs), _config(), record))
    del attrs["57046341"]
    assert "ATTRIBUTE_TEXT_FORMAT_INVALID" not in _error_codes(validate_yandex_draft(_validatable_product(attributes=attrs), _config(), record))


def test_migrated_description_reaches_yandex_catalog():
    product = _product()
    draft = product["drafts"]["yandex"]
    draft["bullets"] = ["Компактный корпус"]
    migrated = migrate_draft_description(draft)
    product["drafts"]["yandex"] = migrated
    payload = build_yandex_publish_payload(product, _config(), _record())
    assert payload["catalog"]["offer"]["description"] == migrated["description"]
    assert "Компактный корпус" in payload["catalog"]["offer"]["description"]


def test_yandex_confirmation_never_advances_unfinished_write_steps(remote):
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())
    result = publish_yandex_payload(payload, config)
    with pytest.raises(ValueError, match="不会执行写操作"):
        poll_yandex_publish_status(result, config)
    assert remote.count("update_yandex_offer_mapping") == 1
    assert remote.count("update_yandex_campaign_offer") == 0


def test_yandex_restores_old_pending_checkpoint_as_unfinished_submission(remote):
    config = _config()
    payload = build_yandex_publish_payload(_product(), config, _record())
    result = publish_yandex_payload(payload, config)
    result["status"] = "publish_pending_confirmation"
    result["result"]["status"] = "pending_confirmation"
    restored = publish_yandex.restore_yandex_publish_result(result)
    assert restored["status"] == "pending_submission"
    terminal = _drive_to_terminal(restored, config)
    assert terminal["status"] == "real_publish_success"
    assert remote.count("update_yandex_offer_mapping") == 1


def test_payload_cannot_change_quoted_warehouse(remote):
    payload = build_yandex_publish_payload(_product(), _config(), _record())
    payload['stock']['warehouse_ids'] = ['9']
    result = publish_yandex_payload(payload, _config())
    assert not result['ok']
    assert result['error_code'] == 'YANDEX_LOGISTICS_CHANGED'
    assert not remote.calls


@pytest.mark.parametrize("warehouse_id", [31, 32])
def test_quoted_warehouse_reaches_stock_write(remote, monkeypatch, warehouse_id):
    from erp_web.services.yandex_logistics_service import fingerprint
    plan = {**PLAN, "publish_warehouse_id": warehouse_id, "publish_warehouse_name": f"发布仓 {warehouse_id}"}
    plan.pop("fingerprint")
    plan["fingerprint"] = fingerprint(plan)
    install_logistics(monkeypatch, [plan])
    pricing = deepcopy(_draft()["pricing"])
    quote = pricing["targets"]["yandex:global"]
    quote["calculation_basis"]["shipping_evidence"]["fulfillment_plan"] = plan
    quote["calculation_fingerprint"] = pricing_calculation_fingerprint(quote["calculation_basis"])
    calls = []
    def stock(*args, **kwargs):
        calls.append(kwargs)
        return remote.update_stock(*args, **kwargs)
    monkeypatch.setattr(publish_yandex, "update_yandex_stock", stock)
    config = _config(warehouse_ids=["9", "7"])
    payload = build_yandex_publish_payload(_product(pricing=pricing), config, _record())
    terminal = _drive_to_terminal(publish_yandex_payload(payload, config), config)
    assert terminal["ok"]
    assert len(calls) == 1
    assert calls[0]["warehouse_ids"] == [str(warehouse_id)]
    assert calls[0]["count"] == 5


def test_changed_logistics_blocks_next_mutation(remote):
    from erp_web.context import get_context
    payload = build_yandex_publish_payload(_product(), _config(), _record())
    result = publish_yandex_payload(payload, _config())
    assert remote.count('update_yandex_offer_mapping') == 1
    get_context().yandex_logistics.current.clear()
    result = advance_yandex_submission(_allow_poll(result), _config())
    assert not result['ok']
    assert result['error_code'] == 'YANDEX_LOGISTICS_CHANGED'
    assert remote.count('update_yandex_campaign_offer') == 0
    assert remote.count('update_yandex_stock') == 0


def test_legacy_quote_without_plan_must_be_recalculated():
    draft = _draft()
    draft['pricing']['targets']['yandex:global']['calculation_basis'].pop('shipping_evidence')
    with pytest.raises(ValueError, match='物流方案'):
        build_yandex_publish_payload(_product(**draft), _config(), _record())


def _group_validation_fixture():
    from erp_web.runtime_units.publish_context import PreparedPublishContext
    from tests.publish_category_support import definition_from_record

    product = _validatable_product()
    draft = product["drafts"]["yandex"]
    draft["grouping"] = {"mode": "separate", "name": "风扇"}
    fact, row = product["sku_items"][0], draft["sku_items"][0]
    quote = row["pricing"]["targets"]["yandex:global"]
    basis = quote["calculation_basis"]
    basis.update(domestic_freight_cny=0, packaging_cost_cny=0, other_cost_cny=0,
                 commission_percent=10, commission_evidence={
                     "source": "platform_api", "purpose": "prelisting", "endpoint": "/v2/tariffs/calculate",
                     "category_id": "91596", "price": {"amount": "1299", "currency": "RUB"},
                     "amount": "129.9", "currency": "RUB", "quoted_at": "2026-10-22T00:00:00Z",
                 })
    quote["calculation_fingerprint"] = pricing_calculation_fingerprint(basis)
    product["sku_items"] = [{**deepcopy(fact), "id": f"fact-{index}"} for index in range(10)]
    draft["sku_items"] = [{**deepcopy(row), "sku_id": f"fact-{index}", "sku": f"SELL-{index}"} for index in range(10)]
    return PreparedPublishContext(product=product, draft=draft, target=draft["target_sites"][0],
                                  platform="yandex", category_definition=definition_from_record(_record()))


def test_group_validation_shares_reads_and_refreshes_on_next_call(monkeypatch):
    from unittest.mock import Mock
    from erp_web.context import get_context
    from erp_web.runtime_units import publish_validation, sku_publish_adapter
    from erp_web.runtime_units.publish_adapter import YandexPublishingAdapter

    context = _group_validation_fixture()
    original = deepcopy(context.product)
    app = get_context()
    normalize = Mock(wraps=sku_publish_adapter.normalize_product_fields)
    monkeypatch.setattr(sku_publish_adapter, "normalize_product_fields", normalize)
    monkeypatch.setattr(publish_validation, "normalize_product_fields", lambda *_: pytest.fail("单 SKU 不应重复规范化整份商品"))
    config_read = Mock(wraps=app.config.load_store_config)
    monkeypatch.setattr(app.config, "load_store_config", config_read)
    logistics = Mock(wraps=app.yandex_logistics.validate)
    monkeypatch.setattr(app.yandex_logistics, "validate", logistics)
    inspect = Mock(wraps=app.image_delivery._inspect_image)
    monkeypatch.setattr(app.image_delivery, "_inspect_image", inspect)
    adapter = sku_publish_adapter.SkuGroupPublishingAdapter(YandexPublishingAdapter())
    result = adapter.validate_draft(context, _config(), image_stage="source")
    assert result["ok"] is True, result["errors"]
    assert "sku_results" not in result
    assert normalize.call_count == config_read.call_count == logistics.call_count == inspect.call_count == 1
    assert context.product == original

    app.yandex_logistics.current = []
    result = adapter.validate_draft(context, _config(), image_stage="source")
    assert "YANDEX_LOGISTICS_INVALID" in _error_codes(result)
    assert normalize.call_count == config_read.call_count == logistics.call_count == inspect.call_count == 2
    assert len(next(item for item in result["errors"] if item["code"] == "YANDEX_LOGISTICS_INVALID")["affected_sku_ids"]) == 10


@pytest.mark.parametrize("change", ["cost", "dimensions", "fingerprint", "stock", "attribute"])
def test_group_validation_retains_per_sku_blockers(change):
    from erp_web.runtime_units.publish_adapter import YandexPublishingAdapter
    from erp_web.runtime_units.sku_publish_adapter import SkuGroupPublishingAdapter

    context = _group_validation_fixture()
    row = context.draft["sku_items"][1]
    if change == "cost":
        context.product["sku_items"][1]["cost_cny"] = "36"
    elif change == "dimensions":
        context.product["sku_items"][1]["package_dimensions"]["weight_kg"] = "0.6"
    elif change == "fingerprint":
        row["pricing"]["targets"]["yandex:global"]["calculation_fingerprint"] = "bad"
    elif change == "stock":
        row["stock"] = "-1"
    else:
        row["attributes_by_target"] = {"yandex:global": {"85": {"values": []}}}
    result = SkuGroupPublishingAdapter(YandexPublishingAdapter()).validate_draft(context, _config(), image_stage="source")
    assert result["ok"] is False
    assert {sku_id for issue in result["errors"] for sku_id in issue["affected_sku_ids"]} == {"fact-1"}
    if change in {"cost", "dimensions", "stock"}:
        assert len(result["errors"]) == 1


@pytest.mark.parametrize("constraints,accepted", [
    ({"min_value": 0, "max_value": 100}, True),
    ({"min_value": "", "max_value": ""}, True),
    ({"min_value": None, "max_value": None}, True),
    ({"min_value": 1, "max_value": 100}, False),
    ({"min_value": "invalid"}, False),
    ({"min_value": "NaN"}, False),
])
def test_numeric_constraints_keep_zero_and_match_precheck(constraints, accepted):
    from erp_web.runtime_units.publish_validation import validate_yandex_draft
    from erp_web.runtime_units.category_definition_support import definition_to_legacy_record
    from tests.publish_category_support import definition_from_record

    record = _record({"required": [], "optional": [{
        "id": "9048", "name": "数值", "value_type": "numeric", "constraints": constraints,
    }]})
    record = definition_to_legacy_record(definition_from_record(record))
    if constraints.get("min_value") == 0:
        assert record["attributes"]["optional"][0]["constraints"]["min_value"] == "0"
    product = _validatable_product(attributes={"9048": {"value": "0"}})
    check = validate_yandex_draft(product, _config(), record)
    assert check["ok"] is accepted, check["errors"]
    if accepted:
        payload = build_yandex_publish_payload(product, _config(), record)
        assert payload["catalog"]["offer"]["parameterValues"] == [{"parameterId": 9048, "value": "0"}]
    else:
        assert any(error["field"] == "attributes.9048" for error in check["errors"])
        with pytest.raises(ValueError):
            build_yandex_publish_payload(product, _config(), record)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "-1", "101"])
def test_numeric_constraints_reject_nonfinite_and_out_of_range_before_upload(value):
    from erp_web.runtime_units.publish_validation import validate_yandex_draft
    record = _record({"required": [], "optional": [{
        "id": "9048", "name": "数值", "value_type": "numeric", "constraints": {"min_value": 0, "max_value": 100},
    }]})
    product = _validatable_product(attributes={"9048": {"value": value}})
    check = validate_yandex_draft(product, _config(), record)
    assert not check["ok"]
    assert any(error["field"] == "attributes.9048" for error in check["errors"])
    with pytest.raises(ValueError):
        build_yandex_publish_payload(product, _config(), record)



def test_unfilled_optional_enum_does_not_become_a_precheck_requirement():
    from erp_web.runtime_units.publish_validation import validate_yandex_draft
    record = _record({"required": [], "optional": [{
        "id": "85", "name": "选填类型", "required": False, "dictionary_id": "1234",
    }]})
    product = _validatable_product(attributes={})
    assert validate_yandex_draft(product, _config(), record)["ok"] is True
    assert "parameterValues" not in build_yandex_publish_payload(product, _config(), record)["catalog"]["offer"]


@pytest.mark.parametrize("unit", ["HOUR", "DAY", "WEEK", "MONTH", "YEAR"])
def test_guarantee_period_reaches_official_payload(unit):
    period = {"timePeriod": "12", "timeUnit": unit, "comment": "Гарантия продавца"}
    payload = build_yandex_publish_payload(_product(guarantee_period=period), _config(), _record())
    assert payload["catalog"]["offer"]["guaranteePeriod"] == {**period, "timePeriod": 12}
    assert not validate_yandex_publish_payload(payload, _config())


def test_unset_guarantee_is_not_invented():
    payload = build_yandex_publish_payload(_product(), _config(), _record())
    assert "guaranteePeriod" not in payload["catalog"]["offer"]


@pytest.mark.parametrize("period", [
    {"timePeriod": "-1", "timeUnit": "MONTH"},
    {"timePeriod": "1.5", "timeUnit": "MONTH"},
    {"timePeriod": True, "timeUnit": "MONTH"},
    {"timePeriod": 12},
    {"timeUnit": "MONTH"},
    {"timePeriod": 12, "timeUnit": "invalid"},
    {"timePeriod": 12, "timeUnit": "MONTH", "comment": "长" * 501},
])
def test_invalid_guarantee_blocks_precheck_and_payload(period):
    from erp_web.runtime_units.publish_validation import validate_yandex_draft
    product = _product(guarantee_period=period)
    precheck = validate_yandex_draft(product, _config(), _record(), image_stage="source")
    assert any(issue["code"] == "YANDEX_GUARANTEE_PERIOD_INVALID" for issue in precheck["errors"])
    with pytest.raises(ValueError, match="保修"):
        build_yandex_publish_payload(product, _config(), _record())
