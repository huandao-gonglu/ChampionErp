"""通过原生 hooks 把单写者 run 连接到收件箱、消息 CAS 和写回执。"""

from __future__ import annotations

import dataclasses
import asyncio
import json
import threading
from typing import Any

from pydantic_ai import ModelRequestNode, ModelRetry, RunContext, UserPromptNode
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, RetryPromptPart

from erp_web.stores.agent_call_store import AgentCallStore
from erp_web.schemas.ai_page_context import page_context_instructions
from erp_web.services.ai_run_cancellation import check_cancellation


class AgentRunStorage:
    def __init__(
        self, store: AgentCallStore, conversation_id: str, history: list, version: int,
    ) -> None:
        self.store = store
        self.conversation_id = conversation_id
        self.history = list(history)
        self.version = version
        self.consumed: set[int] = set()
        self.lock = threading.RLock()
        self.target_draft_ids: tuple[str, ...] = store.selected_drafts(conversation_id)
        self.page_context = None
        for message in history:
            metadata = message.metadata or {}
            if "page_context" in metadata:
                self.page_context = metadata["page_context"]

    def context_instructions(self) -> str:
        """由原生动态 instructions 读取本轮背景，关闭时不复用旧页面。"""
        return page_context_instructions(self.page_context)

    def code_failure_instructions(self, messages: list) -> str:
        """脚本异常后补充已有业务回执，避免把整段脚本当作从未执行。"""
        if not messages or not isinstance(messages[-1], ModelRequest):
            return ""
        prefixes = tuple(
            f"{part.tool_call_id}__" for part in messages[-1].parts
            if isinstance(part, RetryPromptPart) and part.tool_name == "run_code"
        )
        if not prefixes:
            return ""
        rows = [row for row in self.store.current_turn_receipts(self.conversation_id)
                if row["tool_call_id"].startswith(prefixes)]
        if not rows:
            return ""
        lines = ["刚才脚本的实际写回执如下。脚本报错不会撤销已完成操作；只处理未完成部分，禁止整段重放。"]
        size = 0
        for index, row in enumerate(rows):
            output = row["output"] or {}
            summary = {key: row[key] for key in ("tool_call_id", "tool_name", "status", "arguments")}
            summary["ok"] = output.get("ok", row["status"] == "completed" and not output.get("error"))
            summary["error"] = output.get("error")
            line = json.dumps(summary, ensure_ascii=False)
            # 这是提示中的摘要，不改写数据库回执；缺证据时须查询当前状态。
            if len(line) > 2000 or size + len(line) > 24000:
                lines.append(f"另有 {len(rows) - index} 条回执未展开；不能据此假定未执行，请查询业务状态后继续。")
                break
            lines.append(line)
            size += len(line)
        return "\n".join(lines)

    def canonical(self, ctx: RunContext) -> list:
        return [*self.history, *(m for m in ctx.messages if m.run_id == ctx.run_id)]

    def receive_at_boundary(self, ctx: RunContext) -> None:
        check_cancellation()
        with self.lock:
            for row in self.store.inbox(self.conversation_id):
                if row["sequence"] in self.consumed:
                    continue
                messages = ModelMessagesTypeAdapter.validate_json(row["messages_json"])
                for message in messages:
                    message.run_id = ctx.run_id
                    message.conversation_id = self.conversation_id
                    self.page_context = (message.metadata or {}).get("page_context")
                    message.metadata = {
                        "user_message_id": row["message_id"],
                        "page_context": self.page_context,
                    }
                # 在模型节点开始前更新页面背景，用户消息由原生队列注入和编码。
                ctx.enqueue(*messages)
                ids = tuple(json.loads(row["target_draft_ids"]))
                if ids:
                    self.target_draft_ids = ids
                self.consumed.add(row["sequence"])

    def before_tool(self, ctx: RunContext, *, writing: bool) -> None:
        check_cancellation()
        if any(
            row["sequence"] not in self.consumed
            for row in self.store.inbox(self.conversation_id)
        ):
            raise ModelRetry(
                "用户已提交更新。请先读取下一次模型请求中的新用户消息，再决定操作。"
            )
        if writing:
            with self.lock:
                self.version = self.store.commit(
                    self.conversation_id,
                    self.canonical(ctx),
                    expected_version=self.version,
                    consumed_sequences=tuple(self.consumed),
                    replace_deferred=False,
                )

    def execution(self, ctx: RunContext, arguments: dict[str, Any]) -> Any:
        base = ctx.deps.execution_context
        call_id = str(ctx.tool_call_id)
        metadata = dict(ctx.tool_call_metadata or {})
        scope = {
            **dict(base.business_scope),
            "tool_call_id": call_id,
            "approver": str(metadata.get("approver", "")),
            "approval_confirmed_at": str(metadata.get("approval_confirmed_at", "")),
            "target_draft_ids": json.dumps(self.target_draft_ids),
            "user_facts": json.dumps(
                self.store.user_facts(self.conversation_id), ensure_ascii=False
            ),
        }
        return dataclasses.replace(
            base,
            business_scope=scope,
            idempotency_context={
                **dict(base.idempotency_context),
                "operation_key": f"{self.conversation_id}:{call_id}",
            },
            approved_tool_call_ids=frozenset({call_id})
            if ctx.tool_call_approved
            else frozenset(),
            approval_digest=str(metadata.get("approval_digest", "")),
            approval_revision=int(metadata.get("approval_revision", 0)),
        )

    def finish(self, result: Any) -> None:
        from pydantic_ai import DeferredToolRequests

        with self.lock:
            self.version = self.store.commit(
                self.conversation_id,
                [*self.history, *result.new_messages()],
                expected_version=self.version,
                requests=result.output
                if isinstance(result.output, DeferredToolRequests)
                else None,
                usage=result.usage,
                consumed_sequences=tuple(self.consumed),
            )

    def fail(self, messages: list, run_id: str) -> None:
        """保存已发生的原生历史；失败的用户回合不由收件箱无限重新运行。"""
        with self.lock:
            current = [message for message in messages if message.run_id == run_id]
            self.version = self.store.commit(
                self.conversation_id,
                [*self.history, *current],
                expected_version=self.version,
                consumed_sequences=tuple(self.consumed),
            )

    def failure_summary(self) -> str:
        records = self.store.current_turn_receipts(self.conversation_id)
        if not records:
            return ""
        lines = ["本轮业务回执（失败不代表已写入的数据已撤销）："]
        for row in records:
            args, output = row["arguments"], row["output"] or {}
            target = "/".join(str(value or "") for value in (args.get("draft_id"), args.get("target_platform") or args.get("platform"), args.get("site")))
            error = output.get("error") or {}
            status = error.get("code") or output.get("status") or row["status"]
            lines.append(f"{target} {row['tool_name']}：{status}")
        return "\n".join(lines)


async def receive_user_updates(ctx: RunContext, *, node: Any) -> Any:
    support = ctx.deps.tool_runtime.run_support
    # 带历史或 Deferred 的 UserPromptNode 也会提前准备工具。
    if support is not None and isinstance(node, (UserPromptNode, ModelRequestNode)):
        await asyncio.to_thread(support.receive_at_boundary, ctx)
    return node
