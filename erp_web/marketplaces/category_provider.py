from __future__ import annotations

"""统一类目服务契约。"""

from typing import Any, Protocol


class CategoryProvider(Protocol):
    platform: str

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


__all__ = ["CategoryProvider"]
