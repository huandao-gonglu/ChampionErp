"""全局任务审批入口的可信审批凭据。

审批会话 token 在应用启动时随机生成，只随受信 UI bootstrap（/api/state）
下发给前端；它从不进入模型工具结果或对话上下文。审批/拒绝 HTTP 入口必须
出示该 token，服务端从校验通过的 token 派生审批人身份；模型无法自行构造
有效审批身份，因此不能自批高风险任务。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets


class ApprovalSessionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ApprovalSession:
    """进程级可信审批凭据持有者。"""

    def __init__(self, token: str = "") -> None:
        self._token = str(token or "").strip() or secrets.token_hex(32)

    @property
    def token(self) -> str:
        return self._token

    def require_approver(self, presented: str) -> str:
        """校验审批凭据并派生稳定审批人身份；无效凭据直接拒绝。"""

        presented_normalized = str(presented or "").strip()
        if not presented_normalized:
            raise ApprovalSessionError(
                "GLOBAL_TASK_APPROVAL_UNAUTHORIZED",
                "审批请求缺少可信审批凭据。",
            )
        if not hmac.compare_digest(
            presented_normalized.encode("utf-8"),
            self._token.encode("utf-8"),
        ):
            raise ApprovalSessionError(
                "GLOBAL_TASK_APPROVAL_UNAUTHORIZED",
                "审批凭据无效；审批只能由受信 UI 确认。",
            )
        fingerprint = hashlib.sha256(
            self._token.encode("utf-8")
        ).hexdigest()[:12]
        return f"local-ui:{fingerprint}"


__all__ = ["ApprovalSession", "ApprovalSessionError"]
