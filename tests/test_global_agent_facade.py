from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from erp_web.db import ErpDatabase
from erp_web.facades import global_agent_facade
from erp_web.schemas.global_tasks import (
    GlobalPlanningDecision,
    GlobalTaskInputRequest,
    GlobalTaskPlanProposal,
    GlobalTaskStepProposal,
    LocalGlobalTaskState,
    LocalTaskStep,
    PublishConfirmation,
    RequiredInput,
)
from erp_web.services.global_task_controller import GlobalTaskController
from erp_web.stores.global_task_store import LocalGlobalTaskStore


NOW = datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc)


class _FailOnAccess:
    def __init__(self, name: str) -> None:
        self.name = name

    def __getattr__(self, attribute: str) -> Any:
        raise AssertionError(f"装配期间不应访问 {self.name}.{attribute}")


class _Config:
    def __init__(self) -> None:
        self.calls = 0

    def load_app_config(self) -> dict[str, Any]:
        self.calls += 1
        return {"ai": {}}


def _planning_task() -> LocalGlobalTaskState:
    return LocalGlobalTaskState(
        task_id="task-lazy-planner",
        goal="读取当前商品",
        status="planning",
        assistant_message="正在规划。",
        created_at=NOW,
        updated_at=NOW,
    )


def _running_task(*, publish_confirmed: bool = False) -> LocalGlobalTaskState:
    return LocalGlobalTaskState(
        task_id="task-capability",
        goal="处理草稿",
        status="running",
        publish_confirmation=(
            PublishConfirmation(
                status="confirmed",
                validation_digest="a" * 64,
                summary={"draft_id": "draft-1"},
                confirmed_at=NOW,
            )
            if publish_confirmed
            else PublishConfirmation()
        ),
        publish_idempotency_key=(
            "task-capability:publish" if publish_confirmed else ""
        ),
        created_at=NOW,
        updated_at=NOW,
    )


def _step(capability: str, **inputs: Any) -> LocalTaskStep:
    return LocalTaskStep(
        step_id=f"step-{capability}",
        capability=capability,
        objective="执行专项能力",
        status="running",
        operation_key=(
            "task-capability:publish"
            if capability == "product.publish.request"
            else f"task-capability:{capability}"
        ),
        inputs=inputs,
    )


def _capability_context() -> Any:
    return SimpleNamespace(
        products=object(),
        global_tasks=object(),
        publishing_bus=object(),
        paths=SimpleNamespace(app_dir=None),
        config=SimpleNamespace(load_app_config=lambda: {}),
    )


def test_controller_composition_keeps_model_config_and_publishing_bus_lazy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    config = _Config()
    global_tasks = object()
    context = SimpleNamespace(
        paths=SimpleNamespace(app_dir=tmp_path),
        config=config,
        products=object(),
        global_tasks=global_tasks,
        pydantic_messages=object(),
        publishing_bus=_FailOnAccess("publishing_bus"),
    )
    capabilities = {"product.read": object()}
    monkeypatch.setattr(
        global_agent_facade,
        "build_global_task_capabilities",
        lambda received_context: (
            capabilities
            if received_context is context
            else pytest.fail("装配使用了错误 AppContext")
        ),
    )

    controller = global_agent_facade.build_global_task_controller(context)

    assert controller.store is global_tasks
    assert controller.capabilities == capabilities
    assert config.calls == 0
    # status reader 也是惰性闭包；只装配不能解析 PublishingBus 属性。
    assert callable(controller.publish_status_reader)


def test_model_config_is_loaded_only_when_composed_planner_actually_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    config = _Config()
    context = SimpleNamespace(
        paths=SimpleNamespace(app_dir=tmp_path),
        config=config,
        products=object(),
        global_tasks=object(),
        pydantic_messages=object(),
        publishing_bus=_FailOnAccess("publishing_bus"),
    )
    capabilities = {"product.read": object()}
    services: list[Any] = []

    class FakeGlobalAgentService:
        def __init__(self, **kwargs: Any) -> None:
            services.append(SimpleNamespace(kwargs=kwargs, plan_calls=[]))

        def plan(self, **kwargs: Any) -> Any:
            services[-1].plan_calls.append(kwargs)
            return SimpleNamespace(
                decision=GlobalPlanningDecision(
                    action="plan",
                    plan=GlobalTaskPlanProposal(
                        steps=[
                            GlobalTaskStepProposal(
                                local_key="read",
                                capability="product.read",
                                objective="读取商品",
                            )
                        ]
                    ),
                ),
                finish=None,
            )

    monkeypatch.setattr(
        global_agent_facade,
        "build_global_task_capabilities",
        lambda _context: capabilities,
    )
    monkeypatch.setattr(
        global_agent_facade,
        "build_global_task_planning_toolset",
        lambda **kwargs: ("planning-toolset", kwargs),
    )
    monkeypatch.setattr(
        global_agent_facade,
        "GlobalAgentService",
        FakeGlobalAgentService,
    )
    controller = global_agent_facade.build_global_task_controller(context)
    assert config.calls == 0
    assert services == []

    outcome = controller.planner(_planning_task(), "补充：只读即可")

    assert config.calls == 1
    assert len(services) == 1
    assert services[0].kwargs["app_config"] == {"ai": {}}
    assert services[0].kwargs["message_store"] is context.pydantic_messages
    assert services[0].kwargs["allowed_capabilities"] == frozenset(
        {"product.read"}
    )
    assert services[0].plan_calls[0]["goal"] == (
        "读取当前商品\n用户补充说明：补充：只读即可"
    )
    assert outcome.decision.action == "plan"


