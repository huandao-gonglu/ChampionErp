from __future__ import annotations

import base64
import hashlib
import hmac
import json
from email.message import Message
from typing import Any

from erp_web.runtime_units.yunexpress_client import (
    CREATE_PACKAGE_PATH,
    YunExpressClient,
    build_create_package_payload,
    build_create_package_preview,
    calculate_signature,
    canonical_json,
    signature_content,
    validate_create_package_payload,
)


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.headers = Message()
        self.headers["Content-Type"] = "application/json; charset=utf-8"

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def test_yunexpress_signature_content_and_hmac() -> None:
    body = canonical_json({"name": "zhangs"})
    content = signature_content("POST", "/api/test", 1651049235123, body)

    assert content == 'body={"name":"zhangs"}&date=1651049235123&method=POST&uri=/api/test'
    expected = base64.b64encode(hmac.new(b"secret", content.encode("utf-8"), hashlib.sha256).digest()).decode("utf-8")
    assert calculate_signature(content, "secret") == expected


def test_yunexpress_build_create_package_payload_uses_defaults() -> None:
    payload = build_create_package_payload(
        {
            "customer_order_number": "ERP-1001",
            "receiver": {"name": "Buyer", "country_code": "MX"},
            "packages": [{"weight": 0.5, "length": 20, "width": 15, "height": 10}],
            "declaration_info": [{"sku": "SKU-1", "name_en": "Cup", "quantity": 1, "unit_price": 2.5}],
        },
        {
            "product_code": "S1002",
            "label_type": "PDF",
            "weight_unit": "KG",
            "size_unit": "CM",
            "source_code": "ERP",
        },
    )

    assert payload["product_code"] == "S1002"
    assert payload["source_code"] == "ERP"
    assert payload["label_type"] == "PDF"
    assert validate_create_package_payload(payload) == []


def test_yunexpress_preview_reports_missing_required_fields() -> None:
    preview = build_create_package_preview({"app_secret": "secret"}, {}, date_ms=1651049235123)

    assert preview["method"] == "POST"
    assert preview["url"].endswith(CREATE_PACKAGE_PATH)
    assert preview["headers"]["sign"]
    assert "缺少物流产品编码" in "；".join(preview["errors"])


def test_yunexpress_client_requests_token_then_creates_order() -> None:
    requests: list[Any] = []

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        requests.append((request, timeout))
        if request.full_url.endswith("/openapi/oauth2/token"):
            return FakeResponse({"accessToken": "token-123", "expiresIn": 7200})
        return FakeResponse({"success": True, "result": {"waybill_number": "YT123"}})

    client = YunExpressClient(
        {
            "base_url": "https://openapi-sbx.yunexpress.cn",
            "app_id": "app-id",
            "app_secret": "secret",
            "source_key": "source-key",
        },
        urlopen=fake_urlopen,
    )
    result = client.create_package_order(
        {
            "product_code": "S1002",
            "receiver": {"name": "Buyer"},
            "packages": [{"weight": 0.5}],
            "declaration_info": [{"name_en": "Cup"}],
        }
    )

    assert result["response"]["success"] is True
    assert len(requests) == 2
    token_request = requests[0][0]
    order_request = requests[1][0]
    assert json.loads(token_request.data.decode("utf-8"))["sourceKey"] == "source-key"
    assert order_request.full_url.endswith(CREATE_PACKAGE_PATH)
    assert order_request.get_header("Token") == "token-123"
    assert order_request.get_header("Sign")
