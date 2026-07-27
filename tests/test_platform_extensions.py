from __future__ import annotations

from copy import deepcopy
from typing import Any

from erp_web.marketplace_registry import (
    CAP_CATEGORY_SEARCH,
    CAP_PREVIEW_PAYLOAD,
    CAP_PUBLISH,
    platform_has_capability,
)
from erp_web.runtime_units import ai_use_case
from erp_web.runtime_units.publish_adapter import (
    MercadoLibrePublishingAdapter,
    publishing_adapter_for,
    unsupported_publish_response,
)
from erp_web.runtime_units.publishing_bus_core import PublishingBus
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
    assert not platform_has_capability("ozon", CAP_PUBLISH)
    assert not platform_has_capability("yandex", CAP_PUBLISH)

    assert isinstance(publishing_adapter_for("mercadolibre"), MercadoLibrePublishingAdapter)
    assert publishing_adapter_for("ozon") is None
    assert unsupported_publish_response("ozon") == {
        "ok": False,
        "supported": False,
        "platform": "ozon",
        "status": "unsupported",
        "error": "Ozon发布未接入",
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
