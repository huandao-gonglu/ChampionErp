from __future__ import annotations

"""商品/草稿保存、读取与删除 Capability（本地写入）。

删除能力要求审批：审批摘要与规范化参数由服务端快照函数生成，digest 绑定
冻结参数、步骤、任务版本与 Capability 版本；执行时重算快照并复核，防止
模型伪造审批或批准后目标漂移。
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from erp_web.runtime_units.draft_category_resolution import (
    resolve_draft_category_pairs,
)
from erp_web.schemas.ai_tools import TaskApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.product_write_capabilities import (
    DraftDeleteRequest,
    DraftDeleteResult,
    DraftReadRequest,
    DraftReadResult,
    DraftSaveRequest,
    DraftSaveResult,
    ProductDeleteRequest,
    ProductDeleteResult,
    ProductSaveRequest,
    ProductSaveResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.task_approval import verify_execution_approval


class ProductDraftWriteStore(Protocol):
    def save_product_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        ...

    def delete_products_from_index(
        self,
        product_ids: list[Any],
    ) -> dict[str, Any]:
        ...

    def load_product_from_index(
        self,
        product_id: str = "",
        file_path: str = "",
    ) -> dict[str, Any]:
        ...

    def load_draft_detail_from_index(
        self,
        draft_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
        ...

    def save_draft_detail(
        self,
        draft_payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
        ...

    def delete_draft_from_index(self, draft_id: Any) -> dict[str, Any]:
        ...


def _text(value: Any) -> str:
    return str(value or "").strip()


def _raise_store_error(
    error: dict[str, Any] | None,
    *,
    default_code: str,
    default_message: str,
) -> None:
    if error is None:
        return
    raise BusinessCapabilityError(
        _text(error.get("error_code")) or default_code,
        _text(error.get("error")) or default_message,
    )


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _id_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(_text(item) for item in value if _text(item))


@dataclass(frozen=True)
class ProductWriteCapabilityScope:
    """商品/草稿写入的可信商品存储边界。"""

    products: ProductDraftWriteStore


PRODUCT_SAVE_TOOL = "product_save"
PRODUCT_DELETE_TOOL = "product_delete"
DRAFT_READ_TOOL = "draft_read"
DRAFT_SAVE_TOOL = "draft_save"
DRAFT_DELETE_TOOL = "draft_delete"


def _normalized_ids(ids: Any) -> list[str]:
    return sorted(
        {item for item in (_text(value) for value in ids) if item}
    )


def _state_token(value: dict[str, Any] | None) -> str:
    """返回不泄露资源正文的稳定状态指纹；空资源有明确哨兵。"""

    if not value:
        return "missing"
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _product_delete_states(
    ids: list[str],
    scope: ProductWriteCapabilityScope,
) -> dict[str, str]:
    states: dict[str, str] = {}
    for product_id in ids:
        loaded = scope.products.load_product_from_index(product_id, "")
        exact = (
            loaded
            if _text(loaded.get("product_id")) == product_id
            else {}
        )
        states[product_id] = _state_token(exact)
    return states


def _draft_delete_states(
    ids: list[str],
    scope: ProductWriteCapabilityScope,
) -> dict[str, str]:
    states: dict[str, str] = {}
    for draft_id in ids:
        result, error, _status = scope.products.load_draft_detail_from_index(draft_id)
        draft = result.get("draft") if error is None and isinstance(result, dict) else {}
        states[draft_id] = _state_token(draft if isinstance(draft, dict) else {})
    return states


def _product_delete_approval_snapshot(
    request: ProductDeleteRequest,
    scope: ProductWriteCapabilityScope,
) -> TaskApprovalSnapshot:
    """服务端生成的删除审批快照；模型不提供摘要也不提供参数。"""

    ids = _normalized_ids(request.product_ids)
    preview = "、".join(ids[:5])
    more = f"（另有 {len(ids) - 5} 个）" if len(ids) > 5 else ""
    return TaskApprovalSnapshot(
        summary=f"删除 {len(ids)} 个本地商品：{preview}{more}",
        canonical_payload={
            "product_ids": ids,
            "resource_states": _product_delete_states(ids, scope),
        },
    )


def _draft_delete_approval_snapshot(
    request: DraftDeleteRequest,
    scope: ProductWriteCapabilityScope,
) -> TaskApprovalSnapshot:
    """服务端生成的草稿删除审批快照；模型不提供摘要也不提供参数。"""

    ids = _normalized_ids(request.draft_ids)
    preview = "、".join(ids[:5])
    more = f"（另有 {len(ids) - 5} 个）" if len(ids) > 5 else ""
    return TaskApprovalSnapshot(
        summary=f"删除 {len(ids)} 个本地草稿：{preview}{more}",
        canonical_payload={
            "draft_ids": ids,
            "resource_states": _draft_delete_states(ids, scope),
        },
    )


@ai_tool(
    name=PRODUCT_SAVE_TOOL,
    description="保存/更新本地商品主档（不含草稿内容）。",
    permission="product.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def product_save(
    request: ProductSaveRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ProductSaveResult:
    del execution
    saved = scope.products.save_product_profile(dict(request.product))
    return ProductSaveResult(product=_dict_value(saved))


@ai_tool(
    name=PRODUCT_DELETE_TOOL,
    description=(
        "删除本地商品；破坏性操作，需要人工在受信界面批准后才会执行。"
    ),
    permission="product.write",
    side_effect="write",
    approval_required=True,
    approval_snapshot=_product_delete_approval_snapshot,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def product_delete(
    request: ProductDeleteRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ProductDeleteResult:
    snapshot = _product_delete_approval_snapshot(request, scope)
    verify_execution_approval(
        execution,
        snapshot=snapshot,
        capability_name=PRODUCT_DELETE_TOOL,
        capability_version="1",
        stale_code="PRODUCT_DELETE_APPROVAL_STALE",
    )
    result = scope.products.delete_products_from_index(list(request.product_ids))
    if not isinstance(result, dict) or not result.get("ok"):
        raise BusinessCapabilityError(
            "PRODUCT_DELETE_FAILED",
            _text(result.get("error") if isinstance(result, dict) else "")
            or "商品删除失败。",
        )
    return ProductDeleteResult(
        deleted=int(result.get("deleted") or 0),
        deleted_ids=_id_tuple(result.get("deletedIds")),
        missing_ids=_id_tuple(result.get("missingIds")),
    )


@ai_tool(
    name=DRAFT_READ_TOOL,
    description="按 draft_id 读取完整草稿详情与关联商品上下文。",
    permission="draft.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def draft_read(
    request: DraftReadRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
) -> DraftReadResult:
    result, error, _status = scope.products.load_draft_detail_from_index(
        request.draft_id
    )
    _raise_store_error(
        error,
        default_code="DRAFT_NOT_FOUND",
        default_message="草稿不存在。",
    )
    return DraftReadResult(
        draft=_dict_value(result.get("draft")),
        product_context=_dict_value(result.get("productContext")),
    )


@ai_tool(
    name=DRAFT_SAVE_TOOL,
    description="保存草稿详情；Ozon 目标会自动解析实时类目对。",
    permission="draft.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def draft_save(
    request: DraftSaveRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> DraftSaveResult:
    del execution
    try:
        resolved = resolve_draft_category_pairs(dict(request.draft))
    except (RuntimeError, TimeoutError, ValueError) as exc:
        raise BusinessCapabilityError(
            "OZON_CATEGORY_PAIR_RESOLVE_FAILED",
            str(exc) or "Ozon 类目对解析失败。",
        ) from exc
    result, error, status = scope.products.save_draft_detail(resolved)
    _raise_store_error(
        error,
        default_code="DRAFT_NOT_FOUND" if status == 404 else "DRAFT_SAVE_FAILED",
        default_message="草稿不存在。" if status == 404 else "草稿保存失败。",
    )
    return DraftSaveResult(
        draft=_dict_value(result.get("draft")),
        product_context=_dict_value(result.get("productContext")),
    )


@ai_tool(
    name=DRAFT_DELETE_TOOL,
    description=(
        "删除本地草稿；破坏性操作，需要人工在受信界面批准后才会执行。"
    ),
    permission="draft.write",
    side_effect="write",
    approval_required=True,
    approval_snapshot=_draft_delete_approval_snapshot,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def draft_delete(
    request: DraftDeleteRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> DraftDeleteResult:
    snapshot = _draft_delete_approval_snapshot(request, scope)
    verify_execution_approval(
        execution,
        snapshot=snapshot,
        capability_name=DRAFT_DELETE_TOOL,
        capability_version="1",
        stale_code="DRAFT_DELETE_APPROVAL_STALE",
    )
    result = scope.products.delete_draft_from_index(list(request.draft_ids))
    if not isinstance(result, dict) or not result.get("ok"):
        raise BusinessCapabilityError(
            "DRAFT_DELETE_FAILED",
            _text(result.get("error") if isinstance(result, dict) else "")
            or "草稿删除失败。",
        )
    return DraftDeleteResult(
        deleted=int(result.get("deleted") or 0),
        deleted_ids=_id_tuple(result.get("deletedDraftIds")),
        missing_ids=_id_tuple(result.get("missingIds")),
        affected_product_ids=_id_tuple(result.get("affectedProductIds")),
    )


PRODUCT_WRITE_AI_CAPABILITIES = (
    product_save,
    product_delete,
)

DRAFT_WRITE_AI_CAPABILITIES = (
    draft_read,
    draft_save,
    draft_delete,
)


__all__ = [
    "DRAFT_DELETE_TOOL",
    "DRAFT_READ_TOOL",
    "DRAFT_SAVE_TOOL",
    "DRAFT_WRITE_AI_CAPABILITIES",
    "PRODUCT_DELETE_TOOL",
    "PRODUCT_SAVE_TOOL",
    "PRODUCT_WRITE_AI_CAPABILITIES",
    "ProductDraftWriteStore",
    "ProductWriteCapabilityScope",
    "draft_delete",
    "draft_read",
    "draft_save",
    "product_delete",
    "product_save",
]
