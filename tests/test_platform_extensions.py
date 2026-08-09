from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from erp_web.context import get_context
from erp_web.marketplace_registry import (
    CAP_CATEGORY_SEARCH,
    CAP_PREVIEW_PAYLOAD,
    CAP_PUBLISH,
    platform_has_capability,
)
from erp_web.runtime_units import ai_use_case
from erp_web.runtime_units.publish_adapter import (
    MercadoLibrePublishingAdapter,
    OzonPublishingAdapter,
    publishing_adapter_for,
    unsupported_publish_response,
)
from erp_web.runtime_units.draft_publish_context import (
    load_required_draft_publish_context,
)
from erp_web.runtime_units.publishing_bus_core import PublishingBus
from erp_web.runtime_units.publish_bus import (
    persist_publish_bus_terminal_results,
)
from erp_web.runtime_units.runtime_api import (
    _remote_publish_pending,
    _remote_publish_succeeded,
)
from erp_web.runtime_units.source_sites import (
    detect_source_site,
    parse_source_snapshot,
    source_site,
)


class _MemoryPublishJobStore:
    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {}

    def save_publish_job(self, state: dict[str, Any]) -> None:
        self.states[str(state["job_id"])] = deepcopy(state)

    def load_publish_job(self, job_id: str) -> dict[str, Any]:
        return deepcopy(self.states.get(job_id, {}))

    def list_pending_publish_jobs(self) -> list[dict[str, Any]]:
        return []

    def list_publish_jobs(
        self,
        *,
        limit: int = 50,
        cursor: str = "",
        platform: str = "",
        product_id: str = "",
    ) -> tuple[list[dict[str, Any]], str]:
        states = sorted(
            (deepcopy(state) for state in self.states.values()),
            key=lambda state: (str(state.get("created_at") or ""), str(state.get("job_id") or "")),
            reverse=True,
        )
        if cursor:
            cursor_index = next(
                (index for index, state in enumerate(states) if state.get("job_id") == cursor),
                len(states),
            )
            states = states[cursor_index + 1 :]
        if platform:
            states = [state for state in states if platform in state.get("platforms", {})]
        if product_id:
            states = [
                state
                for state in states
                if state.get("product", {}).get("product_id") == product_id
            ]
        has_more = len(states) > limit
        items = states[:limit]
        next_cursor = str(items[-1]["job_id"]) if has_more and items else ""
        return items, next_cursor


