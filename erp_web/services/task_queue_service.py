"""统一发布/在线修改的有限确认调度；结果由原领域服务结算。"""
from __future__ import annotations

import logging
import threading
import time
from erp_web.release_guard import release_guard

from .desktop_connection_service import server_worker_context

from erp_web.product_model.task_queue_model import PENDING_RESULTS, task_timestamp
from erp_web.schemas.task_queue import confirmation_policy
from erp_web.services.external_request_context import request_operation
from erp_web.stores.task_confirmation_store import TaskConfirmationStore
from erp_web.stores.task_queue_reader import TaskQueueReader

logger = logging.getLogger(__name__)


class TaskQueueService:
    def __init__(self, context):
        self.context = context
        self.reader = TaskQueueReader(context.db)
        self.store = TaskConfirmationStore(context.paths.data_dir / "task-confirmations.sqlite3")
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=server_worker_context().run, args=(self._loop,), name="task-confirmations", daemon=True)
            self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _loop(self):
        while not self._stop.is_set():
            try:
                if release_guard.accepting():
                    self.run_once()
            except Exception:
                logger.exception("任务结果确认失败，已保留原业务记录")
            self._stop.wait(2)

    def _plan(self, task):
        plan = self.store.ensure(task, submitted_at=task_timestamp(task["_submitted_at"]),
            policy=confirmation_policy(task["source"], task["platform"], task["operation"], len(task["_result"].get("sku_results") or [1]), settings=self.context.config.system_settings()["sync_policies"]),
            legacy_checks=task["_legacy_checks"])
        if not task["_can_query"]:
            self.store.stop(task["id"], "没有可定位原请求的远端回执，请在平台后台核对")
        elif task["origin"] == "unknown":
            self.store.stop(task["id"], "历史任务未启用新的自动查询；请核对平台结果")
        elif task["_attention"]:
            self.store.stop(task["id"], task["error"] or "平台要求处理，请核对详情")
        elif time.time() >= plan["deadline_at"]:
            self.store.stop(task["id"], "自动确认期限已到，平台结果尚未确认；可手动查询平台结果或核对平台后台")
        return self.store.get(task["id"])

    def _project(self, task):
        task["can_check"] = bool(task["_can_query"] and (task["source"] == "publish" or task["business_status"] in PENDING_RESULTS))
        if task["business_status"] in PENDING_RESULTS:
            plan = self._plan(task)
            task["confirmation"] = self.store.summary(plan)
            if plan["state"] == "needs_attention":
                task["status"] = "needs_attention"
        else:
            task["confirmation"] = self.store.summary(self.store.get(task["id"]))
            if task["confirmation"] and task["confirmation"]["state"] != "checking":
                task["confirmation"]["state"] = "done"
                task["confirmation"]["next_check_at"] = 0
        return {key: value for key, value in task.items() if not key.startswith("_")}

    def list(self, query):
        tasks = self.reader.tasks()
        accounts = sorted({task["account_id"] for task in tasks if task["account_id"]})
        for key in ("source", "origin", "platform", "operation", "account_id"):
            if query.get(key):
                tasks = [task for task in tasks if task[key] == query[key]]
        if query.get("q"):
            needle = query["q"].strip().casefold()
            tasks = [task for task in tasks if needle in " ".join(str(task[key]) for key in ("id", "title", "target_id", "error")).casefold()]
        for key, compare in (("since", lambda a, b: a >= b), ("until", lambda a, b: a < b + 86400)):
            if query.get(key):
                bound = task_timestamp(query[key])
                tasks = [task for task in tasks if compare(task_timestamp(task["created_at"]), bound)]
        tasks.sort(key=lambda task: (task_timestamp(task["created_at"]), task["id"]), reverse=True)
        page = max(1, int(query.get("page") or 1))
        offset = (page - 1) * 30
        status = query.get("status")
        total = 0
        items = []
        for task in tasks:
            # 待确认任务的展示状态取决于确认计划，筛选时须先解析。
            projected = self._project(task) if status and task["business_status"] in PENDING_RESULTS else None
            if status and (projected or task)["status"] != status:
                continue
            if offset <= total < offset + 30:
                items.append(projected or self._project(task))
            total += 1
        return {"ok": True, "items": items, "total": total, "page": page, "accounts": accounts}

    def detail(self, task_id):
        task = self.reader.task(task_id)
        if task is None:
            raise ValueError("任务不存在")
        if task["source"] == "publish":
            detail = self.context.publishing_bus.get_public_status(task["job_id"])["platforms"][task["platform"]]
        else:
            detail = self.context.online_products.store.job(task["job_id"])
        return {"ok": True, "task": self._project(task), "detail": detail}

    def submission_retried(self, task_id):
        self.store.submission_retried(task_id)

    def run_once(self):
        self.context.publishing_bus.retry_terminal_persistence()
        for task in self.reader.tasks(pending_only=True):
            if self._stop.is_set() or not release_guard.accepting():
                return
            plan = self._plan(task)
            if plan["state"] in {"waiting", "checking"} and plan["next_check_at"] <= time.time():
                self.check(task["id"], trigger="scheduled")

    def check(self, task_id, *, trigger="manual"):
        if trigger not in {"manual", "scheduled"}:
            raise ValueError("结果查询只接受手动或任务队列调度。")
        task = self.reader.task(task_id)
        if task is None:
            raise ValueError("任务不存在")
        manual_publish = trigger == "manual" and task["source"] == "publish" and task["_can_query"]
        if task["business_status"] not in PENDING_RESULTS and not manual_publish:
            return {"checked": False, "resolution": "terminal_or_submitting", **self.detail(task_id)}
        self._plan(task)
        if not task["_can_query"]:
            return {"checked": False, "resolution": "unqueryable", **self.detail(task_id)}
        lease, reason = self.store.claim(task_id, manual=trigger == "manual")
        if not lease:
            return {"checked": False, "resolution": reason, **self.detail(task_id)}
        error, attention, resolution = "", False, "checked"
        try:
            with request_operation("task_confirmation", operation_id=task["job_id"], trigger=trigger,
                deadline_at=lease["deadline"], cancel=self._stop,
                before_send=lambda ctx: self.store.consume_request(task_id, lease, ctx)):
                if task["source"] == "publish":
                    result = self.context.publishing_bus.confirmation.check(task["job_id"], task["platform"])
                    resolution = result["resolution"]
                else:
                    self.context.online_products.reconcile_once(task["job_id"], lease_until=lease["deadline"]+30)
            current = self.reader.task(task_id)
            if current["source"] == "publish":
                detail = self.context.publishing_bus.get_status(current["job_id"])["platforms"][current["platform"]]
                error = str(detail.get("confirmation", {}).get("check_error") or "")
            else:
                error = str(current["_result"].get("check_error") or "")
            attention = current["_attention"]
        except ValueError as exc:
            # 正常同步/修改占位只推迟本轮；授权或任务身份问题才需要人工处理。
            error, attention = str(exc), not bool(getattr(exc, "job_id", None))
        finally:
            current = self.reader.task(task_id)
            self.store.finish(task_id, lease["token"], done=current["business_status"] not in PENDING_RESULTS,
                              attention=attention, error=error)
        return {"checked": True, "resolution": "query_failed" if error else resolution, **self.detail(task_id)}


__all__ = ["TaskQueueService"]
