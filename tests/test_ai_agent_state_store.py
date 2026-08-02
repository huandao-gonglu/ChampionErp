from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel
from pydantic_ai import (
    DeferredToolRequests,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)

from erp_web.services.ai_agent_state_store import (
    AI_AGENT_STATE_SCHEMA_VERSION,
    AiAgentApprovalRecord,
    AiAgentStateEnvelope,
    AiAgentStateError,
    AiAgentStateStore,
    validate_ready_context,
    validate_resume_context,
)


NOW = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
DEADLINE = NOW + timedelta(minutes=5)


class _TypedReadyOutput(BaseModel):
    selected_category_id: str
    abstained: bool
    evidence: list[str]


def _messages() -> list[ModelRequest | ModelResponse]:
    return [
        ModelRequest(
            parts=[UserPromptPart("请执行需要审批的库存写入")],
            run_id="run_1",
            conversation_id="conversation_1",
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    "inventory_update",
                    {"product_id": "product_1", "quantity": 3},
                    "call_1",
                )
            ],
            model_name="test-model",
            provider_name="test",
            run_id="run_1",
            conversation_id="conversation_1",
        ),
    ]


def _deferred() -> DeferredToolRequests:
    return DeferredToolRequests(
        approvals=[
            ToolCallPart(
                "inventory_update",
                {"product_id": "product_1", "quantity": 3},
                "call_1",
            )
        ],
        metadata={
            "call_1": {
                "use_case_id": "inventory.adjust",
                "tool_name": "inventory_update",
            }
        },
    )


def _create_pending(
    store: AiAgentStateStore,
    *,
    state_id: str = "state_1",
) -> AiAgentStateEnvelope:
    return store.create_pending(
        state_id=state_id,
        use_case_id="inventory.adjust",
        profile_version="inventory_adjust.v1",
        toolset_id="inventory.write",
        deadline_at=DEADLINE,
        actor_id="user_1",
        tenant_id="tenant_1",
        required_permissions={"inventory.write", "ai.approve"},
        business_scope={"store_id": "store_1", "product_id": "product_1"},
        idempotency_context={"operation_id": "operation_1"},
        message_history=_messages(),
        deferred_requests=_deferred(),
        references={
            "ai_work_id": "conversation_1",
            "invocation_id": "invocation_1",
            "agent_run_id": "run_1",
            "trace_id": "trace_1",
        },
        now=NOW,
    )


def _valid_resume_kwargs() -> dict[str, Any]:
    return {
        "use_case_id": "inventory.adjust",
        "profile_version": "inventory_adjust.v1",
        "toolset_id": "inventory.write",
        "tenant_id": "tenant_1",
        "business_scope": {"store_id": "store_1", "product_id": "product_1"},
        "idempotency_context": {"operation_id": "operation_1"},
        "permissions": {"inventory.write", "ai.approve", "unrelated.read"},
        "now": NOW + timedelta(seconds=1),
    }


def _approval(
    *,
    decision: Literal["approved", "denied"] = "approved",
    decided_at: datetime = NOW + timedelta(milliseconds=500),
) -> AiAgentApprovalRecord:
    return AiAgentApprovalRecord(
        tool_call_id="call_1",
        decision=decision,
        actor_id="approver_1",
        decided_at=decided_at,
    )


def _claim_pending(
    store: AiAgentStateStore,
    envelope: AiAgentStateEnvelope,
    *,
    lease_seconds: float = 60,
) -> AiAgentStateEnvelope:
    return store.claim_for_resume(
        envelope.state_id,
        approval_records=[_approval()],
        lease_seconds=lease_seconds,
        **_valid_resume_kwargs(),
    )


