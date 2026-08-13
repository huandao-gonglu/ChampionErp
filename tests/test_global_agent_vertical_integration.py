from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import pytest

from erp_web.context import get_context
from erp_web.facades import global_agent_facade
from erp_web.facades.global_agent_facade import (
    GLOBAL_TASK_CAPABILITY_NAMES,
    build_global_task_capabilities,
)
from erp_web.runtime_units.attribute_fill_capabilities import (
    fill_product_attributes as real_fill_product_attributes,
)
from erp_web.runtime_units.category_capabilities import (
    match_category as real_match_category,
)
from erp_web.runtime_units.publish_adapter import OzonPublishingAdapter
from erp_web.runtime_units.publish_bus import (
    persist_publish_bus_terminal_results,
)
from erp_web.runtime_units.publishing_bus_core import PublishingBus
from erp_web.schemas.draft_capabilities import (
    DraftQueryCriteria,
    DraftQuerySnapshot,
)
from erp_web.schemas.global_tasks import (
    GlobalPlanningDecision,
    GlobalTaskInputRequest,
    GlobalTaskPlanParameters,
    GlobalTaskPlanProposal,
    GlobalTaskStartRequest,
    GlobalTaskStepProposal,
)
from erp_web.services.global_task_controller import (
    GlobalTaskController,
    GlobalTaskPlanningOutcome,
)
from erp_web.services.pricing_service import pricing_calculation_fingerprint
from erp_web.stores.global_task_store import LocalGlobalTaskStore


class _PlannerBoundary:
    """主 Agent 模型边界；测试计划本身仍由真实 Controller 校验。"""

    def __init__(self, snapshot_id: str) -> None:
        self.snapshot_id = snapshot_id
        self.calls = 0

    def __call__(self, _task, _supplement: str) -> GlobalTaskPlanningOutcome:
        self.calls += 1
        capabilities = (
            "draft.prepare_for_market",
            "product.publish.validate",
            "product.publish.request",
        )
        return GlobalTaskPlanningOutcome(
            decision=GlobalPlanningDecision(
                action="plan",
                plan=GlobalTaskPlanProposal(
                    steps=[
                        GlobalTaskStepProposal(
                            local_key=f"vertical-{index}",
                            capability=capability,
                            objective=f"纵向执行 {capability}",
                        )
                        for index, capability in enumerate(capabilities, start=1)
                    ],
                    draft_position=1,
                    target_platform="ozon",
                    parameters=GlobalTaskPlanParameters(regenerate_copy=True),
                ),
                query_snapshot_id=self.snapshot_id,
                explanation="按市场准备、确定性校验和确认后发布的顺序执行。",
            ),
            execution_conversation_id="planning-execution-vertical",
        )


