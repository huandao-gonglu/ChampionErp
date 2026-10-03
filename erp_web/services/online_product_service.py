"""在线商品领域任务：持久化队列、有限只读确认与人工对账。"""
from __future__ import annotations

import logging
import threading
import time

from erp_web.services.external_request_context import request_operation
from typing import Any
from uuid import uuid4

from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.schemas.online_products import ChangeRequest, snapshot_version
from erp_web.services.online_product_changes import confirmation, validate_changes
from erp_web.services.online_product_listing import listing_page
from erp_web.services.online_product_images import OnlineProductImages
from erp_web.services.online_product_sync import run_sync
from erp_web.stores.online_product_store import OnlineConflict, OnlineProductStore

logger = logging.getLogger(__name__)


def account_identity(platform: str, config: dict[str, Any]) -> str:
    store = config.get(platform) or {}
    if platform == "mercadolibre":
        return str(store.get("user_id") or "")
    if platform == "ozon":
        return str(store.get("client_id") or "")
    if platform == "yandex":
        return f"{store.get('business_id') or ''}:{store.get('campaign_id') or ''}" if store.get("business_id") and store.get("campaign_id") else ""
    raise ValueError("不支持的平台")


class OnlineProductService:
    def __init__(self, context, *, adapter_factories, start_worker: bool = True):
        self.context = context
        self.adapter_factories = adapter_factories
        self.store = OnlineProductStore(context.db)
        self.images = OnlineProductImages(context)
        self.owner = uuid4().hex
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="online-products", daemon=True)
        if start_worker:
            self._thread.start()

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def _loop(self):
        while not self._stop.is_set():
            try:
                if self.run_once():
                    continue
            except Exception:
                logger.exception("在线商品后台任务异常，已保留持久化状态")
            self._wake.wait(2)
            self._wake.clear()

    def _config(self, platform: str, account: str = "") -> tuple[dict[str, Any], str]:
        config = self.context.config.load_store_config()
        current = account_identity(platform, config)
        if not current or account and account != current:
            raise OnlineConflict("店铺身份缺失或已切换，请重新同步当前店铺")
        return config, current

    def _adapter(self, platform: str, account: str):
        config, _ = self._config(platform, account)
        adapter_type = self.adapter_factories.get(platform)
        if adapter_type is None:
            raise ValueError("该平台契约尚未接通")
        adapter = adapter_type(config)
        if adapter.account_id != account:
            raise OnlineConflict("授权刷新后店铺身份变化，操作已停止")
        return adapter

    def list(self, platform: str, *, query: str = "", status: str = "", market: str = "", page: int = 1) -> dict[str, Any]:
        config = self.context.config.load_store_config()
        account = account_identity(platform, config)
        records = self.store.listings(platform, account) if account else []
        jobs = self.store.jobs(platform, account) if account else []
        latest_sync = next((j for j in jobs if j["operation"] == "sync"), None)
        return {"ok": True, "platform": platform, "account_id": account,
            **listing_page(records, query=query, status=status, market=market, page=page),
            "summary": {"total": len(records), "active": sum(r.sale_state == "active" for r in records),
                        "paused": sum(r.sale_state == "paused" for r in records), "attention": sum(r.sale_state not in ("active", "paused") or bool(r.errors) for r in records)},
            "markets": sorted({m.site_id for r in records for m in r.markets}),
            "statuses": sorted({r.raw_status for r in records}), "latest_sync": latest_sync,
            "state": "authorization_required" if not account else ("never_synced" if not latest_sync else "authorization_failed" if "AUTH" in latest_sync["result"].get("error_code", "") else "sync_failed" if latest_sync["status"] == "failed" else "ready"),
            "jobs": jobs, "store_name": str(config.get(platform, {}).get("shop_name") or account),
        }

    def detail(self, listing_id: str) -> dict[str, Any]:
        listing = self.store.get(listing_id)
        self._config(listing.platform, listing.account_id)
        return {"ok": True, "item": listing.model_dump(exclude={"snapshot"})}

    def source_images(self, listing_id: str) -> dict[str, Any]:
        listing = self.store.get(listing_id)
        self._config(listing.platform, listing.account_id)
        capability = listing.capabilities.get("content")
        if not capability or not capability.enabled or "pictures" not in capability.fields:
            raise ValueError("该商品不支持修改图片")
        return self.images.selection(listing).model_dump()

    def refresh_status(self, listing_id: str) -> dict[str, Any]:
        before = self.store.get(listing_id)
        self._config(before.platform, before.account_id)
        self.store.assert_status_refreshable(before)
        adapter = self._adapter(before.platform, before.account_id)
        status = adapter.read_status(before)
        # 查询过程中切换店铺或并发更新时，不允许旧响应覆盖当前记录。
        self._config(before.platform, before.account_id)
        listing = self.store.update_status(before, status)
        return {"ok": True, "item": listing.model_dump(exclude={"snapshot"})}

    def sync(self, platform: str, key: str, *, ids: list[str] | None = None) -> dict[str, Any]:
        if not 8 <= len(key.strip()) <= 128:
            raise ValueError("同步提交键长度必须为 8–128 字符")
        _, account = self._config(platform)
        job = self.store.enqueue(platform, account, "sync", "*", {"ids": ids}, key)
        self._wake.set()
        return {"ok": True, "job": job}

    def change(self, body: dict[str, Any]) -> dict[str, Any]:
        request = ChangeRequest.model_validate(body)
        listing = self.store.get(request.listing_id)
        self._config(listing.platform, listing.account_id)
        previous = self.store.idempotent_job(request.idempotency_key)
        if previous:
            if previous["request"] != request.model_dump() or previous["account_id"] != listing.account_id:
                raise OnlineConflict("重复提交键对应不同操作，请重新确认")
            return {"ok": True, "job": previous}
        if listing.version != request.version:
            raise OnlineConflict("商品快照已更新，请重新查看并确认变更")
        validate_changes(listing, request)
        if "pictures" in request.changes:
            self.images.validate(listing, request.changes["pictures"])
        job = self.store.enqueue(listing.platform, listing.account_id, request.operation, listing.id, request.model_dump(), request.idempotency_key)
        self._wake.set()
        return {"ok": True, "job": job}

    def run_once(self) -> bool:
        job = self.store.claim(self.owner)
        if not job:
            return False
        with request_operation("online_products", operation_id=job["id"], trigger="auto_confirmation" if job["dispatched"] else "background", cancel=self._stop):
            return self._run_job(job)

    def _run_job(self, job):
        dispatched = bool(job["dispatched"])
        write_returned = False
        result: dict[str, Any] = job["result"] if dispatched else {"items": [], "completed": 0, "failed": 0, "created": 0, "updated": 0}
        try:
            if dispatched and job["operation"] != "sync":
                self._prepare_confirmation(job, result)
            adapter = self._adapter(job["platform"], job["account_id"])
            if job["dispatched"] and job["operation"] != "sync":
                dispatched = True
                result = job["result"]
                self._confirm(job, adapter, result)
                return True
            if job["operation"] == "sync":
                run_sync(self, job, adapter, result)
                return True
            request = ChangeRequest.model_validate(job["request"])
            before = self.store.get(request.listing_id)
            fresh = adapter.read(before.remote_id)
            fresh.desired_sale_state = before.desired_sale_state
            if fresh.errors or snapshot_version(fresh) != request.version:
                if not fresh.errors:
                    self.store.save(fresh, lease=job)
                raise OnlineConflict("平台商品已变化或读取不完整，未提交修改；请重新同步并确认")
            validate_changes(fresh, request)
            result = {"before": before.model_dump(exclude={"snapshot"}), "changes": request.changes, "scope_id": request.scope_id}
            prepared_changes = dict(request.changes)
            if "pictures" in prepared_changes:
                prepared_changes["pictures"] = self.images.prepare(fresh, prepared_changes["pictures"], adapter)
                if any(isinstance(picture, dict) and "asset_id" in picture for picture in request.changes["pictures"]):
                    # 上传等待后再核对平台事实，尚未修改刊登时发现冲突直接停止。
                    fresh = adapter.read(before.remote_id)
                    fresh.desired_sale_state = before.desired_sale_state
                    if fresh.errors or snapshot_version(fresh) != request.version:
                        raise OnlineConflict("图片准备期间平台商品已变化，请重新同步并确认")
                    self.images.validate(fresh, request.changes["pictures"])
            self._config(before.platform, before.account_id)
            result["prepared_changes"] = prepared_changes
            # 写前日志是崩溃边界；此后任何没有明确拒绝证据的错误都按结果未知处理。
            self._update(job, "running", result, dispatched=True)
            dispatched = True
            if request.operation == "sale_state" and request.changes["state"] == "paused":
                self.store.sale_intent(before.id, "paused", lease=job)
            receipt = adapter.write(fresh, request.operation, request.scope_id, prepared_changes)
            write_returned = True
            result["receipt"] = receipt
            result["next_confirmation_at"] = time.time() + 120
            result["automatic_confirmation_pending"] = True
            self._update(job, "submitted", result, dispatched=True)
        except Exception as exc:
            code = int(exc.details.get("http_status") or 0) if isinstance(exc, PublishAdapterError) else 0
            # 回读阶段的 4xx 不能证明之前的修改未执行。
            definite = isinstance(exc, PublishAdapterError) and exc.details.get("definitively_rejected") is True
            earlier_write = isinstance(exc, PublishAdapterError) and exc.details.get("remote_write_dispatched") is True
            rejected = not job["dispatched"] and not write_returned and not earlier_write and (definite or 400 <= code < 500 and code not in (408, 425))
            result["error"] = str(exc)
            result["error_code"] = getattr(exc, "code", "ONLINE_OPERATION_FAILED")
            status = "outcome_unknown" if dispatched and not rejected else "failed"
            if rejected and job["operation"] == "sale_state" and result.get("before"):
                self.store.sale_intent(job["target_id"], result["before"].get("desired_sale_state", ""), lease=job)
            if job["operation"] == "sync" and (result["completed"] or result.get("discovered")):
                status = "partial"
            self._update(job, status, result, dispatched=dispatched)
        return True

    def _update(self, job, status, result, **kwargs):
        self.store.update_job(job["id"], status, result, owner=job["lease_token"], **kwargs)

    def _prepare_confirmation(self, job, result):
        # 发起前持久化冷却时间，认证失败或网络异常也不能绕过查询间隔。
        result["polls"] = int(result.get("polls", 0)) + 1
        result["automatic_confirmation_pending"] = False
        result["last_confirmation_at"] = time.time()
        result.pop("next_confirmation_at", None)
        self._update(job, "running", result, dispatched=True)

    def _confirm(self, job: dict[str, Any], adapter, result: dict[str, Any]):
        request = ChangeRequest.model_validate(job["request"])
        if result.get("prepared_changes") is not None:
            request = request.model_copy(update={"changes": result["prepared_changes"]})
        current = self.store.get(request.listing_id)
        fresh = adapter.read_confirmation(current, request)
        fresh.desired_sale_state = current.desired_sale_state
        fresh.synced_at = current.synced_at
        fresh.status_checked_at = current.status_checked_at
        platform_result = adapter.confirmation_details(fresh, request.operation, result.get("receipt", {}), request.scope_id)
        result["platform_confirmation"] = platform_result
        checked = confirmation(fresh, request) if not fresh.errors else {}
        result["confirmation"] = checked
        result["read_errors"] = fresh.errors
        if not fresh.errors:
            fresh = self.store.save(fresh, lease=job, full_snapshot=False)
        status = "confirmed" if checked and all(checked.values()) else ("outcome_unknown" if job["status"] == "outcome_unknown" else "waiting_confirmation")
        errors = receipt_errors(result.get("receipt", {})) + platform_result.get("errors", [])
        if platform_result.get("pending"):
            status = "waiting_confirmation"
        if errors:
            result["platform_errors"] = errors
            # 混合回执只说明局部失败，不能授权重放整个请求。
            if not platform_result.get("pending"):
                receipt = result.get("receipt", {})
                fully_rejected = receipt.get("status") == "ERROR" or receipt.get("success") is False and not receipt.get("listing_sites")
                status = "failed" if fully_rejected and not any(checked.values()) else "partial"
        if status == "confirmed" and request.operation == "sale_state":
            self.store.sale_intent(fresh.id, request.changes["state"], lease=job)
        result["evidence"] = {"version": fresh.version, "synced_at": fresh.synced_at} if not fresh.errors else {}
        self._update(job, status, result, dispatched=True)

    def reconcile(self, job_id: str) -> dict[str, Any]:
        job = self.store.job(job_id)
        self._config(job["platform"], job["account_id"])
        job = self.store.claim_reconcile(job_id)
        result = job["result"]
        try:
            self._prepare_confirmation(job, result)
            with request_operation("online_products", operation_id=job["id"], trigger="manual_confirmation"):
                self._confirm(job, self._adapter(job["platform"], job["account_id"]), result)
        except Exception as exc:
            result["error"] = str(exc)
            self._update(job, "outcome_unknown", result, dispatched=True)
        return {"ok": True, "job": self.store.job(job_id)}

    def retry(self, job_id: str, key: str) -> dict[str, Any]:
        job = self.store.job(job_id)
        self._config(job["platform"], job["account_id"])
        if job["status"] not in ("failed", "partial"):
            raise OnlineConflict("仅明确失败的操作可以重试；未知结果必须先查询")
        if job["operation"] == "sync":
            ids = [row["remote_id"] for row in job["result"].get("items", []) if row["status"] == "failed"]
            if not ids:
                raise ValueError("没有已识别的失败商品；发现阶段失败请重新同步店铺")
            return self.sync(job["platform"], key, ids=ids)
        changes = dict(job["request"]["changes"])
        checks = job["result"].get("confirmation", {})
        if job["status"] == "partial":
            raise OnlineConflict("平台返回混合结果，无法证明完整失败集合；请查看逐项证据并重新确认变更范围")
        if job["operation"] == "content":
            changes = {k:v for k,v in changes.items() if not checks.get(k)}
        current = self.store.get(job["target_id"])
        # 失败之后若外部业务字段又发生变化，不自动替换原审批基线。
        confirmed_version = job["result"].get("evidence", {}).get("version") or job["request"]["version"]
        if current.version != confirmed_version:
            raise OnlineConflict("失败后商品已变化，请重新查看并确认")
        return self.change({**job["request"], "version": current.version, "changes": changes, "idempotency_key": key})


def receipt_errors(value: Any) -> list[Any]:
    """检查成功 HTTP 响应中的逐项错误，不能只读顶层 success。"""
    errors = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("error", "errors") and item:
                errors.extend(item if isinstance(item, list) else [item])
            elif isinstance(item, (dict, list)):
                errors.extend(receipt_errors(item))
        if value.get("success") is False and not errors:
            errors.append({"message": "平台返回明确失败，但未提供错误详情"})
    elif isinstance(value, list):
        for item in value:
            errors.extend(receipt_errors(item))
    return errors