def test_v1_round_trip_uses_public_pydantic_serializers_and_private_files(
    tmp_path: Path,
) -> None:
    store = AiAgentStateStore(tmp_path)

    created = _create_pending(store)
    loaded = store.load(created.state_id)

    assert loaded.schema_version == AI_AGENT_STATE_SCHEMA_VERSION
    assert loaded.status == "pending"
    assert loaded.revision == 0
    assert loaded.security.required_permissions == {
        "inventory.write",
        "ai.approve",
    }
    assert dict(loaded.references) == {
        "ai_work_id": "conversation_1",
        "invocation_id": "invocation_1",
        "agent_run_id": "run_1",
        "trace_id": "trace_1",
    }
    assert ModelMessagesTypeAdapter.dump_python(
        list(loaded.message_history), mode="json"
    ) == ModelMessagesTypeAdapter.dump_python(
        list(created.message_history), mode="json"
    )
    assert [part.tool_call_id for part in loaded.deferred_requests.approvals] == [
        "call_1"
    ]
    assert loaded.deferred_requests.metadata["call_1"]["tool_name"] == (
        "inventory_update"
    )

    payload = json.loads(store.state_path(created.state_id).read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert set(payload) == {
        "schema_version",
        "state_id",
        "status",
        "revision",
        "profile",
        "timestamps",
        "security",
        "references",
        "message_history",
        "deferred_requests",
        "approval_records",
        "resume_claim",
        "resume_result",
    }
    assert payload["resume_claim"] is None
    assert payload["resume_result"] is None
    assert "provider_instance" not in payload
    assert "model_instance" not in payload
    assert "pickle" not in payload
    if os.name != "nt":
        assert store.root.stat().st_mode & 0o777 == 0o700
        assert store.state_path(created.state_id).stat().st_mode & 0o777 == 0o600


def test_v0_flat_envelope_is_migrated_on_read(tmp_path: Path) -> None:
    store = AiAgentStateStore(tmp_path)
    created = _create_pending(store)
    path = store.state_path(created.state_id)
    current = json.loads(path.read_text(encoding="utf-8"))
    legacy = {
        "schema_version": 0,
        "state_id": current["state_id"],
        "status": "waiting_approval",
        "revision": current["revision"],
        **current["profile"],
        **current["timestamps"],
        **current["security"],
        "references": current["references"],
        "message_history": current["message_history"],
        "deferred_requests": current["deferred_requests"],
        "approval_records": current["approval_records"],
    }
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    loaded = store.load(created.state_id)

    assert loaded.schema_version == 1
    assert loaded.status == "pending"
    assert loaded.use_case_id == "inventory.adjust"
    assert loaded.deferred_requests.approvals[0].tool_call_id == "call_1"


def test_original_v1_without_claim_or_result_fields_remains_readable(
    tmp_path: Path,
) -> None:
    store = AiAgentStateStore(tmp_path)
    created = _create_pending(store)
    path = store.state_path(created.state_id)
    original_v1 = json.loads(path.read_text(encoding="utf-8"))
    original_v1.pop("resume_claim")
    original_v1.pop("resume_result")
    path.write_text(json.dumps(original_v1, ensure_ascii=False), encoding="utf-8")

    loaded = store.load(created.state_id)

    assert loaded.status == "pending"
    assert loaded.resume_claim is None
    assert loaded.resume_result is None


def test_original_v1_resuming_without_claim_recovers_as_in_doubt(
    tmp_path: Path,
) -> None:
    store = AiAgentStateStore(tmp_path)
    created = _create_pending(store)
    path = store.state_path(created.state_id)
    original_v1 = json.loads(path.read_text(encoding="utf-8"))
    original_v1["status"] = "resuming"
    original_v1["revision"] = 1
    original_v1.pop("resume_claim")
    original_v1.pop("resume_result")
    path.write_text(json.dumps(original_v1, ensure_ascii=False), encoding="utf-8")

    recovered = store.recover_expired_claim(
        created.state_id,
        now=NOW + timedelta(seconds=2),
    )

    assert recovered.status == "in_doubt"
    assert recovered.resume_claim is None


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (b"{not-json", "AI_AGENT_STATE_CORRUPT"),
        (
            json.dumps({"schema_version": 99}).encode(),
            "AI_AGENT_STATE_VERSION_UNSUPPORTED",
        ),
    ],
)
def test_corrupt_and_unknown_version_are_stably_rejected(
    tmp_path: Path,
    payload: bytes,
    expected_code: str,
) -> None:
    store = AiAgentStateStore(tmp_path)
    path = store.state_path("broken")
    path.write_bytes(payload)

    with pytest.raises(AiAgentStateError) as caught:
        store.load("broken")

    assert caught.value.code == expected_code
    assert "not-json" not in str(caught.value)
    assert "99" not in str(caught.value)


