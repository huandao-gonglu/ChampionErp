"""AiPresentationContext：root/child 派生规则与 contextvar 绑定。"""

from __future__ import annotations

import asyncio

import pytest

from erp_web.services.ai_presentation_context import (
    AiNativeEventPublisher,
    AiRunObserver,
    AiPresentationContext,
    NullAiRunObserver,
    bind_presentation_context,
    current_presentation_context,
    root_presentation_context,
)


def _root(**overrides) -> AiPresentationContext:
    kwargs = {
        "presentation_id": "presentation_a",
        "root_run_id": "run_root",
        "conversation_id": "conversation_a",
        "origin": "business.ui",
    }
    kwargs.update(overrides)
    return root_presentation_context(**kwargs)


def test_root_scope_requires_ids_and_defaults_null_observer() -> None:
    with pytest.raises(ValueError):
        root_presentation_context(
            presentation_id="",
            root_run_id="run_x",
            conversation_id="",
            origin="business.ui",
        )
    with pytest.raises(ValueError):
        root_presentation_context(
            presentation_id="presentation_a",
            root_run_id="",
            conversation_id="",
            origin="business.ui",
        )

    root = _root()
    assert root.is_root_scope is True
    assert root.is_root_run is False
    assert root.is_child_run is False
    assert root.run_id == "" and root.parent_run_id == ""
    assert isinstance(root.observer, NullAiRunObserver)


def test_derive_agent_run_only_from_empty_root_scope() -> None:
    root = _root()
    agent = root.derive_agent_run("attempt_1")
    assert agent.run_id == "attempt_1"
    assert agent.parent_run_id == ""
    assert agent.is_root_run is True
    assert agent.root_run_id == "run_root"
    assert agent.presentation_id == "presentation_a"

    with pytest.raises(ValueError):
        agent.derive_agent_run("attempt_2")
    with pytest.raises(ValueError):
        root.derive_agent_run("   ")


def test_derive_child_run_inherits_presentation_and_observer() -> None:
    observer = NullAiRunObserver()
    root = _root(observer=observer)
    agent = root.derive_agent_run("attempt_1")

    with pytest.raises(ValueError):
        root.derive_child_run("attempt_child")  # 尚无 root Agent

    child = agent.derive_child_run("attempt_child")
    assert child.is_child_run is True
    assert child.run_id == "attempt_child"
    assert child.parent_run_id == "attempt_1"
    assert child.root_run_id == "run_root"
    assert child.presentation_id == "presentation_a"
    assert child.conversation_id == "conversation_a"
    assert child.observer is observer

    grandchild = child.derive_child_run("attempt_grandchild")
    assert grandchild.parent_run_id == "attempt_child"
    assert grandchild.root_run_id == "run_root"

    with pytest.raises(ValueError):
        agent.derive_child_run("")


def test_null_observer_claim_root_run_returns_self() -> None:
    observer = NullAiRunObserver()
    # 无展示订阅者：root 语义保留，直接返回自身 run_id。
    assert observer.claim_root_run(run_id="attempt_1") == "attempt_1"
    assert observer.claim_root_run(run_id="") == ""


def test_derive_child_of_claimed_root_from_root_scope_only() -> None:
    observer = NullAiRunObserver()
    root = _root(observer=observer)

    child = root.derive_child_of_claimed_root(
        run_id="attempt_2",
        parent_run_id="attempt_1",
    )
    assert child.is_child_run is True
    assert child.run_id == "attempt_2"
    assert child.parent_run_id == "attempt_1"
    assert child.root_run_id == "run_root"
    assert child.presentation_id == "presentation_a"
    assert child.conversation_id == "conversation_a"
    assert child.observer is observer

    # 已派生 root Agent 的上下文不能再派生 claimed-root child。
    agent = root.derive_agent_run("attempt_1")
    with pytest.raises(ValueError):
        agent.derive_child_of_claimed_root(
            run_id="attempt_3", parent_run_id="attempt_1"
        )
    with pytest.raises(ValueError):
        root.derive_child_of_claimed_root(run_id="", parent_run_id="attempt_1")
    with pytest.raises(ValueError):
        root.derive_child_of_claimed_root(run_id="attempt_3", parent_run_id="")


def test_bind_presentation_context_scopes_and_restores() -> None:
    assert current_presentation_context() is None
    root = _root()

    with bind_presentation_context(root) as bound:
        assert bound is root
        assert current_presentation_context() is root
        child = root.derive_agent_run("attempt_1")
        with bind_presentation_context(child):
            assert current_presentation_context() is child
        assert current_presentation_context() is root

    assert current_presentation_context() is None

    with pytest.raises(RuntimeError):
        with bind_presentation_context(root):
            raise RuntimeError("boom")
    # 异常退出后仍恢复原值。
    assert current_presentation_context() is None


def test_context_is_thread_local_in_practice() -> None:
    """contextvar 不在未显式传播的线程中泄漏。"""

    import threading

    root = _root()
    observed: list[AiPresentationContext | None] = []

    def worker() -> None:
        observed.append(current_presentation_context())

    with bind_presentation_context(root):
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        assert current_presentation_context() is root

    assert observed == [None]


def test_null_observer_lifecycle_is_noop_and_satisfies_protocol() -> None:
    observer = NullAiRunObserver()
    assert isinstance(observer, AiRunObserver)
    assert isinstance(observer, AiNativeEventPublisher)

    observer.run_started(
        run_id="r", parent_run_id="", use_case_id="u", label="l"
    )
    observer.running(run_id="r")
    observer.tool_activity(run_id="r", tool_name="t")
    observer.finalizing(run_id="r")
    observer.completed(run_id="r")
    observer.failed(run_id="r", code="C", message="m")
    observer.cancelled(run_id="r")
    observer.child_status(child_run_id="c", status="running", label="l")


def test_null_observer_passthrough_keeps_events_and_errors() -> None:
    observer = NullAiRunObserver()

    async def events():
        yield 1
        yield 2

    async def consume():
        published = observer.observe_native_events(events())
        return [event async for event in published]

    assert asyncio.run(consume()) == [1, 2]

    async def failing():
        yield 1
        raise RuntimeError("agent failed")

    async def consume_failure():
        published = observer.observe_native_events(failing())
        received = []
        with pytest.raises(RuntimeError):
            async for event in published:
                received.append(event)
        return received

    assert asyncio.run(consume_failure()) == [1]
