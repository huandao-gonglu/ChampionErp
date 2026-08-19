from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from erp_web.db import ErpDatabase
from erp_web.services.ai_chat_run_registry import AiChatRunRegistry
from erp_web.stores.ai_chat_turn_claim_store import (
    CANCELLED,
    CLAIMED,
    COMPLETED,
    FAILED,
    TERMINAL_CLAIM_STATUSES,
    AiChatTurnAlreadyAcceptedError,
    AiChatTurnClaimError,
    AiChatTurnClaimStore,
)


CONVERSATION = "conversation_global_chat_" + "a" * 32


def _store(tmp_path: Path) -> AiChatTurnClaimStore:
    return AiChatTurnClaimStore(ErpDatabase(tmp_path / "erp.sqlite3"))


def test_claim_inserts_claimed_row_with_control_metadata_only(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    claim = store.claim_turn(
        conversation_id=CONVERSATION,
        client_message_id="ui-message-1",
        profile_id="global.chat",
    )

    assert claim.status == CLAIMED
    assert claim.profile_id == "global.chat"
    assert claim.actor_id == "local-user"
    assert claim.tenant_id == "local"
    assert claim.claimed_at
    assert claim.finished_at == ""


def test_claim_table_has_no_message_content_columns(tmp_path: Path) -> None:
    db = ErpDatabase(tmp_path / "erp.sqlite3")

    with db._connect() as conn:
        columns = {
            str(row[1])
            for row in conn.execute('PRAGMA table_info("ai_chat_turn_claims")')
        }

    banned = {
        "message",
        "messages",
        "messages_json",
        "prompt",
        "response",
        "event",
        "events",
        "task",
        "task_id",
        "parent",
        "part",
        "parts",
        "tool",
    }
    assert not (columns & banned)
    assert columns == {
        "claim_id",
        "conversation_id",
        "client_message_id",
        "profile_id",
        "actor_id",
        "tenant_id",
        "status",
        "claimed_at",
        "finished_at",
        "error_code",
        "trace_id",
        "last_tool_name",
    }


def test_duplicate_claim_is_rejected_with_stable_error_code(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.claim_turn(
        conversation_id=CONVERSATION,
        client_message_id="ui-message-1",
        profile_id="global.chat",
    )

    with pytest.raises(AiChatTurnAlreadyAcceptedError) as caught:
        store.claim_turn(
            conversation_id=CONVERSATION,
            client_message_id="ui-message-1",
            profile_id="global.chat",
        )

    assert caught.value.code == "AI_CHAT_TURN_ALREADY_ACCEPTED"


def test_distinct_client_message_ids_can_claim_same_conversation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = store.claim_turn(
        conversation_id=CONVERSATION,
        client_message_id="ui-message-1",
        profile_id="global.chat",
    )
    second = store.claim_turn(
        conversation_id=CONVERSATION,
        client_message_id="ui-message-2",
        profile_id="global.chat",
    )

    assert first.claim_id != second.claim_id


@pytest.mark.parametrize("status", sorted(TERMINAL_CLAIM_STATUSES))
def test_finish_turn_moves_claimed_to_each_terminal_status(
    tmp_path: Path,
    status: str,
) -> None:
    store = _store(tmp_path)
    claim = store.claim_turn(
        conversation_id=CONVERSATION,
        client_message_id="ui-message-1",
        profile_id="global.chat",
    )

    finished = store.finish_turn(claim.claim_id, status=status)

    assert finished is not None
    assert finished.status == status
    assert finished.finished_at


def test_failed_turn_records_safe_structured_diagnostics(tmp_path: Path) -> None:
    store = _store(tmp_path)
    claim = store.claim_turn(
        conversation_id=CONVERSATION,
        client_message_id="ui-message-failed",
        profile_id="global.chat",
    )

    finished = store.finish_turn(
        claim.claim_id,
        status=FAILED,
        error_code="PRODUCT_NOT_FOUND",
        trace_id="trace-safe-1",
        last_tool_name="product_read",
    )

    assert finished is not None
    assert finished.error_code == "PRODUCT_NOT_FOUND"
    assert finished.trace_id == "trace-safe-1"
    assert finished.last_tool_name == "product_read"


def test_finish_turn_rejects_non_terminal_status(tmp_path: Path) -> None:
    store = _store(tmp_path)
    claim = store.claim_turn(
        conversation_id=CONVERSATION,
        client_message_id="ui-message-1",
        profile_id="global.chat",
    )

    with pytest.raises(AiChatTurnClaimError):
        store.finish_turn(claim.claim_id, status=CLAIMED)


def test_finish_turn_is_idempotent_after_terminal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    claim = store.claim_turn(
        conversation_id=CONVERSATION,
        client_message_id="ui-message-1",
        profile_id="global.chat",
    )
    store.finish_turn(claim.claim_id, status=COMPLETED)

    # 已经处于终态的领取不能被再次推进。
    assert store.finish_turn(claim.claim_id, status=FAILED) is None
    assert store.get(CONVERSATION, "ui-message-1").status == COMPLETED


def test_get_returns_claim_by_idempotency_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.claim_turn(
        conversation_id=CONVERSATION,
        client_message_id="ui-message-1",
        profile_id="global.chat",
    )

    assert store.get(CONVERSATION, "ui-message-1") is not None
    assert store.get(CONVERSATION, "missing") is None


def test_find_for_conversation_returns_latest_claim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.claim_turn(
        conversation_id=CONVERSATION,
        client_message_id="ui-message-1",
        profile_id="global.chat",
    )
    latest = store.claim_turn(
        conversation_id=CONVERSATION,
        client_message_id="ui-message-2",
        profile_id="global.chat",
    )

    found = store.find_for_conversation(CONVERSATION)
    assert found is not None
    assert found.claim_id == latest.claim_id
    assert store.find_for_conversation("conversation_global_chat_" + "b" * 32) is None


def test_run_registry_acquire_release_is_atomic_per_conversation() -> None:
    registry = AiChatRunRegistry()

    assert registry.acquire(CONVERSATION) is True
    assert registry.is_active(CONVERSATION) is True
    assert registry.acquire(CONVERSATION) is False

    registry.release(CONVERSATION)
    assert registry.is_active(CONVERSATION) is False
    # 释放后可以再次领取。
    assert registry.acquire(CONVERSATION) is True
    # 不同 conversation 互不影响。
    other = "conversation_global_chat_" + "c" * 32
    assert registry.acquire(other) is True
    # 幂等释放未领取的 ID 不抛错。
    registry.release("conversation_global_chat_" + "d" * 32)