@pytest.mark.parametrize(
    "capability_name",
    [
        "category.match",
        "product.attributes.fill",
        "draft.prepare_for_market",
    ],
)
def test_market_capability_without_platform_pauses_for_typed_input(
    capability_name: str,
) -> None:
    capability = global_agent_facade._build_base_capabilities(
        _capability_context()
    )[capability_name]

    result = capability(
        _running_task(),
        _step(capability_name, draft_id="draft-1"),
    )

    assert result.status == "needs_input"
    assert len(result.required_inputs) == 1
    required = result.required_inputs[0]
    assert required.key == "target_platform"
    assert required.input_type == "select"
    assert required.options


def test_market_capabilities_accept_target_platform_alias_without_leaking_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def capture_category(request, **_kwargs):
        captured["category"] = request
        return {"draft_id": request.draft_id}

    def capture_attributes(request, **_kwargs):
        captured["attributes"] = request
        return {"draft_id": request.draft_id}

    def capture_prepare(request, **_kwargs):
        captured["prepare"] = request
        return {"draft_id": request.draft_id}

    monkeypatch.setattr(global_agent_facade, "match_category", capture_category)
    monkeypatch.setattr(
        global_agent_facade,
        "fill_product_attributes",
        capture_attributes,
    )
    monkeypatch.setattr(
        global_agent_facade,
        "prepare_draft_for_market",
        capture_prepare,
    )
    capabilities = global_agent_facade._build_base_capabilities(
        _capability_context()
    )
    task = _running_task()

    for capability_name in (
        "category.match",
        "product.attributes.fill",
        "draft.prepare_for_market",
    ):
        result = capabilities[capability_name](
            task,
            _step(
                capability_name,
                draft_id="draft-1",
                target_platform="ozon",
                **(
                    {
                        "provided_attributes": {"COLOR": "Black"},
                        "pricing_input": {
                            "shipping_quote_mode": "manual",
                            "domestic_freight": "12.50",
                            "commission_percent": "16",
                            "target_margin_percent": "30",
                        },
                        # 非结构化顶层字段不再靠白名单猜测 owner。
                        "unowned_field": "ignored",
                    }
                    if capability_name == "draft.prepare_for_market"
                    else {}
                ),
            ),
        )
        assert result.status == "completed"

    assert captured["category"].target_platform == "ozon"
    assert captured["attributes"].target_platform == "ozon"
    assert captured["attributes"].provided_attributes == {}
    assert captured["prepare"].target_platform == "ozon"
    assert captured["prepare"].provided_attributes == {"COLOR": "Black"}
    assert captured["prepare"].pricing_input == {
        "shipping_quote_mode": "manual",
        "domestic_freight": "12.50",
        "commission_percent": "16",
        "target_margin_percent": "30",
    }


