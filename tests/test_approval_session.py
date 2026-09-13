"""受信审批凭据与审批身份派生测试（P1-1）。

审批会话 token 在进程启动时随机生成，只经受信 UI bootstrap 下发；审批/拒绝
入口必须出示该 token，服务端从校验通过的 token 派生审批人身份。模型无法构造
有效 token，因此不能自批高风险任务。
"""

from __future__ import annotations

import pytest

from erp_web.services.approval_session import (
    ApprovalSession,
    ApprovalSessionError,
)


def test_require_approver_derives_stable_identity_from_valid_token() -> None:
    session = ApprovalSession(token="trusted-token-1")

    approver = session.require_approver("trusted-token-1")

    assert approver.startswith("local-ui:")
    # 同一会话重复校验得到同一稳定身份。
    assert session.require_approver("trusted-token-1") == approver


def test_require_approver_identity_is_not_controlled_by_presented_text() -> None:
    session = ApprovalSession(token="trusted-token-2")

    # 身份由服务端 token 派生；出示串只用于校验，不能决定身份内容。
    approver = session.require_approver("trusted-token-2")
    assert "admin" not in approver
    assert approver == session.require_approver("trusted-token-2")


def test_require_approver_rejects_empty_or_mismatched_token() -> None:
    session = ApprovalSession(token="trusted-token-3")

    with pytest.raises(ApprovalSessionError) as empty:
        session.require_approver("")
    assert empty.value.code == "AI_TOOL_APPROVAL_UNAUTHORIZED"

    with pytest.raises(ApprovalSessionError) as mismatch:
        session.require_approver("forged-token")
    assert mismatch.value.code == "AI_TOOL_APPROVAL_UNAUTHORIZED"


def test_generated_token_is_random_and_nonempty() -> None:
    first = ApprovalSession()
    second = ApprovalSession()

    assert len(first.token) >= 32
    # 两个会话默认生成不同 token，避免跨会话复用审批凭据。
    assert first.token != second.token
