"""测试用的可信审批执行上下文。"""

from datetime import datetime, timedelta, timezone
from erp_web.schemas.ai_tools import ToolApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.tool_approval import approval_binding_digest


def _execution(operation_key: str = "op-1") -> AiExecutionContext:
    return AiExecutionContext(
        task_run_id="task-1",
        attempt_id="attempt-1",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        budget_profile="test",
        business_scope={"task_id": "task-1", "tool_call_id": "step-1"},
        idempotency_context={"operation_key": operation_key},
    )


def _approved_execution(
    snapshot: ToolApprovalSnapshot,
    capability_name: str,
    *,
    operation_key: str = "op-1",
    tool_call_id: str = "step-1",
    approval_revision: int = 1,
) -> AiExecutionContext:
    """模拟 Controller 批准后注入的可信审批上下文（digest + 任务版本）。"""

    digest = approval_binding_digest(
        snapshot=snapshot,
        capability_name=capability_name,
        capability_version="1",
        operation_key=operation_key,
        tool_call_id=tool_call_id,
        approval_revision=approval_revision,
    )
    return AiExecutionContext(
        task_run_id="task-1",
        attempt_id="attempt-1",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        budget_profile="test",
        business_scope={
            "task_id": "task-1",
            "tool_call_id": tool_call_id,
            "approver": "local-ui:test",
        },
        idempotency_context={"operation_key": operation_key},
        approval_digest=digest,
        approval_revision=approval_revision,
    )


