# -*- coding: utf-8 -*-
from __future__ import annotations

"""Mercado Libre、Ozon 与 Yandex 的统一类目 Provider 注册表。

每个平台显式继承 :class:`CategoryProvider` ABC：类目详情、属性定义与枚举
值三个核心契约缺失会在实例化阶段失败。属性定义由 Provider 所有的持久缓存
承载（fresh/stale 规则见 ``category_definition_cache``）；完整枚举候选只能
通过 ``attribute_values`` 分页读取。

类目搜索与树导航是可选能力：``search_categories`` /
``root_categories`` / ``browse_categories`` 只在支持的平台实现，消费者用
``CategorySearchProvider`` / ``CategoryNavigationProvider`` 运行时判断。
"""

import time
import urllib.parse
from datetime import timedelta
from typing import Any

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

from .category_definition_cache import (
    DEFINITION_CACHE_FRESH_TTL,
    DEFINITION_CACHE_MAX_AGE,
    load_definition_through_cache,
)
from .category_definition_support import (
    definition_from_legacy_attributes,
    paginate_value_candidates,
)
from .category_refresh import (
    http_json,
    mercadolibre_category_attributes,
    mercadolibre_category_detail,
    mercadolibre_category_record,
)
from .ozon_category_api import (
    fetch_ozon_category_attribute_values,
    fetch_ozon_category_children,
    fetch_ozon_category_record,
    fetch_ozon_category_roots,
    ozon_credential_scope_hash,
    search_ozon_categories,
)
from .yandex_category_api import (
    fetch_yandex_category_parameter_definitions,
    fetch_yandex_leaf_record,
    search_yandex_categories,
    yandex_credential_scope_hash,
)


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


def _cache_root():
    from erp_web.context import get_context

    return get_context().paths.cache_dir


