"""AI 核价薄适配：业务资料由统一草稿核价服务读取。"""
from dataclasses import dataclass
import math
from typing import Annotated, Any

from erp_web.runtime_units.draft_pricing import (
    DraftPricingStore,
    apply_draft_pricing_batch,
    price_draft,
    price_drafts_batch,
)
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.draft_pricing import (
    DraftPricingBatchApplyRequest,
    DraftPricingBatchItemResult,
    DraftPricingBatchPreviewRequest,
    DraftPricingBatchResult,
    DraftPricingRequest,
    DraftPricingResult,
    PricingIssue,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.product_model.sku_model import PACKAGE_FIELDS


@dataclass(frozen=True)
class DraftPricingCapabilityScope:
    products: DraftPricingStore


_MISSING_PACKAGE_GUIDANCE = (
    "已选 SKU 缺少包装边长。先核对相应 SKU 原图上的尺寸与包装层级；"
    "根据已有事实估算缺失边，用区间两端试算物流影响，再提交建议供用户确认。"
    "现有资料不足时说明具体缺口。"
)


def _markets_summary(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    markets: dict[str, dict[str, Any]] = {}
    for row in items:
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
    return list(markets.values())


def _compact_issues(errors: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {key: str(value) for key, value in issue.items() if key in PricingIssue.model_fields}
        for issue in errors[:100]
    ]


def pricing_summary(result: dict[str, Any]) -> DraftPricingResult:
    return DraftPricingResult(draft_id=result["draft_id"], preview_id=result.get("preview_id", ""), applied=result["applied"],
        sku_count=result["sku_count"], target_count=result["target_count"], updated_at=result["updated_at"],
        markets=_markets_summary(result.get("items", [])), error_count=len(result["errors"]),
        errors=_compact_issues(result["errors"]))


def pricing_batch_summary(result: dict[str, Any]) -> DraftPricingBatchResult:
    items: list[DraftPricingBatchItemResult] = []
    for row in result.get("items", []):
        raw_errors = list(row.get("errors") or [])
        error_code = str(row.get("error_code") or "")
        error_message = str(row.get("error") or "")
        if (
            error_code == "PRICING_INPUT_INVALID"
            and any(issue.get("field") in PACKAGE_FIELDS[:3] for issue in raw_errors if isinstance(issue, dict))
        ):
            error_message = _MISSING_PACKAGE_GUIDANCE
        items.append(DraftPricingBatchItemResult(
            draft_id=str(row.get("draft_id") or ""),
            ok=bool(row.get("ok")),
            preview_id=str(row.get("preview_id") or ""),
            applied=bool(row.get("applied")),
            sku_count=int(row.get("sku_count") or 0),
            target_count=int(row.get("target_count") or 0),
            updated_at=str(row.get("updated_at") or ""),
            markets=_markets_summary(row.get("items", [])),
            error_code=error_code,
            error=error_message,
            error_count=len(raw_errors),
            errors=_compact_issues([issue for issue in raw_errors if isinstance(issue, dict)]),
        ))
    return DraftPricingBatchResult(
        total=int(result.get("total") or len(items)),
        succeeded_count=int(result.get("succeeded_count") or 0),
        failed_count=int(result.get("failed_count") or 0),
        applied=bool(result.get("applied")),
        items=items,
    )


@ai_tool(
    name="draft_pricing_preview",
    description=("预览草稿全部已选 SKU 的核价，不保存售价。系统自动读取逐 SKU 已有采购成本、包装资料及费用覆盖，"
        "无需先读取或重新提交成本。新草稿尚无核价配置时自动使用默认模板，已有配置沿用草稿快照。"
        "只提交用户明确修改的参数；国内物流填写 common.domestic_freight_cny，"
        "shipping_amount 仅表示国际运费。多份草稿批量核价请优先使用 draft_pricing_preview_batch。缺失包装边长时先读对应 SKU 原图，再按有依据的包装区间试算物流影响并提交确认；缺成本或毛重再补问；用户未要求核价时不要主动核价。已有结果或预览的物流、费用明细通过 business_data_read(resource=draft.pricing, params 含 draft_id 与可选 preview_id) 按需读取，不要为解释重新核价。"),
    permission="pricing.read", side_effect="none", recovery_policy="retry_safe", version="1",
)
def draft_pricing_preview(request: DraftPricingRequest, scope: Annotated[DraftPricingCapabilityScope, Injected()]) -> DraftPricingResult:
    try:
        return pricing_summary(price_draft(request, product_store=scope.products))
    except BusinessCapabilityError as exc:
        issues = (exc.details or {}).get("errors", ())
        if exc.code != "PRICING_INPUT_INVALID" or not any(issue.get("field") in PACKAGE_FIELDS[:3] for issue in issues):
            raise
        raise BusinessCapabilityError(exc.code, _MISSING_PACKAGE_GUIDANCE, details=exc.details) from exc


@ai_tool(
    name="draft_pricing_preview_batch",
    description=("批量预览多份草稿全部已选 SKU 的核价，不保存售价（最多 50 份）。"
        "服务端共享本轮汇率、物流配置与平台佣金合批请求，大幅减少外部 API 次数；各草稿仍独立选仓并生成各自 preview_id，单份缺资料仅记录该项失败不阻断其他草稿。"
        "配合 draft_pricing_apply_batch 一次应用全部成功预览。"),
    permission="pricing.read", side_effect="none", recovery_policy="retry_safe", version="1",
)
def draft_pricing_preview_batch(
    request: DraftPricingBatchPreviewRequest,
    scope: Annotated[DraftPricingCapabilityScope, Injected()],
) -> DraftPricingBatchResult:
    return pricing_batch_summary(price_drafts_batch(request, product_store=scope.products))


@ai_tool(
    name="draft_pricing_apply",
    description=("应用已核验的预览，原子保存逐 SKU × 市场的售价，不重新计算。"
        "必须先调用 draft_pricing_preview，再传入其 preview_id 和相同核价参数；预览有效期十分钟。"
        "多份草稿批量应用请优先使用 draft_pricing_apply_batch。仅在用户要求应用售价或完成核价时使用；参数改变或预览失效须重新预览，不会部分保存。"),
    permission="draft.write", side_effect="write", approval_required=False,
    idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual", version="5",
)
def draft_pricing_apply(request: DraftPricingRequest, scope: Annotated[DraftPricingCapabilityScope, Injected()],
                        execution: Annotated[AiExecutionContext, Injected()]) -> DraftPricingResult:
    del execution
    return pricing_summary(price_draft(request, product_store=scope.products, apply=True))


@ai_tool(
    name="draft_pricing_apply_batch",
    description=("批量应用多份草稿已核验的预览（最多 50 份），逐草稿原子保存售价并返回各自最新 updated_at，不重新计算。"
        "须先调用 draft_pricing_preview_batch 或 draft_pricing_preview 获取每份草稿的 preview_id，并保持相同核价参数；单份版本冲突或预览失效仅记录该项失败，不影响其他有效草稿保存。"),
    permission="draft.write", side_effect="write", approval_required=False,
    idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual", version="1",
)
def draft_pricing_apply_batch(
    request: DraftPricingBatchApplyRequest,
    scope: Annotated[DraftPricingCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> DraftPricingBatchResult:
    del execution
    return pricing_batch_summary(apply_draft_pricing_batch(request, product_store=scope.products))


DRAFT_PRICING_AI_CAPABILITIES = (
    draft_pricing_preview,
    draft_pricing_preview_batch,
    draft_pricing_apply,
    draft_pricing_apply_batch,
)
__all__ = [
    "DraftPricingCapabilityScope",
    "DRAFT_PRICING_AI_CAPABILITIES",
    "draft_pricing_preview",
    "draft_pricing_preview_batch",
    "draft_pricing_apply",
    "draft_pricing_apply_batch",
]
