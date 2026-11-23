# -*- coding: utf-8 -*-
from __future__ import annotations

"""Mercado Libre、Ozon 与 Yandex 的统一类目 Provider 注册表。

每个平台显式继承 :class:`CategoryProvider` ABC：类目详情、属性定义与枚举
值三个核心契约缺失会在实例化阶段失败。属性定义、类目树与枚举/只读 HTTP
缓存统一归口到 :class:`CategoryCatalogStore`（SQLite WAL + FTS5）。

类目搜索与树导航是可选能力：``search_categories`` / ``root_categories`` /
``browse_categories`` 只在支持的平台实现，消费者用
``CategorySearchProvider`` / ``CategoryNavigationProvider`` 运行时判断。
"""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import timedelta
import hashlib
import json
import time
from typing import Any
import urllib.error
import urllib.parse

from erp_web.marketplace_registry import (
    CAP_CATEGORY_ATTRIBUTES,
    platform_has_capability,
    platform_label,
)
from erp_web.marketplaces.category_provider import CategoryProvider
from erp_web.schemas.category_definition import (
    CategoryAttributeValue,
    CategoryAttributeValuePage,
    CategoryDefinition,
    CategoryDetail,
)
from erp_web.stores.category_catalog_store import (
    DEFINITION_CACHE_FRESH_TTL,
    DEFINITION_CACHE_MAX_AGE,
)

from .category_definition_support import (
    CategoryAttributesUnavailableError,
    definition_from_legacy_attributes,
    is_transient_category_api_error,
    paginate_value_candidates,
)
from .category_refresh import (
    http_json,
    mercadolibre_catalog_attribute_top_values,
    mercadolibre_category_attributes,
    mercadolibre_category_detail,
    mercadolibre_category_record,
    mercadolibre_category_technical_specs,
    normalize_ml_attribute,
)
from .ozon_category_api import (
    fetch_ozon_category_attribute_values,
    fetch_ozon_category_children,
    fetch_ozon_category_record,
    fetch_ozon_category_roots,
    fetch_ozon_category_siblings,
    load_ozon_category_corpus,
    ozon_credential_scope_hash,
    search_ozon_categories,
)
from .store_credentials import get_mercadolibre_access_token
from .yandex_category_api import (
    cached_yandex_category_ancestor_map,
    fetch_yandex_category_children,
    fetch_yandex_category_parameter_definitions,
    fetch_yandex_category_roots,
    fetch_yandex_category_siblings,
    fetch_yandex_leaf_record,
    search_yandex_categories,
    yandex_category_ancestor_ids,
    yandex_credential_scope_hash,
)

_ML_HTTP_CACHE_TTL_SECONDS = 900.0
_ML_SEARCH_DETAIL_ENRICH_LIMIT = 8


def _deadline_at(timeout_seconds: float | None) -> float | None:
    if timeout_seconds is None:
        return None
    timeout = float(timeout_seconds)
    if timeout <= 0:
        raise TimeoutError("类目 Provider deadline 已耗尽")
    return time.monotonic() + timeout


def _remaining_timeout(
    deadline_at: float | None,
    default: float | None = None,
) -> float | None:
    if deadline_at is None:
        return default
    remaining = deadline_at - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("类目 Provider deadline 已耗尽")
    return remaining


def _catalog_store():
    from erp_web.context import get_context

    return get_context().category_catalog_store


def _is_cacheable_ml_http(method: str, url: str) -> bool:
    path = urllib.parse.urlsplit(url).path
    if method == "GET":
        return "/categories/" in path or "/catalog_domains/" in path
    if method == "POST":
        return "/catalog_domains/" in path and path.rstrip("/").endswith("/top_values")
    return False


def _ml_http_cache_key(
    method: str,
    url: str,
    scope_key: str,
    payload: Any,
) -> str:
    payload_text = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if payload is not None
        else ""
    )
    digest = hashlib.sha256(
        f"{method}|{url}|{payload_text}".encode("utf-8")
    ).hexdigest()
    return f"ml_http:{scope_key}:{method}:{digest}"


