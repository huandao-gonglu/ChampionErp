"""将 1688 订单、运单及轨迹回执转换为业务结果。"""
import re
from decimal import Decimal, DecimalException
from urllib.parse import urlsplit

from erp_web.schemas.alibaba_orders import AlibabaOrderStatus, AlibabaPurchaseProduct
from erp_web.schemas.alibaba_provider import BuyerOrder, PreviewGroup, PreviewItem, Shipment, ShipmentItem, ShipmentTrace
from erp_web.schemas.order_procurement import ProcurementSource
from erp_web.services.alibaba_api_errors import AlibabaApiError
from erp_web.services.alibaba_purchase_assignment import quantity


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


def normalize_product(payload: dict, source: ProcurementSource) -> tuple[AlibabaPurchaseProduct | None, str]:
    """按采购时的商品和规格身份取订单图片，禁止按标题或条目顺序猜配。"""
    url_offer = re.fullmatch(r"/offer/(\d+)\.html", urlsplit(source.product_url).path)
    offer_id = source.source_offer_id or (url_offer[1] if url_offer else "")
    if (not offer_id or not (source.source_sku_id or source.source_spec_id)
            or (url_offer and url_offer[1] != offer_id)):
        return None, "缺少或存在冲突的商品、SKU 信息，无法匹配采购图片"
    items = payload.get("result", {}).get("productItems")
    if not isinstance(items, list):
        return None, "1688 未返回订单商品明细，暂无法获取采购图片"
    matches = []
    for item in items:
        if not isinstance(item, dict) or str(item.get("productID") or "") != offer_id:
            continue
        sku, spec = str(item.get("skuID") or ""), str(item.get("specId") or "")
        # 至少一个标识命中；其他双方均有值的标识不能冲突。
        if not ((source.source_sku_id and source.source_sku_id == sku)
                or (source.source_spec_id and source.source_spec_id == spec)):
            continue
        if ((source.source_sku_id and sku and source.source_sku_id != sku)
                or (source.source_spec_id and spec and source.source_spec_id != spec)):
            continue
        matches.append(item)
    if len(matches) != 1:
        return None, "订单商品无法与采购 SKU 唯一匹配，暂不展示采购图片"
    item = matches[0]
    images = item.get("productImgUrl")
    images = images if isinstance(images, list) else []
    image_url = ""
    # 接口依次返回缩略图与原图，优先保留原图；不拼造图片地址。
    for value in reversed(images):
        if not isinstance(value, str):
            continue
        try:
            parsed = urlsplit(value.strip())
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                continue
            if parsed.scheme == "http" and parsed.hostname.endswith(".alicdn.com"):
                parsed = parsed._replace(scheme="https")
            image_url = parsed.geturl()
            break
        except ValueError:
            continue
    sku_infos = item.get("skuInfos")
    specification = "；".join(f"{row.get('name') or ''}：{row.get('value') or ''}" for row in
                              (sku_infos if isinstance(sku_infos, list) else []) if isinstance(row, dict))
    return {
        "item_id": str(item.get("subItemIDString") or item.get("subItemID") or ""),
        "quantity": quantity(item.get("quantity")),
        "offer_id": offer_id, "sku_id": str(item.get("skuID") or ""),
        "spec_id": str(item.get("specId") or ""), "name": str(item.get("name") or ""),
        "specification": specification, "image_url": image_url,
    }, "" if image_url else "1688 未提供该采购商品的有效图片"


def normalize_products(payload):
    """整单商品快照，不依赖销售 SKU；每个远端商品子项单独保留。"""
    result = []
    items = payload.get("result", {}).get("productItems")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        offer = str(item.get("productID") or "")
        source = ProcurementSource(product_url=f"https://detail.1688.com/offer/{offer}.html",
            specification=str(item.get("name") or "未提供规格"), source_offer_id=offer,
            source_sku_id=str(item.get("skuID") or ""), source_spec_id=str(item.get("specId") or ""))
        product, _ = normalize_product({"result": {"productItems": [item]}}, source)
        result.append(product or {
            "item_id": str(item.get("subItemIDString") or item.get("subItemID") or ""),
            "quantity": quantity(item.get("quantity")), "offer_id": offer,
            "sku_id": source.source_sku_id, "spec_id": source.source_spec_id,
            "name": str(item.get("name") or ""), "specification": "", "image_url": "",
        })
    return result


