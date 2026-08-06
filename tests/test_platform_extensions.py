from __future__ import annotations

from copy import deepcopy
from typing import Any

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
from erp_web.runtime_units.publishing_bus_core import PublishingBus
from erp_web.runtime_units.publish_bus import (
    persist_publish_bus_terminal_results,
)
from erp_web.runtime_units.runtime_api import (
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
        queued = bus.enqueue({"name": "缺属性商品"}, ["mercadolibre"])
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
        queued = bus.enqueue({"product_id": "product-1"}, ["ozon"])
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
        queued = bus.enqueue(saved, ["mercadolibre"])
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


def _completed_publish_state(
    product: dict[str, Any],
    job_id: str,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": "completed",
        "created_at": "2026-07-29 10:00:00",
        "updated_at": "2026-07-29 10:00:01",
        "product": deepcopy(product),
        "platforms": {
            "mercadolibre": {
                "platform": "mercadolibre",
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
