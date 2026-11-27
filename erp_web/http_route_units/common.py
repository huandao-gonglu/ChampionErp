from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class UserInputError(Exception):
    """Invalid request input; mapped to an HTTP 400 by the top-level POST handler."""


class JsonRequestHandler(Protocol):
    path: str
    wfile: Any

    def send_json(self, data: Any, status: int = 200) -> None:
        ...

    def send_binary(
        self,
        raw: bytes,
        *,
        content_type: str,
        filename: str = "",
        extra_headers: dict[str, str] | None = None,
        status: int = 200,
    ) -> None:
        ...

    def send_json_with_keepalive(
        self,
        compute: Callable[[], tuple[Any, int]],
        *,
        keepalive_interval: float = 15.0,
    ) -> None:
        ...

    def send_ndjson(self, items: list[dict[str, Any]], status: int = 200) -> None:
        ...

    def send_sse_headers(
        self,
        headers: dict[str, str],
        status: int = 200,
    ) -> None:
        ...

    def write_sse_chunk(self, chunk: bytes) -> None:
        ...

    def read_body(self) -> dict[str, Any]:
        ...

    def read_binary_body(
        self,
        *,
        allowed_content_types: frozenset[str],
        max_bytes: int | None = None,
    ) -> bytes:
        ...

    def send_response(self, code: int, message: str | None = None) -> None:
        ...

    def send_header(self, keyword: str, value: str) -> None:
        ...

    def end_headers(self) -> None:
        ...
