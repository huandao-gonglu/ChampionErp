# -*- coding: utf-8 -*-
"""Yandex Market HTTP 边界的 wire-contract 测试。

本文件直接拦截 ``urllib.request.urlopen``，对每个 wrapper 断言官方
method/path/query/header/body，并用官方响应 fixture 断言解析结果；
不允许只验证上层函数的调用次数。契约依据 Yandex Market Partner API
官方文档（见 docs/yandex-market-edit-publish-acceptance-report.md
第 10 节链接）。
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from typing import Any, Callable

import pytest

from erp_web.marketplaces import yandex_http
from erp_web.marketplaces.yandex_http import (
    YandexApiError,
    fetch_yandex_campaign,
    fetch_yandex_campaign_offer,
    fetch_yandex_category_parameters,
    fetch_yandex_category_tree,
    fetch_yandex_offer_mapping,
    fetch_yandex_partner_warehouses,
    fetch_yandex_price_quarantine,
    fetch_yandex_token_info,
    fetch_yandex_warehouses,
    request_yandex_json,
    update_yandex_campaign_offer,
    update_yandex_offer_mapping,
    update_yandex_price,
    update_yandex_stock,
)

API_TOKEN = "secret-api-token-xyz"

Handler = Callable[[Any], Any]


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _YandexHarness:
    """按 ``METHOD /path`` 路由伪造响应，并记录全部外发请求。"""

    def __init__(self) -> None:
        self.requests: list[Any] = []
        self._routes: dict[str, Any] = {}

    def route(self, key: str, response: Any) -> None:
        self._routes[key] = response

    def reset(self) -> None:
        """清空已记录的请求与路由，便于参数化用例复用同一 harness。"""

        self.requests = []
        self._routes = {}

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_urlopen(request: Any, timeout: float | None = None) -> _FakeResponse:
            self.requests.append(request)
            parsed = urllib.parse.urlparse(request.full_url)
            key = f"{request.get_method()} {parsed.path}"
            if key not in self._routes:
                raise AssertionError(f"未声明的 Yandex 外发请求：{key}")
            handler = self._routes[key]
            body = handler(request) if callable(handler) else handler
            if isinstance(body, Exception):
                raise body
            return _FakeResponse(body)

        monkeypatch.setattr(yandex_http.urllib.request, "urlopen", fake_urlopen)

    def request(self, index: int = -1) -> Any:
        return self.requests[index]

    @staticmethod
    def body(request: Any) -> dict[str, Any]:
        return json.loads(request.data.decode("utf-8")) if request.data else {}

    @staticmethod
    def query(request: Any) -> dict[str, list[str]]:
        parsed = urllib.parse.urlparse(request.full_url)
        return urllib.parse.parse_qs(parsed.query)

    @staticmethod
    def path(request: Any) -> str:
        return urllib.parse.urlparse(request.full_url).path


def _http_error(status: int, detail: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.partner.market.yandex.ru",
        status,
        "failure",
        {},
        io.BytesIO(detail.encode("utf-8")),
    )


@pytest.fixture()
def harness(monkeypatch: pytest.MonkeyPatch) -> _YandexHarness:
    harness = _YandexHarness()
    harness.install(monkeypatch)
    return harness


# ---------------------------------------------------------- transport


def test_request_sends_api_key_header_and_json(harness) -> None:
    harness.route("POST /v2/auth/token", {"status": "OK", "result": {}})

    request_yandex_json("POST", "/v2/auth/token", API_TOKEN, {"language": "RU"})

    request = harness.request()
    assert request.get_method() == "POST"
    assert request.full_url == "https://api.partner.market.yandex.ru/v2/auth/token"
    assert request.get_header("Api-key") == API_TOKEN
    assert request.get_header("Content-type") == "application/json"
    assert harness.body(request) == {"language": "RU"}


def test_request_requires_token_without_network(harness) -> None:
    with pytest.raises(YandexApiError) as exc_info:
        request_yandex_json("POST", "/v2/auth/token", " ", {})
    assert exc_info.value.code == "YANDEX_CREDENTIALS_MISSING"
    assert harness.requests == []


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "YANDEX_AUTH_INVALID", False),
        # token 端点的 403 才是方法权限问题。
        (403, "YANDEX_PERMISSION_DENIED", False),
        # 非 Campaign 端点的 404 不再套用 CAMPAIGN_NOT_FOUND。
        (404, "YANDEX_NOT_FOUND", False),
        (420, "YANDEX_RATE_LIMITED", True),
        (429, "YANDEX_RATE_LIMITED", True),
        (500, "YANDEX_SERVER_ERROR", True),
        (503, "YANDEX_SERVER_ERROR", True),
    ],
)
def test_http_errors_are_classified(harness, status, code, retryable) -> None:
    harness.route("POST /v2/auth/token", _http_error(status))

    with pytest.raises(YandexApiError) as exc_info:
        request_yandex_json("POST", "/v2/auth/token", API_TOKEN, {})

    assert exc_info.value.code == code
    assert exc_info.value.retryable is retryable
    assert exc_info.value.http_status == status


def test_http_error_message_masks_api_key(harness) -> None:
    harness.route(
        "POST /v2/auth/token",
        _http_error(400, f"bad request for token {API_TOKEN}"),
    )

    with pytest.raises(YandexApiError) as exc_info:
        request_yandex_json("POST", "/v2/auth/token", API_TOKEN, {})

    assert API_TOKEN not in str(exc_info.value)
    assert exc_info.value.retryable is False


def _forbidden_body() -> str:
    # 现场真实 403 响应体。
    return json.dumps(
        {"errors": [{"code": "FORBIDDEN", "message": "Access denied"}]},
        ensure_ascii=False,
    )


def test_campaign_403_forbidden_is_not_scope_error(harness) -> None:
    """Campaign 端点 403 FORBIDDEN：提示 Campaign ID 与 Token 不匹配，
    而不是笼统的“API-Key 权限不足”。"""

    harness.route(
        "GET /v2/campaigns/149191519", _http_error(403, _forbidden_body())
    )

    with pytest.raises(YandexApiError) as exc_info:
        fetch_yandex_campaign(API_TOKEN, "149191519")

    assert exc_info.value.code == "YANDEX_CAMPAIGN_ACCESS_DENIED"
    message = str(exc_info.value)
    assert "不属于当前 API-Key 所在柜台" in message
    assert "Campaign ID" in message and "Business ID" in message
    assert "权限不足" not in message
    # HTTPError 响应体中的平台 errors[] 必须被保留。
    assert exc_info.value.errors == [
        {"code": "FORBIDDEN", "message": "Access denied"}
    ]
    assert exc_info.value.details.get("next_action")
    assert API_TOKEN not in message


def test_campaign_404_mentions_campaign_business_id_confusion(harness) -> None:
    harness.route("GET /v2/campaigns/999", _http_error(404))

    with pytest.raises(YandexApiError) as exc_info:
        fetch_yandex_campaign(API_TOKEN, "999")

    assert exc_info.value.code == "YANDEX_CAMPAIGN_NOT_FOUND"
    message = str(exc_info.value)
    assert "Campaign ID" in message and "Business ID" in message


def test_fetch_campaign_parses_top_level_campaign(harness) -> None:
    """官方真实响应：campaign 位于顶层，无 result 包装。"""

    harness.route(
        "GET /v2/campaigns/149191519",
        {
            "campaign": {
                "id": 149191519,
                "business": {"id": 216920295},
                "placementType": "FBS",
                "apiAvailability": "AVAILABLE",
            }
        },
    )

    campaign = fetch_yandex_campaign(API_TOKEN, "149191519")

    request = harness.request()
    assert request.get_method() == "GET"
    assert harness.path(request) == "/v2/campaigns/149191519"
    assert campaign["id"] == 149191519
    assert campaign["business"]["id"] == 216920295
    assert campaign["placementType"] == "FBS"
    assert campaign["apiAvailability"] == "AVAILABLE"


def test_403_on_warehouses_points_to_inventory_scope(harness) -> None:
    harness.route(
        "POST /v2/businesses/222/warehouses",
        _http_error(403, _forbidden_body()),
    )

    with pytest.raises(YandexApiError) as exc_info:
        fetch_yandex_warehouses(API_TOKEN, "222")

    assert exc_info.value.code == "YANDEX_PERMISSION_DENIED"
    assert "INVENTORY_AND_ORDER_PROCESSING" in str(exc_info.value)
    assert exc_info.value.errors[0]["code"] == "FORBIDDEN"


def test_403_on_token_endpoint_points_to_method_permission(harness) -> None:
    harness.route("POST /v2/auth/token", _http_error(403, _forbidden_body()))

    with pytest.raises(YandexApiError) as exc_info:
        request_yandex_json("POST", "/v2/auth/token", API_TOKEN, {})

    assert exc_info.value.code == "YANDEX_PERMISSION_DENIED"
    message = str(exc_info.value)
    assert "缺少所请求方法的权限" in message
    # 平台错误码/消息被保留在 errors[]，且不泄漏 Token。
    assert exc_info.value.errors[0]["code"] == "FORBIDDEN"
    assert API_TOKEN not in message


def test_http_error_preserves_masked_errors_and_warnings(harness) -> None:
    body = json.dumps(
        {
            "errors": [
                {"code": "INVALID_DATA", "message": f"invalid token {API_TOKEN}"}
            ],
            "warnings": [{"code": "W1", "message": f"check {API_TOKEN}"}],
        },
        ensure_ascii=False,
    )
    harness.route(
        "POST /v2/businesses/222/offer-mappings/update", _http_error(400, body)
    )

    with pytest.raises(YandexApiError) as exc_info:
        request_yandex_json(
            "POST", "/v2/businesses/222/offer-mappings/update", API_TOKEN, {}
        )

    assert exc_info.value.code == "YANDEX_HTTP_FAILED"
    # errors[]/warnings[] 保留且逐字段脱敏。
    assert exc_info.value.errors[0]["code"] == "INVALID_DATA"
    assert API_TOKEN not in exc_info.value.errors[0]["message"]
    assert "***" in exc_info.value.errors[0]["message"]
    assert exc_info.value.warnings[0]["code"] == "W1"
    assert API_TOKEN not in exc_info.value.warnings[0]["message"]
    serialized = json.dumps(
        {"errors": exc_info.value.errors, "warnings": exc_info.value.warnings},
        ensure_ascii=False,
    )
    assert API_TOKEN not in serialized


def test_business_error_in_http_200_body(harness) -> None:
    harness.route(
        "POST /v2/businesses/222/offer-mappings/update",
        {
            "status": "ERROR",
            "errors": [{"code": "INVALID_ORDER", "message": "offer 无效"}],
        },
    )

    with pytest.raises(YandexApiError) as exc_info:
        request_yandex_json(
            "POST", "/v2/businesses/222/offer-mappings/update", API_TOKEN, {}
        )

    assert exc_info.value.code == "INVALID_ORDER"
    assert exc_info.value.retryable is False
    assert "offer 无效" in str(exc_info.value)


# ---------------------------------------------------------- auth token


def test_fetch_token_info_official_shape(harness) -> None:
    harness.route(
        "POST /v2/auth/token",
        {
            "status": "OK",
            "result": {
                "apiKey": {
                    "name": "erp-publish-key",
                    "authScopes": ["OFFERS_AND_CARDS_MANAGEMENT", "PRICING"],
                }
            },
        },
    )

    info = fetch_yandex_token_info(API_TOKEN)

    request = harness.request()
    assert request.get_method() == "POST"
    assert harness.path(request) == "/v2/auth/token"
    assert harness.body(request) == {}
    # 与 YandexTokenInfo(extra='forbid') 字段完全一致，不得携带 raw。
    assert info == {
        "name": "erp-publish-key",
        "auth_scopes": ["OFFERS_AND_CARDS_MANAGEMENT", "PRICING"],
    }


# ---------------------------------------------------------- category tree


def test_fetch_category_tree_reads_root_node(harness) -> None:
    # 官方响应：result 本身就是根节点（CategoryDTO），没有 result.categories。
    harness.route(
        "POST /v2/categories/tree",
        {
            "status": "OK",
            "result": {
                "id": 0,
                "name": "Маркет",
                "children": [
                    {
                        "id": 100,
                        "name": "Электроника",
                        "children": [{"id": 1001, "name": "Вентиляторы"}],
                    }
                ],
            },
        },
    )

    tree = fetch_yandex_category_tree(API_TOKEN, language="ru")

    request = harness.request()
    assert harness.path(request) == "/v2/categories/tree"
    assert harness.body(request) == {"language": "RU"}
    assert len(tree) == 1
    root = tree[0]
    assert root["id"] == 0
    assert root["children"][0]["id"] == 100
    assert root["children"][0]["children"][0] == {"id": 1001, "name": "Вентиляторы"}


def test_fetch_category_tree_empty_result(harness) -> None:
    harness.route("POST /v2/categories/tree", {"status": "OK", "result": {}})
    assert fetch_yandex_category_tree(API_TOKEN) == []


def test_fetch_category_parameters_official_shape(harness) -> None:
    harness.route(
        "POST /v2/category/91596/parameters",
        {
            "status": "OK",
            "result": {
                "categoryId": 91596,
                "parameters": [
                    {
                        "id": 85,
                        "name": "Тип",
                        "type": "ENUM",
                        "required": True,
                        "multivalue": False,
                        "allowCustomValues": False,
                        "values": [{"id": 61573, "value": "настольный"}],
                    }
                ],
            },
        },
    )

    parameters = fetch_yandex_category_parameters(API_TOKEN, "91596", "222")

    request = harness.request()
    assert harness.path(request) == "/v2/category/91596/parameters"
    assert harness.query(request) == {"businessId": ["222"]}
    assert harness.body(request) == {"language": "RU"}
    assert parameters[0]["id"] == 85
    assert parameters[0]["multivalue"] is False
    assert parameters[0]["values"] == [{"id": 61573, "value": "настольный"}]


# ---------------------------------------------------------- offer mappings


def test_update_offer_mapping_uses_offer_mappings_wrapper(harness) -> None:
    harness.route(
        "POST /v2/businesses/222/offer-mappings/update", {"status": "OK"}
    )
    offer = {"offerId": "SKU-001", "name": "便携风扇"}

    update_yandex_offer_mapping(API_TOKEN, "222", offer)

    request = harness.request()
    assert request.get_method() == "POST"
    assert harness.path(request) == "/v2/businesses/222/offer-mappings/update"
    # 官方顶层必须是 offerMappings 数组；裸 offer 会被平台拒绝。
    assert harness.body(request) == {"offerMappings": [{"offer": offer}]}


def test_fetch_offer_mapping_returns_nested_offer(harness) -> None:
    harness.route(
        "POST /v2/businesses/222/offer-mappings",
        {
            "status": "OK",
            "result": {
                "paging": {"nextPageToken": ""},
                "offerMappings": [
                    {
                        "offer": {
                            "offerId": "SKU-001",
                            "cardStatus": "HAS_CARD_CAN_UPDATE",
                        },
                        "mapping": {"marketSku": 1001},
                        "showcaseUrls": [],
                    }
                ],
            },
        },
    )

    mappings = fetch_yandex_offer_mapping(API_TOKEN, "222", ["SKU-001"])

    request = harness.request()
    assert harness.body(request) == {"offerIds": ["SKU-001"]}
    # 官方 cardStatus 位于 offerMappings[].offer.cardStatus，必须保留嵌套。
    assert mappings[0]["offer"]["cardStatus"] == "HAS_CARD_CAN_UPDATE"


# ---------------------------------------------------------- campaign offers


def test_fetch_campaign_offer_by_ids_has_no_paging(harness) -> None:
    harness.route(
        "POST /v2/campaigns/111/offers",
        {
            "status": "OK",
            "result": {
                "paging": {},
                "offers": [{"offerId": "SKU-001", "status": "PUBLISHED"}],
            },
        },
    )

    offers = fetch_yandex_campaign_offer(API_TOKEN, "111", offer_ids=["SKU-001"])

    request = harness.request()
    assert harness.path(request) == "/v2/campaigns/111/offers"
    # 官方契约：offerIds 为 body 顶层字段；指定 SKU 时不得携带分页参数。
    assert harness.body(request) == {"offerIds": ["SKU-001"]}
    assert harness.query(request) == {}
    assert offers == [{"offerId": "SKU-001", "status": "PUBLISHED"}]


def test_fetch_campaign_offer_listing_uses_query_paging(harness) -> None:
    harness.route(
        "POST /v2/campaigns/111/offers",
        {"status": "OK", "result": {"offers": [], "paging": {}}},
    )

    fetch_yandex_campaign_offer(API_TOKEN, "111", limit=75, page_token="tok-2")

    request = harness.request()
    assert harness.body(request) == {}
    assert harness.query(request) == {"limit": ["75"], "pageToken": ["tok-2"]}


# ---------------------------------------------------------- campaign offer update


def test_update_campaign_offer_body(harness) -> None:
    harness.route("POST /v2/campaigns/111/offers/update", {"status": "OK"})

    update_yandex_campaign_offer(API_TOKEN, "111", {"offerId": "SKU-001"})

    request = harness.request()
    assert harness.path(request) == "/v2/campaigns/111/offers/update"
    assert harness.body(request) == {"offers": [{"offerId": "SKU-001"}]}


# ---------------------------------------------------------- prices


def test_update_price_business_and_campaign_paths(harness) -> None:
    harness.route("POST /v2/businesses/222/offer-prices/updates", {"status": "OK"})
    harness.route("POST /v2/campaigns/111/offer-prices/updates", {"status": "OK"})
    offers = [{"offerId": "SKU-001", "price": {"value": 1299, "currencyId": "RUR"}}]

    update_yandex_price(API_TOKEN, business_id="222", offers=list(offers))
    update_yandex_price(API_TOKEN, campaign_id="111", offers=list(offers))

    first, second = harness.requests
    assert harness.path(first) == "/v2/businesses/222/offer-prices/updates"
    assert harness.path(second) == "/v2/campaigns/111/offer-prices/updates"
    assert harness.body(first) == {"offers": offers}
    assert harness.body(second) == {"offers": offers}


def test_update_price_requires_target(harness) -> None:
    with pytest.raises(YandexApiError) as exc_info:
        update_yandex_price(API_TOKEN, offers=[])
    assert exc_info.value.code == "YANDEX_PRICE_TARGET_MISSING"
    assert harness.requests == []


def test_update_price_failed_result_raises(harness) -> None:
    harness.route(
        "POST /v2/campaigns/111/offer-prices/updates",
        {
            "status": "OK",
            "result": {"status": "FAILED"},
            "errors": [{"code": "PRICE_CURRENCY_INVALID", "message": "币种无效"}],
        },
    )

    with pytest.raises(YandexApiError) as exc_info:
        update_yandex_price(
            API_TOKEN,
            campaign_id="111",
            offers=[{"offerId": "SKU-001", "price": {"value": 1, "currencyId": "RUB"}}],
        )
    assert exc_info.value.code == "PRICE_CURRENCY_INVALID"


def test_price_quarantine_level_specific_paths(harness) -> None:
    harness.route(
        "POST /v2/businesses/222/price-quarantine",
        {"status": "OK", "result": {"offers": [], "paging": {}}},
    )
    harness.route(
        "POST /v2/campaigns/111/price-quarantine",
        {
            "status": "OK",
            "result": {
                "offers": [
                    {
                        "offerId": "SKU-001",
                        "verdicts": [
                            {
                                "type": "PRICE_CHANGE",
                                "params": [{"name": "CURRENT_PRICE", "value": "1299"}],
                            }
                        ],
                    }
                ],
                "paging": {},
            },
        },
    )

    business_rows = fetch_yandex_price_quarantine(
        API_TOKEN, business_id="222", offer_ids=["SKU-001"]
    )
    campaign_rows = fetch_yandex_price_quarantine(
        API_TOKEN, campaign_id="111", offer_ids=["SKU-001"]
    )

    first, second = harness.requests
    # 官方资源是 price-quarantine（不是 offer-prices/changes），按价格级别分流。
    assert harness.path(first) == "/v2/businesses/222/price-quarantine"
    assert harness.path(second) == "/v2/campaigns/111/price-quarantine"
    assert harness.body(first) == {"offerIds": ["SKU-001"]}
    assert harness.body(second) == {"offerIds": ["SKU-001"]}
    assert business_rows == []
    assert campaign_rows[0]["offerId"] == "SKU-001"
    assert campaign_rows[0]["verdicts"][0]["type"] == "PRICE_CHANGE"


def test_price_quarantine_requires_target(harness) -> None:
    with pytest.raises(YandexApiError) as exc_info:
        fetch_yandex_price_quarantine(API_TOKEN)
    assert exc_info.value.code == "YANDEX_QUARANTINE_TARGET_MISSING"


# ---------------------------------------------------------- stocks


def test_update_stock_campaign_warehouses_body(harness) -> None:
    harness.route("PUT /v2/campaigns/111/offers/stocks", {"status": "OK"})

    update_yandex_stock(
        API_TOKEN,
        mode="campaign_warehouses",
        campaign_id="111",
        business_id="222",
        warehouse_ids=["9", "7"],
        offer_id="SKU-001",
        count=5,
    )

    request = harness.request()
    assert request.get_method() == "PUT"
    assert harness.path(request) == "/v2/campaigns/111/offers/stocks"
    # 官方请求体：skus[].items 恰有一个元素，无 warehouseId。
    assert harness.body(request) == {
        "skus": [{"sku": "SKU-001", "items": [{"count": 5}]}]
    }


def test_update_stock_business_body_uses_sku_items(harness) -> None:
    harness.route(
        "POST /v3/businesses/222/offers/stocks/update", {"status": "OK"}
    )

    update_yandex_stock(
        API_TOKEN,
        mode="business",
        campaign_id="111",
        business_id="222",
        warehouse_ids=["31"],
        offer_id="SKU-001",
        count=3,
    )

    request = harness.request()
    assert request.get_method() == "POST"
    assert harness.path(request) == "/v3/businesses/222/offers/stocks/update"
    # 官方请求体：skuItems[] 每项携带 partnerWarehouseId；
    # 单一库存数只写入选定的唯一发布仓库。
    assert harness.body(request) == {
        "skuItems": [
            {"sku": "SKU-001", "partnerWarehouseId": 31, "count": 3},
        ]
    }


def test_update_stock_business_rejects_multiple_warehouses(harness) -> None:
    """把同一库存数复制到多个无分组仓库会放大可售库存，必须拒绝。"""

    with pytest.raises(YandexApiError) as exc_info:
        update_yandex_stock(
            API_TOKEN,
            mode="business",
            business_id="222",
            warehouse_ids=["31", "32"],
            offer_id="SKU-001",
            count=3,
        )
    assert exc_info.value.code == "YANDEX_STOCK_TARGET_MISSING"
    assert harness.requests == []


def test_update_stock_requires_target_for_mode(harness) -> None:
    with pytest.raises(YandexApiError) as exc_info:
        update_yandex_stock(
            API_TOKEN, mode="campaign_warehouses", offer_id="SKU-001", count=1
        )
    assert exc_info.value.code == "YANDEX_STOCK_TARGET_MISSING"

    with pytest.raises(YandexApiError) as exc_info:
        update_yandex_stock(
            API_TOKEN,
            mode="business",
            business_id="222",
            warehouse_ids=[],
            offer_id="SKU-001",
            count=1,
        )
    assert exc_info.value.code == "YANDEX_STOCK_TARGET_MISSING"

    with pytest.raises(YandexApiError) as exc_info:
        update_yandex_stock(API_TOKEN, mode="unknown", offer_id="SKU-001", count=1)
    assert exc_info.value.code == "YANDEX_STOCK_TARGET_MISSING"
    assert harness.requests == []


# ---------------------------------------------------------- warehouses


def test_fetch_warehouses_sends_campaign_ids_and_paginates(harness) -> None:
    pages = [
        {
            "status": "OK",
            "result": {
                "warehouses": [
                    {
                        "id": 9,
                        "name": "W1",
                        "campaignId": 111,
                        "express": False,
                        "groupInfo": {"id": 5, "name": "G1"},
                    }
                ],
                "paging": {"nextPageToken": "page-2"},
            },
        },
        {
            "status": "OK",
            "result": {
                "warehouses": [
                    {"id": 7, "name": "W2", "campaignId": 111, "express": False}
                ],
                "paging": {},
            },
        },
    ]

    def handler(request: Any) -> dict[str, Any]:
        query = _YandexHarness.query(request)
        if "pageToken" not in query:
            assert query == {"limit": ["30"]}
            return pages[0]
        assert query == {"limit": ["30"], "pageToken": ["page-2"]}
        return pages[1]

    harness.route("POST /v2/businesses/222/warehouses", handler)

    warehouses = fetch_yandex_warehouses(API_TOKEN, "222", campaign_ids=["111"])

    assert len(harness.requests) == 2
    for request in harness.requests:
        # 官方请求体支持 campaignIds；响应保留 groupInfo 供仓库组判定。
        assert harness.body(request) == {"campaignIds": [111]}
    assert [item["id"] for item in warehouses] == [9, 7]
    assert warehouses[0]["groupInfo"] == {"id": 5, "name": "G1"}
    assert "groupInfo" not in warehouses[1]


def test_fetch_partner_warehouses_v3(harness) -> None:
    harness.route(
        "POST /v3/businesses/222/warehouses",
        {
            "status": "OK",
            "result": {
                "warehouses": [
                    {
                        "id": 31,
                        "name": "P1",
                        "models": [{"placementType": "FBS", "apiAvailability": "AVAILABLE"}],
                    }
                ],
                "paging": {},
            },
        },
    )

    warehouses = fetch_yandex_partner_warehouses(API_TOKEN, "222")

    request = harness.request()
    assert request.get_method() == "POST"
    assert harness.path(request) == "/v3/businesses/222/warehouses"
    assert harness.body(request) == {}
    assert warehouses[0]["id"] == 31
    assert warehouses[0]["models"][0]["apiAvailability"] == "AVAILABLE"


# ---------------------------------------------------------- auth flows


def _campaign_response(placement_type: str = "FBS", availability: str = "AVAILABLE") -> dict[str, Any]:
    # 官方 GET /v2/campaigns/{campaignId}：campaign 是顶层字段，
    # 没有 status/result 包装。
    return {
        "campaign": {
            "id": 111,
            "business": {"id": 222, "name": "Example Business"},
            "domain": "example-shop.market",
            "placementType": placement_type,
            "apiAvailability": availability,
        }
    }


def _token_response(scopes: list[str]) -> dict[str, Any]:
    return {
        "status": "OK",
        "result": {"apiKey": {"name": "erp-key", "authScopes": scopes}},
    }


def _install_auth_routes(
    harness: _YandexHarness,
    *,
    scopes: list[str] | None = None,
    campaign: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> None:
    harness.route("POST /v2/auth/token", _token_response(scopes or ["ALL_METHODS"]))
    harness.route("GET /v2/campaigns/111", campaign or _campaign_response())
    harness.route(
        "POST /v2/businesses/222/settings",
        settings or {"status": "OK", "result": {"settings": {"onlyDefaultPrice": False}}},
    )


def test_yandex_auth_success_with_warehouse_group(harness) -> None:
    from erp_web.runtime_units.store_credentials import _test_yandex_auth

    _install_auth_routes(harness)
    harness.route(
        "POST /v2/businesses/222/warehouses",
        {
            "status": "OK",
            "result": {
                "warehouses": [
                    {
                        "id": 9,
                        "name": "W1",
                        "campaignId": 111,
                        "express": False,
                        "groupInfo": {"id": 5, "name": "G1"},
                    },
                    {
                        "id": 7,
                        "name": "W2",
                        "campaignId": 111,
                        "express": False,
                        "groupInfo": {"id": 5, "name": "G1"},
                    },
                ],
                "paging": {},
            },
        },
    )

    config: dict[str, Any] = {"yandex": {"api_token": API_TOKEN, "campaign_id": "111"}}
    result = _test_yandex_auth(config, "")

    assert result["business_id"] == "222"
    assert result["placement_type"] == "FBS"
    assert result["api_availability"] == "AVAILABLE"
    assert result["api_key_name"] == "erp-key"
    assert result["auth_scopes"] == ["ALL_METHODS"]
    # 存在 groupInfo → 仓库组 → Campaign 级库存接口。
    assert result["stock_update_mode"] == "campaign_warehouses"
    assert result["warehouse_ids"] == [9, 7]
    store = config["yandex"]
    assert store["auth_status"] == "测试成功"
    assert store["business_id"] == "222"
    assert store["capabilities_verified_at"]
    # 仓库探测请求携带 campaignIds。
    warehouse_request = next(
        request
        for request in harness.requests
        if harness.path(request) == "/v2/businesses/222/warehouses"
    )
    assert harness.body(warehouse_request) == {"campaignIds": [111]}


def test_yandex_auth_success_without_group_uses_business_mode(harness) -> None:
    from erp_web.runtime_units.store_credentials import _test_yandex_auth

    _install_auth_routes(harness)
    # v2 无 groupInfo（或空）→ v3 仓库 → business 模式。
    harness.route(
        "POST /v2/businesses/222/warehouses",
        {"status": "OK", "result": {"warehouses": [], "paging": {}}},
    )
    harness.route(
        "POST /v3/businesses/222/warehouses",
        {
            "status": "OK",
            "result": {
                "warehouses": [
                    {
                        "id": 31,
                        "name": "P1",
                        "models": [
                            {"placementType": "FBS", "apiAvailability": "AVAILABLE"}
                        ],
                    },
                    {
                        "id": 32,
                        "name": "P2",
                        "models": [
                            {"placementType": "FBS", "apiAvailability": "AVAILABLE"}
                        ],
                    },
                ],
                "paging": {},
            },
        },
    )

    config: dict[str, Any] = {"yandex": {"api_token": API_TOKEN, "campaign_id": "111"}}
    result = _test_yandex_auth(config, "")

    assert result["stock_update_mode"] == "business"
    # 草稿只有单一库存数：只选定唯一发布仓库（确定性最小 id），
    # 绝不把同一库存复制到所有无分组仓库。
    assert result["warehouse_ids"] == [31]
    v3_request = next(
        request
        for request in harness.requests
        if harness.path(request) == "/v3/businesses/222/warehouses"
    )
    assert harness.query(v3_request) == {"limit": ["30"]}


def test_yandex_auth_rejects_warehouses_without_usable_models(harness) -> None:
    """models: [] 或非 AVAILABLE / 模型不匹配的仓库不得作为库存写入目标。"""

    from erp_web.runtime_units.store_credentials import _test_yandex_auth

    cases = [
        # models 为空：旧实现曾把它当成授权成功。
        [{"id": 31, "name": "P1", "models": []}],
        # API 被手动禁用。
        [
            {
                "id": 31,
                "name": "P1",
                "models": [
                    {"placementType": "FBS", "apiAvailability": "MANUALLY_DISABLED"}
                ],
            }
        ],
        # 仓库只支持 DBS，而店铺投放模型是 FBS。
        [
            {
                "id": 31,
                "name": "P1",
                "models": [
                    {"placementType": "DBS", "apiAvailability": "AVAILABLE"}
                ],
            }
        ],
    ]
    for warehouses in cases:
        harness.reset()
        _install_auth_routes(harness)
        harness.route(
            "POST /v2/businesses/222/warehouses",
            {"status": "OK", "result": {"warehouses": [], "paging": {}}},
        )
        harness.route(
            "POST /v3/businesses/222/warehouses",
            {"status": "OK", "result": {"warehouses": warehouses, "paging": {}}},
        )
        config: dict[str, Any] = {
            "yandex": {"api_token": API_TOKEN, "campaign_id": "111"}
        }
        with pytest.raises(RuntimeError, match="AVAILABLE"):
            _test_yandex_auth(config, "")
        assert config["yandex"].get("auth_status") != "测试成功"


def test_fetch_partner_warehouses_v3_paginates(harness) -> None:
    """官方默认每页 15、上限 30；必须按 nextPageToken 读取全部页。"""

    pages = [
        {
            "status": "OK",
            "result": {
                "warehouses": [
                    {
                        "id": 1,
                        "name": "W1",
                        "models": [
                            {"placementType": "FBS", "apiAvailability": "AVAILABLE"}
                        ],
                    }
                ],
                "paging": {"nextPageToken": "page-2"},
            },
        },
        {
            "status": "OK",
            "result": {
                "warehouses": [
                    {
                        "id": 2,
                        "name": "W2",
                        "models": [
                            {"placementType": "DBS", "apiAvailability": "AVAILABLE"}
                        ],
                    }
                ],
                "paging": {},
            },
        },
    ]
    state = {"page": 0}

    def handler(request: Any) -> dict[str, Any]:
        page = state["page"]
        state["page"] += 1
        return pages[page]

    harness.route("POST /v3/businesses/222/warehouses", handler)

    warehouses = fetch_yandex_partner_warehouses(API_TOKEN, "222")

    assert [item["id"] for item in warehouses] == [1, 2]
    assert len(harness.requests) == 2
    assert harness.query(harness.requests[0]) == {"limit": ["30"]}
    assert harness.query(harness.requests[1]) == {
        "limit": ["30"],
        "pageToken": ["page-2"],
    }


def test_yandex_auth_fby_skips_warehouse_probe(harness) -> None:
    from erp_web.runtime_units.store_credentials import _test_yandex_auth

    _install_auth_routes(harness, campaign=_campaign_response(placement_type="FBY"))

    config: dict[str, Any] = {"yandex": {"api_token": API_TOKEN, "campaign_id": "111"}}
    result = _test_yandex_auth(config, "")

    assert result["stock_update_mode"] == "none"
    assert result["warehouse_ids"] == []
    assert not any(
        "warehouses" in harness.path(request) for request in harness.requests
    )


def test_yandex_auth_missing_scopes_fails(harness) -> None:
    from erp_web.runtime_units.store_credentials import _test_yandex_auth

    harness.route(
        "POST /v2/auth/token", _token_response(["ORDERS_MANAGEMENT"])
    )

    config: dict[str, Any] = {"yandex": {"api_token": API_TOKEN, "campaign_id": "111"}}
    with pytest.raises(RuntimeError, match="权限不足"):
        _test_yandex_auth(config, "")
    assert config["yandex"].get("auth_status") != "测试成功"


def test_yandex_auth_rejects_legacy_minimal_scopes_without_inventory(harness) -> None:
    """旧的最小权限集缺少库存 scope：本地检查通过、仓库接口 403 的场景。

    仓库探测与库存写入属于 INVENTORY_AND_ORDER_PROCESSING 域，
    只有商品/价格权限的 token 必须在授权测试阶段被拦下。
    """

    from erp_web.runtime_units.store_credentials import _test_yandex_auth

    harness.route(
        "POST /v2/auth/token",
        _token_response(["OFFERS_AND_CARDS_MANAGEMENT", "PRICING"]),
    )

    config: dict[str, Any] = {"yandex": {"api_token": API_TOKEN, "campaign_id": "111"}}
    with pytest.raises(RuntimeError) as exc_info:
        _test_yandex_auth(config, "")
    assert "INVENTORY_AND_ORDER_PROCESSING" in str(exc_info.value)
    assert harness.requests and harness.path(harness.requests[-1]) == "/v2/auth/token"


def test_yandex_auth_campaign_403_propagates_id_hint(harness) -> None:
    """授权测试中 Campaign 端点 403 FORBIDDEN：保留类型化错误码并提示
    Campaign ID/Business ID 归属问题，不得退化为笼统的权限不足。"""

    from erp_web.runtime_units.store_credentials import _test_yandex_auth

    harness.route("POST /v2/auth/token", _token_response(["ALL_METHODS"]))
    harness.route("GET /v2/campaigns/111", _http_error(403, _forbidden_body()))

    config: dict[str, Any] = {"yandex": {"api_token": API_TOKEN, "campaign_id": "111"}}
    with pytest.raises(YandexApiError) as exc_info:
        _test_yandex_auth(config, "")

    assert exc_info.value.code == "YANDEX_CAMPAIGN_ACCESS_DENIED"
    message = str(exc_info.value)
    assert "Campaign ID" in message and "Business ID" in message
    assert "权限不足" not in message
    assert API_TOKEN not in message


def test_yandex_auth_minimal_scopes_pass(harness) -> None:
    """官方最小权限集（商品+价格+库存）应通过授权测试。"""

    from erp_web.runtime_units.store_credentials import _test_yandex_auth

    _install_auth_routes(
        harness,
        scopes=[
            "OFFERS_AND_CARDS_MANAGEMENT",
            "PRICING",
            "INVENTORY_AND_ORDER_PROCESSING",
        ],
    )
    harness.route(
        "POST /v2/businesses/222/warehouses",
        {
            "status": "OK",
            "result": {
                "warehouses": [
                    {
                        "id": 9,
                        "name": "W1",
                        "campaignId": 111,
                        "express": False,
                        "groupInfo": {"id": 5, "name": "G1"},
                    }
                ],
                "paging": {},
            },
        },
    )

    config: dict[str, Any] = {"yandex": {"api_token": API_TOKEN, "campaign_id": "111"}}
    result = _test_yandex_auth(config, "")

    assert result["auth_scopes"] == [
        "OFFERS_AND_CARDS_MANAGEMENT",
        "PRICING",
        "INVENTORY_AND_ORDER_PROCESSING",
    ]
    assert result["stock_update_mode"] == "campaign_warehouses"


def test_yandex_missing_publish_scopes_contract() -> None:
    from erp_web.marketplaces.yandex_http import (
        YANDEX_PUBLISH_SCOPES,
        yandex_missing_publish_scopes,
    )

    # 库存写入域必须包含在最小权限声明中。
    assert "INVENTORY_AND_ORDER_PROCESSING" in YANDEX_PUBLISH_SCOPES
    assert yandex_missing_publish_scopes(["ALL_METHODS"]) == []
    assert yandex_missing_publish_scopes(list(YANDEX_PUBLISH_SCOPES)) == []
    assert yandex_missing_publish_scopes(
        ["OFFERS_AND_CARDS_MANAGEMENT", "PRICING"]
    ) == ["INVENTORY_AND_ORDER_PROCESSING"]


def test_yandex_auth_unavailable_store_fails(harness) -> None:
    from erp_web.runtime_units.store_credentials import _test_yandex_auth

    _install_auth_routes(
        harness, campaign=_campaign_response(availability="MANUALLY_DISABLED")
    )

    config: dict[str, Any] = {"yandex": {"api_token": API_TOKEN, "campaign_id": "111"}}
    with pytest.raises(RuntimeError, match="店铺不可用"):
        _test_yandex_auth(config, "")


def test_yandex_auth_requires_credentials_without_network(harness) -> None:
    from erp_web.runtime_units.store_credentials import _test_yandex_auth

    with pytest.raises(RuntimeError, match="API-Key Token"):
        _test_yandex_auth({"yandex": {"api_token": "", "campaign_id": "111"}}, "")
    assert harness.requests == []


# ---------------------------------------------------------- category corpus


def test_flatten_leaf_categories_official_root_shape() -> None:
    from erp_web.runtime_units.yandex_category_api import _flatten_leaf_categories

    tree = [
        {
            "id": 0,
            "name": "Маркет",
            "children": [
                {
                    "id": 100,
                    "name": "Электроника",
                    "children": [
                        {"id": 1001, "name": "Вентиляторы", "children": []},
                        {"id": 1002, "name": "Обогреватели"},
                    ],
                },
                {"id": 200, "name": "Бытовая техника"},
            ],
        }
    ]

    records = _flatten_leaf_categories(tree)

    by_id = {record["category_id"]: record for record in records}
    # 叶子判定：无 children（或空 children）；中间节点与根节点不得入选。
    assert set(by_id) == {"1001", "1002", "200"}
    assert by_id["1001"]["name_original"] == "Вентиляторы"
    assert by_id["1001"]["category_path"] == "Маркет / Электроника / Вентиляторы"
    assert by_id["1001"]["parent_id"] == "100"
    assert by_id["1001"]["is_leaf"] is True
    # 官方根节点 id=0 是合成容器节点，不作为可发布类目父级参与记录。
    assert by_id["200"]["parent_id"] == ""


def test_normalize_yandex_parameter_official_shape() -> None:
    from erp_web.runtime_units.category_providers import _yandex_parameter_definition
    from erp_web.runtime_units.yandex_category_api import _normalize_yandex_parameter

    enum_parameter = _normalize_yandex_parameter(
        {
            "id": 85,
            "name": "Тип",
            "type": "ENUM",
            "required": True,
            "multivalue": False,
            "allowCustomValues": False,
            "values": [
                {"id": 61573, "value": "настольный"},
                {"id": 61574, "value": "напольный"},
            ],
        }
    )
    assert enum_parameter["is_collection"] is False
    assert enum_parameter["allow_custom_values"] is False
    assert enum_parameter["values"] == [
        {"value_id": "61573", "value": "настольный"},
        {"value_id": "61574", "value": "напольный"},
    ]
    shared_enum = _yandex_parameter_definition(enum_parameter)
    # allowCustomValues=false 的 ENUM 是严格枚举，禁止自由文本。
    assert shared_enum["value_mode"] == "strict_enum"
    assert shared_enum["values"][0]["id"] == "61573"

    open_parameter = _normalize_yandex_parameter(
        {
            "id": 31,
            "name": "Особенности",
            "type": "ENUM",
            "required": False,
            "multivalue": True,
            "allowCustomValues": True,
            "values": [{"id": 7, "value": "USB"}],
        }
    )
    assert open_parameter["is_collection"] is True
    assert open_parameter["allow_custom_values"] is True
    shared_open = _yandex_parameter_definition(open_parameter)
    # allowCustomValues=true 的 ENUM 是开放枚举，允许自定义文本。
    assert shared_open["value_mode"] == "open_enum"

    unit_parameter = _normalize_yandex_parameter(
        {
            "id": 9048,
            "name": "Вес",
            "type": "NUMERIC",
            "required": False,
            "multivalue": False,
            "allowCustomValues": False,
            "unit": {
                "defaultUnitId": 1,
                "units": [
                    {"id": 1, "name": "г", "fullName": "грамм"},
                    {"id": 2, "name": "кг", "fullName": "килограмм"},
                ],
            },
            "constraints": {"minValue": 0.1, "maxValue": 1000},
        }
    )
    assert unit_parameter["unit_options"] == ["г", "кг"]
    assert unit_parameter["default_unit"] == "г"
    assert unit_parameter["default_unit_id"] == "1"
    assert unit_parameter["units"] == [
        {"id": "1", "name": "г", "full_name": "грамм"},
        {"id": "2", "name": "кг", "full_name": "килограмм"},
    ]
    assert unit_parameter["constraints"] == {"min_value": 0.1, "max_value": 1000}
    shared_unit = _yandex_parameter_definition(unit_parameter)
    # 发布期把选中单位编译为 wire unitId，依赖单位名称 → ID 映射。
    assert shared_unit["unit_ids"] == {"г": "1", "кг": "2"}
    assert shared_unit["default_unit_id"] == "1"
    assert shared_unit["constraints"] == {"min_value": 0.1, "max_value": 1000}
    assert shared_unit["value_mode"] == "free_text"
