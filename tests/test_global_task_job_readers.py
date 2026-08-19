"""通用 Job Status Reader 的受信状态收敛测试（P2-5）。

Controller 不导入领域模块，只依赖按 ``job_type`` 注册的通用读取器；这里
直接验证两个受信读取器（发布 / 选品研究）把领域状态收敛为统一
``job_id → 状态`` 的语义，包括重启后仍可读取持久化终态。
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from erp_web.facades.global_task_facade import (
    PublishJobStatusReader,
    ResearchJobStatusReader,
)


class _FakeBus:
    def __init__(self, public_state: Mapping[str, Any]) -> None:
        self._public_state = dict(public_state)
        self.calls: list[str] = []

    def get_public_status(self, job_id: str) -> Mapping[str, Any]:
        self.calls.append(job_id)
        return self._public_state.get(job_id) or {}


def test_publish_reader_reports_running_until_every_platform_terminal() -> None:
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

    assert reader.read_job_state("job-1") == {"status": "running"}
    assert bus.calls == ["job-1"]


def test_publish_reader_reports_running_when_no_platform_state() -> None:
    reader = PublishJobStatusReader(_FakeBus({}))
    # 无平台状态（尚未产生进度）不能被误判为成功。
    assert reader.read_job_state("missing") == {"status": "running"}


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

    assert reader.read_job_state("job-2") == {"status": "success"}


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

    state = reader.read_job_state("job-3")
    assert state["status"] == "failed"
    assert "平台拒绝。" in state["error"]


def test_research_reader_maps_persisted_run_statuses() -> None:
    runs: dict[str, dict[str, Any]] = {
        "prr-done": {"status": "completed"},
        "prr-fail": {"status": "failed", "error": "研究失败。"},
        "prr-run": {"status": "running"},
    }
    reader = ResearchJobStatusReader(run_loader=lambda run_id: runs.get(run_id))

    assert reader.read_job_state("prr-done") == {"status": "success"}
    failed = reader.read_job_state("prr-fail")
    assert failed["status"] == "failed"
    assert failed["error"] == "研究失败。"
    assert reader.read_job_state("prr-run") == {"status": "running"}


def test_research_reader_is_failed_for_missing_or_cleaned_run() -> None:
    reader = ResearchJobStatusReader(run_loader=lambda run_id: None)

    state = reader.read_job_state("prr-gone")
    assert state["status"] == "failed"
    assert state["error"] == "选品研究运行不存在或已被清理。"


def test_research_reader_survives_restart_via_persisted_loader() -> None:
    # 重启后运行记录仍来自 SQLite 持久层：读取器只信任 loader 返回的终态，
    # 不依赖任何进程内内存状态。
    persisted = {"prr-1": {"status": "completed"}}

    def loader_after_restart(run_id: str) -> Mapping[str, Any] | None:
        return persisted.get(run_id)

    reader = ResearchJobStatusReader(run_loader=loader_after_restart)
    assert reader.read_job_state("prr-1") == {"status": "success"}
