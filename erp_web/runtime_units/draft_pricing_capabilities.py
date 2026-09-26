"""AI 核价薄适配：业务资料由统一草稿核价服务读取。"""
from dataclasses import dataclass
import math
from typing import Annotated, Any

from erp_web.runtime_units.draft_pricing import DraftPricingStore, price_draft
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.draft_pricing import DraftPricingRequest, DraftPricingResult
from erp_web.schemas.draft_pricing import PricingIssue
from erp_web.services.ai_tool_declaration import Injected, ai_tool


@dataclass(frozen=True)
class DraftPricingCapabilityScope:
    products: DraftPricingStore


def pricing_summary(result: dict[str, Any]) -> DraftPricingResult:
    markets: dict[str, dict[str, Any]] = {}
    for row in result.get("items", []):
        for quote in row["result"].get("results", []):
            key = str(quote.get("target_key") or "").lower()
            money = quote.get("applied_price") or {}
            try:
                amount = float(money.get("amount") or 0)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(amount) or amount <= 0:
                continue
            market = markets.setdefault(key, {"target_key": key, "currency": money.get("currency", ""),
                "min_price": amount, "max_price": amount, "sku_count": 0})
            market["min_price"] = min(market["min_price"], amount)
            market["max_price"] = max(market["max_price"], amount)
            market["sku_count"] += 1
    return DraftPricingResult(draft_id=result["draft_id"], applied=result["applied"],
        sku_count=result["sku_count"], target_count=result["target_count"], updated_at=result["updated_at"],
        markets=list(markets.values()), error_count=len(result["errors"]),
        errors=[{key: str(value) for key, value in issue.items() if key in PricingIssue.model_fields} for issue in result["errors"][:100]])


@ai_tool(
    name="draft_pricing_preview",
    description=("预览草稿全部已选 SKU 的核价，不保存售价。系统自动读取逐 SKU 已有采购成本、包装资料及费用覆盖，"
        "无需先读取或重新提交成本。只提交用户明确修改的参数；国内物流填写 common.domestic_freight_cny，"
        "shipping_amount 仅表示国际运费。只根据返回的实际缺失字段向用户补问；用户未要求核价时不要主动核价。"),
    permission="pricing.read", side_effect="none", recovery_policy="retry_safe", version="1",
)
def draft_pricing_preview(request: DraftPricingRequest, scope: Annotated[DraftPricingCapabilityScope, Injected()]) -> DraftPricingResult:
    return pricing_summary(price_draft(request, product_store=scope.products))


@ai_tool(
    name="draft_pricing_apply",
    description=("按现有 SKU 资料和本次明确修改的费用核价，并原子保存逐 SKU × 市场的售价。"
        "留空 common/targets 使用已存设置，无需提供采购成本。国内物流填写 common.domestic_freight_cny；"
        "不要把国内物流设成国际运费。仅在用户要求应用售价或完成核价时使用。错误结果 applied=false，不会部分保存。"),
    permission="draft.write", side_effect="write", approval_required=False,
    idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual", version="4",
)
def draft_pricing_apply(request: DraftPricingRequest, scope: Annotated[DraftPricingCapabilityScope, Injected()],
                        execution: Annotated[AiExecutionContext, Injected()]) -> DraftPricingResult:
    del execution
    return pricing_summary(price_draft(request, product_store=scope.products, apply=True))


DRAFT_PRICING_AI_CAPABILITIES = (draft_pricing_preview, draft_pricing_apply)
__all__ = ["DraftPricingCapabilityScope", "DRAFT_PRICING_AI_CAPABILITIES", "draft_pricing_preview", "draft_pricing_apply"]
