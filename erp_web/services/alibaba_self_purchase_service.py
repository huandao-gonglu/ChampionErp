"""销售订单采购：冻结预览、人工提交、持久化回执与只读核验。"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from erp_web.product_model.alibaba_purchase_model import alibaba_offer_id
from erp_web.schemas.alibaba_self_purchase import SelfPurchasePreviewRequest, SelfPurchaseCreateRequest, SelfPurchaseRecordRequest
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.services.alibaba_api_client import AlibabaApiError, AlibabaApiRejected, ORDER_DETAIL, validate_order_number
from erp_web.services.alibaba_purchase_query_service import normalize_order
from erp_web.services.alibaba_self_purchase_client import AlibabaSelfPurchaseClient, ADDRESSES, PREVIEW, CREATE, ORDER_LIST, PAY_URL


def offer_id(url):
    value = alibaba_offer_id(url)
    if not value:
        raise ValueError("采购来源不是可识别的 1688 商品链接")
    return value


def account_scope(config):
    # 凭据变更使未提交预览失效；不持久化密钥或令牌。
    return hashlib.sha256(json.dumps([config.get("app_key"), config.get("access_token")]).encode()).hexdigest()


def public_record(row):
    result = row["result"]
    return {"id": row["id"], "state": row["state"], "preview": row["payload"]["view"],
            "order_numbers": result.get("order_numbers", []), "order_status": result.get("order_status", ""),
            "message": result.get("message", ""), "pay_channel": result.get("pay_channel", ""), "created_at": row["created_at"], "purchase_record_id": result.get("purchase_record_id", "")}


def default_address(payload):
    # 地址接口将保存地址放在 result.receiveAddressItems。
    result = payload.get("result")
    rows = result.get("receiveAddressItems") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise AlibabaApiError("1688 收货地址格式无效")
    defaults = [a for a in rows if isinstance(a, dict) and (a.get("isDefault") is True or a.get("isDefault") == "true")]
    if len(defaults) != 1:
        raise ValueError("请在 1688 设置唯一的默认收货地址，再重新预览")
    a = defaults[0]
    regions = str(a.get("addressCodeText") or "").split()
    if len(regions) != 3 or not all(a.get(k) for k in ("fullName", "address")) or not (a.get("mobilePhone") or a.get("phone")):
        raise ValueError("默认收货地址缺少省市区、联系人或电话，请先在 1688 补全")
    return {"fullName": a["fullName"], "mobile": a.get("mobilePhone", ""), "phone": a.get("phone", ""),
            "provinceText": regions[0], "cityText": regions[1], "areaText": regions[2],
            "townText": a.get("townName", ""), "address": a["address"], "postCode": a.get("post", "")}


def spec_from_order(payload, candidate):
    rows = payload.get("result", {}).get("productItems", [])
    specs = {str(row.get("specId") or "") for row in rows
             if str(row.get("productID")) == candidate["offer_id"] and str(row.get("skuID")) == candidate["sku_id"]}
    if len(specs) != 1 or not next(iter(specs)):
        raise ValueError("采购订单没有唯一匹配的 1688 商品和 SKU，无法核验下单规格")
    return next(iter(specs))


def normalized_preview(payload, candidate, address, quantity, spec_id):
    rows = payload.get("orderPreviewResuslt")
    if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("status") not in (True, "true"):
        raise AlibabaApiError("本次未返回单笔有效预览，请核对起订量、库存及收货地址")
    order = rows[0]
    cargo = order.get("cargoList")
    if not isinstance(cargo, list) or len(cargo) != 1:
        raise AlibabaApiError("预览商品数量与请求不一致")
    item = cargo[0]
    if (str(item.get("offerId")) != candidate["offer_id"] or str(item.get("skuId")) != candidate["sku_id"]
            or str(item.get("specId")) != spec_id or ("quantity" in item and item["quantity"] != quantity)):
        raise AlibabaApiError("1688 预览的商品、SKU 或数量不一致，已停止下单")
    if "assureTrade" not in order.get("tradeModeNameList", []):
        raise AlibabaApiError("该商品未提供已接入的担保交易流程")
    flow = str(order.get("flowFlag") or "")
    if not flow:
        raise AlibabaApiError("预览未返回交易流程")
    amounts = [order.get(k) for k in ("sumPaymentNoCarriage", "sumCarriage", "sumPayment")]
    if any(type(a) is not int or a < 0 for a in amounts) or amounts[2] <= 0 or amounts[0] + amounts[1] != amounts[2]:
        raise AlibabaApiError("1688 返回的预览金额无效，不能提交")
    channels = [p["name"] for p in order.get("payChannelInfos", []) if p.get("name") in {"alipay", "shegou"}]
    if not channels:
        raise AlibabaApiError("预览未提供已接入的支付渠道")
    phone = address["mobile"] or address["phone"]
    view = {"candidate": candidate, "quantity": quantity, "recipient": address["fullName"],
            "address": " ".join(address[k] for k in ("provinceText", "cityText", "areaText", "townText", "address") if address[k]),
            "phone": phone, "goods_fen": amounts[0], "shipping_fen": amounts[1], "total_fen": amounts[2],
            "flow": flow, "pay_channels": channels, "expires_at": time.time() + 600}
    return view


def created_numbers(payload):
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        raise AlibabaApiError("创建响应未返回有效订单号，请核验原请求")
    numbers = result.get("orderIdList")
    if numbers is None and result.get("orderId"):
        numbers = [result["orderId"]]
    if not isinstance(numbers, list) or len(numbers) != 1:
        raise AlibabaApiError("创建响应未返回唯一订单号，请核验原请求")
    return [validate_order_number(n) for n in numbers]


class AlibabaSelfPurchaseService:
    def __init__(self, store, config_provider, procurement, *, client_factory=AlibabaSelfPurchaseClient):
        self.store, self.config_provider, self.procurement = store, config_provider, procurement
        self.client_factory = client_factory

    @staticmethod
    def _target(order_id, line_key):
        return "order:" + json.dumps([order_id, line_key], ensure_ascii=False)

    def _binding(self, order_id, line_key):
        detail = self.procurement.detail(order_id)
        matches = [r for r in detail["lines"] if r["selection"]["line_key"] == line_key]
        if not line_key or len(matches) != 1:
            raise ValueError("请选择销售订单中的唯一商品行")
        item = matches[0]
        selection = item["selection"]
        binding = {"order_id": order_id, "line_key": line_key, "revision": selection["revision"], "source": selection["source"]}
        return detail, item, binding

    def _purchase_source(self, item):
        selection = item["selection"]
        source = selection["source"]
        if selection["status"] != "confirmed" or not source:
            return None, "请先确认当前订单商品的采购来源"
        try:
            offer = offer_id(source["product_url"])
        except ValueError as exc:
            return None, str(exc)
        if not source["source_sku_id"].isascii() or not source["source_sku_id"].isdigit():
            return None, "采购来源缺少有效的 1688 SKU 编号"
        # 必须命中当前店铺、销售 SKU 的冻结上架证据，人工输入不能代替采集事实。
        matches = [c["source"] for c in selection["candidates"]
                   if c["source"]["product_url"] == source["product_url"]
                   and c["source"]["source_sku_id"] == source["source_sku_id"]
                   and c["source"]["specification"] == source["specification"]]
        if not matches:
            return None, "当前采购来源没有对应的已上架商品记录，或与上架规格不一致，请核对来源"
        # 同一商品重新采集上架后，使用用户确认的那份完整规格依据；不混用旧版缺失值。
        selected = [c for c in matches
                    if c.get("source_offer_id", "") == source.get("source_offer_id", "")
                    and c.get("source_spec_id", "") == source.get("source_spec_id", "")]
        if not selected:
            return None, "已确认来源与上架记录的规格标识不一致，请重新确认采购来源"
        identities = {(c.get("source_offer_id", ""), c.get("source_spec_id", ""), c.get("purchase_block_reason", "")) for c in selected}
        if len(identities) != 1:
            return None, "同一销售 SKU 对应多个不同的采购规格，无法确定本次下单规格"
        saved_offer, spec, reason = next(iter(identities))
        if reason:
            return None, reason
        if not spec:
            return None, "采集上架记录缺少下单规格标识（specId），请重新采集并核对上架规格"
        if saved_offer != offer:
            return None, "采集商品编号与采购链接不一致，请核对来源"
        if source.get("source_offer_id") != saved_offer or source.get("source_spec_id") != spec:
            return None, "已确认来源与上架记录的规格标识不一致，请重新确认采购来源"
        return {"candidate": {"id": str(selection["revision"]), "offer_id": offer,
                "sku_id": source["source_sku_id"], "specification": source["specification"],
                "product_url": source["product_url"]}, "spec_id": spec}, ""

    def options(self, order_id, line_key):
        detail, item, binding = self._binding(order_id, line_key)
        resolved, reason = self._purchase_source(item)
        if not reason and detail["order"]["state"] != "pending_shipment":
            reason = "只有待发货的销售订单可以自动采购"
        if not reason and item["remaining_quantity"] <= 0:
            reason = "该订单商品已采购齐全，无需重复采购"
        scope = account_scope(self.config_provider())
        return {"ok": True, "candidates": [resolved["candidate"]] if resolved else [],
                "remaining_quantity": item["remaining_quantity"], "can_purchase": not reason,
                "blocked_reason": reason,
                "records": [public_record(r) for r in self.store.records(self._target(order_id, line_key), scope)]}

    def _candidate(self, order_id, line_key, candidate_id, quantity):
        options = self.options(order_id, line_key)
        if options["blocked_reason"]:
            raise ValueError("不能自动采购：" + options["blocked_reason"])
        matches = [c for c in options["candidates"] if c["id"] == candidate_id]
        if len(matches) != 1 or quantity > options["remaining_quantity"]:
            raise ValueError("采购来源已变化或数量超过订单剩余数量，请重新预览")
        return matches[0]

    def _attach(self, row, scope, *, cancelled=False):
        if not row["result"].get("order_numbers") or (row["result"].get("purchase_record_id") and not cancelled):
            return row
        result = dict(row["result"])
        try:
            result["purchase_record_id"] = self.procurement.store.complete_purchase(
                row["id"], result["order_numbers"][0], self.procurement.accounts_provider(), cancelled=cancelled)
        except Exception:
            result["message"] = "1688 订单已存在，关联销售订单尚未完成，请查询原订单重试关联，勿重复下单"
        return self.store.finish(row["id"], scope, row["state"], result)

    def preview(self, body):
        req = SelfPurchasePreviewRequest.model_validate(body)
        candidate = self._candidate(req.order_id, req.line_key, req.candidate_id, req.quantity)
        _, _, binding = self._binding(req.order_id, req.line_key)
        config = self.config_provider()
        client = self.client_factory(config)
        spec = binding["source"]["source_spec_id"]
        address = default_address(client.call(ADDRESSES, {}))
        params = {"addressParam": address, "cargoParamList": [{"offerId": int(candidate["offer_id"]), "specId": spec, "quantity": req.quantity}]}
        view = normalized_preview(client.call(PREVIEW, params), candidate, address, req.quantity, spec)
        if self._candidate(req.order_id, req.line_key, req.candidate_id, req.quantity) != candidate or account_scope(config) != account_scope(self.config_provider()):
            raise ValueError("查询期间授权或采购来源已变化，请重新预览")
        if self._binding(req.order_id, req.line_key)[2] != binding:
            raise ValueError("采购来源已变化，请重新预览")
        row = self.store.preview(self._target(req.order_id, req.line_key), account_scope(config), {"params": params, "view": view, "binding": binding})
        return {"ok": True, "record": public_record(row)}

    def _record(self, ident, config):
        row = self.store.get(ident, account_scope(config))
        # 旧版在线商品预览保留读取数据，但不能作为销售订单采购提交。
        binding = row["payload"].get("binding")
        if not binding:
            raise ValueError("旧版预览未关联销售订单，请从订单详情重新预览")
        self.procurement.store.order(binding["order_id"], self.procurement.accounts_provider())
        return row

    def create(self, body):
        req = SelfPurchaseCreateRequest.model_validate(body)
        config = self.config_provider()
        row = self._record(req.preview_id, config)
        client = self.client_factory(config)
        scope = account_scope(config)
        row, claimed = self.store.claim(req.preview_id, scope, req.pay_channel)
        if not claimed:
            return {"ok": True, "record": public_record(self._attach(row, scope) if row["state"] == "created" else row)}
        result = {"pay_channel": req.pay_channel}
        sent = False
        try:
            view = row["payload"]["view"]
            binding = row["payload"]["binding"]
            candidate = self._candidate(binding["order_id"], binding["line_key"], view["candidate"]["id"], view["quantity"])
            self.procurement.store.reserve_purchase(row["id"], binding, view["quantity"], self.procurement.accounts_provider())
            if candidate != view["candidate"]:
                raise ValueError("采购来源已变化，请重新预览")
            params = row["payload"]["params"]
            if binding["source"].get("source_spec_id") != params["cargoParamList"][0].get("specId"):
                raise ValueError("旧预览缺少上架规格依据，请重新预览")
            current = normalized_preview(client.call(PREVIEW, params), candidate, params["addressParam"], view["quantity"], params["cargoParamList"][0]["specId"])
            if any(current[k] != view[k] for k in ("goods_fen", "shipping_fen", "total_fen", "flow", "pay_channels")):
                raise ValueError("价格、运费或可用交易方式已变化，请重新预览后确认")
            if scope != account_scope(self.config_provider()):
                raise ValueError("授权已变化，请重新预览")
            self.procurement.store.validate_reserved_purchase(row["id"], self.procurement.accounts_provider())
            sent = True
            response = client.call(CREATE, {**params, "flow": view["flow"], "tradeType": "assureTrade",
                "preSelectPayChannel": req.pay_channel, "outOrderId": row["id"], "bestOption": False, "isSplitJxhy": False})
            result.update(order_numbers=created_numbers(response), message="订单已创建，尚未发起支付。请在 1688 收银台确认支付方式及账期。")
            row = self.store.finish(row["id"], scope, "created", result)
        except Exception as exc:
            not_sent = isinstance(exc, AlibabaApiRejected) or (isinstance(exc, ExternalRequestBlocked) and exc.details.get("definitively_rejected"))
            unknown = sent and not not_sent
            message = ("创建结果待核验，请查询原请求，勿重复下单" if unknown else
                       str(exc) if isinstance(exc, (ValueError, AlibabaApiError)) else "提交前检查失败，请检查授权配置或中断与恢复")
            if not unknown:
                self.procurement.store.release_purchase(row["id"])
            result["message"] = message
            row = self.store.finish(row["id"], scope, "unknown" if unknown else "failed", result)
        if row["state"] == "created":
            row = self._attach(row, scope)
        return {"ok": True, "record": public_record(row)}

    def reconcile(self, body):
        req = SelfPurchaseRecordRequest.model_validate(body)
        config = self.config_provider()
        row = self._record(req.preview_id, config)
        if row["state"] not in {"created", "unknown", "submitting", "closed"}:
            raise ValueError("该记录尚未提交，无需查询订单")
        if row["state"] == "submitting" and (datetime.now(timezone.utc) - datetime.fromisoformat(row["updated_at"])).total_seconds() < 120:
            return {"ok": True, "record": {**public_record(row), "message": "原请求仍在提交窗口内，请稍后查询"}}
        client = self.client_factory(config)
        result = dict(row["result"])
        numbers = result.get("order_numbers", [])
        if not numbers:
            payload = client.call(ORDER_LIST, {"outOrderId": row["id"], "page": 1, "pageSize": 20, "needBuyerAddressAndPhone": False})
            data = payload.get("result", {})
            orders = data.get("orderList", []) if isinstance(data, dict) else data if isinstance(data, list) else []
            # 必须核对外部订单号，不能把其他订单的查询结果认领为本次下单。
            matching = [o for o in orders if str(o.get("exAttributes", {}).get("outOrderId") or o.get("baseInfo", {}).get("outOrderId") or "") == row["id"]]
            if len(matching) != 1:
                return {"ok": True, "record": {**public_record(row), "message": "暂未查到唯一匹配回执；未查到不等于创建失败，请到 1688 核对，勿重复下单"}}
            base = matching[0].get("baseInfo", {})
            numbers = [validate_order_number(base.get("idOfStr") or base.get("id"))]
        payload = client.query(ORDER_DETAIL, numbers[0])
        order = normalize_order(payload, numbers[0])
        view = row["payload"]["view"]
        if spec_from_order(payload, view["candidate"]) != row["payload"]["params"]["cargoParamList"][0]["specId"]:
            raise AlibabaApiError("返回订单规格与采购记录不一致")
        if account_scope(config) != account_scope(self.config_provider()):
            raise ValueError("授权已切换，请重新查询")
        result.update(order_numbers=numbers, order_status=order["status_label"], message="已从 1688 查询当前订单状态；付款及账期以 1688 为准")
        state = "closed" if order["status"] in {"cancel", "terminated", "success"} else "created"
        row = self.store.finish(row["id"], account_scope(config), state, result)
        row = self._attach(row, account_scope(config), cancelled=order["status"] in {"cancel", "terminated"})
        return {"ok": True, "record": public_record(row)}

    def cashier(self, body):
        req = SelfPurchaseRecordRequest.model_validate(body)
        config = self.config_provider()
        row = self._record(req.preview_id, config)
        numbers = row["result"].get("order_numbers", [])
        if row["state"] != "created" or len(numbers) != 1:
            raise ValueError("请先成功创建并核验订单")
        payload = self.client_factory(config).call(PAY_URL, {"orderIds": [int(n) for n in numbers], "payPlatformType": "PC"})
        url = payload.get("payUrl") or payload.get("url") or payload.get("result")
        if isinstance(url, dict):
            url = url.get("payUrl") or url.get("url")
        parsed = urlsplit(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme != "https" or parsed.username or parsed.password or not any(parsed.hostname == h or (parsed.hostname or "").endswith("." + h) for h in ("1688.com", "alipay.com")):
            raise AlibabaApiError("1688 未返回有效的官方收银台链接，请到 1688 已买到的货品中处理")
        return {"ok": True, "url": url}