class _PlatformNetworkBoundary(OzonPublishingAdapter):
    """保留真实 Ozon 确定性逻辑，只替代最终外部网络提交。"""

    def __init__(self, *, succeed: bool) -> None:
        self.succeed = succeed
        self.publish_calls = 0

    def publish(
        self,
        product: dict[str, Any],
        platform: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        raise AssertionError("确认发布不得重新从 product 构建外发 payload")

    def publish_payload(
        self,
        payload: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        self.publish_calls += 1
        assert payload["items"][0]["description_category_id"] == 17027949
        assert config["ozon"]["client_id"] == "vertical-client"
        if self.succeed:
            return {
                "ok": True,
                "status": "real_publish_success",
                "external_id": "ozon-vertical-item",
            }
        return {
            "ok": False,
            "status": "real_publish_failed",
            "error": "平台拒绝纵向测试商品",
            "error_code": "VERTICAL_REMOTE_REJECTED",
        }


def _category_record(
    platform: str,
    category_id: str,
    *,
    site: str,
    include_attributes: bool,
) -> dict[str, Any]:
    assert (platform, category_id, site, include_attributes) == (
        "ozon",
        "94765",
        "global",
        True,
    )
    return {
        "category_id": "94765",
        "description_category_id": "17027949",
        "category_path": "Электроника / Вентиляторы",
        "platform": "ozon",
        "site": "global",
        "version": 2,
        "source": "vertical-test-boundary",
        "attributes": {
            "required": [
                {
                    "id": "85",
                    "name": "Бренд",
                    "required": True,
                    "dictionary_id": 0,
                    "raw": {"dictionary_id": 0},
                },
                {
                    "id": "4191",
                    "name": "Аннотация",
                    "required": True,
                },
            ],
            "optional": [],
        },
    }


def _source_product() -> dict[str, Any]:
    basis = {
        "cost_cny": "100",
        "listing_currency": "RUB",
        "length_cm": "12.3",
        "width_cm": "4.5",
        "height_cm": "6.7",
        "weight_kg": "0.25",
    }
    return {
        "product_id": "product-vertical-ozon",
        "name": "Portable fan",
        "brand": "Champion",
        "model": "V1",
        "sku": "OZON-VERTICAL-1",
        "cost": "100",
        "stock": "5",
        "upc": "123456789012",
        "source": {
            "title": "Portable fan",
            "description": "Source description",
            "source_platform": "1688",
            "source_url": "https://example.com/vertical-product",
            "currency": "CNY",
            "price": "100",
            "weight_kg": "0.25",
            "dimensions": {
                "length_cm": "12.3",
                "width_cm": "4.5",
                "height_cm": "6.7",
            },
            "image_pool": [
                {
                    "id": "image-vertical-1",
                    "url": "https://cdn.example.com/vertical-ozon.jpg",
                    "origin": "source",
                    "status": "ready",
                    "selected": True,
                    "is_main": True,
                    "order": 0,
                    "platforms": ["ozon"],
                }
            ],
        },
        "drafts": {
            "ozon": {
                "enabled": True,
                "platform": "ozon",
                "platforms": ["ozon"],
                "site": "global",
                "language": "ru-RU",
                "target_sites": [
                    {
                        "platform": "ozon",
                        "site": "global",
                        "language": "ru-RU",
                        "market_currency": "RUB",
                        "listing_currency": "RUB",
                    }
                ],
                "title": "Portable fan",
                "description": "Source description",
                "brand": "Champion",
                "model": "V1",
                "sku": "OZON-VERTICAL-1",
                "upc": "123456789012",
                "stock": "5",
                "vat": "0",
                "images": [],
                "attributes": {},
                "package_dimensions": {
                    "length_cm": "12.3",
                    "width_cm": "4.5",
                    "height_cm": "6.7",
                    "weight_kg": "0.25",
                },
                "pricing": {
                    "targets": {
                        "ozon:global": {
                            "listing_currency": "RUB",
                            "suggested_price": {
                                "amount": "1999.90",
                                "currency": "RUB",
                            },
                            "applied_price": {
                                "amount": "1999.90",
                                "currency": "RUB",
                            },
                            "calculation_basis": basis,
                            "calculation_fingerprint": (
                                pricing_calculation_fingerprint(basis)
                            ),
                        }
                    }
                },
                "status": "claimed",
            }
        },
    }


def _controller(
    *,
    store: LocalGlobalTaskStore,
    planner: _PlannerBoundary,
    capabilities,
    bus: PublishingBus,
) -> GlobalTaskController:
    return GlobalTaskController(
        store=store,
        planner=planner,
        capabilities=capabilities,
        publish_status_reader=bus.get_public_status,
    )


@pytest.mark.parametrize(
    ("remote_success", "expected_task_status", "expected_publish_status"),
    [
        (True, "completed", "published"),
        (False, "failed", "failed"),
    ],
)
def test_global_agent_real_vertical_publish_flow(
    monkeypatch: pytest.MonkeyPatch,
    remote_success: bool,
    expected_task_status: str,
    expected_publish_status: str,
) -> None:
    context = get_context()
    context.config.update_store_config_fields(
        "ozon",
        {
            "client_id": "vertical-client",
            "api_key": "vertical-api-key",
            "auth_status": "success",
            "auth_masked_account": "vertical-seller",
            "shop_name": "纵向测试店铺",
            "contract_currency": "RUB",
            "listing_currency": "RUB",
        },
        preserve_empty_sensitive=False,
    )
    saved_product = context.products.save_product(_source_product())
    draft_id = str(saved_product["drafts"]["ozon"]["draft_id"])
    snapshot_id = f"snapshot-vertical-{'success' if remote_success else 'failed'}"
    task_store = LocalGlobalTaskStore(context.db)
    task_store.save_draft_query_snapshot(
        DraftQuerySnapshot(
            snapshot_id=snapshot_id,
            draft_ids=[draft_id],
            total=1,
            count_by_platform={"ozon": 1},
            count_by_status={"claimed": 1},
            query=DraftQueryCriteria(scope="all", platform="ozon"),
            created_at=datetime.now(timezone.utc),
        )
    )

    boundary_calls = {"copy": 0, "category": 0, "attributes": 0}

    def direct_copy_boundary(
        product: dict[str, Any],
        source_platform: str,
        target_market: str,
        language: str,
        mode: str,
        app_config: dict[str, Any],
        *,
        app_dir,
    ) -> dict[str, Any]:
        boundary_calls["copy"] += 1
        assert product["product_id"] == saved_product["product_id"]
        assert (source_platform, target_market, language, mode) == (
            "1688",
            "ozon",
            "ru-RU",
            "rewrite",
        )
        assert isinstance(app_config, dict)
        assert app_dir == context.paths.app_dir
        return {
            "ok": True,
            "language": "ru-RU",
            "copy": {
                "title": "Портативный вентилятор",
                "description": "Подробное описание портативного вентилятора.",
                "bullets": ["Компактный"],
            },
        }

    def focused_category_boundary(*_args, **kwargs) -> dict[str, Any]:
        assert kwargs["parent_conversation_id"] == "conversation-vertical"
        boundary_calls["category"] += 1
        return {
            "ok": True,
            "status": "unresolved",
            "selected_category_id": None,
            "query": "portable fan",
            "candidates": [
                {"category_id": "94765", "name": "Вентиляторы"}
            ],
            "decision": {"model_confidence": 0.4},
            "failure": {
                "code": "CATEGORY_MATCH_UNRESOLVED",
                "message": "请选择最终 Ozon 类目。",
                "retryable": False,
            },
            "trace": {"conversation_id": "focused-category-vertical"},
        }

    def focused_attribute_boundary(
        product: dict[str, Any],
        platform: str,
        _record: dict[str, Any] | None,
        *,
        parent_conversation_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        assert parent_conversation_id == "conversation-vertical"
        boundary_calls["attributes"] += 1
        updated = deepcopy(product)
        draft = updated["drafts"][platform]
        draft["attributes"] = {
            **(
                draft.get("attributes")
                if isinstance(draft.get("attributes"), dict)
                else {}
            ),
            "4191": draft["description"],
        }
        return updated, {
            "source": "focused_agent",
            "conversation_id": "focused-attributes-vertical",
        }

    def category_capability_boundary(
        request,
        *,
        product_store,
        matcher,
        parent_conversation_id=None,
    ):
        return real_match_category(
            request,
            product_store=product_store,
            matcher=matcher,
            category_record_loader=_category_record,
            parent_conversation_id=parent_conversation_id,
        )

    def attribute_capability_boundary(
        request,
        *,
        product_store,
        parent_conversation_id=None,
    ):
        return real_fill_product_attributes(
            request,
            product_store=product_store,
            attribute_filler=focused_attribute_boundary,
            category_record_loader=_category_record,
            parent_conversation_id=parent_conversation_id,
        )

    monkeypatch.setattr(
        global_agent_facade,
        "generate_ai_copy_bundle",
        direct_copy_boundary,
    )
    monkeypatch.setattr(
        global_agent_facade,
        "run_category_match",
        focused_category_boundary,
    )
    monkeypatch.setattr(
        global_agent_facade,
        "match_category",
        category_capability_boundary,
    )
    monkeypatch.setattr(
        global_agent_facade,
        "fill_product_attributes",
        attribute_capability_boundary,
    )

    network_adapter = _PlatformNetworkBoundary(succeed=remote_success)
    initial_bus = PublishingBus(
        context.db,
        adapters={"ozon": network_adapter},
        config_provider=context.config.load_store_config,
        terminal_callback=lambda state: persist_publish_bus_terminal_results(
            state,
            context=context,
        ),
        max_retries=0,
        auto_resume_pending=False,
    )
    context._publishing_bus = initial_bus
    restarted_bus: PublishingBus | None = None
    try:
        capabilities = build_global_task_capabilities(context)
        assert frozenset(capabilities) == GLOBAL_TASK_CAPABILITY_NAMES
        planner = _PlannerBoundary(snapshot_id)
        controller = _controller(
            store=task_store,
            planner=planner,
            capabilities=capabilities,
            bus=initial_bus,
        )

        category_pause = controller.create_task(
            GlobalTaskStartRequest(
                goal="把第一个草稿准备到 Ozon 并发布",
                platform="ozon",
                draft_query_snapshot_id=snapshot_id,
            ),
            ai_work_conversation_id="conversation-vertical",
        )
        assert category_pause.status == "needs_input"
        assert category_pause.pending_input_owner == "capability"
        assert [item.key for item in category_pause.pending_inputs] == [
            "category_id"
        ]
        assert category_pause.pending_inputs[0].input_owner == "step"
        assert category_pause.agent_execution_conversation_ids == [
            "planning-execution-vertical",
            "focused-category-vertical",
        ]
        assert boundary_calls == {"copy": 1, "category": 1, "attributes": 0}
        operation_key = (
            f"global-task:{category_pause.task_id}:"
            "step:step_1_vertical-1:copy"
        )
        assert context.db.load_draft_model(draft_id)[
            "copy_operation_key"
        ] == operation_key

        # 模拟领域草稿已原子写入文案+marker，但进程在 Capability 返回、任务
        # 状态保存前退出：SQLite 任务仍是 running，且尚未记录 focused link。
        crashed_steps = list(category_pause.steps)
        crashed_steps[0] = crashed_steps[0].model_copy(update={"status": "running"})
        task_store.save_task(
            category_pause.model_copy(
                update={
                    "status": "running",
                    "steps": crashed_steps,
                    "pending_inputs": [],
                    "pending_input_owner": "none",
                    "agent_execution_conversation_ids": [
                        "planning-execution-vertical"
                    ],
                }
            )
        )
        controller = _controller(
            store=LocalGlobalTaskStore(context.db),
            planner=planner,
            capabilities=capabilities,
            bus=initial_bus,
        )
        category_pause = controller.get_state(category_pause.task_id)
        assert category_pause.status == "needs_input"
        assert category_pause.agent_execution_conversation_ids == [
            "planning-execution-vertical",
            "focused-category-vertical",
        ]
        assert boundary_calls == {"copy": 1, "category": 2, "attributes": 0}

        # 每次补资料都重建 Controller/Store，证明暂停状态来自 SQLite owner。
        controller = _controller(
            store=LocalGlobalTaskStore(context.db),
            planner=planner,
            capabilities=capabilities,
            bus=initial_bus,
        )
        attribute_pause = controller.submit_input(
            GlobalTaskInputRequest(
                task_id=category_pause.task_id,
                inputs={"category_id": "94765"},
            )
        )
        assert attribute_pause.status == "needs_input"
        assert [item.key for item in attribute_pause.pending_inputs] == ["85"]
        assert attribute_pause.pending_inputs[0].input_owner == (
            "provided_attributes"
        )
        assert attribute_pause.agent_execution_conversation_ids == [
            "planning-execution-vertical",
            "focused-category-vertical",
            "focused-attributes-vertical",
        ]
        assert boundary_calls == {"copy": 1, "category": 2, "attributes": 1}

        controller = _controller(
            store=LocalGlobalTaskStore(context.db),
            planner=planner,
            capabilities=capabilities,
            bus=initial_bus,
        )
        confirmation = controller.submit_input(
            GlobalTaskInputRequest(
                task_id=attribute_pause.task_id,
                inputs={"85": "Champion"},
            )
        )
        assert confirmation.status == "waiting_publish_confirmation"
        assert confirmation.publish_confirmation.status == "pending"
        assert confirmation.steps[0].status == "completed"
        assert confirmation.steps[1].status == "completed"
        assert confirmation.steps[2].status == "pending"
        assert planner.calls == 1
        assert boundary_calls == {"copy": 1, "category": 2, "attributes": 2}
        prepared_draft = context.db.load_draft_model(draft_id)
        assert prepared_draft["title"] == "Портативный вентилятор"
        assert prepared_draft["category_id"] == "94765"
        assert prepared_draft["description_category_id"] == "17027949"
        assert prepared_draft["attributes"]["85"] == "Champion"
        assert prepared_draft["attributes"]["4191"] == prepared_draft["description"]
        assert prepared_draft["images"] == [
            {"asset_id": "image-vertical-1", "role": "main", "order": 0}
        ]
        assert prepared_draft["publish_status"] == "ready"
        assert confirmation.publish_confirmation.summary["category_id"] == "94765"
        assert confirmation.publish_confirmation.summary["image_count"] == 1
        assert confirmation.agent_execution_conversation_ids == [
            "planning-execution-vertical",
            "focused-category-vertical",
            "focused-attributes-vertical",
        ]

        submitted = controller.confirm_publish(confirmation.task_id)
        confirmed_at = submitted.publish_confirmation.confirmed_at
        assert submitted.publish_confirmation.status == "confirmed"
        assert confirmed_at is not None
        assert submitted.publish_job_id

        duplicate_confirmation = controller.confirm_publish(confirmation.task_id)
        assert duplicate_confirmation.publish_job_id == submitted.publish_job_id
        assert duplicate_confirmation.publish_confirmation.confirmed_at == confirmed_at

        initial_bus.wait(submitted.publish_job_id, timeout=3)
        initial_bus.executor.shutdown(wait=True)

        # 重建 PublishingBus 后按原可信事实重放 enqueue。完整 publish Capability
        # 已在确认时真实执行；这里专门模拟提交后的进程重试，避免在平台终态后
        # 额外重做业务预检而改变终态展示字段。
        restarted_bus = PublishingBus(
            context.db,
            adapters={"ozon": network_adapter},
            config_provider=context.config.load_store_config,
            terminal_callback=lambda state: persist_publish_bus_terminal_results(
                state,
                context=context,
            ),
            max_retries=0,
            auto_resume_pending=False,
        )
        context._publishing_bus = restarted_bus
        persisted_task = LocalGlobalTaskStore(context.db).require_task(
            confirmation.task_id
        )
        persisted_job = context.db.load_publish_job(submitted.publish_job_id)
        persisted_product = persisted_job["product"]
        replay = restarted_bus.enqueue(
            persisted_product,
            ["ozon"],
            targets={
                "ozon": {
                    "draft_id": draft_id,
                    "site": "global",
                    "product_id": str(persisted_product["product_id"]),
                }
            },
            idempotency_key=persisted_task.publish_idempotency_key,
            approved_publications=persisted_job.get("approved_publications"),
        )
        assert replay["idempotent_replay"] is True
        assert replay["job_id"] == submitted.publish_job_id
        assert network_adapter.publish_calls == 1
        assert len(context.db.list_publish_jobs(limit=10)[0]) == 1

        terminal_controller = _controller(
            store=LocalGlobalTaskStore(context.db),
            planner=planner,
            capabilities=capabilities,
            bus=restarted_bus,
        )
        terminal = terminal_controller.get_state(confirmation.task_id)
        assert terminal.status == expected_task_status
        assert terminal.steps[-1].status == (
            "completed" if remote_success else "failed"
        )
        if not remote_success:
            assert terminal.error_code == "PUBLISH_PLATFORM_FAILED"
            assert terminal.error_message == "平台拒绝纵向测试商品"

        persisted_draft = context.db.load_draft_model(draft_id)
        assert persisted_draft["publish_status"] == expected_publish_status
        assert persisted_draft["last_publish_task"]["job_id"] == (
            submitted.publish_job_id
        )
        assert context.db.publish_log_exists(submitted.publish_job_id, "ozon")
    finally:
        # shutdown 可重复；这里显式释放被替换的两个线程池。
        initial_bus.executor.shutdown(wait=True)
        if restarted_bus is not None:
            restarted_bus.executor.shutdown(wait=True)
