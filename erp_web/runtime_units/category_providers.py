# -*- coding: utf-8 -*-
from __future__ import annotations

"""Mercado Libre 与 Ozon 的统一类目 Provider 注册表。"""

from datetime import datetime, timezone
import hashlib
import urllib.parse
from typing import Any

from erp_web.marketplace_registry import (
    CAP_CATEGORY_SEARCH,
    platform_has_capability,
    platform_label,
)
from erp_web.marketplaces.category_provider import CategoryProvider
from erp_web.schemas.category import (
    CategoryCorpusInfo,
    CategoryProviderPreflight,
)

from .category_refresh import (
    http_json,
    mercadolibre_category_attributes,
    mercadolibre_category_detail,
    mercadolibre_category_record,
)
from .ozon_category_api import (
    fetch_ozon_category_record,
    load_ozon_category_corpus,
    search_ozon_categories,
)


def _path_text(record: dict[str, Any]) -> str:
    path = record.get("path_original") if isinstance(record.get("path_original"), list) else []
    if path:
        return " / ".join(str(item).strip() for item in path if str(item).strip())
    return str(record.get("category_path") or record.get("name_original") or record.get("category_id") or "").strip()


class MercadoLibreCategoryProvider:
    platform = "mercadolibre"

    def preflight(self, site: str = "") -> CategoryProviderPreflight:
        resolved_site = self.resolve_site(site)
        now = datetime.now(timezone.utc).isoformat()
        scope_hash = hashlib.sha256(
            f"mercadolibre:public:{resolved_site}".encode("utf-8")
        ).hexdigest()
        return {
            "ok": True,
            "platform": self.platform,
            "site": resolved_site,
            "retrieval_mode": "remote_discovery",
            "corpus_info": {
                "corpus_hash": "",
                "taxonomy_version": None,
                "locale": _mercadolibre_locale(resolved_site),
                "retrieved_at": now,
                "expires_at": now,
                "credential_scope_hash": f"sha256:{scope_hash}",
            },
        }

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
    ) -> dict[str, Any]:
        category_id = str(category_id or "").strip()
        if not category_id:
            raise RuntimeError("缺少 Mercado Libre 类目 ID。")
        resolved_site = self.resolve_site(site)
        detail = mercadolibre_category_detail(category_id, http_client=http_json)
        attrs = (
            mercadolibre_category_attributes(category_id, http_client=http_json)
            if include_attributes
            else {"required": [], "optional": []}
        )
        return mercadolibre_category_record(detail, resolved_site, attrs)

    def discover(
        self,
        query: str,
        site: str = "",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        if not query:
            return []
        resolved_site = self.resolve_site(site)
        data = http_json(self._discovery_url(resolved_site, query, limit))
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
            discoveries.append(
                {
                    "category_id": category_id,
                    "name": name,
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

    def search(self, query: str, site: str = "", limit: int = 5) -> list[dict[str, Any]]:
        resolved_site = self.resolve_site(site)
        suggestions: list[dict[str, Any]] = []
        for discovery in self.discover(query, site=resolved_site, limit=limit):
            category_id = str(discovery.get("category_id") or "").strip()
            record = self.detail(category_id, site=resolved_site)
            name = str(
                discovery.get("name")
                or record.get("name_original")
                or category_id
            ).strip()
            path = _path_text(record) or name
            index = int(discovery.get("provider_rank") or 0)
            suggestions.append(
                {
                    "id": category_id,
                    "category_id": category_id,
                    "name": name,
                    "path": path,
                    "category_path": path,
                    "path_ids": (
                        record.get("path_ids")
                        if isinstance(record.get("path_ids"), list)
                        else []
                    ),
                    "site": resolved_site,
                    "score": max(1, 100 - index * 5),
                    "matched_terms": [str(query or "").strip()],
                    "source": "mercadolibre_domain_discovery",
                    "raw": {
                        "domain_discovery": (
                            discovery.get("raw")
                            if isinstance(discovery.get("raw"), dict)
                            else {}
                        ),
                        "category": (
                            record.get("raw")
                            if isinstance(record.get("raw"), dict)
                            else {}
                        ),
                        "path_ids": (
                            record.get("path_ids")
                            if isinstance(record.get("path_ids"), list)
                            else []
                        ),
                    },
                }
            )
        return suggestions


class OzonCategoryProvider:
    platform = "ozon"

    def preflight(self, site: str = "") -> CategoryProviderPreflight:
        resolved_site = self.resolve_site(site)
        _, corpus_info = self.category_corpus(resolved_site)
        return {
            "ok": True,
            "platform": self.platform,
            "site": resolved_site,
            "retrieval_mode": "full_tree_local",
            "corpus_info": corpus_info,
        }

    def resolve_site(self, site: str = "") -> str:
        del site
        return "global"

    def detail(
        self,
        category_id: str,
        site: str = "",
        *,
        include_attributes: bool = False,
    ) -> dict[str, Any]:
        del site
        return fetch_ozon_category_record(category_id, include_attributes=include_attributes)

    def search(self, query: str, site: str = "", limit: int = 5) -> list[dict[str, Any]]:
        del site
        return search_ozon_categories(query, limit=limit)

    def category_corpus(
        self,
        site: str = "",
    ) -> tuple[list[dict[str, Any]], CategoryCorpusInfo]:
        del site
        return load_ozon_category_corpus()


def _mercadolibre_locale(site: str) -> str:
    return {
        "MLA": "es-AR",
        "MLB": "pt-BR",
        "MLC": "es-CL",
        "MCO": "es-CO",
        "MLM": "es-MX",
        "MPE": "es-PE",
        "MLU": "es-UY",
        "CBT": "es-MX",
    }.get(str(site or "").strip().upper(), "es-419")


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
