"""万邦 1688 调用额度的公开契约，不含企业密钥或买家凭据。"""
from __future__ import annotations

from typing import Literal, TypedDict

DEFAULT_INITIAL_GRANT = 200
MAX_INITIAL_GRANT = 100_000
DAILY_RESERVE_MIN = 50
DAILY_RESERVE_RATIO = 0.05
SHOP_BIND_THROTTLE_SECONDS = 600.0

OneBoundQuotaEntryKind = Literal["grant", "earn", "spend"]
OneBoundQuotaCategory = Literal["", "trade", "auth", "collect"]


class OneBoundQuotaEntry(TypedDict):
    id: str
    member_id: str
    kind: OneBoundQuotaEntryKind
    category: OneBoundQuotaCategory
    amount: int
    ref: str
    created: float


class OneBoundBuyerQuota(TypedDict):
    member_id: str
    account_name: str
    balance: int
    granted: int
    earned: int
    spent_total: int
    spent_trade: int
    spent_auth: int
    spent_collect: int


class OneBoundKeyQuotaStatus(TypedDict):
    configured: bool
    today_snapshot: int | None
    today_estimated: int
    daily_max: int | None
    reserve: int
    expires: str
    days_until_expiry: int | None
    expiring_soon: bool
    expired: bool
    observed_at: float | None
    disabled: bool
    disabled_code: str
    disabled_message: str
    disabled_at: float | None
    exhausted_until: float | None
    initial_grant: int


class OneBoundQuotaView(TypedDict):
    ok: bool
    configured: bool
    is_admin: bool
    active_provider: str
    authorized: bool
    buyer: OneBoundBuyerQuota | None
    buyers: list[OneBoundBuyerQuota]
    key: OneBoundKeyQuotaStatus | None
    entries: list[OneBoundQuotaEntry]


__all__ = [
    "DAILY_RESERVE_MIN",
    "DAILY_RESERVE_RATIO",
    "DEFAULT_INITIAL_GRANT",
    "MAX_INITIAL_GRANT",
    "SHOP_BIND_THROTTLE_SECONDS",
    "OneBoundBuyerQuota",
    "OneBoundKeyQuotaStatus",
    "OneBoundQuotaCategory",
    "OneBoundQuotaEntry",
    "OneBoundQuotaEntryKind",
    "OneBoundQuotaView",
]
