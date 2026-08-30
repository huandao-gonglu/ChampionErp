from __future__ import annotations

"""Workstream D 第二批：本地写 Capability 行为测试。

覆盖商品/草稿保存读取、删除审批 digest、Global Task 审批闸门纵向流程、
文案生成（单个/批量）、图片提示词、文本翻译与图片池维护。
AI 与网络边界以可信替代注入，持久化走隔离 AppContext 的真实存储。
"""

import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from erp_web.context import get_context
from erp_web.facades import global_task_facade
from erp_web.runtime_units.content_capabilities import (
    ContentCapabilityScope,
    copy_generate,
    copy_generate_batch,
    image_prompts_generate,
    text_translate,
)
from erp_web.runtime_units.global_ai_control_tools import (
    GlobalTaskStartControlRequest,
)
from erp_web.runtime_units.image_capabilities import (
    ImageCapabilityScope,
    image_edit,
    image_pool_action,
    image_pool_save,
    image_pool_sync_generated,
    image_pool_upload,
)
from erp_web.runtime_units.product_write_capabilities import (
    DRAFT_DELETE_TOOL,
    PRODUCT_DELETE_TOOL,
    ProductWriteCapabilityScope,
    _draft_delete_approval_snapshot,
    _product_delete_approval_snapshot,
    draft_delete,
    draft_pricing_apply,
    draft_read,
    draft_save,
    draft_stock_update,
    product_delete,
    product_profile_patch,
    product_save,
)
from erp_web.runtime_units.text_translation import TranslationRequestError
from erp_web.schemas.ai_tools import AiToolExecutionError, TaskApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.content_capabilities import (
    CopyGenerateBatchRequest,
    CopyGenerateRequest,
    ImagePromptsGenerateRequest,
    TextTranslateRequest,
)
from erp_web.schemas.global_tasks import GlobalTaskApproveRequest
from erp_web.schemas.image_capabilities import (
    ImageEditRequest,
    ImagePoolActionRequest,
    ImagePoolSaveRequest,
    ImagePoolSyncGeneratedRequest,
    ImagePoolUploadRequest,
)
from erp_web.schemas.product_write_capabilities import (
    DraftDeleteRequest,
    DraftPricingApplyRequest,
    DraftReadRequest,
    DraftSaveRequest,
    DraftStockUpdateRequest,
    ProductDeleteRequest,
    ProductProfilePatchRequest,
    ProductSaveRequest,
)
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.capability_input_provenance import encode_user_input_keys
from erp_web.services.global_task_controller import GlobalTaskControllerError
from erp_web.services.task_approval import approval_binding_digest


_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


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


def _seed_product(product_id: str, *, with_draft: bool = True) -> dict[str, Any]:
    context = get_context()
    payload: dict[str, Any] = {
        "product_id": product_id,
        "name": "Portable fan",
        "brand": "Champion",
        "sku": f"{product_id}-sku",
        "source": {
            "title": "Portable fan",
            "description": "Source description",
            "source_platform": "1688",
            "source_url": f"https://example.com/{product_id}",
            "image_pool": [
                {
                    "id": f"{product_id}-image-1",
                    "url": f"https://cdn.example.com/{product_id}.jpg",
                    "origin": "source",
                    "status": "ready",
                    "selected": True,
                    "is_main": True,
                    "order": 0,
                    "platforms": ["mercadolibre"],
                }
            ],
        },
        "selling_points": ["Ligero", "Silencioso"],
    }
    if with_draft:
        payload["drafts"] = {
            "mercadolibre": {
                "enabled": True,
                "platform": "mercadolibre",
                "platforms": ["mercadolibre"],
                "site": "CBT",
                "language": "en-US",
                "listing_currency": "USD",
                "target_sites": [
                    {
                        "platform": "mercadolibre",
                        "site": "CBT",
                        "language": "en-US",
                        "listing_currency": "USD",
                        "sites_to_sell": [
                            {"site_id": "MLM", "logistic_type": "remote"}
                        ],
                    }
                ],
                "title": "Ventilador portátil",
                "description": "Descripción original",
            }
        }
    return context.products.save_product(payload)


def _write_scope() -> ProductWriteCapabilityScope:
    return ProductWriteCapabilityScope(products=get_context().products)


def _content_scope() -> ContentCapabilityScope:
    context = get_context()
    return ContentCapabilityScope(
        products=context.products,
        app_config_loader=context.config.load_app_config,
    )


