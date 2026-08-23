# -*- coding: utf-8 -*-
from __future__ import annotations

"""统一类目 Provider 抽象（类目 Schema 分离计划 Phase 1）。

平台类目规则的唯一所有权属于本抽象的实现：

- :class:`CategoryProvider` 是强制实现的 ABC，规定类目详情、属性定义与
  枚举值三个核心契约；缺少实现在实例化阶段即失败。
- :class:`CategorySearchProvider` / :class:`CategoryNavigationProvider` 是可选
  能力的小接口，避免把所有平台强塞进同一个胖基类。

Provider 负责平台 API 调用、字段归一化、缓存与 stale 策略；不接收草稿，
不拥有预检结果、发布状态或工作流。业务模块一律通过 CategoryCatalog /
注入 Loader 读取，不得直接导入平台类目 API。
"""

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

from erp_web.schemas.category import CategoryBrowseResult, CategorySearchResult
from erp_web.schemas.category_definition import (
    CategoryAttributeValuePage,
    CategoryDefinition,
    CategoryDetail,
)


class CategoryProvider(ABC):
    """平台类目事实的抽象端口；所有注册平台必须显式继承并实现。"""

    platform: str

    #: 属性定义持久缓存的新鲜窗口（秒）；平台可按配额自行缩短。
    definition_fresh_ttl_seconds: float = 24 * 3600

    @abstractmethod
    def resolve_site(self, site: str = "") -> str:
        """解析平台站点；未指定时回退平台默认站点。"""

    @abstractmethod
    def category_detail(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> CategoryDetail:
        """读取类目详情（身份与展示字段，不含属性定义）。"""

    @abstractmethod
    def attribute_definitions(
        self,
        category_id: str,
        *,
        site: str = "",
        timeout_seconds: float | None = None,
    ) -> CategoryDefinition:
        """读取类目属性定义（临时平台规则，不得持久化进商品/草稿）。"""

    @abstractmethod
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
        """分页读取属性字典候选值；候选全集不得一次性返回。"""


@runtime_checkable
class CategorySearchProvider(Protocol):
    """可选能力：类目关键字搜索。"""

    platform: str

    def search_categories(
        self,
        query: str,
        *,
        site: str = "",
        limit: int = 8,
        timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        ...


@runtime_checkable
class CategoryNavigationProvider(Protocol):
    """可选能力：类目树导航。"""

    platform: str

    def root_categories(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        ...

    def browse_categories(
        self,
        parent_ids: list[str],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        ...


@runtime_checkable
class CategorySearcher(Protocol):
    """已绑定平台、站点和执行约束的类目搜索对象。"""

    def search_categories(self, keyword: str) -> CategorySearchResult:
        ...


@runtime_checkable
class CategoryNavigator(Protocol):
    """已绑定平台、站点和 deadline 的类目树导航对象。"""

    def root_categories(self) -> CategoryBrowseResult:
        ...

    def browse_categories(self, parent_ids: list[str]) -> CategoryBrowseResult:
        ...


__all__ = [
    "CategoryNavigationProvider",
    "CategoryNavigator",
    "CategoryProvider",
    "CategorySearchProvider",
    "CategorySearcher",
]
