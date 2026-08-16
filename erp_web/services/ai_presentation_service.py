"""Presentation 观察服务：预留、claim 绑定与官方 chunk 发布。

位于 HTTP 公共边界、``AiAgentFactory`` 执行内核与 ``AiPresentationRegistry``
之间（docs/aiworkpage.md §2/§4）：

- ``reserve_presentation``：服务端生成 presentation/conversation ID 并预留；
  不执行 Agent、不读取业务数据、不选择能力。
- ``claim_presentation_scope``：HTTP 公共边界原子 claim 已预留 presentation，
  并构造带具体 observer 的 root scope；非法/过期/重复返回 None。
- ``RegistryAiPresentationObserver``：具体 observer。把 Agent native event 流
  用官方 ``VercelAIEventStream.transform_stream`` / ``encode_stream`` 编码为
  chunk 并发布到 registry；事件原样透传给调用方，发布失败（含缓冲溢出）只
  停止发布，不改写 Agent 执行语义。
- ``presentation_sse_headers``：observe stream 的官方 SSE 响应头。

本模块不启动 Agent、不选择工具、不组装或保存业务结果；规范消息仍只由
``PydanticMessageStore`` 持久化，presentation chunk 缓冲只是短期展示副本。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from .ai_presentation_context import (
    AiPresentationContext,
    root_presentation_context,
)
from .ai_presentation_registry import (
    FINALIZING,
    RESERVED,
    RUNNING,
    AiPresentationRegistry,
)
from .vercel_ai_ui_service import new_event_stream

_logger = logging.getLogger(__name__)

PRESENTATION_ID_PATTERN = re.compile(r"^presentation_[0-9a-f]{32}$")
MAX_DISPLAY_TITLE_CHARS = 80
DEFAULT_DISPLAY_TITLE = "AI 任务"

CLAIM_REJECTED_CODE = "AI_PRESENTATION_CLAIM_INVALID"
CLAIM_REJECTED_MESSAGE = "AI 展示不可用、已过期或已被使用。"

__all__ = [
    "CLAIM_REJECTED_CODE",
    "CLAIM_REJECTED_MESSAGE",
    "DEFAULT_DISPLAY_TITLE",
    "MAX_DISPLAY_TITLE_CHARS",
    "PRESENTATION_ID_PATTERN",
    "RegistryAiPresentationObserver",
    "claim_presentation_scope",
    "new_presentation_conversation_id",
    "new_presentation_id",
    "new_presentation_root_run_id",
    "presentation_sse_headers",
    "reserve_presentation",
    "sanitize_display_title",
]


def new_presentation_id() -> str:
    return f"presentation_{uuid4().hex}"


def new_presentation_conversation_id() -> str:
    return f"conversation_{uuid4().hex}"


def new_presentation_root_run_id() -> str:
    return f"run_{uuid4().hex}"


def sanitize_display_title(value: Any) -> str:
    """只保留可打印字符并压缩空白；仅用于 UI 展示。"""

    text = str(value or "")
    cleaned = "".join(
        char for char in text if ord(char) >= 32 and ord(char) != 127
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:MAX_DISPLAY_TITLE_CHARS].strip()


def reserve_presentation(
    registry: AiPresentationRegistry,
    *,
    display_title: Any,
) -> dict[str, object]:
    """预留一次展示并返回公开 descriptor。

    ID 与 conversation ID 由服务端生成；reservation 有短 TTL，未绑定业务
    请求时自动过期。
    """

    title = sanitize_display_title(display_title) or DEFAULT_DISPLAY_TITLE
    for _attempt in range(3):
        presentation_id = new_presentation_id()
        conversation_id = new_presentation_conversation_id()
        if registry.reserve(
            presentation_id=presentation_id,
            conversation_id=conversation_id,
            display_title=title,
        ):
            return {
                "ok": True,
                "presentation_id": presentation_id,
                "conversation_id": conversation_id,
                "status": RESERVED,
                "display_title": title,
            }
    raise RuntimeError("无法预留 AI 展示。")


def claim_presentation_scope(
    registry: AiPresentationRegistry,
    *,
    presentation_id: str,
) -> AiPresentationContext | None:
    """HTTP 公共边界原子 claim 并构造 root scope；失败返回 None。

    非法格式、不存在、过期或已 claim 的 presentation 一律返回 None，由边界
    映射为稳定 409；不得静默创建第二个展示或继续无关联运行。
    """

    normalized = str(presentation_id or "").strip()
    if not PRESENTATION_ID_PATTERN.fullmatch(normalized):
        return None
    if not registry.claim(normalized):
        return None
    conversation_id = registry.conversation_id(normalized)
    return root_presentation_context(
        presentation_id=normalized,
        root_run_id=new_presentation_root_run_id(),
        conversation_id=conversation_id,
        origin="business.ui",
        observer=RegistryAiPresentationObserver(
            registry=registry,
            presentation_id=normalized,
            conversation_id=conversation_id,
        ),
    )


def presentation_sse_headers(conversation_id: str) -> dict[str, str]:
    """observe stream 的官方 SSE 响应头；不设置 Content-Length。"""

    event_stream = new_event_stream(conversation_id)
    headers = {
        "Content-Type": event_stream.content_type,
        "Cache-Control": "no-store",
        "Connection": "close",
    }
    for key, value in (event_stream.response_headers or {}).items():
        headers[key] = value
    return headers


class _FeedFailure:
    """把 native 流的异常送入编码泵，由官方 transform 转成 error/finish chunk。"""

    __slots__ = ("error",)

    def __init__(self, error: BaseException) -> None:
        self.error = error


class RegistryAiPresentationObserver:
    """绑定单个 presentation 的具体 observer。

    生命周期事件推进 registry 展示状态；终态由 HTTP 公共边界的
    ``finish_request`` 统一收尾，observer 不标记终态。native event 发布在
    单一事件源（调用方传入的 ``events``）上进行：编码泵只消费同一循环喂入
    的事件，不产生第二个事件生产者。
    """

    def __init__(
        self,
        *,
        registry: AiPresentationRegistry,
        presentation_id: str,
        conversation_id: str,
    ) -> None:
        self._registry = registry
        self._presentation_id = str(presentation_id or "")
        self._conversation_id = str(conversation_id or "")
        self._publishing_active = True

    @property
    def presentation_id(self) -> str:
        return self._presentation_id

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    # -- AiRunObserver 生命周期 ---------------------------------------------

    def claim_root_run(self, *, run_id: str) -> str:
        """委托 registry 原子领取唯一 root run 槽位（语义见 registry）。"""

        return self._registry.claim_root_run(self._presentation_id, run_id)

    def run_started(
        self,
        *,
        run_id: str,
        parent_run_id: str,
        use_case_id: str,
        label: str,
    ) -> None:
        self._registry.mark_agent_started(self._presentation_id)

    def running(self, *, run_id: str) -> None:
        self._registry.update_status(self._presentation_id, RUNNING)

    def tool_activity(self, *, run_id: str, tool_name: str) -> None:
        return None

    def finalizing(self, *, run_id: str) -> None:
        self._registry.update_status(self._presentation_id, FINALIZING)

    def completed(self, *, run_id: str) -> None:
        return None

    def failed(self, *, run_id: str, code: str, message: str) -> None:
        return None

    def cancelled(self, *, run_id: str) -> None:
        return None

    def child_status(
        self,
        *,
        child_run_id: str,
        status: str,
        label: str,
    ) -> None:
        return None

    # -- native event 发布 ---------------------------------------------------

    def observe_native_events(
        self,
        events: AsyncIterator[Any],
    ) -> AsyncIterator[Any]:
        """透传 native events，同时官方转换/编码并发布 chunk 到 registry。

        调用方消费返回值时驱动 Agent 运行；事件原样透传（调用方可能依赖
        结果事件）。缓冲溢出或编码/发布异常只停用发布（编码泵继续排空，
        避免背压积压），不得改写 Agent 执行语义。
        """

        return self._observe_native_events(events)

    async def _observe_native_events(
        self,
        events: AsyncIterator[Any],
    ) -> AsyncIterator[Any]:
        registry = self._registry
        presentation_id = self._presentation_id
        event_stream = new_event_stream(self._conversation_id)
        feed_queue: asyncio.Queue[Any] = asyncio.Queue()
        feed_done = object()

        async def _feed() -> AsyncIterator[Any]:
            while True:
                item = await feed_queue.get()
                if item is feed_done:
                    return
                if isinstance(item, _FeedFailure):
                    raise item.error
                yield item

        async def _pump() -> None:
            source = _feed()
            transformed = event_stream.transform_stream(source)
            encoded = event_stream.encode_stream(transformed)
            iterators: list[AsyncIterator[Any]] = [encoded, transformed, source]
            try:
                async for chunk in encoded:
                    if not self._publishing_active:
                        continue
                    if not registry.publish_chunk(
                        presentation_id,
                        chunk.encode("utf-8"),
                    ):
                        # 缓冲溢出：registry 已显式 failed 并关闭；停止发布，
                        # 继续排空编码流，不影响 Agent 执行。
                        self._publishing_active = False
            except Exception:
                _logger.warning(
                    "AI 展示发布失败，降级展示：%s",
                    presentation_id,
                    exc_info=True,
                )
                self._publishing_active = False
            finally:
                for iterator in reversed(iterators):
                    try:
                        await iterator.aclose()
                    except Exception:
                        pass

        pump_task = asyncio.get_running_loop().create_task(_pump())
        try:
            async for event in events:
                await feed_queue.put(event)
                yield event
        except GeneratorExit:
            # 调用方提前关闭包装流：正常收尾编码泵，不注入错误。
            raise
        except BaseException as exc:
            await feed_queue.put(_FeedFailure(exc))
            raise
        finally:
            await feed_queue.put(feed_done)
            try:
                await pump_task
            except Exception:
                pass

    async def publish_error_chunks(self, error: BaseException) -> None:
        """流尚未启动（装配期失败）时，也只发布官方 error/finish chunk。"""

        if not self._publishing_active:
            return
        event_stream = new_event_stream(self._conversation_id)

        async def failing_events() -> AsyncIterator[Any]:
            raise error
            yield  # pragma: no cover - 让函数成为 async generator

        source = failing_events()
        transformed = event_stream.transform_stream(source)
        encoded = event_stream.encode_stream(transformed)
        iterators: list[AsyncIterator[Any]] = [encoded, transformed, source]
        try:
            async for chunk in encoded:
                if not self._registry.publish_chunk(
                    self._presentation_id,
                    chunk.encode("utf-8"),
                ):
                    self._publishing_active = False
                    break
        except Exception:
            _logger.warning(
                "AI 展示错误 chunk 发布失败：%s",
                self._presentation_id,
                exc_info=True,
            )
            self._publishing_active = False
        finally:
            for iterator in reversed(iterators):
                try:
                    await iterator.aclose()
                except Exception:
                    pass
