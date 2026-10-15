"""订单中心查询、采购登记、地址备注和通知操作的领域工具。"""

from typing import Annotated, Any

from erp_web.runtime_units.order_capability_support import (
    OrderCapabilityScope, call, operation_key, order_facts, snapshot, verify,
)
from erp_web.schemas.ai_tools import ToolApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.alibaba_orders import AlibabaPurchaseQueryResult, PurchaseQueryRequest, PurchaseSyncRequest
from erp_web.schemas.order_address_notes import AddressNoteView, AddressNoteWrite
from erp_web.schemas.order_capabilities import (
    OrderRequest, OrderSyncRequest, OrderNotificationRequest,
    OrderPurchaseRecordRequest, OrderAddressRequest,
)
from erp_web.schemas.order_procurement import OrderDetail, SelectSourceRequest, CancelPurchaseRequest
from erp_web.schemas.order_capabilities import OrderDataResult, OrderInput
from erp_web.services.ai_tool_declaration import Injected, ai_tool


def _source_snapshot(request: SelectSourceRequest, scope: OrderCapabilityScope) -> ToolApprovalSnapshot:
    facts = order_facts(request.order_id, scope)
    selected = next((line["selection"] for line in facts["lines"] if line["selection"]["line_key"] == request.line_key), {})
    candidate = next((row for row in selected.get("candidates", []) if row["id"] == request.candidate_id), {})
    source = request.source.model_dump() if request.source else candidate.get("source", {})
    return snapshot(f"确认采购来源（不会下单）：{source.get('specification', '')}，{source.get('product_url', '')}", request, facts)


def _record_snapshot(request: OrderPurchaseRecordRequest, scope: OrderCapabilityScope) -> ToolApprovalSnapshot:
    facts = order_facts(request.order_id, scope)
    facts["receiving"] = call(scope.fulfillment().receiving.detail, request.order_id)
    return snapshot("登记已实际采购的单号与数量（不会下单）", request, facts)


def _cancel_snapshot(request: CancelPurchaseRequest, scope: OrderCapabilityScope) -> ToolApprovalSnapshot:
    return snapshot("作废本地采购登记（不会取消远端订单）", request, order_facts(request.order_id, scope))


def _sync_snapshot(request: PurchaseSyncRequest, scope: OrderCapabilityScope) -> ToolApprovalSnapshot:
    return snapshot("刷新采购进度并按实际归属合入国内包裹；已启用自动预报时可能推进履约", request,
                    order_facts(request.order_id, scope))


@ai_tool(name="order_detail_read", description="按 platform_orders_query 返回的 items.id 或页面 order_id 读取本地订单详情、商品行、采购来源候选、版本、剩余数量和采购进度。order_id 是 ERP 订单 ID，不是平台单号。不会查询远端；来源不能按标题或 SKU 编码猜测。",
         permission="order.read")
def order_detail_read(request: OrderRequest, scope: Annotated[OrderCapabilityScope, Injected()]) -> OrderDataResult[OrderDetail]:
    return OrderDataResult(data=OrderDetail.model_validate(call(scope.procurement().detail, request.order_id)))


@ai_tool(name="orders_summary_read", description="读取当前授权店铺订单通知摘要、未读提醒与同步状态，不发起同步，不包含回调凭据。同步提交后可按用户要求查看此状态。", permission="order.read")
def orders_summary_read(request: OrderInput, scope: Annotated[OrderCapabilityScope, Injected()]) -> OrderDataResult[dict[str, Any]]:
    return OrderDataResult(data=call(scope.summary))


