"""本机 Chrome 调试 HTTP 通信，不参与外部平台审计、限流与阻断。"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

from .operation import check_cancellation, remaining_seconds


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def browser_debug_json(port: int, path: str, *, method: str = "GET", timeout: float = 8) -> Any:
    """只访问固定回环主机的 CDP 接口，继承当前任务的取消和期限。"""
    parsed = urllib.parse.urlsplit(path)
    if (type(port) is not int or not 1 <= port <= 65535
            or parsed.scheme or parsed.netloc or parsed.fragment
            or (method, parsed.path) not in {
                ("GET", "/json"), ("GET", "/json/list"),
                ("GET", "/json/version"), ("PUT", "/json/new"),
            }):
        raise ValueError("无效的本机浏览器调试请求")
    url = f"http://127.0.0.1:{port}{path}"
    # 这里只复用操作上下文的取消与期限，不调用外部请求管理器或业务发送钩子。
    check_cancellation()
    remaining = remaining_seconds(timeout)
    # 本机控制通道直连，不受系统代理影响，也不跟随重定向到其他服务。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(url, method=method, headers={"Accept": "application/json"})
    with opener.open(request, timeout=remaining) as response:
        raw = response.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError("浏览器调试响应超过 32 MiB 上限")
    return json.loads(raw.decode("utf-8")) if raw else None


__all__ = ["browser_debug_json"]
