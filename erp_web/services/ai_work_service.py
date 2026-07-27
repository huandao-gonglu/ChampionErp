"""AI Provider 对话事件 journal：JSONL 事件体 + ai_sessions 元数据表。

``AiWorkJournal`` 挂在 ``AppContext.ai_journal`` 上：构造收 paths + db，
持有 per-conversation 的 Condition 表（会话终态后清理条目）。事件体仍写
JSONL 文件（按天分目录），会话列表 / 定位一律查 ``ai_sessions`` 表（写侧
维护），不再做全目录 glob 扫描。
"""

from __future__ import annotations

import json
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
_TERMINAL_EVENT_STATUS = {"RUN_FINISHED": "completed", "RUN_ERROR": "failed"}


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


def _safe_conversation_id(value: Any) -> str:
    text = str(value or "").strip()
    safe = "".join(char for char in text if char.isalnum() or char in {"_", "-"})
    if not safe or safe != text:
        raise ValueError("无效的 AI 对话 ID。")
    return safe


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


def _summary_from_events(events: list[AiWorkEvent], path: Path) -> AiWorkConversationSummary:
    first = events[0] if events else {}
    last = events[-1] if events else {}
    metadata = first.get("rawEvent") if isinstance(first.get("rawEvent"), dict) else {}
    status = "running"
    error = ""
    if last.get("type") == "RUN_FINISHED":
        status = "completed"
    elif last.get("type") == "RUN_ERROR":
        status = "failed"
        error = str(last.get("message") or "")
    elif events and time.time() - path.stat().st_mtime > 60 * 60:
        status = "interrupted"
    return {
        "conversation_id": str(first.get("conversation_id") or path.stem),
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
    _seq: int = 0
    _assistant_message_id: str = ""
    _assistant_started: bool = False
    _assistant_ended: bool = False
    _condition: threading.Condition = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._condition = self.journal._condition_for(self.conversation_id)

    def emit(self, event_type: str, **payload: Any) -> AiWorkEvent:
        with self._condition:
            self._seq += 1
            now = _now()
            event: AiWorkEvent = {
                "schema_version": AI_WORK_SCHEMA_VERSION,
                "seq": self._seq,
                "timestamp": int(now.timestamp() * 1000),
                "occurred_at": now.isoformat(),
                "type": event_type,
                "threadId": self.conversation_id,
                "runId": self.conversation_id,
                "conversation_id": self.conversation_id,
                **_json_safe(payload),
            }
            self.journal._record_event(self, event)
            self._condition.notify_all()
        if event_type in _TERMINAL_EVENT_STATUS:
            # 终态后长轮询都会立即命中文件事件，Condition 条目不再需要。
            self.journal._release_condition(self.conversation_id)
        return event

    def emit_custom(self, name: str, value: Any) -> AiWorkEvent:
        return self.emit("CUSTOM", name=name, value=value)

    def start_assistant_message(self) -> str:
        if not self._assistant_message_id:
            self._assistant_message_id = f"msg_{uuid4().hex[:12]}"
        if not self._assistant_started:
            self.emit("TEXT_MESSAGE_START", messageId=self._assistant_message_id, role="assistant")
            self._assistant_started = True
        return self._assistant_message_id

    def emit_text_delta(self, delta: str) -> None:
        text = str(delta or "")
        if not text:
            return
        message_id = self.start_assistant_message()
        self.emit("TEXT_MESSAGE_CONTENT", messageId=message_id, delta=text)

    def finish_assistant_message(self, raw_text: str = "") -> None:
        if raw_text and not self._assistant_started:
            self.emit_text_delta(raw_text)
        if self._assistant_started and not self._assistant_ended:
            self.emit("TEXT_MESSAGE_END", messageId=self._assistant_message_id)
            self._assistant_ended = True
        if raw_text:
            self.emit_custom("provider.response", {"text": raw_text})

    def finish(self, result: Any) -> None:
        self.emit("RUN_FINISHED", result=result)

    def fail(self, error: Exception) -> None:
        self.emit("RUN_ERROR", message=str(error), code=error.__class__.__name__)


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
            return self._conditions.setdefault(conversation_id, threading.Condition(threading.Lock()))

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
    ) -> AiWorkConversation:
        now = _now()
        conversation_id = f"aic_{now.strftime('%Y%m%d_%H%M%S_%f')}_{uuid4().hex[:8]}"
        day = now.strftime("%Y-%m-%d")
        path = self._root / day / f"{conversation_id}.jsonl"
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
        }
        conversation = AiWorkConversation(self, conversation_id, day, path, metadata)
        conversation.emit(
            "RUN_STARTED",
            input=_json_safe(input_payload),
            rawEvent=metadata,
        )
        return conversation

    def _record_event(self, conversation: AiWorkConversation, event: AiWorkEvent) -> None:
        """Append one event to the JSONL file and maintain the ai_sessions row."""
        conversation.path.parent.mkdir(parents=True, exist_ok=True)
        with conversation.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
            output.write("\n")
        self._db.upsert_ai_session(
            conversation.conversation_id,
            day=conversation.day,
            status=_TERMINAL_EVENT_STATUS.get(str(event.get("type") or ""), "running"),
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

    def list_conversations(self, *, limit: int = 50) -> list[AiWorkConversationSummary]:
        safe_limit = min(max(int(limit), 1), MAX_CONVERSATION_LIST_LIMIT)
        summaries: list[AiWorkConversationSummary] = []
        for record in self._db.list_ai_sessions(limit=safe_limit):
            path = self._root / str(record.get("day") or "") / f"{record['session_id']}.jsonl"
            if not path.exists():
                continue
            summaries.append(_summary_from_events(_read_events_from_path(path), path))
        return summaries


__all__ = [
    "AI_WORK_RELATIVE_DIR",
    "AiWorkConversation",
    "AiWorkJournal",
]