@pytest.mark.parametrize(
    ("changed", "expected_code"),
    [
        ({"use_case_id": "inventory.other"}, "AI_AGENT_STATE_PROFILE_MISMATCH"),
        ({"profile_version": "inventory_adjust.v2"}, "AI_AGENT_STATE_PROFILE_MISMATCH"),
        ({"toolset_id": "inventory.read"}, "AI_AGENT_STATE_TOOLSET_MISMATCH"),
        ({"tenant_id": "tenant_2"}, "AI_AGENT_STATE_SCOPE_MISMATCH"),
        (
            {"business_scope": {"store_id": "store_2", "product_id": "product_1"}},
            "AI_AGENT_STATE_SCOPE_MISMATCH",
        ),
        (
            {"idempotency_context": {"operation_id": "operation_2"}},
            "AI_AGENT_STATE_IDEMPOTENCY_MISMATCH",
        ),
    ],
)
def test_resume_context_rejects_profile_scope_and_idempotency_mismatch(
    tmp_path: Path,
    changed: dict[str, Any],
    expected_code: str,
) -> None:
    store = AiAgentStateStore(tmp_path)
    envelope = _create_pending(store)
    kwargs = {**_valid_resume_kwargs(), **changed}

    with pytest.raises(AiAgentStateError) as caught:
        validate_resume_context(envelope, **kwargs)

    assert caught.value.code == expected_code


def test_permission_change_is_rejected_without_claiming_state(tmp_path: Path) -> None:
    store = AiAgentStateStore(tmp_path)
    envelope = _create_pending(store)
    kwargs = _valid_resume_kwargs()
    kwargs["permissions"] = {"inventory.write"}

    with pytest.raises(AiAgentStateError) as caught:
        store.claim_for_resume(envelope.state_id, **kwargs)

    assert caught.value.code == "AI_AGENT_STATE_PERMISSION_DENIED"
    assert store.load(envelope.state_id).status == "pending"


def test_expired_deadline_is_rejected_without_claiming_state(tmp_path: Path) -> None:
    store = AiAgentStateStore(tmp_path)
    envelope = _create_pending(store)
    kwargs = _valid_resume_kwargs()
    kwargs["now"] = DEADLINE

    with pytest.raises(AiAgentStateError) as caught:
        store.claim_for_resume(envelope.state_id, **kwargs)

    assert caught.value.code == "AI_AGENT_STATE_DEADLINE_EXCEEDED"
    assert store.load(envelope.state_id).status == "pending"


def test_resume_lease_is_finite_and_bounded(tmp_path: Path) -> None:
    store = AiAgentStateStore(tmp_path)
    envelope = _create_pending(store)

    with pytest.raises(AiAgentStateError) as caught:
        store.claim_for_resume(
            envelope.state_id,
            approval_records=[_approval()],
            lease_seconds=301,
            **_valid_resume_kwargs(),
        )

    assert caught.value.code == "AI_AGENT_STATE_LEASE_INVALID"
    assert store.load(envelope.state_id).status == "pending"


def test_pending_state_can_only_be_claimed_once(tmp_path: Path) -> None:
    store = AiAgentStateStore(tmp_path)
    envelope = _create_pending(store)
    approval = AiAgentApprovalRecord(
        tool_call_id="call_1",
        decision="approved",
        actor_id="approver_1",
        decided_at=NOW + timedelta(milliseconds=500),
    )

    claimed = store.claim_for_resume(
        envelope.state_id,
        approval_records=[approval],
        **_valid_resume_kwargs(),
    )

    assert claimed.status == "resuming"
    assert claimed.revision == 1
    assert claimed.approval_records == (approval,)
    assert claimed.resume_claim is not None
    assert claimed.resume_claim.claim_id.startswith("resume_claim_")
    assert claimed.resume_claim.lease_expires_at == NOW + timedelta(seconds=61)
    with pytest.raises(AiAgentStateError) as caught:
        store.claim_for_resume(envelope.state_id, **_valid_resume_kwargs())
    assert caught.value.code == "AI_AGENT_STATE_ALREADY_CLAIMED"
    assert store.load(envelope.state_id).revision == 1

    ready = store.mark_resume_ready(
        envelope.state_id,
        claim_id=claimed.resume_claim.claim_id,
        message_history=claimed.message_history,
        output_payload={"ok": True, "product_id": "product_1"},
        run_id="run_resume_1",
        attempt_id="attempt_resume_1",
        trace_id="trace_resume_1",
        usage={"requests": 1, "input_tokens": 20, "output_tokens": 8},
        now=NOW + timedelta(seconds=2),
    )
    assert ready.status == "ready"
    completed = store.mark_completed(
        envelope.state_id,
        claim_id=claimed.resume_claim.claim_id,
        now=DEADLINE + timedelta(seconds=1),
    )
    assert completed.status == "completed"
    assert completed.revision == 3


