"""包装外尺寸估算只在副本上试算；用户确认后按商品版本写入缺失边长。"""
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, Protocol

from erp_web.international_shipping.tables import extra_small_size_excess
from erp_web.product_model.sku_model import positive_measurement
from erp_web.runtime_units.draft_pricing import DraftPricingStore, prepare_pricing_inputs
from erp_web.runtime_units.market_capability_support import load_draft
from erp_web.runtime_units.pricing_batch import calculate_sku_prices
from erp_web.schemas.ai_tools import ToolApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.draft_pricing import DraftPricingRequest
from erp_web.schemas.package_estimates import (
    FreightSample, FreightScenario, PackageEstimatesRequest, PackageFreightPreview, PackageFreightPreviewRequest,
)
from erp_web.schemas.product import ConfirmedEdgeEstimate
from erp_web.schemas.product_facts import ProductSkuFactsUpdateResult
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.tool_approval import verify_execution_approval

CONFIRM = "draft_package_estimates_confirm"
EDGE_LABELS = {"length_cm": "长", "width_cm": "宽", "height_cm": "高"}


class PackageEstimateStore(DraftPricingStore, Protocol):
    def update_product_sku_facts(
        self, payload: dict[str, Any], *, confirmed_estimates: dict[str, dict[str, ConfirmedEdgeEstimate]],
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PackageEstimateScope:
    products: PackageEstimateStore
    load_tariff: Callable[..., dict]


def _estimate_context(request, scope):
    draft, product = load_draft(scope.products, request.draft_id)
    if product["updated_at"] != request.expected_product_updated_at:
        raise BusinessCapabilityError("PACKAGE_ESTIMATE_STALE", "商品资料已更新，请重新读取并核对估算。")
    selected = {row["sku_id"] for row in draft["sku_items"] if row.get("selected")}
    rows = {row["id"]: row for row in product["sku_items"] if row.get("active", True)}
    for item in request.estimates:
        if item.sku_id not in selected or item.sku_id not in rows:
            raise BusinessCapabilityError("PACKAGE_ESTIMATE_SCOPE", "估算只能用于当前草稿已选且启用的 SKU。")
        row = rows[item.sku_id]
        if item.sku_name and item.sku_name != row.get("name"):
            raise BusinessCapabilityError("PACKAGE_ESTIMATE_SKU_NAME", "规格名称与 SKU ID 不匹配，请核对估算对象。")
        dims = row["package_dimensions"]
        for key in item.dimensions:
            if positive_measurement(dims.get(key)) is not None:
                raise BusinessCapabilityError("PACKAGE_DIMENSION_EXISTS", f"{item.sku_id} 已有包装{EDGE_LABELS[key]}，请保留已有值；错误资料需单独更正。")
    return draft, product, rows


def _review_snapshot(request, product, rows):
    names = "、".join(str(rows[item.sku_id].get("name") or item.sku_id)[:60] for item in request.estimates[:3])
    return ToolApprovalSnapshot(
        requires_user_confirmation=True,
        summary=f"将建议的包装外尺寸保存到 {names} 等 {len(request.estimates)} 个规格的商品资料，供引用该商品的草稿核价。",
        canonical_payload={"product_id": product["product_id"], "proposal": request.model_dump(mode="json")},
    )


def _prepare_review(request, scope):
    with scope.products.mutation_scope({"draft_id": request.draft_id}):
        _, product, rows = _estimate_context(request, scope)
        return _review_snapshot(request, product, rows)


@ai_tool(
    name=CONFIRM, permission="product.write", side_effect="write", approval_required=True,
    approval_snapshot=_prepare_review, idempotency="required", idempotency_keys=("operation_key",),
    recovery_policy="manual", version="1",
    description="将已选 SKU 缺失的包装外尺寸提交用户逐项核对。每条边填写厘米的 low、suggested、high 和该 SKU 的依据。相同商品的一批 SKU 合并提交。批准后仅保存建议值、区间及依据；拒绝后等待用户修改要求。先读取 product_read 中商品版本。不要用普通商品写工具保存推断。",
)
def draft_package_estimates_confirm(
    request: PackageEstimatesRequest, scope: Annotated[PackageEstimateScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ProductSkuFactsUpdateResult:
    with scope.products.mutation_scope({"draft_id": request.draft_id}):
        execution.bounded_timeout_seconds()
        _, product, rows = _estimate_context(request, scope)
        verify_execution_approval(execution, snapshot=_review_snapshot(request, product, rows),
                                  capability_name=CONFIRM, capability_version="1", stale_code="PACKAGE_ESTIMATE_STALE")
        changes = []
        origins = {}
        for item in request.estimates:
            changes.append({"sku_id": item.sku_id, "package_dimensions": {
                key: edge.suggested for key, edge in item.dimensions.items()
            }})
            origins[item.sku_id] = {key: {**edge.model_dump(), "basis": item.basis} for key, edge in item.dimensions.items()}
        result = scope.products.update_product_sku_facts(
            {"product_id": product["product_id"], "expected_updated_at": request.expected_product_updated_at, "changes": changes},
            confirmed_estimates=origins,
        )
        return ProductSkuFactsUpdateResult.model_validate(result)


def _size_check(item, dims, policy):
    weight = positive_measurement(dims.get("weight_kg"))
    if weight is not None and weight * 1000 > Decimal(policy["max_g"]):
        return "包装毛重已超过轻小件上限"
    low, high = [], []
    for key in EDGE_LABELS:
        edge = item.dimensions.get(key)
        if edge:
            low.append(Decimal(str(edge.low)))
            high.append(Decimal(str(edge.high)))
        elif (known := positive_measurement(dims.get(key))) is not None:
            low.append(known)
            high.append(known)
        else:
            return "缺少有效的包装边长"
    if extra_small_size_excess(low, policy):
        return "整个区间超过轻小件尺寸限制"
    crossed = extra_small_size_excess(high, policy)
    if crossed:
        return "区间跨过尺寸门槛，请核实" + "、".join(crossed)
    return "整个区间符合轻小件尺寸限制，重量、货值及渠道另行核对"


def _scenario_quote(endpoint, inputs, estimated_ids):
    quotes, errors = [], []
    result = calculate_sku_prices({"items": inputs})
    for item in result["items"]:
        sku_id = item["sku_id"]
        result_row = item["result"]
        if result_row.get("error"):
            errors.append(f"{sku_id}：{result_row['error']}")
        for issue in result_row.get("errors") or []:
            errors.append(f"{sku_id}：{issue.get('message', '')}")
        for quote in result_row.get("results") or []:
            market = quote.get("target_key") or f"{quote.get('platform', '')}:{quote.get('site', '')}"
            if not quote.get("ok"):
                errors.extend(f"{sku_id} {market}：{issue['message']}" for issue in quote.get("errors", []))
                continue
            if sku_id not in estimated_ids:
                continue
            basis = quote["calculation_basis"]
            destinations = basis.get("destination_shipping") or [{"basis": basis}]
            for destination in destinations:
                part = destination["basis"]
                evidence = part.get("shipping_evidence") or {}
                plan = evidence.get("fulfillment_plan") or {}
                quotes.append(FreightSample(
                    sku_id=sku_id, market="/".join(filter(None, [market, destination.get("site_id"), destination.get("logistic_type")])),
                    route=evidence.get("route") or ("手动运费" if part["shipping_quote_mode"] == "manual" else "自动报价"), warehouse=str(plan.get("publish_warehouse_id") or ""),
                    amount=float(part["shipping_amount"]), currency=part["shipping_currency"],
                ))
    return _scenario(endpoint, quotes, errors)


def _scenario(endpoint, quotes, errors):
    # 整组选仓仍覆盖全部已选 SKU，模型回执保留总数并限制明细体量。
    unique_errors = list(dict.fromkeys(errors))
    return FreightScenario(
        endpoint=endpoint, quote_count=len(quotes), quotes=quotes[:40],
        error_count=len(unique_errors), errors=[message[:240] for message in unique_errors[:20]],
    )


@ai_tool(
    name="draft_package_estimates_preview", permission="pricing.read",
    description="仅用于已选 SKU 缺失包装边长的估算，不接受已有有效值的边长，不用于查询已有运费的渠道明细。按草稿已保存的核价设置、全部已选 SKU、当前运费规则分别试算包装尺寸区间的两个端点，返回渠道、仓库和费用。需要时传 target_keys 限定市场。每端返回估算范围的报价总数、前 40 条报价及前 20 条异常；只比较已返回的明细。相同端点只算一次。结果只用于比较，不产生可应用的核价预览。缺重量或成本时仍可向用户展示尺寸建议。"
)
def draft_package_estimates_preview(
    request: PackageFreightPreviewRequest, scope: Annotated[PackageEstimateScope, Injected()],
) -> PackageFreightPreview:
    with scope.products.mutation_scope({"draft_id": request.draft_id}):
        draft, product, rows = _estimate_context(request, scope)
    pricing = DraftPricingRequest(draft_id=request.draft_id, target_keys=request.target_keys)
    targets = request.target_keys or [f"{t['platform']}:{t['site']}" for t in draft["target_sites"]]
    checks = {}
    if any(key.lower().startswith("yandex:") for key in targets):
        try:
            policy = scope.load_tariff("yandex")["content"]["policies"]["extra_small"]
        except (ValueError, FileNotFoundError):
            policy = None
        if policy:
            checks = {item.sku_id: _size_check(item, rows[item.sku_id]["package_dimensions"], policy)
                      for item in request.estimates}
    scenarios = []
    estimated_ids = {item.sku_id for item in request.estimates}
    for endpoint in ("low", "high"):
        if scenarios and all(edge.low == edge.high for item in request.estimates for edge in item.dimensions.values()):
            scenarios.append(scenarios[0].model_copy(update={"endpoint": "high"}))
            break
        for item in request.estimates:
            rows[item.sku_id]["package_dimensions"].update({key: getattr(edge, endpoint) for key, edge in item.dimensions.items()})
        try:
            inputs, _, _ = prepare_pricing_inputs(pricing, deepcopy(draft), product)
        except BusinessCapabilityError as exc:
            issues = (exc.details or {}).get("errors") or []
            errors = [str(issue["message"]) for issue in issues] or [str(exc)]
            scenarios.append(_scenario(endpoint, [], errors))
            continue
        scenarios.append(_scenario_quote(endpoint, inputs, estimated_ids))
    return PackageFreightPreview(yandex_extra_small_checks=checks, scenarios=scenarios)


PACKAGE_ESTIMATE_CAPABILITIES = (draft_package_estimates_preview, draft_package_estimates_confirm)
