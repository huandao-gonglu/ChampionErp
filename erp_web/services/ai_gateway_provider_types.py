"""AI Provider 请求对象；协议模块共享但不依赖注册表。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

@dataclass(frozen=True)
class AiChatRequest:
    app_dir: Path | str
    model: dict[str, Any]
    messages: list[dict[str, str]]
    required_capabilities: tuple[str, ...] = ()
    temperature: float = 0.2
    max_tokens: int | None = None
    timeout_seconds: int | None = None
    response_format: bool = True
    extra_body: dict[str, Any] | None = None
    stream: bool = False
    token_callback: Callable[[str], None] | None = None
    generation_settings: dict[str, Any] | None = None

    def emit_delta(self, text: str) -> None:
        if self.token_callback:
            self.token_callback(text)

__all__ = ["AiChatRequest"]