def _image_scope() -> ImageCapabilityScope:
    return ImageCapabilityScope(context=get_context())


# ---------------------------------------------------------------- 商品/草稿


def test_product_save_updates_product_profile() -> None:
    context = get_context()
    _seed_product("product-save-1")
    scope = _write_scope()

    saved = product_save(
        ProductSaveRequest(
            product={
                "product_id": "product-save-1",
                "name": "Portable fan Pro",
                "brand": "Champion",
            }
        ),
        scope=scope,
        execution=_execution(),
    )
    # 写回执是有界 mutation receipt，不再携带完整商品对象。
    assert saved.product_id == "product-save-1"
    assert saved.changed_fields == ("brand", "name")
    assert saved.changed is True
    reloaded = context.products.load_product_from_index("product-save-1", "")
    assert str(reloaded.get("name")) == "Portable fan Pro"

    facts = product_save(
        ProductSaveRequest(
            product={
                "product_id": "product-save-1",
                "dimensions": "30x20x10cm",
                "weight_kg": "0.8",
            }
        ),
        scope=scope,
        execution=_execution("op-facts"),
    )
    assert facts.product_id == "product-save-1"
    assert facts.changed_fields == ("dimensions", "weight_kg")
    # 部分补丁不得覆盖未提供字段（name/brand 保持上一次写入的值）。
    reloaded = context.products.load_product_from_index("product-save-1", "")
    assert str(reloaded.get("dimensions")) == "30x20x10cm"
    assert str(reloaded.get("weight_kg")) == "0.8"
    assert str(reloaded.get("name")) == "Portable fan Pro"
    assert str(reloaded.get("brand")) == "Champion"
    source = dict(reloaded.get("source") or {})
    assert str(source.get("source_url")).endswith("product-save-1")


def test_draft_read_save_roundtrip_and_missing_draft() -> None:
    context = get_context()
    saved_product = _seed_product("product-draft-1")
    draft_id = str(saved_product["drafts"]["mercadolibre"]["draft_id"])
    scope = _write_scope()

    read = draft_read(DraftReadRequest(draft_id=draft_id), scope=scope)
    assert dict(read.draft)["draft_id"] == draft_id
    assert dict(read.draft)["platform"] == "mercadolibre"
    assert dict(read.product_context).get("product_id") == "product-draft-1"

    updated = draft_save(
        DraftSaveRequest(
            draft={
                "draft_id": draft_id,
                "title": "Ventilador Pro Max",
                "platform": "mercadolibre",
            }
        ),
        scope=scope,
        execution=_execution(),
    )
    # 写回执是有界 mutation receipt，不再携带完整 draft/product_context。
    assert updated.draft_id == draft_id
    assert updated.product_id == "product-draft-1"
    assert updated.platform == "mercadolibre"
    assert updated.changed_fields == ("platform", "title")
    assert updated.changed is True
    persisted = context.db.load_draft_model(draft_id)
    assert persisted["title"] == "Ventilador Pro Max"

    with pytest.raises(BusinessCapabilityError) as missing:
        draft_read(DraftReadRequest(draft_id="draft-missing"), scope=scope)
    assert missing.value.code == "DRAFT_NOT_FOUND"

    with pytest.raises(BusinessCapabilityError) as missing_save:
        draft_save(
            DraftSaveRequest(draft={"draft_id": "draft-missing", "title": "x"}),
            scope=scope,
            execution=_execution(),
        )
    assert missing_save.value.code == "DRAFT_NOT_FOUND"


