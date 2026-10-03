"""选品研究 Job 的状态、活动白名单与持久化读取测试。"""

from __future__ import annotations

from typing import Any, Mapping

from erp_web.runtime_units.domain_job_readers import ResearchJobStatusReader


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
