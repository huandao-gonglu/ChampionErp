from __future__ import annotations

"""统一类目服务契约及可选召回能力。"""

from typing import Any, Protocol, runtime_checkable

from erp_web.schemas.category import (
    CategoryCorpusInfo,
    CategoryProviderPreflight,
)


class CategoryProvider(Protocol):
    platform: str

    def preflight(self, site: str = "") -> CategoryProviderPreflight:
        ...

    def resolve_site(self, site: str = "") -> str:
        ...

    def search(self, query: str, site: str = "", limit: int = 5) -> list[dict[str, Any]]:
        ...

    def detail(
        self,
        category_id: str,
        site: str = "",
        *,
        include_attributes: bool = False,
    ) -> dict[str, Any]:
        ...


@runtime_checkable
class FullTreeCategoryProvider(CategoryProvider, Protocol):
    """能够提供当前账号作用域下完整、可发布类目语料的 Provider。"""

    def category_corpus(
        self,
        site: str = "",
    ) -> tuple[list[dict[str, Any]], CategoryCorpusInfo]:
        ...


@runtime_checkable
class RemoteDiscoveryCategoryProvider(CategoryProvider, Protocol):
    """只提供远端 discovery、需要在召回层合并查询结果的 Provider。"""

    def discover(
        self,
        query: str,
        site: str = "",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        ...


__all__ = [
    "CategoryProvider",
    "FullTreeCategoryProvider",
    "RemoteDiscoveryCategoryProvider",
]
