from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from erp_web.schemas.publish_capabilities import (
    ProductPublishCapabilityRequest,
    ProductPublishDestination,
    ProductPublishRequest,
    ProductPublishValidateRequest,
    PublishRequestConfirmation,
)
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.runtime_units import publish_capabilities


@pytest.fixture(autouse=True)
def _no_live_category_provider(monkeypatch):
    """纯 Capability 单测不触网：跳过当次类目定义加载。"""

    from erp_web.runtime_units import category_providers

    monkeypatch.setattr(
        category_providers, "category_provider_for", lambda platform: None
    )


class _Adapter:
    def __init__(self, errors: list[dict] | None = None) -> None:
        self.errors = errors or []
        self.prepare_calls = 0

    def prepare_product(self, product: dict, config: dict) -> dict:
        self.prepare_calls += 1
        return deepcopy(product)

    def validate_draft(self, context, config: dict) -> dict:
        return {
            "platform": "mercadolibre",
            "ok": not self.errors,
            "errors": deepcopy(self.errors),
            "warnings": [],
            "checked_at": "2026-08-13T00:00:00Z",
        }

    def build_payload(self, context, config: dict) -> dict:
        draft = context.product["drafts"]["mercadolibre"]
        payload = {
            "title": draft["title"],
            "category_id": draft["category_id"],
            "price": draft["price"],
            "pictures": [{"id": "image-1"}],
        }
        if isinstance(draft.get("sites_to_sell"), list):
            payload["sites_to_sell"] = deepcopy(draft["sites_to_sell"])
        return payload

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
    # 提交发布是写路径：允许预检落盘；测试用轻量 stub 代替真实 DB 写入。
    monkeypatch.setattr(
        publish_capabilities,
        "save_draft_precheck_result",
        lambda publish_context, precheck, **_kwargs: deepcopy(
            publish_context.get("draft") or {}
        ),
    )
    return adapter, store_config


def test_publish_validate_returns_stable_digest_and_is_pure(
    publish_boundary,
    monkeypatch,
) -> None:
    adapter, _store_config = publish_boundary
    request = ProductPublishValidateRequest(
        draft_id="draft-1",
        platform="mercadolibre",
        site="MLM",
    )

    # 纯计算边界：评估绝不持久化预检结果（side_effect="none" 契约）。
    def _explode(*args, **kwargs):
        pytest.fail("发布校验评估不得持久化预检结果")

    monkeypatch.setattr(
        "erp_web.runtime_units.draft_publish_context.save_draft_precheck_result",
        _explode,
    )
    # 同时守卫 capability 模块内的引用，防止评估路径经由它落盘。
    monkeypatch.setattr(
        publish_capabilities,
        "save_draft_precheck_result",
        _explode,
    )

    first = publish_capabilities.validate_product_publish(request)
    second = publish_capabilities.validate_product_publish(request)

    assert first.passed is True
    assert first.validation_digest == second.validation_digest
    assert len(first.validation_digest) == 64
    assert first.summary.price == "199"
    assert first.summary.store_label == "示例店铺"
    assert "seller-1" not in first.summary.store_identity

    evaluation = publish_capabilities.evaluate_publish_validation(request)
    assert evaluation.precheck["ok"] is True
    assert evaluation.approved_payload is not None
    assert adapter.prepare_calls == 0


def test_explicit_payload_preparation_is_the_only_validation_path_that_prepares_assets(
    publish_boundary,
) -> None:
    adapter, _store_config = publish_boundary
    request = ProductPublishValidateRequest(
        draft_id="draft-1",
        platform="mercadolibre",
        site="MLM",
    )

    evaluation = publish_capabilities.prepare_and_evaluate_publish_validation(
        request
    )

    assert evaluation.result.passed is True
    assert adapter.prepare_calls == 1


def test_explicit_payload_preparation_does_not_prepare_assets_when_precheck_blocks(
    publish_boundary,
) -> None:
    adapter, _store_config = publish_boundary
    adapter.errors = [
        {
            "code": "TITLE_MISSING",
            "field": "title",
            "message": "缺少标题",
            "severity": "error",
        }
    ]

    evaluation = publish_capabilities.prepare_and_evaluate_publish_validation(
        ProductPublishValidateRequest(
            draft_id="draft-1",
            platform="mercadolibre",
            site="MLM",
        )
    )

    assert evaluation.result.passed is False
    assert evaluation.result.errors[0].code == "TITLE_MISSING"
    assert adapter.prepare_calls == 0


