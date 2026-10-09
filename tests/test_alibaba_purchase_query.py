"""1688 采购查询：真实采购归属、只读签名、脱敏和多包裹轨迹。"""

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs

import pytest

from erp_web.context import get_context
from erp_web.runtime_units.store_credentials import test_api_config as run_config_test
from erp_web.services import alibaba_api_client as api
from erp_web.services.alibaba_purchase_query_service import query_purchase, normalize_order
from tests.test_order_procurement import domain, confirm, purchase_body


NUMBER = "3317081160226242182"
CONFIG = {"app_key": "12345", "app_secret": "test-secret", "access_token": "test-token"}


@pytest.fixture
def registered(domain):
    service, order, key = domain
    confirm(service, order, key)
    body = purchase_body(order, key)
    body["purchase_order_number"] = NUMBER
    detail = service.record_purchase(body)
    record = detail["lines"][0]["records"][0]
    return service, {"order_id": order.identity, "record_id": record["id"]}


class Client:
    def __init__(self, config):
        self.calls = []

    def query(self, name, number):
        self.calls.append((name, number))
        if name == api.ORDER_DETAIL:
            return {"success": True, "result": {"baseInfo": {
                "id": int(NUMBER), "status": "waitbuyerreceive", "buyerContact": {"mobile": "不可公开"},
            }}}
        if name == api.LOGISTICS_INFO:
            return {"success": True, "result": [
                {"logisticsId": "LP1", "logisticsBillNo": "YT001", "status": "SIGN", "logisticsCompanyName": "圆通", "receiver": "不可公开"},
                {"logisticsId": "LP2", "logisticsBillNo": "SF002", "status": "TRANSPORT", "logisticsCompanyName": "顺丰"},
            ]}
        return {"success": True, "logisticsTrace": [
            {"logisticsId": "LP2", "orderId": int(NUMBER), "logisticsSteps": [{"acceptTime": "2026-10-08 10:00:00", "remark": "运输中"}]},
            {"logisticsId": "LP1", "orderId": int(NUMBER), "logisticsSteps": [{"acceptTime": "2026-10-08 12:00:00", "remark": "已签收"}]},
        ]}


def test_order_uses_saved_number_and_keeps_large_id_as_string(registered):
    service, body = registered
    client = Client(CONFIG)
    result = query_purchase(service, CONFIG, body, client_factory=lambda _: client)
    assert result["order"] == {"order_number": NUMBER, "status": "waitbuyerreceive", "status_label": "等待买家收货"}
    assert client.calls == [(api.ORDER_DETAIL, NUMBER)]
    assert result["checked_at"] and result["logistics"] is None
    assert "不可公开" not in json.dumps(result, ensure_ascii=False)


def test_logistics_matches_multiple_parcels_by_identity(registered):
    service, body = registered
    result = query_purchase(service, CONFIG, {**body, "kind": "logistics"}, client_factory=Client)
    first, second = result["logistics"]
    assert first["status_label"] == "已签收"
    assert first["steps"][0]["description"] == "已签收"
    assert second["steps"][0]["description"] == "运输中"
    assert result["order"] is None
    assert "不可公开" not in json.dumps(result, ensure_ascii=False)


def test_trace_failure_preserves_waybill_and_returns_warning(registered):
    class Failing(Client):
        def query(self, name, number):
            if name == api.LOGISTICS_TRACE:
                raise api.AlibabaApiError("物流轨迹无权限")
            return super().query(name, number)
    service, body = registered
    result = query_purchase(service, CONFIG, {**body, "kind": "logistics"}, client_factory=Failing)
    assert result["logistics"][0]["tracking_number"] == "YT001"
    assert result["logistics_warning"] and not result["logistics"][0]["steps"]


