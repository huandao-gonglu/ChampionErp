from __future__ import annotations

"""Workstream D 第四批：审批写 Capability（发布管理）行为测试。

覆盖 product_publish_direct / publish_real_confirm / platform_item_close 的
服务端审批快照绑定、失败映射，以及 platform_item_close 经由 Global Task
审批闸门的纵向流程（含可信审批身份、任务版本过期负向场景）。
审批摘要与参数只能由服务端快照生成，模型不提供 approval payload。
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from erp_web.context import get_context
from erp_web.facades import global_task_facade
from erp_web.runtime_units.global_ai_control_tools import (
    GlobalTaskStartControlRequest,
)
from erp_web.runtime_units.publish_admin_capabilities import (
    PLATFORM_ITEM_CLOSE_TOOL,
    PRODUCT_PUBLISH_DIRECT_TOOL,
    PUBLISH_REAL_CONFIRM_TOOL,
    PublishAdminCapabilityScope,
    _platform_item_close_approval_snapshot,
    _publish_direct_approval_snapshot,
    _publish_real_confirm_approval_snapshot,
    platform_item_close,
    product_publish_direct,
    publish_real_confirm,
)
from erp_web.schemas.ai_tools import AiToolExecutionError, TaskApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.global_tasks import (
    GlobalTaskApproveRequest,
    GlobalTaskRejectRequest,
)
from erp_web.schemas.publish_admin_capabilities import (
    PlatformItemCloseRequest,
    ProductPublishDirectRequest,
    PublishRealConfirmRequest,
)
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.global_task_controller import GlobalTaskControllerError
from erp_web.services.task_approval import approval_binding_digest


def _execution(operation_key: str = "op-1") -> AiExecutionContext:
    return AiExecutionContext(
        task_run_id="task-1",
        attempt_id="attempt-1",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        budget_profile="test",
        business_scope={"task_id": "task-1", "step_id": "step-1"},
        idempotency_context={"operation_key": operation_key},
    )


def _approved_execution(
    snapshot: TaskApprovalSnapshot,
    capability_name: str,
    *,
    operation_key: str = "op-1",
    step_id: str = "step-1",
    task_revision: int = 1,
) -> AiExecutionContext:
    """模拟 Controller 批准后注入的可信审批上下文（digest + 任务版本）。"""

    digest = approval_binding_digest(
        snapshot=snapshot,
        capability_name=capability_name,
        capability_version="1",
        operation_key=operation_key,
        step_id=step_id,
        task_revision=task_revision,
    )
    return AiExecutionContext(
        task_run_id="task-1",
        attempt_id="attempt-1",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        budget_profile="test",
        business_scope={
            "task_id": "task-1",
            "step_id": step_id,
            "approver": "local-ui:test",
        },
        idempotency_context={"operation_key": operation_key},
        approval_digest=digest,
        approval_task_revision=task_revision,
    )


def _publish_scope(**overrides: Any) -> PublishAdminCapabilityScope:
    defaults: dict[str, Any] = dict(
        direct_publisher=lambda product, platform, config: {
            "ok": True,
            "status": "real_publish_success",
            "result": {"external_id": "item-1"},
        },
        product_loader=lambda body: (
            {
                "product_id": str(body.get("product_id")),
                "title": "测试商品",
            },
            None,
            200,
        ),
        store_config_loader=lambda: {"mercadolibre": {}},
        real_publisher=lambda product, confirm: {
            "ok": True,
            "status": "real_publish_success",
            "payload_path": "/tmp/payload.json",
            "result": {"id": "MLB1"},
        },
        item_closer=lambda item_id: {
            "ok": True,
            "status": "closed",
            "message": f"{item_id} 已提交结束发布。",
        },
    )
    defaults.update(overrides)
    return PublishAdminCapabilityScope(**defaults)


def test_product_publish_direct_approval_gate_and_success() -> None:
    captured: dict[str, Any] = {}

    def publisher(product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        captured["platform"] = platform
        captured["product_id"] = product.get("product_id")
        return {
            "ok": True,
            "status": "real_publish_success",
            "result": {"external_id": "item-1"},
        }

    scope = _publish_scope(direct_publisher=publisher)
    request = ProductPublishDirectRequest(
        product_id="product-pub-1",
        platform="mercadolibre",
    )

    # 没有可信审批上下文的直接执行必须被拒绝。
    with pytest.raises(AiToolExecutionError) as missing:
        product_publish_direct(request, scope=scope, execution=_execution())
    assert missing.value.code == "TASK_APPROVAL_CONTEXT_REQUIRED"
    assert captured == {}

    # 服务端快照冻结发布目标与商品标题；审批展示与执行参数同源。
    snapshot = _publish_direct_approval_snapshot(request, scope)
    assert "product-pub-1" in snapshot.summary
    assert "测试商品" in snapshot.summary
    assert snapshot.canonical_payload["product_id"] == "product-pub-1"
    assert snapshot.canonical_payload["platform"] == "mercadolibre"

    result = product_publish_direct(
        request,
        scope=scope,
        execution=_approved_execution(snapshot, PRODUCT_PUBLISH_DIRECT_TOOL),
    )
    assert result.ok is True
    assert result.status == "real_publish_success"
    assert captured == {"platform": "mercadolibre", "product_id": "product-pub-1"}

    # 审批对应另一个商品（目标漂移）→ 稳定 stale 错误。
    stale_snapshot = _publish_direct_approval_snapshot(
        ProductPublishDirectRequest(
            product_id="product-other",
            platform="mercadolibre",
        ),
        scope,
    )
    with pytest.raises(AiToolExecutionError) as stale:
        product_publish_direct(
            request,
            scope=scope,
            execution=_approved_execution(stale_snapshot, PRODUCT_PUBLISH_DIRECT_TOOL),
        )
    assert stale.value.code == "PUBLISH_DIRECT_APPROVAL_STALE"


def test_product_publish_direct_failure_mapping() -> None:
    not_ready_scope = _publish_scope(
        direct_publisher=lambda product, platform, config: {
            "ok": False,
            "status": "not_ready",
            "error": "缺少必填属性",
        }
    )
    request = ProductPublishDirectRequest(product_id="product-pub-2")
    snapshot = _publish_direct_approval_snapshot(request, not_ready_scope)
    with pytest.raises(BusinessCapabilityError) as not_ready:
        product_publish_direct(
            request,
            scope=not_ready_scope,
            execution=_approved_execution(snapshot, PRODUCT_PUBLISH_DIRECT_TOOL),
        )
    assert not_ready.value.code == "PUBLISH_DIRECT_NOT_READY"

    missing_scope = _publish_scope(
        product_loader=lambda body: (
            {},
            {"error": "商品不存在", "error_code": "PRODUCT_NOT_FOUND"},
            404,
        )
    )
    # 快照生成阶段就要求商品存在；审批不能建立在无效目标上。
    with pytest.raises(BusinessCapabilityError) as missing:
        _publish_direct_approval_snapshot(
            ProductPublishDirectRequest(product_id="product-missing"),
            missing_scope,
        )
    assert missing.value.code == "PRODUCT_NOT_FOUND"


def test_product_publish_direct_approval_expires_when_cbt_destinations_change() -> None:
    product = {
        "product_id": "product-cbt-approval",
        "title": "CBT 测试商品",
        "drafts": {
            "mercadolibre": {
                "target_sites": [
                    {
                        "platform": "mercadolibre",
                        "site": "CBT",
                        "sites_to_sell": [
                            {"site_id": "MLM", "logistic_type": "remote"}
                        ],
                    }
                ]
            }
        },
    }
    scope = _publish_scope(
        product_loader=lambda body: (product, None, 200),
    )
    request = ProductPublishDirectRequest(
        product_id="product-cbt-approval",
        platform="mercadolibre",
    )
    snapshot = _publish_direct_approval_snapshot(request, scope)

    product["drafts"]["mercadolibre"]["target_sites"][0][
        "sites_to_sell"
    ] = [{"site_id": "MLB", "logistic_type": "remote"}]

    with pytest.raises(AiToolExecutionError) as stale:
        product_publish_direct(
            request,
            scope=scope,
            execution=_approved_execution(
                snapshot,
                PRODUCT_PUBLISH_DIRECT_TOOL,
            ),
        )

    assert stale.value.code == "PUBLISH_DIRECT_APPROVAL_STALE"


def test_product_publish_direct_snapshot_binds_config_without_exposing_secrets() -> None:
    config = {
        "mercadolibre": {
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "app_secret": "app-secret",
            "account_site_id": "CBT",
            "currency_fingerprint": "currency-v1",
            "marketplace_bindings": [
                {"site_id": "MLM", "logistic_type": "remote"}
            ],
        },
        "listing": {"listing_type_id": "gold_special"},
    }
    scope = _publish_scope(store_config_loader=lambda: config)
    request = ProductPublishDirectRequest(
        product_id="product-config-approval",
        platform="mercadolibre",
    )
    snapshot = _publish_direct_approval_snapshot(request, scope)
    serialized = str(snapshot.canonical_payload)

    assert "publish_config_fingerprint" in snapshot.canonical_payload
    assert "access-secret" not in serialized
    assert "refresh-secret" not in serialized
    assert "app-secret" not in serialized

    config["mercadolibre"]["marketplace_bindings"] = [
        {"site_id": "MLB", "logistic_type": "remote"}
    ]
    with pytest.raises(AiToolExecutionError) as stale:
        product_publish_direct(
            request,
            scope=scope,
            execution=_approved_execution(
                snapshot,
                PRODUCT_PUBLISH_DIRECT_TOOL,
            ),
        )

    assert stale.value.code == "PUBLISH_DIRECT_APPROVAL_STALE"


def test_publish_real_confirm_approval_gate_and_success() -> None:
    captured: dict[str, Any] = {}

    def real_publisher(product: dict[str, Any], confirm: bool) -> dict[str, Any]:
        captured["confirm"] = confirm
        captured["product_id"] = product.get("product_id")
        return {
            "ok": True,
            "status": "real_publish_success",
            "payload_path": "/tmp/payload.json",
            "result": {"id": "MLB1"},
        }

    scope = _publish_scope(real_publisher=real_publisher)
    request = PublishRealConfirmRequest(product_id="product-real-1")

    with pytest.raises(AiToolExecutionError) as missing:
        publish_real_confirm(request, scope=scope, execution=_execution())
    assert missing.value.code == "TASK_APPROVAL_CONTEXT_REQUIRED"

    snapshot = _publish_real_confirm_approval_snapshot(request, scope)
    result = publish_real_confirm(
        request,
        scope=scope,
        execution=_approved_execution(snapshot, PUBLISH_REAL_CONFIRM_TOOL),
    )
    assert result.ok is True
    assert result.payload_path == "/tmp/payload.json"
    assert captured == {"confirm": True, "product_id": "product-real-1"}

    failed_scope = _publish_scope(
        real_publisher=lambda product, confirm: {
            "ok": False,
            "status": "real_publish_failed",
            "error": "平台拒绝",
        }
    )
    failed_snapshot = _publish_real_confirm_approval_snapshot(request, failed_scope)
    with pytest.raises(BusinessCapabilityError) as failed:
        publish_real_confirm(
            request,
            scope=failed_scope,
            execution=_approved_execution(failed_snapshot, PUBLISH_REAL_CONFIRM_TOOL),
        )
    assert failed.value.code == "PUBLISH_REAL_CONFIRM_FAILED"

    stale_snapshot = _publish_real_confirm_approval_snapshot(
        PublishRealConfirmRequest(product_id="product-other"),
        scope,
    )
    with pytest.raises(AiToolExecutionError) as stale:
        publish_real_confirm(
            request,
            scope=scope,
            execution=_approved_execution(stale_snapshot, PUBLISH_REAL_CONFIRM_TOOL),
        )
    assert stale.value.code == "PUBLISH_REAL_CONFIRM_APPROVAL_STALE"


def test_platform_item_close_approval_gate_and_success() -> None:
    captured: dict[str, Any] = {}

    def closer(item_id: str) -> dict[str, Any]:
        captured["item_id"] = item_id
        return {"ok": True, "status": "closed", "message": f"{item_id} 已关闭"}

    scope = _publish_scope(item_closer=closer)
    request = PlatformItemCloseRequest(item_id="MLB123")

    with pytest.raises(AiToolExecutionError) as missing:
        platform_item_close(request, scope=scope, execution=_execution())
    assert missing.value.code == "TASK_APPROVAL_CONTEXT_REQUIRED"
    assert captured == {}

    snapshot = _platform_item_close_approval_snapshot(request, scope)
    assert snapshot.canonical_payload["item_id"] == "MLB123"
    result = platform_item_close(
        request,
        scope=scope,
        execution=_approved_execution(snapshot, PLATFORM_ITEM_CLOSE_TOOL),
    )
    assert result.ok is True
    assert result.status == "closed"
    assert captured == {"item_id": "MLB123"}

    stale_snapshot = _platform_item_close_approval_snapshot(
        PlatformItemCloseRequest(item_id="MLB999"),
        scope,
    )
    with pytest.raises(AiToolExecutionError) as stale:
        platform_item_close(
            request,
            scope=scope,
            execution=_approved_execution(stale_snapshot, PLATFORM_ITEM_CLOSE_TOOL),
        )
    assert stale.value.code == "ITEM_CLOSE_APPROVAL_STALE"

    unsupported_scope = _publish_scope()
    with pytest.raises(BusinessCapabilityError) as unsupported:
        platform_item_close(
            PlatformItemCloseRequest(platform="ozon", item_id="123"),
            scope=unsupported_scope,
            execution=_execution(),
        )
    assert unsupported.value.code == "PLATFORM_ITEM_CLOSE_UNSUPPORTED"

    failing_scope = _publish_scope(
        item_closer=lambda item_id: {
            "ok": False,
            "error": "缺少 Mercado Libre item id",
            "error_code": "ITEM_ID_MISSING",
        }
    )
    failing_snapshot = _platform_item_close_approval_snapshot(request, failing_scope)
    with pytest.raises(BusinessCapabilityError) as failed:
        platform_item_close(
            request,
            scope=failing_scope,
            execution=_approved_execution(failing_snapshot, PLATFORM_ITEM_CLOSE_TOOL),
        )
    assert failed.value.code == "ITEM_ID_MISSING"


def test_platform_item_close_post_dispatch_error_is_outcome_unknown() -> None:
    """关闭请求发出后抛错（含超时）：必须结果未知且禁止自动重试。"""

    request = PlatformItemCloseRequest(item_id="MLB999")

    def broken_closer(item_id: str) -> dict[str, Any]:
        raise TimeoutError("平台接口超时")

    broken_scope = _publish_scope(item_closer=broken_closer)
    snapshot = _platform_item_close_approval_snapshot(request, broken_scope)
    with pytest.raises(BusinessCapabilityError) as outcome:
        platform_item_close(
            request,
            scope=broken_scope,
            execution=_approved_execution(snapshot, PLATFORM_ITEM_CLOSE_TOOL),
        )
    assert outcome.value.code == "ITEM_CLOSE_OUTCOME_UNKNOWN"
    assert outcome.value.retryable is False
    assert outcome.value.details == {"outcome_unknown": True}


def _accept_and_run(controller, request, *, conversation_id: str, suffix: str):
    """Deferred 生命周期：受理 → 首次 history 提交 → worker 推进。"""

    from pydantic_ai.messages import ModelRequest, UserPromptPart

    context = get_context()
    acceptance = controller.accept_deferred_task(
        request,
        conversation_id=conversation_id,
        request_run_id=f"run-{suffix}",
        tool_call_id=f"call-{suffix}",
        message_id=f"message-{suffix}",
    )
    context.deferred_task_links.commit_initial_deferred_history(
        conversation_id,
        [ModelRequest(parts=[UserPromptPart("创建任务")])],
        link_id=acceptance.link_id,
        request_run_id=f"run-{suffix}",
        encoded_chunks=[],
    )
    return controller.resume_task(acceptance.task_id)


def test_platform_item_close_through_global_task_approval_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = get_context()
    closed: list[str] = []
    monkeypatch.setattr(
        global_task_facade,
        "mercadolibre_close_remote_item",
        lambda item_id: (
            closed.append(item_id)
            or {"ok": True, "status": "closed", "message": f"{item_id} 已关闭"}
        ),
    )
    controller = global_task_facade.build_global_task_controller(context)
    conversation_id = "conversation_global_chat_" + "4" * 32

    task = _accept_and_run(
        controller,
        GlobalTaskStartControlRequest.model_validate(
            {
                "goal": "下架远端商品",
                "platform": "mercadolibre",
                "steps": [
                    {
                        "capability_name": "platform_item_close",
                        "arguments": {
                            "platform": "mercadolibre",
                            "item_id": "MLB456",
                        },
                    }
                ],
            }
        ),
        conversation_id=conversation_id,
        suffix="d4-1",
    )
    assert task.status == "pending_approval"
    approval = task.pending_approval
    assert approval is not None
    assert approval.capability_name == "platform_item_close"
    # 审批 payload 是服务端快照：摘要与冻结参数，模型没有提供。
    assert "MLB456" in str(approval.payload.get("summary"))
    assert approval.payload["canonical_payload"]["item_id"] == "MLB456"
    assert closed == []

    # 批准只改变业务状态；执行由 worker 领取。
    approved = controller.approve_task(
        GlobalTaskApproveRequest(task_id=task.task_id),
        approver="local-ui:test",
        conversation_id=conversation_id,
        message_id="message-d4-2",
    ).task
    assert approved.status == "running"
    approved = controller.resume_task(task.task_id)
    assert approved.status == "completed"
    record = approved.steps[0].approval
    assert record is not None
    assert record.approver == "local-ui:test"
    assert record.digest == approval.digest
    step_result = approved.steps[0].result
    assert step_result is not None
    assert step_result["ok"] is True
    assert step_result["status"] == "closed"
    assert closed == ["MLB456"]


def test_platform_item_close_stale_task_revision_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """审批创建后任务被修改（版本前进）→ 原批准过期，不得执行。"""

    context = get_context()
    closed: list[str] = []
    monkeypatch.setattr(
        global_task_facade,
        "mercadolibre_close_remote_item",
        lambda item_id: (
            closed.append(item_id)
            or {"ok": True, "status": "closed", "message": "closed"}
        ),
    )
    controller = global_task_facade.build_global_task_controller(context)
    conversation_id = "conversation_global_chat_" + "5" * 32

    task = _accept_and_run(
        controller,
        GlobalTaskStartControlRequest.model_validate(
            {
                "goal": "下架远端商品",
                "platform": "mercadolibre",
                "steps": [
                    {
                        "capability_name": "platform_item_close",
                        "arguments": {
                            "platform": "mercadolibre",
                            "item_id": "MLB456",
                        },
                    }
                ],
            }
        ),
        conversation_id=conversation_id,
        suffix="d4-3",
    )
    assert task.status == "pending_approval"

    # 模拟审批创建后任务又被别的写入修改：步骤参数漂移且 revision 前进。
    steps = list(task.steps)
    steps[0] = steps[0].model_copy(
        update={
            "arguments": {"platform": "mercadolibre", "item_id": "MLB789"},
        }
    )
    context.global_tasks.save_task(task.model_copy(update={"steps": steps}))

    with pytest.raises(GlobalTaskControllerError) as stale:
        controller.approve_task(
            GlobalTaskApproveRequest(task_id=task.task_id),
            approver="local-ui:test",
        )
    assert stale.value.code == "GLOBAL_TASK_APPROVAL_REVISION_STALE"
    assert closed == []


def test_platform_item_close_reject_records_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = get_context()
    closed: list[str] = []
    monkeypatch.setattr(
        global_task_facade,
        "mercadolibre_close_remote_item",
        lambda item_id: (
            closed.append(item_id)
            or {"ok": True, "status": "closed", "message": "closed"}
        ),
    )
    controller = global_task_facade.build_global_task_controller(context)
    conversation_id = "conversation_global_chat_" + "6" * 32

    task = _accept_and_run(
        controller,
        GlobalTaskStartControlRequest.model_validate(
            {
                "goal": "下架远端商品",
                "platform": "mercadolibre",
                "steps": [
                    {
                        "capability_name": "platform_item_close",
                        "arguments": {
                            "platform": "mercadolibre",
                            "item_id": "MLB456",
                        },
                    }
                ],
            }
        ),
        conversation_id=conversation_id,
        suffix="d4-5",
    )
    assert task.status == "pending_approval"

    rejected = controller.reject_task(
        GlobalTaskRejectRequest(task_id=task.task_id, reason="不允许下架"),
        approver="local-ui:test",
    ).task
    assert rejected.status == "failed"
    record = rejected.steps[0].approval
    assert record is not None
    assert record.approver == "local-ui:test"
    assert record.decision == "rejected"
    assert record.reason == "不允许下架"
    assert closed == []