class MercadoLibreCategoryProvider(CategoryProvider):
    platform = "mercadolibre"

    def _store_config(self) -> dict[str, Any]:
        from erp_web.context import get_context

        config = get_context().config.load_store_config().get(self.platform)
        return config if isinstance(config, dict) else {}

    @staticmethod
    def _resolved_site(site: str, store_config: dict[str, Any]) -> str:
        configured = str(store_config.get("site_id") or "").strip()
        return str(site or configured or "MLM").strip().upper()

    def resolve_site(self, site: str = "") -> str:
        return self._resolved_site(site, self._store_config())

    def _access_token_and_scope(self) -> tuple[str, str]:
        token = get_mercadolibre_access_token()
        store_config = self._store_config()
        user_id = str(store_config.get("user_id") or "").strip()
        if user_id:
            scope_identity = f"user:{user_id}"
        elif token:
            scope_identity = f"token:{token}"
        else:
            scope_identity = "public"
        digest = hashlib.sha256(scope_identity.encode("utf-8")).hexdigest()
        return token, f"sha256:{digest}"

    def _discovery_url(self, site: str, query: str, limit: int | None) -> str:
        site = str(site or "MLM").strip().upper()
        quoted_query = urllib.parse.quote(query)
        safe_limit = max(1, min(8, int(limit or 8)))
        if site == "CBT":
            return f"https://api.mercadolibre.com/marketplace/domain_discovery/search?q={quoted_query}&limit={safe_limit}"
        return f"https://api.mercadolibre.com/sites/{urllib.parse.quote(site)}/domain_discovery/search?q={quoted_query}&limit={safe_limit}"

    @staticmethod
    def _request_with_auth_refresh(url, access_token=None, **kwargs):
        try:
            return http_json(url, access_token, **kwargs)
        except Exception as exc:
            status = (
                exc.code
                if isinstance(exc, urllib.error.HTTPError)
                else (getattr(exc, "details", {}) or {}).get("http_status")
            )
            if status != 401 or not access_token:
                raise
            token = get_mercadolibre_access_token(
                {"mercadolibre": {"access_token": access_token}},
                force_refresh=True,
            )
            return http_json(url, token, **kwargs)

    def _scoped_http_client(
        self,
        deadline_at: float | None,
        *,
        scope_key: str = "public",
        force_refresh: bool = False,
    ):
        def scoped_http_client(
            url: str,
            access_token: str | None = None,
            *,
            method: str = "GET",
            payload: dict[str, Any] | list[Any] | None = None,
        ) -> dict[str, Any] | list[Any]:
            normalized_method = str(method or "GET").strip().upper()

            def _do_request() -> dict[str, Any] | list[Any]:
                if deadline_at is None:
                    if normalized_method == "GET" and payload is None:
                        return self._request_with_auth_refresh(url, access_token)
                    return self._request_with_auth_refresh(
                        url,
                        access_token,
                        method=normalized_method,
                        payload=payload,
                    )
                timeout = _remaining_timeout(deadline_at, 8) or 8
                if normalized_method == "GET" and payload is None:
                    return self._request_with_auth_refresh(
                        url,
                        access_token,
                        timeout_seconds=timeout,
                    )
                return self._request_with_auth_refresh(
                    url,
                    access_token,
                    timeout_seconds=timeout,
                    method=normalized_method,
                    payload=payload,
                )

            if not _is_cacheable_ml_http(normalized_method, url):
                return _do_request()

            resolved_scope = scope_key if access_token else "public"
            cache_key = _ml_http_cache_key(
                normalized_method,
                url,
                resolved_scope,
                payload,
            )
            return _catalog_store().load_cached_value(
                cache_key,
                platform=self.platform,
                scope_key=resolved_scope,
                loader=_do_request,
                ttl_seconds=_ML_HTTP_CACHE_TTL_SECONDS,
                force_refresh=force_refresh,
            )

        return scoped_http_client

    def category_detail(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> CategoryDetail:
        category_id = str(category_id or "").strip()
        if not category_id:
            raise RuntimeError("缺少 Mercado Libre 类目 ID。")
        resolved_site = self.resolve_site(site)
        deadline_at = _deadline_at(timeout_seconds)
        is_cbt = resolved_site == "CBT" or category_id.upper().startswith("CBT")
        if is_cbt:
            access_token, scope_key = self._access_token_and_scope()
        else:
            access_token, scope_key = "", "public"
        detail = mercadolibre_category_detail(
            category_id,
            access_token or None,
            http_client=self._scoped_http_client(deadline_at, scope_key=scope_key),
        )
        record = mercadolibre_category_record(detail, resolved_site, None)
        children = detail.get("children_categories")
        is_leaf = isinstance(children, list) and len(children) == 0
        return CategoryDetail(
            platform=self.platform,
            site=resolved_site,
            category_id=str(record.get("category_id") or category_id),
            name=str(record.get("name_original") or category_id),
            path=str(record.get("category_path") or ""),
            parent_id=str(record.get("parent_id") or ""),
            is_leaf=is_leaf,
            active=True,
        )

    def attribute_definitions(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
        force_refresh: bool = False,
    ) -> CategoryDefinition:
        category_id = str(category_id or "").strip()
        if not category_id:
            raise RuntimeError("缺少 Mercado Libre 类目 ID。")
        resolved_site = self.resolve_site(site)
        deadline_at = _deadline_at(timeout_seconds)
        is_cbt = resolved_site == "CBT" or category_id.upper().startswith("CBT")
        if is_cbt:
            access_token, credential_scope_hash = self._access_token_and_scope()
        else:
            access_token, credential_scope_hash = "", "public"

        def live_loader() -> CategoryDefinition:
            http = self._scoped_http_client(
                deadline_at,
                scope_key=credential_scope_hash,
                force_refresh=force_refresh,
            )
            detail = mercadolibre_category_detail(
                category_id,
                access_token or None,
                http_client=http,
            )
            technical_specs = (
                mercadolibre_category_technical_specs(
                    category_id,
                    access_token,
                    http_client=http,
                )
                if is_cbt
                else {}
            )
            attrs = mercadolibre_category_attributes(
                category_id,
                access_token or None,
                http_client=http,
                technical_specs=technical_specs,
            )
            settings = (
                detail.get("settings")
                if isinstance(detail.get("settings"), dict)
                else {}
            )
            catalog_domain = str(
                settings.get("catalog_domain") or detail.get("domain_id") or ""
            ).strip()
            brand_definition = next(
                (
                    item
                    for group in ("required", "optional")
                    for item in attrs.get(group, [])
                    if isinstance(item, dict)
                    and str(item.get("id") or "").strip() == "BRAND"
                ),
                None,
            )
            if (
                is_cbt
                and catalog_domain
                and isinstance(brand_definition, dict)
                and brand_definition.get("allow_custom_values") is False
            ):
                brand_values = mercadolibre_catalog_attribute_top_values(
                    catalog_domain,
                    "BRAND",
                    access_token,
                    http_client=http,
                    limit=51,
                )
                raw_brand = (
                    brand_definition.get("raw")
                    if isinstance(brand_definition.get("raw"), dict)
                    else {}
                )
                enriched_brand = normalize_ml_attribute(
                    raw_brand,
                    allow_custom_values=False,
                    supplemental_values=brand_values,
                )
                for group in ("required", "optional"):
                    attrs[group] = [
                        enriched_brand
                        if item is brand_definition
                        else item
                        for item in attrs.get(group, [])
                    ]
            record = mercadolibre_category_record(detail, resolved_site, attrs)
            attributes = (
                record.get("attributes")
                if isinstance(record.get("attributes"), dict)
                else {}
            )
            return definition_from_legacy_attributes(
                platform=self.platform,
                site=resolved_site,
                category_id=str(record.get("category_id") or category_id),
                category_path=str(record.get("category_path") or ""),
                description_category_id="",
                required=list(attributes.get("required") or []),
                optional=list(attributes.get("optional") or []),
            )

        return _catalog_store().load_definition_through_cache(
            platform=self.platform,
            credential_scope_hash=credential_scope_hash,
            site=resolved_site,
            category_id=category_id,
            live_loader=live_loader,
            is_transient_error=is_transient_category_api_error,
            unavailable_error_factory=CategoryAttributesUnavailableError,
            force_refresh=force_refresh,
            fresh_ttl=DEFINITION_CACHE_FRESH_TTL,
            max_age=DEFINITION_CACHE_MAX_AGE,
        )

    def attribute_values(
        self,
        category_id: str,
        attribute_id: str,
        *,
        site: str = "",
        query: str = "",
        cursor: str = "",
        limit: int = 50,
        timeout_seconds: float | None = None,
    ) -> CategoryAttributeValuePage:
        resolved_site = self.resolve_site(site)
        deadline_at = _deadline_at(timeout_seconds)
        normalized_category_id = str(category_id or "").strip()
        is_cbt = (
            resolved_site == "CBT"
            or normalized_category_id.upper().startswith("CBT")
        )
        if is_cbt:
            access_token, scope_key = self._access_token_and_scope()
        else:
            access_token, scope_key = "", "public"
        http = self._scoped_http_client(deadline_at, scope_key=scope_key)
        raw_attributes = mercadolibre_category_attributes(
            normalized_category_id,
            access_token or None,
            http_client=http,
        )
        candidates: list[tuple[str, str]] = []
        target_id = str(attribute_id or "").strip()
        target_exists = False
        for group in ("required", "optional"):
            for item in raw_attributes.get(group) or []:
                if not isinstance(item, dict):
                    continue
                if str(item.get("id") or "").strip() != target_id:
                    continue
                target_exists = True
                raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
                for row in raw.get("values") or []:
                    if not isinstance(row, dict):
                        continue
                    value = str(row.get("name") or row.get("id") or "").strip()
                    if not value:
                        continue
                    candidates.append((value, str(row.get("id") or "").strip()))
        if not target_exists:
            raise ValueError(
                f"Mercado Libre 类目 {normalized_category_id} 没有属性 {target_id}，请使用当前类目定义中的属性 ID。"
            )
        if is_cbt and target_id == "BRAND":
            detail = mercadolibre_category_detail(
                normalized_category_id,
                access_token,
                http_client=http,
            )
            settings = (
                detail.get("settings")
                if isinstance(detail.get("settings"), dict)
                else {}
            )
            catalog_domain = str(
                settings.get("catalog_domain") or detail.get("domain_id") or ""
            ).strip()
            if catalog_domain:
                for row in mercadolibre_catalog_attribute_top_values(
                    catalog_domain,
                    target_id,
                    access_token,
                    http_client=http,
                    limit=1000,
                ):
                    value = str(row.get("name") or row.get("id") or "").strip()
                    if value:
                        candidates.append(
                            (value, str(row.get("id") or "").strip())
                        )
        candidates = list(dict.fromkeys(candidates))
        return paginate_value_candidates(
            candidates,
            platform=self.platform,
            site=resolved_site,
            category_id=normalized_category_id,
            attribute_id=target_id,
            query=query,
            cursor=cursor,
            limit=limit,
        )

    def _enrich_discovery_paths(
        self,
        discoveries: list[dict[str, Any]],
        *,
        resolved_site: str,
        deadline_at: float | None,
    ) -> list[dict[str, Any]]:
        """并发补全 domain_discovery 候选的真实完整类目路径（命中 SQLite 缓存时 0ms）。"""

        if not discoveries:
            return discoveries
        targets = discoveries[:_ML_SEARCH_DETAIL_ENRICH_LIMIT]

        def _fetch_one(item: dict[str, Any]) -> tuple[str, list[str] | None]:
            cid = str(item.get("category_id") or "").strip()
            if not cid:
                return cid, None
            try:
                if deadline_at is not None:
                    remaining = _remaining_timeout(deadline_at, 4.0)
                    if remaining is not None and remaining < 0.3:
                        return cid, None
                    detail_timeout: float | None = min(float(remaining or 4.0), 4.0)
                else:
                    detail_timeout = None
                detail = self.category_detail(
                    cid,
                    site=resolved_site,
                    timeout_seconds=detail_timeout,
                )
                segments = [
                    part.strip()
                    for part in str(detail.path or "").split(" / ")
                    if part.strip()
                ]
                return cid, segments or None
            except Exception:
                return cid, None

        enriched_map: dict[str, list[str]] = {}
        if len(targets) == 1:
            cid, segments = _fetch_one(targets[0])
            if cid and segments:
                enriched_map[cid] = segments
        else:
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="ml-cat-path") as pool:
                futures = [
                    pool.submit(copy_context().run, _fetch_one, item)
                    for item in targets
                ]
                for future in futures:
                    try:
                        cid, segments = future.result()
                        if cid and segments:
                            enriched_map[cid] = segments
                    except Exception:
                        continue

        for item in discoveries:
            cid = str(item.get("category_id") or "").strip()
            if cid in enriched_map:
                item["path_original"] = enriched_map[cid]
                item["category_path"] = " / ".join(enriched_map[cid])
        return discoveries

    def search_categories(
        self,
        query: str,
        *,
        site: str = "",
        limit: int | None = 8,
        timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        if not query:
            return []
        resolved_site = self.resolve_site(site)
        deadline_at = _deadline_at(timeout_seconds)
        discovery_url = self._discovery_url(resolved_site, query, limit)
        access_token: str | None = None
        if resolved_site == "CBT":
            access_token = self._access_token_and_scope()[0]
        if deadline_at is None:
            data = self._request_with_auth_refresh(discovery_url, access_token)
        else:
            data = self._request_with_auth_refresh(
                discovery_url,
                access_token,
                timeout_seconds=_remaining_timeout(deadline_at, 8) or 8,
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
        return self._enrich_discovery_paths(
            discoveries,
            resolved_site=resolved_site,
            deadline_at=deadline_at,
        )


class OzonCategoryProvider(CategoryProvider):
    platform = "ozon"

    def resolve_site(self, site: str = "") -> str:
        del site
        return "global"

    def category_detail(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> CategoryDetail:
        del site
        record = fetch_ozon_category_record(
            category_id,
            include_attributes=False,
            timeout_seconds=timeout_seconds,
        )
        return CategoryDetail(
            platform=self.platform,
            site="global",
            category_id=str(record.get("type_id") or category_id),
            description_category_id=str(record.get("description_category_id") or ""),
            name=str(record.get("name_original") or category_id),
            name_zh=str(record.get("name_zh") or ""),
            path=str(record.get("category_path") or ""),
            path_zh=str(record.get("path_zh") or ""),
            parent_id=str(record.get("parent_id") or ""),
            is_leaf=True,
            active=True,
        )

    def attribute_definitions(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
        force_refresh: bool = False,
    ) -> CategoryDefinition:
        del site
        category_id = str(category_id or "").strip()
        if not category_id:
            raise RuntimeError("缺少 Ozon 商品类型 ID。")
        deadline_at = _deadline_at(timeout_seconds)
        scope_hash = ozon_credential_scope_hash()

        def live_loader() -> CategoryDefinition:
            if force_refresh:
                _catalog_store().clear_cached_values(
                    platform=self.platform,
                    scope_key=scope_hash,
                )
            record = fetch_ozon_category_record(
                category_id,
                include_attributes=True,
                timeout_seconds=_remaining_timeout(deadline_at),
            )
            attributes = (
                record.get("attributes")
                if isinstance(record.get("attributes"), dict)
                else {}
            )
            return definition_from_legacy_attributes(
                platform=self.platform,
                site="global",
                category_id=str(record.get("type_id") or category_id),
                category_path=str(record.get("category_path") or ""),
                category_path_zh=str(record.get("path_zh") or ""),
                description_category_id=str(
                    record.get("description_category_id") or ""
                ),
                required=list(attributes.get("required") or []),
                optional=list(attributes.get("optional") or []),
            )

        # 凭据缺失时 scope 计算抛出确定性错误，stale 缓存不得掩盖。
        return _catalog_store().load_definition_through_cache(
            platform=self.platform,
            credential_scope_hash=scope_hash,
            site="global",
            category_id=category_id,
            live_loader=live_loader,
            is_transient_error=is_transient_category_api_error,
            unavailable_error_factory=CategoryAttributesUnavailableError,
            force_refresh=force_refresh,
            fresh_ttl=DEFINITION_CACHE_FRESH_TTL,
            max_age=DEFINITION_CACHE_MAX_AGE,
        )

    def attribute_values(
        self,
        category_id: str,
        attribute_id: str,
        *,
        site: str = "",
        query: str = "",
        cursor: str = "",
        limit: int = 50,
        timeout_seconds: float | None = None,
    ) -> CategoryAttributeValuePage:
        del site
        result = fetch_ozon_category_attribute_values(
            category_id,
            attribute_id,
            query=query,
            limit=limit,
            start_after_value_id=cursor,
            timeout_seconds=timeout_seconds,
        )
        safe_limit = max(1, min(100, int(limit or 50)))
        values = tuple(
            CategoryAttributeValue(
                value=str(row.get("value") or "").strip(),
                dictionary_value_id=str(row.get("id") or "").strip(),
                label_zh=str(row.get("label_zh") or "").strip(),
            )
            for row in (result.get("values") or [])
            if isinstance(row, dict) and str(row.get("value") or "").strip()
        )
        last_id = values[-1].dictionary_value_id if values else ""
        has_more = bool(result.get("has_more")) and bool(values)
        return CategoryAttributeValuePage(
            platform=self.platform,
            site="global",
            category_id=str(category_id or "").strip(),
            attribute_id=str(attribute_id or "").strip(),
            limit=safe_limit,
            cursor=str(cursor or "").strip(),
            values=values,
            next_cursor=last_id if has_more else "",
            has_more=has_more,
        )

    def search_categories(
        self,
        query: str,
        *,
        site: str = "",
        limit: int | None = 5,
        timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        del site
        return search_ozon_categories(
            query,
            limit=limit,
            timeout_seconds=timeout_seconds,
        )

    def root_categories(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return fetch_ozon_category_roots(timeout_seconds=timeout_seconds)

    def browse_categories(
        self,
        parent_ids: list[str],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return fetch_ozon_category_children(
            parent_ids,
            timeout_seconds=timeout_seconds,
        )

    def sibling_leaves(
        self,
        category_ids: list[str],
        *,
        limit: int = 12,
        timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        return fetch_ozon_category_siblings(
            category_ids,
            limit=limit,
            timeout_seconds=timeout_seconds,
        )

    def list_leaf_records(
        self,
        *,
        credentials: tuple[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        records, _ = load_ozon_category_corpus(
            credentials=credentials,
            timeout_seconds=timeout_seconds,
        )
        return records


def _yandex_parameter_definition(parameter: dict[str, Any]) -> dict[str, Any]:
    """Yandex 类目参数 → legacy 共享属性字典的机械转换。"""

    parameter_id = str(parameter.get("parameter_id") or "").strip()
    values = [
        item
        for item in (parameter.get("values") or [])
        if isinstance(item, dict)
    ]
    parameter_type = str(parameter.get("parameter_type") or "TEXT").strip()
    is_enum = parameter_type.upper() == "ENUM" or bool(values)
    allow_custom_values = bool(parameter.get("allow_custom_values"))
    if is_enum:
        value_mode = "open_enum" if allow_custom_values else "strict_enum"
    else:
        value_mode = "free_text"
    dictionary_id = f"yandex-parameter-{parameter_id}" if is_enum and parameter_id else ""
    units = [
        item for item in (parameter.get("units") or []) if isinstance(item, dict)
    ]
    unit_ids = {
        str(item.get("name") or "").strip(): str(item.get("id") or "").strip()
        for item in units
        if str(item.get("name") or "").strip() and str(item.get("id") or "").strip()
    }
    return {
        "variation_role": "variant" if parameter.get("distinctive") else "",
        "id": parameter_id,
        "name": str(parameter.get("name") or parameter_id).strip(),
        "required": bool(parameter.get("required")),
        "value_type": parameter_type.lower(),
        "value_mode": value_mode,
        "allow_custom_values": allow_custom_values,
        "unit": str(parameter.get("unit") or "").strip(),
        "unit_options": [
            str(item).strip()
            for item in (parameter.get("unit_options") or [])
            if str(item or "").strip()
        ],
        "default_unit": str(parameter.get("default_unit") or "").strip(),
        "unit_ids": unit_ids,
        "default_unit_id": str(parameter.get("default_unit_id") or "").strip(),
        "constraints": {
            **dict(parameter.get("constraints") or {}),
            **({"text_format": "name_value_lines"} if parameter_id == "57046341" else {}),
        },
        "dictionary_id": dictionary_id,
        "is_dictionary": bool(dictionary_id),
        "is_collection": bool(parameter.get("is_collection")),
        "max_value_count": int(parameter.get("max_value_count") or 0),
        "category_dependent": False,
        "options": [str(item.get("value") or "").strip() for item in values],
        "values": [
            {
                "id": str(item.get("value_id") or "").strip(),
                "value": str(item.get("value") or "").strip(),
                "name": str(item.get("value") or "").strip(),
            }
            for item in values
            if str(item.get("value") or "").strip()
        ],
        "description": str(parameter.get("description") or "").strip(),
        "platform_type": parameter_type,
    }


class YandexCategoryProvider(CategoryProvider):
    """Yandex 官方类目树/类目参数的通用 Provider。

    只允许选择叶子类目；类目树使用本地 SQLite FTS5 搜索与导航，不为每次输入发远端请求。
    参数定义的新鲜窗口缩短到 6 小时，尊重 Yandex 的小时级配额。
    """

    platform = "yandex"
    definition_fresh_ttl_seconds = 6 * 3600

    def resolve_site(self, site: str = "") -> str:
        del site
        return "global"

    def category_detail(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> CategoryDetail:
        del site
        record = fetch_yandex_leaf_record(
            category_id,
            include_attributes=False,
            timeout_seconds=timeout_seconds,
        )
        return CategoryDetail(
            platform=self.platform,
            site="global",
            category_id=str(record.get("category_id") or category_id),
            name=str(record.get("name_original") or category_id),
            path=str(record.get("category_path") or ""),
            parent_id=str(record.get("parent_id") or ""),
            ancestor_ids=tuple(
                str(item).strip()
                for item in (record.get("ancestor_ids") or ())
                if str(item).strip()
            ),
            is_leaf=bool(record.get("is_leaf", True)),
            active=True,
        )

    def attribute_definitions(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
        force_refresh: bool = False,
    ) -> CategoryDefinition:
        del site
        category_id = str(category_id or "").strip()
        if not category_id:
            raise RuntimeError("缺少 Yandex 类目 ID。")
        deadline_at = _deadline_at(timeout_seconds)

        def live_loader() -> CategoryDefinition:
            record = fetch_yandex_leaf_record(
                category_id,
                include_attributes=True,
                force_refresh=force_refresh,
                timeout_seconds=_remaining_timeout(deadline_at),
            )
            attributes = (
                record.get("attributes")
                if isinstance(record.get("attributes"), dict)
                else {}
            )
            required = [
                _yandex_parameter_definition(item)
                for item in (attributes.get("required") or [])
                if isinstance(item, dict)
            ]
            optional = [
                _yandex_parameter_definition(item)
                for item in (attributes.get("optional") or [])
                if isinstance(item, dict)
            ]
            return definition_from_legacy_attributes(
                platform=self.platform,
                site="global",
                category_id=str(record.get("category_id") or category_id),
                category_path=str(record.get("category_path") or ""),
                description_category_id="",
                required=required,
                optional=optional,
            )

        # 凭据缺失时 scope 计算抛出确定性错误，stale 缓存不得掩盖。
        return _catalog_store().load_definition_through_cache(
            platform=self.platform,
            credential_scope_hash=yandex_credential_scope_hash(),
            site="global",
            category_id=category_id,
            live_loader=live_loader,
            is_transient_error=is_transient_category_api_error,
            unavailable_error_factory=CategoryAttributesUnavailableError,
            force_refresh=force_refresh,
            fresh_ttl=timedelta(seconds=self.definition_fresh_ttl_seconds),
            max_age=DEFINITION_CACHE_MAX_AGE,
        )

    def attribute_values(
        self,
        category_id: str,
        attribute_id: str,
        *,
        site: str = "",
        query: str = "",
        cursor: str = "",
        limit: int = 50,
        timeout_seconds: float | None = None,
    ) -> CategoryAttributeValuePage:
        del site
        target_id = str(attribute_id or "").strip()
        if target_id.casefold() in {"brand", "vendor"}:
            raise ValueError("Yandex 品牌取自草稿或商品的品牌字段（vendor），无需查询类目候选。")
        parameters = fetch_yandex_category_parameter_definitions(
            category_id,
            timeout_seconds=timeout_seconds,
        )
        parameter = next(
            (item for item in parameters if str(item["parameter_id"]) == target_id),
            None,
        )
        if parameter is None:
            raise ValueError(f"Yandex 类目 {category_id} 没有属性 {target_id}，请使用当前类目定义中的属性 ID。")
        candidates: list[tuple[str, str]] = []
        for row in parameter.get("values") or []:
            if not isinstance(row, dict):
                continue
            value = str(row.get("value") or "").strip()
            if value:
                candidates.append((value, str(row.get("value_id") or "").strip()))
        return paginate_value_candidates(
            candidates,
            platform=self.platform,
            site="global",
            category_id=str(category_id or "").strip(),
            attribute_id=target_id,
            query=query,
            cursor=cursor,
            limit=limit,
        )

    def search_categories(
        self,
        query: str,
        *,
        site: str = "",
        limit: int | None = 5,
        timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        del site
        return search_yandex_categories(
            query,
            limit=limit,
            timeout_seconds=timeout_seconds,
        )

    def root_categories(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return fetch_yandex_category_roots(timeout_seconds=timeout_seconds)

    def browse_categories(
        self,
        parent_ids: list[str],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return fetch_yandex_category_children(
            parent_ids,
            timeout_seconds=timeout_seconds,
        )

    def sibling_leaves(
        self,
        category_ids: list[str],
        *,
        limit: int = 12,
        timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        return fetch_yandex_category_siblings(
            category_ids,
            limit=limit,
            timeout_seconds=timeout_seconds,
        )

    def cached_category_ancestor_map(
        self,
        *,
        site: str = "",
        api_token: str = "",
    ) -> dict[str, tuple[str, ...]]:
        del site
        return cached_yandex_category_ancestor_map(api_token=api_token)

    def cached_category_ancestor_ids(
        self,
        category_id: str,
        *,
        site: str = "",
        api_token: str = "",
    ) -> tuple[str, ...]:
        del site
        return yandex_category_ancestor_ids(category_id, api_token=api_token)


def build_category_provider_registry() -> dict[str, CategoryProvider]:
    """构造 Provider 注册表并执行契约测试。"""

    providers: tuple[CategoryProvider, ...] = (
        MercadoLibreCategoryProvider(),
        OzonCategoryProvider(),
        YandexCategoryProvider(),
    )
    registry: dict[str, CategoryProvider] = {}
    for provider in providers:
        if not isinstance(provider, CategoryProvider):
            raise RuntimeError(
                f"{type(provider).__name__} 必须实现 CategoryProvider ABC。"
            )
        key = str(provider.platform or "").strip().lower()
        if not key:
            raise RuntimeError("CategoryProvider 必须声明 platform。")
        if key in registry:
            raise RuntimeError(f"类目 Provider 平台键重复：{key}")
        if not platform_has_capability(key, CAP_CATEGORY_ATTRIBUTES):
            raise RuntimeError(
                f"平台 {key} 未声明 {CAP_CATEGORY_ATTRIBUTES} 能力，"
                "注册表与 marketplace 声明不一致。"
            )
        registry[key] = provider
    return registry


_CATEGORY_PROVIDERS: dict[str, CategoryProvider] = (
    build_category_provider_registry()
)


def category_provider_for(platform: str) -> CategoryProvider | None:
    key = str(platform or "").strip().lower()
    if not platform_has_capability(key, CAP_CATEGORY_ATTRIBUTES):
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
    "YandexCategoryProvider",
    "build_category_provider_registry",
    "category_provider_for",
    "require_category_provider",
]