def test_no_waybills_does_not_request_traces(registered):
    service, body = registered
    calls = []
    def query(name, number):
        calls.append(name)
        return {"success": True, "result": []}
    result = query_purchase(service, CONFIG, {**body, "kind": "logistics"}, client_factory=lambda _: SimpleNamespace(query=query))
    assert result["logistics"] == [] and calls == [api.LOGISTICS_INFO]


@pytest.mark.parametrize("change", ["account", "record", "cancel", "extra"])
def test_unauthorized_or_invalid_records_never_send(registered, change):
    service, body = registered
    if change == "account":
        service.accounts_provider = lambda: {"yandex": "other-shop"}
    elif change == "record":
        body["record_id"] = "other-record"
    elif change == "cancel":
        service.cancel_purchase(body)
    else:
        body["order_number"] = "111111"
    def unexpected(_):
        pytest.fail("归属或参数验证失败时不得初始化远端客户端")
    with pytest.raises(ValueError):
        query_purchase(service, CONFIG, body, client_factory=unexpected)


def test_cancel_during_network_read_invalidates_result(registered):
    service, body = registered
    class Cancelling(Client):
        def query(self, name, number):
            service.cancel_purchase(body)
            return super().query(name, number)
    with pytest.raises(ValueError, match="作废"):
        query_purchase(service, CONFIG, body, client_factory=Cancelling)


def test_mismatched_remote_order_is_rejected():
    with pytest.raises(api.AlibabaApiError, match="不一致"):
        normalize_order({"result": {"baseInfo": {"id": 123, "status": "success"}}}, NUMBER)


@pytest.mark.parametrize("missing", ["app_key", "app_secret", "access_token"])
def test_missing_credentials_never_send(monkeypatch, missing):
    def unexpected(*args, **kwargs):
        pytest.fail("缺少凭据不得外发请求")
    monkeypatch.setattr(api, "managed_urlopen", unexpected)
    with pytest.raises(api.AlibabaApiError, match="保存"):
        api.AlibabaApiClient({**CONFIG, missing: ""})


def test_other_procurement_platform_cannot_query_1688(domain):
    service, order, key = domain
    service.select_source({"order_id": order.identity, "line_key": key, "revision": 0,
                           "source": {"source_platform": "其他", "product_url": "https://1688.com.example.test/offer/1", "specification": "蓝色"}})
    result = service.record_purchase({**purchase_body(order, key), "purchase_order_number": NUMBER})
    record_id = result["lines"][0]["records"][0]["id"]
    def unexpected(_):
        pytest.fail("其他平台不得发起 1688 查询")
    with pytest.raises(ValueError, match="仅支持"):
        query_purchase(service, CONFIG, {"order_id": order.identity, "record_id": record_id}, client_factory=unexpected)


def test_aop_request_is_signed_and_uses_fixed_gateway(monkeypatch):
    monkeypatch.setattr(api.time, "time", lambda: 1700000000)
    captured = []
    def send(request, **kwargs):
        captured.append((request, kwargs))
        return io.BytesIO(b'{"success":true,"result":{}}')
    monkeypatch.setattr(api, "managed_urlopen", send)
    api.AlibabaApiClient({**CONFIG, "base_url": "https://untrusted.invalid", "method": "write"}).query(api.ORDER_DETAIL, NUMBER)
    request, kwargs = captured[0]
    assert request.full_url == "https://gw.open.1688.com/openapi/param2/1/com.alibaba.trade/alibaba.trade.get.buyerView/12345"
    params = parse_qs(request.data.decode())
    assert params["orderId"] == [NUMBER] and params["access_token"] == ["test-token"]
    assert params["_aop_timestamp"] == ["1700000000000"]
    assert params["_aop_signature"] == ["855D9FA2179344474849CF65B81A42E92656DAC6"]
    assert "test-secret" not in request.data.decode()
    context = kwargs["request_context"]
    assert context.semantics == "read" and context.interface == api.ORDER_DETAIL
    assert context.credential_id != CONFIG["access_token"] and context.max_attempts == 1
    assert kwargs["follow_redirects"] is False


