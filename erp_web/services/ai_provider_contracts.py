"""AI Provider 能力接口。

Provider 按能力实现接口，避免让只支持文本的实现承担图片方法。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from erp_web.schemas.ai_tools import AiToolDefinition, AiToolResult, AiToolTurn

if TYPE_CHECKING:
    from .ai_invocation import AiInvocation
    from .ai_work_service import AiWorkConversation


CAPABILITY_CHAT_JSON = "chat_json"
CAPABILITY_IMAGE_GENERATE = "image_generate"
CAPABILITY_IMAGE_EDIT = "image_edit"
CAPABILITY_TOOL_TURN = "tool_turn"


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
class AiToolTurnRequest:
    """一次模型 tool turn 的 provider-neutral 请求。"""

    invocation: "AiInvocation"
    messages: tuple[dict[str, Any], ...]
    tools: tuple["AiToolDefinition", ...]
    tool_results: tuple["AiToolResult", ...]
    round: int

    def __post_init__(self) -> None:
        if not isinstance(self.round, int) or isinstance(self.round, bool) or self.round < 1:
            raise ValueError("AiToolTurnRequest.round 必须是大于等于 1 的整数")
        object.__setattr__(
            self,
            "messages",
            tuple(dict(message) for message in self.messages),
        )
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "tool_results", tuple(self.tool_results))
        if not all(isinstance(tool, AiToolDefinition) for tool in self.tools):
            raise TypeError("AiToolTurnRequest.tools 必须全部是 AiToolDefinition")
        if not all(isinstance(result, AiToolResult) for result in self.tool_results):
            raise TypeError("AiToolTurnRequest.tool_results 必须全部是 AiToolResult")

    def to_json_payload(self) -> dict[str, Any]:
        return {
            "protocol_version": "1",
            "round": self.round,
            "messages": [dict(message) for message in self.messages],
            "tools": [definition.to_dict() for definition in self.tools],
            "tool_results": [result.to_dict() for result in self.tool_results],
            "context": self.invocation.execution_context.trace_payload(),
        }


class AiToolTurnProvider(AiProvider):
    """原生 function calling 与 JSON adapter 共用的内部 turn 协议。"""

    @abstractmethod
    def run_tool_turn(self, request: AiToolTurnRequest) -> AiToolTurn:
        """执行一个模型 turn；不得创建新的 AI Work conversation。"""


@dataclass(frozen=True)
class AiImageRequest:
    app_dir: Path | str
    model: dict[str, Any]
    prompt: str
    images: list[dict[str, Any]]
    mode: str
    timeout_seconds: int | None = None
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
    "CAPABILITY_TOOL_TURN",
    "AiChatProvider",
    "AiImageProvider",
    "AiImageRequest",
    "AiProvider",
    "AiToolTurnProvider",
    "AiToolTurnRequest",
]
