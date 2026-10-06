"""平台面单回归：只读协议、订单/箱条码对应、公开交付及授权范围。"""

import io
import urllib.error

import pytest

from erp_web.marketplaces import yandex_http
from erp_web.schemas.fulfillment import FulfillmentError
from erp_web.schemas.orders import OrderSnapshot
from erp_web.services import platform_label_service as labels

PDF = b"%PDF-1.7\nfixture\n%%EOF"


@pytest.fixture
def label_domain(monkeypatch):
    order = OrderSnapshot(platform="yandex", account_id="4", order_id="123", fulfillment="FBS", status="PROCESSING", state="pending_shipment")
    config = {"yandex": {"campaign_id": "4", "api_token": "private-token"}}
    service = labels.PlatformLabelService(lambda: config, lambda: {})
    result = {"orderId": 123, "placesNumber": 1, "parcelBoxLabels": [{"orderId": 123, "fulfilmentId": "123-1", "orderNum": "external-order"}]}
    calls = []
    monkeypatch.setattr(labels, "default_profile", lambda app: {})
    def json_request(method, path, token):
        calls.append((method, path, token))
        return {"result": result}
    def pdf_request(path, token):
        calls.append(("PDF", path, token))
        return PDF
    def deliver(config, order_id, data):
        calls.append(("UPLOAD", order_id, data))
        return {"url": "https://cdn.example/label.pdf"}
    monkeypatch.setattr(labels, "request_yandex_json", json_request)
    monkeypatch.setattr(labels, "request_yandex_pdf", pdf_request)
    monkeypatch.setattr(labels, "deliver_label_bytes", deliver)
    return service, order, config, result, calls


def test_yandex_downloads_pdf_and_box_barcode_without_using_response_url(label_domain):
    service, order, _, result, calls = label_domain
    result["url"] = "https://deprecated.example/label"
    value = service.fetch(order)
    assert value.url == "https://cdn.example/label.pdf"
    assert value.tracking_number == "123-1"
    assert calls == [("GET", "/v2/campaigns/4/orders/123/delivery/labels/data", "private-token"),
                     ("PDF", "/v2/campaigns/4/orders/123/delivery/labels", "private-token"),
                     ("UPLOAD", order.identity, PDF)]


@pytest.mark.parametrize("change,reason", [
    ({"orderId": 456}, "不一致"),
    ({"parcelBoxLabels": []}, "尚未生成"),
    ({"placesNumber": 2}, "多个国际箱"),
    ({"parcelBoxLabels": [{"orderId": 456, "fulfilmentId": "456-1"}]}, "对应的箱号"),
    ({"parcelBoxLabels": [{"orderId": 123, "orderNum": "123"}]}, "对应的箱号"),
])
def test_metadata_must_identify_one_matching_box_before_pdf_download(label_domain, change, reason):
    service, order, _, result, calls = label_domain
    result.update(change)
    with pytest.raises(FulfillmentError, match=reason):
        service.fetch(order)
    assert len(calls) == 1


def test_account_switch_prevents_request_and_token_switch_prevents_delivery(label_domain, monkeypatch):
    service, order, config, _, calls = label_domain
    config["yandex"]["campaign_id"] = "5"
    with pytest.raises(FulfillmentError, match="授权已变化"):
        service.fetch(order)
    assert calls == []
    config["yandex"]["campaign_id"] = "4"
    def switched(path, token):
        config["yandex"] = {"campaign_id": "4", "api_token": "new-token"}
        return PDF
    monkeypatch.setattr(labels, "request_yandex_pdf", switched)
    with pytest.raises(FulfillmentError, match="授权在获取期间"):
        service.fetch(order)
    assert not any(c[0] == "UPLOAD" for c in calls)


def test_hosting_required_before_platform_requests(label_domain, monkeypatch):
    service, order, _, _, calls = label_domain
    def missing(config):
        raise ValueError("托管未配置")
    monkeypatch.setattr(labels, "default_profile", missing)
    with pytest.raises(FulfillmentError, match="默认 S3"):
        service.fetch(order)
    assert calls == []


def test_invalid_platform_pdf_is_never_uploaded(label_domain, monkeypatch):
    service, order, _, _, calls = label_domain
    monkeypatch.setattr(labels, "request_yandex_pdf", lambda *args: b'{"status":"ERROR"}')
    with pytest.raises(FulfillmentError, match="完整 PDF"):
        service.fetch(order)
    assert not any(c[0] == "UPLOAD" for c in calls)


def test_permission_error_keeps_credentials_out_of_user_message(label_domain, monkeypatch):
    service, order, _, _, _ = label_domain
    def denied(*args):
        raise yandex_http.YandexApiError("FORBIDDEN", "private-token", http_status=403)
    monkeypatch.setattr(labels, "request_yandex_json", denied)
    with pytest.raises(FulfillmentError, match="订单处理权限") as error:
        service.fetch(order)
    assert "private-token" not in str(error.value)


def test_binary_transport_uses_api_key_and_fixed_get_endpoint(monkeypatch):
    calls = []
    def open_request(request, **kwargs):
        calls.append(request)
        return io.BytesIO(PDF)
    monkeypatch.setattr(yandex_http, "managed_urlopen", open_request)
    assert yandex_http.request_yandex_pdf("/v2/campaigns/4/orders/123/delivery/labels", "secret") == PDF
    assert calls[0].get_method() == "GET"
    assert calls[0].full_url == "https://api.partner.market.yandex.ru/v2/campaigns/4/orders/123/delivery/labels?format=A7"
    assert calls[0].get_header("Api-key") == "secret"
    for path in ("https://other.example/file.pdf", "/v2/campaigns/4/orders/123/delivery/labels?url=other", "/v2/campaigns/../orders/123/delivery/labels"):
        with pytest.raises(yandex_http.YandexApiError):
            yandex_http.request_yandex_pdf(path, "secret")
    assert len(calls) == 1


def test_pdf_http_error_uses_shared_classification_and_masks_token(monkeypatch):
    def forbidden(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"errors":[{"code":"FORBIDDEN","message":"secret"}]}'))
    monkeypatch.setattr(yandex_http, "managed_urlopen", forbidden)
    with pytest.raises(yandex_http.YandexApiError) as error:
        yandex_http.request_yandex_pdf("/v2/campaigns/4/orders/123/delivery/labels", "secret")
    assert error.value.http_status == 403
    assert "secret" not in str(error.value)
