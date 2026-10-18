"""订单级包裹的校验与仓库协议转换；不依赖采购或销售商品数量。"""

from erp_web.schemas.fulfillment import DomesticParcel, FulfillmentError


def domestic_waybill_key(tracking_number: str) -> str:
    return tracking_number.strip().upper()


def parcel_payloads(parcels: list[dict], *, complete=False) -> list[dict]:
    ids, bills, packages = set(), set(), []
    for value in parcels:
        parcel = DomesticParcel.model_validate(value)
        if parcel.id in ids or parcel.tracking_number in bills:
            raise FulfillmentError("同一订单内不能重复登记包裹或运单号。")
        ids.add(parcel.id)
        bills.add(parcel.tracking_number)
        if not parcel.included:
            continue
        if complete and parcel.quantity is None:
            raise FulfillmentError(f"请补充包裹 {parcel.tracking_number} 的商品件数。")
        package = {"package_of": 1, "num": parcel.quantity, "describe": parcel.description,
                   "img": parcel.image_url, "logistics_order": parcel.tracking_number}
        source = {"1688": 1, "taobao": 2, "pinduoduo": 3}.get(parcel.source_platform)
        if source:
            package["from_platform"] = source
            if parcel.purchase_order_number:
                package["from_order"] = parcel.purchase_order_number
        packages.append(package)
    if complete and not packages:
        raise FulfillmentError("请至少选择一个参与本次报单的国内包裹。")
    return packages