def test_resume_requires_a_persisted_approval_for_every_approval_request(
    tmp_path: Path,
) -> None:
    store = AiAgentStateStore(tmp_path)
    envelope = _create_pending(store)

    with pytest.raises(AiAgentStateError) as caught:
        store.claim_for_resume(envelope.state_id, **_valid_resume_kwargs())

    assert caught.value.code == "AI_AGENT_STATE_APPROVAL_REQUIRED"
    assert store.load(envelope.state_id).status == "pending"


def test_concurrent_resume_claim_has_exactly_one_winner(tmp_path: Path) -> None:
    first_store = AiAgentStateStore(tmp_path)
    second_store = AiAgentStateStore(tmp_path)
    envelope = _create_pending(first_store)
    approval = AiAgentApprovalRecord(
        tool_call_id="call_1",
        decision="approved",
        actor_id="approver_1",
        decided_at=NOW + timedelta(milliseconds=500),
    )
    barrier = threading.Barrier(2)

    def claim(store: AiAgentStateStore) -> str:
        barrier.wait()
        try:
            store.claim_for_resume(
                envelope.state_id,
                approval_records=[approval],
                **_valid_resume_kwargs(),
            )
        except AiAgentStateError as exc:
            return exc.code
        return "claimed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, (first_store, second_store)))

    assert sorted(outcomes) == ["AI_AGENT_STATE_ALREADY_CLAIMED", "claimed"]
    assert first_store.load(envelope.state_id).revision == 1


def test_ready_result_round_trips_and_can_be_replayed_after_original_deadline(
    tmp_path: Path,
) -> None:
    store = AiAgentStateStore(tmp_path)
    envelope = _create_pending(store)
    claimed = _claim_pending(store, envelope)
    assert claimed.resume_claim is not None
    claim_id = claimed.resume_claim.claim_id

    ready = store.mark_resume_ready(
        envelope.state_id,
        claim_id=claim_id,
        message_history=claimed.message_history,
        output_payload=_TypedReadyOutput(
            selected_category_id="category_1",
            abstained=False,
            evidence=["匹配商品主体"],
        ),
        run_id="run_resume_1",
        attempt_id="attempt_resume_1",
        trace_id="trace_resume_1",
        usage={"requests": 2, "input_tokens": 120, "output_tokens": 30},
        now=NOW + timedelta(seconds=2),
    )

    loaded = store.load(envelope.state_id)
    assert loaded.status == "ready"
    assert loaded.resume_result is not None
    assert loaded.resume_result.output_payload["selected_category_id"] == "category_1"
    assert dict(loaded.resume_result.usage) == {
        "requests": 2,
        "input_tokens": 120,
        "output_tokens": 30,
    }
    assert loaded.resume_result.run_id == "run_resume_1"
    assert loaded.resume_result.attempt_id == "attempt_resume_1"
    assert loaded.resume_result.trace_id == "trace_resume_1"
    assert loaded.message_history == loaded.resume_result.message_history
    persisted = json.loads(
        store.state_path(envelope.state_id).read_text(encoding="utf-8")
    )
    assert persisted["status"] == "ready"
    assert "message_history" not in persisted["resume_result"]

    replayed = store.load_ready_for_replay(
        envelope.state_id,
        **{key: value for key, value in _valid_resume_kwargs().items() if key != "now"},
    )
    assert replayed.resume_result == ready.resume_result
    validate_ready_context(
        replayed,
        **{key: value for key, value in _valid_resume_kwargs().items() if key != "now"},
    )

    denied_context = {
        key: value for key, value in _valid_resume_kwargs().items() if key != "now"
    }
    denied_context["permissions"] = {"inventory.write"}
    with pytest.raises(AiAgentStateError) as denied:
        store.load_ready_for_replay(envelope.state_id, **denied_context)
    assert denied.value.code == "AI_AGENT_STATE_PERMISSION_DENIED"