@pytest.mark.parametrize("response", [b'not-json', b'{"success":false,"errorMessage":"test-token"}', b'{"success":"false"}', b'{"success":1}', b'[]'])
def test_invalid_responses_never_expose_upstream_text(monkeypatch, response):
    monkeypatch.setattr(api, "managed_urlopen", lambda *args, **kwargs: io.BytesIO(response))
    with pytest.raises(api.AlibabaApiError) as error:
        api.AlibabaApiClient(CONFIG).query(api.ORDER_DETAIL, NUMBER)
    assert "test-token" not in str(error.value)


def test_order_api_string_success_is_accepted(monkeypatch):
    monkeypatch.setattr(api, "managed_urlopen", lambda *args, **kwargs: io.BytesIO(b'{"success":"true","result":{}}'))
    assert api.AlibabaApiClient(CONFIG).query(api.ORDER_DETAIL, NUMBER)["result"] == {}


def test_http_auth_failure_is_safe_and_closes_response(monkeypatch):
    stream = io.BytesIO(b'test-token')
    def send(*args, **kwargs):
        raise HTTPError("https://example.invalid/?token=test-token", 401, "test-token", {}, stream)
    monkeypatch.setattr(api, "managed_urlopen", send)
    with pytest.raises(api.AlibabaApiError, match="更新 Access Token") as error:
        api.AlibabaApiClient(CONFIG).query(api.ORDER_DETAIL, NUMBER)
    assert "test-token" not in str(error.value) and stream.closed


def test_order_config_test_uses_saved_secrets_without_exposing_them(monkeypatch):
    context = get_context()
    config = context.config.load_app_config()
    config["1688_api"] = CONFIG
    context.config.save_app_config(config)
    # 只检查实际客户端收到的凭据；不要求测试伪客户端的 calls 属性。
    def query(self, name, number):
        assert self.app_secret == CONFIG["app_secret"] and self.access_token == "new-token"
        return Client(CONFIG).query(name, number)
    monkeypatch.setattr(api.AlibabaApiClient, "query", query)
    result = run_config_test("1688_order", {"app_key": "", "app_secret": "", "access_token": "new-token"}, NUMBER)
    assert result["ok"] and result["order"]["order_number"] == NUMBER
    assert "new-token" not in json.dumps(result) and "test-secret" not in json.dumps(result)
    assert context.config.load_app_config()["1688_api"]["access_token"] == "test-token"


def test_saved_authorization_returns_masks_and_blank_fields_preserve_secrets():
    from erp_web.facades.auth_config_facade import save_ai_config_payload

    result, status = save_ai_config_payload({"1688_api": CONFIG})
    assert status == 200
    section = result["config"]["1688_api"]
    assert section["masked_app_key"] and section["masked_app_secret"] and section["masked_access_token"]
    assert "test-secret" not in json.dumps(result) and "test-token" not in json.dumps(result)
    result, status = save_ai_config_payload({"1688_api": {"app_key": "", "app_secret": section["masked_app_secret"], "access_token": ""}})
    assert status == 200
    saved = get_context().config.load_app_config()["1688_api"]
    assert {key: saved[key] for key in CONFIG} == CONFIG