def test_pricing_required_inputs_resume_through_controller_into_prepare_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    captured: dict[str, Any] = {}

    def capture_prepare(request, **kwargs):
        captured["request"] = request
        captured["copy_operation_key"] = kwargs["copy_operation_key"]
        return {"draft_id": request.draft_id}

    monkeypatch.setattr(
        global_agent_facade,
        "prepare_draft_for_market",
        capture_prepare,
    )
    store = LocalGlobalTaskStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    task = LocalGlobalTaskState(
        task_id="task-pricing-resume",
        goal="补充核价后继续准备草稿",
        platform="mercadolibre",
        status="needs_input",
        steps=[
            LocalTaskStep(
                step_id="step-prepare-pricing",
                capability="draft.prepare_for_market",
                objective="准备目标市场草稿",
                status="needs_input",
                inputs={
                    "draft_id": "draft-1",
                    "target_platform": "mercadolibre",
                },
            )
        ],
        pending_inputs=[
            RequiredInput(
                key=key,
                label=key,
                reason=f"缺少 {key}",
                input_owner="pricing_input",
            )
            for key in (
                "shipping_quote_mode",
                "domestic_freight",
                "commission_percent",
                "target_margin_percent",
            )
        ],
        pending_input_owner="capability",
        created_at=NOW,
        updated_at=NOW,
    )
    store.create_task(task)
    capabilities = global_agent_facade._build_base_capabilities(
        _capability_context()
    )
    controller = GlobalTaskController(
        store=store,
        planner=lambda _task, _supplement: pytest.fail("不应重新规划"),
        capabilities=capabilities,
        publish_status_reader=lambda _job_id: {},
        answer_resolver=lambda _task, _decision: pytest.fail("不应解析只读答案"),
    )

    completed = controller.submit_input(
        GlobalTaskInputRequest(
            task_id=task.task_id,
            inputs={
                "shipping_quote_mode": "manual",
                "domestic_freight": "12.50",
                "commission_percent": "16",
                "target_margin_percent": "30",
            },
        )
    )

    assert completed.status == "completed"
    assert captured["request"].provided_attributes == {}
    assert captured["request"].pricing_input == {
        "shipping_quote_mode": "manual",
        "domestic_freight": "12.50",
        "commission_percent": "16",
        "target_margin_percent": "30",
    }
    assert captured["copy_operation_key"] == (
        "global-task:task-pricing-resume:step:step-prepare-pricing:copy"
    )


def test_optional_platform_capabilities_accept_target_platform_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def capture_read(request, **_kwargs):
        captured["read"] = request
        return {"product_id": request.product_id}

    def capture_update(request, **_kwargs):
        captured["update"] = request
        return {"draft_id": request.draft_id}

    def capture_validate(request, **_kwargs):
        captured["validate"] = request
        return SimpleNamespace(passed=True, errors=[])

    def capture_publish(request, **_kwargs):
        captured["publish"] = request
        return SimpleNamespace(job_id="job-1")

    monkeypatch.setattr(global_agent_facade, "read_product", capture_read)
    monkeypatch.setattr(
        global_agent_facade,
        "update_product_attributes",
        capture_update,
    )
    monkeypatch.setattr(
        global_agent_facade,
        "validate_product_publish",
        capture_validate,
    )
    monkeypatch.setattr(
        global_agent_facade,
        "request_product_publish",
        capture_publish,
    )
    capabilities = global_agent_facade._build_base_capabilities(
        _capability_context()
    )
    task = _running_task()

    assert capabilities["product.read"](
        task,
        _step(
            "product.read",
            product_id="product-1",
            target_platform="ozon",
        ),
    ).status == "completed"
    assert capabilities["product.attributes.update"](
        task,
        _step(
            "product.attributes.update",
            draft_id="draft-1",
            target_platform="ozon",
            updates={"MODEL": "F-1"},
        ),
    ).status == "completed"
    assert capabilities["product.publish.validate"](
        task,
        _step(
            "product.publish.validate",
            draft_id="draft-1",
            target_platform="ozon",
        ),
    ).status == "completed"
    assert capabilities["product.publish.request"](
        _running_task(publish_confirmed=True),
        _step(
            "product.publish.request",
            draft_id="draft-1",
            target_platform="ozon",
        ),
    ).status == "in_progress"

    assert {request.platform for request in captured.values()} == {"ozon"}


@pytest.mark.parametrize(
    ("facade_name", "valid_body"),
    [
        ("start_global_task_payload", {"goal": "读取商品"}),
        ("get_global_task_payload", {"task_id": "task-1"}),
        (
            "submit_global_task_input_payload",
            {"task_id": "task-1", "message": "补充资料"},
        ),
        ("confirm_global_task_publish_payload", {"task_id": "task-1"}),
        ("cancel_global_task_payload", {"task_id": "task-1"}),
    ],
)
def test_unknown_facade_exception_returns_redacted_http_500(
    monkeypatch: pytest.MonkeyPatch,
    facade_name: str,
    valid_body: dict[str, Any],
) -> None:
    secret_detail = "database password=do-not-leak"

    def fail_context() -> Any:
        raise RuntimeError(secret_detail)

    monkeypatch.setattr(global_agent_facade, "get_context", fail_context)

    payload, status = getattr(global_agent_facade, facade_name)(valid_body)

    assert status == 500
    assert payload == {
        "ok": False,
        "error": "全局任务处理失败，请稍后重试。",
        "error_code": "GLOBAL_TASK_REQUEST_FAILED",
    }
    assert secret_detail not in str(payload)
