"""订单收货仓与跨境履约工具，写入统一经过原生审批和领域版本检查。"""

import json
from typing import Annotated

from erp_web.runtime_units.order_capability_support import OrderCapabilityScope, call, order_facts, snapshot, verify
from erp_web.schemas.ai_tools import ToolApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.fulfillment import BusServices, FulfillmentView
from erp_web.schemas.order_capabilities import OrderRequest, OrderFulfillmentRequest, OrderServicesRequest
from erp_web.schemas.receiving_warehouse import ReceivingWarehouseRequest, ReceivingWarehouseView
from erp_web.schemas.order_capabilities import OrderDataResult
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError


def _receiving_snapshot(request: ReceivingWarehouseRequest, scope: OrderCapabilityScope) -> ToolApprovalSnapshot:
    current = call(scope.fulfillment().receiving.detail, request.order_id)
    candidate = next((row for row in current["candidates"]
                      if row["id"] == request.address_id and row["section_id"] == request.section_id), None)
    if not candidate or candidate["revision"] != request.address_revision or current["revision"] != request.revision:
        raise BusinessCapabilityError("ORDER_RECEIVING_STALE", "收货仓候选或版本已变化，请重新读取。")
    facts = {**order_facts(request.order_id, scope), "receiving": current,
             "bus_identity": scope.fulfillment().client.identity()}
    suffix = '；同时核实并归档原作废报单，重选服务并明确提交后才报单' if current.get('cancelled_fulfillment') else ''
    return snapshot(f"确认收货仓及本单承接能力：{candidate['warehouse_name']}，{json.dumps(candidate['address'], ensure_ascii=False)}{suffix}", request, facts)


def _fulfillment_snapshot(request: OrderFulfillmentRequest, scope: OrderCapabilityScope) -> ToolApprovalSnapshot:
    service = scope.fulfillment()
    current = call(service.detail, request.order_id)
    if current["revision"] != request.revision:
        raise BusinessCapabilityError("ORDER_FULFILLMENT_STALE", "履约资料已变化，请重新读取后操作。")
    facts = {**order_facts(request.order_id, scope), "fulfillment": current,
             "bus_identity": service.client.identity()}
    actions = {"fetch-label": "获取并保存平台面单", "label": "保存面单", "parcels": "保存国内包裹",
               "plan": "保存本单服务方案", "pause": "暂停自动预报并进入五分钟编辑窗口", "resume": "结束编辑并恢复履约",
               "submit": "提交跨境仓库报单", "sync": "查询原仓库单并保存状态",
               "associate": "关联平台订单至仓库单", "retry": "重试履约操作", "cancel": "请求取消仓库单"}
    label = (f"{actions[request.action]}；仓库：{current['warehouse_name']}；仓库单：{current['crossborderbus_order_id']}；"
             f"服务：{json.dumps(current['selected_services'], ensure_ascii=False)}；"
             f"包裹数：{len(current['parcels'])}，商品数量：{sum(p['quantity'] for p in current['parcels'])}；"
             "保存资料可能推进已启用的自动预报或更新已创建仓库单；提交/取消回执不代表远端完成")
    return snapshot(label, request, facts)


@ai_tool(name="order_receiving_warehouse_read", description="读取本订单收货仓、真实候选地址、承接规则和版本。查询哪些订单需要重新绑定时按 needs_rebind 统计；binding_stale 仅表示已有绑定失效，warehouse=null 表示尚未绑定，cancelled_fulfillment 仅表示履约取消，均不能单独当作重绑待办。平台已取消、已发货、已送达不列入重绑待办。needs_rebind=true 仍须检查 can_confirm、locked、reserved 和 blocked_reason；不允许操作时说明阻断，不提议直接重绑。锁定原因按实际回执说明，不从 has_purchases 推断。只能使用返回候选，不能猜测仓库承接能力。", permission="order.read")
def order_receiving_warehouse_read(request: OrderRequest, scope: Annotated[OrderCapabilityScope, Injected()]) -> OrderDataResult[ReceivingWarehouseView]:
    return OrderDataResult(data=ReceivingWarehouseView.model_validate(call(scope.fulfillment().receiving.detail, request.order_id)))


