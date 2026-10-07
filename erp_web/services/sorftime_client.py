"""Sorftime 开放 API：固定端点、单次发送、脱敏错误与额度回执。"""
from __future__ import annotations

import base64
import gzip
import json
import math
import re
import urllib.request
from dataclasses import replace
from typing import Any

from erp_web.services.external_request_context import request_context
from erp_web.services.external_request_manager import managed_urlopen


ENDPOINT_COSTS = {
    "CoinQuery": 1,
    "ProductSearchFromName": 2,
    "ProductSearchFromImage": 2,
    "ProductRequest": 1,
    "ProductVariations": 1,
}


def number(value: Any) -> float | None:
    """负数是数据源的缺失标记，不能当成零销量或零价格。"""
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def rows(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        raise ValueError("Sorftime 返回的数据结构不符合商品列表契约。")
    return [item for item in data if isinstance(item, dict)]


class SorftimeClient:
    def __init__(self, api_key: str, *, timeout: float = 45):
        self.api_key = str(api_key or "").strip()
        if not self.api_key or not re.fullmatch(r"[A-Za-z0-9_-]+", self.api_key):
            raise ValueError("请先配置有效的 Sorftime Account-SK。")
        self.timeout = timeout
        self.receipts: list[dict[str, Any]] = []

    def call(self, endpoint: str, domain: int, params: dict[str, Any]) -> Any:
        if endpoint not in ENDPOINT_COSTS or domain not in {1, 601}:
            raise ValueError("当前选品仅支持 Amazon US 和 1688 的已声明接口。")
        url = f"https://standardapi.sorftime.com/api/{endpoint}?domain={domain}"
        headers = {"Authorization": f"BasicAuth {self.api_key}", "Content-Type": "application/json"}
        data = json.dumps(params, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        ctx = replace(request_context(url, headers, data=data, method="POST", timeout=self.timeout,
                                      source=__name__, semantics="read"), max_attempts=1)
        try:
            with managed_urlopen(request, timeout=self.timeout, request_context=ctx, source=__name__) as response:
                raw = response.read()
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                result = json.loads(gzip.decompress(base64.b64decode(raw, validate=True)))
            if isinstance(result, str):
                result = json.loads(gzip.decompress(base64.b64decode(result, validate=True)))
        except Exception:
            # 上游异常可能包含授权头或请求地址，禁止原样透传。扣费未知时不重试。
            raise ValueError("Sorftime 请求未取得有效回执，扣费情况未知；请核对额度后再操作。") from None
        if not isinstance(result, dict):
            raise ValueError("Sorftime 返回了无效回执。")
        envelope = {str(key).lower(): value for key, value in result.items()}
        self.receipts.append({"endpoint": endpoint, "domain": domain,
                              "request_consumed": number(envelope.get("requestconsumed")),
                              "request_left": number(envelope.get("requestleft"))})
        code = envelope.get("code")
        if code == 11:
            return []
        if code != 0:
            messages = {4: "额度不足", 10: "参数不被数据源接受", 400: "当前 IP 未获授权",
                        401: "接口未开通", 402: "没有数据权限", 500: "月额度已用尽",
                        501: "请求过于频繁", 502: "日额度已用尽", 694: "额度不足或接口未开通"}
            raise ValueError(f"Sorftime：{messages.get(code, '接口未成功返回数据')}（状态 {code if isinstance(code, int) else '未知'}）。")
        return envelope.get("data")


def configured_client(config: dict[str, Any]) -> SorftimeClient:
    providers = config.get("search_providers", [])
    provider = next((p for p in providers if p.get("enabled") and
                     p.get("config_json", {}).get("provider_strategy") == "sorftime"), None)
    if not provider:
        raise ValueError("请先启用 Sorftime 数据源。")
    return SorftimeClient(provider.get("config_json", {}).get("api_key", ""))


__all__ = ["SorftimeClient", "configured_client", "number", "rows"]
