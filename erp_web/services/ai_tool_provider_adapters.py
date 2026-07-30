"""Tool-turn Provider 的无网络 fake，用于冻结 native/JSON 协议契约。"""

from __future__ import annotations

from collections import deque
import json
from typing import Any, Iterable

from erp_web.schemas.ai_tools import AiToolTurn

from .ai_provider_contracts import (
    CAPABILITY_TOOL_TURN,
    AiToolTurnProvider,
    AiToolTurnRequest,
)


class NativeToolTurnFakeProvider(AiToolTurnProvider):
    """直接返回内部 AiToolTurn 的 native function-calling fake。"""

    provider_id = "fake-native-tool-turn"

    def __init__(self, responses: Iterable[AiToolTurn]) -> None:
        self._responses = deque(responses)
        self.requests: list[AiToolTurnRequest] = []

    def supports(self, model: dict[str, Any], capability: str) -> bool:
        del model
        return capability == CAPABILITY_TOOL_TURN

    def run_tool_turn(self, request: AiToolTurnRequest) -> AiToolTurn:
        self.requests.append(request)
        if not self._responses:
            raise RuntimeError("native fake 没有剩余响应")
        return self._responses.popleft()


class JsonToolTurnFakeAdapter(AiToolTurnProvider):
    """通过 JSON 序列化边界解析响应的 tool protocol adapter fake。"""

    provider_id = "fake-json-tool-turn"

    def __init__(self, responses: Iterable[dict[str, Any]]) -> None:
        self._responses = deque(responses)
        self.request_payloads: list[dict[str, Any]] = []

    def supports(self, model: dict[str, Any], capability: str) -> bool:
        del model
        return capability == CAPABILITY_TOOL_TURN

    def run_tool_turn(self, request: AiToolTurnRequest) -> AiToolTurn:
        # 强制经过真实 JSON round-trip，contract test 才能发现不可序列化字段。
        payload = json.loads(
            json.dumps(
                request.to_json_payload(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        self.request_payloads.append(payload)
        if not self._responses:
            raise RuntimeError("JSON fake adapter 没有剩余响应")
        raw_response = json.loads(
            json.dumps(
                self._responses.popleft(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return AiToolTurn.from_dict(raw_response)


__all__ = ["JsonToolTurnFakeAdapter", "NativeToolTurnFakeProvider"]
