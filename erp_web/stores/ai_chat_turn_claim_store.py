"""AI 对话轮次领取（turn claim）的持久化边界。

claim 只保存运行控制元数据与安全诊断标识（conversation/client message ID、
profile、actor、tenant、状态、错误码、trace ID、最后工具名和时间），不保存
prompt、response、UI part、工具参数/结果或业务任务状态，因此不是第二份消息事实源。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from erp_web.db import ErpDatabase


class AiChatTurnClaimError(RuntimeError):
    """对话轮次领取无法按服务端契约读写。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code or "AI_CHAT_TURN_CLAIM_ERROR")
        super().__init__(message)


class AiChatTurnAlreadyAcceptedError(AiChatTurnClaimError):
    """同一 (conversation_id, client_message_id) 已经被领取过。"""

    def __init__(self, message: str) -> None:
        super().__init__("AI_CHAT_TURN_ALREADY_ACCEPTED", message)


CLAIMED = "claimed"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
TERMINAL_CLAIM_STATUSES = frozenset({COMPLETED, FAILED, CANCELLED})


@dataclass(frozen=True)
class AiChatTurnClaim:
    """一次 Agent run 的归属与终态记录；不含任何消息正文。"""

    claim_id: str
    conversation_id: str
    client_message_id: str
    profile_id: str
    actor_id: str
    tenant_id: str
    status: str
    claimed_at: str
    finished_at: str
    error_code: str
    trace_id: str
    last_tool_name: str


def _required_value(name: str, value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise AiChatTurnClaimError(
            "AI_CHAT_TURN_CLAIM_INVALID",
            f"AI chat turn claim 缺少 {name}。",
        )
    return normalized


def _claim_from_row(row: dict[str, Any]) -> AiChatTurnClaim:
    try:
        return AiChatTurnClaim(
            claim_id=str(row["claim_id"] or ""),
            conversation_id=str(row["conversation_id"] or ""),
            client_message_id=str(row["client_message_id"] or ""),
            profile_id=str(row["profile_id"] or ""),
            actor_id=str(row["actor_id"] or ""),
            tenant_id=str(row["tenant_id"] or ""),
            status=str(row["status"] or ""),
            claimed_at=str(row["claimed_at"] or ""),
            finished_at=str(row["finished_at"] or ""),
            error_code=str(row["error_code"] or ""),
            trace_id=str(row["trace_id"] or ""),
            last_tool_name=str(row["last_tool_name"] or ""),
        )
    except Exception:
        raise AiChatTurnClaimError(
            "AI_CHAT_TURN_CLAIM_CORRUPT",
            "已保存的 AI chat turn claim 损坏或格式无效。",
        ) from None


class AiChatTurnClaimStore:
    """原子领取对话轮次并记录终态；不参与消息渲染或 Agent history。"""

    def __init__(self, db: ErpDatabase) -> None:
        self.db = db

    def claim_turn(
        self,
        *,
        conversation_id: str,
        client_message_id: str,
        profile_id: str,
        actor_id: str = "local-user",
        tenant_id: str = "local",
    ) -> AiChatTurnClaim:
        """在 Agent 启动前持久化领取；重复领取抛 AlreadyAccepted。"""

        row = {
            "conversation_id": _required_value(
                "conversation_id", conversation_id
            ),
            "client_message_id": _required_value(
                "client_message_id", client_message_id
            ),
            "profile_id": _required_value("profile_id", profile_id),
        }
        claim_id = f"claim_{uuid4().hex}"
        try:
            inserted = self.db.insert_ai_chat_turn_claim(
                claim_id=claim_id,
                conversation_id=row["conversation_id"],
                client_message_id=row["client_message_id"],
                profile_id=row["profile_id"],
                actor_id=str(actor_id or "").strip() or "local-user",
                tenant_id=str(tenant_id or "").strip() or "local",
                now=datetime.now(timezone.utc).isoformat(),
            )
        except sqlite3.IntegrityError:
            raise AiChatTurnAlreadyAcceptedError(
                "本轮对话已经被服务端接受，请重新读取消息历史。"
            ) from None
        except ValueError as exc:
            raise AiChatTurnClaimError(
                "AI_CHAT_TURN_CLAIM_INVALID",
                str(exc),
            ) from None
        return _claim_from_row(inserted)

    def finish_turn(
        self,
        claim_id: str,
        *,
        status: str,
        error_code: str = "",
        trace_id: str = "",
        last_tool_name: str = "",
    ) -> AiChatTurnClaim | None:
        """把 claimed 领取推进到 completed/failed/cancelled 终态。"""

        normalized_status = str(status or "").strip()
        if normalized_status not in TERMINAL_CLAIM_STATUSES:
            raise AiChatTurnClaimError(
                "AI_CHAT_TURN_CLAIM_STATUS_INVALID",
                "AI chat turn claim 终态只能是 completed、failed 或 cancelled。",
            )
        updated = self.db.update_ai_chat_turn_claim_status(
            _required_value("claim_id", claim_id),
            status=normalized_status,
            now=datetime.now(timezone.utc).isoformat(),
            error_code=str(error_code or "").strip(),
            trace_id=str(trace_id or "").strip(),
            last_tool_name=str(last_tool_name or "").strip(),
        )
        return _claim_from_row(updated) if updated is not None else None

    def get(
        self,
        conversation_id: str,
        client_message_id: str,
    ) -> AiChatTurnClaim | None:
        """按幂等键读取一次领取。"""

        row = self.db.get_ai_chat_turn_claim(
            _required_value("conversation_id", conversation_id),
            _required_value("client_message_id", client_message_id),
        )
        return _claim_from_row(row) if row is not None else None

    def find_for_conversation(
        self,
        conversation_id: str,
    ) -> AiChatTurnClaim | None:
        """返回最近一次领取，用于校验历史归属。"""

        row = self.db.latest_ai_chat_turn_claim_for_conversation(
            _required_value("conversation_id", conversation_id),
        )
        return _claim_from_row(row) if row is not None else None


__all__ = [
    "CANCELLED",
    "CLAIMED",
    "COMPLETED",
    "FAILED",
    "TERMINAL_CLAIM_STATUSES",
    "AiChatTurnAlreadyAcceptedError",
    "AiChatTurnClaim",
    "AiChatTurnClaimError",
    "AiChatTurnClaimStore",
]
