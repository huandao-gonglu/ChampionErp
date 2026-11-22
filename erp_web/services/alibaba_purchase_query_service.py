"""按已登记的采购记录查询 1688；远端读取不持有数据库事务。"""

from erp_web.schemas.alibaba_orders import AlibabaParcel, AlibabaPurchaseQueryResult, PurchaseQueryRequest
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.schemas.orders import utc_iso
from erp_web.services.alibaba_api_client import AlibabaApiError, alibaba_provider, validate_order_number


def shipment_parcels(shipments, order=None) -> list[AlibabaParcel]:
    carriers = order.carrier_names if order else {}
    return [{**shipment.parcel, "company": shipment.parcel["company"] or carriers.get(
        (shipment.parcel["tracking_number"], shipment.carrier_id), ""), "steps": []}
        for shipment in shipments]


def attach_traces(parcels: list[AlibabaParcel], traces, order_number: str) -> None:
    for trace in traces:
        if trace.order_number != order_number:
            raise AlibabaApiError("1688 物流轨迹与采购订单不一致")
        matches = [parcel for parcel in parcels if
                   (trace.logistics_id and parcel["logistics_id"] == trace.logistics_id)
                   or (not trace.logistics_id and trace.tracking_number and parcel["tracking_number"] == trace.tracking_number)]
        if len(matches) == 1:
            matches[0]["steps"] = trace.steps


def query_parcel_traces(parcels, client, number):
    """补充轨迹，保留运单，并将轨迹失败原因交给页面及采购快照。"""
    if parcels:
        try:
            attach_traces(parcels, client.get_logistics_traces(number), number)
        except ExternalRequestBlocked as exc:
            return f"已获取运单，轨迹查询已暂停。{exc}"
        except AlibabaApiError as exc:
            return f"已获取运单，未能更新轨迹。{exc}"
    return ""


def query_purchase(procurement, config: dict, body: dict, *, client_factory=alibaba_provider) -> AlibabaPurchaseQueryResult:
    request = PurchaseQueryRequest.model_validate(body)
    record = procurement.store.purchase_record(request.order_id, request.record_id, procurement.accounts_provider())
    if record.source_platform != "1688":
        raise ValueError("仅支持查询 1688 采购记录")
    number = validate_order_number(record.purchase_order_number)
    client = client_factory(config)
    result: AlibabaPurchaseQueryResult = {
        "ok": True, "record_id": record.id, "order_number": number, "checked_at": "",
        "order": None, "logistics": None, "logistics_warning": "", "products": [],
    }
    if request.kind == "order":
        order = client.get_order(number)
        result["order"] = order.status
        result["products"] = order.products
    else:
        parcels = shipment_parcels(client.get_shipments(number))
        result["logistics_warning"] = query_parcel_traces(parcels, client, number)
        result["logistics"] = parcels
    # 查询期间可能作废记录或切换店铺，返回前重新验证归属与有效性。
    procurement.store.purchase_record(request.order_id, request.record_id, procurement.accounts_provider())
    result["checked_at"] = utc_iso()
    return result