def test_ready_requires_matching_live_claim(tmp_path: Path) -> None:
    store = AiAgentStateStore(tmp_path)
    envelope = _create_pending(store)
    claimed = _claim_pending(store, envelope, lease_seconds=2)
    assert claimed.resume_claim is not None

    ready_kwargs = {
        "message_history": claimed.message_history,
        "output_payload": {"ok": True},
        "run_id": "run_resume_1",
        "attempt_id": "attempt_resume_1",
        "trace_id": "trace_resume_1",
        "usage": {"requests": 1},
    }
    with pytest.raises(AiAgentStateError) as mismatch:
        store.mark_resume_ready(
            envelope.state_id,
            claim_id="resume_claim_wrong",
            now=NOW + timedelta(seconds=2),
            **ready_kwargs,
        )
    assert mismatch.value.code == "AI_AGENT_STATE_CLAIM_MISMATCH"

    with pytest.raises(AiAgentStateError) as expired:
        store.mark_resume_ready(
            envelope.state_id,
            claim_id=claimed.resume_claim.claim_id,
            now=NOW + timedelta(seconds=3),
            **ready_kwargs,
        )
    assert expired.value.code == "AI_AGENT_STATE_LEASE_EXPIRED"
    assert store.load(envelope.state_id).status == "resuming"


def test_expired_claim_without_tool_execution_returns_to_pending_and_reclaims(
    tmp_path: Path,
) -> None:
    store = AiAgentStateStore(tmp_path)
    envelope = _create_pending(store)
    claimed = _claim_pending(store, envelope, lease_seconds=2)
    assert claimed.resume_claim is not None

    with pytest.raises(AiAgentStateError) as active:
        store.recover_expired_claim(
            envelope.state_id,
            now=NOW + timedelta(seconds=2),
        )
    assert active.value.code == "AI_AGENT_STATE_LEASE_ACTIVE"

    recovered = store.recover_expired_claim(
        envelope.state_id,
        now=NOW + timedelta(seconds=3),
    )

    assert recovered.status == "pending"
    assert recovered.resume_claim is None
    assert recovered.approval_records == ()
    reclaimed = store.claim_for_resume(
        envelope.state_id,
        approval_records=[
            _approval(decided_at=NOW + timedelta(seconds=3, milliseconds=100))
        ],
        lease_seconds=2,
        **{**_valid_resume_kwargs(), "now": NOW + timedelta(seconds=4)},
    )
    assert reclaimed.status == "resuming"
    assert reclaimed.resume_claim is not None
    assert reclaimed.resume_claim.claim_id != claimed.resume_claim.claim_id


def test_expired_claim_after_tool_started_becomes_terminal_in_doubt(
    tmp_path: Path,
) -> None:
    store = AiAgentStateStore(tmp_path)
    envelope = _create_pending(store)
    claimed = _claim_pending(store, envelope, lease_seconds=3)
    assert claimed.resume_claim is not None
    claim_id = claimed.resume_claim.claim_id

    started = store.mark_tool_execution_started(
        envelope.state_id,
        claim_id=claim_id,
        now=NOW + timedelta(seconds=2),
    )
    assert started.resume_claim is not None
    assert started.resume_claim.tool_execution_started_at == NOW + timedelta(seconds=2)

    recovered = store.recover_expired_claim(
        envelope.state_id,
        now=NOW + timedelta(seconds=4),
    )
    assert recovered.status == "in_doubt"
    assert recovered.resume_claim is not None
    with pytest.raises(AiAgentStateError) as repeated:
        store.claim_for_resume(
            envelope.state_id,
            approval_records=[_approval()],
            **_valid_resume_kwargs(),
        )
    assert repeated.value.code == "AI_AGENT_STATE_ALREADY_CLAIMED"


