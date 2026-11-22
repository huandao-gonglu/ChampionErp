"""内置同步与重试策略回归：确认渐进退避、合批预算、无空转写库、通道指数退避及旧配置剥离。"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import threading
import time
from urllib.request import Request

import pytest

from erp_web import app_config
from erp_web.account_runtime import AccountRuntime, NEWAPI_FAILURE_BACKOFF_STEPS, NEWAPI_IDLE_SCAN_INTERVAL
from erp_web.context import get_context
from erp_web.runtime_units.publishing_bus_core import PublishingBus
from erp_web.schemas.task_queue import ConfirmationPolicy, confirmation_policy
from erp_web.services.external_request_manager import BufferedResponse
from erp_web.services.wechat_channel_service import DELIVERY_BACKOFF_STEPS, WechatChannelService
from erp_web.stores.order_notification_store import (
    CALLBACK_BASE_DELAY_SECONDS,
    CALLBACK_MAX_ATTEMPTS,
    CALLBACK_MAX_DELAY_SECONDS,
    CALLBACK_MAX_LIFETIME_SECONDS,
)
from tests.test_order_notifications import event, store  # noqa: F401
from tests.test_task_queue import due, queue  # noqa: F401


def test_retired_sync_policies_stripped_on_read_and_rejected_on_write():
    from erp_web.schemas.requests import RequestValidationError
    from tests.test_watermark import post

    legacy = app_config.default_app_config()
    legacy["system_settings"]["sync_policies"] = {"confirmation_default": {"interval": 90}}
    normalized = app_config.normalize_app_config(legacy)
    assert "sync_policies" not in normalized["system_settings"]
    assert normalized["system_settings"]["orders_auto_sync_interval_hours"] == 5

    # 仅提交已退役字段在路由契约层即被拦截。
    with pytest.raises(RequestValidationError):
        post("/api/system-settings", {"sync_policies": {"confirmation_default": {"interval": 90}}})

    # 即使伴随有效字段提交，Pydantic extra="forbid" 同样返回 400 拒绝。
    response, status = post("/api/system-settings", {
        "orders_auto_sync_interval_hours": 12,
        "sync_policies": {"confirmation_default": {"interval": 90}},
    })
    assert status == 400 and response["ok"] is False
    assert get_context().config.system_settings()["orders_auto_sync_interval_hours"] == 5


def test_confirmation_policy_uses_progressive_intervals_and_batch_scaled_budget(queue):
    service, remote, _, task_id = queue
    yandex_10 = confirmation_policy("publish", "yandex", "publish", targets=10)
    yandex_250 = confirmation_policy("publish", "yandex", "publish", targets=250)
    yandex_price = confirmation_policy("online", "yandex", "price", targets=20)
    ozon_4 = confirmation_policy("publish", "ozon", "publish", targets=4)

    assert yandex_10.intervals == (30, 60, 120, 300, 600, 1800)
    assert yandex_10.next_interval(1) == 30
    assert yandex_10.next_interval(2) == 60
    assert yandex_10.next_interval(6) == 1800
    assert yandex_10.next_interval(20) == 1800
    # 10 SKU 与 1 SKU 属于同一个 100-SKU 批次，不按目标数线性放大到 1200 次。
    assert yandex_10.max_requests == 208 and yandex_10.manual_round_requests() == 16
    assert yandex_250.max_requests == 624 and yandex_250.manual_round_requests() == 24
    assert yandex_price.max_requests == 40 and yandex_price.manual_round_requests() == 12
    assert ozon_4.max_requests == 160 and ozon_4.manual_round_requests() == 16

    # 旧持久化 JSON（缺少 intervals 与 round_max_requests）仍可兼容解析。
    legacy = ConfirmationPolicy.from_dict({
        "first_delay": 60, "interval": 1800, "max_checks": 24,
        "lifetime": 86400, "max_requests": 120,
    })
    assert legacy.next_interval(1) == 1800 and legacy.manual_round_requests() == 120

    # 自动确认每轮按渐进间隔推迟 next_check_at。
    due(service, task_id)
    before = time.time()
    assert service.check(task_id)["checked"]
    first_next = service.store.get(task_id)["next_check_at"] - before
    assert 13 <= first_next <= 20
    due(service, task_id)
    before = time.time()
    assert service.check(task_id)["checked"]
    second_next = service.store.get(task_id)["next_check_at"] - before
    assert 28 <= second_next <= 35


def test_manual_check_after_stop_caps_requests_to_round_budget_not_total(queue):
    service, remote, _, task_id = queue
    due(service, task_id)
    with service.store.connect() as conn:
        policy = json.loads(service.store.get(task_id)["policy"])
        policy["max_requests"] = 200
        policy["round_max_requests"] = 2
        conn.execute("UPDATE confirmations SET policy=?,checks_used=? WHERE id=?", (json.dumps(policy), policy["max_checks"], task_id))
    sent = []

    def transport(request, **kwargs):
        sent.append(request)
        return BufferedResponse(b"{}", headers={}, status=200, url=request.full_url)

    def read_three():
        for _ in range(3):
            req = Request("https://api-seller.ozon.ru/v1/product/import/info", data=b"{}", headers={"Client-Id": "seller", "Api-Key": "test"})
            service.context.external_requests.open(req, transport=transport)

    remote.on_read = read_three
    response = service.check(task_id)
    assert response["checked"]
    assert len(sent) == 2


def test_task_queue_plan_does_not_rewrite_existing_plans_on_hot_reads(queue, monkeypatch):
    service, _, _, task_id = queue
    service.detail(task_id)
    ensures = []
    original_ensure = service.store.ensure
    monkeypatch.setattr(service.store, "ensure", lambda *args, **kwargs: ensures.append(args) or original_ensure(*args, **kwargs))
    service.detail(task_id)
    service.list({"status": "waiting"})
    assert ensures == []


def test_publishing_bus_sleeps_exact_backoff_without_busy_writes_and_stops_cleanly():
    class CountingStore:
        def __init__(self):
            self.states = {}
            self.writes = 0
            self._lock = threading.Lock()

        def create_publish_job(self, state):
            with self._lock:
                self.states[state["job_id"]] = deepcopy(state)
            return deepcopy(state), True

        def save_publish_job(self, state):
            with self._lock:
                self.writes += 1
                self.states[state["job_id"]] = deepcopy(state)

        def load_publish_job(self, job_id):
            with self._lock:
                return deepcopy(self.states.get(job_id, {}))

        def load_publish_job_by_idempotency_key(self, key):
            return {}

        def list_pending_publish_jobs(self):
            return []

        def list_publish_jobs(self, **_kwargs):
            return [], ""

    class BackoffAdapter:
        def __init__(self, target_wait):
            self.target_wait = target_wait
            self.advance_calls = 0

        def resolve_category(self, product, config):
            return product

        def required_attributes_missing(self, context, config):
            return []

        def publish(self, product, platform, config):
            return {
                "ok": True,
                "status": "pending_submission",
                "checkpoint": {"next_poll_at": time.time() + self.target_wait},
            }

        def next_submission_wait_seconds(self, result, config):
            return max(0.0, float(result["checkpoint"]["next_poll_at"]) - time.time())

        def submission_step_interval_seconds(self, config):
            return 0.05

        def advance_submission(self, result, config):
            self.advance_calls += 1
            if time.time() < result["checkpoint"]["next_poll_at"]:
                return result
            return {"ok": True, "status": "published", "external_id": "ok-1"}

    # 1. 退避 0.25s 时一次性等待到期，不在期间空转写库。
    store_one = CountingStore()
    adapter_one = BackoffAdapter(0.25)
    bus_one = PublishingBus(store_one, {"yandex": adapter_one})
    try:
        queued = bus_one.enqueue(
            {"product_id": "p1"}, ["yandex"],
            targets={"yandex": {"draft_id": "d1", "site": "global"}},
            idempotency_key="backoff-exact-1",
        )
        bus_one.wait(queued["job_id"], timeout=3)
        assert bus_one.get_status(queued["job_id"])["platforms"]["yandex"]["status"] == "success"
        assert adapter_one.advance_calls == 1
        assert store_one.writes <= 8
    finally:
        bus_one.close()

    # 2. 长退避等待期间 close() 立即唤醒退出，不卡死停机。
    store_two = CountingStore()
    adapter_two = BackoffAdapter(30.0)
    bus_two = PublishingBus(store_two, {"yandex": adapter_two})
    queued = bus_two.enqueue(
        {"product_id": "p2"}, ["yandex"],
        targets={"yandex": {"draft_id": "d2", "site": "global"}},
        idempotency_key="backoff-stop-1",
    )
    deadline = time.time() + 2
    while time.time() < deadline:
        item = bus_two.get_status(queued["job_id"])["platforms"]["yandex"]
        if item.get("stage") == "submitting_steps":
            break
        time.sleep(0.02)
    started = time.monotonic()
    bus_two.close()
    assert time.monotonic() - started < 1.0
    assert bus_two.get_status(queued["job_id"])["platforms"]["yandex"]["status"] == "interrupted"
    assert adapter_two.advance_calls == 0


def test_order_callback_backoff_starts_at_base_delay_and_caps_cooldown_lifetime(store):
    store.enqueue(event())
    now = time.time()
    job = store.claim({"ozon": "3"}, now=now)
    store.finish(job, error="暂时失败", retryable=True, now=now)
    with store.connect() as conn:
        row = dict(conn.execute("SELECT * FROM inbox WHERE id=?", (job["id"],)).fetchone())
    assert row["status"] == "retry"
    assert row["next_attempt"] == pytest.approx(now + CALLBACK_BASE_DELAY_SECONDS)

    # 第 2 次失败等待 2 * base_delay。
    job2 = store.claim({"ozon": "3"}, now=now + CALLBACK_BASE_DELAY_SECONDS + 1)
    store.finish(job2, error="暂时失败", retryable=True, now=now + CALLBACK_BASE_DELAY_SECONDS + 1)
    with store.connect() as conn:
        row2 = dict(conn.execute("SELECT * FROM inbox WHERE id=?", (job["id"],)).fetchone())
    assert row2["next_attempt"] == pytest.approx(now + CALLBACK_BASE_DELAY_SECONDS + 1 + CALLBACK_BASE_DELAY_SECONDS * 2)

    # 达到 max_attempts（无外部冷却）转失败。
    with store.connect() as conn:
        conn.execute("UPDATE inbox SET attempts=?,next_attempt=0 WHERE id=?", (CALLBACK_MAX_ATTEMPTS - 1, job["id"]))
        conn.commit()
    final_job = store.claim({"ozon": "3"}, now=now + 100)
    store.finish(final_job, error="仍然失败", retryable=True, now=now + 100)
    with store.connect() as conn:
        assert conn.execute("SELECT status FROM inbox WHERE id=?", (job["id"],)).fetchone()[0] == "failed"

    # 即使持续传入外部冷却 next_attempt，超过 24 小时总存活期也终止重试。
    store.enqueue(event(sequence="cooldown-expired"))
    cool_job = store.claim({"ozon": "3"}, now=now + 200)
    expired_now = now + CALLBACK_MAX_LIFETIME_SECONDS + 10
    with store.connect() as conn:
        conn.execute("UPDATE inbox SET lease_until=? WHERE id=?", (expired_now + 60, cool_job["id"]))
        conn.commit()
    store.finish(cool_job, error="平台限流", retryable=True, now=expired_now, next_attempt=expired_now + 60)
    with store.connect() as conn:
        expired_row = dict(conn.execute("SELECT status,error FROM inbox WHERE id=?", (cool_job["id"],)).fetchone())
    assert expired_row["status"] == "failed" and "24 小时" in expired_row["error"]
    assert CALLBACK_MAX_DELAY_SECONDS == 1800


def test_wechat_delivery_uses_exponential_backoff_and_resets_on_success(tmp_path):
    from erp_web.stores.wechat_channel_store import WechatChannelStore

    waits: list[float] = []
    calls = 0

    class FakeApi:
        def build_text_message(self, user_id, context_token, text):
            return {"to_user_id": user_id, "context_token": context_token, "text": text}

        async def send_message(self, base_url, token, msg):
            nonlocal calls
            calls += 1
            if calls <= 3:
                raise TimeoutError("发送超时")
            return {"ret": 0}

    store_wc = WechatChannelStore(tmp_path / "wc.sqlite3", get_context().config)
    store_wc.configure(
        conversation_id="conv-1",
        history_position=0,
        credentials={"token": "tok", "account_id": "bot", "user_id": "u", "base_url": "https://ilinkai.weixin.qq.com"},
    )
    store_wc.ingest(store_wc.state()["generation"], "c1", [{"message_id": "m1", "text": "hi", "context_token": "ctx"}])
    store_wc.received(store_wc.state()["generation"], "m1")
    store_wc.observe(store_wc.state()["generation"], 0, [("reply", "k1", "你好")], enabled_types=["reply"])

    service = WechatChannelService(
        store=store_wc,
        conversations=None,
        api=FakeApi(),
        forward_types_reader=lambda: ["reply"],
        jitter=lambda _: 0.0,
    )
    assert [service._delivery_retry_delay(n) for n in (1, 2, 3, 4, 5, 6, 9)] == [
        *DELIVERY_BACKOFF_STEPS,
        DELIVERY_BACKOFF_STEPS[-1],
    ]

    async def run_pump():
        class WakeSpy:
            def clear(self):
                pass

            async def wait(self):
                if calls >= 4:
                    raise asyncio.CancelledError
                await asyncio.sleep(10)

        service._wake_event = WakeSpy()
        original_wait_for = asyncio.wait_for

        async def fake_wait_for(coro, timeout):
            if getattr(coro, "__qualname__", "").endswith("WakeSpy.wait"):
                waits.append(timeout)
                coro.close()
                raise TimeoutError
            return await original_wait_for(coro, timeout)

        service.transfer_inputs = lambda state: None
        service.collect_replies = lambda state: None
        asyncio.wait_for = fake_wait_for
        try:
            await service._pump_loop()
        except asyncio.CancelledError:
            pass
        finally:
            asyncio.wait_for = original_wait_for

    asyncio.run(run_pump())
    assert waits == [1.0, 2.0, 5.0]
    assert calls == 4
    assert store_wc.pending(store_wc.state()["generation"]) == []


def test_newapi_account_sync_backoff_and_reset():
    runtime = AccountRuntime(get_context())
    try:
        assert runtime._next_newapi_delay(False, 3) == (NEWAPI_IDLE_SCAN_INTERVAL, 0)
        streak = 0
        observed = []
        for _ in range(len(NEWAPI_FAILURE_BACKOFF_STEPS) + 2):
            delay, streak = runtime._next_newapi_delay(True, streak)
            observed.append(delay)
        assert observed == [*NEWAPI_FAILURE_BACKOFF_STEPS, NEWAPI_FAILURE_BACKOFF_STEPS[-1], NEWAPI_FAILURE_BACKOFF_STEPS[-1]]
        assert runtime._next_newapi_delay(False, streak) == (NEWAPI_IDLE_SCAN_INTERVAL, 0)
    finally:
        runtime.close()
