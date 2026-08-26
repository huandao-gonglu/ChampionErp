"""通用 Job Status Reader 的受信状态收敛测试（P2-5）。

Controller 不导入领域模块，只依赖按 ``job_type`` 注册的通用读取器；这里
直接验证两个受信读取器（发布 / 选品研究）把领域状态收敛为统一的类型化
``JobStateSnapshot``：生命周期字段（status/error）驱动任务推进，展示字段
（阶段、内部活动、重试、下次检查）白名单映射给任务卡，且重启后仍可读取
持久化终态。
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from erp_web.facades.global_task_facade import (
    PublishJobStatusReader,
    ResearchJobStatusReader,
)
from erp_web.schemas.global_tasks import JobStateSnapshot


class _FakeBus:
    def __init__(self, public_state: Mapping[str, Any]) -> None:
        self._public_state = dict(public_state)
        self.calls: list[str] = []

    def get_public_status(self, job_id: str) -> Mapping[str, Any]:
        self.calls.append(job_id)
        return self._public_state.get(job_id) or {}


class _RaisingBus:
    """模拟 Job 不存在：PublishingBus 对缺失 job 抛 FileNotFoundError。"""

    def get_public_status(self, job_id: str) -> Mapping[str, Any]:
        raise FileNotFoundError(f"发布任务不存在：{job_id}")


def _yandex_confirmation_state(
    *,
    retries: int = 7,
    campaign_status: str = "CHECKING",
    next_poll_offset: float = 12.0,
) -> dict[str, Any]:
    """模拟 Yandex 远端写入已完成、正在确认平台状态的公共状态。

    与真实 pending 结果一致：checkpoint 嵌套在 ``result.result.checkpoint``。
    """

    now = time.time()
    checkpoint = {
        "phase": "confirmation",
        "completed_steps": [
            "offer_mapping",
            "campaign_offer",
            "price",
            "stock",
        ],
        "retries": retries,
        "next_poll_at": now + next_poll_offset,
        "last_response_summary": {
            "step": "confirmation",
            "status": campaign_status,
            "checked_at": "2026-08-24T00:12:58",
        },
        "evidence": {
            "offer_mapping": {
                "status": "OK",
                "at": "2026-08-24T00:08:22",
            },
            "campaign_offer": {
                "status": "OK",
                "at": "2026-08-24T00:08:28",
            },
            "price": {"status": "OK", "at": "2026-08-24T00:08:33"},
            "stock": {"status": "OK", "at": "2026-08-24T00:08:39"},
        },
    }
    return {
        "job_id": "job-confirmation",
        "status": "running",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "platforms": {
            "yandex": {
                "platform": "yandex",
                "status": "running",
                "stage": "waiting_platform_confirmation",
                "attempts": 1,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "result": {
                    "ok": True,
                    "status": "publish_pending_confirmation",
                    "result": {
                        "platform": "yandex",
                        "status": "pending_confirmation",
                        "offer_id": "SKU-001",
                        "checkpoint": checkpoint,
                    },
                },
            }
        },
    }


def _yandex_terminal_state() -> dict[str, Any]:
    """终态结果把 checkpoint 放在 ``result.checkpoint``（顶层）。"""

    return {
        "job_id": "job-done",
        "status": "completed",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "platforms": {
            "yandex": {
                "platform": "yandex",
                "status": "success",
                "stage": "finished",
                "attempts": 1,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "result": {
                    "ok": True,
                    "status": "real_publish_success",
                    "campaign_status": "PUBLISHED",
                    "checkpoint": {
                        "phase": "terminal",
                        "completed_steps": [
                            "offer_mapping",
                            "campaign_offer",
                            "price",
                            "stock",
                        ],
                        "retries": 0,
                        "next_poll_at": 0,
                        "last_response_summary": {
                            "step": "confirmation",
                            "status": "PUBLISHED",
                            "checked_at": "2026-08-24T00:15:00",
                        },
                        "evidence": {
                            "offer_mapping": {
                                "status": "OK",
                                "at": "2026-08-24T00:08:22",
                            },
                        },
                    },
                },
            }
        },
    }


def test_publish_reader_returns_typed_snapshot() -> None:
    bus = _FakeBus(
        {
            "job-1": {
                "platforms": {
                    "ozon": {"status": "success"},
                    "yandex": {"status": "running"},
                }
            }
        }
    )
    reader = PublishJobStatusReader(bus)

    snapshot = reader.read_job_state("job-1")
    assert isinstance(snapshot, JobStateSnapshot)
    assert snapshot.status == "running"
    assert bus.calls == ["job-1"]


def test_publish_reader_reports_running_when_no_platform_state() -> None:
    reader = PublishJobStatusReader(_FakeBus({}))
    # 无平台状态（Job 尚未产生进度）不能被误判为成功；available=False
    # 让任务卡降级展示，但生命周期仍保持 running。
    snapshot = reader.read_job_state("missing")
    assert snapshot.status == "running"
    assert snapshot.available is False


def test_publish_reader_reports_unavailable_when_job_missing() -> None:
    reader = PublishJobStatusReader(_RaisingBus())
    snapshot = reader.read_job_state("gone")
    assert snapshot.status == "running"
    assert snapshot.available is False


def test_publish_reader_reports_success_only_when_all_platforms_succeed() -> None:
    bus = _FakeBus(
        {
            "job-2": {
                "platforms": {
                    "ozon": {"status": "success"},
                    "yandex": {"status": "success"},
                }
            }
        }
    )
    reader = PublishJobStatusReader(bus)

    assert reader.read_job_state("job-2").status == "success"


def test_publish_reader_reports_failed_with_joined_errors() -> None:
    bus = _FakeBus(
        {
            "job-3": {
                "platforms": {
                    "ozon": {"status": "success"},
                    "yandex": {"status": "failed", "error": "平台拒绝。"},
                }
            }
        }
    )
    reader = PublishJobStatusReader(bus)

    snapshot = reader.read_job_state("job-3")
    assert snapshot.status == "failed"
    assert "平台拒绝。" in snapshot.error


def test_publish_reader_maps_confirmation_checkpoint_progress() -> None:
    reader = PublishJobStatusReader(
        _FakeBus({"job-c": _yandex_confirmation_state()})
    )

    snapshot = reader.read_job_state("job-c")
    assert snapshot.status == "running"
    assert snapshot.stage_code == "confirmation"
    assert snapshot.stage_label == "等待平台确认"
    assert snapshot.retry_count == 7
    assert snapshot.last_external_status == "CHECKING"
    assert snapshot.attempt == 1
    assert snapshot.next_check_at is not None
    # 阶段开始时间来自上一个已完成步骤（stock）的 evidence 时间。
    assert snapshot.phase_started_at is not None


def test_publish_reader_maps_internal_activities_whitelist() -> None:
    reader = PublishJobStatusReader(
        _FakeBus({"job-c": _yandex_confirmation_state()})
    )

    snapshot = reader.read_job_state("job-c")
    codes = [activity.code for activity in snapshot.activities]
    labels = {activity.code: activity.label for activity in snapshot.activities}
    assert codes == [
        "offer_mapping",
        "campaign_offer",
        "price",
        "stock",
        "confirmation",
    ]
    assert labels["offer_mapping"] == "提交商品资料"
    assert labels["confirmation"] == "确认平台状态"

    by_code = {activity.code: activity for activity in snapshot.activities}
    assert by_code["offer_mapping"].status == "completed"
    assert by_code["offer_mapping"].completed_at is not None
    assert by_code["stock"].status == "completed"
    assert by_code["confirmation"].status == "running"


def test_publish_reader_reads_terminal_checkpoint_at_top_level() -> None:
    """终态结果把 checkpoint 放在 result.checkpoint；两种嵌套都要能读取。"""

    reader = PublishJobStatusReader(_FakeBus({"job-d": _yandex_terminal_state()}))

    snapshot = reader.read_job_state("job-d")
    assert snapshot.status == "success"
    assert snapshot.stage_code == "terminal"
    by_code = {activity.code: activity for activity in snapshot.activities}
    assert by_code["offer_mapping"].status == "completed"
    # confirmation 不在 completed_steps 中 → 视为当前/未完成活动。
    assert by_code["confirmation"].status == "running"


def test_publish_reader_does_not_leak_checkpoint_objects() -> None:
    """白名单边界：快照字段固定，不透传 checkpoint/payload 等原始对象。"""

    reader = PublishJobStatusReader(
        _FakeBus({"job-c": _yandex_confirmation_state()})
    )
    snapshot = reader.read_job_state("job-c")
    dumped = snapshot.model_dump(mode="json")
    # 只允许契约内字段；checkpoint、result、payload 等不得出现。
    allowed = {
        "status",
        "error",
        "available",
        "stage_code",
        "stage_label",
        "summary",
        "updated_at",
        "attempt",
        "retry_count",
        "next_check_at",
        "last_external_status",
        "phase_started_at",
        "activities",
    }
    assert set(dumped) == allowed
    for forbidden in ("checkpoint", "result", "payload", "product"):
        assert forbidden not in dumped


def test_research_reader_maps_persisted_run_statuses() -> None:
    runs: dict[str, dict[str, Any]] = {
        "prr-done": {"status": "completed"},
        "prr-fail": {"status": "failed", "error": "研究失败。"},
        "prr-run": {"status": "running"},
    }
    reader = ResearchJobStatusReader(run_loader=lambda run_id: runs.get(run_id))

    assert reader.read_job_state("prr-done").status == "success"
    failed = reader.read_job_state("prr-fail")
    assert failed.status == "failed"
    assert failed.error == "研究失败。"
    assert reader.read_job_state("prr-run").status == "running"


def test_research_reader_exposes_progress_summary_and_activities() -> None:
    runs = {
        "prr-live": {
            "status": "running",
            "progress_description": "已接收 12 个候选商品，AI 仍在搜索。",
            "created_at": "2026-08-24T00:00:00Z",
            "source_status": [
                {"source": "TikTok 热销", "source_id": "tiktok", "status": "success"},
                {"source": "Amazon 榜单", "source_id": "amazon", "status": "failed"},
            ],
        }
    }
    reader = ResearchJobStatusReader(run_loader=lambda run_id: runs.get(run_id))

    snapshot = reader.read_job_state("prr-live")
    assert snapshot.status == "running"
    assert "候选商品" in snapshot.summary
    by_code = {activity.code: activity for activity in snapshot.activities}
    assert by_code["tiktok"].status == "completed"
    assert by_code["amazon"].status == "failed"


def test_research_reader_is_failed_for_missing_or_cleaned_run() -> None:
    reader = ResearchJobStatusReader(run_loader=lambda run_id: None)

    snapshot = reader.read_job_state("prr-gone")
    assert snapshot.status == "failed"
    assert snapshot.error == "选品研究运行不存在或已被清理。"


def test_research_reader_survives_restart_via_persisted_loader() -> None:
    # 重启后运行记录仍来自 SQLite 持久层：读取器只信任 loader 返回的终态，
    # 不依赖任何进程内内存状态。
    persisted = {"prr-1": {"status": "completed"}}

    def loader_after_restart(run_id: str) -> Mapping[str, Any] | None:
        return persisted.get(run_id)

    reader = ResearchJobStatusReader(run_loader=loader_after_restart)
    assert reader.read_job_state("prr-1").status == "success"
