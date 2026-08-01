"""Tool-turn Provider 适配器。

生产 JSON adapter 把现有 ``AiChatProvider.chat_json`` 包装成统一
``AiToolTurnProvider``；无网络 fake 继续用于冻结 native/JSON 协议契约。
"""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
from typing import Any, Iterable

from erp_web.schemas.ai_tools import AiToolSchemaError, AiToolTurn

from .ai_gateway_provider_types import AiChatRequest
from .ai_provider_contracts import (
    CAPABILITY_CHAT_JSON,
    CAPABILITY_TOOL_TURN,
    AiChatProvider,
    AiToolTurnProvider,
    AiToolTurnRequest,
)


_JSON_TOOL_PROTOCOL_SYSTEM = """你正在执行受控的 JSON tool protocol。
每轮只能返回以下两种 JSON 对象之一，不能返回 Markdown、解释或普通文本：
1. {"type":"tool_calls","calls":[{"protocol_version":"1","call_id":"本任务内唯一 ID","tool_name":"已提供的工具名","tool_version":"1","arguments":{},"round":当前工具轮次}]}
2. {"type":"final","result":{...}}
同一响应不能同时包含 calls 和 result。只能调用本轮列出的工具，不能虚构工具结果或类目 ID。
当已有工具结果足以得出结论，或无法继续时，返回 final。"""


class JsonToolTurnProviderAdapter(AiToolTurnProvider):
    """用现有 JSON 对话能力执行 provider-neutral tool turn。

    Adapter 复用 invocation 的 recorder，不创建新的 conversation；单个 task
    启动后也不会切换到另一种协议。
    """

    def __init__(self, provider: AiChatProvider, *, app_dir: Path | str) -> None:
        if not isinstance(provider, AiChatProvider):
            raise TypeError("JSON tool adapter 需要 AiChatProvider")
        self.provider = provider
        self.app_dir = app_dir
        self.provider_id = f"{provider.provider_id}:json-tool-protocol"

    def supports(self, model: dict[str, Any], capability: str) -> bool:
        return capability == CAPABILITY_TOOL_TURN and self.provider.supports(
            model,
            CAPABILITY_CHAT_JSON,
        )

    def run_tool_turn(self, request: AiToolTurnRequest) -> AiToolTurn:
        protocol_payload = {
            "protocol_version": "1",
            "round": request.round,
            "tools": [definition.to_dict() for definition in request.tools],
            "tool_results": [result.to_dict() for result in request.tool_results],
        }
        messages = [
            {"role": "system", "content": _JSON_TOOL_PROTOCOL_SYSTEM},
            *request.messages,
            {
                "role": "user",
                "content": (
                    "本轮受控工具协议上下文：\n"
                    + json.dumps(
                        protocol_payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                ),
            },
        ]
        remaining_seconds = (
            request.invocation.execution_context.bounded_timeout_seconds()
        )
        if remaining_seconds < 1:
            raise TimeoutError("AI Task 剩余 deadline 不足以启动模型调用")
        try:
            raw_response = self.provider.chat_json(
                AiChatRequest(
                    app_dir=self.app_dir,
                    model=request.invocation.model,
                    messages=messages,
                    required_capabilities=request.invocation.required_capabilities,
                    temperature=0,
                    max_tokens=900,
                    timeout_seconds=int(remaining_seconds),
                    response_format=True,
                    stream=False,
                    conversation=request.invocation.recorder,
                )
            )
        except (TypeError, ValueError) as exc:
            raise AiToolSchemaError(
                f"Provider 未返回 JSON tool protocol：{exc}",
                code="TOOL_PROTOCOL_UNSUPPORTED",
            ) from exc
        if not isinstance(raw_response, dict):
            raise AiToolSchemaError(
                "Provider 未返回 JSON tool protocol 对象",
                code="TOOL_PROTOCOL_UNSUPPORTED",
            )
        return AiToolTurn.from_dict(raw_response)


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


__all__ = [
    "JsonToolTurnFakeAdapter",
    "JsonToolTurnProviderAdapter",
    "NativeToolTurnFakeProvider",
]
