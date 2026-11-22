"""订单领域后台执行；HTTP 收件与页面读取均不等待平台网络。"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from .desktop_connection_service import server_worker_context
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

from erp_web.release_guard import release_guard
from erp_web.schemas.orders import (
    PLATFORMS,
    OrderAdapter,
    OrderDataError,
    OrderEvent,
    OrderSnapshot,
    OrderShop,
    account_pairs,
    account_in_scope,
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
        config_store,
        owner_id: str = "",
        adapters: dict[str, Callable[[dict[str, Any]], OrderAdapter]],
        parser: Callable[..., tuple[OrderEvent | None, dict[str, Any]]],
        external_manager=None,
        request_scopes=None,
        progress_sync: Callable[[str, Callable[[], None]], bool] | None = None,
        auto_sync_interval_provider: Callable[[], int] = lambda: 5 * 3600,
        start_worker: bool = True,
    ):
        self.external_manager = external_manager
        self.request_scopes = request_scopes
        self.progress_sync = progress_sync
        self.auto_sync_interval_provider = auto_sync_interval_provider
        self.store = store
        self.config_provider = config_provider
        self.config_store = config_store
        self.owner_id = owner_id
        self.adapters = adapters
        self.parser = parser
        self.stop_event = threading.Event()
        self.workers = []
        if start_worker:
            self.start_workers()

    def start_workers(self):
        """装配依赖后再启动，避免后台提前调用尚未绑定的适配器。"""
        if self.workers:
            return
        for platform in PLATFORMS:
            worker = threading.Thread(
                target=server_worker_context().run,
                args=(self._run, platform),
                name=f"orders-{platform}",
                daemon=True,
            )
            self.workers.append(worker)
            worker.start()

    @staticmethod
    def _shops_key(config):
        from erp_web.services.external_request_context import credential_fingerprint
        store = config.get("yandex") or {}
        return f"shops:yandex:{store.get('business_id', '')}:{credential_fingerprint(store.get('api_token', ''))}"

    def shops(self, config=None) -> list[OrderShop]:
        config = self.config_provider() if config is None else config
        shops = []
        for platform, account in configured_accounts(config).items():
            if platform == "yandex":
                saved = self.store.setting(self._shops_key(config))
                if saved:
                    shops.extend(OrderShop.model_validate(row) for row in json.loads(saved))
                    continue
            store = config[platform]
            shops.append(OrderShop(platform=platform, account_id=account,
                                   name=str(store.get("shop_name") or store.get("nickname") or account)))
        return shops

    def save_yandex_shops(self, config, names):
        if self._shops_key(config) != self._shops_key(self.config_provider()):
            raise InterruptedError("Yandex 授权已变化，请重新同步订单")
        self.store.set_setting(self._shops_key(config), json.dumps([
            OrderShop(platform="yandex", account_id=account, name=name).model_dump()
            for account, name in names.items()
        ], ensure_ascii=False))

    def accounts(self):
        result = {}
        for shop in self.shops():
            result.setdefault(shop.platform, []).append(shop.account_id)
        return {platform: tuple(ids) for platform, ids in result.items()}

    def receive(
        self, platform: str, token: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        expected = self.config_store.order_notification_token(platform)
        if not expected or not secrets.compare_digest(expected.encode("utf-8"), token.encode("utf-8")):
            raise PermissionError("订单回调凭据无效")
        event, response = self.parser(platform, body, self.config_provider(), accounts=self.accounts())
        self.store.receive(platform, event)
        return response

    def integrations(self) -> dict[str, Any]:
        accounts = configured_accounts(self.config_provider())
        base = self.config_store.order_notification_public_url()
        return {
            "ok": True,
            "public_url": base,
            "platforms": [
                {
                    "platform": platform,
                    "account_id": accounts.get(platform, ""),
                    "configured": platform in accounts,
                    "callback_url": self._callback_url(base, platform)
                    if base
                    else "",
                    "last_received": self.store.setting("last_received:" + platform),
                }
                for platform in PLATFORMS
            ],
        }

    def _callback_url(self, base, platform):
        owner = self.owner_id
        suffix = f"&owner={owner}" if owner else ""
        return f"{base}/api/{platform}/notifications?token={self.config_store.order_notification_token(platform)}{suffix}"

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
        self.config_store.save_order_notification_public_url(value)
        return self.integrations()

    def sync(self, platform: str = "", *, automatic: bool = False) -> dict[str, Any]:
        # 同步是平台级任务；沿用既有任务身份，适配器在后台发现并读取全部店铺。
        accounts = configured_accounts(self.config_provider())
        if platform:
            accounts = {
                key: value for key, value in accounts.items() if key == platform
            }
        if not accounts:
            raise ValueError("没有已配置的平台账号，请先完成店铺授权")
        operation_ids = self.store.schedule(accounts, now=time.time(), automatic=automatic,
                                            automatic_interval_seconds=self.auto_sync_interval_provider() if automatic else 0)
        return {"ok": True, "operation_ids": operation_ids}

    def _blocks(self, config, platform):
        if self.external_manager is None or self.request_scopes is None:
            return []
        return self.external_manager.matching_status(self.request_scopes(config, accounts=self.accounts()).get(platform, []))

    def sync_status(self):
        config = self.config_provider()
        rows = self.store.sync_status(configured_accounts(config))
        for row in rows:
            blocks = self._blocks(config, row["platform"])
            if not blocks:
                continue
            hard = [b for b in blocks if b["hard"]]
            chosen = (hard or sorted(blocks, key=lambda b: b.get("resume_at") or 0, reverse=True))[0]
            row.update(
                status="blocked" if hard else "cooldown",
                error=chosen["message"],
                next_attempt=0 if hard else max(row["next_attempt"], chosen.get("resume_at") or 0),
            )
        return rows

    def process_one(self, platform: str = "", *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        config = self.config_provider()
        accounts = self.accounts()
        if platform:
            accounts = {
                key: value for key, value in accounts.items() if key == platform
            }
        job = self.store.claim(accounts, now=now)
        if job is None:
            return False
        event = OrderEvent.model_validate_json(job["event_json"])
        progress_failed = False
        refreshed = set()

        def checkpoint():
            if self.stop_event.is_set() or not account_in_scope(self.accounts(), event.platform, event.account_id):
                raise InterruptedError("订单同步已停止或平台账号已切换")
            if event.platform == "yandex" and self._shops_key(config) != self._shops_key(self.config_provider()):
                raise InterruptedError("Yandex 授权已变化，本次任务停止")
            if not self.store.renew_claim(job, now=time.time()):
                raise InterruptedError("订单处理租约已失效")

        try:
            adapter = self.adapters[event.platform](config)
            snapshots = (
                self._sync_snapshots(adapter, event)
                if event.topic == "sync"
                else adapter.read(event)
            )
            with request_operation("orders", operation_id=f"orders:{job['id']}", trigger="background"):
                for snapshot in snapshots:
                    checkpoint()
                    if (
                        snapshot.platform != event.platform
                        or not account_in_scope(self.accounts(), snapshot.platform, snapshot.account_id)
                        or (event.topic != "sync" and snapshot.account_id != event.account_id)
                    ):
                        raise ValueError("订单快照越过账号范围")
                    if not self.store.save_snapshot(job, snapshot, now=time.time()):
                        raise InterruptedError("订单处理租约已失效")
                    if self.progress_sync and snapshot.identity not in refreshed:
                        checkpoint()
                        if not self.progress_sync(snapshot.identity, checkpoint):
                            progress_failed = True
                        checkpoint()
                        refreshed.add(snapshot.identity)
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
            timed = [b for b in blocks if not b["hard"] and b.get("resume_at") is not None]
            hard = any(b["hard"] for b in blocks)
            next_attempt = None
            if timed and not hard:
                retryable = True
                next_attempt = max(time.time() + 1, *(b["resume_at"] for b in timed))
                message = "平台订单同步暂时暂停，将在冷却结束后自动重试。"
            if event.topic == "sync":
                retryable, next_attempt = False, None
                if timed and not hard:
                    message = "平台订单同步暂时暂停，请在冷却结束后点击同步订单。"
            self.store.finish(job, error=message, retryable=retryable, now=time.time(), next_attempt=next_attempt)
        else:
            self.store.finish(job, now=time.time(), error="平台订单已更新，部分采购或仓库状态未更新，请到订单详情查看后手动刷新。" if progress_failed else "")
        return True

    def _sync_snapshots(
        self, adapter: OrderAdapter, event: OrderEvent
    ) -> Iterable[OrderSnapshot]:
        seen = set()
        for snapshot in adapter.sync():
            seen.add(snapshot.identity)
            yield snapshot
        tracked = [snapshot for platform, account in account_pairs(self.accounts()) if platform == event.platform
                   for snapshot in self.store.tracked(platform, account)]
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
                    account_id=snapshot.account_id,
                    topic=topic,
                    resource=resource,
                )
            )

    def _run(self, platform: str) -> None:
        while not self.stop_event.is_set():
            try:
                with release_guard.admit("订单处理") as allowed:
                    worked = allowed and self.process_one(platform)
            except Exception:
                logger.exception("订单后台任务失败：平台=%s", platform)
                worked = False
            self.stop_event.wait(0.1 if worked else 2)

    def close(self) -> None:
        self.stop_event.set()
        for worker in self.workers:
            worker.join(timeout=2)
