"""启动恢复只领取领域 Job，模型运行使用独立投递器。"""

import threading
from types import SimpleNamespace
from erp_web import server


def test_native_job_scanner_recovers_and_stops(monkeypatch):
    stopped = threading.Event()
    calls = []

    class Service:
        def scan(self):
            calls.append("scan")
            if calls.count("scan") == 2:
                stopped.set()

        def close(self):
            calls.append("close")

    context = SimpleNamespace(
        agent_calls=SimpleNamespace(
            mark_interrupted_writes=lambda: calls.append("recover")
        )
    )
    monkeypatch.setattr(server, "get_context", lambda: context)
    monkeypatch.setattr(
        server,
        "build_job_service",
        lambda received: Service() if received is context else None,
    )
    worker = server.start_agent_job_worker(stop_event=stopped, interval_seconds=0.1)
    worker.join(timeout=2)
    assert worker.daemon and not worker.is_alive()
    assert calls == ["recover", "scan", "scan", "close"]
