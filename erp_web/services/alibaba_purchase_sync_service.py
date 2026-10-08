"""采购登记后的单次同步与手动刷新；快照持久化，安全合并国内包裹。"""

import logging

from erp_web.schemas.alibaba_orders import PurchaseSyncRequest
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.schemas.orders import utc_iso
from erp_web.services.alibaba_api_client import AlibabaApiClient, AlibabaApiError, ORDER_DETAIL, LOGISTICS_INFO, LOGISTICS_TRACE, validate_order_number
from erp_web.services.alibaba_purchase_assignment import propose_parcels, supported
from erp_web.services.alibaba_purchase_query_service import normalize_order, normalize_parcels, attach_traces

logger = logging.getLogger(__name__)


def sync_purchase(procurement, fulfillment, config_provider, body, *, once=False, client_factory=AlibabaApiClient):
    request = PurchaseSyncRequest.model_validate(body)
    order_id, record_id = request.order_id, request.record_id
    accounts = procurement.accounts_provider
    record = procurement.store.purchase_record(order_id, record_id, accounts())
    if not supported(record):
        raise ValueError("仅支持同步 1688 采购记录")
    progress = procurement.store.purchase_progress
    started = progress.begin(order_id, record_id, accounts(), once=once)
    if started is None:
        return procurement.detail(order_id)
    generation, value = started
    try:
        config = config_provider()
        client = client_factory(config)
        number = validate_order_number(record.purchase_order_number)
        order = client.query(ORDER_DETAIL, number)
        status = normalize_order(order, number)
        checked_at = utc_iso()
        previous_data = value.get("data") or {}
        parcels = previous_data.get("logistics")
        logistics_checked_at = previous_data.get("logistics_checked_at", previous_data.get("checked_at", "") if parcels is not None else "")
        logistics = None
        warning = ""
        try:
            logistics = client.query(LOGISTICS_INFO, number)
            parcels = normalize_parcels(logistics, order)
            logistics_checked_at = utc_iso()
        except (AlibabaApiError, ExternalRequestBlocked) as exc:
            logistics = None
            reason = "请求已中断，请到授权配置的「中断与恢复」查看原因" if isinstance(exc, ExternalRequestBlocked) else str(exc)
            warning = "物流查询失败：" + reason
        if logistics is not None and parcels:
            try:
                attach_traces(parcels, client.query(LOGISTICS_TRACE, number), number)
            except (AlibabaApiError, ExternalRequestBlocked):
                warning = "已获取运单，物流轨迹暂不可用，可稍后刷新"
        if config_provider() != config:
            raise AlibabaApiError("1688 授权已变化，请重新刷新采购进度")
        value["data"] = {"ok": True, "record_id": record_id, "order_number": number, "checked_at": checked_at,
                         "order": status, "logistics": parcels, "logistics_warning": warning,
                         "logistics_checked_at": logistics_checked_at}
        if logistics is None:
            previous_state = (record.progress or {}).get("state")
            state = previous_state if previous_state in {"pending_assignment", "conflict", "manual", "locked"} else "synced"
            value.update(state=state, message="采购订单状态已更新，物流本次未更新", error="")
        else:
            _merge_parcels(procurement, fulfillment, order_id, record_id, generation, record, order, logistics, parcels, value)
        value["error"] = ""
    except ExternalRequestBlocked:
        value.update(state="error", error="1688 请求已中断，请到授权配置的「中断与恢复」查看原因", message="本次同步未完成")
    except ValueError as exc:
        value.update(state="error", error=str(exc), message="本次同步未完成")
    except Exception:
        # 不向日志写入可能包含授权、地址或上游响应的异常原文。
        logger.error("采购进度同步发生异常，采购登记已保留")
        value.update(state="error", error="采购同步失败，请稍后刷新", message="本次同步未完成")
    progress.finish(order_id, record_id, generation, accounts(), value)
    return procurement.detail(order_id)


def _merge_parcels(procurement, fulfillment, order_id, record_id, generation, record, order, logistics, parcels, value):
    progress = procurement.store.purchase_progress
    proposals, reason = propose_parcels(record, order, logistics, parcels)

    def guard(conn):
        current = progress.guard(conn, order_id, record_id, generation, procurement.accounts_provider())
        if progress.competing(conn, current):
            raise ValueError("同一采购订单和 SKU 已用于多条采购记录，请确认包裹分配")

    previous = value.get("resolution")
    current_parcels = fulfillment.detail(order_id)["parcels"]
    assigned = [p for p in current_parcels if p["purchase_record_id"] == record_id]
    def bills(rows):
        return sorted((p["company"], p["tracking_number"]) for p in rows or [])
    if previous and previous["parcels"] == assigned and bills(previous["logistics"]) == bills(parcels):
        value.update(state="manual", message="国内包裹已人工确认")
    elif proposals:
        value["state"], value["message"] = fulfillment.merge_purchase_parcels(order_id, record_id, proposals, guard)
    else:
        value.update(state="pending_assignment" if parcels else "synced", message=reason if parcels else "采购进度已更新，1688 暂未提供物流信息")
