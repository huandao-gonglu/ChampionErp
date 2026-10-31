"""在线商品任务到现有 Agent Job Reader 的只读投影，不执行模型或推进平台任务。"""
from collections.abc import Callable
from typing import Any

from erp_web.schemas.domain_jobs import JobStateSnapshot


_LABELS = {"queued": "排队中", "running": "执行中", "submitted": "平台已受理",
           "waiting_confirmation": "等待平台确认", "confirmed": "已完成",
           "partial": "部分完成", "failed": "失败", "outcome_unknown": "结果未知，需回读",
           "needs_attention": "需要处理",
           "closed_unconfirmed": "已人工结束，原结果未确认"}


class OnlineProductJobReader:
    def __init__(self, loader: Callable[[str], dict[str, Any]]):
        self.loader = loader

    def read_job_state(self, job_id: str) -> JobStateSnapshot:
        try:
            job = self.loader(job_id)
        except ValueError as exc:
            return JobStateSnapshot(status="failed", available=False, error=str(exc)[:2000])
        status, result = job["status"], job["result"]
        # 受理后本次提交已完成；需人工查询的异步结果不让 Agent 永久等待。
        lifecycle = ("success" if status in ("confirmed", "submitted") else
                     "running" if status in ("queued", "running") else
                     "partial" if status == "partial" else "failed")
        summary = f"{job['platform']} · {job['operation']}：{_LABELS.get(status, status)}"
        if status == "submitted":
            summary += "；尚未核验展示是否生效，可按需查询平台结果。"
        if job["operation"] == "sync":
            summary += f"；成功 {result.get('completed', 0)}，失败 {result.get('failed', 0)}"
            if result.get("completed", 0):
                summary += "；成功项已保存，后续尝试失败不会撤销这些更新。"
            if status in ("partial", "failed"):
                if result.get("discovery_complete") is False:
                    summary += "；目录发现未完成，已发现数量不是全店总数，需重新同步目录。"
                elif not any(item.get("status") == "failed" for item in result.get("items", [])):
                    summary += "；未取得可重试的失败项，需重新同步目录。"
                else:
                    summary += "；继续处理时只重试失败项，不重复全量同步。"
        if lifecycle == "failed":
            summary += "；未确认全部生效，请读取任务详情中的逐项证据。"
        return JobStateSnapshot(status=lifecycle, stage_code=status, stage_label=_LABELS.get(status, status),
                                summary=summary[:500], last_external_status=status,
                                error=str(result.get("error") or (summary if lifecycle == "failed" else ""))[:2000])
