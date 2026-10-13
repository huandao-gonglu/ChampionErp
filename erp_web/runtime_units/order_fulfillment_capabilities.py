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
    return snapshot(f"确认收货仓及本单承接能力：{candidate['warehouse_name']}，{json.dumps(candidate['address'], ensure_ascii=False)}", request, facts)


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


@ai_tool(name="order_receiving_warehouse_read", description="读取本订单收货仓、真实候选地址、承接规则及版本。只能使用返回候选；仓库承接能力不能猜测。", permission="order.read")
def order_receiving_warehouse_read(request: OrderRequest, scope: Annotated[OrderCapabilityScope, Injected()]) -> OrderDataResult[ReceivingWarehouseView]:
    return OrderDataResult(data=ReceivingWarehouseView.model_validate(call(scope.fulfillment().receiving.detail, request.order_id)))


@ai_tool(name="order_receiving_warehouse_update", description="审批后从已配置且匹配承接规则的真实候选确定采购收货仓，冻结地址。使用最新 revision、address_revision、delivery_key；已有采购或预报时沿用领域锁定约束。confirmed 仅是业务参数，不能替代服务端审批。",
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


@ai_tool(name="order_fulfillment_update", description="审批后执行订单履约操作。先读取当前 revision；plan 保存真实服务方案/备注，parcels 保存完整国内包裹列表，label 保存公开面单，fetch-label 获取平台面单，pause/resume 控制编辑，submit 报单，sync 查询原单，associate 关联平台，retry 重试明确失败，cancel 请求取消。包裹须对应实际采购记录，不伪造单号、承接能力或数量。保存资料可能推进已有自动履约。提交、更新和取消由领域后台执行，返回本地状态不代表远端成功；不要主动循环轮询或重报。",
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