@ai_tool(name="orders_sync", description="显式提交指定平台订单同步，由既有后台订单任务更新平台快照及采购、仓库进度。返回入队结果，不表示同步完成；不要自动反复提交或轮询。",
         permission="order.sync", side_effect="write", approval_required=False,
         idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual")
def orders_sync(request: OrderSyncRequest, scope: Annotated[OrderCapabilityScope, Injected()], execution: Annotated[AiExecutionContext, Injected()]) -> OrderDataResult[dict[str, Any]]:
    return OrderDataResult(data=call(scope.orders().sync, request.platform, automatic=False))


@ai_tool(name="order_notification_update", description="retry 使用 platform_orders_query(include_notifications=true) 返回的 notifications[].id 作为 event_id 重试失败通知；acknowledge 使用 orders_summary_read 返回的 latest_alert_id 作为 through_id 将此前提醒标为已读。通知与提醒是不同编号，不能混用或猜测。",
         permission="order.sync", side_effect="write", approval_required=False,
         idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual")
def order_notification_update(request: OrderNotificationRequest, scope: Annotated[OrderCapabilityScope, Injected()], execution: Annotated[AiExecutionContext, Injected()]) -> OrderDataResult[dict[str, bool]]:
    service = scope.orders()
    method = service.store.retry if request.action == "retry" else service.store.acknowledge
    identifier = request.event_id if request.action == "retry" else request.through_id
    call(method, identifier, service.accounts())
    return OrderDataResult(data={"ok": True})


@ai_tool(name="order_source_select", description="依据订单详情的候选 ID 或用户明确提供的真实来源确认采购来源。使用 selection.revision；不能伪造规格直达验证、发布关联或凭空补齐采购事实。审批后复用来源版本和已采购数量保护。",
         permission="order.procurement", side_effect="write", approval_required=True, approval_snapshot=_source_snapshot,
         idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual")
def order_source_select(request: SelectSourceRequest, scope: Annotated[OrderCapabilityScope, Injected()], execution: Annotated[AiExecutionContext, Injected()]) -> OrderDataResult[OrderDetail]:
    verify(execution, "order_source_select", _source_snapshot(request, scope))
    return OrderDataResult(data=OrderDetail.model_validate(call(scope.procurement_command, "select-source", request.model_dump(mode="json"))))


@ai_tool(name="order_purchase_record", description="登记已实际采购的订单号及数量，不创建远端采购。必须有真实采购依据，使用当前来源 revision 及已确认收货仓；request_id 由服务端生成。登记后沿用采购状态同步。",
         permission="order.procurement", side_effect="write", approval_required=True, approval_snapshot=_record_snapshot,
         idempotency="required", idempotency_keys=("operation_key",), recovery_policy="idempotent")
def order_purchase_record(request: OrderPurchaseRecordRequest, scope: Annotated[OrderCapabilityScope, Injected()], execution: Annotated[AiExecutionContext, Injected()]) -> OrderDataResult[OrderDetail]:
    verify(execution, "order_purchase_record", _record_snapshot(request, scope))
    return OrderDataResult(data=OrderDetail.model_validate(call(scope.procurement_command, "record-purchase",
        {**request.model_dump(mode="json"), "request_id": operation_key(execution)})))


@ai_tool(name="order_purchase_cancel", description="按采购 record_id 作废当前 SKU 的本地采购并释放数量，同时移除该记录的国内包裹分配；同单其他 SKU 不受影响，不取消远端采购订单。手工及自动采购均可在报单前作废；报单已开始或结果待核实时禁止作废。",
         permission="order.procurement", side_effect="write", approval_required=True, approval_snapshot=_cancel_snapshot,
         idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual")
def order_purchase_cancel(request: CancelPurchaseRequest, scope: Annotated[OrderCapabilityScope, Injected()], execution: Annotated[AiExecutionContext, Injected()]) -> OrderDataResult[OrderDetail]:
    verify(execution, "order_purchase_cancel", _cancel_snapshot(request, scope))
    return OrderDataResult(data=OrderDetail.model_validate(call(scope.procurement_command, "cancel-purchase", request.model_dump(mode="json"))))


@ai_tool(name="order_purchase_query", description="按本单已登记采购 record_id 只读查询 1688 订单或物流；不保存包裹、不下单、不付款。按需查询，不自动轮询。", permission="order.read")
def order_purchase_query(request: PurchaseQueryRequest, scope: Annotated[OrderCapabilityScope, Injected()]) -> OrderDataResult[AlibabaPurchaseQueryResult]:
    return OrderDataResult(data=call(scope.purchase_query, request.model_dump(mode="json")))


@ai_tool(name="order_purchase_sync", description="刷新采购状态和物流，按真实商品与数量归属保存国内包裹。同一采购单复用查询；冲突不覆盖人工分配。可能推进已开启的自动履约，先审批。",
         permission="order.procurement", side_effect="write", approval_required=True, approval_snapshot=_sync_snapshot,
         idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual")
def order_purchase_sync(request: PurchaseSyncRequest, scope: Annotated[OrderCapabilityScope, Injected()], execution: Annotated[AiExecutionContext, Injected()]) -> OrderDataResult[OrderDetail]:
    verify(execution, "order_purchase_sync", _sync_snapshot(request, scope))
    return OrderDataResult(data=OrderDetail.model_validate(call(scope.purchase_sync, request.model_dump(mode="json"))))


@ai_tool(name="order_address_note_read", description="读取本单实际交货地址的本地共享备注与版本，不改变平台地址。shipment_id、address 来自订单交货快照。", permission="order.read")
def order_address_note_read(request: OrderAddressRequest, scope: Annotated[OrderCapabilityScope, Injected()]) -> OrderDataResult[AddressNoteView]:
    return OrderDataResult(data=call(scope.notes().read, request.order_id, request.shipment_id, scope.orders().accounts(), address=request.address))


@ai_tool(name="order_address_note_update", description="保存本地交货地址备注，沿用读取返回的 address_key 与 revision；不更改平台地址或收货仓地址，冲突时重读并核对，不强制覆盖。",
         permission="order.procurement", side_effect="write", approval_required=False,
         idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual")
def order_address_note_update(request: AddressNoteWrite, scope: Annotated[OrderCapabilityScope, Injected()], execution: Annotated[AiExecutionContext, Injected()]) -> OrderDataResult[AddressNoteView]:
    return OrderDataResult(data=call(scope.notes().save, request, scope.orders().accounts()))


ORDER_AI_CAPABILITIES = (
    order_detail_read, orders_summary_read, orders_sync, order_notification_update,
    order_source_select, order_purchase_record, order_purchase_cancel,
    order_purchase_query, order_purchase_sync, order_address_note_read, order_address_note_update,
)

__all__ = ["ORDER_AI_CAPABILITIES"]
