from __future__ import annotations

"""UPC 分配/导入 Capability。"""

from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.upc_capabilities import (
    UpcAssignRequest,
    UpcAssignResult,
    UpcImportRequest,
    UpcImportResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError


class ProductUpcStore(Protocol):
    def load_product_from_index(
        self,
        product_id: str = "",
        file_path: str = "",
    ) -> dict[str, Any]:
        ...

    def assign_upc_to_product(
        self,
        data: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        ...


class UpcDatabase(Protocol):
    def import_upcs(self, values: list[Any]) -> int:
        ...

    def upc_pool_stats(self) -> dict[str, Any]:
        ...


def _text(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class UpcCapabilityScope:
    """UPC 的可信依赖边界。"""

    products: ProductUpcStore
    database: UpcDatabase


UPC_ASSIGN_TOOL = "upc_assign"
UPC_IMPORT_TOOL = "upc_import"


@ai_tool(
    name=UPC_ASSIGN_TOOL,
    description="从本地 UPC 池为指定商品原子分配一个 UPC 并保存商品。",
    permission="product.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def upc_assign(
    request: UpcAssignRequest,
    scope: Annotated[UpcCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> UpcAssignResult:
    del execution
    product = scope.products.load_product_from_index(request.product_id, "")
    if _text(product.get("product_id")) != request.product_id:
        raise BusinessCapabilityError("PRODUCT_NOT_FOUND", "商品不存在。")
    upc, _saved = scope.products.assign_upc_to_product(product)
    if not upc:
        raise BusinessCapabilityError(
            "UPC_POOL_EMPTY",
            "UPC 池为空，请先在设置中导入 UPC。",
        )
    return UpcAssignResult(
        product_id=request.product_id,
        upc=upc,
        upc_pool=dict(scope.database.upc_pool_stats()),
    )


@ai_tool(
    name=UPC_IMPORT_TOOL,
    description="向本地 UPC 池批量导入 UPC 值。",
    permission="product.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def upc_import(
    request: UpcImportRequest,
    scope: Annotated[UpcCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> UpcImportResult:
    del execution
    values = [_text(value) for value in request.values if _text(value)]
    if not values:
        raise BusinessCapabilityError("UPC_IMPORT_EMPTY", "没有可导入的 UPC。")
    imported = int(scope.database.import_upcs(values) or 0)
    return UpcImportResult(
        imported=imported,
        upc_pool=dict(scope.database.upc_pool_stats()),
    )


UPC_AI_CAPABILITIES = (
    upc_assign,
    upc_import,
)


__all__ = [
    "UPC_AI_CAPABILITIES",
    "UPC_ASSIGN_TOOL",
    "UPC_IMPORT_TOOL",
    "UpcCapabilityScope",
    "ProductUpcStore",
    "UpcDatabase",
    "upc_assign",
    "upc_import",
]
