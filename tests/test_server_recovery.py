"""启动恢复只领取领域 Job，模型运行使用独立投递器。"""

import threading
from erp_web.account_runtime import AccountRuntime
from erp_web.context import get_context
from tests.account_test_utils import _password_hash


def test_native_job_scanner_recovers_and_stops(monkeypatch):
    stopped = threading.Event()
    calls = []
    runtime = AccountRuntime(get_context())
    user = runtime.store.create_user('admin@example.com', '管理员', _password_hash(), admin=True)
    context = runtime.context_for(user)
    context._agent_calls = object()

    class Service:
        def scan(self):
            calls.append("scan")
            if calls.count("scan") == 2:
                stopped.set()

        def close(self):
            calls.append("close")

    from erp_web.facades import ai_chat_facade
    monkeypatch.setattr(
        ai_chat_facade,
        "build_job_service",
        lambda received: Service() if received is context else None,
    )
    runtime.start()
    assert stopped.wait(5)
    runtime.close()
    assert runtime._worker.daemon and not runtime._worker.is_alive()
    assert calls == ["scan", "scan", "close"]
