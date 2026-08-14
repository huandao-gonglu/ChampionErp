from __future__ import annotations

import threading

from erp_web import server


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

    worker = server.start_global_task_recovery_worker(
        stop_event=stopped,
        interval_seconds=0.01,
    )

    assert worker.daemon is True
    worker.join(timeout=2)
    assert called.is_set()
    assert calls == 2