class MercadoLibreCategoryProvider(CategoryProvider):
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

    def _scoped_http_client(self, deadline_at: float | None):
        def scoped_http_client(
            url: str,
            access_token: str | None = None,
        ) -> dict[str, Any] | list[Any]:
            if deadline_at is None:
                return http_json(url, access_token)
            return http_json(
                url,
                access_token,
                timeout_seconds=_remaining_timeout(deadline_at, 8) or 8,
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
        detail = mercadolibre_category_detail(
            category_id,
            http_client=self._scoped_http_client(deadline_at),
        )
        record = mercadolibre_category_record(detail, resolved_site, None)
        return CategoryDetail(
            platform=self.platform,
            site=resolved_site,
            category_id=str(record.get("category_id") or category_id),
            name=str(record.get("name_original") or category_id),
            path=str(record.get("category_path") or ""),
            parent_id=str(record.get("parent_id") or ""),
            is_leaf=False,
            active=True,
        )

    def attribute_definitions(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> CategoryDefinition:
        category_id = str(category_id or "").strip()
        if not category_id:
            raise RuntimeError("缺少 Mercado Libre 类目 ID。")
        resolved_site = self.resolve_site(site)
        deadline_at = _deadline_at(timeout_seconds)

        def live_loader() -> CategoryDefinition:
            http = self._scoped_http_client(deadline_at)
            detail = mercadolibre_category_detail(category_id, http_client=http)
            attrs = mercadolibre_category_attributes(category_id, http_client=http)
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

        return load_definition_through_cache(
            cache_root=_cache_root(),
            platform=self.platform,
            # Mercado Libre 类目接口公开，无需凭据作用域。
            credential_scope_hash="public",
            site=resolved_site,
            category_id=category_id,
            live_loader=live_loader,
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
        raw_attributes = mercadolibre_category_attributes(
            str(category_id or "").strip(),
            http_client=self._scoped_http_client(deadline_at),
        )
        candidates: list[tuple[str, str]] = []
        target_id = str(attribute_id or "").strip()
        for group in ("required", "optional"):
            for item in raw_attributes.get(group) or []:
                if not isinstance(item, dict):
                    continue
                if str(item.get("id") or "").strip() != target_id:
                    continue
                raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
                for row in raw.get("values") or []:
                    if not isinstance(row, dict):
                        continue
                    value = str(row.get("name") or row.get("id") or "").strip()
                    if not value:
                        continue
                    candidates.append((value, str(row.get("id") or "").strip()))
        return paginate_value_candidates(
            candidates,
            platform=self.platform,
            site=resolved_site,
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
        limit: int = 8,
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
                )
                or 8,
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
            name=str(record.get("name_original") or category_id),
            path=str(record.get("category_path") or ""),
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
    ) -> CategoryDefinition:
        del site
        category_id = str(category_id or "").strip()
        if not category_id:
            raise RuntimeError("缺少 Ozon 商品类型 ID。")
        deadline_at = _deadline_at(timeout_seconds)

        def live_loader() -> CategoryDefinition:
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
                description_category_id=str(
                    record.get("description_category_id") or ""
                ),
                required=list(attributes.get("required") or []),
                optional=list(attributes.get("optional") or []),
            )

        # 凭据缺失时 scope 计算抛出确定性错误，stale 缓存不得掩盖。
        return load_definition_through_cache(
            cache_root=_cache_root(),
            platform=self.platform,
            credential_scope_hash=ozon_credential_scope_hash(),
            site="global",
            category_id=category_id,
            live_loader=live_loader,
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
        limit: int = 5,
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


def _yandex_parameter_definition(parameter: dict[str, Any]) -> dict[str, Any]:
    """Yandex 类目参数 → legacy 共享属性字典的机械转换。

    ``parameterId`` → 通用 ``id``；ENUM 按官方 ``allowCustomValues`` 区分
    ``strict_enum``（只能选平台枚举值）与 ``open_enum``（允许自定义文本）；
    ``multivalue`` → ``is_collection``；``unit.units[]``/``defaultUnitId``
    → 共享 ``unit_options/default_unit`` 与发布期解析 ``unitId`` 所需的
    ``unit_ids``；官方 ``constraints`` 原样带入草稿校验。平台 wire 字段
    （parameter_type）保留给 platform_binding 归一化。
    """

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
        # 单位名称 → Yandex unitId；发布期把选中单位编译为 wire unitId。
        "unit_ids": unit_ids,
        "default_unit_id": str(parameter.get("default_unit_id") or "").strip(),
        "constraints": dict(parameter.get("constraints") or {}),
        "dictionary_id": dictionary_id,
        "is_dictionary": bool(dictionary_id),
        "is_collection": bool(parameter.get("is_collection")),
        "max_value_count": int(parameter.get("max_value_count") or 0),
        "category_dependent": False,
        "options": [str(item.get("value") or "").strip() for item in values],
        # legacy 选项 shape：id 为字符串 dictionary_value_id；类型化定义只
        # 保留有界预览，候选全集由 attribute_values 分页读取。
        "values": [
            {
                "id": str(item.get("value_id") or "").strip(),
                "value": str(item.get("value") or "").strip(),
                "name": str(item.get("value") or "").strip(),
            }
            for item in values
            if str(item.get("value") or "").strip()
        ],
        "description": "",
        "platform_type": parameter_type,
    }


class YandexCategoryProvider(CategoryProvider):
    """Yandex 官方类目树/类目参数的通用 Provider。

    只允许选择叶子类目；类目树使用本地缓存搜索，不为每次输入发远端请求。
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
            is_leaf=bool(record.get("is_leaf", True)),
            active=True,
        )

    def attribute_definitions(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
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
        return load_definition_through_cache(
            cache_root=_cache_root(),
            platform=self.platform,
            credential_scope_hash=yandex_credential_scope_hash(),
            site="global",
            category_id=category_id,
            live_loader=live_loader,
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
        parameters = fetch_yandex_category_parameter_definitions(
            category_id,
            timeout_seconds=timeout_seconds,
        )
        target_id = str(attribute_id or "").strip()
        candidates: list[tuple[str, str]] = []
        for parameter in parameters if isinstance(parameters, list) else []:
            if not isinstance(parameter, dict):
                continue
            if str(parameter.get("parameter_id") or "").strip() != target_id:
                continue
            for row in parameter.get("values") or []:
                if not isinstance(row, dict):
                    continue
                value = str(row.get("value") or "").strip()
                if not value:
                    continue
                # valueId 规范化为字符串 dictionary_value_id。
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
        limit: int = 5,
        timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        del site
        return search_yandex_categories(
            query,
            limit=limit,
            timeout_seconds=timeout_seconds,
        )


def build_category_provider_registry() -> dict[str, CategoryProvider]:
    """构造 Provider 注册表并执行契约测试。

    每个注册项必须继承 :class:`CategoryProvider`、平台键唯一，并与
    marketplace capability 声明（CAP_CATEGORY_ATTRIBUTES）一致。
    """

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
