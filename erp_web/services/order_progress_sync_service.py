"""按订单阶段读取仍需跟进的采购与仓库状态；复用各领域快照和失败记录。"""

import logging
import time

from erp_web.schemas.order_procurement import PurchaseRecord
from erp_web.schemas.orders import OrderView
from erp_web.services.alibaba_purchase_assignment import supported
from erp_web.services.alibaba_purchase_sync_service import sync_purchase
from erp_web.services.external_request_context import current_operation_id, request_operation

logger = logging.getLogger(__name__)


def sync_order_progress(procurement, fulfillment, config_provider, order_id, checkpoint, *, cancel=None):
    detail = procurement.detail(order_id)
    order = OrderView.model_validate(detail["order"])
    # 平台已发货后只跟进平台状态，不再查询前序采购物流和仓库报单。
    if order.state in {"shipped", "delivered"}:
        return True

    value = fulfillment.store.get(order_id) if fulfillment.store.tracked(order) else None
    # 当前报单已有远端编号才算确认成功；待核实或已归档报单不结束国内物流查询。
    purchases = [] if value and value["crossborderbus_order_id"] else detail["purchases"]
    complete = True
    queried = set()
    for record in purchases:
        if record["status"] != "purchased" or not supported(PurchaseRecord.model_validate(record)):
            continue
        number = record['purchase_order_number']
        if number in queried:
            continue
        queried.add(number)
        checkpoint()
        try:
            # 每笔查询限时短于订单任务租约；沿用同一同步操作的请求审计关联。
            with request_operation("orders:purchase", operation_id=current_operation_id(), trigger="background",
                                   cancel=cancel, deadline_at=time.time() + 120):
                result = sync_purchase(procurement, fulfillment, config_provider,
                                       {"order_id": order_id, "record_id": record["id"]})
            for updated in result['purchases']:
                if (updated['status'] != 'purchased' or updated['purchase_order_number'] != number
                        or not supported(PurchaseRecord.model_validate(updated))):
                    continue
                progress = updated.get("progress") or {}
                if progress.get("error") or (progress.get("data") or {}).get("logistics_warning"):
                    complete = False
        except Exception:
            # 单笔失败不阻断其他采购及仓库读取；原始异常可能含上游敏感信息。
            logger.warning("本轮采购状态未完成刷新，保留已保存结果")
            complete = False
    checkpoint()
    if fulfillment.store.tracked(order):
        # 采购同步可能增补包裹并更新版本，仓库查询须使用最新记录。
        value = fulfillment.store.get(order_id)
        if not value["busy"] and (value["crossborderbus_order_id"] or value["create_unknown"]):
            try:
                with request_operation("orders:fulfillment", operation_id=current_operation_id(), trigger="background",
                                       cancel=cancel, deadline_at=time.time() + 120):
                    result = fulfillment.sync(order_id, value["revision"])
                if result["error_message"]:
                    complete = False
            except Exception:
                logger.warning("本轮仓库状态未完成刷新，保留已保存结果")
                complete = False
    return complete
