"""领域发布与研究 Job 的有界只读状态；不选择或推进 Agent 工具。"""

from __future__ import annotations
from datetime import datetime
from typing import Any, Callable, Mapping, cast
from erp_web.schemas.domain_jobs import (
    JobLifecycleStatus,
    JobStateActivity,
    JobStateSnapshot,
)
from erp_web.services import product_research_service

_PUBLISH_ACTIVITY_ORDER = (
    "offer_mapping",
    "campaign_offer",
    "price",
    "stock",
    "confirmation",
)

#: 内部步骤 code → 用户可见名称（前端不识别平台专用 checkpoint）。
_PUBLISH_ACTIVITY_LABELS = {
    "offer_mapping": "提交商品资料",
    "campaign_offer": "加入店铺",
    "price": "更新价格",
    "stock": "更新库存",
    "confirmation": "确认平台状态",
}

#: 发布阶段 / 总线 stage → 用户可见阶段名称。
_PUBLISH_STAGE_LABELS = {
    "queued": "排队等待执行",
    "pending": "排队等待执行",
    "resolving_category": "解析商品类目",
    "publishing": "正在提交发布",
    "publishing_approved_payload": "正在提交发布",
    "waiting_platform_confirmation": "等待平台确认",
    "offer_mapping": "提交商品资料",
    "campaign_offer": "加入店铺",
    "price": "更新价格",
    "stock": "更新库存",
    "confirmation": "等待平台确认",
    "terminal": "已完成",
    "finished": "已完成",
    "success": "已完成",
    "retrying": "退避重试中",
    "failed": "发布失败",
}


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit]


def _parse_datetime(value: Any) -> datetime | None:
    """把 ISO / 本地时间字符串解析为带时区 datetime；失败返回 None。"""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        # PublishingBus 使用本地 naive 时间；按本地时区解释，保证耗时正确。
        parsed = parsed.astimezone()
    return parsed


def _epoch_datetime(value: Any) -> datetime | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds).astimezone()
    except (OverflowError, OSError, ValueError):
        return None


