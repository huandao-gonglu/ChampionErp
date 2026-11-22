"""业务响应与接口故障隔离；真实客户端和请求管理器使用离线传输。"""
from dataclasses import replace
import io
import json
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

from erp_web.context import get_context
from erp_web.schemas.external_requests import ExternalRequestBlocked, RequestContext
from erp_web.services import alibaba_api_client as alibaba
from erp_web.services.platform_request_policy import classify_response, platform_error_codes

PLATFORM = "gw.open.1688.com"
UNSHIPPED = {"success": False, "errorCode": "500_2", "errorMessage": "订单尚未发货，暂无物流详情，请稍候再试。"}


class Response(io.BytesIO):
    status = 200
    headers = {}


def context(**values):
    return RequestContext(**{
        "platform": PLATFORM, "account_id": "test-account", "interface": alibaba.LOGISTICS_INFO,
        "source": "离线回归", "semantics": "read", "timeout": 2, **values,
    })


def send(manager, ctx, payload, *, status=200):
    raw = json.dumps(payload).encode()

    def transport(*args, **kwargs):
        if status >= 400:
            raise urllib.error.HTTPError("https://test.invalid", status, "离线拒绝", {}, io.BytesIO(raw))
        return Response(raw)

    return manager.open(
        urllib.request.Request("https://test.invalid"),
        request_context_value=ctx,
        transport=transport,
        timeout=2,
    )


@pytest.mark.parametrize("payload", [UNSHIPPED, {**UNSHIPPED, "success": "false"}, {"errorCode": "500_2"}])
def test_unshipped_orders_pass_real_client_and_manager_without_tripping_circuit(monkeypatch, payload):
    calls = []

    def transport(request, **kwargs):
        calls.append(request)
        return Response(json.dumps(payload).encode())

    # 只替换最底层传输，保留签名、响应解析、审计和熔断判断的完整调用链。
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: SimpleNamespace(open=transport))
    client = alibaba.alibaba_provider({"app_key": "123", "app_secret": "fake", "access_token": "fake"})
    for _ in range(6):
        assert client.get_shipments("12345") == []
    manager = get_context().external_requests
    assert len(calls) == 6
    audit = manager.store.query(platform=PLATFORM)
    assert audit["stats"]["network_attempts"] == 6
    assert all(row["result"]["outcome"] == "success" for row in audit["items"])


@pytest.mark.parametrize("status", [200, 400])
def test_business_rejections_only_protect_original_request(status):
    manager = get_context().external_requests
    manager.limiter.set_limit(PLATFORM, consecutive_failure_limit=2)
    contexts = [context(interface="alibaba.createOrder.preview", fingerprint="parameters") for _ in range(6)]
    payload = {"success": False, "errorCode": "500_005", "errorMessage": "业务条件不满足"}
    for ctx in contexts:
        if status == 400:
            with pytest.raises(urllib.error.HTTPError):
                send(manager, ctx, payload, status=status)
        else:
            send(manager, ctx, payload).close()
    # 同操作同指纹被本地拦截；修改参数指纹后允许重新发送。
    with pytest.raises(ExternalRequestBlocked):
        send(manager, contexts[0], payload, status=status)
    send(manager, replace(contexts[0], fingerprint="corrected"), {"success": True}).close()
    failed = [row for row in manager.store.query()["items"] if row["result"].get("outcome") == "failed"]
    assert len(failed) == 6
    assert all(row["result"]["platform_error_codes"] == ["500_005"] for row in failed)


@pytest.mark.parametrize("platform,payload", [
    ("unknown.example", {"success": False, "status": "FAILED", "errors": [{"code": "FORBIDDEN"}]}),
    ("ai:test", {"status": "failed", "output": [{"text": "access_denied"}]}),
    ("ozon", {"success": False, "status": "FAILED", "result": {"errors": [{"code": "FORBIDDEN"}]}}),
    ("yandex", {"status": "OK", "result": {"status": "ERROR", "errors": [{"code": "FORBIDDEN"}]}}),
    ("mercadolibre", {"status": "FAILED", "success": False}),
    ("crossborderbus", {"code": 1, "status": "FAILED", "success": False}),
])
def test_business_content_does_not_control_transport(platform, payload):
    manager = get_context().external_requests
    for _ in range(6):
        send(manager, context(platform=platform), payload).close()
    assert all(row["result"]["outcome"] == "success" for row in manager.store.query()["items"])


