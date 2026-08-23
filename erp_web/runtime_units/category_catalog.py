# -*- coding: utf-8 -*-
"""统一类目读取入口 CategoryCatalog（类目 Schema 分离计划 Phase 1）。

业务模块不得自行解析注册表，也不得直接调用平台 API；一律通过注入的
CategoryCatalog / CategoryDefinitionLoader 读取类目事实：

```text
业务消费者
    ↓
注入的 CategoryCatalog / CategoryDefinitionLoader
    ↓
require_category_provider(platform)
    ↓
MercadoLibre / Ozon / Yandex CategoryProvider
    ↓
平台 API 与 Provider 所有的缓存
```
"""

from __future__ import annotations

from typing import Callable, Mapping, Protocol

from erp_web.marketplaces.category_provider import CategoryProvider
from erp_web.runtime_units.category_definition_support import (
    project_attribute_page,
)
from erp_web.schemas.category_definition import (
    CategoryAttributePage,
    CategoryAttributeValuePage,
    CategoryDefinition,
    CategoryDetail,
)


class CategoryDefinitionLoader(Protocol):
    """注入式属性定义加载端口；发布/预检/填充统一消费该契约。"""

    def __call__(
        self,
        platform: str,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> CategoryDefinition:
        ...


class CategoryCatalog:
    """Provider 解析、统一读取、指纹和有界公共视图。"""

    def __init__(self, providers: Mapping[str, CategoryProvider]) -> None:
        if not providers:
            raise RuntimeError("CategoryCatalog 需要至少一个平台 Provider。")
        for key, provider in providers.items():
            if not isinstance(provider, CategoryProvider):
                raise RuntimeError(
                    f"{type(provider).__name__} 必须实现 CategoryProvider ABC。"
                )
            if str(key).strip().lower() != str(provider.platform).strip().lower():
                raise RuntimeError(
                    f"Provider 注册键 {key} 与 platform {provider.platform} 不一致。"
                )
        self._providers: dict[str, CategoryProvider] = {
            str(key).strip().lower(): provider
            for key, provider in providers.items()
        }

    @property
    def platforms(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def provider_for(self, platform: str) -> CategoryProvider:
        key = str(platform or "").strip().lower()
        provider = self._providers.get(key)
        if provider is None:
            raise RuntimeError(f"未注册的类目 Provider：{platform or '(空)'}")
        return provider

    def category_detail(
        self,
        platform: str,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> CategoryDetail:
        return self.provider_for(platform).category_detail(
            category_id,
            site=site,
            timeout_seconds=timeout_seconds,
        )

    def attribute_definitions(
        self,
        platform: str,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> CategoryDefinition:
        return self.provider_for(platform).attribute_definitions(
            category_id,
            site=site,
            timeout_seconds=timeout_seconds,
        )

    def attribute_values(
        self,
        platform: str,
        category_id: str,
        attribute_id: str,
        *,
        site: str = "",
        query: str = "",
        cursor: str = "",
        limit: int = 50,
        timeout_seconds: float | None = None,
    ) -> CategoryAttributeValuePage:
        return self.provider_for(platform).attribute_values(
            category_id,
            attribute_id,
            site=site,
            query=query,
            cursor=cursor,
            limit=limit,
            timeout_seconds=timeout_seconds,
        )

    def public_attribute_page(
        self,
        platform: str,
        category_id: str,
        *,
        site: str = "",
        cursor: str = "",
        limit: int = 50,
        timeout_seconds: float | None = None,
    ) -> CategoryAttributePage:
        """前端/Agent 的有界属性页；内部定义不外泄 platform_binding。"""

        definition = self.attribute_definitions(
            platform,
            category_id,
            site=site,
            timeout_seconds=timeout_seconds,
        )
        return project_attribute_page(definition, cursor=cursor, limit=limit)

    def definition_loader(self) -> "CategoryDefinitionLoader":
        """返回可注入业务编排的定义加载 callable。"""

        return self.attribute_definitions


def build_category_catalog() -> CategoryCatalog:
    """从平台 Provider 注册表构造统一 Catalog。"""

    from erp_web.runtime_units.category_providers import (
        build_category_provider_registry,
    )

    return CategoryCatalog(build_category_provider_registry())


def get_category_catalog() -> CategoryCatalog:
    """当前 AppContext 的统一类目 Catalog。"""

    from erp_web.context import get_context

    return get_context().category_catalog


__all__ = [
    "CategoryCatalog",
    "CategoryDefinitionLoader",
    "build_category_catalog",
    "get_category_catalog",
]