def test_draft_read_omits_unbounded_raw_product_context() -> None:
    context = get_context()
    saved_product = _seed_product("product-draft-large-context")
    draft_id = str(saved_product["drafts"]["mercadolibre"]["draft_id"])
    product = context.products.load_product_from_index(
        "product-draft-large-context",
        "",
    )
    product["source"]["description"] = "x" * 300_000
    context.products.save_product(product)

    read = draft_read(DraftReadRequest(draft_id=draft_id), scope=_write_scope())
    serialized = json.dumps(
        read.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    assert "raw" not in read.product_context
    assert read.product_context["product_id"] == "product-draft-large-context"
    assert len(serialized) < 262_144


def _receipt_bytes(result: Any) -> int:
    return len(
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def test_write_receipts_are_bounded_and_exclude_full_aggregates() -> None:
    """大商品/大草稿写入后，回执必须小于 8 KiB 且不含完整聚合对象。"""

    context = get_context()
    saved_product = _seed_product("product-receipt-large")
    draft_id = str(saved_product["drafts"]["mercadolibre"]["draft_id"])
    scope = _write_scope()

    # 把商品和草稿膨胀到远超旧 64 KiB 上限的真实规模。
    product = context.products.load_product_from_index("product-receipt-large", "")
    product["description"] = "长描述" * 60_000
    product["source"]["description"] = "来源长描述" * 60_000
    context.products.save_product(product)
    big_draft = context.db.load_draft_model(draft_id)
    big_draft["description"] = "草稿长描述" * 60_000
    context.db.upsert_draft_model(
        str(big_draft.get("product_id") or ""),
        str(big_draft.get("platform") or ""),
        big_draft,
    )

    product_receipt = product_save(
        ProductSaveRequest(
            product={"product_id": "product-receipt-large", "stock": "200"}
        ),
        scope=scope,
        execution=_execution("op-receipt-product"),
    )
    dumped_product_receipt = product_receipt.model_dump(mode="json")
    assert set(dumped_product_receipt) == {
        "product_id",
        "changed_fields",
        "updated_at",
        "changed",
    }
    assert "description" not in json.dumps(dumped_product_receipt)
    assert _receipt_bytes(product_receipt) < 8 * 1024

    draft_receipt = draft_save(
        DraftSaveRequest(
            draft={"draft_id": draft_id, "stock": "10", "platform": "mercadolibre"}
        ),
        scope=scope,
        execution=_execution("op-receipt-draft"),
    )
    dumped_draft_receipt = draft_receipt.model_dump(mode="json")
    assert set(dumped_draft_receipt) == {
        "draft_id",
        "product_id",
        "platform",
        "changed_fields",
        "updated_at",
        "changed",
    }
    assert "product_context" not in dumped_draft_receipt
    assert "raw" not in json.dumps(dumped_draft_receipt, ensure_ascii=False)
    assert _receipt_bytes(draft_receipt) < 8 * 1024

    # 写入确实生效（回执紧凑不等于丢数据）。
    reloaded = context.products.load_product_from_index("product-receipt-large", "")
    assert str(reloaded.get("stock")) == "200"
    assert str(context.db.load_draft_model(draft_id).get("stock")) == "10"


def test_draft_stock_update_owns_publish_stock() -> None:
    """库存 focused write：只改平台草稿库存，不触碰商品主档库存。"""

    context = get_context()
    saved_product = _seed_product("product-stock-focused")
    draft_id = str(saved_product["drafts"]["mercadolibre"]["draft_id"])
    scope = _write_scope()

    result = draft_stock_update(
        DraftStockUpdateRequest(draft_id=draft_id, stock="10"),
        scope=scope,
        execution=_execution("op-stock-focused"),
    )
    assert result.draft_id == draft_id
    assert result.stock == "10"
    assert result.changed is True
    assert _receipt_bytes(result) < 8 * 1024

    # 草稿库存已更新；商品主档库存不受影响（owner 分离）。
    assert str(context.db.load_draft_model(draft_id).get("stock")) == "10"
    reloaded = context.products.load_product_from_index("product-stock-focused", "")
    assert str(reloaded.get("stock")) != "10"

    with pytest.raises(BusinessCapabilityError) as missing:
        draft_stock_update(
            DraftStockUpdateRequest(draft_id="draft-missing", stock="5"),
            scope=scope,
            execution=_execution("op-stock-missing"),
        )
    assert missing.value.code == "DRAFT_NOT_FOUND"

    with pytest.raises(Exception):
        DraftStockUpdateRequest(draft_id=draft_id, stock="not-a-number")


def test_product_profile_patch_is_partial() -> None:
    """focused 部分补丁：未提供字段保持原值，回执有界。"""

    context = get_context()
    _seed_product("product-patch-focused", with_draft=False)
    scope = _write_scope()

    result = product_profile_patch(
        ProductProfilePatchRequest(
            product={"product_id": "product-patch-focused", "stock": "200"}
        ),
        scope=scope,
        execution=_execution("op-patch-focused"),
    )
    assert result.product_id == "product-patch-focused"
    assert result.changed_fields == ("stock",)
    assert result.changed is True

    reloaded = context.products.load_product_from_index("product-patch-focused", "")
    assert str(reloaded.get("stock")) == "200"
    # 未提供字段保持原值（seed 的 name/brand 不被清空）。
    assert str(reloaded.get("name")) == "Portable fan"
    assert str(reloaded.get("brand")) == "Champion"


def test_draft_pricing_apply_requires_existing_draft() -> None:
    scope = _write_scope()
    with pytest.raises(BusinessCapabilityError) as missing:
        draft_pricing_apply(
            DraftPricingApplyRequest(
                draft_id="draft-missing",
                pricing_input={"target": {"manual_price": "1"}},
            ),
            scope=scope,
            execution=_execution("op-pricing-missing"),
        )
    assert missing.value.code == "DRAFT_NOT_FOUND"


def test_draft_pricing_apply_only_accepts_user_submitted_sales_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型初始计划中的选择被忽略，Controller 标记的用户选择才可生效。"""

    saved_product = _seed_product("product-pricing-sales-target")
    draft_id = str(saved_product["drafts"]["mercadolibre"]["draft_id"])
    received: list[list[str]] = []

    def fake_prepare_target_pricing(**kwargs: Any) -> dict[str, Any]:
        received.append(list(kwargs.get("sales_target") or []))
        return {
            "target_key": "mercadolibre:cbt",
            "applied_price": {"amount": "100", "currency": "USD"},
            "calculation_fingerprint": "fingerprint-1",
        }

    monkeypatch.setattr(
        "erp_web.runtime_units.product_write_capabilities.prepare_target_pricing",
        fake_prepare_target_pricing,
    )
    request = DraftPricingApplyRequest(
        draft_id=draft_id,
        target_platform="mercadolibre",
        site="CBT",
        sales_target=["MLM:remote", "MLB:remote"],
        pricing_input={"common": {"purchase_cost": "100"}},
    )

    draft_pricing_apply(
        request,
        scope=_write_scope(),
        execution=_execution("op-pricing-model-target"),
    )
    trusted_execution = AiExecutionContext(
        task_run_id="task-1",
        attempt_id="attempt-1",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        budget_profile="test",
        business_scope={
            "task_id": "task-1",
            "step_id": "step-1",
            "user_input_keys": encode_user_input_keys(["sales_target"]),
        },
        idempotency_context={"operation_key": "op-pricing-user-target"},
    )
    draft_pricing_apply(
        request,
        scope=_write_scope(),
        execution=trusted_execution,
    )

    assert received == [[], ["MLM:remote", "MLB:remote"]]


def test_draft_pricing_apply_sales_target_rejects_legacy_scalar_selector() -> None:
    with pytest.raises(ValueError):
        DraftPricingApplyRequest(
            draft_id="draft-pricing",
            sales_target="MLM:remote",  # type: ignore[arg-type]
            pricing_input={"common": {"purchase_cost": "100"}},
        )


def test_product_delete_requires_trusted_approval_context() -> None:
    context = get_context()
    _seed_product("product-del-1", with_draft=False)
    _seed_product("product-del-2", with_draft=False)
    scope = _write_scope()
    product_ids = ("product-del-1", "product-del-2")
    request = ProductDeleteRequest(product_ids=product_ids)

    # 没有可信审批上下文的直接执行必须被拒绝（模型自批不可能成功）。
    with pytest.raises(AiToolExecutionError) as missing:
        product_delete(request, scope=scope, execution=_execution())
    assert missing.value.code == "TASK_APPROVAL_CONTEXT_REQUIRED"
    assert len(context.products.load_products_index()) == 2

    # 审批 digest 与当前参数不一致（目标漂移）→ 稳定 stale 错误。
    stale_snapshot = _product_delete_approval_snapshot(
        ProductDeleteRequest(product_ids=("product-del-1",)),
        scope,
    )
    with pytest.raises(AiToolExecutionError) as stale:
        product_delete(
            request,
            scope=scope,
            execution=_approved_execution(stale_snapshot, PRODUCT_DELETE_TOOL),
        )
    assert stale.value.code == "PRODUCT_DELETE_APPROVAL_STALE"
    assert len(context.products.load_products_index()) == 2

    # 审批 digest 匹配冻结参数后才真正执行删除。
    snapshot = _product_delete_approval_snapshot(request, scope)
    result = product_delete(
        request,
        scope=scope,
        execution=_approved_execution(snapshot, PRODUCT_DELETE_TOOL),
    )
    assert result.deleted == 2
    assert set(result.deleted_ids) == set(product_ids)
    assert result.missing_ids == ()
    assert context.products.load_products_index() == []


def test_product_delete_rejects_approval_when_target_disappeared() -> None:
    """批准后、执行前资源消失时，旧审批必须失效而非成功删除 0 个。"""

    context = get_context()
    _seed_product("product-stale-approval", with_draft=False)
    scope = _write_scope()
    request = ProductDeleteRequest(product_ids=("product-stale-approval",))
    approved_snapshot = _product_delete_approval_snapshot(request, scope)

    # 模拟另一个任务先完成了同一商品的删除。
    context.products.delete_products_from_index(["product-stale-approval"])

    with pytest.raises(AiToolExecutionError) as stale:
        product_delete(
            request,
            scope=scope,
            execution=_approved_execution(
                approved_snapshot,
                PRODUCT_DELETE_TOOL,
            ),
        )

    assert stale.value.code == "PRODUCT_DELETE_APPROVAL_STALE"


def test_draft_delete_approval_flow() -> None:
    context = get_context()
    saved_product = _seed_product("product-draft-del")
    draft_id = str(saved_product["drafts"]["mercadolibre"]["draft_id"])
    scope = _write_scope()
    request = DraftDeleteRequest(draft_ids=(draft_id,))

    with pytest.raises(AiToolExecutionError) as missing:
        draft_delete(request, scope=scope, execution=_execution())
    assert missing.value.code == "TASK_APPROVAL_CONTEXT_REQUIRED"
    assert context.db.load_draft_model(draft_id)

    stale_snapshot = _draft_delete_approval_snapshot(
        DraftDeleteRequest(draft_ids=("other-draft",)),
        scope,
    )
    with pytest.raises(AiToolExecutionError) as stale:
        draft_delete(
            request,
            scope=scope,
            execution=_approved_execution(stale_snapshot, DRAFT_DELETE_TOOL),
        )
    assert stale.value.code == "DRAFT_DELETE_APPROVAL_STALE"
    assert context.db.load_draft_model(draft_id)

    snapshot = _draft_delete_approval_snapshot(request, scope)
    result = draft_delete(
        request,
        scope=scope,
        execution=_approved_execution(snapshot, DRAFT_DELETE_TOOL),
    )
    assert result.deleted == 1
    assert result.deleted_ids == (draft_id,)
    assert result.affected_product_ids == ("product-draft-del",)
    assert not context.db.load_draft_model(draft_id)


def test_product_delete_through_global_task_approval_gate() -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    context = get_context()
    _seed_product("product-task-del", with_draft=False)
    controller = global_task_facade.build_global_task_controller(context)
    links = context.deferred_task_links
    target_ids = ["product-task-del"]
    conversation_id = "conversation_global_chat_" + "d" * 32

    # 模型只提供业务参数；审批摘要与 payload 由服务端快照生成。
    # Deferred 生命周期：受理 → 首次 history 提交 → worker 推进。
    acceptance = controller.accept_deferred_task(
        GlobalTaskStartControlRequest.model_validate(
            {
                "goal": "清理测试商品",
                "product_id": "product-task-del",
                "platform": "mercadolibre",
                "steps": [
                    {
                        "capability_name": "product_delete",
                        "arguments": {"product_ids": target_ids},
                    }
                ],
            }
        ),
        conversation_id=conversation_id,
        request_run_id="run-d2-1",
        tool_call_id="call-d2-1",
        message_id="message-d2-1",
    )
    links.commit_initial_deferred_history(
        conversation_id,
        [ModelRequest(parts=[UserPromptPart("创建任务")])],
        link_id=acceptance.link_id,
        request_run_id="run-d2-1",
        encoded_chunks=[],
    )
    task = controller.resume_task(acceptance.task_id)
    assert task.status == "pending_approval"
    approval = task.pending_approval
    assert approval is not None
    assert approval.capability_name == "product_delete"
    payload = approval.payload
    assert str(payload.get("summary"))  # 服务端生成摘要，模型不能提供
    assert payload["canonical_payload"]["product_ids"] == target_ids
    assert len(context.products.load_products_index()) == 1

    # 缺少可信审批身份的批准必须被拒绝。
    with pytest.raises(GlobalTaskControllerError) as no_identity:
        controller.approve_task(
            GlobalTaskApproveRequest(task_id=task.task_id),
            approver="",
        )
    assert no_identity.value.code == "GLOBAL_TASK_APPROVAL_IDENTITY_REQUIRED"
    assert len(context.products.load_products_index()) == 1

    # 批准只改变业务状态；执行由 worker 领取。
    approved = controller.approve_task(
        GlobalTaskApproveRequest(task_id=task.task_id),
        approver="local-ui:test",
        conversation_id=conversation_id,
        message_id="message-d2-2",
    ).task
    assert approved.status == "running"
    approved = controller.resume_task(task.task_id)
    assert approved.status == "completed"
    record = approved.steps[0].approval
    assert record is not None
    assert record.approver == "local-ui:test"
    assert record.decision == "approved"
    assert record.digest == approval.digest
    assert record.task_revision == approval.task_revision
    step_result = approved.steps[0].result
    assert step_result is not None
    assert step_result["deleted"] == 1
    assert step_result["deleted_ids"] == target_ids
    assert context.products.load_products_index() == []

    # 重复批准：任务已终结，不再是待审批状态。
    with pytest.raises(GlobalTaskControllerError) as repeat:
        controller.approve_task(
            GlobalTaskApproveRequest(task_id=task.task_id),
            approver="local-ui:test",
        )
    assert repeat.value.code == "GLOBAL_TASK_APPROVAL_NOT_EXPECTED"


# ---------------------------------------------------------------- 文案 / 翻译


def _fake_copy_bundle(title: str = "Ventilador generado") -> Any:
    def bundle(
        product: dict[str, Any],
        source_platform: str,
        target_market: str,
        language: str,
        mode: str,
        app_cfg: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "copy": {
                "title": title,
                "description": "Descripción generada",
                "bullets": ["Aire fresco"],
            },
            "language": language,
            "source_platform": source_platform,
            "mode": mode,
            "target_market": target_market,
        }

    return bundle


def test_copy_generate_saves_draft_and_maps_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = get_context()
    _seed_product("product-copy-1")
    scope = _content_scope()
    monkeypatch.setattr(
        "erp_web.runtime_units.content_capabilities.generate_ai_copy_bundle",
        _fake_copy_bundle(),
    )

    result = copy_generate(
        CopyGenerateRequest(product_id="product-copy-1", platform="mercadolibre"),
        scope=scope,
        execution=_execution(),
    )
    assert result.product_id == "product-copy-1"
    assert result.platform == "mercadolibre"
    assert result.copy_record["title"] == "Ventilador generado"
    assert result.copy_record["mode"] == "rewrite"
    assert result.listing.get("title") == "Ventilador generado"

    reloaded = context.products.load_product_from_index("product-copy-1", "")
    draft = dict((reloaded.get("drafts") or {}).get("mercadolibre") or {})
    assert draft.get("title") == "Ventilador generado"
    assert draft.get("copy_source") == "ai"

    with pytest.raises(BusinessCapabilityError) as unsupported:
        copy_generate(
            CopyGenerateRequest(product_id="product-copy-1", platform="shopee"),
            scope=scope,
            execution=_execution(),
        )
    assert unsupported.value.code == "COPY_PLATFORM_UNSUPPORTED"

    def failing_bundle(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "AI 服务不可用"}

    monkeypatch.setattr(
        "erp_web.runtime_units.content_capabilities.generate_ai_copy_bundle",
        failing_bundle,
    )
    with pytest.raises(BusinessCapabilityError) as failed:
        copy_generate(
            CopyGenerateRequest(product_id="product-copy-1", platform="mercadolibre"),
            scope=scope,
            execution=_execution(),
        )
    assert failed.value.code == "COPY_GENERATE_FAILED"


def test_copy_generate_for_draft_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = get_context()
    saved_product = _seed_product("product-copy-draft")
    draft_id = str(saved_product["drafts"]["mercadolibre"]["draft_id"])
    scope = _content_scope()
    monkeypatch.setattr(
        "erp_web.runtime_units.content_capabilities.generate_ai_copy_bundle",
        _fake_copy_bundle("Título desde borrador"),
    )

    result = copy_generate(
        CopyGenerateRequest(draft_id=draft_id, platform="mercadolibre"),
        scope=scope,
        execution=_execution(),
    )
    assert result.draft_id == draft_id
    assert result.product_id == "product-copy-draft"

    with pytest.raises(BusinessCapabilityError) as mismatch:
        copy_generate(
            CopyGenerateRequest(
                draft_id=draft_id,
                product_id="product-other",
                platform="mercadolibre",
            ),
            scope=scope,
            execution=_execution(),
        )
    assert mismatch.value.code == "DRAFT_PRODUCT_MISMATCH"


def test_copy_generate_batch_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = get_context()
    _seed_product("product-batch-1", with_draft=False)
    _seed_product("product-batch-2", with_draft=False)
    scope = _content_scope()
    monkeypatch.setattr(
        "erp_web.runtime_units.copy_generation.generate_ai_copy_bundle",
        _fake_copy_bundle(),
    )

    result = copy_generate_batch(
        CopyGenerateBatchRequest(
            product_ids=("product-batch-1", "product-batch-2"),
            platform="mercadolibre",
        ),
        scope=scope,
        execution=_execution(),
    )
    assert result.success_count == 2
    assert result.failed_count == 0
    assert len(result.items) == 2
    for item in result.items:
        assert dict(item)["ok"] is True

    with pytest.raises(BusinessCapabilityError) as unsupported:
        copy_generate_batch(
            CopyGenerateBatchRequest(
                product_ids=("product-batch-1",),
                platform="shopee",
            ),
            scope=scope,
            execution=_execution(),
        )
    assert unsupported.value.code == "COPY_GENERATE_BATCH_FAILED"


def test_image_prompts_generate_builds_prompt_pack() -> None:
    _seed_product("product-prompt-1")
    scope = _content_scope()

    result = image_prompts_generate(
        ImagePromptsGenerateRequest(
            product_id="product-prompt-1",
            platform="mercadolibre",
            target_language="es",
        ),
        scope=scope,
    )
    assert result.prompt
    assert "Portable fan" in result.prompt or "Ventilador" in result.prompt
    assert result.selected_image_ids == ()

    with pytest.raises(BusinessCapabilityError) as missing:
        image_prompts_generate(
            ImagePromptsGenerateRequest(product_id="product-missing"),
            scope=scope,
        )
    assert missing.value.code == "PRODUCT_NOT_FOUND"


def test_text_translate_and_invalid_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _content_scope()

    def fake_translate(
        target_language: str,
        content: dict[str, Any],
        *,
        preserve_terms: tuple[str, ...] = (),
    ) -> dict[str, str]:
        assert preserve_terms == ("Generic", "MODEL-1")
        return {
            str(key): f"[{target_language}] {value}"
            for key, value in content.items()
        }

    monkeypatch.setattr(
        "erp_web.runtime_units.content_capabilities.translate_texts",
        fake_translate,
    )
    result = text_translate(
        TextTranslateRequest(
            target_language="es",
            content={"title": "Portable fan", "bullets": "Light"},
            preserve_terms=("Generic", "MODEL-1"),
        ),
        scope=scope,
    )
    assert result.translations == {
        "title": "[es] Portable fan",
        "bullets": "[es] Light",
    }

    def failing_translate(
        target_language: str,
        content: dict[str, Any],
        *,
        preserve_terms: tuple[str, ...] = (),
    ) -> Any:
        del preserve_terms
        raise TranslationRequestError("target_language 不支持")

    monkeypatch.setattr(
        "erp_web.runtime_units.content_capabilities.translate_texts",
        failing_translate,
    )
    with pytest.raises(BusinessCapabilityError) as invalid:
        text_translate(
            TextTranslateRequest(target_language="xx", content={"title": "a"}),
            scope=scope,
        )
    assert invalid.value.code == "INVALID_TRANSLATION_REQUEST"


# ---------------------------------------------------------------- 图片池


def test_image_pool_upload_action_save_roundtrip() -> None:
    context = get_context()
    _seed_product("product-image-1")
    scope = _image_scope()

    uploaded = image_pool_upload(
        ImagePoolUploadRequest(
            product_id="product-image-1",
            uploads=({"base64": _TINY_PNG_B64, "filename": "fan-upload.png"},),
        ),
        scope=scope,
        execution=_execution(),
    )
    assert uploaded.uploaded_count == 1
    pool_ids = [str(dict(item).get("id")) for item in uploaded.image_pool]
    uploaded_id = next(
        str(dict(item).get("id"))
        for item in uploaded.image_pool
        if dict(item).get("origin") == "local_upload"
    )
    assert "product-image-1-image-1" in pool_ids
    reloaded = context.products.load_product_from_index("product-image-1", "")
    source = dict(reloaded.get("source") or {})
    persisted_ids = [str(dict(item).get("id")) for item in source.get("image_pool") or []]
    assert uploaded_id in persisted_ids

    filtered = image_pool_action(
        ImagePoolActionRequest(
            product_id="product-image-1",
            action="filter",
            params={"platform": "mercadolibre"},
        ),
        scope=scope,
        execution=_execution(),
    )
    assert filtered.persisted is False

    deleted = image_pool_action(
        ImagePoolActionRequest(
            product_id="product-image-1",
            action="delete",
            params={"image_ids": [uploaded_id]},
        ),
        scope=scope,
        execution=_execution(),
    )
    assert deleted.persisted is True
    deleted_ids = [str(dict(item).get("id")) for item in deleted.image_pool]
    assert uploaded_id not in deleted_ids

    saved = image_pool_save(
        ImagePoolSaveRequest(
            product_id="product-image-1",
            image_pool=(
                {
                    "id": "custom-1",
                    "url": "https://cdn.example.com/custom-1.jpg",
                    "origin": "manual",
                    "selected": True,
                    "is_main": True,
                    "order": 0,
                },
            ),
        ),
        scope=scope,
        execution=_execution(),
    )
    saved_ids = [str(dict(item).get("id")) for item in saved.image_pool]
    assert saved_ids == ["custom-1"]
    final = context.products.load_product_from_index("product-image-1", "")
    final_source = dict(final.get("source") or {})
    final_ids = [
        str(dict(item).get("id")) for item in final_source.get("image_pool") or []
    ]
    assert final_ids == ["custom-1"]


def test_image_pool_upload_rejects_undecodable_payload() -> None:
    _seed_product("product-image-2")
    scope = _image_scope()
    with pytest.raises(BusinessCapabilityError) as error:
        image_pool_upload(
            ImagePoolUploadRequest(
                product_id="product-image-2",
                uploads=({"filename": "broken.png"},),
            ),
            scope=scope,
            execution=_execution(),
        )
    assert error.value.code == "IMAGE_UPLOAD_DECODE_FAILED"


def test_image_pool_sync_generated_keeps_pool_when_nothing_generated() -> None:
    context = get_context()
    _seed_product("product-image-3")
    scope = _image_scope()

    before = context.products.load_product_from_index("product-image-3", "")
    before_ids = [
        str(dict(item).get("id"))
        for item in dict(before.get("source") or {}).get("image_pool") or []
    ]
    result = image_pool_sync_generated(
        ImagePoolSyncGeneratedRequest(product_id="product-image-3"),
        scope=scope,
        execution=_execution(),
    )
    synced_ids = [str(dict(item).get("id")) for item in result.image_pool]
    assert sorted(before_ids) == sorted(synced_ids)


def test_image_edit_uses_trusted_main_image_when_ids_are_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_product("product-image-main")
    captured: dict[str, Any] = {}

    def fake_edit_images(
        app_dir: Any,
        product: dict[str, Any],
        app_config: dict[str, Any],
        *,
        prompt: str,
        platform: str,
        image_ids: list[str],
    ) -> dict[str, Any]:
        del app_dir, product, app_config, prompt, platform
        captured["image_ids"] = image_ids
        return {
            "ok": False,
            "error_code": "IMAGE_SERVICE_UNSUPPORTED",
            "error": "当前图片服务不支持编辑。",
        }

    monkeypatch.setattr(
        "erp_web.runtime_units.image_capabilities.image_translate_service.edit_images",
        fake_edit_images,
    )

    with pytest.raises(BusinessCapabilityError) as error:
        image_edit(
            ImageEditRequest(
                product_id="product-image-main",
                prompt="纯白背景",
            ),
            scope=_image_scope(),
            execution=_execution(),
        )

    assert error.value.code == "IMAGE_SERVICE_UNSUPPORTED"
    assert captured["image_ids"] == ["product-image-main-image-1"]


def test_image_edit_can_persist_generated_image_as_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = get_context()
    _seed_product("product-image-replace-main")

    def fake_edit_images(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return {
            "ok": True,
            "message": "编辑完成。",
            "imagePoolItems": [
                {
                    "id": "generated-main-1",
                    "url": "https://cdn.example.com/generated-main-1.png",
                    "origin": "ai_generated",
                    "status": "ready",
                    "selected": True,
                    "is_main": False,
                }
            ],
        }

    monkeypatch.setattr(
        "erp_web.runtime_units.image_capabilities.image_translate_service.edit_images",
        fake_edit_images,
    )

    result = image_edit(
        ImageEditRequest(
            product_id="product-image-replace-main",
            prompt="纯白背景",
            set_as_main=True,
        ),
        scope=_image_scope(),
        execution=_execution(),
    )

    assert result.main_image_id == "generated-main-1"
    saved = context.products.load_product_from_index(
        "product-image-replace-main",
        "",
    )
    pool = list(dict(saved.get("source") or {}).get("image_pool") or [])
    assert [item["id"] for item in pool if item.get("is_main")] == [
        "generated-main-1"
    ]
