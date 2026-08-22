"""唯一 Vercel AI 协议入口：解析请求、运行官方 SSE、派生展示历史。

不选择业务工具、不改写消息语义：Agent 装配与运行通过
``GlobalAgentChatService`` → ``AiAgentFactory``，全部 UIMessage 与事件转换
使用官方 ``VercelAIAdapter`` / ``VercelAIEventStream``。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage
from pydantic_ai.ui.vercel_ai import VercelAIAdapter, VercelAIEventStream
from pydantic_ai.ui.vercel_ai.request_types import (
    RegenerateMessage,
    SubmitMessage,
    TextUIPart,
    UIMessage,
)

from erp_web.schemas.ai_work import AiWorkUiMessagesDetail
from erp_web.services.ai_chat_detached_runner import get_detached_chat_runner
from erp_web.services.ai_agent_factory import (
    AiAgentExecutionError,
    AiAgentStreamSession,
)
from erp_web.services.ai_chat_run_registry import AiChatRunRegistry
from erp_web.services.ai_conversation_event_bus import AiConversationEventBus
from erp_web.services.global_agent_chat_service import (
    GLOBAL_CHAT_ACTOR_ID,
    GLOBAL_CHAT_PROFILE_ID,
    GLOBAL_CHAT_TENANT_ID,
    GlobalAgentChatService,
)
from erp_web.stores.ai_chat_turn_claim_store import (
    COMPLETED,
    FAILED,
    AiChatTurnAlreadyAcceptedError,
    AiChatTurnClaim,
    AiChatTurnClaimError,
    AiChatTurnClaimStore,
)
from erp_web.stores.pydantic_ai_event_outbox_store import (
    PydanticAiEventOutboxStore,
)
from erp_web.stores.pydantic_deferred_task_link_store import (
    DEFERRED_HANDSHAKE_OUTBOX_KIND,
    MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS,
    PydanticDeferredLinkError,
    PydanticDeferredTaskLinkStore,
)
from erp_web.stores.pydantic_message_store import PydanticMessageStoreError


_logger = logging.getLogger(__name__)

VERCEL_SDK_VERSION = 7
MAX_USER_TEXT_CHARS = 32_000

# 报告 A-13：请求侧投递队列上限。慢 observer 时中间内容 chunk（文本/推理
# delta 与 tool-input-delta）按背压丢弃（保持已投递顺序），但结构性事件
# （工具骨架、finish-step/finish/error）与 None 哨兵保证送达——客户端绝不
# 收到缺少闭合或工具骨架的截断流；回合结束由前端按 /ui-messages 对账完整
# 内容（onFinish 重读）。
MAX_DELIVERY_QUEUE_CHUNKS = 1024
# 保证送达的结构性事件类型（报告 A-13）。
_GUARANTEED_CHUNK_TYPES = frozenset({
    "finish-step",
    "finish",
    "error",
    "tool-input-start",
    "tool-input-available",
    "tool-output-available",
})
# 报告 A-01/A-13：候选缓冲段上限。run 开始到提交/收尾之间，进入 Deferred
# 握手后需要等待提交屏障的结构事件进入该缓冲；超限时丢弃中间内容 chunk
# （保持已缓冲顺序），但结构闭合事件进入有界尾部保留区，保证流始终完整
# 闭合并保持 encoder 原顺序；内容最终由 /ui-messages 提供。
MAX_HELD_CHUNKS = 4096
MAX_HELD_TOTAL_BYTES = 4 * 1024 * 1024
MAX_HELD_TAIL_CHUNKS = 16

# 修复计划第 14 节：模型临时 text/reasoning、工具输入骨架与步骤开始信封属于
# 运行中临时态，产生时立即写入请求流；不再因「run 未来可能进入 Deferred」
# 而整轮缓冲。提交屏障只约束结构化业务成功终态，不对自然语言做语义审查。
_LIVE_STREAM_CHUNK_TYPES = frozenset(
    {
        "start",
        "start-step",
        "text-delta",
        "reasoning-delta",
        "tool-input-start",
        "tool-input-delta",
        "tool-input-available",
    }
)
# Deferred 握手开始后必须等待组合事务提交成功才发布的结构化成功/闭合事件。
_COMMIT_GATED_CHUNK_TYPES = frozenset(
    {
        "tool-output-available",
        "finish-step",
        "finish",
    }
)

_GLOBAL_CHAT_CONVERSATION_PATTERN = re.compile(
    r"^conversation_global_chat_[0-9a-f]{32}$"
)


def new_event_stream(conversation_id: str) -> VercelAIEventStream[Any, Any]:
    """创建最小安全 ``run_input`` 的官方 Vercel 事件流。

    本模块是 ``pydantic_ai.ui`` 协议类型的唯一 import 边界；其他展示服务
    （如业务根运行的 observe 编码）必须通过该工厂获得事件流，不得自行
    构造 ``SubmitMessage`` / ``VercelAIEventStream``。``messages=[]`` 表示
    该流不代表一条新的用户聊天消息，只作为展示编码会话键。
    """

    run_input = SubmitMessage(id=str(conversation_id or ""), messages=[])
    return VercelAIEventStream(run_input, sdk_version=VERCEL_SDK_VERSION)


def _chunk_payload(chunk: str) -> dict[str, Any] | None:
    """解析单条官方 SSE chunk 的 JSON 载荷；不可解析时返回 None。"""

    text = str(chunk or "").strip()
    if not text.startswith("data:"):
        return None
    payload_text = text[len("data:") :].strip()
    if not payload_text or payload_text == "[DONE]":
        return None
    try:
        payload = json.loads(payload_text)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _is_guaranteed_chunk(chunk: str | None) -> bool:
    """报告 A-13：结构闭合事件与哨兵必须送达，背压下等待队列空位而不丢弃。"""

    if chunk is None:
        return True
    payload = _chunk_payload(chunk)
    return (
        payload is not None
        and payload.get("type") in _GUARANTEED_CHUNK_TYPES
    )


class VercelUiProtocolError(Exception):
    """流开始前的协议错误；按 status_code 映射项目标准 JSON 响应。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = int(status_code)
        self.code = str(code)
        super().__init__(message)


