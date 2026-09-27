"""发布结果的单次只读确认与首次延迟检查；不运行模型或推进 Agent。"""

from __future__ import annotations

import copy
import logging
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .publish_confirmation import resolve_publish_store_binding

if TYPE_CHECKING:
    from .publishing_bus_core import PublishingBus

FIRST_CHECK_DELAY = 120
VIEW_CHECK_INTERVAL = 300
MANUAL_CHECK_INTERVAL = 30
CONFIRMABLE_STATUSES = frozenset({"pending_confirmation", "outcome_unknown"})
logger = logging.getLogger(__name__)


def timestamp(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def iso_time(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def has_query_identity(result: dict[str, Any]) -> bool:
    """任务回执或已完成提交的 checkpoint 才能定位本次远端操作。"""
    if result.get("task_id") or any(result.get("task_ids") or []):
        return True
    checkpoint = result.get("checkpoint")
    if isinstance(checkpoint, dict) and checkpoint.get("offer_id"):
        return True
    inner = result.get("result")
    if isinstance(inner, dict) and has_query_identity(inner):
        return True
    return any(
        has_query_identity(row)
        for row in result.get("sku_results", [])
        if isinstance(row, dict)
    )


class PublishResultConfirmation:
    """所有确认触发共用同一入口；首次计划持久化，内存队列只负责唤醒。"""

    def __init__(self, bus: PublishingBus) -> None:
        self.bus = bus
        self._condition = threading.Condition()
        self._scheduled: dict[tuple[str, str], float] = {}
        self._thread: threading.Thread | None = None
        self._closed = False
        self._checking: set[tuple[str, str]] = set()

    def schedule(self, job_id: str, platform: str, due: str) -> None:
        if not due:
            return
        with self._condition:
            if self._closed:
                return
            self._scheduled[(job_id, platform)] = timestamp(due)
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="PublishFirstCheck", daemon=True
                )
                self._thread.start()
            self._condition.notify()

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._closed:
                    return
                if not self._scheduled:
                    self._condition.wait()
                    continue
                key, due = min(self._scheduled.items(), key=lambda item: item[1])
                delay = due - time.time()
                if delay > 0:
                    self._condition.wait(delay)
                    continue
                self._scheduled.pop(key)
            try:
                self.bus.executor.submit(self._scheduled_check, *key)
            except RuntimeError:
                # 关闭过程不丢失计划；下一次启动从持久化记录恢复。
                return

    def _scheduled_check(self, job_id: str, platform: str) -> None:
        try:
            self.check(job_id, platform, trigger="scheduled")
        except Exception:
            logger.exception("首次发布结果检查未完成：%s/%s", job_id, platform)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join()

    def accept(self, job_id: str, platform: str, result: dict[str, Any]) -> None:
        now = time.time()
        state = self.bus._read_state(job_id)
        item = state["platforms"][platform]
        confirmation = item.get("confirmation") or {
            "submitted_at": iso_time(now),
            "next_check_at": iso_time(now + FIRST_CHECK_DELAY),
            "last_checked_at": "",
            "check_error": "",
        }
        self.bus._set_platform(
            job_id, platform, status="pending_confirmation",
            stage="waiting_platform_confirmation", error="",
            result=self.bus._persisted_platform_result(result), confirmation=confirmation,
        )
        self.schedule(job_id, platform, confirmation.get("next_check_at", ""))

    def check(self, job_id: str, platform: str, *, trigger: str = "manual") -> dict[str, Any]:
        if trigger not in {"manual", "view", "scheduled"}:
            raise ValueError("发布结果查询触发来源无效。")
        key = (job_id, platform)
        now = time.time()
        with self.bus._lock:
            state = self.bus._read_state(job_id)
            item = state.get("platforms", {}).get(platform)
            if not isinstance(item, dict):
                raise ValueError("发布任务不包含此平台。")
            status = item.get("status")
            if status not in CONFIRMABLE_STATUSES:
                return self._response(job_id, platform, False, "terminal_or_submitting")
            if key in self._checking:
                return self._response(job_id, platform, False, "checking")
            confirmation = dict(item.get("confirmation") or {})
            last_checked = timestamp(confirmation.get("last_checked_at"))
            submitted = timestamp(confirmation.get("submitted_at") or item.get("created_at"))
            due = timestamp(confirmation.get("next_check_at"))
            if trigger == "scheduled" and (not due or due > now or last_checked):
                return self._response(job_id, platform, False, "not_due")
            interval = MANUAL_CHECK_INTERVAL if trigger == "manual" else VIEW_CHECK_INTERVAL
            if (last_checked and now - last_checked < interval) or (
                trigger == "view" and not last_checked and now - submitted < FIRST_CHECK_DELAY
            ):
                return self._response(job_id, platform, False, "cooldown")
            result = copy.deepcopy(item.get("result"))
            if not isinstance(result, dict):
                raise ValueError("此任务没有可查询的远端结果，请先在平台后台核实。")
            if not has_query_identity(result):
                raise ValueError("此任务没有远端 task_id 或可查询的发布回执，请先在平台后台核实。")
            checker = getattr(self.bus.adapters.get(platform), "poll_publish_status", None)
            if not callable(checker):
                raise ValueError("此平台没有结果查询能力。")
            self._checking.add(key)

        checked_result = None
        check_error = ""
        try:
            config = self.bus.config_provider()
            approval = state.get("approved_publications", {}).get(platform) or {}
            identity = approval.get("store_identity")
            if identity and resolve_publish_store_binding(platform, config).identity != identity:
                raise ValueError("当前店铺与发布时的店铺不一致，请恢复原店铺授权后查询。")
            checked_result = checker(result, config)
            if not isinstance(checked_result, dict):
                raise ValueError("平台没有返回可验证的查询结果。")
            check_error = str(checked_result.get("check_error") or "")
        except Exception as exc:
            checked_result = None
            check_error = str(exc)

        try:
            with self.bus._lock:
                state = self.bus._read_state(job_id)
                item = state["platforms"][platform]
                confirmation.update(
                    {
                        "submitted_at": confirmation.get("submitted_at") or iso_time(submitted or now),
                        "last_checked_at": iso_time(time.time()),
                        "next_check_at": "",
                        "check_error": check_error,
                    }
                )
                resolved_status = str(item["status"])
                resolution = "query_failed" if check_error else "unconfirmed"
                if isinstance(checked_result, dict):
                    remote_status = str(checked_result.get("status") or "").lower()
                    if self.bus._is_pending_publish_result(checked_result):
                        resolved_status, resolution = "pending_confirmation", "pending"
                    elif remote_status == "partial":
                        resolved_status, resolution = "partial", "partially_applied"
                    elif remote_status in {"failed", "real_publish_failed", "not_ready", "skipped"}:
                        resolved_status, resolution = "failed", "not_applied"
                    elif checked_result.get("ok") is True and (
                        remote_status in {"success", "published", "real_publish_success", "imported"}
                        or checked_result.get("external_id") or checked_result.get("item_id") or checked_result.get("id")
                    ):
                        resolved_status, resolution = "success", "applied"
                    elif remote_status == "outcome_unknown":
                        # 已受理的事实不会因为一次确认响应畸形而消失。
                        confirmation["check_error"] = str(checked_result.get("error") or "本次查询未返回可验证的结果。")
                        resolution = "query_failed"
                        checked_result = None
                    else:
                        confirmation["check_error"] = "平台返回的结果无法判定，保留上次发布状态。"
                        resolution = "query_failed"
                        checked_result = None
                    if check_error:
                        resolution = "query_failed"
                stage = {
                    "pending_confirmation": "waiting_platform_confirmation",
                    "success": "finished",
                }.get(resolved_status, resolved_status)
                item.update(
                    {
                        "status": resolved_status,
                        "stage": stage,
                        "confirmation": confirmation,
                        "updated_at": iso_time(time.time()),
                    }
                )
                if checked_result is not None:
                    item["result"] = self.bus._persisted_platform_result(checked_result)
                    item["error"] = (
                        str(checked_result.get("error") or "")
                        if resolved_status in {"failed", "partial", "outcome_unknown"}
                        else ""
                    )
                if resolved_status not in CONFIRMABLE_STATUSES:
                    item["reconciliation"] = {
                        "status": resolution,
                        "checked_at": confirmation["last_checked_at"],
                        "write_replayed": False,
                    }
                    state.pop("terminal_results_persisted", None)
                    state.pop("terminal_persistence_error", None)
                self.bus._write_state(job_id, state)
            self.bus._update_job_status(job_id)
            return self._response(job_id, platform, True, resolution)
        finally:
            with self.bus._lock:
                self._checking.discard(key)

    def _response(self, job_id: str, platform: str, checked: bool, reason: str) -> dict[str, Any]:
        return {
            "ok": True,
            "job_id": job_id,
            "platform": platform,
            "checked": checked,
            "resolved": reason in {"applied", "partially_applied", "not_applied"},
            "resolution": reason,
            "job": self.bus.get_public_status(job_id),
            "summary": self.bus.get_job_summary(job_id),
        }


__all__ = ["PublishResultConfirmation"]
