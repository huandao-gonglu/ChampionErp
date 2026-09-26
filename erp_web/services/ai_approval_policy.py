"""把受信 UI 保存的审批偏好接入 Pydantic AI 原生 Deferred 结果。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from pydantic_ai import DeferredToolRequests, DeferredToolResults, RunContext

from erp_web.schemas.ai_approval import AiToolApprovalMode
from erp_web.services.ai_run_cancellation import check_cancellation


def automatic_approval_results(
    requests: DeferredToolRequests, *, mode: AiToolApprovalMode,
) -> DeferredToolResults | None:
    """只批准带服务端快照的审批请求；外部任务仍由现有 Job 服务执行。"""
    if mode != "full":
        return None
    approvals = {}
    metadata = {}
    confirmed_at = datetime.now(timezone.utc).isoformat()
    for call in requests.approvals:
        snapshot = requests.metadata.get(call.tool_call_id, {})
        if not snapshot.get("approval_digest") or not snapshot.get("approval_revision"):
            continue
        approvals[call.tool_call_id] = True
        metadata[call.tool_call_id] = {
            **snapshot,
            "approver": "local-user:full-authorization",
            "approval_confirmed_at": confirmed_at,
            "approval_mode": "full",
        }
    return requests.build_results(approvals=approvals, metadata=metadata) if approvals else None


async def handle_configured_tool_approvals(
    ctx: RunContext, requests: DeferredToolRequests,
) -> DeferredToolResults | None:
    """同进程审批交给官方 HandleDeferredToolCalls 继续执行，不另建 Agent 循环。"""
    support = ctx.deps.tool_runtime.run_support
    if support is None or not requests.approvals:
        return None
    check_cancellation()
    mode = await asyncio.to_thread(support.approval_mode_reader)
    return automatic_approval_results(requests, mode=mode)


__all__ = ["automatic_approval_results", "handle_configured_tool_approvals"]