@dataclass
class VercelAiChatRun:
    """一次已领取的聊天 run；预流检查完成后只输出官方 SSE。"""

    conversation_id: str
    client_message_id: str
    claim: AiChatTurnClaim
    run_input: SubmitMessage
    new_messages: list[ModelMessage]
    _service: "VercelAiUiService" = field(repr=False)
    _event_stream: VercelAIEventStream[Any, Any] = field(repr=False)

    def sse_headers(self) -> dict[str, str]:
        """官方 SSE 响应头；不设置 Content-Length。"""

        headers = {
            "Content-Type": self._event_stream.content_type,
            "Cache-Control": "no-store",
            "Connection": "close",
        }
        for key, value in (self._event_stream.response_headers or {}).items():
            headers[key] = value
        return headers

    async def stream(self, write_chunk: Callable[[bytes], None]) -> None:
        """server-side drain：Agent run 独立于客户端连接完整消费到终态。

        producer 由进程级 ``DetachedChatRunner`` 托管（报告 R-02）：HTTP 路由
        为每个请求创建一次性 event loop 并在请求退出时立即关闭，请求协程取消
        或 loop 关闭只停止本方法的 SSE 写入，不取消 producer；收尾（首次
        Deferred history/link/outbox 原子提交与 claim 终态）在 runner loop 上
        完成，claim 到达终态后才投递 None 哨兵结束请求侧 drain。

        Deferred 握手的官方事件遵守“未提交内容与成功终态不外泄”不变量
        （计划 R-01 / 报告 A-01 多轮收紧后）：run 是否进入 Deferred 只有在
        CallDeferred 抛出时才能确定，因此除整条 run 的初始 start 与首个
        start-step 外，从 run 开始的全部官方事件——包括首个工具出现之前的
        文本、后续模型轮的 start-step、工具事件与终态事件——都先进入有界
        缓冲。Deferred run 只有组合事务提交成功后才发布缓冲段；提交失败时
        客户端只会收到官方 error/finish，不会出现任何未提交的文本、工具事
        件、调用 id、参数或成功终态。run 最终未进入 Deferred（含控制工具调
        用被运行时拒绝）时，缓冲段在回合收尾按 encoder 原顺序补发。后续模
        型轮的 start-step 不再实时发布，避免越过仍在缓冲的前一轮内容造成
        官方事件重排。

        缓冲与投递均有明确上限（报告 A-13）：超限时丢弃中间内容 chunk，但
        结构闭合事件（finish-step/finish/error）与哨兵保证送达——客户端绝
        不收到缺少闭合的截断流；回合结束由前端按 /ui-messages 对账完整内容。

        outbox 批次只持久化官方编码流的有界终态段（link store 截取），不持
        久化整条流式 delta 流——流式 run 按 delta 逐条编码可达数千 chunk，
        订阅端以 /ui-messages 的已提交历史为内容事实源。
        """

        request_loop = asyncio.get_running_loop()
        # 报告 A-13：投递队列有界。慢/停滞 observer 不得让 producer 无界堆积；
        # 队列满时丢弃新 chunk（保持已投递顺序），None 哨兵保证最终送达。
        chunk_queue: asyncio.Queue[str | None] = asyncio.Queue(
            maxsize=MAX_DELIVERY_QUEUE_CHUNKS
        )
        client_disconnected = False

        def write_live(chunk: str) -> None:
            nonlocal client_disconnected
            if client_disconnected:
                return
            try:
                write_chunk(chunk.encode("utf-8"))
            except OSError:
                client_disconnected = True

        def deliver(chunk: str | None) -> None:
            # producer 运行在进程级 runner loop，向请求 loop 投递必须线程
            # 安全。请求 loop 已关闭（客户端断线/请求退出）时停止投递；
            # producer 在 runner loop 继续完成提交与 claim 收尾（报告 R-02）。
            def _put() -> None:
                try:
                    chunk_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    if _is_guaranteed_chunk(chunk):
                        # 报告 A-13：结构闭合事件与哨兵稍后重试直至送达，
                        # 绝不产生缺少 finish 的截断流。
                        request_loop.call_later(0.05, _put)
                    # 中间内容 chunk 按背压丢弃；前端在 onFinish 按
                    # /ui-messages 对账完整内容。

            try:
                request_loop.call_soon_threadsafe(_put)
            except RuntimeError:
                pass

        # producer 移交进程级 runner：请求 loop 取消/关闭不再连坐取消它
        # （报告 R-02）。请求侧只按原顺序消费投递队列。
        try:
            get_detached_chat_runner().submit(self._produce_and_finalize(deliver))
        except Exception:
            # 报告 A-02：stream 启动失败（producer 未托管）时确定性收尾，
            # 不留下永久 run lock 与 claimed turn。
            self.abort_before_stream()
            raise
        try:
            while True:
                chunk = await chunk_queue.get()
                if chunk is None:
                    break
                write_live(chunk)
        except asyncio.CancelledError:
            # 请求协程被取消（客户端断线等）：producer 已托管在进程级
            # runner loop，不随请求取消；这里只结束原 POST 写入。
            raise

    def abort_before_stream(self) -> None:
        """报告 A-02：stream 启动前的一切失败（写响应头、创建 loop）收尾。

        ``prepare_run()`` 已领取 registry 锁与 claim；若此后在启动 producer
        之前失败，必须把 claim 写为 failed 终态并释放 run lock，否则
        conversation 永久锁死（后续请求全部 AI_CHAT_RUN_ACTIVE）。
        """

        try:
            self._service.claim_store.finish_turn(
                self.claim.claim_id,
                status=FAILED,
            )
        except Exception:
            _logger.warning(
                "stream 启动前失败后的 claim 收尾失败：claim_id=%s",
                self.claim.claim_id,
            )
        self._service.run_registry.release(self.conversation_id)

    async def _produce_and_finalize(
        self,
        deliver: Callable[[str | None], None],
    ) -> None:
        """runner loop 托管的 producer：消费官方事件流并完成提交/claim 收尾。

        所有到达客户端的 chunk 都通过 ``deliver`` 按原顺序投递到请求 loop
        队列；None 哨兵在收尾完成（claim 终态、registry 释放）之后才到达，
        因此请求侧 drain 结束后 claim 必然已是终态。请求 loop 关闭只影响
        投递（``deliver`` 内部吞掉 RuntimeError），不中断本协程。
        """

        session: AiAgentStreamSession[str] | None = None
        failure_error: AiAgentExecutionError | None = None
        # 报告 A-13：outbox 只需要官方编码流的有界终态段（link store 截取末
        # 尾 MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS 条）。这里用固定上限 ring
        # buffer 收集，避免长回复/大量 delta 线性占用进程内存；向当前 observer
        # 传输的内容（deliver/held_chunks）与 durable outbox 缓冲相互独立。
        encoded_chunks: deque[str] = deque(maxlen=MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS)
        # 报告 A-01/A-13：候选缓冲段。run 是否进入 Deferred 只有到 CallDeferred
        # 抛出时才能确定，因此除首个信封外的全部内容事件（含首个工具前的文本
        # 与后续模型轮的 start-step）从 run 开始就进入该缓冲。条数/字节上限
        # 明确有界；超限丢弃中间内容 chunk（保持已缓冲顺序），但结构闭合事件
        # （finish-step/finish/error）进入有界尾部保留区，保证流始终完整闭合
        # 且保持 encoder 原顺序；内容事实源是 /ui-messages。
        held_chunks: list[str] = []
        held_tail: list[str] = []
        held_bytes = 0
        held_overflow_logged = False
        error_close_delivered = False

        def hold(chunk: str) -> None:
            nonlocal held_bytes, held_overflow_logged
            chunk_bytes = len(chunk.encode("utf-8"))
            if (
                len(held_chunks) >= MAX_HELD_CHUNKS
                or held_bytes + chunk_bytes > MAX_HELD_TOTAL_BYTES
            ):
                if (
                    _is_guaranteed_chunk(chunk)
                    and len(held_tail) < MAX_HELD_TAIL_CHUNKS
                ):
                    # 报告 A-13：溢出后的结构闭合事件仍进入尾部保留区，
                    # 补发时位于剩余缓冲之后，保持 encoder 原顺序。
                    held_tail.append(chunk)
                elif not held_overflow_logged:
                    held_overflow_logged = True
                    _logger.warning(
                        "候选缓冲段超过上限，丢弃中间内容 chunk：conversation=%s",
                        self.conversation_id,
                    )
                return
            held_chunks.append(chunk)
            held_bytes += chunk_bytes

        def flush_held_before_error_close() -> None:
            """run 未进入 Deferred 就失败时，按原顺序补发缓冲段。

            缓冲段属于该失败回合的真实事件（旧实现里它们本来就是实时到达
            的），必须先于官方 error 闭合送达；已进入 Deferred 握手的缓冲段
            绝不在此补发（由组合事务提交结果决定发布或丢弃）。
            """

            if session is not None and session.deferred_handshake_started:
                return
            for pending_chunk in held_chunks:
                deliver(pending_chunk)
            held_chunks.clear()
            for pending_chunk in held_tail:
                deliver(pending_chunk)
            held_tail.clear()

        async def produce() -> None:
            nonlocal session, failure_error, error_close_delivered
            # 修复计划第 14 节：模型临时 text/reasoning/进度与每一步的
            # start-step 信封实时下发；只有在 Deferred 握手已经开始之后，
            # 工具结果成功态与 finish-step/finish 等结构化成功终态才进入
            # 提交屏障缓冲。非 Deferred run 因此不再经过整轮候选缓冲。
            try:
                async with self._service.chat_service.open_chat_run(
                    conversation_id=self.conversation_id,
                    new_messages=self.new_messages,
                    client_message_id=self.client_message_id,
                ) as chat_session:
                    session = chat_session
                    native = chat_session.events(self.new_messages)
                    transformed = self._event_stream.transform_stream(native)
                    encoded = self._event_stream.encode_stream(transformed)
                    async for chunk in encoded:
                        encoded_chunks.append(chunk)
                        payload = _chunk_payload(chunk)
                        chunk_type = (
                            payload.get("type") if payload is not None else None
                        )
                        if chunk_type in _LIVE_STREAM_CHUNK_TYPES:
                            deliver(chunk)
                        elif chunk_type in _COMMIT_GATED_CHUNK_TYPES and (
                            session is not None
                            and session.deferred_handshake_started
                        ):
                            # Deferred 握手已开始：结构化成功/闭合终态必须等待
                            # 组合事务提交成功后才发布。
                            hold(chunk)
                        else:
                            # 非 Deferred（或握手尚未开始）：结构闭合事件也按
                            # encoder 原顺序实时下发，不做整轮缓冲。
                            deliver(chunk)
            except AiAgentExecutionError as error:
                # 装配期/流前失败：先补发非 Deferred 缓冲段，再发送官方
                # error/finish chunk。
                failure_error = error
                flush_held_before_error_close()
                async for chunk in self._encoded_error_chunks(error):
                    encoded_chunks.append(chunk)
                    deliver(chunk)
                error_close_delivered = True
            except asyncio.CancelledError:
                # runner loop 只在进程退出时停止；按 run 失败处理并继续收尾，
                # 不留下永远 claimed 的 conversation。
                failure_error = AiAgentExecutionError(
                    "AI_AGENT_RUN_CANCELLED",
                    "AI Agent 运行被终止。",
                    conversation_id=self.conversation_id,
                )
                flush_held_before_error_close()
            except Exception:
                failure_error = (
                    session.failure_error
                    if session is not None and session.failure_error is not None
                    else AiAgentExecutionError(
                        "AI_AGENT_RUN_FAILED",
                        "AI Agent 运行失败。",
                        conversation_id=self.conversation_id,
                    )
                )
                flush_held_before_error_close()

        await produce()
        try:
            if failure_error is None and session is not None:
                # 官方 Vercel transform 会把 native 异常转换成 error chunk
                # 并消费掉异常；从 session 取回稳定错误，才能把 claim 写成
                # 可诊断的终态，而不是 failed + 空 error_code。
                failure_error = session.failure_error
            commit_error: AiAgentExecutionError | None = None
            deferred_handshake = (
                session is not None and session.deferred_handshake_started
            )
            if (
                session is not None
                and session.completed
                and failure_error is None
            ):
                commit_error = self._commit_deferred_handshake(
                    session,
                    encoded_chunks,
                )
                if commit_error is not None:
                    failure_error = commit_error
            if commit_error is None and not (
                deferred_handshake and failure_error is not None
            ):
                # Deferred run 只有组合事务成功后才发布缓冲段；非 Deferred
                # run 在回合收尾按 encoder 原顺序补发缓冲段（含溢出时保留的
                # 结构闭合尾部）。缓冲为空时是 no-op。
                for chunk in held_chunks:
                    deliver(chunk)
                for chunk in held_tail:
                    deliver(chunk)
            else:
                # 提交失败（或已进入 Deferred 握手后失败）：绝不发布未提交
                # 段；以官方 error 闭合。优先使用提交错误，其次是 run 失败错误。
                held_chunks.clear()
                held_tail.clear()
                close_error = (
                    commit_error if commit_error is not None else failure_error
                )
                if close_error is not None and not error_close_delivered:
                    async for chunk in self._encoded_error_chunks(close_error):
                        deliver(chunk)
                    error_close_delivered = True
            if (
                session is not None
                and session.completed
                and failure_error is None
            ):
                terminal_status = COMPLETED
            else:
                terminal_status = FAILED
            try:
                self._service.claim_store.finish_turn(
                    self.claim.claim_id,
                    status=terminal_status,
                    error_code=(
                        failure_error.code
                        if terminal_status == FAILED
                        and failure_error is not None
                        else ""
                    ),
                    trace_id=(
                        failure_error.trace_id
                        if terminal_status == FAILED
                        and failure_error is not None
                        else ""
                    ),
                )
            except AiChatTurnClaimError:
                _logger.warning(
                    "AI chat turn claim 终态更新失败：claim_id=%s",
                    self.claim.claim_id,
                )
            except Exception:
                _logger.warning(
                    "AI chat turn claim 终态更新失败：claim_id=%s",
                    self.claim.claim_id,
                )
        except Exception:
            _logger.exception(
                "AI chat run 收尾失败：conversation=%s",
                self.conversation_id,
            )
        finally:
            self._service.run_registry.release(self.conversation_id)
        # None 哨兵在 claim 终态与 registry 释放之后投递：请求侧 drain 结束
        # 时收尾必然已完成；请求 loop 已关闭时 deliver 静默放弃。
        deliver(None)

    def _commit_deferred_handshake(
        self,
        session: AiAgentStreamSession[Any],
        encoded_chunks: Sequence[str],
    ) -> AiAgentExecutionError | None:
        """run 以 DeferredToolRequests 暂停时：history/link/outbox 同事务提交。

        非 Deferred 结果由 session 在完成时正常保存历史，这里不做任何事。
        Deferred 握手必须恰好包含一个未闭合调用且与 link 的 tool_call_id 一致；
        重复调用已由 Runtime 以稳定错误闭合，这里的 cardinality 校验是组合事务
        前的最后一道防线。提交失败时返回稳定错误（claim 记为 failed），任务与
        link 交由恢复协调器对账，不向客户端发布未提交的最终结果。
        """

        deferred_output = session.deferred_output
        if deferred_output is None:
            return None
        link_store = self._service.deferred_links
        link = link_store.active_for_conversation(self.conversation_id)
        calls = list(deferred_output.calls)
        call_ids = {call.tool_call_id for call in calls}
        if len(calls) != 1 or link is None or link.tool_call_id not in call_ids:
            return AiAgentExecutionError(
                "AI_CHAT_DEFERRED_HANDSHAKE_INVALID",
                "Deferred 握手必须恰好包含一个与 link 匹配的未闭合调用，"
                "无法提交首次历史。",
                conversation_id=self.conversation_id,
                run_id=session.run_id,
            )
        result = session.result
        messages = list(result.all_messages()) if result is not None else []
        try:
            committed_version = self._commit_initial_history_with_degradation(
                link_store,
                link,
                session,
                messages,
                list(encoded_chunks),
            )
        except (PydanticDeferredLinkError, PydanticMessageStoreError) as exc:
            _logger.exception(
                "首次 Deferred history 提交失败：conversation=%s link=%s",
                self.conversation_id,
                link.link_id,
            )
            return AiAgentExecutionError(
                exc.code,
                str(exc),
                conversation_id=self.conversation_id,
                run_id=session.run_id,
            )
        # 报告 R-05：durable commit 之后的通知是 best-effort 独立阶段。
        # publish/mark_published 异常只留下未发布 outbox（后台 publisher 重投），
        # 不得向上传播跳过 held chunks flush 与 claim.finish_turn()。
        try:
            batches = self._service.event_outbox.list_after(
                self.conversation_id,
                after_history_version=committed_version - 1,
            )
            for batch in batches:
                if (
                    batch.history_version == committed_version
                    and batch.kind == DEFERRED_HANDSHAKE_OUTBOX_KIND
                ):
                    self._service.event_bus.publish(self.conversation_id, batch)
                    # 记录已投递；提交成功但进程在广播前退出时，由后台 outbox
                    # publisher 重投，订阅端按 history_version 去重。
                    self._service.event_outbox.mark_published([batch])
        except Exception:
            _logger.exception(
                "首次握手批次通知失败，交由 outbox publisher 重投：conversation=%s",
                self.conversation_id,
            )
        return None

    def _commit_initial_history_with_degradation(
        self,
        link_store: PydanticDeferredTaskLinkStore,
        link: Any,
        session: AiAgentStreamSession[str],
        messages: Sequence[ModelMessage],
        encoded_chunks: list[str],
    ) -> int:
        """报告 A-16：首次握手与 continuation 一致的确定性 resync-only 降级。

        合法请求可能产出超过 outbox 上限的官方事件（单条超大 chunk 或超大
        批次）；旧实现会把本轮直接标记失败，已原子创建的 Task/link 永远无法
        提交，最终只能由 provisional sweep 取消。修复后超限降级为空事件批次：
        同一事务仍保存 history、置 link ready、递增 history_version 并写入空
        事件 outbox；订阅端推进游标并重读 /ui-messages（内容事实源）。
        """

        try:
            return link_store.commit_initial_deferred_history(
                self.conversation_id,
                messages,
                link_id=link.link_id,
                request_run_id=session.run_id,
                encoded_chunks=encoded_chunks,
            )
        except PydanticDeferredLinkError as exc:
            if exc.code != "PYDANTIC_DEFERRED_OUTBOX_TOO_LARGE":
                raise
            _logger.warning(
                "首次握手官方事件超过 outbox 上限，降级为 resync-only 批次："
                "conversation=%s link=%s",
                self.conversation_id,
                link.link_id,
            )
            return link_store.commit_initial_deferred_history(
                self.conversation_id,
                messages,
                link_id=link.link_id,
                request_run_id=session.run_id,
                encoded_chunks=[],
            )

    async def _encoded_error_chunks(
        self,
        error: AiAgentExecutionError,
    ) -> AsyncIterator[str]:
        """流开始前失败或提交失败时，只产生官方 error/finish chunk。

        必须使用全新的事件流实例编码：run 使用的事件流携带已缓冲工具调用的
        transform 状态，复用它会让错误闭合流回填未提交调用的
        ``tool-input-error``，把未提交的工具状态泄露给客户端。
        """

        async def failing_events() -> AsyncIterator[Any]:
            raise error
            yield  # pragma: no cover - 让函数成为 async generator

        error_stream = new_event_stream(self.conversation_id)
        transformed = error_stream.transform_stream(failing_events())
        encoded = error_stream.encode_stream(transformed)
        async for chunk in encoded:
            yield chunk