def test_http_purchase_query_contract_and_blocked_response(registered, monkeypatch):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    import requests
    from erp_web.http_handler import Handler
    from erp_web.schemas.external_requests import ExternalRequestBlocked, RequestFailure

    service, body = registered
    context = get_context()
    context._order_procurement = service
    config = context.config.load_app_config()
    config["1688_api"] = CONFIG
    context.config.save_app_config(config)
    def query(self, name, number):
        return Client(CONFIG).query(name, number)
    monkeypatch.setattr(api.AlibabaApiClient, "query", query)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/orders/purchase-query"
    try:
        response = requests.post(url, json=body, timeout=3)
        assert response.status_code == 200 and response.json()["order"]["order_number"] == NUMBER
        assert requests.post(url, json={**body, "kind": "write"}, timeout=3).status_code == 400
        assert requests.post(url, json={**body, "record_id": 123}, timeout=3).status_code == 400
        assert requests.post(url, json={**body, "order_id": "other-order"}, timeout=3).status_code == 400
        def blocked(*args):
            raise ExternalRequestBlocked(RequestFailure("TEST_BLOCK", "已中断"))
        monkeypatch.setattr(api.AlibabaApiClient, "query", blocked)
        response = requests.post(url, json=body, timeout=3)
        assert response.status_code == 409 and "中断与恢复" in response.json()["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


@pytest.mark.parametrize('name', [api.LOGISTICS_INFO, api.ORDER_DETAIL, api.LOGISTICS_TRACE])
def test_unshipped_code_is_empty_only_for_logistics_info(monkeypatch, name):
    payload = {'success': False, 'errorCode': '500_2', 'errorMessage': '订单尚未发货，暂无物流详情，请稍候再试。'}
    monkeypatch.setattr(api, 'managed_urlopen', lambda *a, **k: io.BytesIO(json.dumps(payload).encode()))
    if name == api.LOGISTICS_INFO:
        result = api.AlibabaApiClient(CONFIG).query(name, NUMBER)
        assert result['result'] == [] and result['errorCode'] == '500_2'
    else:
        with pytest.raises(api.AlibabaApiError): api.AlibabaApiClient(CONFIG).query(name, NUMBER)


def test_product_image_matches_exact_sku_and_prefers_original():
    from erp_web.schemas.order_procurement import ProcurementSource
    from erp_web.services.alibaba_purchase_query_service import normalize_product
    source = ProcurementSource(product_url="https://detail.1688.com/offer/123.html", source_sku_id="22", specification="蓝色")
    other = {"productID": 123, "skuID": 11, "productImgUrl": ["https://cbu01.alicdn.com/other.jpg"]}
    selected = {"productID": 123, "skuID": 22, "specId": "spec22", "name": "商品",
                "skuInfos": [{"name": "颜色", "value": "蓝色"}],
                "productImgUrl": ["http://cbu01.alicdn.com/thumb.jpg", "http://cbu01.alicdn.com/original.jpg"]}
    payload = {"result": {"productItems": [other, selected]}}
    product, warning = normalize_product(payload, source)
    assert warning == ""
    assert product == {"offer_id": "123", "sku_id": "22", "spec_id": "spec22", "name": "商品",
                       "specification": "颜色：蓝色", "image_url": "https://cbu01.alicdn.com/original.jpg"}
    for patch in ({"source_sku_id": "99"}, {"source_offer_id": "999"},
                  {"source_spec_id": "conflicting"}, {"source_sku_id": ""}):
        product, warning = normalize_product(payload, source.model_copy(update=patch))
        assert product is None and warning
    product, warning = normalize_product(payload, source.model_copy(update={"source_sku_id": "", "source_spec_id": "spec22"}))
    assert product["sku_id"] == "22" and not warning
    payload["result"]["productItems"].append(selected)
    product, warning = normalize_product(payload, source)
    assert product is None and warning


@pytest.mark.parametrize("images", [None, [], ["javascript:alert(1)"], ["https://secret:password@example.com/p.jpg"], ["http://[invalid"], [123]])
def test_missing_or_invalid_product_image_is_not_a_query_failure(images):
    from erp_web.schemas.order_procurement import ProcurementSource
    from erp_web.services.alibaba_purchase_query_service import normalize_product
    source = ProcurementSource(product_url="https://detail.1688.com/offer/123.html", source_sku_id="22", specification="蓝色")
    product, warning = normalize_product({"result": {"productItems": [{"productID": 123, "skuID": 22, "productImgUrl": images}]}}, source)
    assert product["image_url"] == "" and warning
