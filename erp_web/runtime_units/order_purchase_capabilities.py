"""1688 采购工具：预览、原生审批提交和原请求核验共用现有采购服务。"""

import json
from typing import Annotated

from erp_web.runtime_units.order_capability_support import OrderCapabilityScope, call, order_facts, verify
from erp_web.schemas.ai_tools import ToolApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.alibaba_self_purchase import (
    AlibabaSelfPurchaseOptions, AlibabaBatchPurchaseOptions, AlibabaPurchaseAddresses,
    AlibabaParsedPurchaseAddress, ParsePurchaseAddressRequest, SelfPurchasePreviewRequest,
    BatchPurchasePreviewRequest, SelfPurchaseCreateRequest, SelfPurchasePayRequest,
    SelfPurchaseRecordRequest, SelfPurchasePaymentStatusRequest,
)
from erp_web.schemas.order_capabilities import (
    OrderRequest, OrderLineRequest, PurchaseResult, BatchPurchaseResult, PaymentStatusResult, CashierResult,
)
from erp_web.schemas.order_capabilities import OrderDataResult
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError


def _purchase_snapshot(request, scope, *, payment):
    facts = call(scope.purchase().approval_facts, request.preview_id)
    facts["sales_order"] = order_facts(facts["binding"]["order_id"], scope)
    record = facts["record"]
    view = record["preview"]
    channel = record["pay_channel"] if payment else request.pay_channel
    if channel not in view["pay_channels"]:
        raise BusinessCapabilityError("ORDER_PAYMENT_CHANNEL_INVALID", "支付渠道不在冻结预览的可用范围内。")
    if payment and request.confirmed_total_fen != view["total_fen"]:
        raise BusinessCapabilityError("ORDER_PAYMENT_AMOUNT_CHANGED", "确认金额与冻结预览不同。")
    products = view.get("items") or [{"candidate": view["candidate"], "quantity": view["quantity"]}]
    items = [{"商品": item["candidate"]["offer_id"], "规格": item["candidate"]["specification"],
              "SKU": item["candidate"]["sku_id"], "数量": item["quantity"]} for item in products]
    action = "支付（支付宝直接扣款）" if payment and channel == "alipay" else "支付（先采后付）" if payment else "创建采购订单（本次不付款）"
    summary = (f"{action}；销售订单：{facts['binding']['order_id']}，状态：{facts['sales_order']['order']['state']}；1688 单号：{', '.join(record['order_numbers']) or '尚未创建'}；"
               f"渠道：{channel}；商品 {view['goods_fen']} 分 + 运费 {view['shipping_fen']} 分，合计人民币 {view['total_fen']} 分；"
               f"收货：{view['recipient']}，{view['phone']}，{view['address']}；"
               f"商品：{json.dumps(items, ensure_ascii=False)}")
    # 不截断支付对象；超长批量应缩小分组后重新预览，确保审批内容可完整查看。
    if len(summary) > 2000:
        raise BusinessCapabilityError("ORDER_APPROVAL_TOO_LARGE", "采购审批商品过多，请减少本次采购商品后重新预览。")
    return ToolApprovalSnapshot(summary=summary, canonical_payload={"request": request.model_dump(mode="json"), "facts": facts})


def _create_snapshot(request: SelfPurchaseCreateRequest, scope: OrderCapabilityScope) -> ToolApprovalSnapshot:
    return _purchase_snapshot(request, scope, payment=False)


def _pay_snapshot(request: SelfPurchasePayRequest, scope: OrderCapabilityScope) -> ToolApprovalSnapshot:
    return _purchase_snapshot(request, scope, payment=True)


@ai_tool(name="order_alibaba_options", description="读取订单商品行可用的 1688 采购候选、剩余数量、已冻结收货仓及本行已提交采购记录。缺少上架来源证据会返回阻断原因，不允许猜补 SKU/specId。", permission="order.read")
def order_alibaba_options(request: OrderLineRequest, scope: Annotated[OrderCapabilityScope, Injected()]) -> OrderDataResult[AlibabaSelfPurchaseOptions]:
    return OrderDataResult(data=call(scope.purchase().options, request.order_id, request.line_key))


@ai_tool(name="order_alibaba_batch_options", description="读取一笔销售订单各商品行的批量采购候选及已提交记录；用于选择真实 line_key、candidate_id 和数量。", permission="order.read")
def order_alibaba_batch_options(request: OrderRequest, scope: Annotated[OrderCapabilityScope, Injected()]) -> OrderDataResult[AlibabaBatchPurchaseOptions]:
    return OrderDataResult(data=call(scope.purchase().batch_options, request.order_id))


@ai_tool(name="order_alibaba_addresses", description="读取该订单商品可用的 1688 收货地址候选。Yandex 应使用已确定收货仓及 receiving_revision，不覆盖仓库地址。", permission="order.read")
def order_alibaba_addresses(request: OrderLineRequest, scope: Annotated[OrderCapabilityScope, Injected()]) -> OrderDataResult[AlibabaPurchaseAddresses]:
    return OrderDataResult(data=call(scope.purchase().addresses, request.order_id, request.line_key))


@ai_tool(name="order_alibaba_parse_address", description="仅在本地从用户提供的地址文本解析联系人、电话和行政区域，返回缺失提示；解析不代表用户确认，不保存或下单。", permission="order.read")
def order_alibaba_parse_address(request: ParsePurchaseAddressRequest, scope: Annotated[OrderCapabilityScope, Injected()]) -> OrderDataResult[AlibabaParsedPurchaseAddress]:
    return OrderDataResult(data=call(scope.purchase().parse_address, request.model_dump(mode="json")))


