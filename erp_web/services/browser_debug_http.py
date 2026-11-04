"""本机 Chrome 调试 HTTP 通信，不参与外部平台审计、限流与阻断。"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

from erp_web.services.external_request_context import request_context


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
    ctx = request_context(url, method=method, timeout=timeout, source=__name__)
    if ctx.cancellation_check is not None:
        ctx.cancellation_check()
    if ctx.cancel is not None and ctx.cancel.is_set():
        raise RuntimeError("任务已取消，尚未发送浏览器调试请求")
    remaining = min(timeout, ctx.deadline_at - time.time()) if ctx.deadline_at is not None else timeout
    if remaining <= 0:
        # 任务过期不是连接失败，不能被连接探测当作浏览器尚未启动。
        raise RuntimeError("浏览器调试请求超过任务时间边界，尚未发送")
    # 本机控制通道直连，不受系统代理影响，也不跟随重定向到其他服务。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(url, method=method, headers={"Accept": "application/json"})
    with opener.open(request, timeout=remaining) as response:
        raw = response.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError("浏览器调试响应超过 32 MiB 上限")
    return json.loads(raw.decode("utf-8")) if raw else None


__all__ = ["browser_debug_json"]
