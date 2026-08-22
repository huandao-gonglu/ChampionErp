"""后台 continuation：任务终结后用官方 DeferredToolResults 恢复主对话。

职责严格限定为：
1. 找到「link 已 ready、Task 已终结且 link 未解决」的记录；
2. 原子领取 link（continuation claim，独立于 Task execution lease）；
3. 校验同一 conversation 的 Pydantic history：存在与
   ``(conversation_id, tool_call_id)`` 匹配且尚未闭合的 ``global_task_start``
   调用，history version 与 link 冻结版本一致；
4. 从 Task 规范 Result 构造大小受限、无敏感字段的类型化结果，组装官方
   ``DeferredToolResults``；
5. 通过 ``GlobalAgentChatService.open_continuation_run`` 启动同一 conversation、
   新 run_id、无新用户 prompt 的后续 run；
6. 独立完整消费 native events（官方 transform/encode 收集 chunk），不依赖订阅者；
7. 以 link 冻结的 history version 做 CAS，在同一事务保存官方完整 history、
   记录 continuation_run_id、置 ``link_status='resolved'`` 并写 outbox；
8. 提交成功后才向事件总线广播官方编码批次。

CAS 失败时重新读取对账：link 已 resolved 且 history 已提交则不再调用模型。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any, Mapping

from pydantic_ai.messages import (
    BaseToolCallPart,
    BaseToolReturnPart,
    ModelResponse,
)
from pydantic_ai.tools import DeferredToolResults

from erp_web.schemas.global_tasks import (
    TERMINAL_GLOBAL_TASK_STATUSES,
)
from erp_web.services.ai_agent_factory import AiAgentExecutionError
from erp_web.services.global_agent_chat_service import GlobalAgentChatService
from erp_web.stores.global_task_store import LocalGlobalTaskStore
from erp_web.stores.pydantic_ai_event_outbox_store import (
    PydanticAiEventOutboxStore,
)
from erp_web.stores.pydantic_deferred_task_link_store import (
    CONTINUATION_OUTBOX_KIND,
    MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS,
    DeferredTaskLink,
    PydanticDeferredLinkError,
    PydanticDeferredTaskLinkStore,
)
from erp_web.stores.pydantic_message_store import PydanticMessageStore


_logger = logging.getLogger(__name__)

# 单个 continuation 结果载荷的序列化上限；超限直接截断步骤详情。
MAX_CONTINUATION_RESULT_BYTES = 8 * 1024


class GlobalTaskContinuationService:
    """任务终结后恢复主对话的唯一 continuation owner。"""

    def __init__(
        self,
        *,
        chat_service: GlobalAgentChatService,
        task_store: LocalGlobalTaskStore,
        deferred_links: PydanticDeferredTaskLinkStore,
        message_store: PydanticMessageStore,
        event_outbox: PydanticAiEventOutboxStore,
        event_bus: Any,
        run_registry: Any,
    ) -> None:
        self.chat_service = chat_service
        self.task_store = task_store
        self.deferred_links = deferred_links
        self.message_store = message_store
        self.event_outbox = event_outbox
        self.event_bus = event_bus
        self.run_registry = run_registry

    # -- 对外入口（同步，供 recovery worker 调用） --------------------------

    def recover_pending(self, *, limit: int = 50) -> int:
        """领取并恢复全部可 continuation 的 link；返回成功提交数量。"""

        resolved = 0
        for link in self.deferred_links.list_continuable(limit=limit):
            try:
                if self._recover_one(link):
                    resolved += 1
            except Exception:
                _logger.exception(
                    "continuation 恢复失败：link=%s task=%s",
                    link.link_id,
                    link.task_id,
                )
                self.deferred_links.set_last_error(
                    link.link_id,
                    error_code="CONTINUATION_RECOVERY_FAILED",
                )
        return resolved

    def sweep_provisional_links(self, *, ttl_seconds: float) -> int:
        """清理超过 TTL 仍未形成首次 history 的 provisional link。

        已存在匹配 Deferred history 的 link 不允许 abandoned：修复为 ready 后
        走标准恢复链路。无法对上的 link 原子 abandoned，并把尚未执行的 Task
        明确取消，从而释放 conversation。返回处理数量。
        """

        handled = 0
        for link in self.deferred_links.list_expired_provisional(
            ttl_seconds=ttl_seconds
        ):
            try:
                history = self.message_store.get(link.conversation_id)
                if history is not None and self._has_open_start_call(
                    history,
                    link,
                ):
                    self.deferred_links.repair_to_ready(
                        link.link_id,
                        history_version=int(history.history_version),
                    )
                    _logger.info(
                        "provisional link 修复为 ready：link=%s",
                        link.link_id,
                    )
                else:
                    self.deferred_links.abandon_expired(
                        link.link_id,
                        cancel_assistant_message=(
                            "首次对话历史未能提交，任务已取消；请重新提交。"
                        ),
                    )
                    _logger.info(
                        "provisional link 已 abandoned 并取消任务：link=%s",
                        link.link_id,
                    )
                handled += 1
            except Exception:
                _logger.exception(
                    "provisional link 清理失败：link=%s",
                    link.link_id,
                )
        return handled

    def _recover_one(self, link: DeferredTaskLink) -> bool:
        # continuation claim 与 run_registry 双重串行化：claim 防重复
        # continuation，run_registry 与普通用户回合互斥。默认租约（600 秒）
        # 覆盖模型运行超时窗口，租约存续期间第二个 worker 不能再次领取。
        claimed = self.deferred_links.claim(link.link_id)
        if claimed is None:
            return False
        claimed_link, lease_id = claimed
        if not self.run_registry.acquire(link.conversation_id):
            self.deferred_links.release_claim(link.link_id, lease_id)
            return False
        committed = False
        try:
            committed = asyncio.run(
                self._run_continuation(claimed_link, lease_id=lease_id)
            )
            return committed
        finally:
            self.run_registry.release(link.conversation_id)
            if not committed:
                # 非终结失败立即释放租约，让下一轮恢复马上重试；成功提交后
                # link 已 resolved，release 自动失效（无副作用）。
                self.deferred_links.release_claim(link.link_id, lease_id)

    # -- 核心异步流程 ---------------------------------------------------------

    async def _run_continuation(
        self,
        link: DeferredTaskLink,
        *,
        lease_id: str,
    ) -> bool:
        task = self.task_store.load_task(link.task_id)
        if task is None:
            _logger.warning(
                "continuation 跳过：任务不存在 link=%s task=%s",
                link.link_id,
                link.task_id,
            )
            return False
        if task.status not in TERMINAL_GLOBAL_TASK_STATUSES:
            # Task 尚未终结（状态在 list 之后被改写）：释放 claim 等待下轮。
            return False

        history = self.message_store.get(link.conversation_id)
        if history is None or not self._history_matches_link(history, link):
            _logger.warning(
                "continuation 跳过：history 与 link 不一致 link=%s",
                link.link_id,
            )
            self.deferred_links.set_last_error(
                link.link_id,
                error_code="CONTINUATION_HISTORY_MISMATCH",
            )
            return False

        result_payload = self._bounded_task_result(task)
        deferred_results = DeferredToolResults(
            calls={link.tool_call_id: result_payload}
        )

        # 延迟导入避免模块级环：协议编码边界只在运行时使用。
        from erp_web.services.vercel_ai_ui_service import new_event_stream

        event_stream = new_event_stream(link.conversation_id)
        # 报告 A-13：outbox 只需要终态段，用固定上限 ring buffer 收集，避免
        # 长最终回复线性占用进程内存。
        encoded_chunks: deque[str] = deque(
            maxlen=MAX_OUTBOX_TERMINAL_SEGMENT_CHUNKS
        )
        try:
            async with self.chat_service.open_continuation_run(
                conversation_id=link.conversation_id,
                deferred_tool_results=deferred_results,
            ) as session:
                native = session.events([])
                transformed = event_stream.transform_stream(native)
                encoded = event_stream.encode_stream(transformed)
                async for chunk in encoded:
                    encoded_chunks.append(chunk)
                if not session.completed or session.result is None:
                    failure = session.failure_error
                    _logger.warning(
                        "continuation run 未完成 link=%s error=%s",
                        link.link_id,
                        failure.code if failure is not None else "unknown",
                    )
                    self.deferred_links.set_last_error(
                        link.link_id,
                        error_code=(
                            failure.code
                            if failure is not None
                            else "CONTINUATION_RUN_INCOMPLETE"
                        ),
                    )
                    return False
                final_messages = list(session.result.all_messages())
                continuation_run_id = session.run_id
        except AiAgentExecutionError as exc:
            _logger.warning(
                "continuation run 失败 link=%s code=%s",
                link.link_id,
                exc.code,
            )
            self.deferred_links.set_last_error(
                link.link_id,
                error_code=exc.code,
            )
            return False

        try:
            committed_version = self.deferred_links.commit_continuation_history(
                link.conversation_id,
                final_messages,
                link_id=link.link_id,
                expected_version=link.history_version,
                continuation_run_id=continuation_run_id,
                lease_id=lease_id,
                encoded_chunks=encoded_chunks,
            )
        except PydanticDeferredLinkError as exc:
            if exc.code != "PYDANTIC_DEFERRED_OUTBOX_TOO_LARGE":
                raise
            # 报告 R-03：终态段超过 outbox 字节上限时确定性降级为 resync-only
            # 批次（空事件列表恒满足上限）。history 仍按 CAS 提交、link 置
            # resolved；订阅端按 history_version 推进游标并重读 /ui-messages。
            # 绝不能把超限释放为可重新调用模型的重试状态，否则稳定超限的最终
            # 回复会造成无限模型重试并持续锁定 conversation。
            _logger.warning(
                "continuation 终态段超过 outbox 上限，降级为 resync-only 批次："
                "link=%s task=%s",
                link.link_id,
                link.task_id,
            )
            committed_version = self.deferred_links.commit_continuation_history(
                link.conversation_id,
                final_messages,
                link_id=link.link_id,
                expected_version=link.history_version,
                continuation_run_id=continuation_run_id,
                lease_id=lease_id,
                encoded_chunks=[],
            )
        if committed_version is None:
            # CAS / 租约冲突：重新读取对账。若已 resolved 则不再调用模型。
            current = self.deferred_links.get(link.link_id)
            if current is not None and current.link_status == "resolved":
                _logger.info(
                    "continuation 已由其他执行者提交 link=%s",
                    link.link_id,
                )
                return True
            _logger.warning(
                "continuation CAS 冲突 link=%s expected_version=%s",
                link.link_id,
                link.history_version,
            )
            self.deferred_links.set_last_error(
                link.link_id,
                error_code="CONTINUATION_CAS_CONFLICT",
            )
            return False

        # 报告 R-05：durable commit 之后的通知是 best-effort 独立阶段。
        # publish/mark_published 异常只留下未发布 outbox（后台 publisher 重投），
        # 不得把已成功 resolved 的 link 误记为 CONTINUATION_RECOVERY_FAILED，
        # 也不得让本轮恢复被当作失败重试。
        try:
            published = self.event_outbox.list_after(
                link.conversation_id,
                after_history_version=committed_version - 1,
            )
            for batch in published:
                if (
                    batch.history_version == committed_version
                    and batch.kind == CONTINUATION_OUTBOX_KIND
                ):
                    self.event_bus.publish(link.conversation_id, batch)
                    # 记录已投递；崩溃窗口中未到达此行的批次由后台 outbox
                    # publisher 重投，订阅端按 history_version 去重。
                    self.event_outbox.mark_published([batch])
        except Exception:
            _logger.exception(
                "continuation 批次通知失败，交由 outbox publisher 重投：link=%s",
                link.link_id,
            )
        return True

    # -- 校验与结果构造 -------------------------------------------------------

    @staticmethod
    def _has_open_start_call(
        history: Any,
        link: DeferredTaskLink,
    ) -> bool:
        """history 中存在与 link 匹配且尚未闭合的 global_task_start 调用。"""

        returned_ids: set[str] = set()
        for message in history.model_messages():
            for part in getattr(message, "parts", None) or ():
                if isinstance(part, BaseToolReturnPart):
                    call_id = str(getattr(part, "tool_call_id", "") or "")
                    if call_id:
                        returned_ids.add(call_id)
        if link.tool_call_id in returned_ids:
            return False
        for message in history.model_messages():
            if not isinstance(message, ModelResponse):
                continue
            for part in message.parts:
                if not isinstance(part, BaseToolCallPart):
                    continue
                if (
                    part.tool_name == "global_task_start"
                    and part.tool_call_id == link.tool_call_id
                ):
                    return True
        return False

    def _history_matches_link(
        self,
        history: Any,
        link: DeferredTaskLink,
    ) -> bool:
        """continuation 前置校验：版本与 link 冻结一致，且调用仍未闭合。"""

        if int(history.history_version) != int(link.history_version):
            return False
        return self._has_open_start_call(history, link)

    @staticmethod
    def _serialized_bytes(payload: Mapping[str, Any]) -> int:
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    @classmethod
    def _bounded_task_result(cls, task: Any) -> dict[str, Any]:
        """从任务规范状态构造大小受限、含成功步骤业务结果的结果载荷。

        报告 A-06：成功步骤的 ``result``（翻译文本、生成标识、发布结果等能力
        输出）必须随 ``DeferredToolResults`` 送达 continuation 模型，否则后台
        任务虽完成，主对话也无法生成包含实际结果的最终回复。载荷不含步骤
        ``arguments`` 等输入侧字段。

        分级收缩并逐级复查字节数，优先保留业务结果：先把最大的步骤结果逐个
        换成截断标记，再移除步骤错误详情，再截断长文本字段，再移除步骤列表；
        最后兜底只保留投递键，确保任何输入下都不超过上限。
        """

        steps: list[dict[str, Any]] = []
        for step in task.steps:
            entry: dict[str, Any] = {
                "capability_name": step.capability_name,
                "status": step.status,
            }
            if step.status == "completed" and step.result is not None:
                entry["result"] = dict(step.result)
            if step.error is not None:
                entry["error_code"] = step.error.code
                entry["error_message"] = step.error.message
            steps.append(entry)
        payload: dict[str, Any] = {
            "task_id": task.task_id,
            "status": task.status,
            "goal": task.goal,
            "assistant_message": task.assistant_message,
            "error_code": task.error_code,
            "error_message": task.error_message,
            "steps": steps,
        }
        if cls._serialized_bytes(payload) <= MAX_CONTINUATION_RESULT_BYTES:
            return payload

        # 第一级：逐个把最大的步骤结果替换为截断标记，优先保留较小的业务结果。
        # 报告 A-06：每个 result 最多收缩一次——替换标记本身也是 dict，若允许
        # 重复选中，任务级长字段超限时循环会原地替换同一标记而永不进入后续
        # 阶段，永久阻塞 recovery worker。
        shrunk_indexes: set[int] = set()
        while True:
            largest_index = None
            largest_bytes = 0
            for index, entry in enumerate(payload["steps"]):
                if index in shrunk_indexes:
                    continue
                result = entry.get("result")
                if not isinstance(result, dict):
                    continue
                result_bytes = len(
                    json.dumps(result, ensure_ascii=False).encode("utf-8")
                )
                if result_bytes > largest_bytes:
                    largest_bytes = result_bytes
                    largest_index = index
            if largest_index is None:
                break
            payload["steps"][largest_index]["result"] = {"truncated": True}
            shrunk_indexes.add(largest_index)
            payload["truncated"] = True
            if cls._serialized_bytes(payload) <= MAX_CONTINUATION_RESULT_BYTES:
                return payload

        # 第二级：移除步骤错误详情。
        for entry in payload["steps"]:
            entry.pop("error_message", None)
        payload["truncated"] = True
        if cls._serialized_bytes(payload) <= MAX_CONTINUATION_RESULT_BYTES:
            return payload

        # 第三级：截断长文本字段。
        for key in ("goal", "assistant_message", "error_message"):
            value = payload.get(key)
            if isinstance(value, str) and len(value) > 200:
                payload[key] = value[:200] + "…"
        if cls._serialized_bytes(payload) <= MAX_CONTINUATION_RESULT_BYTES:
            return payload

        # 第四级：移除步骤列表，只保留任务级字段。
        payload["steps"] = []
        if cls._serialized_bytes(payload) <= MAX_CONTINUATION_RESULT_BYTES:
            return payload

        # 兜底：只保留最小投递键（task_id/status 由 schema 约束长度）。
        return {
            "task_id": task.task_id,
            "status": task.status,
            "truncated": True,
        }


__all__ = ["GlobalTaskContinuationService", "MAX_CONTINUATION_RESULT_BYTES"]
