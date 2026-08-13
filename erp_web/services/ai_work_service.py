"""AI Provider 对话事件 journal：JSONL 事件体 + ai_sessions 元数据表。

``AiWorkJournal`` 挂在 ``AppContext.ai_journal`` 上：构造收 paths + db，
持有 per-conversation 的 Condition 表（会话终态后清理条目）。事件体仍写
JSONL 文件（按天分目录），会话列表 / 定位一律查 ``ai_sessions`` 表（写侧
维护），不再做全目录 glob 扫描。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from erp_web.context import AppPaths
from erp_web.db import ErpDatabase
from erp_web.schemas.ai_work import AiWorkConversationSummary, AiWorkEvent


AI_WORK_RELATIVE_DIR = Path("data") / "logs" / "ai_work"
AI_WORK_SCHEMA_VERSION = 1
MAX_CONVERSATION_LIST_LIMIT = 200
MAX_WAIT_MILLISECONDS = 25_000
MAX_GLOBAL_AGENT_PROJECTION_EVENTS = 500
_EVENT_STATUS = {
    "RUN_FINISHED": "completed",
    "RUN_ERROR": "failed",
    "RUN_DEFERRED": "waiting_approval",
    "RUN_RESUMED": "running",
}
_RELEASE_CONDITION_EVENTS = frozenset({"RUN_FINISHED", "RUN_ERROR", "RUN_DEFERRED"})
GLOBAL_AGENT_CHAT_USE_CASE_ID = "global.agent.chat"
GLOBAL_AGENT_PROJECTION_EVENTS = frozenset(
    {
        "global.user_message",
        "global.assistant_message",
        "global.task_state",
        "global.agent_execution_link",
    }
)
_BUSINESS_SUMMARY_KEYS = frozenset(
    {
        "use_case_id",
        "result_version",
        "platform",
        "site",
        "language",
        "locale",
        "status",
        "count",
    }
)


def _now() -> datetime:
    return datetime.now().astimezone()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _business_input_summary(value: Any) -> Any:
    """只保留稳定业务引用与计数，不复制 prompt、消息或商品正文。"""

    if not isinstance(value, dict):
        if isinstance(value, (list, tuple, set)):
            return {"item_count": len(value)}
        return None
    summary: dict[str, Any] = {}
    for key, item in value.items():
        normalized_key = str(key or "").strip()
        if normalized_key in _BUSINESS_SUMMARY_KEYS or normalized_key.endswith("_id"):
            if item is None or isinstance(item, (str, int, float, bool)):
                summary[normalized_key] = _json_safe(item)
        elif isinstance(item, (list, tuple, set)):
            summary[f"{normalized_key}_count"] = len(item)
    return summary


def _provider_event_summary(value: Any) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    provider_payload = (
        payload.get("provider_payload")
        if isinstance(payload.get("provider_payload"), dict)
        else {}
    )
    messages = payload.get("messages")
    images = payload.get("images")
    return {
        "method": str(payload.get("method") or ""),
        "model": str(provider_payload.get("model") or payload.get("model") or ""),
        "stream": bool(provider_payload.get("stream") or payload.get("stream")),
        "message_count": len(messages) if isinstance(messages, list) else 0,
        "image_count": len(images) if isinstance(images, list) else 0,
        "character_count": int(payload.get("character_count") or 0),
    }


def _safe_conversation_id(value: Any) -> str:
    text = str(value or "").strip()
    safe = "".join(char for char in text if char.isalnum() or char in {"_", "-"})
    if not safe or safe != text:
        raise ValueError("无效的 AI 对话 ID。")
    return safe


def _ensure_private_journal_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


def _append_private_journal_line(path: Path, line: str) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT,
        0o600,
    )
    if os.name != "nt":
        try:
            os.fchmod(descriptor, 0o600)
        except BaseException:
            os.close(descriptor)
            raise
    with os.fdopen(descriptor, "a", encoding="utf-8") as output:
        output.write(line)
        output.write("\n")


def _replace_private_journal_events(
    path: Path,
    events: list[AiWorkEvent],
) -> None:
    """以原子替换压缩稳定对话；序号保持不变，DB last_seq 继续单调递增。"""

    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            for event in events:
                output.write(
                    json.dumps(
                        event,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
                output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _read_events_from_path(path: Path, after_seq: int = 0) -> list[AiWorkEvent]:
    if not path.exists() or not path.is_file():
        return []
    events: list[AiWorkEvent] = []
    with path.open("r", encoding="utf-8") as source:
        for raw_line in source:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if int(event.get("seq") or 0) > after_seq:
                events.append(event)
    return events


def _read_first_event_from_path(path: Path) -> AiWorkEvent | None:
    if not path.exists() or not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as source:
        for raw_line in source:
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                return event
    return None


def _summary_from_events(
    events: list[AiWorkEvent],
    path: Path,
    *,
    parent_conversation_id: str | None = None,
) -> AiWorkConversationSummary:
    first = events[0] if events else {}
    last = events[-1] if events else {}
    metadata = first.get("rawEvent") if isinstance(first.get("rawEvent"), dict) else {}
    status = "running"
    error = ""
    latest_task_status: str | None = None
    if last.get("type") == "RUN_FINISHED":
        status = "completed"
    elif last.get("type") == "RUN_ERROR":
        status = "failed"
        error = str(last.get("message") or "")
    elif last.get("type") == "RUN_DEFERRED":
        status = "waiting_approval"
    elif (
        events
        and metadata.get("use_case_id") != GLOBAL_AGENT_CHAT_USE_CASE_ID
        and time.time() - path.stat().st_mtime > 60 * 60
    ):
        status = "interrupted"
    if metadata.get("use_case_id") == GLOBAL_AGENT_CHAT_USE_CASE_ID:
        for event in reversed(events):
            if (
                event.get("type") != "CUSTOM"
                or event.get("name") != "global.task_state"
            ):
                continue
            value = event.get("value")
            raw_status = (
                str(value.get("status") or "").strip()
                if isinstance(value, dict)
                else ""
            )
            latest_task_status = raw_status or None
            break
    return {
        "conversation_id": str(first.get("conversation_id") or path.stem),
        "parent_conversation_id": parent_conversation_id,
        "use_case_id": str(metadata.get("use_case_id") or ""),
        "capability": str(metadata.get("capability") or ""),
        "provider_id": str(metadata.get("provider_id") or ""),
        "provider": str(metadata.get("provider") or ""),
        "model_id": str(metadata.get("model_id") or ""),
        "model": str(metadata.get("model") or ""),
        "stream": bool(metadata.get("stream")),
        "required_capabilities": list(metadata.get("required_capabilities") or []),
        "timeout_seconds": metadata.get("timeout_seconds"),
        "status": status,
        "latest_task_status": latest_task_status,
        "created_at": str(first.get("occurred_at") or ""),
        "updated_at": str(last.get("occurred_at") or first.get("occurred_at") or ""),
        "last_seq": int(last.get("seq") or 0),
        "event_count": len(events),
        "error": error,
    }


@dataclass
class AiWorkConversation:
    journal: "AiWorkJournal"
    conversation_id: str
    day: str
    path: Path
    metadata: dict[str, Any]
    parent_conversation_id: str | None = None
    _seq: int = 0
    _reasoning_message_id: str = ""
    _reasoning_started: bool = False
    _reasoning_ended: bool = False
    _assistant_message_id: str = ""
    _assistant_started: bool = False
    _assistant_ended: bool = False
    _condition: threading.Condition = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._condition = self.journal._condition_for(self.conversation_id)

    def emit(self, event_type: str, **payload: Any) -> AiWorkEvent:
        with self._condition:
            if self.metadata.get("use_case_id") == GLOBAL_AGENT_CHAT_USE_CASE_ID:
                session_record = self.journal._db.get_ai_session(
                    self.conversation_id
                )
                if session_record:
                    self._seq = max(
                        self._seq,
                        int(session_record.get("last_seq") or 0),
                    )
            self._seq += 1
            now = _now()
            trace_payload = {
                key: self.metadata[key]
                for key in (
                    "task_run_id",
                    "attempt_id",
                    "workflow_run_id",
                    "parent_task_run_id",
                )
                if key in self.metadata
            }
            event: AiWorkEvent = {
                "schema_version": AI_WORK_SCHEMA_VERSION,
                "seq": self._seq,
                "timestamp": int(now.timestamp() * 1000),
                "occurred_at": now.isoformat(),
                "type": event_type,
                "threadId": self.conversation_id,
                "runId": self.conversation_id,
                "conversation_id": self.conversation_id,
                **trace_payload,
                **_json_safe(payload),
            }
            self.journal._record_event(self, event)
            self._condition.notify_all()
        if event_type in _RELEASE_CONDITION_EVENTS:
            # 终态后长轮询都会立即命中文件事件，Condition 条目不再需要。
            self.journal._release_condition(self.conversation_id)
        return event

    def emit_custom(self, name: str, value: Any) -> AiWorkEvent:
        if name in {"provider.request", "provider.response"}:
            value = _provider_event_summary(value)
        return self.emit("CUSTOM", name=name, value=value)

    def start_assistant_message(self) -> str:
        if not self._assistant_message_id:
            self._assistant_message_id = f"msg_{uuid4().hex[:12]}"
        if not self._assistant_started:
            self.emit("TEXT_MESSAGE_START", messageId=self._assistant_message_id, role="assistant")
            self._assistant_started = True
        return self._assistant_message_id

    def start_reasoning_message(self) -> str:
        if self._reasoning_ended:
            self._reasoning_message_id = ""
            self._reasoning_started = False
            self._reasoning_ended = False
        if not self._reasoning_message_id:
            self._reasoning_message_id = f"reasoning_{uuid4().hex[:12]}"
        if not self._reasoning_started:
            self.emit(
                "REASONING_MESSAGE_START",
                messageId=self._reasoning_message_id,
                role="assistant",
            )
            self._reasoning_started = True
        return self._reasoning_message_id

    def emit_reasoning_delta(self, delta: str) -> None:
        text = str(delta or "")
        if not text or self._reasoning_ended:
            return
        message_id = self.start_reasoning_message()
        self.emit(
            "REASONING_MESSAGE_CONTENT",
            messageId=message_id,
            delta=text,
        )

    def finish_reasoning_message(self) -> None:
        if self._reasoning_started and not self._reasoning_ended:
            self.emit(
                "REASONING_MESSAGE_END",
                messageId=self._reasoning_message_id,
            )
            self._reasoning_ended = True

    def emit_text_delta(self, delta: str) -> None:
        text = str(delta or "")
        if not text:
            return
        self.finish_reasoning_message()
        message_id = self.start_assistant_message()
        self.emit("TEXT_MESSAGE_CONTENT", messageId=message_id, delta=text)

    def finish_assistant_message(self, raw_text: str = "") -> None:
        self.finish_reasoning_message()
        if raw_text and not self._assistant_started:
            self.emit_text_delta(raw_text)
        if self._assistant_started and not self._assistant_ended:
            self.emit("TEXT_MESSAGE_END", messageId=self._assistant_message_id)
            self._assistant_ended = True
        if raw_text:
            self.emit_custom("provider.response", {"character_count": len(raw_text)})

    def finish(self, result: Any) -> None:
        self.emit("RUN_FINISHED", result=result)

    def fail(self, error: Exception) -> None:
        payload = {
            key: value
            for key in ("trace_id", "run_id", "task_run_id")
            if (value := str(getattr(error, key, "") or ""))
        }
        self.emit(
            "RUN_ERROR",
            message=str(error),
            code=getattr(error, "code", error.__class__.__name__),
            **payload,
        )


class AiWorkJournal:
    """AI 会话 journal：append/read/wait + 表驱动的会话列表与定位。"""

    def __init__(self, paths: AppPaths, db: ErpDatabase) -> None:
        self._root = Path(paths.app_dir) / AI_WORK_RELATIVE_DIR
        self._db = db
        self._guard = threading.Lock()
        self._conditions: dict[str, threading.Condition] = {}

    @property
    def root(self) -> Path:
        return self._root

    # -- condition table -------------------------------------------------------

    def _condition_for(self, conversation_id: str) -> threading.Condition:
        with self._guard:
            return self._conditions.setdefault(
                conversation_id,
                threading.Condition(threading.RLock()),
            )

    def _release_condition(self, conversation_id: str) -> None:
        with self._guard:
            self._conditions.pop(conversation_id, None)

    # -- append ------------------------------------------------------------------

    def start_conversation(
        self,
        *,
        use_case_id: str,
        capability: str,
        provider_id: str,
        model: dict[str, Any],
        stream: bool = False,
        required_capabilities: list[str] | tuple[str, ...] | None = None,
        timeout_seconds: int | None = None,
        input_payload: Any = None,
        trace_context: dict[str, Any] | None = None,
        parent_conversation_id: str | None = None,
    ) -> AiWorkConversation:
        now = _now()
        conversation_id = f"aic_{now.strftime('%Y%m%d_%H%M%S_%f')}_{uuid4().hex[:8]}"
        day = now.strftime("%Y-%m-%d")
        path = self._root / day / f"{conversation_id}.jsonl"
        safe_trace_context = {
            key: value
            for key, value in dict(trace_context or {}).items()
            if key
            in {
                "task_run_id",
                "attempt_id",
                "workflow_run_id",
                "parent_task_run_id",
                "actor_id",
                "tenant_id",
                "deadline_at",
                "budget_profile",
                "trace_id",
            }
        }
        metadata = {
            "use_case_id": str(use_case_id or ""),
            "capability": str(capability or ""),
            "provider_id": str(provider_id or ""),
            "provider": str(model.get("provider") or ""),
            "model_id": str(model.get("id") or ""),
            "model": str(model.get("model") or model.get("name") or ""),
            "stream": bool(stream),
            "required_capabilities": list(required_capabilities or []),
            "timeout_seconds": timeout_seconds,
            **safe_trace_context,
        }
        parent_id: str | None = None
        if parent_conversation_id:
            parent_id = _safe_conversation_id(parent_conversation_id)
            parent = self._db.get_ai_session(parent_id)
            if not parent:
                raise ValueError("AI 父对话不存在，无法创建子对话。")
            if parent.get("parent_session_id") is not None:
                raise ValueError("AI 父对话必须是根对话。")
        conversation = AiWorkConversation(
            self,
            conversation_id,
            day,
            path,
            metadata,
            parent_id,
        )
        conversation.emit(
            "RUN_STARTED",
            input=_business_input_summary(input_payload),
            rawEvent=metadata,
            **safe_trace_context,
        )
        return conversation

    def resume_conversation(
        self,
        conversation_id: str,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> AiWorkConversation:
        """从已持久化事件恢复 recorder；不复制或重建 Provider 状态。"""

        safe_id = _safe_conversation_id(conversation_id)
        record = self._db.get_ai_session(safe_id)
        if not record:
            raise ValueError("AI 对话不存在，无法恢复。")
        path = self.find_conversation_path(safe_id)
        if path is None:
            raise ValueError("AI 对话不存在，无法恢复。")
        events = _read_events_from_path(path)
        if not events:
            raise ValueError("AI 对话没有可恢复的事件。")
        first = events[0]
        metadata = (
            dict(first.get("rawEvent"))
            if isinstance(first.get("rawEvent"), dict)
            else {}
        )
        safe_trace_context = {
            key: value
            for key, value in dict(trace_context or {}).items()
            if key
            in {
                "task_run_id",
                "attempt_id",
                "workflow_run_id",
                "parent_task_run_id",
                "actor_id",
                "tenant_id",
                "deadline_at",
                "budget_profile",
                "trace_id",
                "run_id",
            }
        }
        metadata.update(safe_trace_context)
        conversation = AiWorkConversation(
            self,
            safe_id,
            path.parent.name,
            path,
            metadata,
            (
                str(record.get("parent_session_id"))
                if record.get("parent_session_id") is not None
                else None
            ),
            _seq=max(int(event.get("seq") or 0) for event in events),
        )
        conversation.emit("RUN_RESUMED", **safe_trace_context)
        return conversation

    def _open_conversation_for_projection(
        self,
        conversation_id: str,
    ) -> AiWorkConversation:
        """打开稳定业务投影会话，不伪造一次 Provider/Agent resume。"""

        safe_id = _safe_conversation_id(conversation_id)
        record = self._db.get_ai_session(safe_id)
        if not record:
            raise ValueError("AI 对话不存在，无法写入业务投影。")
        path = self._root / str(record.get("day") or "") / f"{safe_id}.jsonl"
        first = _read_first_event_from_path(path)
        if first is None:
            raise ValueError("AI 对话没有可追加的事件。")
        metadata = (
            dict(first.get("rawEvent"))
            if isinstance(first.get("rawEvent"), dict)
            else {}
        )
        if metadata.get("use_case_id") != GLOBAL_AGENT_CHAT_USE_CASE_ID:
            raise ValueError("只有全局 Agent 对话允许追加任务投影。")
        return AiWorkConversation(
            self,
            safe_id,
            path.parent.name,
            path,
            metadata,
            (
                str(record.get("parent_session_id"))
                if record.get("parent_session_id") is not None
                else None
            ),
            _seq=int(record.get("last_seq") or 0),
        )

    def start_global_agent_conversation(self) -> str:
        """创建 /aiWork 中独立于各次 Agent run 的稳定业务对话。"""

        conversation = self.start_conversation(
            use_case_id=GLOBAL_AGENT_CHAT_USE_CASE_ID,
            capability="global_agent_chat",
            provider_id="",
            model={},
            required_capabilities=[],
            input_payload={"task_kind": GLOBAL_AGENT_CHAT_USE_CASE_ID},
        )
        return conversation.conversation_id

    def require_global_agent_conversation(self, conversation_id: str) -> None:
        """验证客户端要复用的是稳定全局对话而非普通 Agent 执行记录。"""

        self._open_conversation_for_projection(conversation_id)

    def project_global_agent_event(
        self,
        conversation_id: str,
        name: str,
        value: dict[str, Any],
    ) -> AiWorkEvent:
        """追加有界展示事件；失败不得反向改变任务业务状态。"""

        normalized_name = str(name or "").strip()
        if normalized_name not in GLOBAL_AGENT_PROJECTION_EVENTS:
            raise ValueError("未知的全局 Agent 投影事件。")
        payload = value if isinstance(value, dict) else {}
        task_id = str(payload.get("task_id") or "").strip()[:160]
        if not task_id:
            raise ValueError("全局 Agent 投影缺少 task_id。")
        bounded: dict[str, Any] = {"task_id": task_id}
        if normalized_name in {
            "global.user_message",
            "global.assistant_message",
        }:
            message = str(payload.get("message") or "").strip()[:4000]
            if not message:
                raise ValueError("全局 Agent 消息投影缺少 message。")
            bounded["message"] = message
        elif normalized_name == "global.task_state":
            bounded["status"] = str(payload.get("status") or "").strip()[:80]
            summary = str(payload.get("summary") or "").strip()[:2000]
            if summary:
                bounded["summary"] = summary
        else:
            raw_execution_id = str(
                payload.get("conversation_id") or ""
            ).strip()
            if not raw_execution_id:
                raise ValueError("Agent 执行链接缺少 conversation_id。")
            execution_id = _safe_conversation_id(raw_execution_id)
            bounded["conversation_id"] = execution_id
        conversation = self._open_conversation_for_projection(conversation_id)
        if normalized_name == "global.agent_execution_link":
            self._db.bind_ai_session_parent(
                bounded["conversation_id"],
                conversation.conversation_id,
            )
            with conversation._condition:
                for existing in reversed(
                    _read_events_from_path(conversation.path)
                ):
                    if (
                        existing.get("type") == "CUSTOM"
                        and existing.get("name") == normalized_name
                        and existing.get("value") == bounded
                    ):
                        return existing
                event = conversation.emit_custom(normalized_name, bounded)
        else:
            event = conversation.emit_custom(normalized_name, bounded)
        self._compact_global_agent_projection(conversation)
        return event

    def _compact_global_agent_projection(
        self,
        conversation: AiWorkConversation,
    ) -> None:
        """保留会话元数据首事件和最近一段展示投影，防止长期对话无界增长。"""

        condition = self._condition_for(conversation.conversation_id)
        with condition:
            events = _read_events_from_path(conversation.path)
            maximum = MAX_GLOBAL_AGENT_PROJECTION_EVENTS + 1
            if len(events) <= maximum:
                return
            first = events[0]
            retained = [first, *events[-MAX_GLOBAL_AGENT_PROJECTION_EVENTS:]]
            _replace_private_journal_events(conversation.path, retained)

    def _record_event(self, conversation: AiWorkConversation, event: AiWorkEvent) -> None:
        """Append one event to the JSONL file and maintain the ai_sessions row."""
        _ensure_private_journal_directory(self._root)
        _ensure_private_journal_directory(
            conversation.path.parent
        )
        _append_private_journal_line(
            conversation.path,
            json.dumps(
                event,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        self._db.upsert_ai_session(
            conversation.conversation_id,
            parent_session_id=conversation.parent_conversation_id,
            day=conversation.day,
            status=_EVENT_STATUS.get(str(event.get("type") or ""), "running"),
            last_seq=int(event.get("seq") or 0),
            updated_at=str(event.get("occurred_at") or ""),
        )

    # -- read / wait ----------------------------------------------------------------

    def find_conversation_path(self, conversation_id: str) -> Path | None:
        safe_id = _safe_conversation_id(conversation_id)
        record = self._db.get_ai_session(safe_id)
        if not record:
            return None
        path = self._root / str(record.get("day") or "") / f"{safe_id}.jsonl"
        return path if path.exists() else None

    def read_events(self, conversation_id: str, *, after_seq: int = 0) -> list[AiWorkEvent]:
        path = self.find_conversation_path(conversation_id)
        return _read_events_from_path(path, after_seq) if path else []

    def wait_for_events(self, conversation_id: str, *, after_seq: int = 0, wait_ms: int = 0) -> list[AiWorkEvent]:
        events = self.read_events(conversation_id, after_seq=after_seq)
        if events or wait_ms <= 0:
            return events
        timeout = min(max(int(wait_ms), 0), MAX_WAIT_MILLISECONDS) / 1000
        condition = self._condition_for(_safe_conversation_id(conversation_id))
        deadline = time.monotonic() + timeout
        with condition:
            # 写入可能发生在首次读取和拿到 condition 之间；等待前必须复查，
            # 否则会错过那次 notify，平白等满一个长轮询周期。
            events = self.read_events(conversation_id, after_seq=after_seq)
            while not events:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                condition.wait(remaining)
                events = self.read_events(conversation_id, after_seq=after_seq)
        return events

    def get_conversation_summary(
        self,
        conversation_id: str,
    ) -> AiWorkConversationSummary | None:
        safe_id = _safe_conversation_id(conversation_id)
        record = self._db.get_ai_session(safe_id)
        if not record:
            return None
        path = (
            self._root
            / str(record.get("day") or "")
            / f"{safe_id}.jsonl"
        )
        if not path.exists():
            return None
        return _summary_from_events(
            _read_events_from_path(path),
            path,
            parent_conversation_id=record.get("parent_session_id"),
        )

    def _summaries_from_records(
        self,
        records: list[dict[str, Any]],
    ) -> list[AiWorkConversationSummary]:
        summaries: list[AiWorkConversationSummary] = []
        for record in records:
            path = (
                self._root
                / str(record.get("day") or "")
                / f"{record['session_id']}.jsonl"
            )
            if not path.exists():
                continue
            summaries.append(
                _summary_from_events(
                    _read_events_from_path(path),
                    path,
                    parent_conversation_id=record.get(
                        "parent_session_id"
                    ),
                )
            )
        return summaries

    def list_conversations(
        self,
        *,
        limit: int = 50,
        include_children: bool = False,
    ) -> list[AiWorkConversationSummary]:
        safe_limit = min(max(int(limit), 1), MAX_CONVERSATION_LIST_LIMIT)
        return self._summaries_from_records(
            self._db.list_ai_sessions(
                limit=safe_limit,
                include_children=include_children,
            )
        )

    def list_child_conversations(
        self,
        parent_conversation_id: str,
        *,
        limit: int = 50,
    ) -> list[AiWorkConversationSummary]:
        parent_id = _safe_conversation_id(parent_conversation_id)
        parent = self._db.get_ai_session(parent_id)
        if not parent:
            raise ValueError("AI 父对话不存在。")
        if parent.get("parent_session_id") is not None:
            raise ValueError("只有根对话可以查询直接子对话。")
        safe_limit = min(max(int(limit), 1), MAX_CONVERSATION_LIST_LIMIT)
        return self._summaries_from_records(
            self._db.list_ai_session_children(
                parent_id,
                limit=safe_limit,
            )
        )


__all__ = [
    "AI_WORK_RELATIVE_DIR",
    "GLOBAL_AGENT_CHAT_USE_CASE_ID",
    "GLOBAL_AGENT_PROJECTION_EVENTS",
    "MAX_GLOBAL_AGENT_PROJECTION_EVENTS",
    "AiWorkConversation",
    "AiWorkJournal",
]
