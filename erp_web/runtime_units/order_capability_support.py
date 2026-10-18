"""订单能力的可信依赖与审批事实；不管理 Agent 执行或审批生命周期。"""

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from erp_web.schemas.ai_tools import ToolApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.services.alibaba_api_client import AlibabaApiError
from erp_web.services.alibaba_self_purchase_service import AlibabaSelfPurchaseService
from erp_web.services.fulfillment_service import FulfillmentService
from erp_web.services.order_notification_service import OrderNotificationService
from erp_web.services.order_procurement_service import OrderProcurementService
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.tool_approval import verify_execution_approval
from erp_web.stores.order_address_note_store import OrderAddressNoteStore


@dataclass(frozen=True)
class OrderCapabilityScope:
    orders: Callable[[], OrderNotificationService]
    procurement: Callable[[], OrderProcurementService]
    fulfillment: Callable[[], FulfillmentService]
    purchase: Callable[[], AlibabaSelfPurchaseService]
    summary: Callable[[], dict[str, Any]]
    procurement_command: Callable[[str, dict], dict]
    purchase_command: Callable[[str, dict], dict]
    purchase_query: Callable[[dict], dict]
    purchase_sync: Callable[[dict], dict]

    def notes(self) -> OrderAddressNoteStore:
        return OrderAddressNoteStore(self.orders().store)


def call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ExternalRequestBlocked:
        raise BusinessCapabilityError("ORDER_REQUEST_BLOCKED", "请求已中断，请到系统设置的中断与恢复核对；不要重复提交。") from None
    except AlibabaApiError as exc:
        raise BusinessCapabilityError("ORDER_PLATFORM_ERROR", str(exc)) from None
    except (ValueError, PermissionError) as exc:
        raise BusinessCapabilityError("ORDER_INVALID_REQUEST", str(exc)) from None


def operation_key(execution: AiExecutionContext) -> str:
    value = execution.idempotency_context.get("operation_key")
    if not value:
        raise BusinessCapabilityError("ORDER_OPERATION_KEY_REQUIRED", "订单操作缺少可信提交身份。")
    return "ai-order-" + hashlib.sha256(value.encode()).hexdigest()


def order_facts(order_id: str, scope: OrderCapabilityScope) -> dict:
    detail = call(scope.procurement().detail, order_id)
    order = detail["order"]
    # 查询时间和物流轮询不改变审批；身份、商品、来源、数量、采购记录仍受保护。
    return {
        "order": {key: order.get(key) for key in (
            "id", "platform", "account_id", "order_id", "state", "items", "delivery", "handover",
        )},
        "lines": [{
            "selection": line["selection"],
            "remaining_quantity": line["remaining_quantity"],
        } for line in detail["lines"]],
        "purchases": [{key: value for key, value in record.items() if key != "progress"} for record in detail["purchases"]],
    }


def snapshot(label: str, request, facts: dict) -> ToolApprovalSnapshot:
    payload = request.model_dump(mode="json")
    order = facts.get("order", {})
    identity = f"{order.get('platform', '')} {order.get('order_id') or payload.get('order_id', '')}"
    summary = f"{label}：{identity}；{json.dumps(payload, ensure_ascii=False)}"
    if len(summary) > 2000:
        raise BusinessCapabilityError("ORDER_APPROVAL_TOO_LARGE", "本次变更超出审批展示范围，请缩小操作范围后重试。")
    return ToolApprovalSnapshot(
        summary=summary,
        canonical_payload={"request": payload, "facts": facts},
    )


def verify(execution: AiExecutionContext, name: str, value: ToolApprovalSnapshot) -> None:
    verify_execution_approval(execution, snapshot=value, capability_name=name,
                              capability_version="1", stale_code="ORDER_APPROVAL_STALE")
