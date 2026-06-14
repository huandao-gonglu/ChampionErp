from __future__ import annotations

from typing import Any, TypedDict


class ApiResponse(TypedDict, total=False):
    ok: bool
    error: str
    error_code: str
    message: str
    data: Any
    product: dict[str, Any]
    productsIndex: list[dict[str, Any]]
    draftsIndex: list[dict[str, Any]]
