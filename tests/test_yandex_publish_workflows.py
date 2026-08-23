# -*- coding: utf-8 -*-
"""Yandex 发布工作流（preview/enqueue）的确认契约测试。"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from erp_web.runtime_units import publish_capabilities, publish_workflows


class _Adapter:
    platform = "yandex"

    def prepare_product(self, product: dict, config: dict) -> dict:
        return deepcopy(product)

    def validate_draft(self, context, config: dict) -> dict:
        return {
            "platform": "yandex",
            "ok": True,
            "errors": [],
            "warnings": [],
            "checked_at": "2026-08-16T00:00:00Z",
        }

    def build_payload(self, context, config: dict) -> dict:
        draft = context.product["drafts"]["yandex"]
        return {
            "platform": "yandex",
            "offer_id": draft["sku"],
            "campaign_id": "111",
            "business_id": "222",
            "price": {"level": "campaign", "offers": [{"offerId": draft["sku"], "price": {"value": "1299", "currencyId": "RUB"}}]},
        }

    def validate_payload(self, payload: dict, config: dict) -> list[str]:
        return []


class _Products:
    def publish_queue_platforms(self, product: dict, requested_platforms=None) -> list[str]:
        return list(requested_platforms or [])


class _Bus:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def recover_publish_job(self, **facts) -> dict | None:
        return None

    def enqueue(self, product: dict, platforms: list[str], *, targets: dict, idempotency_key: str, approved_publications=None) -> dict:
        self.calls.append(
            {
                "platforms": platforms,
                "targets": targets,
                "idempotency_key": idempotency_key,
                "approved_publications": approved_publications,
            }
        )
        return {"job_id": "job-yandex-1", "status": "queued"}


def _yandex_context() -> dict:
    draft = {
        "draft_id": "draft-y1",
        "product_id": "product-y1",
        "source_product_id": "product-y1",
        "platform": "yandex",
        "site": "global",
        "title": "便携风扇",
        "sku": "SKU-001",
        "category_id": "91596",
        "images": [{"asset_id": "image-1", "role": "main", "order": 0}],
        "selected_pricing": {"applied_price": {"amount": "1299", "currency": "RUB"}},
        "listing_currency": "RUB",
        "price": "1299",
    }
    return {
        "draft": deepcopy(draft),
        "product": {
            "product_id": "product-y1",
            "name": "便携风扇",
            "drafts": {"yandex": deepcopy(draft)},
        },
        "platform": "yandex",
        "site": "global",
        "target": {"platform": "yandex", "site": "global"},
        "targets": [{"platform": "yandex", "site": "global"}],
    }


@pytest.fixture()
def workflow_boundary(monkeypatch):
    adapter = _Adapter()
    bus = _Bus()
    store_config = {
        "yandex": {
            "shop_name": "示例店铺",
            "business_id": "222",
            "campaign_id": "111",
        }
    }
    context = SimpleNamespace(
        config=SimpleNamespace(load_store_config=lambda: deepcopy(store_config)),
        products=_Products(),
        publishing_bus=bus,
    )

    # 纯工作流单测不触网：跳过当次类目定义加载。
    from erp_web.runtime_units import category_providers

    monkeypatch.setattr(
        category_providers, "category_provider_for", lambda platform: None
    )

    def fake_context_loader(body, **_kwargs):
        return deepcopy(_yandex_context()), None, 200

    monkeypatch.setattr(publish_workflows, "load_required_draft_publish_context", fake_context_loader)
    monkeypatch.setattr(publish_capabilities, "load_required_draft_publish_context", fake_context_loader)
    monkeypatch.setattr(publish_capabilities, "publishing_adapter_for", lambda platform: adapter)
    monkeypatch.setattr(publish_workflows, "publishing_adapter_for", lambda platform: adapter)
    # 评估是纯计算；受信预览入口在 publish_workflows 里负责预检落盘。
    monkeypatch.setattr(
        publish_workflows,
        "save_draft_precheck_result",
        lambda publish_context, precheck, **_kwargs: {
            "draft": deepcopy(publish_context.get("draft") or {}),
            "productContext": {},
            "productsIndex": {},
            "draftsIndex": {},
        },
    )
    monkeypatch.setattr(publish_capabilities, "get_context", lambda: context)
    # 提交发布（enqueue → request_product_publish）是写路径：允许预检落盘；
    # 测试用轻量 stub 代替真实 DB 写入。
    monkeypatch.setattr(
        publish_capabilities,
        "save_draft_precheck_result",
        lambda publish_context, precheck, **_kwargs: deepcopy(
            publish_context.get("draft") or {}
        ),
    )
    monkeypatch.setattr(
        publish_workflows,
        "publish_logs_runtime",
        SimpleNamespace(
            _sanitize_for_log=lambda value: value,
            append_platform_publish_log=lambda *args, **kwargs: ("preview.json", None),
        ),
    )
    return adapter, store_config, bus


def test_preview_returns_digest_summary_and_sanitized_payload(workflow_boundary) -> None:
    response, status = publish_workflows.preview_publish_payload({"draft_id": "draft-y1"})

    assert status == 200
    assert response["ok"] is True
    assert response["status"] == "preview_only"
    assert response["validation_digest"]
    assert len(response["validation_digest"]) == 64
    assert response["summary"]["price"] == "1299"
    assert response["summary"]["store_label"] == "示例店铺"
    # 店铺身份不能泄露明文 business_id/campaign_id
    assert "222" not in response["summary"]["store_identity"]
    assert response["payload"]["offer_id"] == "SKU-001"

    # digest 稳定：同一事实重复预览结果一致
    second, _ = publish_workflows.preview_publish_payload({"draft_id": "draft-y1"})
    assert second["validation_digest"] == response["validation_digest"]


def test_enqueue_requires_explicit_confirmation_and_digest(workflow_boundary) -> None:
    response, status = publish_workflows.enqueue_publish_job({"draft_id": "draft-y1"})
    assert status == 400
    assert response["error_code"] == "PUBLISH_CONFIRMATION_REQUIRED"

    response, status = publish_workflows.enqueue_publish_job(
        {"draft_id": "draft-y1", "confirm": True}
    )
    assert status == 400
    assert response["error_code"] == "PUBLISH_CONFIRMATION_REQUIRED"


def test_enqueue_with_valid_digest_creates_job_with_server_generated_key(
    workflow_boundary,
) -> None:
    _adapter, _store, bus = workflow_boundary
    preview, _ = publish_workflows.preview_publish_payload({"draft_id": "draft-y1"})

    response, status = publish_workflows.enqueue_publish_job(
        {
            "draft_id": "draft-y1",
            "confirm": True,
            "validation_digest": preview["validation_digest"].upper(),
            # 客户端伪造的幂等键/确认时间必须被忽略
            "idempotency_key": "client-forged-key",
            "confirmed_at": "1970-01-01T00:00:00Z",
        }
    )

    assert status == 200, response
    assert response["ok"] is True
    assert response["job_id"] == "job-yandex-1"
    enqueue_call = bus.calls[-1]
    assert enqueue_call["idempotency_key"].startswith("manual:")
    assert enqueue_call["idempotency_key"] != "client-forged-key"
    approval = enqueue_call["approved_publications"]["yandex"]
    assert approval["validation_digest"] == preview["validation_digest"].lower()
    assert approval["payload"]["offer_id"] == "SKU-001"


def test_enqueue_with_stale_digest_is_conflict(workflow_boundary) -> None:
    response, status = publish_workflows.enqueue_publish_job(
        {
            "draft_id": "draft-y1",
            "confirm": True,
            "validation_digest": "0" * 64,
        }
    )
    assert status == 409
    assert response["error_code"] == "PUBLISH_CONFIRMATION_STALE"


def test_preview_precheck_failure_returns_structured_errors(
    workflow_boundary,
) -> None:
    adapter, _store, _bus = workflow_boundary
    adapter.validate_draft = lambda context, config: {
        "platform": "yandex",
        "ok": False,
        "errors": [
            {
                "code": "CATEGORY_INVALID",
                "field": "category_id",
                "message": "Yandex 类目 ID 必须是正整数",
                "severity": "error",
            }
        ],
        "warnings": [],
        "checked_at": "2026-08-16T00:00:00Z",
    }

    response, status = publish_workflows.preview_publish_payload({"draft_id": "draft-y1"})

    assert status == 400
    assert response["ok"] is False
    assert response["status"] == "precheck_failed"
    assert response["precheck"]["errors"][0]["code"] == "CATEGORY_INVALID"
    assert "payload" not in response


def test_unsupported_platform_preview_fails_closed() -> None:
    response, status = publish_workflows.preview_publish_payload(
        {"draft_id": "draft-x", "platform": "wildberries"}
    )
    assert status == 501
    assert response["supported"] is False
