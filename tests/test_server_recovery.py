from __future__ import annotations

import threading

from erp_web import server
from erp_web.facades import ai_chat_facade


def test_global_task_recovery_runs_repeatedly_in_daemon_thread(monkeypatch) -> None:
    called = threading.Event()
    stopped = threading.Event()
    calls = 0
    context = object()

    class Controller:
        def recover_unfinished_tasks(self):
            nonlocal calls
            calls += 1
            called.set()
            if calls >= 2:
                stopped.set()
            return []

    class Continuation:
        def sweep_provisional_links(self, *, ttl_seconds):
            return 0

        def recover_pending(self, *, limit=50):
            return 0

    class OutboxPublisher:
        def publish_pending(self, *, limit=200):
            return 0

    monkeypatch.setattr(server, "get_context", lambda: context)
    monkeypatch.setattr(
        server,
        "build_global_task_controller",
        lambda received: (
            Controller()
            if received is context
            else (_ for _ in ()).throw(AssertionError("恢复使用了错误 AppContext"))
        ),
    )
    monkeypatch.setattr(
        ai_chat_facade,
        "build_continuation_service",
        lambda received: (
            Continuation()
            if received is context
            else (_ for _ in ()).throw(AssertionError("恢复使用了错误 AppContext"))
        ),
    )
    monkeypatch.setattr(
        ai_chat_facade,
        "build_outbox_publisher",
        lambda received: (
            OutboxPublisher()
            if received is context
            else (_ for _ in ()).throw(AssertionError("恢复使用了错误 AppContext"))
        ),
    )

    worker = server.start_global_task_recovery_worker(
        stop_event=stopped,
        interval_seconds=0.01,
    )

    assert worker.daemon is True
    worker.join(timeout=2)
    assert called.is_set()
    assert calls == 2
