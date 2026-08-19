"""服务端审批绑定：冻结快照 digest 与执行侧重核。

审批摘要与规范化参数只能由服务端生成（Capability 声明的快照函数），
digest 同时绑定 Capability 名称/版本、operation_key、步骤 ID 与任务版本；
批准后的执行必须重算快照并复核 digest，任何受保护参数或任务版本变化都
会使旧审批失效。
"""

from __future__ import annotations

import hashlib
import json

from erp_web.schemas.ai_tools import AiToolExecutionError, TaskApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext


def approval_binding_digest(
    *,
    snapshot: TaskApprovalSnapshot,
    capability_name: str,
    capability_version: str,
    operation_key: str,
    step_id: str,
    task_revision: int,
) -> str:
    """把审批快照与步骤/任务版本绑定为稳定 digest。"""

    payload = {
        "capability_name": str(capability_name or "").strip(),
        "capability_version": str(capability_version or "").strip(),
        "operation_key": str(operation_key or "").strip(),
        "step_id": str(step_id or "").strip(),
        "task_revision": int(task_revision),
        "summary": snapshot.summary,
        "canonical_payload": snapshot.canonical_payload,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_execution_approval(
    execution: AiExecutionContext,
    *,
    snapshot: TaskApprovalSnapshot,
    capability_name: str,
    capability_version: str,
    stale_code: str,
) -> None:
    """执行侧重核：重算快照 digest 并与批准时持久化的 digest 比对。

    缺少可信审批上下文或任何受保护字段变化都会抛出稳定错误码，阻止
    在“所见非所批”的状态下执行破坏性写入。
    """

    digest = str(execution.approval_digest or "").strip()
    if not digest or execution.approval_task_revision <= 0:
        raise AiToolExecutionError(
            "TASK_APPROVAL_CONTEXT_REQUIRED",
            "审批写入缺少可信审批上下文，不能执行。",
        )
    step_id = str(execution.business_scope.get("step_id") or "").strip()
    operation_key = str(
        execution.idempotency_context.get("operation_key") or ""
    ).strip()
    expected = approval_binding_digest(
        snapshot=snapshot,
        capability_name=capability_name,
        capability_version=capability_version,
        operation_key=operation_key,
        step_id=step_id,
        task_revision=execution.approval_task_revision,
    )
    if expected != digest:
        raise AiToolExecutionError(
            stale_code,
            "审批内容与当前执行参数或任务版本已不一致，原审批已失效。",
        )


__all__ = [
    "approval_binding_digest",
    "verify_execution_approval",
]