@ai_tool(name="order_alibaba_preview", description="按已读取的真实采购候选、数量及确认收货地址实时预览，返回十分钟有效的冻结记录 ID、金额（人民币分）、渠道。不会创建或支付，但会使交叠旧预览失效。先核对返回的商品、数量、仓库和总额，再请求创建审批。",
         permission="order.procurement", side_effect="write", approval_required=False,
         idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual")
def order_alibaba_preview(request: SelfPurchasePreviewRequest, scope: Annotated[OrderCapabilityScope, Injected()], execution: Annotated[AiExecutionContext, Injected()]) -> PurchaseResult:
    return PurchaseResult.model_validate(call(scope.purchase().preview, request.model_dump(mode="json")))


@ai_tool(name="order_alibaba_batch_preview", description="预览同一销售订单最多 50 个商品行的 1688 批量采购，按平台真实结果分组。每组独立审批创建和支付；任何创建或支付结果未知时停止后续组，查询原请求，不能重新预览绕过防重。",
         permission="order.procurement", side_effect="write", approval_required=False,
         idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual")
def order_alibaba_batch_preview(request: BatchPurchasePreviewRequest, scope: Annotated[OrderCapabilityScope, Injected()], execution: Annotated[AiExecutionContext, Injected()]) -> BatchPurchaseResult:
    return BatchPurchaseResult.model_validate(call(scope.purchase().batch_preview, request.model_dump(mode="json")))


@ai_tool(name="order_alibaba_create", description="审批后根据冻结 preview_id 创建 1688 采购订单并回填原销售订单采购记录。本工具不付款。价格、渠道、来源或收货仓变化会拒绝；unknown/creating 不等于失败，只能核验原请求，禁止换预览重复下单。ok 仅为请求处理成功，实际结果看 record.state、order_numbers 和 message。",
         permission="order.purchase", side_effect="write", approval_required=True, approval_snapshot=_create_snapshot,
         idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual")
def order_alibaba_create(request: SelfPurchaseCreateRequest, scope: Annotated[OrderCapabilityScope, Injected()], execution: Annotated[AiExecutionContext, Injected()]) -> PurchaseResult:
    verify(execution, "order_alibaba_create", _create_snapshot(request, scope))
    return PurchaseResult.model_validate(call(scope.purchase_command, "create", request.model_dump(mode="json")))


@ai_tool(name="order_alibaba_payment_status", description="只读检查指定渠道的免密签约；ready 才可支付。签约链接交给用户操作，不代表已经扣款。", permission="order.read")
def order_alibaba_payment_status(request: SelfPurchasePaymentStatusRequest, scope: Annotated[OrderCapabilityScope, Injected()]) -> PaymentStatusResult:
    return PaymentStatusResult.model_validate(call(scope.purchase().payment_status, request.model_dump(mode="json")))


@ai_tool(name="order_alibaba_pay", description="单独审批后对已创建且关联的 1688 原采购订单发起实际支付。confirmed_total_fen 必须等于冻结总额；支付宝直接扣款，先采后付沿冻结渠道执行，不能覆盖渠道。payment.state/message 为实际支付回执；unknown/rejected 等状态不能报支付成功，也不能重发扣款。",
         permission="order.payment", side_effect="write", approval_required=True, approval_snapshot=_pay_snapshot,
         idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual")
def order_alibaba_pay(request: SelfPurchasePayRequest, scope: Annotated[OrderCapabilityScope, Injected()], execution: Annotated[AiExecutionContext, Injected()]) -> PurchaseResult:
    verify(execution, "order_alibaba_pay", _pay_snapshot(request, scope))
    return PurchaseResult.model_validate(call(scope.purchase_command, "pay", request.model_dump(mode="json")))


@ai_tool(name="order_alibaba_reconcile", description="查询原 preview_id 对应的采购请求并保存核验回执、恢复采购关联；不重新创建、不支付。未查到不等于创建失败，仍待付款不等于原支付失败；不要自动反复查询。",
         permission="order.procurement", side_effect="write", approval_required=False,
         idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual")
def order_alibaba_reconcile(request: SelfPurchaseRecordRequest, scope: Annotated[OrderCapabilityScope, Injected()], execution: Annotated[AiExecutionContext, Injected()]) -> PurchaseResult:
    return PurchaseResult.model_validate(call(scope.purchase_command, "reconcile", request.model_dump(mode="json")))


@ai_tool(name="order_alibaba_cashier", description="获取原采购订单的官方收银台链接供用户打开；不操作网页、不付款。", permission="order.read")
def order_alibaba_cashier(request: SelfPurchaseRecordRequest, scope: Annotated[OrderCapabilityScope, Injected()]) -> CashierResult:
    return CashierResult.model_validate(call(scope.purchase().cashier, request.model_dump(mode="json")))


ORDER_PURCHASE_AI_CAPABILITIES = (
    order_alibaba_options, order_alibaba_batch_options, order_alibaba_addresses,
    order_alibaba_parse_address, order_alibaba_preview, order_alibaba_batch_preview,
    order_alibaba_create, order_alibaba_payment_status, order_alibaba_pay,
    order_alibaba_reconcile, order_alibaba_cashier,
)

__all__ = ["ORDER_PURCHASE_AI_CAPABILITIES"]
