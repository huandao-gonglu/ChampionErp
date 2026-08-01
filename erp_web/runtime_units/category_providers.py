# -*- coding: utf-8 -*-
from __future__ import annotations

"""Mercado Libre 与 Ozon 的统一类目 Provider 注册表。"""

import time
import urllib.parse
from typing import Any

from erp_web.marketplace_registry import (
    CAP_CATEGORY_SEARCH,
    platform_has_capability,
    platform_label,
)
from erp_web.marketplaces.category_provider import CategoryProvider

from .category_refresh import (
    http_json,
    mercadolibre_category_attributes,
    mercadolibre_category_detail,
    mercadolibre_category_record,
)
from .ozon_category_api import (
    fetch_ozon_category_record,
    search_ozon_categories,
)


def _deadline_at(timeout_seconds: float | None) -> float | None:
    if timeout_seconds is None:
        return None
    timeout = float(timeout_seconds)
    if timeout <= 0:
        raise TimeoutError("类目 Provider deadline 已耗尽")
    return time.monotonic() + timeout


def _remaining_timeout(deadline_at: float | None, default: float) -> float:
    if deadline_at is None:
        return default
    remaining = deadline_at - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("类目 Provider deadline 已耗尽")
    return remaining


class MercadoLibreCategoryProvider:
    platform = "mercadolibre"

    def resolve_site(self, site: str = "") -> str:
        from erp_web.context import get_context

        configured = str(
            (
                get_context().config.load_store_config().get(self.platform)
                or {}
            ).get("site_id")
            or ""
        ).strip()
        return str(site or configured or "MLM").strip().upper()

    def _discovery_url(self, site: str, query: str, limit: int) -> str:
        site = self.resolve_site(site)
        quoted_query = urllib.parse.quote(query)
        safe_limit = max(1, min(8, int(limit or 5)))
        if site == "CBT":
            return f"https://api.mercadolibre.com/marketplace/domain_discovery/search?q={quoted_query}&limit={safe_limit}"
        return f"https://api.mercadolibre.com/sites/{urllib.parse.quote(site)}/domain_discovery/search?q={quoted_query}&limit={safe_limit}"

    def detail(
        self,
        category_id: str,
        site: str = "",
        *,
        include_attributes: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        category_id = str(category_id or "").strip()
        if not category_id:
            raise RuntimeError("缺少 Mercado Libre 类目 ID。")
        resolved_site = self.resolve_site(site)
        deadline_at = _deadline_at(timeout_seconds)

        def scoped_http_client(
            url: str,
            access_token: str | None = None,
        ) -> dict[str, Any] | list[Any]:
            if deadline_at is None:
                return http_json(url, access_token)
            return http_json(
                url,
                access_token,
                timeout_seconds=_remaining_timeout(deadline_at, 8),
            )

        detail = mercadolibre_category_detail(
            category_id,
            http_client=scoped_http_client,
        )
        attrs = (
            mercadolibre_category_attributes(
                category_id,
                http_client=scoped_http_client,
            )
            if include_attributes
            else {"required": [], "optional": []}
        )
        return mercadolibre_category_record(detail, resolved_site, attrs)

    def discover(
        self,
        query: str,
        site: str = "",
        limit: int = 8,
        *,
        timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        if not query:
            return []
        resolved_site = self.resolve_site(site)
        discovery_url = self._discovery_url(resolved_site, query, limit)
        if timeout_seconds is None:
            data = http_json(discovery_url)
        else:
            data = http_json(
                discovery_url,
                timeout_seconds=_remaining_timeout(
                    _deadline_at(timeout_seconds),
                    8,
                ),
            )
        if not isinstance(data, list):
            raise RuntimeError(
                "Mercado Libre domain discovery 响应不是列表。"
            )
        discoveries: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            category_id = str(item.get("category_id") or "").strip()
            if not category_id or category_id in seen_ids:
                continue
            name = str(
                item.get("category_name")
                or item.get("domain_name")
                or category_id
            ).strip()
            domain_name = str(item.get("domain_name") or "").strip()
            path = list(
                dict.fromkeys(
                    value
                    for value in (domain_name, name)
                    if value
                )
            )
            discoveries.append(
                {
                    "category_id": category_id,
                    "name": name,
                    "path_original": path or [name],
                    "site": resolved_site,
                    "provider_rank": index,
                    "raw": item,
                }
            )
            seen_ids.add(category_id)
            if len(discoveries) >= max(1, min(8, int(limit or 8))):
                break
        if data and not discoveries:
            raise RuntimeError(
                "Mercado Libre domain discovery 未返回有效 category_id。"
            )
        return discoveries

class OzonCategoryProvider:
    platform = "ozon"

    def resolve_site(self, site: str = "") -> str:
        del site
        return "global"

    def detail(
        self,
        category_id: str,
        site: str = "",
        *,
        include_attributes: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        del site
        return fetch_ozon_category_record(
            category_id,
            include_attributes=include_attributes,
            timeout_seconds=timeout_seconds,
        )

    def search(
        self,
        query: str,
        site: str = "",
        limit: int = 5,
        *,
        timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        del site
        return search_ozon_categories(
            query,
            limit=limit,
            timeout_seconds=timeout_seconds,
        )


_CATEGORY_PROVIDERS: dict[str, CategoryProvider] = {
    MercadoLibreCategoryProvider.platform: MercadoLibreCategoryProvider(),
    OzonCategoryProvider.platform: OzonCategoryProvider(),
}


def category_provider_for(platform: str) -> CategoryProvider | None:
    key = str(platform or "").strip().lower()
    if not platform_has_capability(key, CAP_CATEGORY_SEARCH):
        return None
    return _CATEGORY_PROVIDERS.get(key)


def require_category_provider(platform: str) -> CategoryProvider:
    provider = category_provider_for(platform)
    if provider is None:
        raise RuntimeError(f"{platform_label(platform)}尚未接入官方实时类目接口。")
    return provider


__all__ = [
    "MercadoLibreCategoryProvider",
    "OzonCategoryProvider",
    "category_provider_for",
    "require_category_provider",
]