def test_explicit_payload_preparation_fails_closed_when_adapter_does_not_confirm_ok(
    publish_boundary,
    monkeypatch,
) -> None:
    adapter, _store_config = publish_boundary
    monkeypatch.setattr(
        adapter,
        "validate_draft",
        lambda _context, _config: {
            "platform": "mercadolibre",
            "ok": False,
            "errors": [],
            "warnings": [],
        },
    )

    evaluation = publish_capabilities.prepare_and_evaluate_publish_validation(
        ProductPublishValidateRequest(draft_id="draft-1")
    )

    assert evaluation.result.passed is False
    assert evaluation.result.errors[0].code == "PUBLISH_VALIDATION_FAILED"
    assert adapter.prepare_calls == 0


def test_publish_request_revalidates_digest_and_forwards_idempotency_key(
    publish_boundary,
) -> None:
    _adapter, _store_config = publish_boundary
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


def test_cbt_publish_approval_shows_and_binds_actual_destinations(
    publish_boundary,
    monkeypatch,
) -> None:
    _adapter, _store_config = publish_boundary
    loaded = _context()
    draft = loaded["product"]["drafts"]["mercadolibre"]
    draft.update(
        {
            "site": "CBT",
            "category_id": "CBT123",
            "listing_currency": "USD",
            "price": "18",
            "sites_to_sell": [
                {
                    "site_id": "MLB",
                    "logistic_type": "remote",
                    "price": 200.0,
                    "listing_type_id": "gold_special",
                },
                {
                    "site_id": "MLM",
                    "logistic_type": "remote",
                    "net_proceeds": "15.50",
                    "listing_type_id": "gold_special",
                    "status": "active",
                    "free_shipping": True,
                    "sale_terms": [
                        {
                            "id": "WARRANTY_TYPE",
                            "value_name": "No warranty",
                        }
                    ],
                },
            ],
            "selected_pricing": {
                "applied_price": {"amount": "18", "currency": "USD"}
            },
        }
    )
    loaded["draft"] = deepcopy(draft)
    loaded["site"] = "CBT"
    loaded["target"] = {
        "platform": "mercadolibre",
        "site": "CBT",
        "sites_to_sell": deepcopy(draft["sites_to_sell"]),
    }
    loaded["targets"] = [deepcopy(loaded["target"])]
    monkeypatch.setattr(
        publish_capabilities,
        "load_required_draft_publish_context",
        lambda body, **_kwargs: (deepcopy(loaded), None, 200),
    )
    request = ProductPublishCapabilityRequest(
        draft_id="draft-1",
        platform="mercadolibre",
        site="CBT",
    )
    scope = publish_capabilities.PublishCapabilityScope(
        context=publish_capabilities.get_context(),
        publishing_bus=_Bus(),
    )

    first = publish_capabilities._publish_request_approval_snapshot(
        request,
        scope,
    )

    assert "MLB/remote" in first.summary
    assert "MLM/remote" in first.summary
    assert first.canonical_payload["destinations"] == [
        {
            "site_id": "MLB",
            "logistic_type": "remote",
            "price": 200.0,
            "listing_type_id": "gold_special",
        },
        {
            "site_id": "MLM",
            "logistic_type": "remote",
            "net_proceeds": "15.50",
            "listing_type_id": "gold_special",
            "status": "active",
            "free_shipping": True,
            "sale_terms": [
                {
                    "id": "WARRANTY_TYPE",
                    "value_name": "No warranty",
                }
            ],
        },
    ]

    loaded["product"]["drafts"]["mercadolibre"]["sites_to_sell"] = [
        {"site_id": "MLM", "logistic_type": "remote"}
    ]
    loaded["draft"]["sites_to_sell"] = [
        {"site_id": "MLM", "logistic_type": "remote"}
    ]
    second = publish_capabilities._publish_request_approval_snapshot(
        request,
        scope,
    )

    assert second.canonical_payload["destinations"] == [
        {"site_id": "MLM", "logistic_type": "remote"}
    ]
    assert (
        second.canonical_payload["validation_digest"]
        != first.canonical_payload["validation_digest"]
    )


def test_publish_destination_accepts_current_mercadolibre_sales_conditions() -> None:
    destination = ProductPublishDestination(
        site_id="MLM",
        logistic_type="remote",
        price=200.0,
        listing_type_id="gold_special",
    )

    assert destination.model_dump(exclude_none=True) == {
        "site_id": "MLM",
        "logistic_type": "remote",
        "price": 200.0,
        "listing_type_id": "gold_special",
    }


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
    _adapter, store_config = publish_boundary
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
    adapter, _store_config = publish_boundary
    adapter.errors = [
        {
            "code": "TITLE_MISSING",
            "field": "title",
            "message": "缺少标题",
            "severity": "error",
        }
    ]

    request = ProductPublishValidateRequest(draft_id="draft-1")
    result = publish_capabilities.validate_product_publish(request)

    assert result.passed is False
    assert result.validation_digest == ""
    assert result.errors[0].code == "TITLE_MISSING"

    evaluation = publish_capabilities.evaluate_publish_validation(request)
    assert evaluation.precheck["ok"] is False
    assert evaluation.approved_payload is None
