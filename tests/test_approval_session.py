"""受信审批凭据与审批身份派生测试（P1-1）。

审批会话 token 在进程启动时随机生成，只经受信 UI bootstrap 下发；审批/拒绝
入口必须出示该 token，服务端从校验通过的 token 派生审批人身份。模型无法构造
有效 token，因此不能自批高风险任务。
"""

from __future__ import annotations

import pytest

from erp_web.context import get_context
from erp_web.facades import global_task_facade
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
    assert empty.value.code == "GLOBAL_TASK_APPROVAL_UNAUTHORIZED"

    with pytest.raises(ApprovalSessionError) as mismatch:
        session.require_approver("forged-token")
    assert mismatch.value.code == "GLOBAL_TASK_APPROVAL_UNAUTHORIZED"


def test_generated_token_is_random_and_nonempty() -> None:
    first = ApprovalSession()
    second = ApprovalSession()

    assert len(first.token) >= 32
    # 两个会话默认生成不同 token，避免跨会话复用审批凭据。
    assert first.token != second.token


def test_facade_approve_without_valid_token_is_unauthorized() -> None:
    # 缺少凭据：403，且不会触达 Controller。
    payload, status = global_task_facade.approve_global_task_payload(
        {"task_id": "gtask_missing"},
        approval_token="",
    )
    assert status == 403
    assert payload["error_code"] == "GLOBAL_TASK_APPROVAL_UNAUTHORIZED"

    # 伪造凭据：同样 403。
    payload, status = global_task_facade.approve_global_task_payload(
        {"task_id": "gtask_missing"},
        approval_token="forged-by-model",
    )
    assert status == 403
    assert payload["error_code"] == "GLOBAL_TASK_APPROVAL_UNAUTHORIZED"


def test_facade_reject_without_valid_token_is_unauthorized() -> None:
    payload, status = global_task_facade.reject_global_task_payload(
        {"task_id": "gtask_missing", "reason": "拒绝"},
        approval_token="forged-by-model",
    )
    assert status == 403
    assert payload["error_code"] == "GLOBAL_TASK_APPROVAL_UNAUTHORIZED"


def test_facade_accepts_bootstrap_token_and_derives_identity() -> None:
    # 受信 UI 通过 /api/state 拿到的 token 能通过校验；随后的失败只应来自
    # 任务不存在，而不是审批凭据无效。
    token = get_context().approval_session.token

    payload, status = global_task_facade.approve_global_task_payload(
        {"task_id": "gtask_missing"},
        approval_token=token,
    )
    assert payload["error_code"] != "GLOBAL_TASK_APPROVAL_UNAUTHORIZED", (
        payload,
        status,
    )
