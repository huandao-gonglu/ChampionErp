"""采购登记后的单次同步与手动刷新；快照持久化，安全合并国内包裹。"""

import logging
from copy import deepcopy

from erp_web.schemas.alibaba_orders import PurchaseSyncRequest
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.schemas.orders import utc_iso
from erp_web.services.alibaba_api_client import AlibabaApiError, alibaba_provider, validate_order_number
from erp_web.services.alibaba_purchase_assignment import propose_parcels, supported
from erp_web.services.alibaba_purchase_query_service import shipment_parcels, query_parcel_traces

logger = logging.getLogger(__name__)


def sync_purchases(procurement, fulfillment, config_provider, order_id, record_ids, *, once=False, client_factory=alibaba_provider):
    """采购回填后的整组同步失败不撤销已完成的采购。"""
    try:
        _sync_records(procurement, fulfillment, config_provider, order_id, record_ids,
                      once=once, client_factory=client_factory)
    except Exception:
        logger.error('批量采购已回填，采购进度同步未完成')


def sync_purchase(procurement, fulfillment, config_provider, body, *, once=False, client_factory=alibaba_provider):
    """刷新所选 1688 采购单的完整商品及物流快照。"""
    request = PurchaseSyncRequest.model_validate(body)
    _sync_records(procurement, fulfillment, config_provider, request.order_id,
                  [request.record_id], once=once, client_factory=client_factory)
    return procurement.detail(request.order_id)


def _sync_records(procurement, fulfillment, config_provider, order_id, record_ids, *, once, client_factory):
    """采购单先占位，再共用本次查询回执；写入时核对记录有效性。"""
    accounts = procurement.accounts_provider()
    records = {record_id: procurement.store.purchase_record(order_id, record_id, accounts)
               for record_id in dict.fromkeys(record_ids)}
    if any(not supported(record) for record in records.values()):
        raise ValueError("仅支持同步 1688 采购记录")
    started = procurement.store.purchase_progress.begin_many(order_id, records, accounts, once=once)
    config = None
    client = None
    results = {}

    class GroupQuery:
        def _read(self, operation, number):
            key = (operation, number)
            if key not in results:
                try:
                    results[key] = operation(number)
                except Exception as exc:
                    results[key] = exc
            result = results[key]
            if isinstance(result, Exception):
                raise result
            return deepcopy(result)

        def get_order(self, number):
            return self._read(client.get_order, number)

        def get_shipments(self, number):
            return self._read(client.get_shipments, number)

        def get_logistics_traces(self, number):
            return self._read(client.get_logistics_traces, number)

    def query_client(current):
        nonlocal config, client
        if config is None:
            config = deepcopy(current)
        if current != config:
            raise ValueError('1688 授权已变化，请重新刷新采购进度')
        if client is None:
            client = client_factory(config)
        return GroupQuery()

    errors = []
    for record_id, (generation, value) in started.items():
        try:
            _sync_record(procurement, fulfillment, config_provider, order_id, records[record_id],
                         generation, value, client_factory=query_client)
        except Exception as exc:
            # 一条记录失效后仍结算其他已占位记录，最后向显式刷新调用方报告失败。
            errors.append(exc)
    if errors:
        raise errors[0]


def _sync_record(procurement, fulfillment, config_provider, order_id, record, generation, value, *, client_factory):
    record_id = record.id
    accounts = procurement.accounts_provider
    progress = procurement.store.purchase_progress
    try:
        config = config_provider()
        client = client_factory(config)
        number = validate_order_number(record.purchase_order_number)
        order = client.get_order(number)
        checked_at = utc_iso()
        previous_data = value.get("data") or {}
        parcels = previous_data.get("logistics")
        logistics_checked_at = previous_data.get("logistics_checked_at", previous_data.get("checked_at", "") if parcels is not None else "")
        logistics = None
        warning = ""
        try:
            logistics = client.get_shipments(number)
            parcels = shipment_parcels(logistics, order)
            logistics_checked_at = utc_iso()
        except (AlibabaApiError, ExternalRequestBlocked) as exc:
            logistics = None
            warning = f"物流查询失败：{exc}"
            if isinstance(exc, ExternalRequestBlocked):
                warning += "（可在授权配置的「中断与恢复」查看）"
        if logistics is not None:
            warning = query_parcel_traces(parcels, client, number)
        if config_provider() != config:
            raise AlibabaApiError("1688 授权已变化，请重新刷新采购进度")
        value["data"] = {"ok": True, "record_id": record_id, "order_number": number, "checked_at": checked_at,
                         "order": order.status, "products": order.products, "logistics": parcels, "logistics_warning": warning,
                         "logistics_checked_at": logistics_checked_at}
        if logistics is None:
            value.update(state="synced", message="采购订单状态已更新，物流本次未更新", error="")
        else:
            _merge_parcels(procurement, fulfillment, order_id, record_id, generation, record, logistics, parcels, value)
        value["error"] = ""
    except ExternalRequestBlocked as exc:
        value.update(state="error", error=f"1688 请求已中断：{exc}（可在授权配置的「中断与恢复」查看）", message="本次同步未完成")
    except ValueError as exc:
        value.update(state="error", error=str(exc), message="本次同步未完成")
    except Exception:
        # 不向日志写入可能包含授权、地址或上游响应的异常原文。
        logger.error("采购进度同步发生异常，采购登记已保留")
        value.update(state="error", error="采购同步失败，请稍后刷新", message="本次同步未完成")
    progress.finish(order_id, record_id, generation, accounts(), value)


def _merge_parcels(procurement, fulfillment, order_id, record_id, generation, record, logistics, parcels, value):
    progress = procurement.store.purchase_progress
    proposals, reason = propose_parcels(record, logistics, parcels)

    def guard(conn):
        progress.guard(conn, order_id, record_id, generation, procurement.accounts_provider())
        shared = conn.execute("SELECT 1 FROM order_purchases WHERE order_id<>? AND source_platform=? AND purchase_order_number=? AND status='purchased'",
                              (order_id, record.source_platform, record.purchase_order_number)).fetchone()
        if shared:
            raise ValueError("该采购单关联多个销售订单，请按实际归属登记包裹")

    if proposals:
        try:
            value["state"], value["message"] = fulfillment.merge_purchase_parcels(order_id, record_id, proposals, guard)
        except ValueError as exc:
            value.update(state="pending_review", message=str(exc))
    else:
        value.update(state="synced", message=reason)
