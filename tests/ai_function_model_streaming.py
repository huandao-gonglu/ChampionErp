"""FunctionModel 流式适配：统一执行内核要求所有测试模型支持流式。

``AiAgentFactory.run_sync()`` 与 ``open_stream_run()`` 共用同一个
native event 流式内核（重构计划 §7.2：不得保留旧 ``agent.run_sync()``
旁路），因此只定义了同步 ``function`` 的 ``FunctionModel`` 测试夹具必须
补一个等价的 ``stream_function``。本模块提供通用适配：调用原同步函数
获得完整 ``ModelResponse``，再按 FunctionModel 官方流式缩写协议
（文本 delta / ``DeltaToolCall`` / ``DeltaThinkingPart`` / native part）
重放，不改变任何断言语义。
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from pydantic_ai.messages import (
    NativeToolCallPart,
    NativeToolReturnPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
)
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaThinkingPart,
    DeltaToolCall,
    FunctionModel,
)


def streaming_function_model(model: FunctionModel) -> FunctionModel:
    """给只定义了同步 function 的 FunctionModel 补一个通用流式适配。

    已携带 ``stream_function`` 的模型原样返回。适配后的流式函数在每次
    模型请求时重新调用原同步函数，因此多轮工具循环、按消息内容分支的
    测试模型行为保持不变；同步函数抛出的异常（如 ``ModelAPIError``）
    也会在流开始时原样传播。
    """

    if model.stream_function is not None:
        return model
    sync_function = model.function
    assert sync_function is not None, (
        "测试 FunctionModel 必须至少定义 function 或 stream_function"
    )

    async def stream_function(
        messages: list[Any],
        agent_info: AgentInfo,
    ):
        if inspect.iscoroutinefunction(sync_function):
            response = await sync_function(messages, agent_info)
        else:
            response = sync_function(messages, agent_info)
        for index, part in enumerate(response.parts):
            if isinstance(part, TextPart):
                if part.content:
                    yield part.content
            elif isinstance(part, ThinkingPart):
                yield {
                    index: DeltaThinkingPart(
                        content=part.content,
                        signature=getattr(part, "signature", None),
                    )
                }
            elif isinstance(part, ToolCallPart):
                args = part.args
                json_args = (
                    args
                    if isinstance(args, str)
                    else json.dumps(args, ensure_ascii=False)
                )
                yield {
                    index: DeltaToolCall(
                        name=part.tool_name,
                        json_args=json_args,
                        tool_call_id=part.tool_call_id,
                    )
                }
            elif isinstance(part, (NativeToolCallPart, NativeToolReturnPart)):
                yield {index: part}
            else:
                raise AssertionError(
                    f"测试流式适配暂不支持 part 类型：{type(part)!r}"
                )

    return FunctionModel(
        sync_function,
        stream_function=stream_function,
        settings=model.settings,
    )


__all__ = ["streaming_function_model"]
