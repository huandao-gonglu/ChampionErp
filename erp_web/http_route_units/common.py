from __future__ import annotations

from typing import Any, Protocol


class JsonRequestHandler(Protocol):
    path: str
    wfile: Any

    def send_json(self, data: Any, status: int = 200) -> None:
        ...

    def read_body(self) -> dict[str, Any]:
        ...

    def send_response(self, code: int, message: str | None = None) -> None:
        ...

    def send_header(self, keyword: str, value: str) -> None:
        ...

    def end_headers(self) -> None:
        ...
