from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from erp_web.schemas.publish_capabilities import (
    ProductPublishRequest,
    ProductPublishValidateRequest,
    PublishRequestConfirmation,
)
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.runtime_units import publish_capabilities


class _Adapter:
    def __init__(self, errors: list[dict] | None = None) -> None:
        self.errors = errors or []

    def prepare_product(self, product: dict, config: dict) -> dict:
        return deepcopy(product)

    def validate_draft(self, product: dict, config: dict) -> dict:
        return {
            "platform": "mercadolibre",
            "ok": not self.errors,
            "errors": deepcopy(self.errors),
            "warnings": [],
            "checked_at": "2026-08-13T00:00:00Z",
        }

    def build_payload(self, product: dict, config: dict) -> dict:
        draft = product["drafts"]["mercadolibre"]
        return {
            "title": draft["title"],
            "category_id": draft["category_id"],
            "price": draft["price"],
            "pictures": [{"id": "image-1"}],
        }

    def validate_payload(self, payload: dict, config: dict) -> list[str]:
        return []


class _Products:
    def publish_queue_platforms(
        self,
        product: dict,
        requested_platforms: list[str] | None = None,
    ) -> list[str]:
        return list(requested_platforms or [])


class _Bus:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.recovered: dict | None = None

    def recover_publish_job(self, **facts) -> dict | None:
        self.calls.append({"recovery": facts})
        return deepcopy(self.recovered)

    def enqueue(
        self,
        product: dict,
        platforms: list[str],
        *,
        targets: dict[str, dict],
        idempotency_key: str,
        approved_publications: dict[str, dict] | None = None,
    ) -> dict:
        self.calls.append(
            {
                "product": product,
                "platforms": platforms,
                "targets": targets,
                "idempotency_key": idempotency_key,
                "approved_publications": approved_publications,
            }
        )
        return {"job_id": "job-1", "status": "queued"}


def _context() -> dict:
    draft = {
        "draft_id": "draft-1",
        "product_id": "product-1",
        "source_product_id": "product-1",
        "platform": "mercadolibre",
        "site": "MLM",
        "title": "Portable fan",
        "category_id": "MLM123",
        "price": "199",
        "listing_currency": "MXN",
        "stock": "5",
        "images": [{"asset_id": "image-1", "role": "main", "order": 0}],
        "selected_pricing": {
            "applied_price": {"amount": "199", "currency": "MXN"}
        },
    }
    return {
        "draft": deepcopy(draft),
        "product": {
            "product_id": "product-1",
            "name": "Portable fan",
            "drafts": {"mercadolibre": deepcopy(draft)},
        },
        "platform": "mercadolibre",
        "site": "MLM",
        "target": {"platform": "mercadolibre", "site": "MLM"},
        "targets": [{"platform": "mercadolibre", "site": "MLM"}],
    }


@pytest.fixture()
def publish_boundary(monkeypatch):
    adapter = _Adapter()
    saved: list[dict] = []
    monkeypatch.setattr(
        publish_capabilities,
        "load_required_draft_publish_context",
        lambda body, **_kwargs: (deepcopy(_context()), None, 200),
    )
    monkeypatch.setattr(
        publish_capabilities,
        "publishing_adapter_for",
        lambda platform: adapter,
    )
    monkeypatch.setattr(
        publish_capabilities,
        "save_draft_precheck_result",
        lambda publish_context, precheck, **_kwargs: (
            saved.append(deepcopy(precheck)) or {}
        ),
    )
    store_config = {
        "mercadolibre": {
            "user_id": "seller-1",
            "shop_name": "示例店铺",
        }
    }
    context = SimpleNamespace(
        config=SimpleNamespace(
            load_store_config=lambda: deepcopy(store_config)
        ),
        products=_Products(),
    )
    monkeypatch.setattr(publish_capabilities, "get_context", lambda: context)
    return adapter, saved, store_config


def test_publish_validate_returns_stable_digest_and_persists_combined_precheck(
    publish_boundary,
) -> None:
    _adapter, saved, _store_config = publish_boundary
    request = ProductPublishValidateRequest(
        draft_id="draft-1",
        platform="mercadolibre",
        site="MLM",
    )

    first = publish_capabilities.validate_product_publish(request)
    second = publish_capabilities.validate_product_publish(request)

    assert first.passed is True
    assert first.validation_digest == second.validation_digest
    assert len(first.validation_digest) == 64
    assert first.summary.price == "199"
    assert first.summary.store_label == "示例店铺"
    assert "seller-1" not in first.summary.store_identity
    assert saved[-1]["ok"] is True