def test_expired_claim_at_total_deadline_becomes_failed_not_orphan_pending(
    tmp_path: Path,
) -> None:
    store = AiAgentStateStore(tmp_path)
    envelope = _create_pending(store)
    claimed = _claim_pending(store, envelope, lease_seconds=300)
    assert claimed.resume_claim is not None
    assert claimed.resume_claim.lease_expires_at == DEADLINE

    recovered = store.recover_expired_claim(envelope.state_id, now=DEADLINE)

    assert recovered.status == "failed"
    assert recovered.resume_claim == claimed.resume_claim


def test_retry_release_only_returns_to_pending_before_tool_execution(
    tmp_path: Path,
) -> None:
    store = AiAgentStateStore(tmp_path)
    safe_envelope = _create_pending(store, state_id="safe_retry")
    safe_claim = _claim_pending(store, safe_envelope)
    assert safe_claim.resume_claim is not None

    released = store.release_claim_for_retry(
        safe_envelope.state_id,
        claim_id=safe_claim.resume_claim.claim_id,
        now=NOW + timedelta(seconds=2),
    )
    assert released.status == "pending"
    assert released.resume_claim is None
    assert released.approval_records == ()

    unsafe_envelope = _create_pending(store, state_id="unsafe_retry")
    unsafe_claim = _claim_pending(store, unsafe_envelope)
    assert unsafe_claim.resume_claim is not None
    store.mark_tool_execution_started(
        unsafe_envelope.state_id,
        claim_id=unsafe_claim.resume_claim.claim_id,
        now=NOW + timedelta(seconds=2),
    )
    unsafe_release = store.release_claim_for_retry(
        unsafe_envelope.state_id,
        claim_id=unsafe_claim.resume_claim.claim_id,
        now=NOW + timedelta(seconds=3),
    )
    assert unsafe_release.status == "in_doubt"


def test_redeferred_resume_requires_claim_and_clears_it_for_next_approval(
    tmp_path: Path,
) -> None:
    store = AiAgentStateStore(tmp_path)
    envelope = _create_pending(store)
    claimed = _claim_pending(store, envelope)
    assert claimed.resume_claim is not None
    next_deferred = DeferredToolRequests(
        approvals=[ToolCallPart("inventory_update", {"quantity": 4}, "call_2")]
    )

    with pytest.raises(AiAgentStateError) as mismatch:
        store.replace_pending_after_resume(
            envelope.state_id,
            claim_id="resume_claim_wrong",
            message_history=claimed.message_history,
            deferred_requests=next_deferred,
            now=NOW + timedelta(seconds=2),
        )
    assert mismatch.value.code == "AI_AGENT_STATE_CLAIM_MISMATCH"

    pending = store.replace_pending_after_resume(
        envelope.state_id,
        claim_id=claimed.resume_claim.claim_id,
        message_history=claimed.message_history,
        deferred_requests=next_deferred,
        now=NOW + timedelta(seconds=2),
    )
    assert pending.status == "pending"
    assert pending.resume_claim is None
    assert [record.tool_call_id for record in pending.approval_records] == ["call_1"]

    next_claim = store.claim_for_resume(
        envelope.state_id,
        approval_records=[
            AiAgentApprovalRecord(
                tool_call_id="call_2",
                decision="approved",
                actor_id="approver_1",
                decided_at=NOW + timedelta(seconds=3),
            )
        ],
        **{**_valid_resume_kwargs(), "now": NOW + timedelta(seconds=3)},
    )
    assert next_claim.status == "resuming"
    assert {record.tool_call_id for record in next_claim.approval_records} == {
        "call_1",
        "call_2",
    }


