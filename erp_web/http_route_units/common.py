from __future__ import annotations

from typing import Any, Protocol


class UserInputError(Exception):
    """Invalid request input; mapped to an HTTP 400 by the top-level POST handler."""


class JsonRequestHandler(Protocol):
    path: str
    wfile: Any

    def send_json(self, data: Any, status: int = 200) -> None:
        ...

    def send_ndjson(self, items: list[dict[str, Any]], status: int = 200) -> None:
        ...

    def read_body(self) -> dict[str, Any]:
        ...

    def send_response(self, code: int, message: str | None = None) -> None:
        ...

    def send_header(self, keyword: str, value: str) -> None:
        ...

    def end_headers(self) -> None:
        ...
