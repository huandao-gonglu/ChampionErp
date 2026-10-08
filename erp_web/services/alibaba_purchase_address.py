"""采购地址的只读候选和本地文字提取；不猜缺失地区，不自动确认解析结果。"""
import hashlib
import json
import re

from pydantic import ValidationError

from erp_web.schemas.alibaba_self_purchase import PurchaseAddress
from erp_web.services.alibaba_api_client import AlibabaApiError


def address_text(address):
    return " ".join(address.get(k, "") for k in
                    ("provinceText", "cityText", "areaText", "townText", "address") if address.get(k))


def saved_addresses(payload):
    result = payload.get("result")
    rows = result.get("receiveAddressItems") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise AlibabaApiError("1688 收货地址格式无效")
    addresses = {}
    for row in rows:
        if not isinstance(row, dict):
            raise AlibabaApiError("1688 收货地址格式无效")
        regions = str(row.get("addressCodeText") or "").split()
        fields = dict(fullName=str(row.get("fullName") or ""), mobile=str(row.get("mobilePhone") or ""),
                      phone=str(row.get("phone") or ""), provinceText=regions[0] if len(regions) == 3 else "",
                      cityText=regions[1] if len(regions) == 3 else "", areaText=regions[2] if len(regions) == 3 else "",
                      townText=str(row.get("townName") or ""), address=str(row.get("address") or ""),
                      postCode=str(row.get("post") or ""))
        reason = ""
        try:
            fields = PurchaseAddress.model_validate(fields).model_dump()
        except ValidationError:
            reason = "地址缺少省市区、详细地址、联系人或电话，请在 1688 补全或手动输入"
        ident = "1688:" + hashlib.sha256(json.dumps([row.get("id"), fields], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        text = "\n".join(filter(None, [fields["fullName"], fields["mobile"] or fields["phone"],
                                      " ".join(filter(None, [str(row.get("addressCodeText") or ""), fields["townText"], fields["address"]]))]))
        candidate = dict(id=ident, kind="1688", label="1688 保存地址", text=text,
                         is_default=row.get("isDefault") in (True, "true"), blocked_reason=reason)
        addresses[ident] = (candidate, fields)
    return list(addresses.values())


def order_addresses(order, notes, accounts):
    result = []
    for shipment in order.handover.shipments if order.handover else []:
        point = shipment.origin if shipment.shipment_type == "WITHDRAW" else shipment.destination if shipment.shipment_type == "IMPORT" else None
        if not point or not point.address.strip():
            continue
        label = "订单揽收地址" if shipment.shipment_type == "WITHDRAW" else "订单交货地址"
        result.append(dict(id="order:" + shipment.shipment_id, kind="order", label=label,
                           text=point.address, is_default=False, blocked_reason=""))
        note = notes.read(order.id, shipment.shipment_id, accounts, address=point.address)
        if note.note.strip():
            result.append(dict(id="note:" + shipment.shipment_id, kind="note", label="地址备注 · " + (point.name or label),
                               text=note.note, is_default=False, blocked_reason=""))
    return result


def parse_address(text):
    """只提取明确文字。解析结果始终需要用户检查，缺失或歧义保留为空。"""
    fields = {key: "" for key in PurchaseAddress.model_fields}
    warnings = []
    text = text.strip().replace("\r", "\n")
    # 显式标签优先；多条联系人或电话不任选其一。
    labels = r"(?:收货人|收件人|联系人|姓名|收货地址|详细地址|实际地址|地址|手机号|手机|电话|邮编)\s*[:：]"
    pairs = list(re.finditer(labels, text))
    rest = text
    names = []
    for index, match in enumerate(pairs):
        end = pairs[index + 1].start() if index + 1 < len(pairs) else len(text)
        value = text[match.end():end].strip(" \n,，;；")
        label = match.group().rstrip(" :：")
        if label in {"收货人", "收件人", "联系人", "姓名"}:
            value = re.split(r"[\n,，;；]|(?=\s+(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d))", value, maxsplit=1)[0].strip()
            names.append(value)
            rest = rest.replace(text[match.start():match.end()] + value, " ", 1)
        elif label == "邮编":
            fields["postCode"] = value
            rest = rest.replace(text[match.start():end], " ", 1)
    if len(set(names)) == 1:
        fields["fullName"] = names[0]
    elif names:
        warnings.append("存在多个联系人，请填写本次收货人")
    phones = list(re.finditer(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)|(?<!\d)0\d{2,3}[- ]?\d{7,8}(?:[-转]\d{1,6})?(?!\d)", rest))
    unique = {match.group() for match in phones}
    if len(unique) == 1:
        value = next(iter(unique))
        fields["phone" if value.startswith("0") else "mobile"] = value
    elif unique:
        warnings.append("存在多个电话号码，请填写本次收货电话")
    rest = re.sub(labels, " ", rest)
    for value in unique:
        rest = rest.replace(value, " ")
    # 从明确的省级行政区起解析，避免把联系人或备注识别为地区。
    province = re.search(r"北京市|上海市|天津市|重庆市|内蒙古自治区|广西壮族自治区|西藏自治区|宁夏回族自治区|新疆维吾尔自治区|(?:河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾)省", rest)
    if province:
        fields["provinceText"] = province.group()
        before, tail = rest[:province.start()].strip(" \n,，;；"), rest[province.end():].strip()
        if not names and not fields["fullName"] and before and len(before) <= 30 and not re.search(r"[:：\n\d]", before):
            fields["fullName"] = before
        if fields["provinceText"] in {"北京市", "上海市", "天津市", "重庆市"}:
            fields["cityText"] = fields["provinceText"]
            tail = tail.removeprefix(fields["cityText"]).strip()
        else:
            city = re.match(r"([^\s,，;；:：]{2,15}?(?:自治州|地区|市|盟))", tail)
            if city:
                fields["cityText"] = city.group(1)
                tail = tail[city.end():].strip()
        if fields["cityText"]:
            area = re.match(r"([^\s,，;；:：]{2,15}?(?:自治县|区|县|市|旗))", tail)
            if area:
                fields["areaText"] = area.group(1)
                tail = tail[area.end():].strip(" \n,，;；")
        fields["address"] = tail
    else:
        fields["address"] = rest.strip(" \n,，;；")
    missing = [label for key, label in (("fullName", "收货人"), ("provinceText", "省"), ("cityText", "市"),
                                      ("areaText", "区县"), ("address", "详细地址")) if not fields[key]]
    if not (fields["phone"] or fields["mobile"]):
        missing.append("电话")
    if missing:
        warnings.append("未识别：" + "、".join(missing) + "，请手动补全")
    return dict(ok=True, address=fields, warnings=warnings)
