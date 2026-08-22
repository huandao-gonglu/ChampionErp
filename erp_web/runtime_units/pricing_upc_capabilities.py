from __future__ import annotations

"""定价纯计算与 UPC 分配/导入 Capability。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.pricing_upc_capabilities import (
    PricingCalculateRequest,
    PricingCalculateResult,
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


def _dict_rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


@dataclass(frozen=True)
class PricingUpcCapabilityScope:
    """定价与 UPC 的可信依赖边界。"""

    pricing_calculator: Callable[[dict[str, Any]], dict[str, Any]]
    products: ProductUpcStore
    database: UpcDatabase


PRICING_CALCULATE_TOOL = "pricing_calculate"
UPC_ASSIGN_TOOL = "upc_assign"
UPC_IMPORT_TOOL = "upc_import"


@ai_tool(
    name=PRICING_CALCULATE_TOOL,
    description=(
        "按发布目标执行确定性核价计算；targets 为发布目标数组，"
        "可选提供汇率模式与手动汇率。共享采购成本放 common.cost_cny；"
        "每个目标的手动运费使用 shipping_quote_mode=manual、"
        "shipping_currency 与 shipping_amount，目标销售利润率使用 "
        "target_margin_percent。"
    ),
    permission="pricing.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def pricing_calculate(
    request: PricingCalculateRequest,
    scope: Annotated[PricingUpcCapabilityScope, Injected()],
) -> PricingCalculateResult:
    input_data: dict[str, Any] = {"targets": [dict(item) for item in request.targets]}
    if request.exchange_rate_mode:
        input_data["exchange_rate_mode"] = request.exchange_rate_mode
    for field in ("usd_cny_rate", "mxn_usd_rate", "rub_usd_rate", "rub_cny_rate"):
        value = getattr(request, field)
        if value is not None:
            input_data[field] = value
    if request.force_exchange_rate_refresh:
        input_data["force_exchange_rate_refresh"] = True
    if request.common:
        input_data["common"] = dict(request.common)
    result = scope.pricing_calculator(input_data)
    if not isinstance(result, dict) or not result.get("ok"):
        # 确定性核价把字段级错误放进 errors 数组；只有基础设施异常才用
        # 顶层 error。不得把具体字段错误抹成统一 PRICING_CALCULATE_FAILED。
        field_errors = [
            dict(item)
            for item in (
                result.get("errors") if isinstance(result, dict) else None
            )
            or []
            if isinstance(item, dict)
        ]
        if field_errors:
            first_message = _text(field_errors[0].get("message")) or "核价资料不完整。"
            raise BusinessCapabilityError(
                "PRICING_INPUT_INVALID",
                first_message,
                retryable=False,
                details={"errors": field_errors},
            )
        message = _text(
            result.get("error") if isinstance(result, dict) else ""
        ) or "核价计算失败。"
        raise BusinessCapabilityError("PRICING_CALCULATE_FAILED", message)
    exchange_rates = (
        result.get("exchange_rates")
        if isinstance(result.get("exchange_rates"), dict)
        else {}
    )
    return PricingCalculateResult(
        targets=_dict_rows(result.get("targets")),
        exchange_rates=dict(exchange_rates),
        exchange_rate_mode=_text(result.get("exchange_rate_mode")),
    )


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
    scope: Annotated[PricingUpcCapabilityScope, Injected()],
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
    scope: Annotated[PricingUpcCapabilityScope, Injected()],
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


PRICING_UPC_AI_CAPABILITIES = (
    pricing_calculate,
    upc_assign,
    upc_import,
)


__all__ = [
    "PRICING_CALCULATE_TOOL",
    "PRICING_UPC_AI_CAPABILITIES",
    "UPC_ASSIGN_TOOL",
    "UPC_IMPORT_TOOL",
    "PricingUpcCapabilityScope",
    "ProductUpcStore",
    "UpcDatabase",
    "pricing_calculate",
    "upc_assign",
    "upc_import",
]
