from __future__ import annotations

"""统一类目详情服务与绑定式搜索器契约。"""

from typing import Any, Protocol, runtime_checkable

from erp_web.schemas.category import CategoryBrowseResult, CategorySearchResult


class CategoryProvider(Protocol):
    platform: str

    def resolve_site(self, site: str = "") -> str:
        ...

    def detail(
        self,
        category_id: str,
        site: str = "",
        *,
        include_attributes: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        ...

    def attribute_values(
        self,
        category_id: str,
        attribute_id: str,
        site: str = "",
        *,
        query: str = "",
        limit: int = 50,
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
    "CategoryNavigator",
    "CategoryProvider",
    "CategorySearcher",
]