def test_mark_failed_handles_pending_resuming_ready_without_reopening_terminal(
    tmp_path: Path,
) -> None:
    store = AiAgentStateStore(tmp_path)
    pending = _create_pending(store, state_id="pending_failure")
    failed_pending = store.mark_failed(
        pending.state_id,
        now=NOW + timedelta(seconds=1),
    )
    assert failed_pending.status == "failed"

    resuming = _create_pending(store, state_id="resuming_failure")
    resuming_claim = _claim_pending(store, resuming)
    assert resuming_claim.resume_claim is not None
    failed_resuming = store.mark_failed(
        resuming.state_id,
        claim_id=resuming_claim.resume_claim.claim_id,
        now=NOW + timedelta(seconds=2),
    )
    assert failed_resuming.status == "failed"

    ready_source = _create_pending(store, state_id="ready_failure")
    ready_claim = _claim_pending(store, ready_source)
    assert ready_claim.resume_claim is not None
    ready = store.mark_resume_ready(
        ready_source.state_id,
        claim_id=ready_claim.resume_claim.claim_id,
        message_history=ready_claim.message_history,
        output_payload={"ok": True},
        run_id="run_ready",
        attempt_id="attempt_ready",
        trace_id="trace_ready",
        usage={"requests": 1},
        now=NOW + timedelta(seconds=2),
    )
    failed_ready = store.mark_failed(
        ready_source.state_id,
        claim_id=ready_claim.resume_claim.claim_id,
        now=NOW + timedelta(seconds=3),
    )
    assert ready.status == "ready"
    assert failed_ready.status == "failed"
    assert failed_ready.resume_result == ready.resume_result
    with pytest.raises(AiAgentStateError) as terminal:
        store.mark_failed(
            ready_source.state_id,
            claim_id=ready_claim.resume_claim.claim_id,
            now=NOW + timedelta(seconds=4),
        )
    assert terminal.value.code == "AI_AGENT_STATE_STATUS_CONFLICT"

    unsafe = _create_pending(store, state_id="unsafe_resuming_failure")
    unsafe_claim = _claim_pending(store, unsafe)
    assert unsafe_claim.resume_claim is not None
    store.mark_tool_execution_started(
        unsafe.state_id,
        claim_id=unsafe_claim.resume_claim.claim_id,
        now=NOW + timedelta(seconds=2),
    )
    in_doubt = store.mark_failed(
        unsafe.state_id,
        claim_id=unsafe_claim.resume_claim.claim_id,
        now=NOW + timedelta(seconds=3),
    )
    assert in_doubt.status == "in_doubt"


def test_denial_is_terminal_and_records_approver(tmp_path: Path) -> None:
    store = AiAgentStateStore(tmp_path)
    envelope = _create_pending(store)
    denial = AiAgentApprovalRecord(
        tool_call_id="call_1",
        decision="denied",
        actor_id="approver_1",
        decided_at=NOW + timedelta(seconds=1),
    )

    resume_context = _valid_resume_kwargs()
    resume_context["now"] = NOW + timedelta(seconds=1)
    denied = store.mark_denied(
        envelope.state_id,
        approval_records=[denial],
        **resume_context,
    )

    assert denied.status == "denied"
    assert denied.approval_records == (denial,)
    with pytest.raises(AiAgentStateError) as caught:
        store.mark_completed(
            envelope.state_id,
            claim_id="resume_claim_unknown",
            now=NOW + timedelta(seconds=2),
        )
    assert caught.value.code == "AI_AGENT_STATE_STATUS_CONFLICT"


def test_sensitive_scope_and_write_failures_do_not_leak_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AiAgentStateStore(tmp_path)
    secret = "sk-do-not-leak"

    with pytest.raises(AiAgentStateError) as sensitive:
        store.create_pending(
            state_id="sensitive",
            use_case_id="inventory.adjust",
            profile_version="inventory_adjust.v1",
            toolset_id="inventory.write",
            deadline_at=DEADLINE,
            actor_id="user_1",
            tenant_id="tenant_1",
            required_permissions={"inventory.write"},
            business_scope={"api_key": secret},
            idempotency_context={"operation_id": "operation_1"},
            message_history=_messages(),
            deferred_requests=_deferred(),
            now=NOW,
        )
    assert sensitive.value.code == "AI_AGENT_STATE_SENSITIVE_DATA_REJECTED"
    assert secret not in str(sensitive.value)

    def fail_replace(source: Path, target: Path) -> None:
        del source, target
        raise OSError(secret)

    monkeypatch.setattr(
        "erp_web.services.ai_agent_state_store.os.replace",
        fail_replace,
    )
    with pytest.raises(AiAgentStateError) as write_failure:
        _create_pending(store, state_id="write_failure")
    assert write_failure.value.code == "AI_AGENT_STATE_WRITE_FAILED"
    assert secret not in str(write_failure.value)
    assert not store.state_path("write_failure").exists()
    assert not list(store.root.glob("*.tmp"))
