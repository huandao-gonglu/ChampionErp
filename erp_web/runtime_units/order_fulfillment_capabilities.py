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
    return snapshot(
        f"确认收货仓及本单承接能力：{candidate['warehouse_name']}，{json.dumps(candidate['address'], ensure_ascii=False)}",
        request, facts, approval_tier="local",
    )


def _fulfillment_snapshot(request: OrderFulfillmentRequest, scope: OrderCapabilityScope) -> ToolApprovalSnapshot:
    service = scope.fulfillment()
    current = call(service.detail, request.order_id)
    if current["revision"] != request.revision:
        raise BusinessCapabilityError("ORDER_FULFILLMENT_STALE", "履约资料已变化，请重新读取后操作。")
    order, _ = service.order_detail(request.order_id)
    facts = {"order": order.model_dump(mode="json", include={"id", "platform", "account_id", "order_id", "state", "delivery", "handover"}), "fulfillment": current,
             "bus_identity": service.client.identity()}
    actions = {"fetch-label": "获取并保存平台面单", "label": "保存面单", "parcels": "保存国内包裹",
               "plan": "保存本单服务方案", "pause": "暂停自动预报并进入五分钟编辑窗口", "resume": "结束编辑并恢复履约",
               "submit": "提交跨境仓库报单", "sync": "查询原仓库单并保存状态",
               "associate": "关联平台订单至仓库单", "retry": "重试履约操作", "cancel": "请求取消仓库单"}
    parcels = [p.model_dump() for p in request.parcels] if request.action == "parcels" and request.parcels is not None else current["parcels"]
    included = [p for p in parcels if p["included"]]
    count = "待补齐" if any(p["quantity"] is None for p in included) else str(sum(p["quantity"] for p in included))
    if request.action == "parcels":
        actions["parcels"] = "保存并确认包裹清单" if request.confirm_parcels else "保存包裹草稿"
    label = (f"{actions[request.action]}；仓库：{current['warehouse_name']}；仓库单：{current['crossborderbus_order_id']}；"
             f"服务：{json.dumps(current['selected_services'], ensure_ascii=False)}；"
             f"包裹数：{len(included)}，商品数量：{count}；"
             "已确认清单按本单方案报单；提交/取消回执不代表远端完成")
    if request.action == 'plan' and current['fulfillment_status'] == 'CANCELLED':
        label = (f"保存本次报单方案：{request.plan.model_dump() if request.plan else None}；"
                 f"核实并归档原作废报单 {current['crossborderbus_order_id']}，保留收货仓绑定、面单和国内包裹；"
                 "保存后仍须明确提交才会重新报单")
    return snapshot(label, request, facts)


@ai_tool(name="order_receiving_warehouse_read", description="读取本订单收货仓、真实候选地址、承接规则和版本。查询哪些订单需要重新绑定时按 needs_rebind 统计；binding_stale 仅表示已有绑定失效，warehouse=null 表示尚未绑定，cancelled_fulfillment 仅表示履约取消，均不能单独当作重绑待办。平台已取消、已发货、已送达不列入重绑待办。needs_rebind=true 仍须检查 can_confirm、locked、reserved 和 blocked_reason；不允许操作时说明阻断，不提议直接重绑。锁定原因按实际回执说明，不从 has_purchases 推断。只能使用返回候选，不能猜测仓库承接能力。", permission="order.read")
def order_receiving_warehouse_read(request: OrderRequest, scope: Annotated[OrderCapabilityScope, Injected()]) -> OrderDataResult[ReceivingWarehouseView]:
    return OrderDataResult(data=ReceivingWarehouseView.model_validate(call(scope.fulfillment().receiving.detail, request.order_id)))


@ai_tool(name="order_receiving_warehouse_update", description="审批后从真实候选确定收货仓及报单绑定。使用最新 revision、address_revision、delivery_key。引用的物流配置变更导致 binding_stale 时，已有采购也可重新选择仓库和地址，但须核对包裹收货安排并设置 existing_purchase_confirmed；该确认不能绕过采购处理中、已有预报或未知结果的锁定。取消履约不使仓库绑定失效，保存绑定不归档或重启履约，无需填写 fulfillment_revision；配置未变化的已采购订单不能任意换仓。重绑后须重新保存报单服务，原采购快照不改写。confirmed 仅是业务参数，不能替代审批。",
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


@ai_tool(name="order_fulfillment_update", description="审批后执行订单履约操作。先读取当前 revision；plan 保存真实服务方案/备注，收货仓版本由程序从已核验的审批快照传入，无需填写 receiving_revision；parcels 保存完整国内包裹列表，label 保存公开面单，fetch-label 获取平台面单，pause/resume 控制编辑，submit 报单，sync 查询原单，associate 关联平台，retry 重试明确失败，cancel 请求取消；已取消后保留有效收货绑定，直接用 plan 保存本次服务方案；程序核实原报单已作废后归档旧单，仍须显式 submit 才会重报。只有引用配置失效时才先重新确认收货绑定。包裹直接属于订单，不绑定销售 SKU 或采购记录。parcels 提交完整清单，included 表示本次是否报单；quantity 是包裹商品件数，未知可先留空。confirm_parcels=true 明确确认本次清单，要求参与包裹件数齐全；确认后可能推进已开启的自动履约。不伪造采购来源、数量或仓库能力。提交、更新和取消由领域后台执行，返回本地状态不代表远端成功；不要主动循环轮询或重报。",
         permission="order.fulfillment", side_effect="write", approval_required=True, approval_snapshot=_fulfillment_snapshot,
         idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual")
def order_fulfillment_update(request: OrderFulfillmentRequest, scope: Annotated[OrderCapabilityScope, Injected()], execution: Annotated[AiExecutionContext, Injected()]) -> OrderDataResult[FulfillmentView]:
    current = _fulfillment_snapshot(request, scope)
    verify(execution, "order_fulfillment_update", current)
    body = request.model_dump(mode="json", exclude={"action", "receiving_revision"})
    if request.action == "plan":
        # 摘要校验保证这是批准时的同一份资料；不能在校验后重新取最新版本补入。
        receiving = current.canonical_payload["facts"]["fulfillment"]["receiving_warehouse"]
        if receiving:
            body["receiving_revision"] = receiving["revision"]
    return OrderDataResult(data=FulfillmentView.model_validate(call(scope.fulfillment().command, request.action, body)))


ORDER_FULFILLMENT_AI_CAPABILITIES = (
    order_receiving_warehouse_read, order_receiving_warehouse_update,
    order_fulfillment_read, order_fulfillment_services, order_fulfillment_update,
)

__all__ = ["ORDER_FULFILLMENT_AI_CAPABILITIES"]
