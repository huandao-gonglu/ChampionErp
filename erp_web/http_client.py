# -*- coding: utf-8 -*-
"""受统一外部请求管理器控制的 JSON 传输入口。"""
from __future__ import annotations

import json
import urllib.request
from typing import Any

from erp_web.services.external_request_manager import managed_urlopen

DEFAULT_TIMEOUT_SECONDS = 30


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    """经统一管理发送并解析 JSON；平台适配器继续解释业务响应。"""
    request = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)
    with managed_urlopen(request, timeout=timeout, source=__name__) as response:
        raw = response.read()
    text = raw.decode("utf-8")
    return json.loads(text) if text else {}


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "request_json"]
