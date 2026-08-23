from __future__ import annotations

from typing import Literal, TypedDict


CurrencyMode = Literal[
    "locked",
    "selectable",
    "manual",
    "unresolved",
]

CurrencyStatus = Literal[
    "ready",
    "selection_required",
    "manual_required",
    "refresh_failed",
    "unresolved",
]


class Money(TypedDict):
    amount: str
    currency: str


class StoreListingCurrency(TypedDict):
    """店铺授权配置中的发布币种状态（唯一事实源）。

    持久化于 ``store_auth.auth_detail_json``；核价与发布只能读取这里的
    ``listing_currency``。注册表、国家、站点、草稿历史值都不是发布真值。
    """

    listing_currency: str
    allowed_currencies: list[str]
    currency_mode: CurrencyMode
    currency_status: CurrencyStatus
    currency_source: str
    currency_verified_at: str
    currency_fingerprint: str
    currency_error_code: str
    currency_error_message: str


class StoreListingCurrencyDiscovery(TypedDict, total=False):
    """平台授权 tester 返回的统一远端币种发现结果。

    - ``supported``：平台/店铺是否提供店铺级发布币种查询能力。
    - ``currencies``：远端返回的币种（已规范化为大写 ISO 代码）。
    - ``source``：发现来源（如 ``account_api`` / ``business_settings`` / ``site_api``）。
    - ``error_code`` / ``error_message``：声明支持但请求失败或响应无效时填写。
    """

    supported: bool
    currencies: list[str]
    source: str
    error_code: str
    error_message: str


class StorePublishContext(TypedDict):
    """发布前构造的不可变店铺发布上下文。

    payload builder 只能从这里读取发布币种，不得从注册表或草稿猜值。
    """

    platform: str
    site: str
    store_identity: str
    listing_currency: str
    currency_fingerprint: str


__all__ = [
    "CurrencyMode",
    "CurrencyStatus",
    "Money",
    "StoreListingCurrency",
    "StoreListingCurrencyDiscovery",
    "StorePublishContext",
]
