"""AiPresentationRegistry：reservation/claim、lease、chunk 缓冲与 request 收尾。"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from erp_web.services.ai_presentation_registry import (
    BOUND,
    BUFFER_OVERFLOW_CODE,
    COMPLETED,
    EXPIRED,
    FAILED,
    FINALIZING,
    RESERVED,
    RUNNING,
    TERMINAL_STATUSES,
    AiPresentationRegistry,
)


def _reserve(registry: AiPresentationRegistry, presentation_id: str = "presentation_a") -> None:
    assert registry.reserve(
        presentation_id=presentation_id,
        conversation_id=f"conversation_{presentation_id}",
        display_title="AI 填充属性",
    )


def test_reserve_is_atomic_and_descriptor_is_public() -> None:
    registry = AiPresentationRegistry()
    _reserve(registry)

    assert (
        registry.reserve(
            presentation_id="presentation_a",
            conversation_id="other",
            display_title="other",
        )
        is False
    )
    assert (
        registry.reserve(presentation_id="   ", conversation_id="", display_title="")
        is False
    )

    assert registry.descriptor("presentation_a") == {
        "presentation_id": "presentation_a",
        "conversation_id": "conversation_presentation_a",
        "display_title": "AI 填充属性",
        "status": RESERVED,
    }
    assert registry.descriptor("presentation_missing") is None


def test_claim_happens_once_and_rejects_unknown_or_claimed() -> None:
    registry = AiPresentationRegistry()
    _reserve(registry)

    assert registry.claim("presentation_missing") is False
    assert registry.claim("presentation_a") is True
    assert registry.descriptor("presentation_a")["status"] == BOUND
    # 同一 presentation 只能 claim 一次。
    assert registry.claim("presentation_a") is False


def test_claim_of_expired_reservation_marks_expired() -> None:
    registry = AiPresentationRegistry(reservation_ttl_seconds=60.0)
    _reserve(registry)
    state = registry._presentations["presentation_a"]  # noqa: SLF001 - 测试注入过期时间
    state.reserved_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert registry.claim("presentation_a") is False
    assert registry.status_payload("presentation_a")["status"] == EXPIRED
    assert registry.is_terminal("presentation_a") is True


def test_cleanup_expires_reservations_and_removes_old_terminals() -> None:
    registry = AiPresentationRegistry(ttl_seconds=0.05, reservation_ttl_seconds=60.0)
    _reserve(registry, "presentation_expired_reservation")
    _reserve(registry, "presentation_old_terminal")
    _reserve(registry, "presentation_live")
    registry.claim("presentation_old_terminal")
    registry.finish_request("presentation_old_terminal")
    assert registry.status_payload("presentation_old_terminal")["status"] == COMPLETED

    state = registry._presentations["presentation_old_terminal"]  # noqa: SLF001
    state.terminal_at = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    reserved = registry._presentations["presentation_expired_reservation"]  # noqa: SLF001
    reserved.reserved_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    removed = registry.cleanup_expired()
    assert removed == 2
    assert registry.status_payload("presentation_expired_reservation")["status"] == EXPIRED
    assert registry.status_payload("presentation_old_terminal") is None
    assert registry.status_payload("presentation_live") is not None


def test_single_lease_and_expired_or_unknown_rejected() -> None:
    registry = AiPresentationRegistry(reservation_ttl_seconds=60.0)
    _reserve(registry)

    assert registry.acquire_lease("presentation_missing") is False
    assert registry.acquire_lease("presentation_a") is True
    assert registry.acquire_lease("presentation_a") is False
    registry.release_lease("presentation_a")
    assert registry.acquire_lease("presentation_a") is True
    registry.release_lease("presentation_missing")  # 幂等

    _reserve(registry, "presentation_expired")
    state = registry._presentations["presentation_expired"]  # noqa: SLF001
    state.reserved_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    registry.cleanup_expired()
    assert registry.acquire_lease("presentation_expired") is False


def test_claim_root_run_is_atomic_single_slot() -> None:
    registry = AiPresentationRegistry()
    _reserve(registry)
    registry.claim("presentation_a")

    # 首个顺序 Agent 领取成功，返回自己的 run_id。
    assert registry.claim_root_run("presentation_a", "run_first") == "run_first"
    # 同一请求内第二个顺序 Agent 领取返回已领取的 root，保证最多一个根流。
    assert registry.claim_root_run("presentation_a", "run_second") == "run_first"
    # 同一 root run 幂等重领仍返回自己。
    assert registry.claim_root_run("presentation_a", "run_first") == "run_first"

    # 空 run_id、未知 presentation 返回空（调用方放弃展示关联）。
    assert registry.claim_root_run("presentation_a", "   ") == ""
    assert registry.claim_root_run("presentation_a", "") == ""
    assert registry.claim_root_run("presentation_missing", "run_x") == ""

    # 过期 presentation 返回空。
    _reserve(registry, "presentation_expired_root")
    state = registry._presentations["presentation_expired_root"]  # noqa: SLF001
    state.reserved_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    registry.cleanup_expired()
    assert registry.claim_root_run("presentation_expired_root", "run_y") == ""


def test_mark_agent_started_and_status_progression() -> None:
    registry = AiPresentationRegistry()
    _reserve(registry)
    registry.claim("presentation_a")

    registry.mark_agent_started("presentation_a")
    assert registry.had_agent_run("presentation_a") is True
    assert registry.status_payload("presentation_a")["status"] == RUNNING

    registry.update_status("presentation_a", FINALIZING)
    assert registry.status_payload("presentation_a")["status"] == FINALIZING

    registry.finish_request("presentation_a")
    # 终态后生命周期事件不再改写状态。
    registry.mark_agent_started("presentation_a")
    registry.update_status("presentation_a", RUNNING)
    assert registry.status_payload("presentation_a")["status"] == COMPLETED

    registry.mark_agent_started("presentation_missing")  # 未知 ID 不抛异常


def test_publish_chunks_until_closed_and_overflow_fails_explicitly() -> None:
    registry = AiPresentationRegistry()
    _reserve(registry)
    registry.claim("presentation_a")

    assert registry.publish_chunk("presentation_a", b"data: one\n\n") is True
    registry.finish_request("presentation_a")
    # 关闭后发布被忽略。
    assert registry.publish_chunk("presentation_a", b"data: late\n\n") is False

    overflow = AiPresentationRegistry(max_buffered_chunks=1)
    _reserve(overflow, "presentation_overflow")
    assert overflow.publish_chunk("presentation_overflow", b"data: 1\n\n") is True
    assert overflow.publish_chunk("presentation_overflow", b"data: 2\n\n") is False
    payload = overflow.status_payload("presentation_overflow")
    assert payload["status"] == FAILED
    assert payload["error_code"] == BUFFER_OVERFLOW_CODE
    assert payload["terminal"] is True
    # 溢出后继续发布仍被拒绝。
    assert overflow.publish_chunk("presentation_overflow", b"data: 3\n\n") is False


def test_finish_request_closes_empty_presentation_deterministically() -> None:
    registry = AiPresentationRegistry()
    _reserve(registry)
    registry.claim("presentation_a")

    # 整个请求未产生 Agent run：SSE 必须确定结束，不得永久等待。
    registry.finish_request("presentation_a")
    payload = registry.status_payload("presentation_a")
    assert payload["status"] == COMPLETED
    assert payload["had_agent_run"] is False
    assert registry.is_terminal("presentation_a") is True
    chunks, _cursor, closed = registry.read_chunks("presentation_a", 0, wait_timeout=0.05)
    assert chunks == [] and closed is True


def test_finish_request_failed_requires_sanitized_error() -> None:
    registry = AiPresentationRegistry()
    _reserve(registry)
    registry.claim("presentation_a")
    registry.mark_agent_started("presentation_a")

    registry.finish_request(
        "presentation_a",
        request_failed=True,
        error_code="AI_AGENT_RUN_FAILED",
        error_message="模型服务不可用",
    )
    payload = registry.status_payload("presentation_a")
    assert payload["status"] == FAILED
    assert payload["error_code"] == "AI_AGENT_RUN_FAILED"
    assert payload["error_message"] == "模型服务不可用"

    # 已终态：再次收尾不覆盖。
    registry.finish_request("presentation_a")
    assert registry.status_payload("presentation_a")["status"] == FAILED


def test_read_chunks_replays_and_waits_across_threads() -> None:
    registry = AiPresentationRegistry()
    _reserve(registry)
    registry.claim("presentation_a")
    registry.publish_chunk("presentation_a", b"data: one\n\n")

    chunks, cursor, closed = registry.read_chunks("presentation_a", 0, wait_timeout=0.05)
    assert chunks == [b"data: one\n\n"] and cursor == 1 and closed is False

    # 没有新 chunk 且未关闭：超时返回空，不阻塞订阅循环。
    chunks, cursor, closed = registry.read_chunks("presentation_a", cursor, wait_timeout=0.05)
    assert chunks == [] and closed is False

    def publisher() -> None:
        time.sleep(0.05)
        registry.publish_chunk("presentation_a", b"data: two\n\n")
        time.sleep(0.02)
        registry.finish_request("presentation_a")

    thread = threading.Thread(target=publisher)
    thread.start()
    received = list(registry.iter_chunks("presentation_a", wait_timeout=0.5))
    thread.join()

    assert received == [b"data: one\n\n", b"data: two\n\n"]
    # 迟到订阅者仍能完整 replay。
    assert list(registry.iter_chunks("presentation_a")) == received


def test_read_chunks_unknown_or_expired_reservation_is_closed() -> None:
    registry = AiPresentationRegistry()
    chunks, _cursor, closed = registry.read_chunks("presentation_missing", 0, wait_timeout=0.05)
    assert chunks == [] and closed is True

    _reserve(registry, "presentation_expired")
    state = registry._presentations["presentation_expired"]  # noqa: SLF001
    state.reserved_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    # 订阅者等待期间就地过期：流确定关闭，不依赖外部清理。
    chunks, _cursor, closed = registry.read_chunks(
        "presentation_expired", 0, wait_timeout=0.05
    )
    assert chunks == [] and closed is True
    assert registry.status_payload("presentation_expired")["status"] == EXPIRED


def test_status_payload_is_metadata_only() -> None:
    registry = AiPresentationRegistry()
    assert registry.status_payload("presentation_missing") is None

    _reserve(registry)
    payload = registry.status_payload("presentation_a")
    assert payload == {
        "presentation_id": "presentation_a",
        "conversation_id": "conversation_presentation_a",
        "display_title": "AI 填充属性",
        "status": RESERVED,
        "terminal": False,
        "had_agent_run": False,
        "error_code": "",
        "error_message": "",
    }
    # 通用状态读取不拥有业务结果。
    assert "result" not in payload

    assert registry.conversation_id("presentation_a") == "conversation_presentation_a"
    assert registry.conversation_id("presentation_missing") == ""

    assert TERMINAL_STATUSES == frozenset({COMPLETED, FAILED, EXPIRED})
    with pytest.raises(ValueError):
        registry.mark_terminal(presentation_id="presentation_a", status=RUNNING)
