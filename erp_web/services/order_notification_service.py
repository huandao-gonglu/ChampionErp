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
from erp_web.services.external_request_context import request_operation

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
        external_store=None,
        request_scopes=None,
        start_worker: bool = True,
    ):
        self.external_store = external_store
        self.request_scopes = request_scopes
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
        operation_ids = self.store.schedule(accounts, now=time.time())
        return {"ok": True, "operation_ids": operation_ids}

    def _blocks(self, config, platform):
        if self.external_store is None or self.request_scopes is None:
            return []
        return self.external_store.matching_blocks(self.request_scopes(config).get(platform, []))

    def sync_status(self):
        config = self.config_provider()
        rows = self.store.sync_status(configured_accounts(config))
        for row in rows:
            blocks = self._blocks(config, row["platform"])
            if not blocks:
                continue
            hard = [b for b in blocks if b["failure"].get("resume_at") is None]
            chosen = (hard or sorted(blocks, key=lambda b: b["failure"].get("resume_at") or 0, reverse=True))[0]["failure"]
            row.update(status="blocked" if hard else "cooldown", error=chosen["message"],
                       next_attempt=0 if hard else max(row["next_attempt"], chosen.get("resume_at") or 0, chosen.get("probe_until") or 0))
        return rows

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
            with request_operation("orders", operation_id=f"orders:{job['id']}", trigger="background"):
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
            blocks = self._blocks(config, event.platform)
            timed = [b["failure"] for b in blocks if b["failure"].get("resume_at") is not None]
            hard = any(b["failure"].get("resume_at") is None for b in blocks)
            next_attempt = None
            if timed and not hard:
                retryable = True
                next_attempt = max(time.time()+1, *(max(b["resume_at"], b.get("probe_until") or 0) for b in timed))
                message = "平台订单同步暂时暂停，将在冷却结束后自动重试。"
            if event.topic == "sync":
                retryable, next_attempt = False, None
                if timed and not hard:
                    message = "平台订单同步暂时暂停，请在冷却结束后点击同步订单。"
            self.store.finish(job, error=message, retryable=retryable, now=time.time(), next_attempt=next_attempt)
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
                worked = self.process_one(platform)
            except Exception:
                logger.exception("订单后台任务失败：平台=%s", platform)
                worked = False
            self.stop_event.wait(0.1 if worked else 2)

    def close(self) -> None:
        self.stop_event.set()
        for worker in self.workers:
            worker.join(timeout=2)
