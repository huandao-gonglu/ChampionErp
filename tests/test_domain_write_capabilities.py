from __future__ import annotations

"""Workstream D 第二批：本地写 Capability 行为测试。

覆盖商品/草稿保存读取、删除审批 digest、Global Task 审批闸门纵向流程、
文案生成（单个/批量）、图片提示词、文本翻译与图片池维护。
AI 与网络边界以可信替代注入，持久化走隔离 AppContext 的真实存储。
"""

import base64
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
    draft_read,
    draft_save,
    product_delete,
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
    ImagePoolActionRequest,
    ImagePoolSaveRequest,
    ImagePoolSyncGeneratedRequest,
    ImagePoolUploadRequest,
)
from erp_web.schemas.product_write_capabilities import (
    DraftDeleteRequest,
    DraftReadRequest,
    DraftSaveRequest,
    ProductDeleteRequest,
    ProductSaveRequest,
)
from erp_web.services.capability_errors import BusinessCapabilityError
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
                "site": "MLM",
                "language": "es",
                "target_sites": [
                    {
                        "platform": "mercadolibre",
                        "site": "MLM",
                        "language": "es",
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
    assert dict(saved.product)["name"] == "Portable fan Pro"
    reloaded = context.products.load_product_from_index("product-save-1", "")
    assert str(reloaded.get("name")) == "Portable fan Pro"


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
    assert dict(updated.draft)["title"] == "Ventilador Pro Max"
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
    context = get_context()
    _seed_product("product-task-del", with_draft=False)
    controller = global_task_facade.build_global_task_controller(context)
    target_ids = ["product-task-del"]

    # 模型只提供业务参数；审批摘要与 payload 由服务端快照生成。
    response = controller.start_task(
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
        conversation_id="conversation-d2",
        message_id="message-d2-1",
    )
    task = response.task
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

    approved = controller.approve_task(
        GlobalTaskApproveRequest(task_id=task.task_id),
        approver="local-ui:test",
        conversation_id="conversation-d2",
        message_id="message-d2-2",
    ).task
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
    ) -> dict[str, str]:
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
        ),
        scope=scope,
    )
    assert result.translations == {
        "title": "[es] Portable fan",
        "bullets": "[es] Light",
    }

    def failing_translate(target_language: str, content: dict[str, Any]) -> Any:
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
