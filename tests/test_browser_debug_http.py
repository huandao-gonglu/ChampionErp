"""本地浏览器通信与外部业务保护隔离的回归。"""
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
import time
import urllib.error
from unittest.mock import Mock

import pytest

from erp_web.context import get_context
from desktop_runtime import collection
from desktop_runtime.config import LocalConfig
from desktop_runtime.operation import bind_operation
from desktop_runtime import browser_ai_runtime as ai
from desktop_runtime import browser_debug_http as transport
from desktop_runtime import browser_debug_service as browser
from erp_web.services.external_request_context import request_context, request_operation
from erp_web.schemas.external_requests import ExternalRequestBlocked, RequestFailure


def fake_transport(monkeypatch, **kwargs):
    send = Mock(**kwargs)
    monkeypatch.setattr(transport.urllib.request, "build_opener", Mock(return_value=Mock(open=send)))
    return send


def test_browser_recovers_after_repeated_disconnects_without_external_audit(monkeypatch):
    store = get_context().external_requests.store
    send = fake_transport(monkeypatch, side_effect=[
        *[urllib.error.URLError("浏览器未启动") for _ in range(6)],
        io.BytesIO(b'{"webSocketDebuggerUrl":"ws://127.0.0.1/devtools/browser/test"}'),
    ])
    for _ in range(6):
        assert browser.browser_debug_connected(9222) is False
    assert browser.browser_debug_connected(9222) is True
    assert send.call_count == 7
    assert store.query()["items"] == []
    assert store.blocks() == []


def test_old_loopback_blocks_do_not_affect_browser_but_business_requests_stay_blocked(monkeypatch, tmp_path):
    manager = get_context().external_requests
    ctx = request_context("http://127.0.0.1:9222/json/version")
    manager.store.block(ctx, RequestFailure("EXTERNAL_API_DISABLED", "已有阻断", "account"))
    original_blocks = manager.store.blocks()
    target = {"id": "original", "type": "page", "url": "https://detail.1688.com/offer/123.html",
              "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/original"}
    def send(request, **kwargs):
        payload = {"webSocketDebuggerUrl": target["webSocketDebuggerUrl"]} if request.full_url.endswith("/version") else [target]
        return io.BytesIO(json.dumps(payload).encode())
    fake_transport(monkeypatch, side_effect=send)
    assert browser.browser_debug_connected(9222)
    config = LocalConfig(tmp_path)
    config.browser_port = 9222
    monkeypatch.setattr(collection, "ensure_browser_debug_session", lambda *_: None)
    assert collection.browser_status(config)["connected"]
    assert ai._page_targets(9222) == [target]
    assert collection.target_for_url(config, target["url"]) == target
    assert manager.store.query()["items"] == []
    assert manager.store.blocks() == original_blocks
    network = Mock(side_effect=AssertionError("已有业务阻断不能绕过"))
    with pytest.raises(ExternalRequestBlocked):
        manager.open("http://127.0.0.1:9222/json/version", transport=network)
    network.assert_not_called()


@pytest.mark.parametrize('expired', [False, True])
def test_cancelled_or_expired_operation_does_not_send(monkeypatch, expired):
    send = fake_transport(monkeypatch, side_effect=AssertionError('已取消操作不能发送'))
    cancel = Event()
    if not expired:
        cancel.set()
    with bind_operation(cancel, -1 if expired else 20):
        with pytest.raises((RuntimeError, TimeoutError)):
            transport.browser_debug_json(9222, '/json/list')
    send.assert_not_called()


def test_operation_deadline_limits_socket_timeout(monkeypatch):
    send = fake_transport(monkeypatch, return_value=io.BytesIO(b'[]'))
    with bind_operation(Event(), 2):
        assert transport.browser_debug_json(9222, '/json/list', timeout=8) == []
    assert 0 < send.call_args.kwargs['timeout'] <= 2


@pytest.mark.parametrize(("port", "path", "method"), [
    (9222, "https://example.test/json", "GET"), (9222, "//example.test/json", "GET"),
    (9222, "/api/orders", "GET"), (9222, "/json/new", "POST"),
    (0, "/json", "GET"), (65536, "/json", "GET"),
])
def test_local_transport_rejects_non_cdp_requests(monkeypatch, port, path, method):
    send = fake_transport(monkeypatch, side_effect=AssertionError("非法请求不能发送"))
    with pytest.raises(ValueError):
        transport.browser_debug_json(port, path, method=method)
    send.assert_not_called()


def test_local_transport_bypasses_proxy_and_refuses_redirect(monkeypatch):
    paths = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/must-not-follow")
            self.end_headers()

        def log_message(self, *args):
            pass

    # 若错误使用系统代理，请求会先触发此失败而无法到达本机测试服务。
    monkeypatch.setattr(transport.urllib.request, "getproxies", Mock(side_effect=AssertionError("本机通信不能使用代理")))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            transport.browser_debug_json(server.server_port, "/json/version", timeout=2)
        assert error.value.code == 302
        error.value.close()
        assert paths == ["/json/version"]
        assert get_context().external_requests.store.query()["items"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
