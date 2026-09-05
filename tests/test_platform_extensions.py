from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any

import pytest

from erp_web.runtime_units import publish_adapter
from erp_web.context import get_context
from erp_web.db import ErpDatabase
from erp_web.marketplace_registry import (
    CAP_CATEGORY_ATTRIBUTES,
    CAP_CATEGORY_SEARCH,
    CAP_ORDERS,
    CAP_PREVIEW_PAYLOAD,
    CAP_PUBLISH,
    platform_has_capability,
)
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.runtime_units import ai_use_case
from erp_web.runtime_units.publish_adapter import (
    MercadoLibrePublishingAdapter,
    OzonPublishingAdapter,
    YandexPublishingAdapter,
    publishing_adapter_for,
    unsupported_publish_response,
)
from erp_web.runtime_units.publish_confirmation import (
    canonical_publish_digest,
    resolve_publish_store_binding,
)
from erp_web.runtime_units.draft_publish_context import (
    load_required_draft_publish_context,
)
from erp_web.runtime_units.publishing_bus_core import (
    PublishApprovalBindingError,
    PublishIdempotencyConflictError,
    PublishingBus,
)
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
        self._lock = threading.Lock()

    def create_publish_job(
        self,
        state: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        idempotency_key = str(state.get("idempotency_key") or "")
        with self._lock:
            existing = next(
                (
                    item
                    for item in self.states.values()
                    if item.get("idempotency_key") == idempotency_key
                ),
                None,
            )
            if existing is not None:
                return deepcopy(existing), False
            self.states[str(state["job_id"])] = deepcopy(state)
        return deepcopy(state), True

    def save_publish_job(self, state: dict[str, Any]) -> None:
        job_id = str(state["job_id"])
        with self._lock:
            existing = self.states.get(job_id)
            if existing is None:
                raise FileNotFoundError(job_id)
            if existing.get("idempotency_key") != state.get("idempotency_key"):
                raise ValueError("发布任务的 idempotency_key 不可变更。")
            self.states[job_id] = deepcopy(state)

    def load_publish_job(self, job_id: str) -> dict[str, Any]:
        return deepcopy(self.states.get(job_id, {}))

    def load_publish_job_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return deepcopy(
            next(
                (
                    state
                    for state in self.states.values()
                    if state.get("idempotency_key") == idempotency_key
                ),
                {},
            )
        )

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


class _SuccessfulPublishingAdapter:
    def __init__(self) -> None:
        self.publish_calls = 0

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
        return {"ok": True, "status": "published", "id": "remote-1"}


class _ApprovedPayloadAdapter:
    def __init__(self) -> None:
        self.legacy_publish_calls = 0
        self.published_payloads: list[dict[str, Any]] = []

    def publish(
        self,
        product: dict[str, Any],
        platform: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        self.legacy_publish_calls += 1
        raise AssertionError("确认发布不应重新构建 product 发布路径")

    @staticmethod
    def validate_payload(
        payload: Any,
        config: dict[str, Any],
    ) -> list[str]:
        return [] if isinstance(payload, dict) and payload.get("title") else ["缺少标题"]

    def publish_payload(
        self,
        payload: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        self.published_payloads.append(deepcopy(payload))
        return {"ok": True, "status": "published", "id": "approved-remote-1"}


def _approved_publication(
    *,
    payload: dict[str, Any],
    config: dict[str, Any],
    product_id: str = "product-1",
    draft_id: str = "draft-1",
    platform: str = "mercadolibre",
    site: str = "mlm",
) -> dict[str, dict[str, Any]]:
    binding = resolve_publish_store_binding(platform, config)
    digest = canonical_publish_digest(
        product_id=product_id,
        draft_id=draft_id,
        platform=platform,
        site=site,
        store_identity=binding.identity,
        payload=payload,
    )
    return {
        platform: {
            "payload": deepcopy(payload),
            "validation_digest": digest,
            "store_identity": binding.identity,
        }
    }


def test_marketplace_capabilities_only_enable_real_integrations() -> None:
    assert platform_has_capability("mercadolibre", CAP_PUBLISH)
    assert platform_has_capability("mercadolibre", CAP_PREVIEW_PAYLOAD)
    assert platform_has_capability("ozon", CAP_CATEGORY_SEARCH)
    assert platform_has_capability("ozon", CAP_PUBLISH)
    assert platform_has_capability("ozon", CAP_PREVIEW_PAYLOAD)
    # Yandex 已完成真实接入：发布/预览/类目检索/类目属性全部开放，
    # 未接入的能力（如订单）保持关闭。
    assert platform_has_capability("yandex", CAP_PUBLISH)
    assert platform_has_capability("yandex", CAP_PREVIEW_PAYLOAD)
    assert platform_has_capability("yandex", CAP_CATEGORY_SEARCH)
    assert platform_has_capability("yandex", CAP_CATEGORY_ATTRIBUTES)
    assert not platform_has_capability("yandex", CAP_ORDERS)
    # 注册表之外的平台保持 fail-closed。
    assert not platform_has_capability("wildberries", CAP_PUBLISH)

    assert isinstance(publishing_adapter_for("mercadolibre").item_adapter, MercadoLibrePublishingAdapter)
    assert isinstance(publishing_adapter_for("ozon").item_adapter, OzonPublishingAdapter)
    assert isinstance(publishing_adapter_for("yandex").item_adapter, YandexPublishingAdapter)
    assert unsupported_publish_response("wildberries") == {
        "ok": False,
        "supported": False,
        "platform": "wildberries",
        "status": "unsupported",
        "error": "wildberries发布未接入",
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
            idempotency_key="test:required-attributes",
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


def test_publishing_bus_reuses_persisted_job_for_same_idempotency_facts() -> None:
    store = _MemoryPublishJobStore()
    adapter = _SuccessfulPublishingAdapter()
    bus = PublishingBus(
        store,
        adapters={"mercadolibre": adapter, "ozon": adapter},
        max_retries=0,
        auto_resume_pending=False,
    )
    target = {
        "mercadolibre": {
            "draft_id": "draft-1",
            "site": "mlm",
            "product_id": "product-1",
        }
    }
    try:
        first = bus.enqueue(
            {"product_id": "product-1"},
            ["mercadolibre"],
            targets=target,
            idempotency_key="global-task-1:publish-step-1",
        )
        replay = bus.enqueue(
            {"product_id": "product-1"},
            ["mercadolibre", "mercadolibre"],
            targets=target,
            idempotency_key="global-task-1:publish-step-1",
        )
        bus.wait(first["job_id"], timeout=2)
    finally:
        bus.executor.shutdown(wait=True)

    assert replay["job_id"] == first["job_id"]
    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert len(store.states) == 1
    assert adapter.publish_calls == 1


def test_publishing_bus_reuses_idempotency_mapping_after_restart(tmp_path) -> None:
    database = ErpDatabase(tmp_path / "erp.sqlite3")
    adapter = _SuccessfulPublishingAdapter()
    target = {
        "mercadolibre": {
            "draft_id": "draft-1",
            "site": "mlm",
            "product_id": "product-1",
        }
    }
    first_bus = PublishingBus(
        database,
        adapters={"mercadolibre": adapter},
        max_retries=0,
        auto_resume_pending=False,
    )
    try:
        first = first_bus.enqueue(
            {"product_id": "product-1"},
            ["mercadolibre"],
            targets=target,
            idempotency_key="global-task-restart:publish-step-1",
        )
        first_bus.wait(first["job_id"], timeout=2)
    finally:
        first_bus.executor.shutdown(wait=True)

    restarted_bus = PublishingBus(
        database,
        adapters={"mercadolibre": adapter},
        max_retries=0,
        auto_resume_pending=False,
    )
    try:
        replay = restarted_bus.enqueue(
            {"product_id": "product-1"},
            ["mercadolibre"],
            targets=target,
            idempotency_key="global-task-restart:publish-step-1",
        )
        public_state = restarted_bus.get_public_status(replay["job_id"])
    finally:
        restarted_bus.executor.shutdown(wait=True)

    assert replay["job_id"] == first["job_id"]
    assert replay["idempotent_replay"] is True
    assert adapter.publish_calls == 1
    assert "idempotency_key" not in public_state
    assert "idempotency_facts" not in public_state


def test_publishing_bus_rejects_idempotency_key_bound_to_different_facts() -> None:
    store = _MemoryPublishJobStore()
    adapter = _SuccessfulPublishingAdapter()
    bus = PublishingBus(
        store,
        adapters={"mercadolibre": adapter, "ozon": adapter},
        max_retries=0,
        auto_resume_pending=False,
    )
    key = "global-task-2:publish-step-1"
    try:
        first = bus.enqueue(
            {"product_id": "product-1"},
            ["mercadolibre"],
            targets={
                "mercadolibre": {
                    "draft_id": "draft-1",
                    "site": "mlm",
                    "product_id": "product-1",
                }
            },
            idempotency_key=key,
        )
        bus.wait(first["job_id"], timeout=2)

        conflicting_requests = [
            (
                {"product_id": "product-2"},
                ["mercadolibre"],
                {
                    "mercadolibre": {
                        "draft_id": "draft-1",
                        "site": "mlm",
                        "product_id": "product-2",
                    }
                },
            ),
            (
                {"product_id": "product-1"},
                ["ozon"],
                {
                    "ozon": {
                        "draft_id": "draft-1",
                        "site": "global",
                        "product_id": "product-1",
                    }
                },
            ),
            (
                {"product_id": "product-1"},
                ["mercadolibre"],
                {
                    "mercadolibre": {
                        "draft_id": "draft-2",
                        "site": "mlm",
                        "product_id": "product-1",
                    }
                },
            ),
        ]
        for product, platforms, targets in conflicting_requests:
            with pytest.raises(PublishIdempotencyConflictError) as raised:
                bus.enqueue(
                    product,
                    platforms,
                    targets=targets,
                    idempotency_key=key,
                )
            assert raised.value.code == "PUBLISH_IDEMPOTENCY_CONFLICT"
    finally:
        bus.executor.shutdown(wait=True)

    assert len(store.states) == 1
    assert adapter.publish_calls == 1


def test_approved_publish_uses_exact_persisted_payload_and_hides_binding() -> None:
    store = _MemoryPublishJobStore()
    adapter = _ApprovedPayloadAdapter()
    config = {
        "mercadolibre": {
            "user_id": "seller-1",
            "access_token": "secret-token",
        }
    }
    approved_payload = {"title": "人工确认标题", "price": 199}
    approvals = _approved_publication(payload=approved_payload, config=config)
    bus = PublishingBus(
        store,
        adapters={"mercadolibre": adapter},
        config_provider=lambda: deepcopy(config),
        max_retries=0,
        auto_resume_pending=False,
    )
    try:
        queued = bus.enqueue(
            {
                "product_id": "product-1",
                "drafts": {
                    "mercadolibre": {"title": "入队后可变化的商品标题"}
                },
            },
            ["mercadolibre"],
            targets={
                "mercadolibre": {
                    "draft_id": "draft-1",
                    "site": "MLM",
                    "product_id": "product-1",
                }
            },
            idempotency_key="approved:exact-payload",
            approved_publications=approvals,
        )
        bus.wait(queued["job_id"], timeout=2)
        internal = bus.get_status(queued["job_id"])
        public = bus.get_public_status(queued["job_id"])
    finally:
        bus.executor.shutdown(wait=True)

    assert adapter.legacy_publish_calls == 0
    assert adapter.published_payloads == [approved_payload]
    assert internal["approved_publications"]["mercadolibre"]["payload"] == approved_payload
    assert "approved_publications" not in public
    assert "idempotency_key" not in public
    assert "idempotency_facts" not in public


def test_approved_publish_blocks_changed_store_identity_before_network() -> None:
    store = _MemoryPublishJobStore()
    adapter = _ApprovedPayloadAdapter()
    confirmed_config = {"mercadolibre": {"user_id": "seller-1"}}
    current_config = {"mercadolibre": {"user_id": "seller-2"}}
    approvals = _approved_publication(
        payload={"title": "人工确认标题"},
        config=confirmed_config,
    )
    bus = PublishingBus(
        store,
        adapters={"mercadolibre": adapter},
        config_provider=lambda: deepcopy(current_config),
        max_retries=1,
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
            idempotency_key="approved:changed-store",
            approved_publications=approvals,
        )
        bus.wait(queued["job_id"], timeout=2)
        state = bus.get_status(queued["job_id"])
    finally:
        bus.executor.shutdown(wait=True)

    platform_state = state["platforms"]["mercadolibre"]
    assert platform_state["status"] == "failed"
    assert platform_state["attempts"] == 1
    assert "账号不一致" in platform_state["error"]
    assert adapter.legacy_publish_calls == 0
    assert adapter.published_payloads == []


def test_approved_publish_rejects_payload_changed_after_digest() -> None:
    store = _MemoryPublishJobStore()
    adapter = _ApprovedPayloadAdapter()
    config = {"mercadolibre": {"user_id": "seller-1"}}
    approvals = _approved_publication(
        payload={"title": "原确认标题"},
        config=config,
    )
    approvals["mercadolibre"]["payload"]["title"] = "确认后被篡改"
    bus = PublishingBus(
        store,
        adapters={"mercadolibre": adapter},
        config_provider=lambda: deepcopy(config),
        max_retries=0,
        auto_resume_pending=False,
    )
    try:
        with pytest.raises(PublishApprovalBindingError, match="digest"):
            bus.enqueue(
                {"product_id": "product-1"},
                ["mercadolibre"],
                targets={
                    "mercadolibre": {
                        "draft_id": "draft-1",
                        "site": "mlm",
                        "product_id": "product-1",
                    }
                },
                idempotency_key="approved:tampered-payload",
                approved_publications=approvals,
            )
    finally:
        bus.executor.shutdown(wait=True)

    assert store.states == {}
    assert adapter.published_payloads == []


def test_recover_publish_job_after_restart_matches_all_confirmation_facts(
    tmp_path,
) -> None:
    database = ErpDatabase(tmp_path / "erp.sqlite3")
    adapter = _ApprovedPayloadAdapter()
    config = {"mercadolibre": {"user_id": "seller-1"}}
    approvals = _approved_publication(
        payload={"title": "可恢复发布"},
        config=config,
    )
    digest = approvals["mercadolibre"]["validation_digest"]
    first_bus = PublishingBus(
        database,
        adapters={"mercadolibre": adapter},
        config_provider=lambda: deepcopy(config),
        max_retries=0,
        auto_resume_pending=False,
    )
    try:
        first = first_bus.enqueue(
            {"product_id": "product-1"},
            ["mercadolibre"],
            targets={
                "mercadolibre": {
                    "draft_id": "draft-1",
                    "site": "MLM",
                    "product_id": "product-1",
                }
            },
            idempotency_key="approved:restart-recovery",
            approved_publications=approvals,
        )
        first_bus.wait(first["job_id"], timeout=2)
    finally:
        first_bus.executor.shutdown(wait=True)

    restarted = PublishingBus(
        database,
        adapters={"mercadolibre": adapter},
        config_provider=lambda: deepcopy(config),
        max_retries=0,
        auto_resume_pending=False,
    )
    try:
        recovered = restarted.recover_publish_job(
            idempotency_key="approved:restart-recovery",
            product_id="product-1",
            draft_id="draft-1",
            platform="mercadolibre",
            site="mlm",
            validation_digest=digest,
        )
        assert recovered is not None
        assert recovered["job_id"] == first["job_id"]
        assert recovered["status"] == "completed"
        assert recovered["idempotent_replay"] is True

        conflicts = [
            {"product_id": "product-2"},
            {"draft_id": "draft-2"},
            {"platform": "ozon"},
            {"site": "mlb"},
            {"validation_digest": "0" * 64},
        ]
        base = {
            "idempotency_key": "approved:restart-recovery",
            "product_id": "product-1",
            "draft_id": "draft-1",
            "platform": "mercadolibre",
            "site": "mlm",
            "validation_digest": digest,
        }
        for changed in conflicts:
            with pytest.raises(PublishIdempotencyConflictError):
                restarted.recover_publish_job(**{**base, **changed})
    finally:
        restarted.executor.shutdown(wait=True)

    assert adapter.published_payloads == [{"title": "可恢复发布"}]


def test_publishing_bus_requires_trusted_idempotency_key() -> None:
    store = _MemoryPublishJobStore()
    bus = PublishingBus(
        store,
        adapters={"mercadolibre": _RequiredAttributeAdapter()},
        auto_resume_pending=False,
    )
    try:
        with pytest.raises(ValueError, match="可信 idempotency_key"):
            bus.enqueue(
                {"product_id": "product-1"},
                ["mercadolibre"],
                targets={
                    "mercadolibre": {
                        "draft_id": "draft-1",
                        "site": "mlm",
                        "product_id": "product-1",
                    }
                },
                idempotency_key=" ",
            )
    finally:
        bus.executor.shutdown(wait=True)

    assert store.states == {}


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
                    "result": {
                        "error_code": "OZON_CONTRACT_CURRENCY_MISMATCH",
                        "error_map": {
                            "error_code": "OZON_CONTRACT_CURRENCY_MISMATCH",
                            "next_action": "按店铺合同币种重新核价后发布。",
                        },
                    },
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
            "error_code": "OZON_CONTRACT_CURRENCY_MISMATCH",
            "next_action": "按店铺合同币种重新核价后发布。",
            "platforms": [
                {
                    "platform": "ozon",
                    "draft_id": "",
                        "site": "",
                        "sites_to_sell": [],
                        "market_results": [],
                        "status": "failed",
                    "stage": "failed",
                    "attempts": 1,
                    "error": "合同币种不匹配",
                    "error_code": "OZON_CONTRACT_CURRENCY_MISMATCH",
                    "next_action": "按店铺合同币种重新核价后发布。",
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


def test_publishing_bus_lists_mercadolibre_sales_markets_without_payload_leaks() -> None:
    store = _MemoryPublishJobStore()
    store.states = {
        "20260829-203906-86e8145e": {
            "job_id": "20260829-203906-86e8145e",
            "status": "completed",
            "created_at": "2026-08-29 20:39:06",
            "updated_at": "2026-08-29 20:39:08",
            "product": {
                "product_id": "50869d686a598917",
                "drafts": {
                    "mercadolibre": {
                        "sites_to_sell": [
                            {"site_id": "MLU", "logistic_type": "remote"}
                        ]
                    }
                },
            },
            "approved_publications": {
                "mercadolibre": {
                    "validation_digest": "digest-mla-must-not-leak",
                    "store_identity": "store-identity-must-not-leak",
                    "payload": {
                        "title": "APPROVED_PAYLOAD_MUST_NOT_LEAK_MLA",
                        "sites_to_sell": [
                            {
                                "site_id": "MLA",
                                "logistic_type": "remote",
                                "price": 10.49,
                                "listing_type_id": "gold_special",
                            }
                        ],
                    },
                }
            },
            "platforms": {
                "mercadolibre": {
                    "draft_id": "d12fb1fe48cb6",
                    "site": "CBT",
                    "status": "success",
                    "stage": "finished",
                    "attempts": 1,
                    "error": "",
                }
            },
        },
        "20260829-204353-9e0373b7": {
            "job_id": "20260829-204353-9e0373b7",
            "status": "completed",
            "created_at": "2026-08-29 20:43:53",
            "updated_at": "2026-08-29 20:43:54",
            "product": {
                "product_id": "50869d686a598917",
                "drafts": {
                    "mercadolibre": {
                        "sites_to_sell": [
                            {"site_id": "MLA", "logistic_type": "remote"}
                        ]
                    }
                },
            },
            "approved_publications": {
                "mercadolibre": {
                    "validation_digest": "digest-mlu-must-not-leak",
                    "store_identity": "store-identity-must-not-leak",
                    "payload": {
                        "title": "APPROVED_PAYLOAD_MUST_NOT_LEAK_MLU",
                        "sites_to_sell": [
                            {
                                "site_id": "MLU",
                                "logistic_type": "remote",
                                "price": 199.99,
                                "listing_type_id": "gold_special",
                            }
                        ],
                    },
                }
            },
            "platforms": {
                "mercadolibre": {
                    "draft_id": "d5cc0d58cb7bd",
                    "site": "CBT",
                    "status": "failed",
                    "stage": "failed",
                    "attempts": 1,
                    "error": "Listing in Uruguay is currently unavailable",
                }
            },
        },
    }
    bus = PublishingBus(store, adapters={}, auto_resume_pending=False)
    try:
        items = {
            item["job_id"]: item
            for item in bus.list_jobs(platform="mercadolibre")["items"]
        }
    finally:
        bus.executor.shutdown(wait=True)

    mla = items["20260829-203906-86e8145e"]
    assert mla["status"] == "success"
    assert mla["platforms"][0]["site"] == "CBT"
    assert mla["platforms"][0]["sites_to_sell"] == [
        {"site_id": "MLA", "logistic_type": "remote"}
    ]

    mlu = items["20260829-204353-9e0373b7"]
    assert mlu["status"] == "failed"
    assert mlu["platforms"][0]["site"] == "CBT"
    assert mlu["platforms"][0]["sites_to_sell"] == [
        {"site_id": "MLU", "logistic_type": "remote"}
    ]

    serialized = repr(items)
    for forbidden in (
        "approved_publications",
        "validation_digest",
        "store_identity",
        "APPROVED_PAYLOAD_MUST_NOT_LEAK_MLA",
        "APPROVED_PAYLOAD_MUST_NOT_LEAK_MLU",
        "gold_special",
        "199.99",
    ):
        assert forbidden not in serialized


def test_publishing_bus_sales_markets_fall_back_to_frozen_product_snapshot() -> None:
    store = _MemoryPublishJobStore()
    store.states = {
        "legacy-job": {
            "job_id": "legacy-job",
            "status": "completed",
            "product": {
                "product_id": "product-legacy",
                "drafts": {
                    "mercadolibre": {
                        "sites_to_sell": [
                            {"site_id": "mco", "logistic_type": "REMOTE"},
                            {"site_id": "MCO", "logistic_type": "remote"},
                            {"site_id": "CBT", "logistic_type": "remote"},
                        ]
                    }
                },
            },
            "platforms": {
                "mercadolibre": {
                    "draft_id": "draft-legacy",
                    "site": "CBT",
                    "status": "success",
                    "stage": "finished",
                }
            },
        }
    }
    bus = PublishingBus(store, adapters={}, auto_resume_pending=False)
    try:
        result = bus.list_jobs()
    finally:
        bus.executor.shutdown(wait=True)

    assert result["items"][0]["platforms"][0]["sites_to_sell"] == [
        {"site_id": "MCO", "logistic_type": "remote"}
    ]


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
            context: Any,
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
            idempotency_key="test:verified-success",
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
            context: Any,
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
            idempotency_key="test:pending-poll",
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
            context: Any,
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
        "idempotency_key": "test:resume-pending",
        "idempotency_facts": {
            "product_id": "product-1",
            "platforms": ["ozon"],
            "targets": {
                "ozon": {
                    "draft_id": "draft-1",
                    "site": "global",
                    "product_id": "product-1",
                }
            },
        },
        "status": "running",
        "created_at": "2026-08-08 20:00:00",
        "updated_at": "2026-08-08 20:00:01",
        "product": {"product_id": "product-1"},
        "platforms": {
            "ozon": {
                "platform": "ozon",
                "draft_id": "draft-1",
                "site": "global",
                "product_id": "product-1",
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
            context: Any,
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
            idempotency_key="test:compact-result",
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
    monkeypatch,
) -> None:
    # 总线终态钩子测试不触网：跳过当次类目定义加载。
    from erp_web.runtime_units import category_providers

    monkeypatch.setattr(
        category_providers, "category_provider_for", lambda platform: None
    )

    class SuccessfulAdapter:
        @staticmethod
        def resolve_category(
            product: dict[str, Any],
            config: dict[str, Any],
        ) -> dict[str, Any]:
            return product

        @staticmethod
        def required_attributes_missing(
            context: Any,
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
            idempotency_key="test:terminal-hook",
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


def test_reconciled_terminal_result_writes_a_distinct_audit_log(
    sample_product: dict[str, Any],
) -> None:
    context = get_context()
    saved = context.products.save_product(sample_product)
    product_id = str(saved["product_id"])
    draft = saved["drafts"]["mercadolibre"]
    draft_id = str(draft["draft_id"])
    site = str(draft.get("site") or "mlm")
    initial_state = {
        "job_id": "job-reconciled-audit",
        "status": "outcome_unknown",
        "created_at": "2026-08-27 10:00:00",
        "updated_at": "2026-08-27 10:00:01",
        "product": deepcopy(saved),
        "platforms": {
            "mercadolibre": {
                "platform": "mercadolibre",
                "draft_id": draft_id,
                "site": site,
                "product_id": product_id,
                "status": "outcome_unknown",
                "stage": "outcome_unknown",
                "error": "远端终态未知",
                "result": {
                    "ok": False,
                    "status": "outcome_unknown",
                    "task_id": "task-reconciled-audit",
                },
            }
        },
    }

    persist_publish_bus_terminal_results(
        deepcopy(initial_state),
        context=context,
    )
    reconciled_state = deepcopy(initial_state)
    reconciled_state["status"] = "completed"
    reconciled_state["updated_at"] = "2026-08-27 10:01:00"
    reconciled_item = reconciled_state["platforms"]["mercadolibre"]
    reconciled_item.update(
        {
            "status": "success",
            "stage": "finished",
            "error": "",
            "updated_at": "2026-08-27 10:01:00",
            "result": {
                "ok": True,
                "status": "published",
                "task_id": "task-reconciled-audit",
                "id": "U-RECONCILED-1",
            },
            "reconciliation": {
                "status": "applied",
                "checked_at": "2026-08-27 10:01:00",
                "write_replayed": False,
            },
        }
    )

    persist_publish_bus_terminal_results(
        reconciled_state,
        context=context,
    )

    logs = context.db.list_publish_logs()
    original = [
        item
        for item in logs
        if item.get("job_id") == "job-reconciled-audit"
    ]
    reconciliation = [
        item
        for item in logs
        if item.get("job_id") == "job-reconciled-audit:reconciliation"
    ]
    assert len(original) == 1
    assert original[0]["status"] == "outcome_unknown"
    assert len(reconciliation) == 1
    assert reconciliation[0]["status"] == "published"
    assert reconciliation[0]["source_job_id"] == "job-reconciled-audit"
    assert reconciliation[0]["reconciliation"]["status"] == "applied"
    assert reconciliation[0]["reconciliation"]["write_replayed"] is False
    assert context.db.load_draft_model(draft_id)["publish_status"] == "published"


def test_terminal_hook_updates_a_bound_non_primary_target(
    sample_product: dict[str, Any],
) -> None:
    context = get_context()
    saved = context.products.save_product(sample_product)
    product_id = str(saved["product_id"])
    draft_id = str(saved["drafts"]["mercadolibre"]["draft_id"])
    draft = context.db.load_draft_model(draft_id)
    primary_site = str(draft.get("site") or "mlm")
    draft["platform"] = "mercadolibre"
    draft["platforms"] = ["mercadolibre", "ozon"]
    draft["target_sites"] = [
        {
            "platform": "mercadolibre",
            "site": primary_site,
            "publish_status": "ready",
            "status": "ready_to_publish",
        },
        {
            "platform": "ozon",
            "site": "global",
            "publish_status": "ready",
            "status": "ready_to_publish",
        },
    ]
    context.db.upsert_draft_model(product_id, "mercadolibre", draft)
    state = {
        "job_id": "job-bound-secondary-target",
        "draft_id": draft_id,
        "status": "completed",
        "created_at": "2026-08-08 22:10:00",
        "updated_at": "2026-08-08 22:10:01",
        "product": deepcopy(saved),
        "platforms": {
            "ozon": {
                "platform": "ozon",
                "draft_id": draft_id,
                "site": "global",
                "product_id": product_id,
                "status": "success",
                "stage": "finished",
                "attempts": 1,
                "error": "",
                "result": {
                    "ok": True,
                    "status": "published",
                    "id": "OZON-SECONDARY-1",
                },
            }
        },
    }

    persisted = persist_publish_bus_terminal_results(state, context=context)

    saved_draft = context.db.load_draft_model(draft_id)
    targets = {
        (str(item.get("platform") or ""), str(item.get("site") or "")): item
        for item in saved_draft["target_sites"]
    }
    assert saved_draft["platform"] == "mercadolibre"
    assert targets[("mercadolibre", primary_site)]["publish_status"] == "ready"
    assert targets[("ozon", "global")]["publish_status"] == "published"
    assert targets[("ozon", "global")]["last_publish_task"]["job_id"] == "job-bound-secondary-target"
    assert persisted["persisted_drafts"]["ozon"]["draft_id"] == draft_id


def test_terminal_hook_rejects_a_target_not_bound_to_the_draft(
    sample_product: dict[str, Any],
) -> None:
    context = get_context()
    saved = context.products.save_product(sample_product)
    product_id = str(saved["product_id"])
    draft_id = str(saved["drafts"]["mercadolibre"]["draft_id"])
    state = {
        "job_id": "job-unbound-target",
        "status": "completed",
        "product": deepcopy(saved),
        "platforms": {
            "ozon": {
                "platform": "ozon",
                "draft_id": draft_id,
                "site": "global",
                "product_id": product_id,
                "status": "success",
                "stage": "finished",
                "attempts": 1,
                "error": "",
                "result": {"ok": True, "status": "published"},
            }
        },
    }

    with pytest.raises(RuntimeError, match="发布任务目标不属于绑定草稿"):
        persist_publish_bus_terminal_results(state, context=context)

    persisted = context.db.load_draft_model(draft_id)
    assert str(persisted.get("publish_status") or "") != "published"


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
    facts = {
        "product_id": str(product["product_id"]),
        "platforms": ["mercadolibre"],
        "targets": {
            "mercadolibre": {
                "draft_id": str(draft["draft_id"]),
                "site": str(draft.get("site") or "mlm"),
                "product_id": str(product["product_id"]),
            }
        },
    }
    return {
        "job_id": job_id,
        "idempotency_key": f"test:{job_id}",
        "idempotency_facts": facts,
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
    context.db.create_publish_job(state)

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


def test_publish_recovery_fences_crash_during_remote_write(tmp_path) -> None:
    database = ErpDatabase(tmp_path / "erp.sqlite3")
    state = {
        "job_id": "job-write-crash",
        "idempotency_key": "write-crash:first",
        "idempotency_facts": {"digest": "facts-1"},
        "draft_id": "draft-write-crash",
        "status": "running",
        "created_at": "2026-08-26 10:00:00",
        "updated_at": "2026-08-26 10:00:01",
        "product": {"product_id": "product-write-crash"},
        "platforms": {
            "mercadolibre": {
                "platform": "mercadolibre",
                "draft_id": "draft-write-crash",
                "site": "CBT",
                "product_id": "product-write-crash",
                "status": "running",
                "stage": "publishing_approved_payload",
                "attempts": 1,
                "error": "",
                "result": None,
            }
        },
    }
    database.create_publish_job(state)
    adapter = _SuccessfulPublishingAdapter()
    bus = PublishingBus(
        database,
        adapters={"mercadolibre": adapter},
        auto_resume_pending=False,
    )
    try:
        recovered = bus.recover_pending_jobs()
        persisted = bus.get_status("job-write-crash")
    finally:
        bus.executor.shutdown(wait=True)

    assert recovered == ["job-write-crash"]
    assert persisted["status"] == "outcome_unknown"
    assert persisted["platforms"]["mercadolibre"]["result"][
        "outcome_unknown"
    ] is True
    assert adapter.publish_calls == 0

    competing = deepcopy(state)
    competing.update(
        {
            "job_id": "job-write-crash-retry",
            "idempotency_key": "write-crash:retry",
            "idempotency_facts": {"digest": "facts-2"},
            "status": "queued",
        }
    )
    competing["platforms"]["mercadolibre"].update(
        {"status": "queued", "stage": "queued", "attempts": 0}
    )
    reused, created = database.create_publish_job(competing)
    assert created is False
    assert reused["job_id"] == "job-write-crash"


def test_outcome_unknown_task_can_be_reconciled_without_replaying_write() -> None:
    class ReconcileAdapter:
        def __init__(self) -> None:
            self.poll_calls = 0

        def poll_publish_status(
            self,
            result: dict[str, Any],
            config: dict[str, Any],
        ) -> dict[str, Any]:
            self.poll_calls += 1
            assert result["task_id"] == "task-1"
            return {
                "ok": True,
                "status": "published",
                "id": "U123",
            }

    store = _MemoryPublishJobStore()
    store.states["job-unknown"] = {
        "job_id": "job-unknown",
        "idempotency_key": "unknown:task-1",
        "status": "outcome_unknown",
        "terminal_results_persisted": True,
        "product": {"product_id": "product-1"},
        "platforms": {
            "mercadolibre": {
                "platform": "mercadolibre",
                "draft_id": "draft-1",
                "site": "CBT",
                "product_id": "product-1",
                "status": "outcome_unknown",
                "stage": "outcome_unknown",
                "error": "远端终态未知",
                "result": {
                    "ok": False,
                    "status": "outcome_unknown",
                    "task_id": "task-1",
                    "outcome_unknown": True,
                },
            }
        },
    }
    adapter = ReconcileAdapter()
    terminal_states: list[dict[str, Any]] = []
    bus = PublishingBus(
        store,
        adapters={"mercadolibre": adapter},
        terminal_callback=lambda state: terminal_states.append(
            deepcopy(state)
        ) or state,
        auto_resume_pending=False,
    )
    try:
        result = bus.reconcile_outcome_unknown(
            "job-unknown",
            "mercadolibre",
        )
        persisted = bus.get_status("job-unknown")
    finally:
        bus.executor.shutdown(wait=True)

    assert result["resolved"] is True
    assert result["resolution"] == "applied"
    assert adapter.poll_calls == 1
    assert persisted["status"] == "completed"
    assert persisted["platforms"]["mercadolibre"]["status"] == "success"
    assert persisted["platforms"]["mercadolibre"]["reconciliation"] == {
        "status": "applied",
        "checked_at": persisted["platforms"]["mercadolibre"][
            "reconciliation"
        ]["checked_at"],
        "write_replayed": False,
    }
    assert persisted["terminal_results_persisted"] is True
    assert len(terminal_states) == 1


def test_outcome_unknown_reconciliation_keeps_lock_while_task_is_pending() -> None:
    class PendingAdapter:
        @staticmethod
        def poll_publish_status(
            result: dict[str, Any],
            config: dict[str, Any],
        ) -> dict[str, Any]:
            return {
                **result,
                "ok": True,
                "status": "pending_confirmation",
            }

    store = _MemoryPublishJobStore()
    store.states["job-pending-reconcile"] = {
        "job_id": "job-pending-reconcile",
        "idempotency_key": "unknown:task-pending",
        "status": "outcome_unknown",
        "terminal_results_persisted": True,
        "product": {"product_id": "product-1"},
        "platforms": {
            "mercadolibre": {
                "platform": "mercadolibre",
                "draft_id": "draft-1",
                "site": "CBT",
                "product_id": "product-1",
                "status": "outcome_unknown",
                "result": {
                    "ok": False,
                    "status": "outcome_unknown",
                    "task_ids": ["task-pending"],
                },
            }
        },
    }
    terminal_states: list[dict[str, Any]] = []
    bus = PublishingBus(
        store,
        adapters={"mercadolibre": PendingAdapter()},
        terminal_callback=lambda state: terminal_states.append(state) or state,
        auto_resume_pending=False,
    )
    try:
        result = bus.reconcile_outcome_unknown(
            "job-pending-reconcile",
            "mercadolibre",
        )
        persisted = bus.get_status("job-pending-reconcile")
    finally:
        bus.executor.shutdown(wait=True)

    assert result["resolved"] is False
    assert result["resolution"] == "pending"
    assert persisted["status"] == "outcome_unknown"
    assert (
        persisted["platforms"]["mercadolibre"]["status"]
        == "outcome_unknown"
    )
    assert persisted["terminal_results_persisted"] is True
    assert terminal_states == []


def test_outcome_unknown_without_task_id_cannot_be_auto_reconciled() -> None:
    class PollCapableAdapter:
        @staticmethod
        def poll_publish_status(
            result: dict[str, Any],
            config: dict[str, Any],
        ) -> dict[str, Any]:
            raise AssertionError("缺少 task_id 时不应访问远端")

    store = _MemoryPublishJobStore()
    store.states["job-no-task"] = {
        "job_id": "job-no-task",
        "idempotency_key": "unknown:no-task",
        "status": "outcome_unknown",
        "product": {"product_id": "product-1"},
        "platforms": {
            "mercadolibre": {
                "status": "outcome_unknown",
                "result": {
                    "ok": False,
                    "status": "outcome_unknown",
                },
            }
        },
    }
    bus = PublishingBus(
        store,
        adapters={"mercadolibre": PollCapableAdapter()},
        auto_resume_pending=False,
    )
    try:
        with pytest.raises(ValueError, match="没有远端 task_id"):
            bus.reconcile_outcome_unknown("job-no-task", "mercadolibre")
    finally:
        bus.executor.shutdown(wait=True)


def test_mercadolibre_adapter_uses_root_owned_required_attributes() -> None:
    from erp_web.runtime_units.publish_context import PreparedPublishContext

    from tests.publish_category_support import definition_from_record

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
    }
    record = {
        "platform": "mercadolibre",
        "category_id": "MLM-1",
        "attributes": {
            "required": [
                {"id": "BRAND", "name": "Marca", "required": True},
            ],
            "optional": [],
        },
    }
    context = PreparedPublishContext(
        product=product,
        draft=product["drafts"]["mercadolibre"],
        target={},
        category_definition=definition_from_record(record),
        platform="mercadolibre",
    )

    assert adapter.required_attributes_missing(context, {}) == []


def test_mercadolibre_adapter_refreshes_auth_before_preparing_product(
    monkeypatch,
) -> None:
    adapter = MercadoLibrePublishingAdapter()
    config = {
        "mercadolibre": {
            "access_token": "expired-token",
            "refresh_token": "refresh-token",
        }
    }
    product = {"product_id": "product-1", "source": {}, "drafts": {}}
    upload_tokens: list[str] = []

    def fake_auth(
        target_config: dict[str, Any],
        *,
        force_refresh: bool = False,
    ) -> str:
        assert force_refresh is False
        target_config["mercadolibre"]["access_token"] = "fresh-token"
        return "fresh-token"

    def fake_upload(
        target_product: dict[str, Any],
        token: str,
    ) -> dict[str, Any]:
        upload_tokens.append(token)
        return {"ok": True, "product": target_product, "errors": []}

    monkeypatch.setattr(
        publish_adapter,
        "get_mercadolibre_access_token",
        fake_auth,
    )
    monkeypatch.setattr(
        publish_adapter,
        "ensure_mercadolibre_pictures_uploaded",
        fake_upload,
    )

    adapter.prepare_product(product, config)

    assert upload_tokens == ["fresh-token"]
    assert config["mercadolibre"]["access_token"] == "fresh-token"


def test_mercadolibre_adapter_refreshes_and_retries_once_on_401(
    monkeypatch,
) -> None:
    adapter = MercadoLibrePublishingAdapter()
    config = {
        "mercadolibre": {
            "access_token": "expired-token",
            "refresh_token": "refresh-token",
        }
    }
    publish_tokens: list[str] = []
    refresh_requests: list[bool] = []

    def fake_publish(payload: dict[str, Any], token: str) -> dict[str, Any]:
        del payload
        publish_tokens.append(token)
        if token == "expired-token":
            raise PublishAdapterError(
                "MERCADOLIBRE_AUTH_FAILED",
                'POST https://api.mercadolibre.com/global/items failed: '
                '401 {"code":"unauthorized","message":"invalid access token"}',
                details={"http_status": 401},
            )
        return {"ok": True, "item_id": "CBT123"}

    def fake_auth(
        target_config: dict[str, Any],
        *,
        force_refresh: bool = False,
    ) -> str:
        refresh_requests.append(force_refresh)
        if not force_refresh:
            return "expired-token"
        target_config["mercadolibre"]["access_token"] = "fresh-token"
        return "fresh-token"

    monkeypatch.setattr(
        publish_adapter.marketplace_api,
        "publish_mercadolibre",
        fake_publish,
    )
    monkeypatch.setattr(
        publish_adapter,
        "get_mercadolibre_access_token",
        fake_auth,
    )

    result = adapter.publish_payload(
        {"_listing_model": "traditional_global_items"},
        config,
    )

    assert result["ok"] is True
    assert publish_tokens == ["expired-token", "fresh-token"]
    assert refresh_requests == [False, True]


def test_mercadolibre_adapter_does_not_retry_non_auth_publish_error(
    monkeypatch,
) -> None:
    adapter = MercadoLibrePublishingAdapter()
    config = {"mercadolibre": {"access_token": "valid-token"}}
    publish_calls = 0

    def fake_publish(payload: dict[str, Any], token: str) -> dict[str, Any]:
        del payload, token
        nonlocal publish_calls
        publish_calls += 1
        raise RuntimeError("POST /global/items failed: 400 invalid title")

    monkeypatch.setattr(
        publish_adapter.marketplace_api,
        "publish_mercadolibre",
        fake_publish,
    )
    monkeypatch.setattr(
        publish_adapter,
        "get_mercadolibre_access_token",
        lambda *_args, **_kwargs: "valid-token",
    )

    with pytest.raises(RuntimeError, match="invalid title"):
        adapter.publish_payload(
            {"_listing_model": "traditional_global_items"},
            config,
        )

    assert publish_calls == 1


@pytest.mark.parametrize(
    "error",
    [
        PublishAdapterError(
            "MERCADOLIBRE_REQUEST_INVALID",
            "响应里的商品 ID CBT401 不满足契约",
            details={"http_status": 400},
        ),
        PublishAdapterError(
            "MERCADOLIBRE_AUTH_FAILED",
            "POST /global/items failed: 401 unauthorized",
            details={
                "http_status": 401,
                "remote_write_dispatched": True,
                "outcome_unknown": True,
            },
        ),
    ],
)
def test_mercadolibre_adapter_never_replays_ambiguous_write_failure(
    monkeypatch,
    error: PublishAdapterError,
) -> None:
    adapter = MercadoLibrePublishingAdapter()
    config = {"mercadolibre": {"access_token": "token"}}
    publish_calls = 0

    def fake_publish(payload: dict[str, Any], token: str) -> dict[str, Any]:
        del payload, token
        nonlocal publish_calls
        publish_calls += 1
        raise error

    monkeypatch.setattr(
        publish_adapter.marketplace_api,
        "publish_mercadolibre",
        fake_publish,
    )
    monkeypatch.setattr(
        publish_adapter,
        "get_mercadolibre_access_token",
        lambda *_args, **_kwargs: "token",
    )

    with pytest.raises(PublishAdapterError) as exc_info:
        adapter.publish_payload(
            {"_listing_model": "traditional_global_items"},
            config,
        )

    assert exc_info.value is error
    assert publish_calls == 1


def test_mercadolibre_adapter_stops_after_second_401(monkeypatch) -> None:
    adapter = MercadoLibrePublishingAdapter()
    config = {"mercadolibre": {"access_token": "expired-token"}}
    publish_tokens: list[str] = []
    refresh_calls = 0

    def rejected_publish(
        payload: dict[str, Any],
        token: str,
    ) -> dict[str, Any]:
        del payload
        publish_tokens.append(token)
        raise PublishAdapterError(
            "MERCADOLIBRE_AUTH_FAILED",
            "POST /global/items failed: 401 unauthorized",
            details={"http_status": 401},
        )

    def fake_auth(
        target_config: dict[str, Any],
        *,
        force_refresh: bool = False,
    ) -> str:
        nonlocal refresh_calls
        if not force_refresh:
            return "expired-token"
        refresh_calls += 1
        target_config["mercadolibre"]["access_token"] = "fresh-token"
        return "fresh-token"

    monkeypatch.setattr(
        publish_adapter.marketplace_api,
        "publish_mercadolibre",
        rejected_publish,
    )
    monkeypatch.setattr(
        publish_adapter,
        "get_mercadolibre_access_token",
        fake_auth,
    )

    with pytest.raises(PublishAdapterError) as exc_info:
        adapter.publish_payload(
            {"_listing_model": "traditional_global_items"},
            config,
        )

    assert exc_info.value.code == "MERCADOLIBRE_AUTH_FAILED"
    assert publish_tokens == ["expired-token", "fresh-token"]
    assert refresh_calls == 1


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
