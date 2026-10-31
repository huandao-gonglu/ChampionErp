"""在线商品领域任务：持久化队列、有限只读确认与人工对账。"""
from __future__ import annotations

import logging
import threading
import time

from erp_web.services.external_request_context import request_operation
from typing import Any, Literal
from uuid import uuid4

from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.product_model.online_operation_model import receipt_errors, submission_status
from erp_web.db import utc_now
from erp_web.schemas.online_products import ChangeRequest, CloseOnlineOperationRequest, OnlineAttributeQuery, OnlineAttributeResponse, snapshot_version
from erp_web.services.online_product_attributes import validate_yandex_attributes
from erp_web.product_model.online_attribute_model import yandex_category_id
from erp_web.services.online_product_changes import confirmation, observed_changes, validate_changes
from erp_web.services.online_product_listing import listing_page, listing_summary_page, project_listing_fields
from erp_web.services.online_product_images import OnlineProductImages
from erp_web.services.online_ozon_export import OnlineOzonExportService
from erp_web.services.online_product_sync import run_sync
from erp_web.services.online_product_batch import preview_batch, submit_batch, run_batch
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
        self.ozon_export = OnlineOzonExportService(self.store, lambda: self._config("yandex")[1])
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

    def _list_context(self, platform: str, catalog: str = "current"):
        if catalog not in ("current", "archived", "all"):
            raise ValueError("商品目录范围只能为 current、archived 或 all")
        config = self.context.config.load_store_config()
        account = account_identity(platform, config)
        records = self.store.listings(platform, account) if account else []
        if catalog != "all":
            records = [row for row in records if row.archived is (catalog == "archived")]
        latest_sync = self.store.latest_sync_summary(platform, account, catalog=catalog) if account else None
        return records, {"ok": True, "platform": platform, "account_id": account, "catalog": catalog,
            "summary": {"total": len(records), "active": sum(r.sale_state == "active" for r in records),
                        "paused": sum(r.sale_state == "paused" for r in records), "attention": sum(r.sale_state not in ("active", "paused") or bool(r.errors) or bool(r.platform_issues) for r in records)},
            "markets": sorted({m.site_id for r in records for m in r.markets}),
            "statuses": sorted({r.raw_status for r in records}), "latest_sync": latest_sync,
            "state": "authorization_required" if not account else ("never_synced" if not latest_sync else "authorization_failed" if "AUTH" in latest_sync["error_code"] else "sync_failed" if latest_sync["status"] == "failed" else "ready"),
            "store_name": str(config.get(platform, {}).get("shop_name") or account),
        }

    def list(self, platform: str, *, query: str = "", status: str = "", market: str = "", page: int = 1, catalog: str = "current") -> dict[str, Any]:
        records, context = self._list_context(platform, catalog)
        jobs = self.store.jobs(platform, context["account_id"]) if context["account_id"] else []
        return {**context, **listing_page(records, query=query, status=status, market=market, page=page),
                "jobs": jobs, "latest_sync": next((job for job in jobs if job["operation"] == "sync" and (catalog == "all" or job["request"].get("catalog", "all") in (catalog, "all"))), None)}

    def read_page(self, platform: str, *, query: str = "", status: str = "", market: str = "",
                  page: int = 1, limit: int = 25, view: Literal["groups", "listings"] = "groups",
                  group_id: str = "", fields: list[str] | None = None, catalog: str = "current") -> dict[str, Any]:
        """按页面同序返回有界摘要或所选业务字段；组内 SKU 可跨页。"""
        if not 1 <= limit <= 50 or page < 1 or view not in ("groups", "listings"):
            raise ValueError("在线商品分页参数无效")
        records, context = self._list_context(platform, catalog)
        return {**context, **listing_summary_page(records, query=query, status=status, market=market,
                page=page, limit=limit, view=view, group_id=group_id, fields=fields)}

    def job_detail(self, job_id: str) -> dict[str, Any]:
        job = self.store.job(job_id)
        self._config(job["platform"], job["account_id"])
        return {"ok": True, "job": job}

    def detail(self, listing_id: str, *, fields: list[str] | None = None) -> dict[str, Any]:
        listing = self.store.get(listing_id)
        self._config(listing.platform, listing.account_id)
        if fields:
            return {"ok": True, "records": [project_listing_fields(listing, fields)]}
        return {"ok": True, "item": listing.model_dump(exclude={"snapshot"})}

    def source_images(self, listing_id: str) -> dict[str, Any]:
        listing = self.store.get(listing_id)
        self._config(listing.platform, listing.account_id)
        capability = listing.capabilities.get("content")
        if not capability or not capability.enabled or "pictures" not in capability.fields:
            raise ValueError("该商品不支持修改图片")
        return self.images.selection(listing).model_dump()

    def attribute_options(self, query: OnlineAttributeQuery) -> dict[str, Any]:
        listing = self.store.get(query.listing_id)
        self._config(listing.platform, listing.account_id)
        if listing.version != query.version:
            raise OnlineConflict("商品快照已更新，请重新打开编辑器")
        capability = listing.capabilities.get("content")
        if listing.details_state != "ready" or listing.errors or not capability or not capability.enabled or "attributes" not in capability.fields:
            raise ValueError("商品详情未完整同步或不支持编辑属性")
        category = yandex_category_id(listing)
        catalog = self.context.category_catalog
        if query.attribute_id:
            definition = catalog.attribute_definitions("yandex", category, site="global")
            attribute = definition.attribute_by_id(query.attribute_id)
            if attribute is None or attribute.read_only or attribute.id == "200":
                raise ValueError("该属性不属于当前商品的可编辑类目字段")
            page = catalog.attribute_values("yandex", category, query.attribute_id, site="global",
                                            query=query.query, cursor=query.cursor, limit=query.limit)
        else:
            page = catalog.public_attribute_page("yandex", category, site="global", cursor=query.cursor, limit=query.limit)
        self._config(listing.platform, listing.account_id)
        if self.store.get(listing.id).version != query.version:
            raise OnlineConflict("读取期间商品已变化，请重新打开编辑器")
        ids = {query.attribute_id} if query.attribute_id else {attribute.id for attribute in page.attributes}
        # 枚举 ID 可能超出 JavaScript 安全整数；旧持久快照仍按原格式读取。
        current = [{key: str(value) if key == "valueId" else value for key, value in row.items()
                    if key in {"id", "parameterId", "value", "valueId", "unitId"}}
                   for row in listing.content.get("attributes", []) if str(row.get("id")) in ids]
        return OnlineAttributeResponse(listing_id=listing.id, version=listing.version, page=page,
                                       current_values=current).model_dump(exclude_none=True)

    def validate_change(self, request: ChangeRequest):
        """页面提交和 AI 审批使用同一份本地版本及平台类目校验。"""
        listing = self.store.get(request.listing_id)
        self._config(listing.platform, listing.account_id)
        if listing.version != request.version:
            raise OnlineConflict("商品快照已更新，请重新读取目标字段和版本后提交，原修改未入队", code="ONLINE_VERSION_STALE")
        self._validate_changes(listing, request)
        self._config(listing.platform, listing.account_id)
        if self.store.get(listing.id).version != request.version:
            raise OnlineConflict("属性校验期间商品已更新，请重新读取目标字段和版本后提交，原修改未入队", code="ONLINE_VERSION_STALE")
        return listing

    def _validate_changes(self, listing, request):
        validate_changes(listing, request)
        if listing.platform == "yandex" and "attributes" in request.changes:
            validate_yandex_attributes(listing, request.changes["attributes"], self.context.category_catalog)

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

    def sync(self, platform: str, key: str, *, ids: list[str] | None = None, catalog: str = "current") -> dict[str, Any]:
        if not 8 <= len(key.strip()) <= 128:
            raise ValueError("同步提交键长度必须为 8–128 字符")
        if catalog not in ("current", "archived"):
            raise ValueError("同步目录只能为 current 或 archived")
        if catalog == "archived" and platform != "yandex":
            raise ValueError("该平台暂不支持单独同步归档目录")
        _, account = self._config(platform)
        job = self.store.enqueue(platform, account, "sync", "*", {"ids": ids, "catalog": catalog}, key)
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
        listing = self.validate_change(request)
        if "pictures" in request.changes:
            self.images.validate(listing, request.changes["pictures"])
        job = self.store.enqueue(listing.platform, listing.account_id, request.operation, listing.id, request.model_dump(), request.idempotency_key)
        self._wake.set()
        return {"ok": True, "job": job}

    def run_once(self) -> bool:
        job = self.store.claim(self.owner)
        if not job:
            return False
        with request_operation("online_products", operation_id=job["id"], trigger="background", cancel=self._stop):
            if job.get("batch_jobs"):
                return run_batch(self, job["batch_jobs"])
            return self._run_job(job)

    def preview_batch(self, body):
        return preview_batch(self, body)

    def change_batch(self, body):
        return submit_batch(self, body)

    def _run_job(self, job):
        dispatched = bool(job["dispatched"])
        write_returned = False
        result: dict[str, Any] = job["result"] if dispatched else {"items": [], "completed": 0, "failed": 0, "created": 0, "updated": 0}
        try:
            adapter = self._adapter(job["platform"], job["account_id"])
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
            self._validate_changes(fresh, request)
            result = {"before": before.model_dump(exclude={"snapshot"}), "changes": request.changes, "scope_id": request.scope_id,
                      "receipt_target": fresh.snapshot.get("siteless_id") or fresh.remote_id}
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
            result["submitted_at"] = utc_now()
            # 写前日志是崩溃边界；此后任何没有明确拒绝证据的错误都按结果未知处理。
            self._update(job, "running", result, dispatched=True)
            dispatched = True
            if request.operation == "sale_state" and request.changes["state"] == "paused":
                self.store.sale_intent(before.id, "paused", lease=job)
            receipt = adapter.write(fresh, request.operation, request.scope_id, prepared_changes)
            write_returned = True
            result["receipt"] = receipt
            status = submission_status(job["platform"], job["operation"], receipt, model=fresh.model,
                                       target_id=result["receipt_target"], scope_id=request.scope_id)
            if status in ("failed", "partial"):
                result["platform_errors"] = receipt_errors(receipt)
            if status == "confirmed" and request.operation == "archive":
                self.store.archive_state(before.id, request.changes["archived"], lease=job)
                result["platform_confirmation"] = {"note": "平台已确认归档操作成功；买家端展示更新可能稍有延迟"}
            if status == "failed" and request.operation == "sale_state":
                self.store.sale_intent(before.id, before.desired_sale_state, lease=job)
            elif status in {"submitted", "confirmed"} and request.operation == "sale_state":
                self.store.sale_intent(before.id, request.changes["state"], lease=job)
            self._update(job, status, result, dispatched=True)
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
        result = {**{key: value for key, value in job["result"].items() if key.startswith(("batch_", "task_"))}, **result}
        if "confirmation_lease_until" in job:
            kwargs["lease_until"] = job["confirmation_lease_until"]
        self.store.update_job(job["id"], status, result, owner=job["lease_token"], **kwargs)

    def _prepare_confirmation(self, job, result):
        # 保存本轮业务核对时间，使旧证据失效；调度和冷却归统一队列。
        result.pop("automatic_confirmation_pending", None)
        result["last_confirmation_at"] = time.time()
        result.pop("next_confirmation_at", None)
        # 新查询失败或进程中断时，不允许拿上次成功证据解除阻塞。
        result.pop("evidence", None)
        result.pop("check_error", None)
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
        # 展示尚未更新不撤销明确受理；只有真实异步处理或写入未知才保留等待。
        status = "confirmed" if checked and all(checked.values()) else (
            "outcome_unknown" if job["status"] == "outcome_unknown"
            else submission_status(job["platform"], job["operation"], result.get("receipt"), model=current.model,
                                   target_id=result.get("receipt_target", ""), scope_id=request.scope_id)
        )
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
        if platform_result.get("needs_attention"):
            status = "needs_attention"
        elif platform_result.get("completion") in {"confirmed", "partial", "failed"}:
            status = platform_result["completion"]
        if status == "confirmed" and request.operation == "archive":
            self.store.archive_state(fresh.id, request.changes["archived"], lease=job)
        if status == "confirmed" and request.operation == "sale_state":
            self.store.sale_intent(fresh.id, request.changes["state"], lease=job)
        elif status == "failed" and request.operation == "sale_state":
            self.store.sale_intent(fresh.id, result.get("before", {}).get("desired_sale_state", ""), lease=job)
        result["evidence"] = {"version": fresh.version, "synced_at": fresh.synced_at,
                              "checked_at": utc_now(), "observed": observed_changes(fresh, request)} if not fresh.errors else {}
        self._update(job, status, result, dispatched=True)

    def reconcile(self, job_id: str) -> dict[str, Any]:
        response = self.context.task_queue.check(f"online:{job_id}")
        if not response["checked"]:
            raise OnlineConflict({"cooldown": "刚刚查询过，请稍后再查", "checking": "已有结果查询正在执行"}.get(
                response["resolution"], (response["task"].get("confirmation") or {}).get("error") or "当前任务无需查询"))
        return {"ok": True, "job": self.store.job(job_id)}

    def reconcile_once(self, job_id: str, *, lease_until: float | None = None) -> dict[str, Any]:
        job = self.store.job(job_id)
        self._config(job["platform"], job["account_id"])
        job = self.store.claim_reconcile(job_id, lease_until=lease_until)
        result = job["result"]
        try:
            self._prepare_confirmation(job, result)
            with request_operation("online_products", operation_id=job["id"], trigger="manual_confirmation"):
                self._confirm(job, self._adapter(job["platform"], job["account_id"]), result)
        except Exception as exc:
            result["error"] = str(exc)
            result["check_error"] = str(exc)
            # 后续只读查询失败不能把已有明确受理改成写入结果未知。
            self._update(job, job["status"], result, dispatched=True)
        return {"ok": True, "job": self.store.job(job_id)}

    def close_operation(self, body: dict[str, Any]) -> dict[str, Any]:
        request = CloseOnlineOperationRequest.model_validate(body)
        job = self.store.job(request.job_id)
        self._config(job["platform"], job["account_id"])
        return {"ok": True, "job": self.store.close_operation(request.job_id, request.expected_updated_at)}

    def retry(self, job_id: str, key: str) -> dict[str, Any]:
        job = self.store.job(job_id)
        self._config(job["platform"], job["account_id"])
        if job["status"] not in ("failed", "partial"):
            raise OnlineConflict("仅明确失败的操作可以重试；未知结果必须先查询")
        if job["operation"] == "sync":
            ids = [row["remote_id"] for row in job["result"].get("items", []) if row["status"] == "failed"]
            if not ids:
                raise ValueError("没有已识别的失败商品；发现阶段失败请重新同步店铺")
            return self.sync(job["platform"], key, ids=ids, catalog=job["request"].get("catalog", "current"))
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
