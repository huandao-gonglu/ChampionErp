"""地址限制、DNS 绑定、下载上限与重定向边界。"""
import io
import ssl
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from erp_web.services import image_hosting_transport as transport
from erp_web.schemas.image_hosting import ImageHostingError
from erp_web.schemas.external_requests import ExternalRequestNotSent


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "::ffff:127.0.0.1", "198.18.0.6", "198.19.255.254"])
def test_dns_to_nonpublic_address_is_rejected(monkeypatch, address):
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", (address, 443))])
    with pytest.raises(ImageHostingError):
        transport.public_addresses("images.example.test", 443)


def test_fake_ip_is_rejected_before_socket_connection(monkeypatch):
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("198.18.0.6", 443))])
    monkeypatch.setattr(transport.socket, "create_connection", lambda *a, **k: pytest.fail("不得连接 Fake-IP"))
    with pytest.raises(ExternalRequestNotSent) as error:
        transport.safe_image_urlopen(Request("https://s3.example.test/test.png", method="PUT", data=b"image"))
    assert error.value.code == "IMAGE_DNS_NONPUBLIC"
    assert "Fake-IP" in str(error.value)


@pytest.mark.parametrize("stage", ["connect", "tls"])
def test_connection_failure_before_http_is_safe_and_not_sent(monkeypatch, stage):
    monkeypatch.setattr(transport, "public_addresses", lambda *a: ["8.8.8.8"])
    closed = []
    sock = SimpleNamespace(close=lambda: closed.append(True))
    def connect(*a, **kw):
        if stage == "connect":
            raise OSError("不能泄露的敏感诊断")
        return sock
    def wrap(*a, **kw):
        raise ssl.SSLError("不能泄露的敏感诊断")
    monkeypatch.setattr(transport.socket, "create_connection", connect)
    connection = transport._PinnedHTTPSConnection("images.example.test")
    connection._context = SimpleNamespace(wrap_socket=wrap)
    with pytest.raises(ExternalRequestNotSent) as error:
        connection.connect()
    assert error.value.code == ("IMAGE_CONNECTION_FAILED" if stage == "connect" else "IMAGE_TLS_FAILED")
    assert "不能泄露" not in str(error.value)
    assert closed == ([True] if stage == "tls" else [])


def test_connection_uses_validated_ip_with_original_tls_name(monkeypatch):
    monkeypatch.setattr(transport, "public_addresses", lambda host, port: ["8.8.8.8"])
    calls = []
    sock = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(transport.socket, "create_connection", lambda address, timeout: calls.append(address) or sock)
    tls = SimpleNamespace(wrap_socket=lambda value, server_hostname: calls.append(server_hostname) or value)
    connection = transport._PinnedHTTPSConnection("images.example.test", context=None)
    connection._context = tls
    connection.connect()
    assert calls == [("8.8.8.8", 443), "images.example.test"]


@pytest.mark.parametrize(("status", "data", "error"), [(302, b"", HTTPError), (200, b"12345", ImageHostingError)])
def test_redirect_and_oversized_download_are_rejected(monkeypatch, status, data, error):
    class Response(io.BytesIO):
        headers = {}
        def __init__(self):
            super().__init__(data)
            self.status = status
    connection = SimpleNamespace(request=lambda *a, **k: None, getresponse=lambda: Response(), sock=None, close=lambda: None)
    monkeypatch.setattr(transport, "_PinnedHTTPSConnection", lambda *a, **k: connection)
    with pytest.raises(error):
        transport.safe_image_urlopen(Request("https://images.example.test/test.png"), max_bytes=4)
