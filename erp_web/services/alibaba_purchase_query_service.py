"""按已登记的采购记录查询 1688；远端读取不持有数据库事务。"""

from urllib.parse import urlsplit

from erp_web.schemas.alibaba_orders import AlibabaOrderStatus, AlibabaParcel, AlibabaPurchaseQueryResult, PurchaseQueryRequest
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.schemas.orders import utc_iso
from erp_web.services.alibaba_api_client import AlibabaApiClient, AlibabaApiError, ORDER_DETAIL, LOGISTICS_INFO, LOGISTICS_TRACE, validate_order_number


_ORDER_STATUSES = {
    "waitbuyerpay": "等待买家付款", "waitsellersend": "等待卖家发货",
    "waitbuyerreceive": "等待买家收货", "waitbuyerreceives": "等待买家收货",
    "confirm_goods": "已收货", "success": "交易成功", "cancel": "交易取消",
    "terminated": "交易终止",
}
_LOGISTICS_STATUSES = {
    "WAITACCEPT": "等待揽收", "ACCEPT": "已揽收", "TRANSPORT": "运输中",
    "DELIVERING": "派送中", "SIGN": "已签收", "UNSIGN": "未签收",
}


def normalize_order(payload: dict, order_number: str) -> AlibabaOrderStatus:
    result = payload.get("result")
    base = result.get("baseInfo") if isinstance(result, dict) else None
    if not isinstance(base, dict):
        raise AlibabaApiError("1688 未返回订单详情")
    returned_id = str(base.get("idOfStr") or base.get("id") or "")
    if returned_id != order_number:
        raise AlibabaApiError("1688 返回的订单号与采购记录不一致")
    status = str(base.get("status") or "")
    return {"order_number": returned_id, "status": status,
            "status_label": _ORDER_STATUSES.get(status, status or "状态未提供")}


def normalize_parcels(payload: dict, order_payload: dict | None = None) -> list[AlibabaParcel]:
    rows = payload.get("result")
    if not isinstance(rows, list):
        raise AlibabaApiError("1688 未返回有效的物流信息")
    native = (order_payload or {}).get("result", {}).get("nativeLogistics", {})
    details = native.get("logisticsItems", []) if isinstance(native, dict) else []
    parcels = []
    for row in rows:
        if not isinstance(row, dict):
            raise AlibabaApiError("1688 物流信息格式无效")
        status = str(row.get("status") or "")
        company = str(row.get("logisticsCompanyName") or "")
        # 两个只读接口的字段完整度不同；仅按同一订单的唯一运单及承运商 ID 合并。
        if not company and row.get("logisticsBillNo") and isinstance(details, list):
            matches = [item for item in details if isinstance(item, dict)
                       and item.get("logisticsBillNo") == row["logisticsBillNo"]
                       and item.get("logisticsCompanyId") == row.get("logisticsCompanyId")]
            if len(matches) == 1:
                company = str(matches[0].get("logisticsCompanyName") or "")
        parcels.append({
            "logistics_id": str(row.get("logisticsId") or ""),
            "company": company,
            "tracking_number": str(row.get("logisticsBillNo") or ""),
            "status": status, "status_label": _LOGISTICS_STATUSES.get(status, status or "状态未提供"),
            "steps": [],
        })
    return parcels


def attach_traces(parcels: list[AlibabaParcel], payload: dict, order_number: str) -> None:
    rows = payload.get("logisticsTrace")
    if not isinstance(rows, list):
        raise AlibabaApiError("1688 未返回有效的物流轨迹")
    for row in rows:
        if not isinstance(row, dict) or str(row.get("orderId") or order_number) != order_number:
            raise AlibabaApiError("1688 物流轨迹与采购订单不一致")
        logistics_id = str(row.get("logisticsId") or "")
        bill = str(row.get("logisticsBillNo") or "")
        # 仅凭明确的包裹身份匹配，不能将一个包裹的轨迹挂到其他包裹。
        matches = [p for p in parcels if (logistics_id and p["logistics_id"] == logistics_id)
                   or (not logistics_id and bill and p["tracking_number"] == bill)]
        if len(matches) != 1:
            continue
        steps = row.get("logisticsSteps")
        if not isinstance(steps, list):
            raise AlibabaApiError("1688 物流轨迹格式无效")
        matches[0]["steps"] = [
            {"time": str(step.get("acceptTime") or ""), "description": str(step.get("remark") or "")}
            for step in steps if isinstance(step, dict)
        ]


def query_purchase(procurement, config: dict, body: dict, *, client_factory=AlibabaApiClient) -> AlibabaPurchaseQueryResult:
    request = PurchaseQueryRequest.model_validate(body)
    record = procurement.store.purchase_record(request.order_id, request.record_id, procurement.accounts_provider())
    host = (urlsplit(record.source.product_url).hostname or "").lower()
    if record.source.source_platform.strip().lower() != "1688" and not (host == "1688.com" or host.endswith(".1688.com")):
        raise ValueError("仅支持查询 1688 采购记录")
    number = validate_order_number(record.purchase_order_number)
    client = client_factory(config)
    result: AlibabaPurchaseQueryResult = {
        "ok": True, "record_id": record.id, "order_number": number, "checked_at": "",
        "order": None, "logistics": None, "logistics_warning": "",
    }
    if request.kind == "order":
        result["order"] = normalize_order(client.query(ORDER_DETAIL, number), number)
    else:
        parcels = normalize_parcels(client.query(LOGISTICS_INFO, number))
        if parcels:
            try:
                attach_traces(parcels, client.query(LOGISTICS_TRACE, number), number)
            except (AlibabaApiError, ExternalRequestBlocked):
                result["logistics_warning"] = "已获取运单，但本次物流轨迹查询失败，可稍后重试；若持续失败，请检查物流轨迹接口权限及中断与恢复。"
        result["logistics"] = parcels
    # 查询期间可能作废记录或切换店铺，返回前重新验证归属与有效性。
    procurement.store.purchase_record(request.order_id, request.record_id, procurement.accounts_provider())
    result["checked_at"] = utc_iso()
    return result
