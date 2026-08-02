"""CLI/浏览器等独立连接的最小 Provider 能力接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


CAPABILITY_CHAT_JSON = "chat_json"
CAPABILITY_IMAGE_GENERATE = "image_generate"
CAPABILITY_IMAGE_EDIT = "image_edit"


class AiProvider(ABC):
    """所有 AI Provider 的最小公共接口。"""

    provider_id: str

    @abstractmethod
    def supports(self, model: dict[str, Any], capability: str) -> bool:
        """判断当前 Provider 是否支持模型与能力组合。"""


class AiChatProvider(AiProvider):
    """文本/JSON 对话能力。"""

    @abstractmethod
    def chat_json(self, request: Any) -> dict[str, Any]:
        """执行对话并返回解析后的 JSON 对象。"""

    @abstractmethod
    def test_model(
        self,
        app_dir: Path | str,
        model: dict[str, Any],
        raw_model: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """检测模型连接和声明能力。"""


__all__ = [
    "CAPABILITY_CHAT_JSON",
    "CAPABILITY_IMAGE_EDIT",
    "CAPABILITY_IMAGE_GENERATE",
    "AiChatProvider",
    "AiProvider",
]
