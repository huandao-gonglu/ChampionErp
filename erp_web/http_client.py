# -*- coding: utf-8 -*-
"""Shared urllib-based JSON HTTP core.

The project previously had five near-identical ad-hoc JSON clients
(category_refresh.http_json, marketplaces.config_http.request_json /
request_form_json, ai_gateway._post_json, product_research's configured-API
requester, browser_ai_runtime._http_json) with diverging timeouts and error
semantics. They now all delegate the transport to :func:`request_json` and keep
only their own header/auth/error conventions.

Error semantics are intentionally NOT unified here: callers that used to see
raw ``urllib.error.HTTPError`` still do; callers that mapped errors keep their
own mapping in their thin wrapper.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 30


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    """Perform an HTTP request and parse the JSON response body.

    Returns ``{}`` for an empty body. Lets ``urllib.error.HTTPError`` /
    ``URLError`` propagate unchanged so wrappers can apply their own policy.
    """
    request = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    text = raw.decode("utf-8")
    return json.loads(text) if text else {}


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "request_json"]