class VercelAiUiService:
    """实时 SSE 与历史 UIMessage 派生的协议边界。"""

    def __init__(
        self,
        *,
        chat_service: GlobalAgentChatService,
        claim_store: AiChatTurnClaimStore,
        run_registry: AiChatRunRegistry,
        deferred_links: PydanticDeferredTaskLinkStore,
        event_outbox: PydanticAiEventOutboxStore,
        event_bus: AiConversationEventBus,
    ) -> None:
        self.chat_service = chat_service
        self.claim_store = claim_store
        self.run_registry = run_registry
        self.deferred_links = deferred_links
        self.event_outbox = event_outbox
        self.event_bus = event_bus

    def prepare_run(
        self,
        body: bytes,
    ) -> VercelAiChatRun:
        """预流校验 + 领取锁与 claim；失败时仍返回标准 JSON 错误。"""

        run_input = self._parse_run_input(body)
        conversation_id = self._validate_conversation_id(run_input.id)
        ui_message = self._validate_single_user_message(run_input.messages)
        # 服务端原子前置校验：存在未解决 Deferred link（awaiting_history/ready）
        # 的 conversation 不接受新的普通用户回合；前端禁用发送只是体验提示。
        if self.deferred_links.has_active(conversation_id):
            raise VercelUiProtocolError(
                409,
                "AI_CHAT_CONVERSATION_TASK_PENDING",
                "该会话存在未解决的全局任务；任务终结前不能发送新消息，"
                "仍可提交补充资料、审批或取消任务。",
            )
        if not self.run_registry.acquire(conversation_id):
            raise VercelUiProtocolError(
                409,
                "AI_CHAT_RUN_ACTIVE",
                "该 conversation 已有活动 run，请稍后再试。",
            )
        claim: AiChatTurnClaim | None = None
        try:
            self._verify_history_ownership(conversation_id)
            try:
                claim = self.claim_store.claim_turn(
                    conversation_id=conversation_id,
                    client_message_id=str(ui_message.id or "").strip(),
                    profile_id=GLOBAL_CHAT_PROFILE_ID,
                    actor_id=GLOBAL_CHAT_ACTOR_ID,
                    tenant_id=GLOBAL_CHAT_TENANT_ID,
                )
            except AiChatTurnAlreadyAcceptedError:
                raise VercelUiProtocolError(
                    409,
                    "AI_CHAT_TURN_ALREADY_ACCEPTED",
                    "本轮消息已被服务端接受，请重新读取消息历史。",
                ) from None
            new_messages = self._load_new_messages(ui_message)
            event_stream: VercelAIEventStream[Any, Any] = VercelAIEventStream(
                run_input,
                sdk_version=VERCEL_SDK_VERSION,
            )
            assert claim is not None
            return VercelAiChatRun(
                conversation_id=conversation_id,
                client_message_id=str(ui_message.id or "").strip(),
                claim=claim,
                run_input=run_input,
                new_messages=new_messages,
                _service=self,
                _event_stream=event_stream,
            )
        except Exception:
            if claim is not None:
                try:
                    self.claim_store.finish_turn(
                        claim.claim_id,
                        status=FAILED,
                    )
                except Exception:
                    _logger.warning(
                        "AI chat 预流失败后的 claim 收尾失败：claim_id=%s",
                        claim.claim_id,
                    )
            self.run_registry.release(conversation_id)
            raise

    def dump_ui_messages(self, conversation_id: str) -> AiWorkUiMessagesDetail:
        """用官方 Adapter 派生只读 UIMessage[]；不存在时抛 404 协议错误。"""

        normalized_id = str(conversation_id or "").strip()
        try:
            history = self.chat_service.message_store.get(normalized_id)
        except PydanticMessageStoreError as exc:
            raise VercelUiProtocolError(500, exc.code, str(exc)) from None
        if history is None:
            raise VercelUiProtocolError(
                404,
                "PYDANTIC_MESSAGE_HISTORY_NOT_FOUND",
                "Pydantic 对话不存在。",
            )
        try:
            messages = history.model_messages()
        except PydanticMessageStoreError as exc:
            raise VercelUiProtocolError(500, exc.code, str(exc)) from None
        ui_messages = VercelAIAdapter.dump_messages(
            messages,
            sdk_version=VERCEL_SDK_VERSION,
        )
        serialized = [
            item.model_dump(mode="json", by_alias=True, exclude_none=True)
            for item in ui_messages
        ]
        self._normalize_deferred_approval_parts(
            history.conversation_id,
            serialized,
        )
        return {
            "ok": True,
            "conversation_id": history.conversation_id,
            "history_version": history.history_version,
            "created_at": history.created_at,
            "updated_at": history.updated_at,
            "messages": serialized,
        }

    def _normalize_deferred_approval_parts(
        self,
        conversation_id: str,
        serialized: list[dict[str, Any]],
    ) -> None:
        """按 Deferred ledger 归一化开放 global_task_start 的展示语义。

        官方 Adapter 无法仅凭缺少 ToolReturn 区分 ``CallDeferred`` 与业务审批，
        会把开放的 ``global_task_start`` 输出为 ``approval-requested``。但该调用
        实际是后台 Global Task，不是业务审批；conversation 级任务卡才是状态的
        唯一展示事实源。这里按当前未解决 link 把该 part 改写为中性的
        ``input-available``（已受理），并去掉 approval 字段，避免前端误显示
        「等待审批」。
        """

        link = self.deferred_links.active_for_conversation(conversation_id)
        if link is None:
            return
        deferred_call_id = link.tool_call_id
        if not deferred_call_id:
            return
        for message in serialized:
            parts = message.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if part.get("state") != "approval-requested":
                    continue
                if part.get("toolCallId") != deferred_call_id:
                    continue
                part["state"] = "input-available"
                part.pop("approval", None)

    # -- 预流校验 ----------------------------------------------------------

    def _parse_run_input(self, body: bytes) -> SubmitMessage:
        try:
            run_input = VercelAIAdapter.build_run_input(body)
        except ValidationError:
            raise VercelUiProtocolError(
                422,
                "AI_CHAT_REQUEST_SCHEMA_INVALID",
                "请求体不符合 Vercel AI 消息协议。",
            ) from None
        if isinstance(run_input, RegenerateMessage):
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_TRIGGER_UNSUPPORTED",
                "只接受 submit-message；不支持 regenerate 或客户端工具审批请求。",
            )
        return run_input

    def _validate_conversation_id(self, value: Any) -> str:
        conversation_id = str(value or "").strip()
        if not _GLOBAL_CHAT_CONVERSATION_PATTERN.fullmatch(conversation_id):
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_CONVERSATION_ID_INVALID",
                "conversation ID 必须是 conversation_global_chat_ 加 32 位十六进制。",
            )
        return conversation_id

    def _validate_single_user_message(
        self,
        messages: Sequence[UIMessage],
    ) -> UIMessage:
        if len(messages) != 1:
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_MESSAGE_INVALID",
                "本轮只能提交恰好一条新用户消息。",
            )
        message = messages[0]
        if message.role != "user":
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_MESSAGE_INVALID",
                "本轮只能提交 user 消息。",
            )
        if not str(message.id or "").strip():
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_MESSAGE_INVALID",
                "用户消息必须携带客户端生成的 id。",
            )
        if not message.parts:
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_MESSAGE_INVALID",
                "用户消息不能为空。",
            )
        total_chars = 0
        for part in message.parts:
            if not isinstance(part, TextUIPart):
                raise VercelUiProtocolError(
                    400,
                    "AI_CHAT_PART_UNSUPPORTED",
                    "本期入口只接受非空 text part。",
                )
            if not str(part.text or "").strip():
                raise VercelUiProtocolError(
                    400,
                    "AI_CHAT_MESSAGE_INVALID",
                    "文本消息不能为空。",
                )
            total_chars += len(str(part.text or ""))
        if total_chars > MAX_USER_TEXT_CHARS:
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_MESSAGE_TOO_LONG",
                f"用户消息不能超过 {MAX_USER_TEXT_CHARS} 字符。",
            )
        return message

    def _verify_history_ownership(self, conversation_id: str) -> None:
        """已有历史必须归属 global.chat profile；否则拒绝继续运行。"""

        try:
            history = self.chat_service.message_store.get(conversation_id)
        except PydanticMessageStoreError as exc:
            raise VercelUiProtocolError(500, exc.code, str(exc)) from None
        if history is None:
            return
        claim = self.claim_store.find_for_conversation(conversation_id)
        if (
            claim is None
            or claim.profile_id != GLOBAL_CHAT_PROFILE_ID
            or claim.actor_id != GLOBAL_CHAT_ACTOR_ID
            or claim.tenant_id != GLOBAL_CHAT_TENANT_ID
        ):
            raise VercelUiProtocolError(
                409,
                "AI_CHAT_CONVERSATION_UNOWNED",
                "该历史不属于全局对话 profile，不能继续运行。",
            )

    def _load_new_messages(self, ui_message: UIMessage) -> list[ModelMessage]:
        """官方 load_messages 转换后由服务端重新标记，忽略客户端 metadata。"""

        try:
            loaded = VercelAIAdapter.load_messages([ui_message])
        except Exception:
            raise VercelUiProtocolError(
                400,
                "AI_CHAT_MESSAGE_INVALID",
                "用户消息无法转换为 Pydantic 消息。",
            ) from None
        for message in loaded:
            message.metadata = None
            message.timestamp = None
        return loaded


__all__ = [
    "MAX_USER_TEXT_CHARS",
    "VERCEL_SDK_VERSION",
    "VercelAiChatRun",
    "VercelAiUiService",
    "VercelUiProtocolError",
    "new_event_stream",
]
