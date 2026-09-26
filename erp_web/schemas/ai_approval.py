"""主对话工具审批偏好；不改变工具权限和业务校验。"""

from typing import Literal, cast

AiToolApprovalMode = Literal["ask", "full"]


def normalize_ai_tool_approval_mode(value: object = "ask") -> AiToolApprovalMode:
    if value not in ("ask", "full"):
        raise ValueError("工具审批模式只能是 ask（询问审批）或 full（完全授权）。")
    return cast(AiToolApprovalMode, value)


__all__ = ["AiToolApprovalMode", "normalize_ai_tool_approval_mode"]