@pytest.mark.parametrize("status,expected", [(401, "credential"), (403, "interface"), (429, "account"), (500, "")])
def test_http_failure_cannot_be_hidden_by_normal_business_payload(status, expected):
    failure = classify_response(PLATFORM, status, {}, json.dumps(UNSHIPPED).encode(), interface=alibaba.LOGISTICS_INFO)
    assert failure is not None and failure.scope == expected


@pytest.mark.parametrize("interface", [alibaba.ORDER_DETAIL, alibaba.LOGISTICS_TRACE, ""])
def test_unshipped_exception_is_scoped_to_logistics_interface(interface):
    failure = classify_response(PLATFORM, 200, {}, json.dumps(UNSHIPPED).encode(), interface=interface)
    assert failure is not None and failure.scope == "request"


def test_alibaba_acl_rejection_is_diagnosable_and_isolated():
    manager = get_context().external_requests
    payload = {"errorCode": "gw.APIACLDecline", "errorMessage": "敏感原文不得保存"}
    ctx = context()
    with pytest.raises(ExternalRequestBlocked, match="gw.APIACLDecline"):
        send(manager, ctx, payload)
    # 单接口权限拒绝不影响其他接口。
    send(manager, replace(ctx, interface=alibaba.ORDER_DETAIL), {"success": "true"}).close()
    audit = manager.store.query()
    assert "敏感原文" not in json.dumps(audit, ensure_ascii=False)
    assert audit["items"][1]["result"]["platform_error_codes"] == ["gw.APIACLDecline"]


def test_buyer_trace_permission_code_is_kept_in_request_audit():
    manager = get_context().external_requests
    send(manager, context(interface=alibaba.LOGISTICS_TRACE),
         {"success": False, "errorCode": "order.nopermission.buyer"}).close()
    assert manager.store.query()["items"][0]["result"]["platform_error_codes"] == ["order.nopermission.buyer"]


@pytest.mark.parametrize("platform,payload", [
    (PLATFORM, {"success": False, "errorCode": "TOO_MANY_REQUESTS"}),
    ("yandex", {"status": "ERROR", "errors": [{"code": "TOO_MANY_REQUESTS"}]}),
    ("ozon", {"code": 8}),
    ("ai:test", {"error": {"code": "rate_limit_exceeded"}}),
    ("mercadolibre", {"error": "too_many_requests"}),
])
def test_explicit_rate_rejection_in_success_http_enters_memory_cooldown(platform, payload):
    manager = get_context().external_requests
    ctx = context(platform=platform, timeout=0.5)
    with pytest.raises(ExternalRequestBlocked) as error:
        send(manager, ctx, payload)
    assert error.value.code.endswith("_RATE_LIMITED")
    assert error.value.details["resume_at"] is not None
    # 超出剩余 deadline 的后续请求被本地直接拦截，不外发。
    with pytest.raises(ExternalRequestBlocked):
        send(manager, replace(ctx, operation_id="another", timeout=0.2), {})
    assert manager.store.query()["stats"]["network_attempts"] == 1


def test_expected_business_code_cannot_hide_a_separate_protocol_error():
    raw = json.dumps({**UNSHIPPED, "error": "too_many_requests"}).encode()
    failure = classify_response(PLATFORM, 200, {}, raw, interface=alibaba.LOGISTICS_INFO)
    assert failure.code.endswith("_RATE_LIMITED")


@pytest.mark.parametrize("code", ["带空格的错误 原文", "token=secret", "https://secret.invalid", "X" * 100])
def test_error_code_audit_remains_bounded_and_excludes_raw_content(code):
    assert platform_error_codes(json.dumps({"errorCode": code}), platform=PLATFORM) == []


def test_business_rejection_breaks_service_failure_streak():
    manager = get_context().external_requests
    manager.limiter.set_limit(PLATFORM, consecutive_failure_limit=2)
    for status, payload in [(503, {}), (200, {"success": False, "errorCode": "500_005"}), (503, {})]:
        if status == 503:
            with pytest.raises(urllib.error.HTTPError):
                send(manager, context(), payload, status=status)
        else:
            send(manager, context(), payload).close()
    # 业务拒绝中断了连续故障序列，第二次 503 后才进入瞬时冷却。
    assert manager.matching_status([context()]) == []
    with pytest.raises(urllib.error.HTTPError):
        send(manager, context(), {}, status=503)
    statuses = manager.matching_status([context()])
    assert any(item["code"] == "EXTERNAL_TRANSIENT_FAILURE" and not item["hard"] for item in statuses)
