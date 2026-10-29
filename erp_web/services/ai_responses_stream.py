"""校验 Responses 流的错误与结束边界，其他事件原样交给原生实现。

Pydantic AI 2.44.0 未处理顶层 ResponseErrorEvent，前台流缺少结束事件
也可能被当作完成。这里只补齐错误传播，不解析文本、编码事件或重试请求。
上游修复且对应 SDK 回归通过后移除此适配。
"""
from collections.abc import AsyncIterator

from openai import AsyncStream
from openai.types import responses
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models.openai import OpenAIResponsesModel


class _CheckedResponseEvents:
    def __init__(self, source: AsyncStream[responses.ResponseStreamEvent], model_name: str,
                 *, resuming: bool = False):
        self.source = source
        self.model_name = model_name
        self.background = resuming

    async def close(self) -> None:
        await self.source.close()

    async def __aiter__(self) -> AsyncIterator[responses.ResponseStreamEvent]:
        terminal = False
        async for event in self.source:
            if isinstance(event, responses.ResponseErrorEvent):
                raise ModelAPIError(self.model_name, f"模型生成失败：{event.message}")
            if isinstance(event, responses.ResponseFailedEvent):
                error = event.response.error
                reason = error.message if error else "服务商未提供错误详情"
                raise ModelAPIError(self.model_name, f"模型生成失败：{reason}")
            if isinstance(event, (responses.ResponseCreatedEvent, responses.ResponseInProgressEvent,
                                  responses.ResponseQueuedEvent)):
                if event.response.background is not None:
                    self.background = event.response.background
            if isinstance(event, (responses.ResponseCompletedEvent, responses.ResponseIncompleteEvent)):
                terminal = True
            yield event
        # 原生后台任务允许 EOF 后继续 retrieve；前台响应必须有明确结束事件。
        if not terminal and not self.background:
            raise ModelAPIError(self.model_name, "模型响应流提前结束，未收到完成事件；本轮未正常完成。")


class CheckedOpenAIResponsesModel(OpenAIResponsesModel):
    async def _process_streamed_response(self, response, model_settings, model_request_parameters, **kwargs):
        checked = _CheckedResponseEvents(response, self.model_name,
                                         resuming=bool(kwargs.get("expected_response_id")))
        return await super()._process_streamed_response(
            checked, model_settings, model_request_parameters, **kwargs,
        )


__all__ = ["CheckedOpenAIResponsesModel"]
