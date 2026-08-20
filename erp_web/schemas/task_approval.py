"""全局任务审批等级的共享配置契约。"""

from __future__ import annotations

from typing import Literal, cast


TaskApprovalMode = Literal["ask", "full"]

TASK_APPROVAL_MODE_ASK: TaskApprovalMode = "ask"
TASK_APPROVAL_MODE_FULL: TaskApprovalMode = "full"
TASK_APPROVAL_MODES = frozenset(
    {TASK_APPROVAL_MODE_ASK, TASK_APPROVAL_MODE_FULL}
)


def normalize_task_approval_mode(value: object) -> TaskApprovalMode:
    """把持久化配置严格归一为当前支持的审批等级。"""

    normalized = str(value or TASK_APPROVAL_MODE_ASK).strip().lower()
    if normalized not in TASK_APPROVAL_MODES:
        raise ValueError(
            "task_approval_mode 仅支持 ask 或 full"
        )
    return cast(TaskApprovalMode, normalized)


__all__ = [
    "TASK_APPROVAL_MODE_ASK",
    "TASK_APPROVAL_MODE_FULL",
    "TASK_APPROVAL_MODES",
    "TaskApprovalMode",
    "normalize_task_approval_mode",
]
