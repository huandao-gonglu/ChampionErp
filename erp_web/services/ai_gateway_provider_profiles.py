"""非 HTTP Provider 的能力配方构造。

HTTP 的 request body 与 api_style 配方由 HTTP Provider 自己持有；CLI 与浏览器
只共享不含协议字段的基础记录，避免为复用一个小字典而依赖 HTTP 实现模块。
"""

from __future__ import annotations

from typing import Any

from . import ai_gateway_probe, ai_model_config


def non_http_capability_profile(
    model: dict[str, Any],
    capability: str,
    *,
    channel: str,
    tested: bool = False,
) -> dict[str, Any]:
    strategy = (
        "external_read"
        if capability == ai_model_config.CAP_WEB_SEARCH
        else f"{channel}_prompt"
    )
    profile = ai_gateway_probe.build_capability_profile(
        model,
        capability,
        strategy=strategy,
    )
    profile["tested"] = tested
    return profile


__all__ = ["non_http_capability_profile"]
