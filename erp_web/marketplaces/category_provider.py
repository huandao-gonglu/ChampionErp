from __future__ import annotations

"""统一类目详情服务与绑定式搜索器契约。"""

from typing import Any, Protocol, runtime_checkable

from erp_web.schemas.category import CategorySearchResult


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


@runtime_checkable
class CategorySearcher(Protocol):
    """已绑定平台、站点和执行约束的类目搜索对象。"""

    def search_categories(self, keyword: str) -> CategorySearchResult:
        ...


__all__ = [
    "CategoryProvider",
    "CategorySearcher",
]