def _publish_focus_entry(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """选择一个用于展示进度的平台条目：活跃优先，其次含 checkpoint。"""

    active = [
        item
        for item in entries
        if str(item.get("status") or "").strip().lower()
        in {"queued", "pending", "running", "retrying"}
    ]
    candidates = active or entries
    for item in candidates:
        result = item.get("result")
        if isinstance(result, dict) and isinstance(result.get("checkpoint"), dict):
            return item
    return candidates[0] if candidates else None


def _publish_checkpoint(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """提取平台 checkpoint。

    进行中的 pending 结果把 checkpoint 放在 ``result.result.checkpoint``；
    终态结果放在 ``result.checkpoint``。与轮询函数相同的防御性查找。
    """

    if not isinstance(entry, dict):
        return None
    result = entry.get("result")
    if not isinstance(result, dict):
        return None
    checkpoint = result.get("checkpoint")
    if isinstance(checkpoint, dict):
        return checkpoint
    inner = result.get("result")
    if isinstance(inner, dict):
        inner_checkpoint = inner.get("checkpoint")
        if isinstance(inner_checkpoint, dict):
            return inner_checkpoint
    return None


def _evidence_time(evidence: Mapping[str, Any], code: str) -> datetime | None:
    item = evidence.get(code)
    if not isinstance(item, dict):
        return None
    return _parse_datetime(item.get("at") or item.get("checked_at"))


def _publish_activities(
    checkpoint: Mapping[str, Any],
) -> tuple[JobStateActivity, ...]:
    completed = {str(step) for step in (checkpoint.get("completed_steps") or [])}
    evidence = (
        checkpoint.get("evidence")
        if isinstance(checkpoint.get("evidence"), dict)
        else {}
    )
    activities: list[JobStateActivity] = []
    running_assigned = False
    for code in _PUBLISH_ACTIVITY_ORDER:
        if code in completed:
            activity_status = "completed"
            completed_at = _evidence_time(evidence, code)
        elif not running_assigned:
            activity_status = "running"
            completed_at = None
            running_assigned = True
        else:
            activity_status = "queued"
            completed_at = None
        activities.append(
            JobStateActivity(
                code=code,
                label=_PUBLISH_ACTIVITY_LABELS.get(code, code),
                status=activity_status,
                completed_at=completed_at,
            )
        )
    return tuple(activities)


def _publish_phase_started_at(
    checkpoint: Mapping[str, Any],
) -> datetime | None:
    """当前阶段开始时间 ≈ 上一个已完成步骤的完成时间。"""

    completed = {str(step) for step in (checkpoint.get("completed_steps") or [])}
    evidence = (
        checkpoint.get("evidence")
        if isinstance(checkpoint.get("evidence"), dict)
        else {}
    )
    previous: str | None = None
    for code in _PUBLISH_ACTIVITY_ORDER:
        if code not in completed:
            break
        previous = code
    if previous is None:
        return None
    return _evidence_time(evidence, previous)


def _publish_display_fields(
    raw: Mapping[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """从发布 Job 公共状态提取白名单展示字段。"""

    attempts = [int(item.get("attempts") or 0) for item in entries]
    fields: dict[str, Any] = {
        "updated_at": _parse_datetime(raw.get("updated_at")),
        "attempt": (max(attempts) if any(value > 0 for value in attempts) else None),
    }

    focus = _publish_focus_entry(entries)
    checkpoint = _publish_checkpoint(focus)
    if checkpoint is not None:
        phase = str(checkpoint.get("phase") or "").strip().lower()
        stage_label = _PUBLISH_STAGE_LABELS.get(phase, "正在执行发布")
        summary = (
            "远端写入已完成，正在确认店铺商品状态"
            if phase == "confirmation"
            else f"正在{stage_label}"
        )
        last_response = (
            checkpoint.get("last_response_summary")
            if isinstance(checkpoint.get("last_response_summary"), dict)
            else {}
        )
        fields.update(
            {
                "stage_code": _truncate(phase, 120),
                "stage_label": _truncate(stage_label, 200),
                "summary": _truncate(summary, 500),
                "retry_count": max(0, int(checkpoint.get("retries") or 0)),
                "next_check_at": _epoch_datetime(checkpoint.get("next_poll_at")),
                "last_external_status": _truncate(
                    last_response.get("status") or "", 80
                ),
                "phase_started_at": _publish_phase_started_at(checkpoint),
                "activities": _publish_activities(checkpoint),
            }
        )
        return fields

    stage = str((focus or {}).get("stage") or "").strip().lower()
    if stage:
        fields.update(
            {
                "stage_code": _truncate(stage, 120),
                "stage_label": _truncate(_PUBLISH_STAGE_LABELS.get(stage, stage), 200),
                "summary": _truncate(
                    _PUBLISH_STAGE_LABELS.get(stage, "正在执行发布"), 500
                ),
            }
        )
    return fields


def _research_activities(run: Mapping[str, Any]) -> tuple[JobStateActivity, ...]:
    source_status = (
        run.get("source_status") if isinstance(run.get("source_status"), list) else []
    )
    activities: list[JobStateActivity] = []
    for item in source_status:
        if not isinstance(item, dict):
            continue
        code = str(item.get("source_id") or item.get("source") or "").strip()
        if not code:
            continue
        raw_status = str(item.get("status") or "").strip().lower()
        if raw_status in {"failed", "error"}:
            activity_status = "failed"
        else:
            activity_status = "completed"
        activities.append(
            JobStateActivity(
                code=_truncate(code, 80),
                label=_truncate(str(item.get("source") or code), 200),
                status=activity_status,
            )
        )
    return tuple(activities[:50])


class PublishJobStatusReader:
    """把 PublishingBus 的平台状态收敛为通用 job_id → 类型化快照。

    生命周期字段（status/error）与原实现一致；展示字段从已持久化的平台
    checkpoint 白名单映射而来（阶段、内部活动、重试、下次检查、最近外部
    状态），绝不透传凭据、完整 payload 或原始平台对象。
    """

    def __init__(self, publishing_bus: Any) -> None:
        self._bus = publishing_bus

    def read_job_state(self, job_id: str) -> JobStateSnapshot:
        try:
            raw = self._bus.get_public_status(job_id)
        except Exception:
            # Job 不存在或读取失败：生命周期保持 running（不误判终态），
            # 任务卡展示降级为“暂时无法读取后台任务进度”。
            return JobStateSnapshot(status="running", available=False)
        if not isinstance(raw, dict) or not raw:
            return JobStateSnapshot(status="running", available=False)

        platforms = (
            raw.get("platforms") if isinstance(raw.get("platforms"), dict) else {}
        )
        entries = [item for item in platforms.values() if isinstance(item, dict)]
        statuses = [str(item.get("status") or "").strip().lower() for item in entries]

        # 生命周期判定与原实现保持一致。
        status: str
        error = ""
        if not statuses or any(
            value in {"queued", "pending", "running", "retrying"} for value in statuses
        ):
            status = "running"
        elif all(value == "success" for value in statuses):
            status = "success"
        else:
            errors = [
                str(item.get("error") or "").strip()
                for item in entries
                if item.get("error")
            ]
            status = "failed"
            error = "；".join(item for item in errors if item) or "平台任务失败。"

        display = _publish_display_fields(raw, entries)
        return JobStateSnapshot(
            status=cast(JobLifecycleStatus, status),
            error=_truncate(error, 2000),
            available=True,
            **display,
        )


class ResearchJobStatusReader:
    """把热门选品研究运行状态收敛为通用 job_id → 类型化快照。

    运行记录持久化在 SQLite（ProductResearchRunRegistry），进程重启后仍可
    读取终态；后台工具对账器只依赖这里注册的通用读取器。
    """

    def __init__(
        self,
        run_loader: Callable[[str], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self._run_loader = run_loader or product_research_service.get_hot_product_run

    def read_job_state(self, job_id: str) -> JobStateSnapshot:
        run = self._run_loader(str(job_id or "").strip())
        if run is None:
            return JobStateSnapshot(
                status="failed",
                error="选品研究运行不存在或已被清理。",
            )
        status = str(run.get("status") or "").strip().lower()
        error = ""
        if status == "completed":
            lifecycle: str = "success"
        elif status == "failed":
            lifecycle = "failed"
            error = str(run.get("error") or run.get("description") or "").strip()
        else:
            lifecycle = "running"

        summary = str(
            run.get("progress_description") or run.get("description") or ""
        ).strip()
        return JobStateSnapshot(
            status=cast(JobLifecycleStatus, lifecycle),
            error=_truncate(error, 2000),
            available=True,
            summary=_truncate(summary, 500),
            updated_at=_parse_datetime(
                run.get("completed_at") or run.get("created_at")
            ),
            activities=_research_activities(run),
        )
