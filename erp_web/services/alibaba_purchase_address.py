"""采购地址的只读候选和本地文字提取；提取收货字段、忽略冗余资料。"""
import hashlib
import json
import re
import unicodedata

from pydantic import ValidationError

from erp_web.schemas.alibaba_self_purchase import PurchaseAddress
from erp_web.services.alibaba_api_errors import AlibabaApiError


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


_PHONE = re.compile(r"(?<!\d)(?:\+?86[- \t]?)?(?:(?P<mobile>1[3-9]\d[- \t]?\d{4}[- \t]?\d{4})|(?P<phone>0\d{2,3}[- \t]?\d{7,8}(?:[-转]\d{1,6})?))(?!\d)")
_NAME_LABELS = {'收货人': 0, '收件人': 0, '联系人': 1, '姓名': 1}
_ADDRESS_LABELS = {'收货地址': 0, '实际地址': 0, '地址': 1, '详细地址': 1}
_PHONE_LABELS = {'收货电话': 0, '收件电话': 0, '收货手机号': 0, '收件手机号': 0,
                 '联系电话': 1, '手机号': 1, '手机号码': 1, '手机': 1, '电话': 1, '固定电话': 1, '座机': 1,
                 '备用电话': 3, '备用手机': 3}
_IGNORED_LABELS = {'客服电话', '客服手机', '客服', '备用联系人', '备注', '收货时间', '工作时间', '营业时间',
                   '业务范围', '支持物流', '微信', '微信号', 'QQ', '网址', '温馨提示', '注意事项'}
_LABELS = re.compile('(?:' + '|'.join(sorted(set(_NAME_LABELS) | set(_ADDRESS_LABELS) | set(_PHONE_LABELS) | _IGNORED_LABELS | {'邮编'}, key=len, reverse=True)) + r')\s*[:：]')
_PROVINCE = re.compile(r"北京市|上海市|天津市|重庆市|内蒙古自治区|广西壮族自治区|西藏自治区|宁夏回族自治区|新疆维吾尔自治区|(?:河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾)省")


def parse_address(text):
    """优先提取收货字段，备用资料和备注不阻止填充；缺失信息仍需人工补全。"""
    fields = {key: "" for key in PurchaseAddress.model_fields}
    warnings = []
    text = unicodedata.normalize('NFKC', text).strip().replace("\r", "\n")
    pairs = list(_LABELS.finditer(text))
    rest = text
    names, addresses, phones = [], [], []
    for index, match in enumerate(pairs):
        end = pairs[index + 1].start() if index + 1 < len(pairs) else len(text)
        value = text[match.end():end].strip(" \n,，;；")
        label = match.group().rstrip(" :：")
        if label in _IGNORED_LABELS:
            rest = rest.replace(text[match.start():end], ' ', 1)
        elif label in _NAME_LABELS:
            name = _PHONE.split(value, maxsplit=1)[0]
            name = re.split(r"[\n,，;；]", name, maxsplit=1)[0].strip()
            if name:
                names.append((_NAME_LABELS[label], index, name))
                name_end = text.index(name, match.end(), end) + len(name)
                rest = rest.replace(text[match.start():name_end], ' ', 1)
        elif label in _ADDRESS_LABELS and value:
            addresses.append((_ADDRESS_LABELS[label], index, value))
        elif label == '邮编':
            postal = re.search(r'(?<!\d)\d{6}(?!\d)', value)
            if postal and not fields['postCode']:
                fields['postCode'] = postal.group()
            rest = rest.replace(text[match.start():end], ' ', 1)
    if names:
        fields['fullName'] = min(names)[2]
    for match in _PHONE.finditer(text):
        preceding = next((p for p in reversed(pairs) if p.end() <= match.start()), None)
        label = preceding.group().rstrip(' :：') if preceding else ''
        if label in _IGNORED_LABELS or label == '邮编':
            continue
        key = 'mobile' if match.group('mobile') else 'phone'
        value = re.sub(r'[- \t]' if key == 'mobile' else r'[ \t]', '', match.group(key)).replace('转', '-')
        phones.append((_PHONE_LABELS.get(label, 2), match.start(), key, value))
    # 同类多个号码按明确收货标签、普通电话、未标注、备用的顺序取首个。
    for _, _, key, value in sorted(phones):
        if not fields[key]:
            fields[key] = value
    rest = _PHONE.sub(' ', _LABELS.sub(' ', rest))
    province = _PROVINCE.search(rest)
    if province and not fields['fullName']:
        before = rest[:province.start()].strip(" \n,，;；/、|")
        if before and len(before) <= 30 and not re.search(r"[:：\n\d]", before):
            fields['fullName'] = before
    if addresses:
        rest = _PHONE.sub(' ', min(addresses)[2])
    # 从明确的省级行政区起解析，避免把联系人或备注识别为地区。
    province = _PROVINCE.search(rest)
    if province:
        fields["provinceText"] = province.group()
        tail = rest[province.end():].strip()
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
