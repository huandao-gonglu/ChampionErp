"""图片请求专用安全传输；由统一请求管理器调用，禁止重定向与隐式代理。"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit

from erp_web.services.external_request_manager import BufferedResponse
from erp_web.services.image_content import MAX_IMAGE_BYTES
from erp_web.services.image_hosting_config import validate_public_url
from erp_web.schemas.image_hosting import ImageHostingError
from erp_web.schemas.external_requests import ExternalRequestNotSent, RequestFailure


def public_addresses(host: str, port: int) -> list[str]:
    try:
        addresses = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    except socket.gaierror:
        raise ImageHostingError("IMAGE_DNS_FAILED", "图片域名解析失败，请检查 DNS 和网络") from None
    if not addresses:
        raise ImageHostingError("IMAGE_DNS_FAILED", "图片域名未解析到地址，请检查 DNS 和网络")
    parsed = [ipaddress.ip_address(address) for address in addresses]
    if any(address.version == 4 and address in ipaddress.ip_network("198.18.0.0/15") for address in parsed):
        raise ImageHostingError("IMAGE_DNS_NONPUBLIC", "图片域名解析到 198.18.0.0/15 保留地址，已在发送前拦截。可能启用了代理 Fake-IP；请让上传和公开图片域名返回真实公网 IP 后重试")
    if any(not address.is_global for address in parsed):
        raise ImageHostingError("IMAGE_DNS_NONPUBLIC", "图片域名解析到非公开地址，已在发送前拦截；请检查 DNS 和代理配置")
    return addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        # DNS 校验与实际连接使用同一 IP；证书仍校验原域名，避免 DNS 重绑定。
        try:
            address = public_addresses(self.host, self.port)[0]
        except ImageHostingError as exc:
            raise ExternalRequestNotSent(RequestFailure(exc.code, str(exc))) from None
        try:
            sock = socket.create_connection((address, self.port), self.timeout)
        except OSError:
            raise ExternalRequestNotSent(RequestFailure("IMAGE_CONNECTION_FAILED", "图片服务器连接失败，尚未发送 HTTP 请求；请检查网络和代理配置")) from None
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except ssl.SSLError:
            sock.close()
            raise ExternalRequestNotSent(RequestFailure("IMAGE_TLS_FAILED", "图片服务器 TLS 握手或证书校验失败，尚未发送 HTTP 请求；请检查证书信任和网络拦截")) from None
        except OSError:
            sock.close()
            raise ExternalRequestNotSent(RequestFailure("IMAGE_CONNECTION_FAILED", "图片服务器连接在 TLS 握手阶段中断，尚未发送 HTTP 请求；请检查网络")) from None
        except BaseException:
            sock.close()
            raise


def safe_image_urlopen(request, *, timeout=30, max_bytes=MAX_IMAGE_BYTES):
    try:
        validate_public_url(request.full_url)
    except ImageHostingError as exc:
        raise ExternalRequestNotSent(RequestFailure(exc.code, str(exc))) from None
    parsed = urlsplit(request.full_url)
    connection = _PinnedHTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout, context=ssl.create_default_context())
    deadline = time.monotonic() + timeout
    try:
        connection.request(request.get_method(), parsed.path or "/", body=request.data, headers=dict(request.header_items()))
        response = connection.getresponse()
        raw = bytearray()
        while True:
            if connection.sock is not None:
                connection.sock.settimeout(max(.001, deadline - time.monotonic()))
            if time.monotonic() >= deadline:
                raise TimeoutError("图片请求超时")
            chunk = response.read1(min(65536, max_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > max_bytes:
                raise ImageHostingError("IMAGE_PUBLIC_ACCESS_FAILED", "图片响应超过下载大小上限")
        result = BufferedResponse(bytes(raw), headers=response.headers, status=response.status, url=request.full_url)
        if response.status >= 300:
            # 不跟随重定向；S3 签名与匿名读取使用完全相同的地址边界。
            raise HTTPError(request.full_url, response.status, "图片请求失败", response.headers, result)
        return result
    finally:
        connection.close()


__all__ = ["safe_image_urlopen", "public_addresses", "MAX_IMAGE_BYTES"]
