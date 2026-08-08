from __future__ import annotations

from typing import Literal, TypedDict


CurrencyResolutionMode = Literal[
    "account_locked",
    "site_locked",
    "campaign_locked",
    "selectable",
    "manual_verified",
    "unresolved",
]


class Money(TypedDict):
    amount: str
    currency: str


class ListingCurrencyResolution(TypedDict):
    platform: str
    site: str
    mode: CurrencyResolutionMode
    listing_currency: str
    allowed_currencies: list[str]
    source: str
    verified_at: str


__all__ = [
    "CurrencyResolutionMode",
    "ListingCurrencyResolution",
    "Money",
]