class _RequiredAttributeAdapter:
    def __init__(self) -> None:
        self.publish_calls = 0

    def resolve_category(
        self,
        product: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        return product

    def required_attributes_missing(
        self,
        product: dict[str, Any],
        config: dict[str, Any],
    ) -> list[str]:
        return ["attributes.BRAND"]

    def publish(
        self,
        product: dict[str, Any],
        platform: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        self.publish_calls += 1
        return {"ok": True}


def test_marketplace_capabilities_only_enable_real_integrations() -> None:
    assert platform_has_capability("mercadolibre", CAP_PUBLISH)
    assert platform_has_capability("mercadolibre", CAP_PREVIEW_PAYLOAD)
    assert platform_has_capability("ozon", CAP_CATEGORY_SEARCH)
    assert platform_has_capability("ozon", CAP_PUBLISH)
    assert platform_has_capability("ozon", CAP_PREVIEW_PAYLOAD)
    assert not platform_has_capability("yandex", CAP_PUBLISH)

    assert isinstance(publishing_adapter_for("mercadolibre"), MercadoLibrePublishingAdapter)
    assert isinstance(publishing_adapter_for("ozon"), OzonPublishingAdapter)
    assert unsupported_publish_response("yandex") == {
        "ok": False,
        "supported": False,
        "platform": "yandex",
        "status": "unsupported",
        "error": "Yandex发布未接入",
    }


def test_publishing_bus_blocks_before_publish_when_required_attributes_are_missing() -> None:
    store = _MemoryPublishJobStore()
    adapter = _RequiredAttributeAdapter()
    bus = PublishingBus(
        store,
        adapters={"mercadolibre": adapter},
        config_provider=lambda: {"mercadolibre": {}},
        max_retries=0,
        auto_resume_pending=False,
    )
    try:
        queued = bus.enqueue(
            {"product_id": "product-1", "name": "缺属性商品"},
            ["mercadolibre"],
            targets={
                "mercadolibre": {
                    "draft_id": "draft-1",
                    "site": "mlm",
                    "product_id": "product-1",
                }
            },
        )
        bus.wait(queued["job_id"], timeout=2)
        state = bus.get_status(queued["job_id"])
    finally:
        bus.executor.shutdown(wait=True)

    platform_state = state["platforms"]["mercadolibre"]
    assert state["status"] == "completed"
    assert platform_state["status"] == "failed"
    assert platform_state["stage"] == "failed"
    assert platform_state["error"] == "缺失必填属性：attributes.BRAND"
    assert adapter.publish_calls == 0


def test_publishing_bus_lists_lightweight_business_status_summaries() -> None:
    store = _MemoryPublishJobStore()
    store.states = {
        "job-failed": {
            "job_id": "job-failed",
            "status": "completed",
            "created_at": "2026-08-06 22:01:40",
            "updated_at": "2026-08-06 22:01:44",
            "product_name": "测试商品",
            "product": {"product_id": "product-1", "large": "x" * 10_000},
            "platforms": {
                "ozon": {
                    "status": "failed",
                    "stage": "failed",
                    "attempts": 1,
                    "error": "合同币种不匹配",
                }
            },
        }
    }
    bus = PublishingBus(store, adapters={}, auto_resume_pending=False)
    try:
        result = bus.list_jobs(status="failed")
    finally:
        bus.executor.shutdown(wait=True)

    assert result["next_cursor"] == ""
    assert result["items"] == [
        {
            "job_id": "job-failed",
            "product_id": "product-1",
            "product_name": "测试商品",
            "draft_id": "",
            "status": "failed",
            "raw_status": "completed",
            "stage": "failed",
            "attempts": 1,
            "error": "合同币种不匹配",
            "platforms": [
                    {
                        "platform": "ozon",
                        "draft_id": "",
                        "site": "",
                        "status": "failed",
                    "stage": "failed",
                    "attempts": 1,
                    "error": "合同币种不匹配",
                    "updated_at": "",
                }
            ],
            "created_at": "2026-08-06 22:01:40",
            "updated_at": "2026-08-06 22:01:44",
        }
    ]
    assert "large" not in result["items"][0]

    public_detail = bus.get_public_status("job-failed")
    assert public_detail["display_status"] == "failed"
    assert "product" not in public_detail


def test_publishing_bus_requires_verified_success_and_runs_terminal_hook() -> None:
    class InvalidSuccessAdapter:
        @staticmethod
        def resolve_category(
            product: dict[str, Any],
            config: dict[str, Any],
        ) -> dict[str, Any]:
            return product

        @staticmethod
        def required_attributes_missing(
            product: dict[str, Any],
            config: dict[str, Any],
        ) -> list[str]:
            return []

        @staticmethod
        def publish(
            product: dict[str, Any],
            platform: str,
            config: dict[str, Any],
        ) -> dict[str, Any]:
            return {}

    store = _MemoryPublishJobStore()
    terminal_states: list[dict[str, Any]] = []

    def on_terminal(state: dict[str, Any]) -> dict[str, Any]:
        terminal_states.append(state)
        return state

    bus = PublishingBus(
        store,
        adapters={"mercadolibre": InvalidSuccessAdapter()},
        terminal_callback=on_terminal,
        max_retries=0,
        auto_resume_pending=False,
    )
    try:
        queued = bus.enqueue(
            {"product_id": "product-1"},
            ["mercadolibre"],
            targets={
                "mercadolibre": {
                    "draft_id": "draft-1",
                    "site": "mlm",
                    "product_id": "product-1",
                }
            },
        )
        bus.wait(queued["job_id"], timeout=2)
        state = bus.get_status(queued["job_id"])
    finally:
        bus.executor.shutdown(wait=True)

    platform_state = state["platforms"]["mercadolibre"]
    assert platform_state["status"] == "failed"
    assert "可验证的成功结果" in platform_state["error"]
    assert len(terminal_states) == 1
    assert state["terminal_results_persisted"] is True


def test_publishing_bus_polls_pending_publish_without_resubmitting() -> None:
    class AsyncAdapter:
        def __init__(self) -> None:
            self.publish_calls = 0
            self.poll_calls = 0

        @staticmethod
        def resolve_category(
            product: dict[str, Any],
            config: dict[str, Any],
        ) -> dict[str, Any]:
            return product

        @staticmethod
        def required_attributes_missing(
            product: dict[str, Any],
            config: dict[str, Any],
        ) -> list[str]:
            return []

        def publish(
            self,
            product: dict[str, Any],
            platform: str,
            config: dict[str, Any],
        ) -> dict[str, Any]:
            self.publish_calls += 1
            return {
                "ok": True,
                "status": "publish_pending_confirmation",
                "result": {"task_id": 172549793, "status": "pending_confirmation"},
                "product": {"large": "not persisted in platform result"},
            }

        def poll_publish_status(
            self,
            result: dict[str, Any],
            config: dict[str, Any],
        ) -> dict[str, Any]:
            self.poll_calls += 1
            assert result["result"]["task_id"] == 172549793
            return {
                "ok": True,
                "status": "real_publish_success",
                "result": {
                    "status": "imported",
                    "task_id": 172549793,
                    "external_id": "137285792",
                },
            }

        @staticmethod
        def publish_poll_interval_seconds(config: dict[str, Any]) -> float:
            return 0.01

    store = _MemoryPublishJobStore()
    adapter = AsyncAdapter()
    bus = PublishingBus(
        store,
        adapters={"ozon": adapter},
        max_retries=0,
        auto_resume_pending=False,
    )
    try:
        queued = bus.enqueue(
            {"product_id": "product-1"},
            ["ozon"],
            targets={
                "ozon": {
                    "draft_id": "draft-1",
                    "site": "global",
                    "product_id": "product-1",
                }
            },
        )
        bus.wait(queued["job_id"], timeout=2)
        state = bus.get_status(queued["job_id"])
    finally:
        bus.executor.shutdown(wait=True)

    assert adapter.publish_calls == 1
    assert adapter.poll_calls == 1
    assert state["status"] == "completed"
    assert state["platforms"]["ozon"]["status"] == "success"
    assert state["platforms"]["ozon"]["result"]["result"]["task_id"] == 172549793
    assert "product" not in state["platforms"]["ozon"]["result"]


def test_pending_remote_result_is_not_success_or_failure() -> None:
    result = {
        "ok": True,
        "status": "pending_confirmation",
        "task_id": 172549793,
    }

    assert _remote_publish_pending(result) is True
    assert _remote_publish_succeeded(result) is False


def test_publishing_bus_resumes_saved_platform_poll_without_resubmitting() -> None:
    class RecoverableStore(_MemoryPublishJobStore):
        def list_pending_publish_jobs(self) -> list[dict[str, Any]]:
            return [deepcopy(state) for state in self.states.values()]

    class ResumeAdapter:
        def __init__(self) -> None:
            self.publish_calls = 0
            self.poll_calls = 0

        @staticmethod
        def resolve_category(
            product: dict[str, Any],
            config: dict[str, Any],
        ) -> dict[str, Any]:
            return product

        @staticmethod
        def required_attributes_missing(
            product: dict[str, Any],
            config: dict[str, Any],
        ) -> list[str]:
            return []

        def publish(
            self,
            product: dict[str, Any],
            platform: str,
            config: dict[str, Any],
        ) -> dict[str, Any]:
            self.publish_calls += 1
            raise AssertionError("恢复轮询时不应重新提交商品")

        def poll_publish_status(
            self,
            result: dict[str, Any],
            config: dict[str, Any],
        ) -> dict[str, Any]:
            self.poll_calls += 1
            assert result["result"]["task_id"] == 172549793
            return {
                "ok": True,
                "status": "real_publish_success",
                "result": {
                    "status": "imported",
                    "task_id": 172549793,
                    "external_id": "137285792",
                },
            }

        @staticmethod
        def publish_poll_interval_seconds(config: dict[str, Any]) -> float:
            return 0.01

    store = RecoverableStore()
    store.states["job-pending"] = {
        "job_id": "job-pending",
        "status": "running",
        "created_at": "2026-08-08 20:00:00",
        "updated_at": "2026-08-08 20:00:01",
        "product": {"product_id": "product-1"},
        "platforms": {
            "ozon": {
                "platform": "ozon",
                "status": "running",
                "stage": "waiting_platform_confirmation",
                "attempts": 1,
                "error": "",
                "result": {
                    "ok": True,
                    "status": "publish_pending_confirmation",
                    "result": {
                        "status": "pending_confirmation",
                        "task_id": 172549793,
                    },
                },
            }
        },
    }
    adapter = ResumeAdapter()
    bus = PublishingBus(
        store,
        adapters={"ozon": adapter},
        max_retries=0,
        auto_resume_pending=False,
    )
    try:
        assert bus.recover_pending_jobs() == ["job-pending"]
        bus.wait("job-pending", timeout=2)
        state = bus.get_status("job-pending")
    finally:
        bus.executor.shutdown(wait=True)

    assert adapter.publish_calls == 0
    assert adapter.poll_calls == 1
    assert state["status"] == "completed"
    assert state["platforms"]["ozon"]["status"] == "success"


def test_publishing_bus_does_not_duplicate_product_in_platform_result() -> None:
    class FailedAdapter:
        @staticmethod
        def resolve_category(
            product: dict[str, Any],
            config: dict[str, Any],
        ) -> dict[str, Any]:
            return product

        @staticmethod
        def required_attributes_missing(
            product: dict[str, Any],
            config: dict[str, Any],
        ) -> list[str]:
            return []

        @staticmethod
        def publish(
            product: dict[str, Any],
            platform: str,
            config: dict[str, Any],
        ) -> dict[str, Any]:
            return {
                "ok": False,
                "status": "failed",
                "error": "远端拒绝",
                "payload": {"items": []},
                "product": {"duplicated": "x" * 10_000},
            }

    store = _MemoryPublishJobStore()
    bus = PublishingBus(
        store,
        adapters={"ozon": FailedAdapter()},
        max_retries=0,
        auto_resume_pending=False,
    )
    try:
        queued = bus.enqueue(
            {"product_id": "product-1"},
            ["ozon"],
            targets={
                "ozon": {
                    "draft_id": "draft-1",
                    "site": "global",
                    "product_id": "product-1",
                }
            },
        )
        bus.wait(queued["job_id"], timeout=2)
        state = bus.get_status(queued["job_id"])
    finally:
        bus.executor.shutdown(wait=True)

    result = state["platforms"]["ozon"]["result"]
    assert "product" not in result
    assert result["payload"] == {"items": []}


def test_remote_publish_success_requires_explicit_evidence() -> None:
    assert not _remote_publish_succeeded(None)
    assert not _remote_publish_succeeded({})
    assert not _remote_publish_succeeded({"ok": True})
    assert _remote_publish_succeeded({"id": "MLM-1"})
    assert _remote_publish_succeeded(
        {"ok": True, "status": "published"}
    )


def test_terminal_hook_persists_product_and_log_without_status_poll(
    sample_product: dict[str, Any],
) -> None:
    class SuccessfulAdapter:
        @staticmethod
        def resolve_category(
            product: dict[str, Any],
            config: dict[str, Any],
        ) -> dict[str, Any]:
            return product

        @staticmethod
        def required_attributes_missing(
            product: dict[str, Any],
            config: dict[str, Any],
        ) -> list[str]:
            return []

        @staticmethod
        def publish(
            product: dict[str, Any],
            platform: str,
            config: dict[str, Any],
        ) -> dict[str, Any]:
            return {
                "ok": True,
                "status": "published",
                "id": "MLM-REMOTE-1",
            }

    context = get_context()
    saved = context.products.save_product(sample_product)
    bus = PublishingBus(
        context.db,
        adapters={"mercadolibre": SuccessfulAdapter()},
        terminal_callback=lambda state: (
            persist_publish_bus_terminal_results(
                state,
                context=context,
            )
        ),
        max_retries=0,
        auto_resume_pending=False,
    )
    try:
        persisted_draft = saved["drafts"]["mercadolibre"]
        queued = bus.enqueue(
            saved,
            ["mercadolibre"],
            targets={
                "mercadolibre": {
                    "draft_id": persisted_draft["draft_id"],
                    "site": str(persisted_draft.get("site") or "mlm"),
                    "product_id": saved["product_id"],
                }
            },
        )
        bus.wait(queued["job_id"], timeout=2)
    finally:
        bus.executor.shutdown(wait=True)

    persisted_product = context.db.load_product_model(
        saved["product_id"]
    )
    persisted_draft = persisted_product["drafts"]["mercadolibre"]
    assert persisted_draft["publish_status"] == "published"
    logs = [
        item
        for item in context.db.list_publish_logs()
        if item.get("job_id") == queued["job_id"]
    ]
    assert len(logs) == 1
    assert logs[0]["product_id"] == saved["product_id"]
    assert logs[0]["draft_id"] == persisted_draft["draft_id"]


def test_terminal_hook_updates_only_the_bound_draft(
    sample_product: dict[str, Any],
) -> None:
    context = get_context()
    saved = context.products.save_product(sample_product)
    product_id = str(saved["product_id"])
    first_draft = context.db.load_draft_model(
        str(saved["drafts"]["mercadolibre"]["draft_id"])
    )
    second_draft = {
        **deepcopy(first_draft),
        "draft_id": "d-bound-second",
        "title": "只允许这一条草稿接收发布结果",
        "publish_status": "ready",
        "status": "ready_to_publish",
        "last_publish_task": {},
    }
    second_draft_id = context.db.upsert_draft_model(
        product_id,
        "mercadolibre",
        second_draft,
    )
    state = {
        "job_id": "job-bound-second",
        "draft_id": second_draft_id,
        "status": "completed",
        "created_at": "2026-08-08 22:00:00",
        "updated_at": "2026-08-08 22:00:01",
        "product": deepcopy(saved),
        "platforms": {
            "mercadolibre": {
                "platform": "mercadolibre",
                "draft_id": second_draft_id,
                "site": str(second_draft.get("site") or "mlm"),
                "product_id": product_id,
                "status": "success",
                "stage": "finished",
                "attempts": 1,
                "error": "",
                "result": {"ok": True, "status": "published", "id": "MLM-2"},
            }
        },
    }

    persisted = persist_publish_bus_terminal_results(state, context=context)

    unchanged_first = context.db.load_draft_model(str(first_draft["draft_id"]))
    published_second = context.db.load_draft_model(second_draft_id)
    assert str(unchanged_first.get("publish_status") or "") != "published"
    assert published_second["publish_status"] == "published"
    assert published_second["last_publish_task"]["job_id"] == "job-bound-second"
    assert published_second["last_publish_task"]["item_id"] == "MLM-2"
    assert published_second["last_publish_task"]["external_id"] == "MLM-2"
    assert persisted["persisted_drafts"]["mercadolibre"]["draft_id"] == second_draft_id
    logs = [
        item
        for item in context.db.list_publish_logs()
        if item.get("job_id") == "job-bound-second"
    ]
    assert len(logs) == 1
    assert logs[0]["draft_id"] == second_draft_id


def test_database_rejects_reparenting_existing_draft(
    sample_product: dict[str, Any],
) -> None:
    context = get_context()
    first = context.products.save_product(sample_product)
    other_product = deepcopy(sample_product)
    other_product["product_id"] = "other-product"
    other_product["source"] = {
        **deepcopy(sample_product["source"]),
        "source_url": "https://example.com/other-product",
    }
    other = context.products.save_product(other_product)
    draft = context.db.load_draft_model(
        str(first["drafts"]["mercadolibre"]["draft_id"])
    )
    draft["product_id"] = str(other["product_id"])
    draft["source_product_id"] = str(other["product_id"])

    with pytest.raises(ValueError, match="禁止静默换绑"):
        context.db.upsert_draft_model(
            str(other["product_id"]),
            "mercadolibre",
            draft,
        )

    persisted = context.db.load_draft_model(str(draft["draft_id"]))
    assert persisted["product_id"] == first["product_id"]


def test_publish_context_blocks_legacy_mismatched_draft(
    sample_product: dict[str, Any],
) -> None:
    context = get_context()
    first = context.products.save_product(sample_product)
    other_product = deepcopy(sample_product)
    other_product["source"] = {
        **deepcopy(sample_product["source"]),
        "source_url": "https://example.com/mismatch-owner",
    }
    other = context.products.save_product(other_product)
    draft_id = str(first["drafts"]["mercadolibre"]["draft_id"])
    with context.db._connect() as connection:
        connection.execute(
            "UPDATE platform_drafts SET product_id = ? WHERE draft_id = ?",
            (str(other["product_id"]), draft_id),
        )
        connection.commit()

    result, error, status = load_required_draft_publish_context(
        {
            "draft_id": draft_id,
            "platform": "mercadolibre",
            "site": "mlm",
        }
    )

    assert result == {}
    assert status == 409
    assert error is not None
    assert error["error_code"] == "DRAFT_PRODUCT_MISMATCH"


def _completed_publish_state(
    product: dict[str, Any],
    job_id: str,
) -> dict[str, Any]:
    draft = product["drafts"]["mercadolibre"]
    return {
        "job_id": job_id,
        "draft_id": str(draft["draft_id"]),
        "status": "completed",
        "created_at": "2026-07-29 10:00:00",
        "updated_at": "2026-07-29 10:00:01",
        "product": deepcopy(product),
        "platforms": {
            "mercadolibre": {
                "platform": "mercadolibre",
                "draft_id": str(draft["draft_id"]),
                "site": str(draft.get("site") or "mlm"),
                "product_id": str(product["product_id"]),
                "status": "success",
                "stage": "finished",
                "created_at": "2026-07-29 10:00:00",
                "updated_at": "2026-07-29 10:00:01",
                "attempts": 1,
                "error": "",
                "result": {
                    "ok": True,
                    "status": "published",
                    "id": "MLM-RECOVERED-1",
                },
            }
        },
    }


def test_completed_job_without_terminal_marker_is_compensated_after_restart(
    sample_product: dict[str, Any],
) -> None:
    context = get_context()
    saved = context.products.save_product(sample_product)
    state = _completed_publish_state(saved, "job-crash-window")
    context.db.save_publish_job(state)

    bus = PublishingBus(
        context.db,
        adapters={},
        terminal_callback=lambda terminal_state: (
            persist_publish_bus_terminal_results(
                terminal_state,
                context=context,
            )
        ),
        auto_resume_pending=False,
    )
    try:
        recovered = bus.recover_pending_jobs()
    finally:
        bus.executor.shutdown(wait=True)

    persisted_job = context.db.load_publish_job("job-crash-window")
    persisted_product = context.db.load_product_model(
        saved["product_id"]
    )
    logs = [
        item
        for item in context.db.list_publish_logs()
        if item.get("job_id") == "job-crash-window"
    ]
    artifact_paths = {
        path
        for path in (
            context.paths.output_dir / "publish_artifacts"
        ).glob("*")
        if path.is_file()
    }

    assert recovered == ["job-crash-window"]
    assert persisted_job["terminal_results_persisted"] is True
    assert (
        persisted_product["drafts"]["mercadolibre"][
            "publish_status"
        ]
        == "published"
    )
    assert len(logs) == 1
    assert len(artifact_paths) == 2


def test_terminal_callback_retry_reuses_artifacts_and_log(
    sample_product: dict[str, Any],
) -> None:
    context = get_context()
    saved = context.products.save_product(sample_product)
    state = _completed_publish_state(saved, "job-idempotent")

    persist_publish_bus_terminal_results(
        deepcopy(state),
        context=context,
    )
    artifact_dir = (
        context.paths.output_dir / "publish_artifacts"
    )
    first_paths = {
        path
        for path in artifact_dir.glob("*")
        if path.is_file()
    }

    persist_publish_bus_terminal_results(
        deepcopy(state),
        context=context,
    )
    second_paths = {
        path
        for path in artifact_dir.glob("*")
        if path.is_file()
    }
    logs = [
        item
        for item in context.db.list_publish_logs()
        if item.get("job_id") == "job-idempotent"
    ]

    assert len(first_paths) == 2
    assert second_paths == first_paths
    assert len(logs) == 1


def test_source_site_registry_dispatches_detection_checks_and_parser() -> None:
    assert detect_source_site("https://detail.1688.com/offer/1.html") == "1688"
    assert detect_source_site("https://www.amazon.com/dp/TEST") == "amazon"
    assert detect_source_site("https://example.com/item/1") == "generic"

    amazon = source_site("amazon")
    flags, reason = amazon.diagnose(
        "https://www.amazon.com/errors/validateCaptcha",
        "Robot Check",
        "Enter the characters you see below",
        "Amazon CAPTCHA",
    )
    assert flags["is_captcha_page"] is True
    assert reason == "ROBOT"

    parsed = parse_source_snapshot(
        "generic",
        "<html><head><title>通用商品标题</title></head><body></body></html>",
        "https://example.com/item/1",
    )
    assert parsed["name"] == "通用商品标题"


def test_mercadolibre_adapter_reads_required_attributes_from_local_category() -> None:
    adapter = MercadoLibrePublishingAdapter()
    product = {
        "drafts": {
            "mercadolibre": {
                "category_id": "MLM-1",
                "brand": "Generic",
                "model": "M1",
                "package_dimensions": {
                    "length_cm": "1",
                    "width_cm": "1",
                    "height_cm": "1",
                    "weight_kg": "1",
                },
                "attributes": {},
            },
        },
        "local_platform_categories": {
            "mercadolibre": {
                "category_id": "MLM-1",
                "attributes": {
                    "required": [
                        {"id": "BRAND", "name": "Marca", "required": True},
                    ],
                },
            },
        },
    }

    assert adapter.required_attributes_missing(product, {}) == ["attributes.BRAND"]


def test_run_ai_use_case_renders_payload_and_normalizes_result(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        ai_use_case.ai_prompt_templates,
        "load_ai_use_case_prompt_pair",
        lambda app_dir, app_config, use_case_id: {
            "system": "只返回 JSON",
            "user": "输入：{{input_json}}",
        },
    )

    def fake_chat_json(
        app_dir,
        app_config,
        use_case_id,
        messages,
        **options,
    ):
        captured.update(
            {
                "use_case_id": use_case_id,
                "messages": messages,
                "options": options,
            }
        )
        return {"value": " raw "}

    monkeypatch.setattr(ai_use_case.ai_gateway, "chat_json", fake_chat_json)

    result = ai_use_case.run_ai_use_case(
        "category_translate",
        {"title": "收纳盒"},
        lambda value: str(value["value"]).strip(),
        temperature=0.2,
    )

    assert result == "raw"
    assert captured["use_case_id"] == "category_translate"
    assert '"title": "收纳盒"' in captured["messages"][1]["content"]
    assert captured["options"]["temperature"] == 0.2
