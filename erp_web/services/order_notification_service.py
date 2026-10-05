"""订单领域后台执行；HTTP 收件与页面读取均不等待平台网络。"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

from erp_web.schemas.orders import (
    PLATFORMS,
    OrderAdapter,
    OrderDataError,
    OrderEvent,
    OrderSnapshot,
    configured_accounts,
)
from erp_web.stores.order_notification_store import OrderNotificationStore

logger = logging.getLogger(__name__)


class OrderNotificationService:
    def __init__(
        self,
        store: OrderNotificationStore,
        config_provider: Callable[[], dict[str, Any]],
        *,
        adapters: dict[str, Callable[[dict[str, Any]], OrderAdapter]],
        parser: Callable[
            [str, dict[str, Any], dict[str, Any]],
            tuple[OrderEvent | None, dict[str, Any]],
        ],
        start_worker: bool = True,
    ):
        self.store = store
        self.config_provider = config_provider
        self.adapters = adapters
        self.parser = parser
        self.stop_event = threading.Event()
        self.workers = []
        if start_worker:
            for platform in PLATFORMS:
                worker = threading.Thread(
                    target=self._run,
                    args=(platform,),
                    name=f"orders-{platform}",
                    daemon=True,
                )
                self.workers.append(worker)
                worker.start()

    def accounts(self) -> dict[str, str]:
        return configured_accounts(self.config_provider())

    def receive(
        self, platform: str, token: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        expected = self.store.setting("token:" + platform)
        if not expected or not secrets.compare_digest(expected, token):
            raise PermissionError("订单回调凭据无效")
        event, response = self.parser(platform, body, self.config_provider())
        self.store.receive(platform, event)
        return response

    def integrations(self) -> dict[str, Any]:
        accounts = self.accounts()
        base = self.store.setting("public_url")
        return {
            "ok": True,
            "public_url": base,
            "platforms": [
                {
                    "platform": platform,
                    "account_id": accounts.get(platform, ""),
                    "configured": platform in accounts,
                    "callback_url": f"{base}/api/{platform}/notifications?token={self.store.setting('token:' + platform)}"
                    if base
                    else "",
                    "last_received": self.store.setting("last_received:" + platform),
                }
                for platform in PLATFORMS
            ],
        }

    def configure(self, public_url: str) -> dict[str, Any]:
        value = public_url.strip().rstrip("/")
        parsed = urlsplit(value)
        if value and (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path
        ):
            raise ValueError("请填写不含路径、查询参数及凭据的公网 HTTPS 地址")
        self.store.set_setting("public_url", value)
        return self.integrations()

    def sync(self, platform: str = "") -> dict[str, Any]:
        accounts = self.accounts()
        if platform:
            accounts = {
                key: value for key, value in accounts.items() if key == platform
            }
        if not accounts:
            raise ValueError("没有已配置的平台账号，请先完成店铺授权")
        self.store.schedule(accounts, now=time.time(), force=True)
        return {"ok": True}

    def process_one(self, platform: str = "", *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        config = self.config_provider()
        accounts = configured_accounts(config)
        if platform:
            accounts = {
                key: value for key, value in accounts.items() if key == platform
            }
        job = self.store.claim(accounts, now=now)
        if job is None:
            return False
        event = OrderEvent.model_validate_json(job["event_json"])
        try:
            adapter = self.adapters[event.platform](config)
            snapshots = (
                self._sync_snapshots(adapter, event)
                if event.topic == "sync"
                else adapter.read(event)
            )
            for snapshot in snapshots:
                if self.stop_event.is_set():
                    raise InterruptedError("订单同步已停止，下次启动继续处理")
                if self.accounts().get(event.platform) != event.account_id:
                    raise ValueError("平台账号已切换，本次任务停止")
                if (
                    snapshot.platform != event.platform
                    or snapshot.account_id != event.account_id
                ):
                    raise ValueError("订单快照越过账号范围")
                if not self.store.save_snapshot(job, snapshot, now=time.time()):
                    raise InterruptedError("订单处理租约已失效")
        except Exception as exc:  # noqa: BLE001 -- 领域任务须持久记录任何适配器故障，正文不进入日志。
            # 错误正文可能含平台凭据或买家信息，只保留类型与稳定错误码。
            raw_code = str(getattr(exc, "code", ""))
            code = (
                raw_code
                if re.fullmatch(r"[A-Z][A-Z_0-9]{1,80}", raw_code)
                else type(exc).__name__
            )
            retryable = bool(getattr(exc, "retryable", False)) or isinstance(
                exc, (TimeoutError, ConnectionError, OSError, InterruptedError)
            )
            message = (
                str(exc)
                if isinstance(exc, OrderDataError)
                else f"订单读取失败（{code}），请检查平台授权、请求记录或网络后重试。"
            )
            self.store.finish(job, error=message, retryable=retryable, now=time.time())
        else:
            self.store.finish(job, now=time.time())
        return True

    def _sync_snapshots(
        self, adapter: OrderAdapter, event: OrderEvent
    ) -> Iterable[OrderSnapshot]:
        tracked = self.store.tracked(event.platform, event.account_id)
        seen = set()
        for snapshot in adapter.sync():
            seen.add(snapshot.identity)
            yield snapshot
        # 时间窗口外仍未完成的订单逐单核对；列表中未出现不能解释为已取消。
        for snapshot in tracked:
            if snapshot.identity in seen:
                continue
            if snapshot.platform == "mercadolibre":
                topic, resource = "orders_v2", "/orders/" + snapshot.order_id
            elif snapshot.platform == "ozon":
                topic = (
                    "TYPE_FBO_POSTING_STATE_CHANGED"
                    if snapshot.fulfillment == "fbo"
                    else "TYPE_STATE_CHANGED"
                )
                resource = snapshot.order_id
            else:
                topic, resource = "ORDER_UPDATED", snapshot.order_id
            yield from adapter.read(
                OrderEvent(
                    platform=event.platform,
                    account_id=event.account_id,
                    topic=topic,
                    resource=resource,
                )
            )

    def _run(self, platform: str) -> None:
        while not self.stop_event.is_set():
            try:
                accounts = {
                    key: value
                    for key, value in self.accounts().items()
                    if key == platform
                }
                self.store.schedule(accounts, now=time.time())
                worked = self.process_one(platform)
            except Exception:
                logger.exception("订单后台任务失败：平台=%s", platform)
                worked = False
            self.stop_event.wait(0.1 if worked else 2)

    def close(self) -> None:
        self.stop_event.set()
        for worker in self.workers:
            worker.join(timeout=2)
