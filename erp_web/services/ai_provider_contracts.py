"""AI Provider 能力接口。

Provider 按能力实现接口，避免让只支持文本的实现承担图片方法。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .ai_work_service import AiWorkConversation


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


@dataclass(frozen=True)
class AiImageRequest:
    app_dir: Path | str
    model: dict[str, Any]
    prompt: str
    images: list[dict[str, Any]]
    mode: str
    size: str = "1024x1024"
    quality: str = "medium"
    count: int = 1
    conversation: AiWorkConversation | None = None


class AiImageProvider(AiProvider):
    """图片生成和图片编辑能力。"""

    @abstractmethod
    def generate_images(self, request: AiImageRequest) -> list[dict[str, Any]]:
        """根据提示词生成图片。"""

    @abstractmethod
    def edit_images(self, request: AiImageRequest) -> list[dict[str, Any]]:
        """根据提示词和源图片编辑图片。"""


__all__ = [
    "CAPABILITY_CHAT_JSON",
    "CAPABILITY_IMAGE_EDIT",
    "CAPABILITY_IMAGE_GENERATE",
    "AiChatProvider",
    "AiImageProvider",
    "AiImageRequest",
    "AiProvider",
]