@ai_tool(name="order_receiving_warehouse_update", description="审批后从真实候选确定收货仓及报单绑定。使用最新 revision、address_revision、delivery_key。物流配置变更或原履约取消导致 binding_stale 时，已有采购也可重新选择仓库和地址，但须核对包裹收货安排并设置 existing_purchase_confirmed；该确认不能绕过采购处理中、已有预报或未知结果的锁定。保存取消履约的新绑定时自动核实并归档原作废报单，必须携带当前 fulfillment_revision；未取消且配置未变化的已采购订单不能任意换仓。重绑后须重新保存报单服务，原采购快照不改写。confirmed 仅是业务参数，不能替代审批。",
         permission="order.fulfillment", side_effect="write", approval_required=True, approval_snapshot=_receiving_snapshot,
         idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual")
def order_receiving_warehouse_update(request: ReceivingWarehouseRequest, scope: Annotated[OrderCapabilityScope, Injected()], execution: Annotated[AiExecutionContext, Injected()]) -> OrderDataResult[ReceivingWarehouseView]:
    verify(execution, "order_receiving_warehouse_update", _receiving_snapshot(request, scope))
    return OrderDataResult(data=ReceivingWarehouseView.model_validate(call(scope.fulfillment().receiving.save, request.model_dump(mode="json"))))


@ai_tool(name="order_fulfillment_read", description="读取订单履约资料、方案、包裹、面单、阻断原因及 revision。返回本地事实，不触发远端查询或报单；未知创建结果须使用 sync 核验，不能重报。", permission="order.read")
def order_fulfillment_read(request: OrderRequest, scope: Annotated[OrderCapabilityScope, Injected()]) -> OrderDataResult[FulfillmentView]:
    return OrderDataResult(data=FulfillmentView.model_validate(call(scope.fulfillment().detail, request.order_id)))


@ai_tool(name="order_fulfillment_services", description="按收货仓或现有履约方案返回的渠道和仓库 ID 查询合作服务，不能猜测 service_ids 或金额。", permission="order.read")
def order_fulfillment_services(request: OrderServicesRequest, scope: Annotated[OrderCapabilityScope, Injected()]) -> OrderDataResult[BusServices]:
    return OrderDataResult(data=BusServices.model_validate(call(scope.fulfillment().client.services, request.section_id, request.warehouse_id)))


@ai_tool(name="order_fulfillment_update", description="审批后执行订单履约操作。先读取当前 revision；plan 保存真实服务方案/备注，parcels 保存完整国内包裹列表，label 保存公开面单，fetch-label 获取平台面单，pause/resume 控制编辑，submit 报单，sync 查询原单，associate 关联平台，retry 重试明确失败，cancel 请求取消；已取消后须通过 order_receiving_warehouse_update 重新设置报单绑定，再选择服务并显式 submit。包裹须对应实际采购记录，不伪造单号、承接能力或数量。保存资料可能推进已有自动履约。提交、更新和取消由领域后台执行，返回本地状态不代表远端成功；不要主动循环轮询或重报。",
         permission="order.fulfillment", side_effect="write", approval_required=True, approval_snapshot=_fulfillment_snapshot,
         idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual")
def order_fulfillment_update(request: OrderFulfillmentRequest, scope: Annotated[OrderCapabilityScope, Injected()], execution: Annotated[AiExecutionContext, Injected()]) -> OrderDataResult[FulfillmentView]:
    verify(execution, "order_fulfillment_update", _fulfillment_snapshot(request, scope))
    return OrderDataResult(data=FulfillmentView.model_validate(call(scope.fulfillment().command, request.action, request.model_dump(mode="json", exclude={"action"}))))


ORDER_FULFILLMENT_AI_CAPABILITIES = (
    order_receiving_warehouse_read, order_receiving_warehouse_update,
    order_fulfillment_read, order_fulfillment_services, order_fulfillment_update,
)

__all__ = ["ORDER_FULFILLMENT_AI_CAPABILITIES"]
