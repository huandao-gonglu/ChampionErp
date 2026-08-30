# -*- coding: utf-8 -*-
"""PublishAdapterError.retryable 类型化重试契约与 HTTP 错误分类测试。

覆盖文档 §5.6：
- PublishingBus 只对 ``PublishAdapterError(retryable=True)`` 重试；
- 确认绑定失效与未分类异常默认不可重试；
- Ozon / Mercado Libre HTTP 边界把远端失败分类为类型化错误，
  并保持既有 "failed:" 消息格式供字符串解析继续使用。
"""

from __future__ import annotations

import io
import threading
import urllib.error
from copy import deepcopy
from typing import Any

import pytest

from erp_web.marketplaces import config_http
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.runtime_units.publishing_bus_core import PublishingBus


class _MemoryPublishJobStore:
    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create_publish_job(self, state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        with self._lock:
            self.states[str(state["job_id"])] = deepcopy(state)
        return deepcopy(state), True

    def save_publish_job(self, state: dict[str, Any]) -> None:
        with self._lock:
            self.states[str(state["job_id"])] = deepcopy(state)

    def load_publish_job(self, job_id: str) -> dict[str, Any]:
        return deepcopy(self.states.get(job_id, {}))

    def load_publish_job_by_idempotency_key(self, key: str) -> dict[str, Any]:
        return deepcopy(
            next(
                (state for state in self.states.values() if state.get("idempotency_key") == key),
                {},
            )
        )

    def list_pending_publish_jobs(self) -> list[dict[str, Any]]:
        return []

    def list_publish_jobs(self, **_kwargs: Any) -> tuple[list[dict[str, Any]], str]:
        return [], ""


class _FakeAdapter:
    def __init__(self, outcomes: list[Any]) -> None:
        # outcomes: Exception 实例表示抛出；dict 表示成功返回。
        self.outcomes = list(outcomes)
        self.publish_calls = 0

    def resolve_category(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return product

    def required_attributes_missing(self, product: dict[str, Any], config: dict[str, Any]) -> list[str]:
        return []

    def publish(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        self.publish_calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else {"ok": True, "status": "success", "id": "x"}
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def publish_payload(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return self.publish({}, "mercadolibre", {})

    def validate_payload(self, payload: Any, config: dict[str, Any]) -> list[str]:
        return []


def _run_bus(adapter: _FakeAdapter, *, max_retries: int) -> dict[str, Any]:
    bus = PublishingBus(
        _MemoryPublishJobStore(),
        adapters={"mercadolibre": adapter},
        config_provider=lambda: {"mercadolibre": {}},
        max_retries=max_retries,
        retry_delay_seconds=0.0,
        auto_resume_pending=False,
    )
    try:
        queued = bus.enqueue(
            {"product_id": "product-1"},
            ["mercadolibre"],
            targets={
                "mercadolibre": {
                    "draft_id": "draft-1",
                    "site": "MLM",
                    "product_id": "product-1",
                }
            },
            idempotency_key="retry-contract:case",
        )
        bus.wait(queued["job_id"], timeout=5)
        return bus.get_status(queued["job_id"])
    finally:
        bus.executor.shutdown(wait=True)


def test_transient_adapter_error_is_retried_until_success() -> None:
    adapter = _FakeAdapter(
        [
            PublishAdapterError("OZON_SERVER_ERROR", "远端 5xx", retryable=True),
            {"ok": True, "status": "success", "id": "item-1"},
        ]
    )
    state = _run_bus(adapter, max_retries=1)
    platform_state = state["platforms"]["mercadolibre"]
    assert platform_state["status"] == "success"
    assert platform_state["attempts"] == 2
    assert adapter.publish_calls == 2


def test_transient_adapter_error_exhausts_attempts_then_fails() -> None:
    adapter = _FakeAdapter(
        [
            PublishAdapterError("OZON_SERVER_ERROR", "远端 5xx", retryable=True),
            PublishAdapterError("OZON_SERVER_ERROR", "远端 5xx", retryable=True),
        ]
    )
    state = _run_bus(adapter, max_retries=1)
    platform_state = state["platforms"]["mercadolibre"]
    assert platform_state["status"] == "failed"
    assert platform_state["attempts"] == 2
    assert adapter.publish_calls == 2


def test_uncertain_remote_outcome_retries_reads_then_keeps_reconciliation_lock() -> None:
    class _PendingConfirmationAdapter(_FakeAdapter):
        def __init__(self) -> None:
            super().__init__([])
            self.poll_calls = 0
            self.poll_outcomes = [
                PublishAdapterError(
                    "MERCADOLIBRE_SERVER_ERROR",
                    "确认读取 5xx",
                    retryable=True,
                    details={"outcome_unknown": True},
                ),
                PublishAdapterError(
                    "MERCADOLIBRE_SERVER_ERROR",
                    "确认读取 5xx",
                    retryable=True,
                    details={"outcome_unknown": True},
                ),
            ]

        def publish(
            self,
            product: dict[str, Any],
            platform: str,
            config: dict[str, Any],
        ) -> dict[str, Any]:
            self.publish_calls += 1
            return {
                "ok": True,
                "status": "pending_confirmation",
                "task_id": "task-1",
            }

        def poll_publish_status(
            self,
            result: dict[str, Any],
            config: dict[str, Any],
        ) -> dict[str, Any]:
            self.poll_calls += 1
            raise self.poll_outcomes.pop(0)

        @staticmethod
        def publish_poll_interval_seconds(config: dict[str, Any]) -> float:
            return 0.0

    adapter = _PendingConfirmationAdapter()

    state = _run_bus(adapter, max_retries=1)

    platform_state = state["platforms"]["mercadolibre"]
    assert state["status"] == "outcome_unknown"
    assert platform_state["status"] == "outcome_unknown"
    assert platform_state["result"]["outcome_unknown"] is True
    assert platform_state["result"]["task_id"] == "task-1"
    assert adapter.publish_calls == 1
    assert adapter.poll_calls == 2


def test_deterministic_adapter_error_is_not_retried() -> None:
    adapter = _FakeAdapter(
        [PublishAdapterError("OZON_AUTH_FAILED", "凭证无效", retryable=False)]
    )
    state = _run_bus(adapter, max_retries=3)
    platform_state = state["platforms"]["mercadolibre"]
    assert platform_state["status"] == "failed"
    assert platform_state["attempts"] == 1
    assert adapter.publish_calls == 1


def test_unclassified_exception_defaults_to_not_retryable() -> None:
    adapter = _FakeAdapter([RuntimeError("本地确定性错误")])
    state = _run_bus(adapter, max_retries=3)
    platform_state = state["platforms"]["mercadolibre"]
    assert platform_state["status"] == "failed"
    assert platform_state["attempts"] == 1
    assert adapter.publish_calls == 1


# ------------------------------------------------- HTTP 边界错误分类


def _http_error(url: str, code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "status", {}, io.BytesIO(body))


def test_mercadolibre_request_json_classifies_http_errors(monkeypatch) -> None:
    url = "https://api.mercadolibre.com/items"

    cases = [
        (401, "MERCADOLIBRE_AUTH_FAILED", False),
        (403, "MERCADOLIBRE_AUTH_FAILED", False),
        (404, "MERCADOLIBRE_NOT_FOUND", False),
        (400, "MERCADOLIBRE_REQUEST_INVALID", False),
        (429, "MERCADOLIBRE_RATE_LIMITED", True),
        (500, "MERCADOLIBRE_SERVER_ERROR", True),
        (503, "MERCADOLIBRE_SERVER_ERROR", True),
    ]
    for status, expected_code, expected_retryable in cases:
        def make_fake(status_code: int) -> Any:
            def fake_request(*_args: Any, **_kwargs: Any) -> Any:
                raise _http_error(url, status_code, b'{"error":"detail"}')

            return fake_request

        monkeypatch.setattr(
            config_http.http_client,
            "request_json",
            make_fake(status),
        )
        with pytest.raises(PublishAdapterError) as exc_info:
            config_http.request_json("POST", url, "token", {"title": "x"})
        exc = exc_info.value
        assert exc.code == expected_code, status
        assert exc.retryable is expected_retryable, status
        # 消息格式保持 "METHOD url failed: <code> <body>"，字符串解析继续可用
        assert str(exc).startswith(f"POST {url} failed: {status} "), status
        assert '{"error":"detail"}' in str(exc)


def test_mercadolibre_request_json_classifies_network_errors(monkeypatch) -> None:
    def fake_request(*_args: Any, **_kwargs: Any) -> Any:
        raise urllib.error.URLError(ConnectionRefusedError("Connection refused"))

    monkeypatch.setattr(config_http.http_client, "request_json", fake_request)
    with pytest.raises(PublishAdapterError) as exc_info:
        config_http.request_json("GET", "https://api.mercadolibre.com/users/me", "token")
    assert exc_info.value.code == "MERCADOLIBRE_NETWORK"
    assert exc_info.value.retryable is True
    assert "Connection refused" in str(exc_info.value)


def test_ozon_request_classifies_http_and_network_errors(monkeypatch) -> None:
    url = "https://api-seller.ozon.ru/v3/product/import"

    def patch_urlopen(exc: Exception) -> None:
        def fake_urlopen(*_args: Any, **_kwargs: Any) -> Any:
            raise exc

        monkeypatch.setattr(config_http.urllib.request, "urlopen", fake_urlopen)

    patch_urlopen(_http_error(url, 401, b'{"message":"Invalid api key"}'))
    with pytest.raises(PublishAdapterError) as exc_info:
        config_http.request_ozon_json("POST", url, "client", "key", {})
    assert exc_info.value.code == "OZON_AUTH_FAILED"
    assert exc_info.value.retryable is False
    assert str(exc_info.value).startswith(f"POST {url} failed: 401 ")

    patch_urlopen(_http_error(url, 429, b"rate limited"))
    with pytest.raises(PublishAdapterError) as exc_info:
        config_http.request_ozon_json("POST", url, "client", "key", {})
    assert exc_info.value.code == "OZON_RATE_LIMITED"
    assert exc_info.value.retryable is True

    patch_urlopen(TimeoutError("The read operation timed out"))
    with pytest.raises(PublishAdapterError) as exc_info:
        config_http.request_ozon_json("POST", url, "client", "key", {})
    assert exc_info.value.code == "OZON_TIMEOUT"
    assert exc_info.value.retryable is True
    # map_ozon_publish_error 依赖消息中的 timeout 标记
    assert "timeout" in str(exc_info.value)

    patch_urlopen(urllib.error.URLError(ConnectionResetError("reset")))
    with pytest.raises(PublishAdapterError) as exc_info:
        config_http.request_ozon_json("POST", url, "client", "key", {})
    assert exc_info.value.code == "OZON_NETWORK"
    assert exc_info.value.retryable is True


def test_ozon_deadline_guard_keeps_timeout_error() -> None:
    with pytest.raises(TimeoutError, match="deadline"):
        config_http.request_ozon_json("POST", "https://api-seller.ozon.ru/x", "c", "k", {}, timeout_seconds=0)


def test_mercadolibre_picture_upload_classifies_errors(monkeypatch) -> None:
    def fake_urlopen(*_args: Any, **_kwargs: Any) -> Any:
        raise _http_error(
            "https://api.mercadolibre.com/pictures/items/upload",
            400,
            b'{"message":"Error creating image","error":"bad_request"}',
        )

    monkeypatch.setattr(config_http.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(PublishAdapterError) as exc_info:
        config_http.upload_mercadolibre_picture(__file__, "token")
    assert exc_info.value.code == "MERCADOLIBRE_REQUEST_INVALID"
    assert exc_info.value.retryable is False
    # 与历史消息格式完全一致，既有错误解析测试不受影响
    assert str(exc_info.value).startswith("POST Mercado Libre picture upload failed: 400 ")


def test_ozon_map_publish_error_honors_typed_code() -> None:
    from erp_web.runtime_units.publish_ozon import map_ozon_publish_error

    transient = PublishAdapterError(
        "OZON_RATE_LIMITED",
        "POST https://api-seller.ozon.ru/v3/product/import failed: 429 rate limited",
        retryable=True,
        details={"http_status": 429},
    )
    mapped = map_ozon_publish_error(transient)
    assert mapped["error_code"] == "OZON_RATE_LIMITED"
    assert mapped["retryable"] is True

    auth = PublishAdapterError(
        "OZON_AUTH_FAILED",
        "POST https://api-seller.ozon.ru/v3/product/import failed: 401 Invalid api key",
        retryable=False,
        details={"http_status": 401},
    )
    mapped = map_ozon_publish_error(auth)
    assert mapped["error_code"] == "OZON_AUTH_FAILED"
    assert mapped["field_errors"].get("auth")
    assert mapped["retryable"] is False
