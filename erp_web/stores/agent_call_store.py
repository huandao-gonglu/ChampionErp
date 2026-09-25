"""原生 Deferred、传输收件箱与工具副作用回执的 SQLite 边界。

这里只保存原生请求/结果，不保存下一步工具、Agent 状态或模型事件副本。
"""

from __future__ import annotations

import json
import sqlite3
from uuid import uuid4
from typing import Any, Sequence

from pydantic import TypeAdapter
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.usage import RunUsage

from erp_web.db import ErpDatabase, utc_now
from erp_web.schemas.ai_work import BusinessWriteReceipt
from erp_web.stores.pydantic_message_store import (
    canonical_model_messages_json,
    PydanticMessageStoreError,
)
from erp_web.stores.ai_chat_turn_claim_store import AiChatTurnAlreadyAcceptedError

_REQUESTS = TypeAdapter(DeferredToolRequests)
_RESULTS = TypeAdapter(DeferredToolResults)
_USAGE = TypeAdapter(RunUsage)


class AgentCallStore:
    def __init__(self, db: ErpDatabase) -> None:
        self.db = db

    def current_turn_receipts(self, conversation_id: str) -> list[dict[str, Any]]:
        """按真实输入时间读取本轮业务回执，不存储重试计数或执行计划。"""
        with self.db._connect() as conn:
            rows = conn.execute(
                """SELECT tool_call_id,tool_name,arguments_json,output_json,status FROM ai_tool_receipts
                WHERE conversation_id=? AND updated_at >= COALESCE(
                    (SELECT MAX(created_at) FROM ai_chat_inbox WHERE conversation_id=?), '')
                ORDER BY updated_at""", (conversation_id, conversation_id),
            ).fetchall()
        return [{"tool_call_id": row["tool_call_id"], "tool_name": row["tool_name"], "status": row["status"],
                 "arguments": json.loads(row["arguments_json"]),
                 "output": json.loads(row["output_json"] or "null")} for row in rows]

    def completed_tool_receipts(
        self, conversation_id: str, tool_name: str,
    ) -> list[dict[str, Any]]:
        """读取同会话已完成的工具回执，供领域层核对创建对象的来源。"""
        with self.db._connect() as conn:
            rows = conn.execute(
                """SELECT arguments_json,output_json FROM ai_tool_receipts
                WHERE conversation_id=? AND tool_name=? AND status='completed'
                AND output_json IS NOT NULL ORDER BY rowid""",
                (conversation_id, tool_name),
            ).fetchall()
        return [{"arguments": json.loads(row["arguments_json"]),
                 "output": json.loads(row["output_json"])} for row in rows]

    def script_write_receipts(self, conversation_id: str) -> dict[str, list[BusinessWriteReceipt]]:
        """按官方 CodeMode 子调用 ID 归属汇总已落盘写回执，失败脚本也可展示成功部分。"""
        with self.db._connect() as conn:
            rows = conn.execute(
                """SELECT tool_call_id,tool_name,output_json FROM ai_tool_receipts
                WHERE conversation_id=? AND status='completed' AND output_json IS NOT NULL
                AND instr(tool_call_id, '__') > 0 ORDER BY updated_at, rowid""",
                (conversation_id,),
            ).fetchall()
        grouped: dict[str, list[BusinessWriteReceipt]] = {}
        for row in rows:
            parent_id, _, sequence = row["tool_call_id"].rpartition("__")
            output = json.loads(row["output_json"])
            if not sequence.isdigit() or not isinstance(output, dict):
                continue
            grouped.setdefault(parent_id, []).append({
                "tool_call_id": row["tool_call_id"], "tool_name": row["tool_name"], "output": output,
            })
        return grouped

    def pending(
        self, conversation_id: str
    ) -> tuple[DeferredToolRequests, DeferredToolResults, RunUsage] | None:
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ai_deferred_requests WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return (
            _REQUESTS.validate_json(row["requests_json"]),
            _RESULTS.validate_json(row["results_json"]),
            _USAGE.validate_json(row["usage_json"]),
        )

    def commit(
        self,
        conversation_id: str,
        messages: Sequence[ModelMessage],
        *,
        expected_version: int,
        requests: DeferredToolRequests | None = None,
        usage: RunUsage | None = None,
        consumed_sequences: Sequence[int] = (),
        replace_deferred: bool = True,
    ) -> int:
        encoded = canonical_model_messages_json(messages, stored=False)
        now = utc_now()
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT history_version FROM pydantic_message_histories WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            version = int(row[0]) if row else 0
            if version != expected_version:
                raise PydanticMessageStoreError(
                    "PYDANTIC_HISTORY_CONFLICT",
                    "对话历史版本已改变，拒绝覆盖其他写入。",
                )
            conn.execute(
                """INSERT INTO pydantic_message_histories
                (conversation_id,messages_json,history_version,created_at,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(conversation_id) DO UPDATE SET messages_json=excluded.messages_json,
                history_version=excluded.history_version,updated_at=excluded.updated_at""",
                (conversation_id, encoded, version + 1, now, now),
            )
            if replace_deferred:
                conn.execute(
                    "DELETE FROM ai_deferred_requests WHERE conversation_id=?",
                    (conversation_id,),
                )
            if requests is not None:
                conn.execute(
                    "INSERT INTO ai_deferred_requests VALUES(?,?,?,?,?)",
                    (
                        conversation_id,
                        _REQUESTS.dump_json(requests),
                        _RESULTS.dump_json(DeferredToolResults()),
                        _USAGE.dump_json(usage or RunUsage()),
                        now,
                    ),
                )
                for call in requests.calls:
                    conn.execute(
                        """INSERT INTO ai_tool_receipts
                        (conversation_id,tool_call_id,tool_name,arguments_json,execution_json,status,updated_at)
                        VALUES(?,?,?,?,?,'queued',?) ON CONFLICT(conversation_id,tool_call_id) DO NOTHING""",
                        (
                            conversation_id,
                            call.tool_call_id,
                            call.tool_name,
                            json.dumps(call.args_as_dict(), ensure_ascii=False),
                            json.dumps(
                                requests.metadata.get(call.tool_call_id, {}),
                                ensure_ascii=False,
                            ),
                            now,
                        ),
                    )
            for sequence in consumed_sequences:
                conn.execute(
                    "UPDATE ai_chat_inbox SET status='applied' WHERE sequence=? AND conversation_id=?",
                    (sequence, conversation_id),
                )
            conn.commit()
        return version + 1

    def record_results(
        self, conversation_id: str, results: DeferredToolResults
    ) -> None:
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT requests_json,results_json FROM ai_deferred_requests WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("没有待恢复的原生工具请求。")
            requests = _REQUESTS.validate_json(row[0])
            # 原生校验拒绝不存在的 ID 和错误种类，不能由客户端添加调用。
            requests.build_results(calls=results.calls, approvals=results.approvals)
            saved = _RESULTS.validate_json(row[1])
            for name in ("calls", "approvals", "metadata"):
                target = getattr(saved, name)
                for key, value in getattr(results, name).items():
                    if key in target and target[key] != value:
                        raise ValueError("该工具结果已提交，不能覆盖。")
                    target[key] = value
            conn.execute(
                "UPDATE ai_deferred_requests SET results_json=? WHERE conversation_id=?",
                (_RESULTS.dump_json(saved), conversation_id),
            )
            conn.commit()

    def ready(self, conversation_id: str) -> bool:
        pending = self.pending(conversation_id)
        if pending is None:
            return False
        requests, results, _ = pending
        return {c.tool_call_id for c in requests.calls} <= results.calls.keys() and {
            c.tool_call_id for c in requests.approvals
        } <= results.approvals.keys()

    def receive(
        self,
        conversation_id: str,
        message_id: str,
        messages: Sequence[ModelMessage],
        *,
        draft_ids: Sequence[str] = (),
    ) -> int:
        with self.db._connect() as conn:
            with conn:
                cursor = conn.execute(
                    "INSERT INTO ai_chat_inbox(conversation_id,message_id,messages_json,target_draft_ids,created_at) VALUES(?,?,?,?,?)",
                    (
                        conversation_id,
                        message_id,
                        ModelMessagesTypeAdapter.dump_json(list(messages)),
                        json.dumps(list(draft_ids)),
                        utc_now(),
                    ),
                )
                return int(cursor.lastrowid)

    def accept_input(
        self,
        conversation_id: str,
        message_id: str,
        messages: Sequence[ModelMessage],
        *,
        draft_ids=(),
        profile_id: str,
        actor_id: str,
        tenant_id: str,
    ) -> None:
        """身份领取与原生输入同事务落盘，不留下只有领取记录的孤立回合。"""
        encoded = ModelMessagesTypeAdapter.dump_json(list(messages))
        draft_ids = draft_ids or self.selected_drafts(conversation_id)
        try:
            with self.db._connect() as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """INSERT INTO ai_chat_turn_claims
                    (claim_id,conversation_id,client_message_id,profile_id,actor_id,tenant_id,status,claimed_at)
                    VALUES(?,?,?,?,?,?,'claimed',?)""",
                    (
                        f"claim_{uuid4().hex}",
                        conversation_id,
                        message_id,
                        profile_id,
                        actor_id,
                        tenant_id,
                        utc_now(),
                    ),
                )
                conn.execute(
                    "INSERT INTO ai_chat_inbox(conversation_id,message_id,messages_json,target_draft_ids,created_at) VALUES(?,?,?,?,?)",
                    (
                        conversation_id,
                        message_id,
                        encoded,
                        json.dumps(list(draft_ids)),
                        utc_now(),
                    ),
                )
        except sqlite3.IntegrityError:
            raise AiChatTurnAlreadyAcceptedError("本轮消息已经被接收。") from None

    def cancel_pending_work(self, conversation_id: str) -> None:
        """撤下未执行的领域任务和恢复请求；已发出的操作继续保留真实回执。"""
        output = json.dumps({"ok": False, "error": {
            "code": "OPERATION_CANCELLED", "message": "用户已停止，本次工具未执行。",
        }}, ensure_ascii=False)
        with self.db._connect() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE ai_tool_receipts SET status='completed',output_json=?,updated_at=? WHERE conversation_id=? AND status='queued'",
                (output, utc_now(), conversation_id),
            )
            conn.execute("DELETE FROM ai_deferred_requests WHERE conversation_id=?", (conversation_id,))
            conn.execute("UPDATE ai_chat_inbox SET status='failed' WHERE conversation_id=? AND status='received'", (conversation_id,))
            conn.execute(
                "UPDATE ai_chat_turn_claims SET status='cancelled',finished_at=? WHERE conversation_id=? AND status='claimed'",
                (utc_now(), conversation_id),
            )

    def cancel_input(self, conversation_id: str, message_id: str, *, profile_id: str, actor_id: str, tenant_id: str) -> None:
        """取消先于发送请求到达时，使用现有幂等领取记录阻止该消息迟到后启动。"""
        with self.db._connect() as conn, conn:
            conn.execute(
                """INSERT INTO ai_chat_turn_claims
                (claim_id,conversation_id,client_message_id,profile_id,actor_id,tenant_id,status,claimed_at,finished_at)
                VALUES(?,?,?,?,?,?,'cancelled',?,?) ON CONFLICT(conversation_id,client_message_id) DO NOTHING""",
                (f"claim_{uuid4().hex}", conversation_id, message_id, profile_id, actor_id, tenant_id, utc_now(), utc_now()),
            )

    def fail_unconsumed(self, conversation_id: str) -> None:
        with self.db._connect() as conn, conn:
            conn.execute(
                "UPDATE ai_chat_inbox SET status='failed' WHERE conversation_id=? AND status='received'",
                (conversation_id,),
            )

    def mark_unknown(self, conversation_id: str, call_id: str) -> None:
        self.finish(
            conversation_id,
            call_id,
            {
                "ok": False,
                "error": {
                    "code": "TOOL_OUTCOME_UNKNOWN",
                    "message": "执行未取得可靠回执；操作可能已经生效，请先查询业务或平台状态。",
                    "retryable": False,
                    "details": {"outcome_unknown": True},
                },
            },
        )

    def inbox(self, conversation_id: str) -> list[dict[str, Any]]:
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_chat_inbox WHERE conversation_id=? AND status='received' ORDER BY sequence",
                (conversation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def user_facts(self, conversation_id: str) -> list[dict[str, Any]]:
        from pydantic_ai.messages import UserPromptPart

        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT message_id,messages_json,target_draft_ids FROM ai_chat_inbox WHERE conversation_id=? ORDER BY sequence DESC LIMIT 50",
                (conversation_id,),
            ).fetchall()
        return [
            {
                "message_id": row["message_id"],
                "draft_ids": json.loads(row["target_draft_ids"]),
                "text": "\n".join(
                    str(part.content)
                    for message in ModelMessagesTypeAdapter.validate_json(
                        row["messages_json"]
                    )
                    for part in message.parts
                    if isinstance(part, UserPromptPart)
                )[:4000],
            }
            for row in reversed(rows)
        ]

    def selected_drafts(self, conversation_id: str) -> tuple[str, ...]:
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT target_draft_ids FROM ai_chat_inbox WHERE conversation_id=? AND target_draft_ids!='[]' ORDER BY sequence DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return tuple(json.loads(row[0])) if row else ()

    def receipt(self, conversation_id: str, call_id: str) -> dict[str, Any] | None:
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ai_tool_receipts WHERE conversation_id=? AND tool_call_id=?",
                (conversation_id, call_id),
            ).fetchone()
        return dict(row) if row else None

    def unresolved_outcome(
        self, conversation_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        """新 call_id 也不能绕过同一操作尚未对账的未知回执。"""
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT arguments_json,output_json,status FROM ai_tool_receipts WHERE conversation_id=? AND tool_name=? AND (output_json IS NOT NULL OR status='executing')",
                (conversation_id, tool_name),
            ).fetchall()
        for row in rows:
            if json.loads(row["arguments_json"]) != arguments:
                continue
            if row["status"] == "executing":
                return {"ok": False, "error": {
                    "code": "TOOL_OUTCOME_UNKNOWN",
                    "message": "相同操作仍在执行或等待回执，本次未重复提交。",
                    "details": {"outcome_unknown": True},
                }}
            output = json.loads(row["output_json"])
            if (
                json.loads(row["arguments_json"]) == arguments
                and isinstance(output, dict)
                and output.get("error", {}).get("details", {}).get("outcome_unknown")
            ):
                return output
        return None

    def touch_job(self, conversation_id: str, call_id: str) -> None:
        with self.db._connect() as conn, conn:
            conn.execute(
                "UPDATE ai_tool_receipts SET updated_at=? WHERE conversation_id=? AND tool_call_id=?",
                (utc_now(), conversation_id, call_id),
            )

    def begin_write(
        self,
        conversation_id: str,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> bool:
        with self.db._connect() as conn:
            with conn:
                cursor = conn.execute(
                    """INSERT INTO ai_tool_receipts(conversation_id,tool_call_id,tool_name,arguments_json,status,updated_at)
                    VALUES(?,?,?,?,'executing',?) ON CONFLICT(conversation_id,tool_call_id) DO NOTHING""",
                    (
                        conversation_id,
                        call_id,
                        tool_name,
                        json.dumps(arguments, ensure_ascii=False),
                        utc_now(),
                    ),
                )
                return bool(cursor.rowcount)

    def claim_job(self, conversation_id: str, call_id: str) -> bool:
        with self.db._connect() as conn:
            with conn:
                return bool(
                    conn.execute(
                        "UPDATE ai_tool_receipts SET status='executing',updated_at=? WHERE conversation_id=? AND tool_call_id=? AND status='queued'",
                        (utc_now(), conversation_id, call_id),
                    ).rowcount
                )

    def finish(
        self, conversation_id: str, call_id: str, output: Any, *, job: bool = False
    ) -> None:
        with self.db._connect() as conn:
            with conn:
                conn.execute(
                    "UPDATE ai_tool_receipts SET status=?,output_json=?,job_json=?,updated_at=? WHERE conversation_id=? AND tool_call_id=?",
                    (
                        "waiting_job" if job else "completed",
                        json.dumps(output, ensure_ascii=False),
                        json.dumps(output, ensure_ascii=False) if job else None,
                        utc_now(),
                        conversation_id,
                        call_id,
                    ),
                )

    def work(self) -> list[dict[str, Any]]:
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_tool_receipts WHERE status IN ('queued','waiting_job') ORDER BY updated_at LIMIT 100"
            ).fetchall()
        return [dict(row) for row in rows]

    def conversations(self) -> list[str]:
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT conversation_id FROM ai_deferred_requests UNION SELECT conversation_id FROM ai_chat_inbox WHERE status='received'"
            ).fetchall()
        return [str(row[0]) for row in rows]

    def mark_interrupted_writes(self) -> int:
        """进程重启后不盲目重发；未知副作用保留调用与参数供领域对账。"""
        output = json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "TOOL_OUTCOME_UNKNOWN",
                    "message": "进程在工具执行期间退出；操作可能已经完成，须先查询业务或平台回执。",
                    "retryable": False,
                    "details": {"outcome_unknown": True},
                },
            },
            ensure_ascii=False,
        )
        with self.db._connect() as conn:
            with conn:
                conn.execute(
                    """UPDATE ai_chat_turn_claims SET status='failed',error_code='TOOL_OUTCOME_UNKNOWN',finished_at=?
                    WHERE status='claimed' AND conversation_id IN (SELECT conversation_id FROM ai_tool_receipts WHERE status='executing')
                    AND conversation_id NOT IN (SELECT conversation_id FROM ai_deferred_requests)""",
                    (utc_now(),),
                )
                return conn.execute(
                    "UPDATE ai_tool_receipts SET status='completed',output_json=?,updated_at=? WHERE status='executing'",
                    (output, utc_now()),
                ).rowcount
