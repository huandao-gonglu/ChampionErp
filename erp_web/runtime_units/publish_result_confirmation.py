"""发布结果的单次只读确认；调度与预算由任务队列负责。"""

from __future__ import annotations

import copy
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from erp_web.product_model.task_queue_model import has_query_identity

from .publish_confirmation import resolve_publish_store_binding

if TYPE_CHECKING:
    from .publishing_bus_core import PublishingBus

CONFIRMABLE_STATUSES = frozenset({"pending_confirmation", "outcome_unknown", "needs_attention"})

def iso_time(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


class PublishResultConfirmation:
    """在统一队列领取查询后解释结果，回写原发布记录及草稿。"""

    def __init__(self, bus: PublishingBus) -> None:
        self.bus = bus

    def accept(self, job_id: str, platform: str, result: dict[str, Any]) -> None:
        now = time.time()
        state = self.bus._read_state(job_id)
        item = state["platforms"][platform]
        confirmation = item.get("confirmation") or {
            "submitted_at": iso_time(now),
            "last_checked_at": "",
            "check_error": "",
        }
        confirmation.pop("next_check_at", None)
        self.bus._set_platform(
            job_id, platform, status="pending_confirmation",
            stage="waiting_platform_confirmation", error="",
            result=self.bus._persisted_platform_result(result), confirmation=confirmation,
        )

    def check(self, job_id: str, platform: str) -> dict[str, Any]:
        now = time.time()
        with self.bus._lock:
            state = self.bus._read_state(job_id)
            item = state.get("platforms", {}).get(platform)
            if not isinstance(item, dict):
                raise ValueError("发布任务不包含此平台。")
            status = item.get("status")
            if status not in CONFIRMABLE_STATUSES:
                return self._response(job_id, platform, False, "terminal_or_submitting")
            confirmation = dict(item.get("confirmation") or {})
            confirmation.pop("next_check_at", None)
            result = copy.deepcopy(item.get("result"))
            if not isinstance(result, dict):
                raise ValueError("此任务没有可查询的远端结果，请先在平台后台核实。")
            if not has_query_identity(result):
                raise ValueError("此任务没有远端 task_id 或可查询的发布回执，请先在平台后台核实。")
            checker = getattr(self.bus.adapters.get(platform), "poll_publish_status", None)
            if not callable(checker):
                raise ValueError("此平台没有结果查询能力。")

        checked_result = None
        check_error = ""
        requires_attention = False
        try:
            config = self.bus.config_provider()
            approval = state.get("approved_publications", {}).get(platform) or {}
            identity = approval.get("store_identity")
            if identity and resolve_publish_store_binding(platform, config).identity != identity:
                requires_attention = True
                raise ValueError("当前店铺与发布时的店铺不一致，请恢复原店铺授权后查询。")
            checked_result = checker(result, config)
            if not isinstance(checked_result, dict):
                raise ValueError("平台没有返回可验证的查询结果。")
            check_error = str(checked_result.get("check_error") or "")
        except Exception as exc:
            checked_result = None
            check_error = str(exc)

        with self.bus._lock:
            state = self.bus._read_state(job_id)
            item = state["platforms"][platform]
            confirmation.update(
                {
                    "submitted_at": confirmation.get("submitted_at") or item.get("created_at") or iso_time(now),
                    "last_checked_at": iso_time(time.time()),
                    "check_error": check_error,
                    "requires_attention": requires_attention,
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
                elif remote_status == "needs_attention":
                    resolved_status, resolution = "needs_attention", "needs_attention"
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
                    if resolved_status in {"failed", "partial", "outcome_unknown", "needs_attention"}
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
