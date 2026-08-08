from __future__ import annotations

"""Resolve the currency accepted by a marketplace publishing boundary.

Market display metadata is deliberately not a publishing fallback.  A target
is publishable only when its account/site/campaign rule yields a verified
listing currency.
"""

from typing import Any, cast

from erp_web.marketplace_registry import marketplace_site
from erp_web.schemas.currency import CurrencyResolutionMode, ListingCurrencyResolution


def _currency(value: Any) -> str:
    return str(value or "").strip().upper()


def _resolution(
    platform: str,
    site: str,
    *,
    mode: str,
    currency: str,
    source: str,
    verified_at: str = "",
) -> ListingCurrencyResolution:
    normalized = _currency(currency)
    return {
        "platform": platform,
        "site": site,
        "mode": cast(CurrencyResolutionMode, mode),
        "listing_currency": normalized,
        "allowed_currencies": [normalized] if normalized else [],
        "source": source,
        "verified_at": str(verified_at or "").strip(),
    }


def resolve_listing_currency(
    platform: str,
    site: str,
    store_config: dict[str, Any] | None,
) -> ListingCurrencyResolution:
    platform_key = str(platform or "").strip().lower()
    selected_site = marketplace_site(platform_key, site)
    site_code = str(selected_site.get("code") or site or "").strip()
    store = store_config if isinstance(store_config, dict) else {}

    if platform_key == "ozon":
        currency = _currency(
            store.get("contract_currency")
            or store.get("listing_currency")
        )
        if currency:
            return _resolution(
                platform_key,
                site_code,
                mode="account_locked",
                currency=currency,
                source=str(store.get("currency_source") or "account_api"),
                verified_at=str(
                    store.get("currency_verified_at")
                    or store.get("auth_checked_at")
                    or ""
                ),
            )
        return _resolution(
            platform_key,
            site_code,
            mode="unresolved",
            currency="",
            source="account_api_required",
        )

    if platform_key == "mercadolibre":
        currency = _currency(selected_site.get("listing_currency"))
        return _resolution(
            platform_key,
            site_code,
            mode="site_locked" if currency else "unresolved",
            currency=currency,
            source="site_rule" if currency else "site_rule_missing",
        )

    if platform_key == "yandex":
        currency = _currency(selected_site.get("listing_currency"))
        return _resolution(
            platform_key,
            site_code,
            mode="campaign_locked" if currency else "unresolved",
            currency=currency,
            source="campaign_rule" if currency else "campaign_rule_missing",
        )

    return _resolution(
        platform_key,
        site_code,
        mode="unresolved",
        currency="",
        source="platform_unsupported",
    )


def require_listing_currency(
    platform: str,
    site: str,
    store_config: dict[str, Any] | None,
) -> ListingCurrencyResolution:
    result = resolve_listing_currency(platform, site, store_config)
    if not result["listing_currency"]:
        raise RuntimeError(
            f"{platform or '平台'} 店铺发布币种尚未核验，请先完成店铺授权测试"
        )
    return result


__all__ = ["require_listing_currency", "resolve_listing_currency"]