def order_result(payload, order_number):
    status = normalize_order(payload, order_number)
    result = payload["result"]
    try:
        amount = Decimal(str(result["baseInfo"]["totalAmount"])) * 100
    except (KeyError, TypeError, DecimalException):
        amount = None
    native = result.get("nativeLogistics")
    rows = native.get("logisticsItems") if isinstance(native, dict) else None
    carriers = {}
    duplicates = set()
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("logisticsBillNo"):
            key = str(row["logisticsBillNo"]), str(row.get("logisticsCompanyId") or "")
            if key in carriers:
                duplicates.add(key)
            carriers[key] = str(row.get("logisticsCompanyName") or "")
    for key in duplicates:
        del carriers[key]
    items = result.get("productItems")
    complete = isinstance(items, list) and all(isinstance(item, dict) for item in items)
    return BuyerOrder(status, normalize_products(payload), amount, carriers, complete)


def shipment_results(payload):
    rows = payload.get("result")
    if not isinstance(rows, list):
        raise AlibabaApiError("1688 未返回有效的物流信息")
    shipments = []
    for row in rows:
        if not isinstance(row, dict):
            raise AlibabaApiError("1688 物流信息格式无效")
        status = str(row.get("status") or "")
        parcel = {
            "logistics_id": str(row.get("logisticsId") or ""),
            "company": str(row.get("logisticsCompanyName") or ""),
            "tracking_number": str(row.get("logisticsBillNo") or ""),
            "status": status,
            "status_label": _LOGISTICS_STATUSES.get(status, status or "状态未提供"),
            "steps": [],
        }
        goods = row.get("logisticsOrderGoods")
        items = tuple(ShipmentItem(
            str(item.get("tradeOrderItemId") or ""), str(item.get("logisticsId") or ""),
            str(item.get("tradeOrderId") or ""), quantity(item.get("quantity")),
        ) for item in goods) if isinstance(goods, list) and all(isinstance(item, dict) for item in goods) else ()
        shipments.append(Shipment(parcel, str(row.get("logisticsCompanyId") or ""), items))
    return shipments


def trace_results(payload, order_number):
    rows = payload.get("logisticsTrace")
    if not isinstance(rows, list):
        raise AlibabaApiError("1688 未返回有效的物流轨迹")
    traces = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("orderId") or order_number) != order_number:
            raise AlibabaApiError("1688 物流轨迹与采购订单不一致")
        steps = row.get("logisticsSteps")
        if not isinstance(steps, list):
            raise AlibabaApiError("1688 物流轨迹格式无效")
        traces.append(ShipmentTrace(
            order_number, str(row.get("logisticsId") or ""), str(row.get("logisticsBillNo") or ""),
            [{"time": str(step.get("acceptTime") or ""), "description": str(step.get("remark") or "")}
             for step in steps if isinstance(step, dict)],
        ))
    return traces


def preview_results(payload):
    rows = payload.get("orderPreviewResuslt")
    if not isinstance(rows, list) or not rows:
        raise AlibabaApiError("未返回有效订单预览，请核对起订量、库存及收货地址")
    groups = []
    for order in rows:
        if not isinstance(order, dict) or order.get("status") not in (True, "true"):
            raise AlibabaApiError("部分商品预览失败，请核对起订量、库存及收货地址后重新预览")
        cargo = order.get("cargoList")
        if not isinstance(cargo, list) or not cargo or any(not isinstance(item, dict) for item in cargo):
            raise AlibabaApiError("预览缺少商品明细")
        if "assureTrade" not in order.get("tradeModeNameList", []):
            raise AlibabaApiError("该商品未提供已接入的担保交易流程")
        flow = str(order.get("flowFlag") or "")
        if not flow:
            raise AlibabaApiError("预览未返回交易流程")
        goods, shipping, total = (order.get(k) for k in ("sumPaymentNoCarriage", "sumCarriage", "sumPayment"))
        if any(type(a) is not int or a < 0 for a in (goods, shipping, total)) or total <= 0 or goods + shipping != total:
            raise AlibabaApiError("1688 返回的预览金额无效，不能提交")
        channels = tuple(p["name"] for p in order.get("payChannelInfos", []) if p.get("name") in {"alipay", "shegou"})
        if not channels:
            raise AlibabaApiError("预览未提供已接入的支付渠道")
        items = tuple(PreviewItem(str(item.get("offerId")), str(item.get("skuId")), str(item.get("specId")),
                                  item.get("quantity"), "quantity" in item) for item in cargo)
        groups.append(PreviewGroup(items, goods, shipping, total, flow, channels))
    return groups
