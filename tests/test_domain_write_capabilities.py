from __future__ import annotations

"""Workstream D 第二批：本地写 Capability 行为测试。

覆盖商品/草稿保存读取、删除审批 digest、Global Task 审批闸门纵向流程、
文案生成（单个/批量）、图片提示词、文本翻译与图片池维护。
AI 与网络边界以可信替代注入，持久化走隔离 AppContext 的真实存储。
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from erp_web.context import get_context
from erp_web.runtime_units.content_capabilities import (
    ContentCapabilityScope,
    copy_generate,
    copy_generate_batch,
    image_prompts_generate,
    text_translate,
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
    draft_read,
    draft_save,
    product_delete,
    product_profile_patch,
    product_save,
)
from erp_web.runtime_units.text_translation import TranslationRequestError
from erp_web.schemas.ai_tools import AiToolExecutionError, ToolApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.content_capabilities import (
    CopyGenerateBatchRequest,
    CopyGenerateRequest,
    ImagePromptsGenerateRequest,
    TextTranslateRequest,
)
from erp_web.schemas.image_capabilities import (
    ImageEditRequest,
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
    ProductProfilePatchRequest,
    ProductSaveRequest,
)
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.tool_approval import approval_binding_digest


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
        business_scope={"task_id": "task-1", "tool_call_id": "step-1"},
        idempotency_context={"operation_key": operation_key},
    )


def _approved_execution(
    snapshot: ToolApprovalSnapshot,
    capability_name: str,
    *,
    operation_key: str = "op-1",
    tool_call_id: str = "step-1",
    approval_revision: int = 1,
) -> AiExecutionContext:
    """模拟 Controller 批准后注入的可信审批上下文（digest + 任务版本）。"""

    digest = approval_binding_digest(
        snapshot=snapshot,
        capability_name=capability_name,
        capability_version="1",
        operation_key=operation_key,
        tool_call_id=tool_call_id,
        approval_revision=approval_revision,
    )
    return AiExecutionContext(
        task_run_id="task-1",
        attempt_id="attempt-1",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        budget_profile="test",
        business_scope={
            "task_id": "task-1",
            "tool_call_id": tool_call_id,
            "approver": "local-ui:test",
        },
        idempotency_context={"operation_key": operation_key},
        approval_digest=digest,
        approval_revision=approval_revision,
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
    product["source"]["attributes"] = {"规格": "x" * 300_000}
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


def test_draft_read_and_save_do_not_scan_full_indexes(monkeypatch: pytest.MonkeyPatch) -> None:
    context = get_context()
    saved_product = _seed_product("product-draft-no-full-index")
    draft_id = str(saved_product["drafts"]["mercadolibre"]["draft_id"])
    scope = _write_scope()

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("AI draft_read/draft_save 不应扫描全库页面索引")

    monkeypatch.setattr(context.products, "load_products_index", forbidden)
    monkeypatch.setattr(context.products, "load_drafts_index", forbidden)

    read = draft_read(DraftReadRequest(draft_id=draft_id), scope=scope)
    assert dict(read.draft)["draft_id"] == draft_id
    updated = draft_save(
        DraftSaveRequest(draft={"draft_id": draft_id, "title": "Updated without index scan"}),
        scope=scope,
        execution=_execution(),
    )
    assert updated.changed is True


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
    product["attributes"] = {"规格": "大规格" * 60_000}
    product["source"]["attributes"] = {"规格": "来源规格" * 60_000}
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
            draft={"draft_id": draft_id, "title": "新草稿标题", "platform": "mercadolibre"}
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
    assert context.db.load_draft_model(draft_id)["title"] == "新草稿标题"



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


def test_product_profile_attribute_patch_can_be_verified_by_product_read() -> None:
    """复现对话中的主档属性写入，回读必须显示属性实际值。"""
    from erp_web.runtime_units.product_capabilities import read_product
    from erp_web.schemas.product_capabilities import ProductReadRequest

    context = get_context()
    _seed_product("product-attribute-readback", with_draft=False)
    result = product_profile_patch(
        ProductProfilePatchRequest(
            product={
                "product_id": "product-attribute-readback",
                "attributes": {"适用年龄": "1-99岁"},
            }
        ),
        scope=_write_scope(),
        execution=_execution("op-profile-attribute-readback"),
    )
    facts = read_product(
        ProductReadRequest(product_id=result.product_id),
        product_store=context.products,
    )
    assert facts.product.attributes == {"适用年龄": "1-99岁"}
    assert "适用年龄" not in facts.product.source_attributes


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
    assert missing.value.code == "TOOL_APPROVAL_CONTEXT_REQUIRED"
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
    assert missing.value.code == "TOOL_APPROVAL_CONTEXT_REQUIRED"
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


# ---------------------------------------------------------------- 文案 / 翻译


def _fake_copy_bundle(
    title: str = "Ventilador generado",
    *,
    global_title: str = "",
) -> Any:
    def bundle(
        product: dict[str, Any],
        source_platform: str,
        target_market: str,
        language: str,
        mode: str,
        app_cfg: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        copy = {
            "title": title,
            "description": "Descripción generada",
        }
        if global_title:
            copy["global_title"] = global_title
        return {
            "ok": True,
            "copy": copy,
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


def test_copy_generate_persists_global_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = get_context()
    _seed_product("product-copy-global-title")
    scope = _content_scope()
    monkeypatch.setattr(
        "erp_web.runtime_units.content_capabilities.generate_ai_copy_bundle",
        _fake_copy_bundle(
            "Localized marketplace title",
            global_title="Portable Fan with Quiet Cooling",
        ),
    )

    result = copy_generate(
        CopyGenerateRequest(
            product_id="product-copy-global-title",
            platform="mercadolibre",
        ),
        scope=scope,
        execution=_execution(),
    )

    assert result.copy_record["global_title"] == "Portable Fan with Quiet Cooling"
    reloaded = context.products.load_product_from_index(
        "product-copy-global-title",
        "",
    )
    draft = dict((reloaded.get("drafts") or {}).get("mercadolibre") or {})
    assert draft.get("global_title") == "Portable Fan with Quiet Cooling"


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


def test_copy_generate_batch_is_compact_concurrent_and_skips_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    from erp_web.runtime_units.copy_generation import batch_generate_copy_for_products

    context = get_context()
    product_ids = (
        "product-batch-compact-1",
        "product-batch-compact-2",
        "product-batch-compact-3",
    )
    for pid in product_ids:
        product = _seed_product(pid, with_draft=False)
        product["attributes"] = {
            f"attr_{idx}": "X" * 200
            for idx in range(100)
        }
        context.products.save_product(product)

    scope = _content_scope()
    barrier = threading.Barrier(len(product_ids), timeout=3.0)

    def concurrent_copy_bundle(
        product: dict[str, Any],
        source_platform: str,
        target_platform: str,
        language: str,
        mode: str,
        app_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        del app_cfg
        assert get_context() is context
        # 只有真正并发执行时三个线程才能同时到达栅栏，串行会超时失败。
        barrier.wait()
        pid = str(product.get("product_id") or "")
        return {
            "ok": True,
            "target_market": target_platform,
            "source_platform": source_platform,
            "language": language or "es",
            "mode": mode or "rewrite",
            "copy": {
                "title": f"Localized {pid}",
                "description": f"Detailed description for {pid}",
                "highlights": ["Compact", "Durable"],
                "search_terms": ["fan"],
            },
        }

    monkeypatch.setattr(
        "erp_web.runtime_units.copy_generation.generate_ai_copy_bundle",
        concurrent_copy_bundle,
    )

    with monkeypatch.context() as scoped:
        scoped.setattr(
            context.products,
            "load_products_index",
            lambda *args, **kwargs: pytest.fail("AI 批量文案工具不应扫描全库 productsIndex"),
        )
        result = copy_generate_batch(
            CopyGenerateBatchRequest(
                product_ids=product_ids,
                platform="mercadolibre",
            ),
            scope=scope,
            execution=_execution(),
        )

    assert result.success_count == 3
    assert result.failed_count == 0
    assert [item["product_id"] for item in result.items] == list(product_ids)
    for item in result.items:
        assert item["ok"] is True
        assert item["draft_id"]
        assert item["description_length"] > 0
        assert "product" not in item
    # 即使商品源属性较大，AI 工具回执也应保持紧凑，不触发输出体积超限。
    assert len(json.dumps(list(result.items), ensure_ascii=False).encode("utf-8")) < 4000

    # HTTP 路由默认调用仍保留兼容字段（product 与 productsIndex）。
    monkeypatch.setattr(
        "erp_web.runtime_units.copy_generation.generate_ai_copy_bundle",
        _fake_copy_bundle("Default HTTP Title"),
    )
    http_payload = batch_generate_copy_for_products(
        [product_ids[0]],
        platform="mercadolibre",
    )
    assert http_payload["ok"] is True
    assert "productsIndex" in http_payload
    assert "product" in http_payload["items"][0]


def test_copy_generate_batch_strict_lookup_and_draft_ids_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import ValidationError

    context = get_context()
    product_a = _seed_product("product-copy-strict-a", with_draft=False)
    product_a["drafts"] = {
        "yandex": {
            "platform": "yandex",
            "site": "global",
            "language": "Russian",
            "title": "Original A",
            "description": "Original desc A",
        }
    }
    product_a = context.products.save_product(product_a)
    product_b = _seed_product("product-copy-strict-b", with_draft=False)
    product_b["drafts"] = {
        "yandex": {
            "platform": "yandex",
            "site": "global",
            "language": "Russian",
            "title": "Original B",
            "description": "Original desc B",
        }
    }
    product_b = context.products.save_product(product_b)
    draft_a = str(product_a["drafts"]["yandex"]["draft_id"])
    draft_b = str(product_b["drafts"]["yandex"]["draft_id"])
    before_a_title = str(product_a["drafts"]["yandex"].get("title") or "")
    before_b_title = str(product_b["drafts"]["yandex"].get("title") or "")

    calls: list[tuple[str, str, str]] = []

    def tracking_bundle(
        product: dict[str, Any],
        source_platform: str,
        target_platform: str,
        language: str,
        mode: str,
        app_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        del source_platform, mode, app_cfg
        pid = str(product.get("product_id") or "")
        calls.append((pid, target_platform, language))
        return {
            "ok": True,
            "target_market": target_platform,
            "source_platform": target_platform,
            "language": language,
            "mode": "rewrite",
            "copy": {
                "title": f"Generated for {pid}",
                "description": f"Description for {pid}",
                "search_keywords": [pid],
            },
        }

    monkeypatch.setattr(
        "erp_web.runtime_units.copy_generation.generate_ai_copy_bundle",
        tracking_bundle,
    )
    scope = _content_scope()

    # 1) 误把 draft_id 填入 product_ids 时必须严格报错，绝不能静默回落到默认商品。
    wrong_id_result = copy_generate_batch(
        CopyGenerateBatchRequest(
            product_ids=(draft_a,),
            platform="yandex",
        ),
        scope=scope,
        execution=_execution(),
    )
    assert wrong_id_result.success_count == 0
    assert wrong_id_result.failed_count == 1
    assert wrong_id_result.items[0]["ok"] is False
    assert "draft_ids" in str(wrong_id_result.items[0]["error"])
    assert calls == []
    assert context.products.find_draft_from_index(draft_a)["drafts"]["yandex"].get("title", "") == before_a_title
    assert context.products.find_draft_from_index(draft_b)["drafts"]["yandex"].get("title", "") == before_b_title

    # 2) 使用 draft_ids 批量生成（省略 platform 时自动按草稿所属平台解析），各自写回对应草稿。
    draft_batch_result = copy_generate_batch(
        CopyGenerateBatchRequest(draft_ids=(draft_a, draft_b)),
        scope=scope,
        execution=_execution(),
    )
    assert draft_batch_result.success_count == 2
    assert draft_batch_result.failed_count == 0
    assert [item["draft_id"] for item in draft_batch_result.items] == [draft_a, draft_b]
    assert [item["product_id"] for item in draft_batch_result.items] == [
        "product-copy-strict-a",
        "product-copy-strict-b",
    ]
    assert all(item["platform"] == "yandex" for item in draft_batch_result.items)
    saved_a = context.products.find_draft_from_index(draft_a)["drafts"]["yandex"]
    saved_b = context.products.find_draft_from_index(draft_b)["drafts"]["yandex"]
    assert saved_a["title"] == "Generated for product-copy-strict-a"
    assert draft_batch_result.items[0]["updated_at"] == saved_a["updated_at"]
    assert draft_batch_result.items[1]["updated_at"] == saved_b["updated_at"]
    assert context.products.find_draft_from_index(draft_b)["drafts"]["yandex"]["title"] == "Generated for product-copy-strict-b"

    # 3) 不存在的 draft_id 与跨平台不匹配的 draft_id 均严格失败，不污染任何草稿。
    calls.clear()
    mismatch_result = copy_generate_batch(
        CopyGenerateBatchRequest(
            draft_ids=("draft-does-not-exist", draft_a),
            platform="ozon",
        ),
        scope=scope,
        execution=_execution(),
    )
    assert mismatch_result.success_count == 0
    assert mismatch_result.failed_count == 2
    assert "草稿不存在" in str(mismatch_result.items[0]["error"])
    assert "不匹配" in str(mismatch_result.items[1]["error"])
    assert calls == []

    # 4) 请求校验拒绝空目标与同时传 product_ids + draft_ids。
    with pytest.raises(ValidationError):
        CopyGenerateBatchRequest()
    with pytest.raises(ValidationError):
        CopyGenerateBatchRequest(product_ids=("product-copy-strict-a",), draft_ids=(draft_a,))


def test_category_match_and_text_translate_preserve_fatal_model_error_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from erp_web.runtime_units.category_capabilities import CategoryCapabilityScope, category_match
    from erp_web.schemas.market_prepare_capabilities import CategoryMatchRequest
    from erp_web.services.ai_model_errors import AIHTTPError

    context = get_context()
    saved = _seed_product("product-cat-402")
    draft_id = str(saved["drafts"]["mercadolibre"]["draft_id"])
    expected_details = {
        "status_code": 402,
        "use_case_id": "category.product_match",
        "use_case_label": "商品类目候选匹配",
        "model_id": "ai_model_ds41",
        "model_name": "deepSeekv4.1flash",
    }

    def failing_matcher(*_args, **_kwargs):
        return {
            "ok": False,
            "status": "failed",
            "failure": {
                "code": "AI_PROVIDER_PAYMENT_REQUIRED",
                "message": "「商品类目候选匹配」绑定的模型「deepSeekv4.1flash」余额不足或计费不可用（HTTP 402），请充值或在「授权配置 → AI 模型」切换绑定。",
                "retryable": False,
                "details": expected_details,
            },
        }

    with pytest.raises(BusinessCapabilityError) as cat_err:
        category_match(
            CategoryMatchRequest(draft_id=draft_id, target_platform="mercadolibre"),
            scope=CategoryCapabilityScope(products=context.products, matcher=failing_matcher),
            execution=_execution(),
        )
    assert cat_err.value.code == "AI_PROVIDER_PAYMENT_REQUIRED"
    assert cat_err.value.retryable is False
    assert cat_err.value.details == expected_details
    assert "deepSeekv4.1flash" in str(cat_err.value)

    def failing_translate(*_args, **_kwargs):
        raise AIHTTPError(
            status_code=402,
            reason="Payment Required",
            detail="Insufficient Balance",
            model_id="ai_model_tr",
            model_name="deepseek-chat",
            api_style="openai_compatible",
            endpoint="api.deepseek.com/openai_compatible",
            use_case_id="text.translate",
            model_display_name="翻译测试模型",
        )

    monkeypatch.setattr(
        "erp_web.runtime_units.content_capabilities.translate_texts",
        failing_translate,
    )
    with pytest.raises(BusinessCapabilityError) as tr_err:
        text_translate(
            TextTranslateRequest(target_language="ru", content={"a": "黑色"}),
            scope=_content_scope(),
        )
    assert tr_err.value.code == "AI_PROVIDER_PAYMENT_REQUIRED"
    assert tr_err.value.retryable is False
    assert "「翻译」绑定的模型「翻译测试模型」" in str(tr_err.value)
    assert tr_err.value.details["use_case_id"] == "text.translate"
    assert tr_err.value.details["model_name"] == "翻译测试模型"


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
            str(key): f"[{target_language}] {value}" for key, value in content.items()
        }

    monkeypatch.setattr(
        "erp_web.runtime_units.content_capabilities.translate_texts",
        fake_translate,
    )
    result = text_translate(
        TextTranslateRequest(
            target_language="es",
            content={"title": "Portable fan", "description": "Light"},
            preserve_terms=("Generic", "MODEL-1"),
        ),
        scope=scope,
    )
    assert result.translations == {
        "title": "[es] Portable fan",
        "description": "[es] Light",
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
    persisted_ids = [
        str(dict(item).get("id")) for item in source.get("image_pool") or []
    ]
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

    monkeypatch.setattr("erp_web.services.image_translate_service.check_batch_connection", lambda *_: 1)
    monkeypatch.setattr(
        "erp_web.services.image_translate_service.edit_images",
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
        # 出图期间另一项工作更新商品，采用新图时应合入最新快照。
        latest = context.products.load_product_from_index("product-image-replace-main", "")
        from erp_web.runtime_units.image_pool import append_images_to_product_pool
        latest = append_images_to_product_pool(latest, [{
            "id": "other-upload", "url": "https://cdn.example.com/other-upload.png",
            "origin": "upload", "selected": True, "status": "ready",
        }])
        saved = context.products.save_product(latest)
        assert "other-upload" in {item["id"] for item in saved["source"]["image_pool"]}
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

    monkeypatch.setattr("erp_web.services.image_translate_service.check_batch_connection", lambda *_: 1)
    monkeypatch.setattr(
        "erp_web.services.image_translate_service.edit_images",
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
    assert "other-upload" in {item["id"] for item in pool}
    assert [item["id"] for item in pool if item.get("is_main")] == ["generated-main-1"]
