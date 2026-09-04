"""锁定版 Pydantic AI 的 Images API focused Model 适配。

Pydantic AI 2.22 的公开 OpenAI Model 支持 Responses 原生图片工具，但没有为绑定
专用图片模型的 ``images.generate`` / ``images.edit`` 提供 Model。该适配器只实现图片
能力，仍遵循 Pydantic ``Model`` 请求/响应协议，并且只能由 ``ai_model_factory`` 创建。
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Sequence

from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import (
    BinaryContent,
    FilePart,
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import (
    CompletedStreamedResponse,
    Model,
    ModelRequestParameters,
    StreamedResponse,
)
from pydantic_ai.native_tools import ImageGenerationTool
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.providers import Provider
from pydantic_ai.settings import ModelSettings


def _user_content(messages: Sequence[ModelMessage]) -> tuple[str, list[BinaryContent]]:
    text_parts: list[str] = []
    images: list[BinaryContent] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, UserPromptPart):
                continue
            content = part.content
            items = [content] if isinstance(content, str) else list(content)
            for item in items:
                if isinstance(item, str):
                    if item.strip():
                        text_parts.append(item.strip())
                elif isinstance(item, BinaryContent) and item.is_image:
                    images.append(item)
                elif isinstance(item, ImageUrl):
                    raise ValueError("图片编辑只接受已解析的本地图片内容。")
    return "\n\n".join(text_parts).strip(), images


def _tool(parameters: ModelRequestParameters) -> ImageGenerationTool:
    selected = next(
        (
            item
            for item in parameters.native_tools
            if isinstance(item, ImageGenerationTool)
        ),
        None,
    )
    if selected is None:
        raise ValueError("图片请求缺少 Pydantic ImageGenerationTool。")
    return selected


def _response_parts(response: Any, provider_name: str) -> list[FilePart | TextPart]:
    items = response.data if isinstance(getattr(response, "data", None), list) else []
    parts: list[FilePart | TextPart] = []
    for item in items:
        b64_json = str(getattr(item, "b64_json", "") or "").strip()
        url = str(getattr(item, "url", "") or "").strip()
        if b64_json:
            parts.append(
                FilePart(
                    content=BinaryContent(
                        data=base64.b64decode(b64_json),
                        media_type="image/png",
                    )
                )
            )
        elif url:
            parts.append(
                TextPart(
                    url,
                    provider_name=provider_name,
                    provider_details={"image_url": url},
                )
            )
    return parts


class OpenAIImagesModel(Model[AsyncOpenAI]):
    """把 OpenAI-compatible Images API 表达为 Pydantic Model。"""

    def __init__(
        self,
        model_name: str,
        *,
        provider: Provider[AsyncOpenAI],
    ) -> None:
        super().__init__(
            profile=ModelProfile(
                supports_tools=True,
                supports_image_output=True,
                supported_native_tools=frozenset({ImageGenerationTool}),
            )
        )
        self._model_name = model_name
        self._provider = provider

    @classmethod
    def supported_native_tools(cls) -> frozenset[type[ImageGenerationTool]]:
        return frozenset({ImageGenerationTool})

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def system(self) -> str:
        return self._provider.name

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        settings, parameters = self.prepare_request(
            model_settings,
            model_request_parameters,
        )
        return await self._request_prepared(messages, settings, parameters)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        """用官方 completed stream 暴露 Images API 的一次性响应事件。"""

        del run_context
        settings, parameters = self.prepare_request(
            model_settings,
            model_request_parameters,
        )
        response = await self._request_prepared(messages, settings, parameters)
        yield CompletedStreamedResponse(
            response,
            model_request_parameters=parameters,
            replay_events=True,
        )

    async def _request_prepared(
        self,
        messages: list[ModelMessage],
        settings: ModelSettings | None,
        parameters: ModelRequestParameters,
    ) -> ModelResponse:
        prompt, images = _user_content(messages)
        if not prompt:
            raise ValueError("图片请求 prompt 不能为空。")
        tool = _tool(parameters)
        action = tool.action
        if action == "auto":
            action = "edit" if images else "generate"

        settings = dict(settings or {})
        extra_body = (
            dict(settings.get("extra_body"))
            if isinstance(settings.get("extra_body"), dict)
            else {}
        )
        count = int(extra_body.pop("n", 1) or 1)
        timeout = settings.get("timeout")
        common: dict[str, Any] = {
            "model": tool.model or self.model_name,
            "prompt": prompt,
            "n": count,
        }
        if tool.size:
            common["size"] = tool.size
        if tool.quality:
            common["quality"] = tool.quality
        if extra_body:
            common["extra_body"] = extra_body
        if timeout is not None:
            common["timeout"] = timeout

        try:
            if action == "edit":
                if not images:
                    raise ValueError("图片编辑至少需要一张源图片。")
                uploads = [
                    (f"source-{index + 1}.png", image.data, image.media_type)
                    for index, image in enumerate(images)
                ]
                response = await self._provider.client.images.edit(
                    image=uploads[0] if len(uploads) == 1 else uploads,
                    **common,
                )
            else:
                response = await self._provider.client.images.generate(**common)
        except APIStatusError as exc:
            raise ModelHTTPError(
                status_code=exc.status_code,
                model_name=self.model_name,
                body=exc.body,
                headers=exc.response.headers,
            ) from None
        except APIConnectionError:
            raise ModelAPIError(
                self.model_name,
                "图片 Provider 连接失败。",
            ) from None

        parts = _response_parts(response, self.system)
        if not parts:
            raise RuntimeError("图片 Provider 没有返回图片数据。")
        return ModelResponse(
            parts=parts,
            model_name=self.model_name,
            provider_name=self.system,
        )


__all__ = ["OpenAIImagesModel"]