def test_publish_request_revalidates_digest_and_forwards_idempotency_key(
    publish_boundary,
) -> None:
    _adapter, _saved, _store_config = publish_boundary
    validation = publish_capabilities.validate_product_publish(
        ProductPublishValidateRequest(
            draft_id="draft-1",
            platform="mercadolibre",
            site="MLM",
        )
    )
    bus = _Bus()

    result = publish_capabilities.request_product_publish(
        ProductPublishRequest(
            draft_id="draft-1",
            platform="mercadolibre",
            site="MLM",
            idempotency_key="task-1:publish-step",
            confirmation=PublishRequestConfirmation(
                task_id="task-1",
                step_id="publish-step",
                validation_digest=validation.validation_digest,
                confirmed_at=datetime.now(timezone.utc),
            ),
        ),
        publishing_bus=bus,
    )

    assert result.job_id == "job-1"
    enqueue_call = next(item for item in bus.calls if "idempotency_key" in item)
    assert enqueue_call["idempotency_key"] == "task-1:publish-step"
    assert enqueue_call["targets"] == {
        "mercadolibre": {
            "draft_id": "draft-1",
            "site": "MLM",
            "product_id": "product-1",
        }
    }
    approval = enqueue_call["approved_publications"]["mercadolibre"]
    assert approval["validation_digest"] == validation.validation_digest
    assert approval["payload"]["price"] == "199"
    assert "seller-1" not in approval["store_identity"]


def test_publish_request_recovers_lost_job_id_before_revalidation(
    publish_boundary,
    monkeypatch,
) -> None:
    validation = publish_capabilities.validate_product_publish(
        ProductPublishValidateRequest(
            draft_id="draft-1",
            platform="mercadolibre",
            site="MLM",
        )
    )
    bus = _Bus()
    bus.recovered = {
        "job_id": "job-already-published",
        "platforms": ["mercadolibre"],
        "status": "completed",
        "idempotent_replay": True,
    }
    monkeypatch.setattr(
        publish_capabilities,
        "evaluate_publish_validation",
        lambda *_args, **_kwargs: pytest.fail("恢复已有 job 时不应重新校验"),
    )

    result = publish_capabilities.request_product_publish(
        ProductPublishRequest(
            draft_id="draft-1",
            platform="mercadolibre",
            site="MLM",
            idempotency_key="task-crashed:publish-step",
            confirmation=PublishRequestConfirmation(
                task_id="task-crashed",
                step_id="publish-step",
                validation_digest=validation.validation_digest,
                confirmed_at=datetime.now(timezone.utc),
            ),
        ),
        publishing_bus=bus,
    )

    assert result.job_id == "job-already-published"
    assert result.status == "completed"
    assert result.idempotent_replay is True
    assert bus.calls == [
        {
            "recovery": {
                "idempotency_key": "task-crashed:publish-step",
                "product_id": "product-1",
                "draft_id": "draft-1",
                "validation_digest": validation.validation_digest,
                "platform": "mercadolibre",
                "site": "MLM",
            }
        }
    ]


def test_publish_request_rejects_stale_confirmation_before_enqueue(
    publish_boundary,
) -> None:
    bus = _Bus()

    with pytest.raises(BusinessCapabilityError) as exc_info:
        publish_capabilities.request_product_publish(
            ProductPublishRequest(
                draft_id="draft-1",
                platform="mercadolibre",
                site="MLM",
                idempotency_key="task-1:publish-step",
                confirmation=PublishRequestConfirmation(
                    task_id="task-1",
                    step_id="publish-step",
                    validation_digest="0" * 64,
                    confirmed_at=datetime.now(timezone.utc),
                ),
            ),
            publishing_bus=bus,
        )

    assert exc_info.value.code == "PUBLISH_CONFIRMATION_STALE"
    assert not any("idempotency_key" in item for item in bus.calls)


def test_publish_request_rejects_confirmation_after_store_account_changes(
    publish_boundary,
) -> None:
    _adapter, _saved, store_config = publish_boundary
    validation = publish_capabilities.validate_product_publish(
        ProductPublishValidateRequest(
            draft_id="draft-1",
            platform="mercadolibre",
            site="MLM",
        )
    )
    store_config["mercadolibre"]["user_id"] = "seller-2"
    bus = _Bus()

    with pytest.raises(BusinessCapabilityError) as exc_info:
        publish_capabilities.request_product_publish(
            ProductPublishRequest(
                draft_id="draft-1",
                platform="mercadolibre",
                site="MLM",
                idempotency_key="task-1:publish-step",
                confirmation=PublishRequestConfirmation(
                    task_id="task-1",
                    step_id="publish-step",
                    validation_digest=validation.validation_digest,
                    confirmed_at=datetime.now(timezone.utc),
                ),
            ),
            publishing_bus=bus,
        )

    assert exc_info.value.code == "PUBLISH_CONFIRMATION_STALE"
    assert not any("idempotency_key" in item for item in bus.calls)


def test_publish_validate_returns_structured_hard_errors(
    publish_boundary,
) -> None:
    adapter, saved, _store_config = publish_boundary
    adapter.errors = [
        {
            "code": "TITLE_MISSING",
            "field": "title",
            "message": "缺少标题",
            "severity": "error",
        }
    ]

    result = publish_capabilities.validate_product_publish(
        ProductPublishValidateRequest(draft_id="draft-1")
    )

    assert result.passed is False
    assert result.validation_digest == ""
    assert result.errors[0].code == "TITLE_MISSING"
    assert saved[-1]["ok"] is False
