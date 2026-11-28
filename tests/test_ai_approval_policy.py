"""三档工具审批模式（ask / auto / full）与执行侧分级复核测试。"""

from __future__ import annotations

import pytest
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ToolCallPart

from erp_web.schemas.ai_approval import normalize_ai_tool_approval_mode
from erp_web.schemas.ai_tools import AiToolExecutionError, ToolApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.ai_approval_policy import automatic_approval_results
from erp_web.services.tool_approval import approval_binding_digest, verify_execution_approval


def _requests() -> DeferredToolRequests:
    local_call = ToolCallPart(
        tool_name="draft_package_estimates_confirm",
        args={"draft_id": "d1"},
        tool_call_id="call-local",
    )
    high_call = ToolCallPart(
        tool_name="product_publish_request",
        args={"draft_id": "d1"},
        tool_call_id="call-high",
    )
    unsigned_call = ToolCallPart(
        tool_name="draft_delete",
        args={"draft_id": "d1"},
        tool_call_id="call-unsigned",
    )
    return DeferredToolRequests(
        approvals=[local_call, high_call, unsigned_call],
        metadata={
            "call-local": {
                "summary": "保存包装尺寸建议",
                "approval_tier": "local",
                "approval_digest": "digest-local",
                "approval_revision": 2,
            },
            "call-high": {
                "summary": "提交发布任务",
                "approval_tier": "high_impact",
                "approval_digest": "digest-high",
                "approval_revision": 2,
            },
            "call-unsigned": {
                "summary": "缺少 digest 的无效快照",
                "approval_tier": "local",
            },
        },
    )


def test_normalize_approval_mode_accepts_three_tiers_and_rejects_unknown() -> None:
    assert normalize_ai_tool_approval_mode("ask") == "ask"
    assert normalize_ai_tool_approval_mode("auto") == "auto"
    assert normalize_ai_tool_approval_mode("full") == "full"
    with pytest.raises(ValueError):
        normalize_ai_tool_approval_mode("unsafe")


def test_automatic_approval_results_respects_ask_auto_and_full_tiers() -> None:
    requests = _requests()

    assert automatic_approval_results(requests, mode="ask") is None

    auto_results = automatic_approval_results(requests, mode="auto")
    assert auto_results is not None
    assert auto_results.approvals == {"call-local": True}
    assert auto_results.metadata["call-local"]["approver"] == "local-user:auto-approval"
    assert auto_results.metadata["call-local"]["approval_mode"] == "auto"
    remaining_after_auto = requests.remaining(auto_results)
    assert remaining_after_auto is not None
    assert [call.tool_call_id for call in remaining_after_auto.approvals] == ["call-high", "call-unsigned"]

    full_results = automatic_approval_results(requests, mode="full")
    assert full_results is not None
    assert full_results.approvals == {"call-local": True, "call-high": True}
    assert full_results.metadata["call-high"]["approver"] == "local-user:full-authorization"
    assert full_results.metadata["call-high"]["approval_mode"] == "full"


def test_verify_execution_approval_blocks_auto_approver_on_high_impact_snapshot() -> None:
    local_snapshot = ToolApprovalSnapshot(
        summary="保存尺寸估算",
        approval_tier="local",
        canonical_payload={"draft_id": "d1"},
    )
    high_snapshot = ToolApprovalSnapshot(
        summary="发布商品",
        approval_tier="high_impact",
        canonical_payload={"draft_id": "d1"},
    )

    def make_context(snapshot: ToolApprovalSnapshot, approver: str) -> AiExecutionContext:
        digest = approval_binding_digest(
            snapshot=snapshot,
            capability_name="test_cap",
            capability_version="1",
            operation_key="op-1",
            tool_call_id="call-1",
            approval_revision=3,
        )
        return AiExecutionContext.create(
            timeout_seconds=30,
            budget_profile="test",
            business_scope={"tool_call_id": "call-1", "approver": approver},
            idempotency_context={"operation_key": "op-1"},
            approved_tool_call_ids={"call-1"},
            approval_digest=digest,
            approval_revision=3,
        )

    verify_execution_approval(
        make_context(local_snapshot, "local-user:auto-approval"),
        snapshot=local_snapshot,
        capability_name="test_cap",
        capability_version="1",
        stale_code="STALE",
    )
    verify_execution_approval(
        make_context(high_snapshot, "local-user:full-authorization"),
        snapshot=high_snapshot,
        capability_name="test_cap",
        capability_version="1",
        stale_code="STALE",
    )

    with pytest.raises(AiToolExecutionError) as exc:
        verify_execution_approval(
            make_context(high_snapshot, "local-user:auto-approval"),
            snapshot=high_snapshot,
            capability_name="test_cap",
            capability_version="1",
            stale_code="STALE",
        )
    assert exc.value.code == "AUTO_APPROVAL_NOT_ALLOWED"
