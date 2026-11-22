"""销售订单采购：冻结预览、人工提交、持久化回执与只读核验。"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone

from erp_web.product_model.alibaba_purchase_model import alibaba_offer_id
from erp_web.schemas.alibaba_self_purchase import SelfPurchasePreviewRequest, SelfPurchaseCreateRequest, SelfPurchaseRecordRequest, SelfPurchasePayRequest, SelfPurchasePaymentStatusRequest, ParsePurchaseAddressRequest, BatchPurchasePreviewRequest
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.services.alibaba_api_client import AlibabaApiError, AlibabaApiRejected, alibaba_provider
from erp_web.services.alibaba_purchase_preview import group_cargo, preview_groups, verify_group_order
from erp_web.services.alibaba_purchase_address import order_addresses, parse_address
from erp_web.services.alibaba_purchase_payment import payment_label, check_payment_amount


def offer_id(url):
    value = alibaba_offer_id(url)
    if not value:
        raise ValueError("采购来源不是可识别的 1688 商品链接")
    return value


def account_scope(config):
    # 官方授权按买家身份、万邦按企业 Key 和个人凭据隔离；旧记录保留原摘要。
    return config.get("account_scope") or hashlib.sha256(json.dumps([config.get("app_key"), config.get("access_token")]).encode()).hexdigest()


def public_record(row):
    result = row["result"]
    return {"id": row["id"], "state": row["state"], "preview": row["payload"]["view"],
            "payment": row.get('payment') or None,
            "order_numbers": result.get("order_numbers", []), "order_status": result.get("order_status", ""),
            "message": (row.get('payment') or {}).get('message') or result.get("message", ""), "pay_channel": result.get("pay_channel", ""), "created_at": row["created_at"], "purchase_record_id": result.get("purchase_record_id", ""),
            **({"purchase_record_ids": result.get("purchase_record_ids", [])} if row["payload"].get("items") else {})}


def spec_from_order(order, candidate):
    if not order.products_complete:
        raise AlibabaApiError("返回订单缺少商品明细，不能关联")
    specs = {product["spec_id"] for product in order.products
             if product["offer_id"] == candidate["offer_id"] and product["sku_id"] == candidate["sku_id"]}
    if len(specs) != 1 or not next(iter(specs)):
        raise ValueError("采购订单没有唯一匹配的 1688 商品和 SKU，无法核验下单规格")
    return next(iter(specs))


def normalized_preview(previews, candidate, address, quantity, spec_id):
    if len(previews) != 1:
        raise AlibabaApiError("本次未返回单笔有效预览，请核对起订量、库存及收货地址")
    preview = previews[0]
    if len(preview.items) != 1:
        raise AlibabaApiError("预览商品数量与请求不一致")
    item = preview.items[0]
    if ((item.offer_id, item.sku_id, item.spec_id) != (candidate["offer_id"], candidate["sku_id"], spec_id)
            or (item.quantity_reported and item.quantity != quantity)):
        raise AlibabaApiError("1688 预览的商品、SKU 或数量不一致，已停止下单")
    return {"candidate": candidate, "quantity": quantity, "recipient": address["fullName"],
            "address": " ".join(address[k] for k in ("provinceText", "cityText", "areaText", "townText", "address") if address[k]),
            "phone": address["mobile"] or address["phone"], "goods_fen": preview.goods_fen,
            "shipping_fen": preview.shipping_fen, "total_fen": preview.total_fen,
            "flow": preview.flow, "pay_channels": list(preview.pay_channels), "expires_at": time.time() + 600}


def _frozen_purchase(payload):
    if "purchase" in payload:
        return payload["purchase"]
    # 已保存的采购记录采用开放平台字段，读取时转换为当前业务形状。
    params = payload["params"]
    return {"address": params["addressParam"], "cargo": [
        {"offer_id": str(item["offerId"]), "spec_id": item["specId"], "quantity": item["quantity"]}
        for item in params["cargoParamList"]
    ]}


class AlibabaSelfPurchaseService:
    def __init__(self, store, config_provider, procurement, *, address_notes, receiving, client_factory=alibaba_provider):
        self.store, self.config_provider, self.procurement = store, config_provider, procurement
        self.client_factory = client_factory
        self.address_notes = address_notes
        self.receiving = receiving

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
        # 必须命中当前店铺、销售 SKU 的可信绑定（采集上架记录或经 1688 API 核验绑定的规格）。
        matches = [c["source"] for c in selection["candidates"]
                   if c["source"]["product_url"] == source["product_url"]
                   and c["source"]["source_sku_id"] == source["source_sku_id"]
                   and c["source"]["specification"] == source["specification"]]
        if not matches:
            return None, "当前采购来源尚未通过采集上架或 1688 规格核验，请先选择已有货源或粘贴 1688 链接绑定规格"
        # 同一商品重新采集或更换货源后，使用用户确认的那份完整规格依据；不混用旧版缺失值。
        selected = [c for c in matches
                    if c.get("source_offer_id", "") == source.get("source_offer_id", "")
                    and c.get("source_spec_id", "") == source.get("source_spec_id", "")]
        if not selected:
            return None, "已确认来源与绑定货源的规格标识不一致，请重新确认采购来源"
        identities = {(c.get("source_offer_id", ""), c.get("source_spec_id", ""), c.get("purchase_block_reason", "")) for c in selected}
        if len(identities) != 1:
            return None, "同一销售 SKU 对应多个不同的采购规格，无法确定本次下单规格"
        saved_offer, spec, reason = next(iter(identities))
        if reason:
            return None, reason
        if not spec:
            return None, "当前绑定来源缺少下单规格标识（specId），请粘贴 1688 链接重新绑定规格"
        if saved_offer != offer:
            return None, "采集商品编号与采购链接不一致，请核对来源"
        if source.get("source_offer_id") != saved_offer or source.get("source_spec_id") != spec:
            return None, "已确认来源与绑定货源的规格标识不一致，请重新确认采购来源"
        return {"candidate": {"id": str(selection["revision"]), "offer_id": offer,
                "sku_id": source["source_sku_id"], "specification": source["specification"],
                "product_url": source["product_url"]}, "spec_id": spec}, ""

    def options(self, order_id, line_key):
        detail, item, binding = self._binding(order_id, line_key)
        resolved, reason = self._purchase_source(item)
        if not reason and detail["order"]["state"] != "pending_shipment":
            reason = "只有待发货的销售订单可以自动采购"
        warehouse = None
        required = detail['order']['platform'] == 'yandex'
        if required:
            try:
                warehouse = self.receiving.checked(order_id)
            except ValueError as exc:
                reason = reason or str(exc)
        scope = account_scope(self.config_provider())
        return {"warehouse_required": required, "receiving_revision": warehouse['revision'] if warehouse else 0,
                "warehouse_name": warehouse['warehouse_name'] if warehouse else '', "receiving_address": warehouse['address'] if warehouse else None,
                "ok": True, "candidates": [resolved["candidate"]] if resolved else [],
                "remaining_quantity": item["remaining_quantity"], "can_purchase": not reason,
                "blocked_reason": reason,
                "records": [public_record(r) for r in self.store.records(self._target(order_id, line_key), scope)]}

    @staticmethod
    def _reservation_ids(row):
        if row['payload'].get('items'):
            return [row['id'] + ':' + str(index) for index in range(len(row['payload']['items']))]
        return [row['id']]

    def batch_options(self, order_id):
        detail = self.procurement.detail(order_id)
        if detail['order']['platform'] != 'yandex':
            raise ValueError('批量采购目前仅支持 Yandex 订单')
        lines = []
        for item in detail['lines']:
            key = item['selection']['line_key']
            lines.append({'line_key': key, 'title': item['line']['title'], 'sku': item['line']['sku'],
                          'supplier': (item['selection']['source'] or {}).get('supplier', ''),
                          'options': self.options(order_id, key)})
        return {'ok': True, 'lines': lines, 'records': [public_record(row) for row in
                self.store.batch_records(order_id, account_scope(self.config_provider()))]}

    def _check_batch_items(self, items):
        for item in items:
            binding = item['binding']
            candidate = self._candidate(binding['order_id'], binding['line_key'], item['candidate']['id'], item['quantity'])
            current = self._binding(binding['order_id'], binding['line_key'])[2]
            if candidate != item['candidate'] or any(current[k] != binding[k] for k in current):
                raise ValueError('采购来源已变化，请重新预览')

    def batch_preview(self, body):
        req = BatchPurchasePreviewRequest.model_validate(body)
        detail = self.procurement.detail(req.order_id)
        if detail['order']['platform'] != 'yandex':
            raise ValueError('批量采购目前仅支持 Yandex 订单')
        receiving = self.receiving.checked(req.order_id)
        if receiving['revision'] != req.receiving_revision:
            raise ValueError('收货仓已变化，请刷新后重新预览')
        config = self.config_provider()
        items = []
        for entry in req.items:
            candidate = self._candidate(req.order_id, entry.line_key, entry.candidate_id, entry.quantity)
            _, line, binding = self._binding(req.order_id, entry.line_key)
            binding['receiving_warehouse'] = receiving
            items.append({'candidate': candidate, 'binding': binding, 'quantity': entry.quantity,
                          'spec_id': binding['source']['source_spec_id'], 'title': line['line']['title'],
                          'supplier': binding['source']['supplier']})
        address = receiving['address']
        cargo = group_cargo(items)
        # 供应商展示名不是身份，按 1688 预览的实际分单结果构造提交组。
        groups = preview_groups(self.client_factory(config).preview_order(address, cargo), items, address)
        self._check_batch_items(items)
        if account_scope(config) != account_scope(self.config_provider()) or self.receiving.checked(req.order_id) != receiving:
            raise ValueError('授权或收货仓已变化，请重新预览')
        records = []
        for group, view in groups:
            targets = [self._target(req.order_id, item['binding']['line_key']) for item in group]
            target = 'batch:' + hashlib.sha256(json.dumps(targets).encode()).hexdigest()
            payload = {'binding': {'order_id': req.order_id, 'receiving_warehouse': receiving}, 'items': group,
                       'targets': targets, 'purchase': {'address': address, 'cargo': group_cargo(group)}, 'view': view}
            records.append(public_record(self.store.preview(target, account_scope(config), payload)))
        return {'ok': True, 'records': records}

    def addresses(self, order_id, line_key):
        self._binding(order_id, line_key)
        accounts = self.procurement.accounts_provider()
        order = self.procurement.store.order(order_id, accounts)
        items = order_addresses(order, self.address_notes, accounts)
        config = self.config_provider()
        notice = ""
        try:
            saved = self.client_factory(config).list_addresses()
            if account_scope(config) != account_scope(self.config_provider()):
                raise ValueError("授权已变化，请刷新地址列表")
            items = [candidate for candidate, _ in saved] + items
        except (AlibabaApiError, ExternalRequestBlocked):
            notice = "1688 地址读取失败，可重试或填写本次收货地址"
        return {"ok": True, "items": items, "notice": notice}

    def parse_address(self, body):
        req = ParsePurchaseAddressRequest.model_validate(body)
        self._binding(req.order_id, req.line_key)
        return parse_address(req.text)

    def _candidate(self, order_id, line_key, candidate_id, quantity):
        options = self.options(order_id, line_key)
        if options["blocked_reason"]:
            raise ValueError("不能自动采购：" + options["blocked_reason"])
        matches = [c for c in options["candidates"] if c["id"] == candidate_id]
        if len(matches) != 1:
            raise ValueError("采购来源已变化，请重新预览")
        return matches[0]

    def _attach(self, row, scope, *, cancelled=False):
        if not row["result"].get("order_numbers") or (row["result"].get("purchase_record_id") and not cancelled):
            return row
        result = dict(row["result"])
        try:
            if row['payload'].get('items'):
                result['purchase_record_ids'] = self.procurement.store.complete_purchases(
                    self._reservation_ids(row), result['order_numbers'][0], self.procurement.accounts_provider(), cancelled=cancelled)
                result['purchase_record_id'] = result['purchase_record_ids'][0]
            else:
                result["purchase_record_id"] = self.procurement.store.complete_purchase(
                    row["id"], result["order_numbers"][0], self.procurement.accounts_provider(), cancelled=cancelled)
        except Exception:
            result["message"] = "1688 订单已存在，关联销售订单尚未完成，请查询原订单重试关联，勿重复下单"
        return self.store.finish(row["id"], scope, row["state"], result)

    def preview(self, body):
        req = SelfPurchasePreviewRequest.model_validate(body)
        candidate = self._candidate(req.order_id, req.line_key, req.candidate_id, req.quantity)
        detail, _, binding = self._binding(req.order_id, req.line_key)
        config = self.config_provider()
        client = self.client_factory(config)
        spec = binding["source"]["source_spec_id"]
        receiving = self.receiving.checked(req.order_id) if detail['order']['platform'] == 'yandex' else None
        if receiving:
            if req.address is not None or req.address_id or req.receiving_revision != receiving['revision']:
                raise ValueError('请使用已确定采购收货仓的当前地址重新预览')
            address = receiving['address']
        elif req.address is not None:
            address = req.address.model_dump()
        else:
            matches = [(candidate, fields) for candidate, fields in client.list_addresses()
                       if candidate["id"] == req.address_id]
            if len(matches) != 1:
                raise ValueError("收货地址已变化，请刷新地址列表并重新选择")
            selected, address = matches[0]
            if selected["blocked_reason"]:
                raise ValueError(selected["blocked_reason"])
        cargo = [{"offer_id": candidate["offer_id"], "spec_id": spec, "quantity": req.quantity}]
        view = normalized_preview(client.preview_order(address, cargo), candidate, address, req.quantity, spec)
        if self._candidate(req.order_id, req.line_key, req.candidate_id, req.quantity) != candidate or account_scope(config) != account_scope(self.config_provider()):
            raise ValueError("查询期间授权或采购来源已变化，请重新预览")
        if self._binding(req.order_id, req.line_key)[2] != binding:
            raise ValueError("采购来源已变化，请重新预览")
        if receiving:
            if self.receiving.checked(req.order_id) != receiving:
                raise ValueError('收货仓库已变化，请重新预览')
            binding['receiving_warehouse'] = receiving
        row = self.store.preview(self._target(req.order_id, req.line_key), account_scope(config), {"purchase": {"address": address, "cargo": cargo}, "view": view, "binding": binding})
        return {"ok": True, "record": public_record(row)}

    def _record(self, ident, config):
        row = self.store.get(ident, account_scope(config))
        # 旧版在线商品预览保留读取数据，但不能作为销售订单采购提交。
        binding = row["payload"].get("binding")
        if not binding:
            raise ValueError("旧版预览未关联销售订单，请从订单详情重新预览")
        self.procurement.store.order(binding["order_id"], self.procurement.accounts_provider())
        return row

    def approval_facts(self, preview_id):
        """审批只读取已冻结采购事实；不预览、不创建，也不发起支付。"""
        config = self.config_provider()
        row = self._record(preview_id, config)
        return {
            "account_scope": account_scope(config),
            "binding": row["payload"]["binding"],
            "items": row["payload"].get("items", []),
            "record": public_record(row),
        }

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
            purchase = _frozen_purchase(row['payload'])
            address, cargo = purchase['address'], purchase['cargo']
            if row['payload'].get('items'):
                items = row['payload']['items']
                self._check_batch_items(items)
                self.procurement.store.reserve_purchases(
                    [(ident, item['binding'], item['quantity']) for ident, item in zip(self._reservation_ids(row), items, strict=True)],
                    self.procurement.accounts_provider())
                groups = preview_groups(client.preview_order(address, cargo), items, address)
                if len(groups) != 1:
                    raise ValueError('1688 分单方案已变化，请重新预览')
                current = groups[0][1]
            else:
                candidate = self._candidate(binding['order_id'], binding['line_key'], view['candidate']['id'], view['quantity'])
                self.procurement.store.reserve_purchase(row['id'], binding, view['quantity'], self.procurement.accounts_provider())
                if candidate != view['candidate']:
                    raise ValueError('采购来源已变化，请重新预览')
                if binding['source'].get('source_spec_id') != cargo[0].get('spec_id'):
                    raise ValueError('旧预览缺少上架规格依据，请重新预览')
                current = normalized_preview(client.preview_order(address, cargo), candidate, address, view['quantity'], cargo[0]['spec_id'])
            if any(current[k] != view[k] for k in ("goods_fen", "shipping_fen", "total_fen", "flow", "pay_channels")):
                raise ValueError("价格、运费或可用交易方式已变化，请重新预览后确认")
            if scope != account_scope(self.config_provider()):
                raise ValueError("授权已变化，请重新预览")
            if binding.get('receiving_warehouse') and self.receiving.checked(binding['order_id']) != binding['receiving_warehouse']:
                raise ValueError('收货仓或物流方案已变化，请重新预览')
            if row['payload'].get('items'):
                self.procurement.store.validate_reserved_purchases(self._reservation_ids(row), self.procurement.accounts_provider())
            else:
                self.procurement.store.validate_reserved_purchase(row["id"], self.procurement.accounts_provider())
            sent = True
            numbers = client.create_order(address, cargo, flow=view["flow"], channel=req.pay_channel, external_id=row["id"])
            result.update(order_numbers=numbers, message="订单已创建，尚未发起支付。请在 1688 收银台确认支付方式及账期。")
            row = self.store.finish(row["id"], scope, "created", result)
        except Exception as exc:
            not_sent = isinstance(exc, AlibabaApiRejected) or (isinstance(exc, ExternalRequestBlocked) and exc.details.get("definitively_rejected"))
            unknown = sent and not not_sent
            message = ("创建结果待核验，请查询原请求，勿重复下单" if unknown else
                       str(exc) if isinstance(exc, (ValueError, AlibabaApiError)) else "提交前检查失败，请检查授权配置后重试")
            if not unknown:
                self.procurement.store.release_purchases(self._reservation_ids(row))
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
            numbers = client.find_order_numbers(row["id"])
            if len(numbers) != 1:
                return {"ok": True, "record": {**public_record(row), "message": "暂未查到唯一匹配回执；未查到不等于创建失败，请到 1688 核对，勿重复下单"}}
        order = client.get_order(numbers[0])
        view = row["payload"]["view"]
        if row['payload'].get('items'):
            verify_group_order(order, row['payload']['items'])
        elif spec_from_order(order, view["candidate"]) != _frozen_purchase(row["payload"])["cargo"][0]["spec_id"]:
            raise AlibabaApiError("返回订单规格与采购记录不一致")
        if account_scope(config) != account_scope(self.config_provider()):
            raise ValueError("授权已切换，请重新查询")
        result.update(order_numbers=numbers, order_status=order.status["status_label"], message="已从 1688 查询当前订单状态；付款及账期以 1688 为准")
        state = "closed" if order.status["status"] in {"cancel", "terminated", "success"} else "created"
        row = self.store.finish(row["id"], account_scope(config), state, result)
        row = self._attach(row, account_scope(config), cancelled=order.status["status"] in {"cancel", "terminated"})
        payment = row.get('payment') or {}
        if payment.get('state') in {'submitting', 'unknown'} and order.status['status'] in {'waitsellersend', 'waitbuyerreceive', 'waitbuyerreceives', 'confirm_goods', 'success'}:
            row = self.store.finish_payment(row['id'], account_scope(config), payment['request_id'], 'settled',
                                            '订单已进入发货或收货阶段，停止再次支付；实际渠道和账期以 1688 为准')
        return {"ok": True, "record": public_record(row)}

    def payment_status(self, body):
        req = SelfPurchasePaymentStatusRequest.model_validate(body)
        config = self.config_provider()
        agreement = self.client_factory(config).get_payment_agreement(req.pay_channel)
        if account_scope(config) != account_scope(self.config_provider()):
            raise ValueError('授权已切换，请重新查询免密签约状态')
        return {'ok': True, 'agreement': agreement}

    def pay(self, body):
        req = SelfPurchasePayRequest.model_validate(body)
        config = self.config_provider()
        scope = account_scope(config)
        row = self._record(req.preview_id, config)
        if row.get('payment'):
            return {'ok': True, 'record': public_record(row)}
        channel = row['result'].get('pay_channel')
        if (row['state'] != 'created' or not row['result'].get('purchase_record_id')
                or channel not in {'alipay', 'shegou'}
                or len(row['result'].get('order_numbers', [])) != 1):
            raise ValueError('请先创建、核验并关联已选择支付渠道的采购订单')
        if req.confirmed_total_fen != row['payload']['view']['total_fen']:
            raise ValueError('确认金额与采购预览不同，请重新核对')
        client = self.client_factory(config)
        agreement = client.get_payment_agreement(channel)
        if not agreement['ready']:
            raise ValueError(agreement['message'])
        number = row['result']['order_numbers'][0]
        order = client.get_order(number)
        if order.status['status'] != 'waitbuyerpay':
            raise ValueError('1688 订单已不是待付款状态，请查询原订单；不会再次支付')
        check_payment_amount(order.total_fen, req.confirmed_total_fen)
        items = row['payload'].get('items') or [{'candidate': row['payload']['view']['candidate'],
            'spec_id': _frozen_purchase(row['payload'])['cargo'][0]['spec_id'], 'quantity': row['payload']['view']['quantity']}]
        verify_group_order(order, items)
        if account_scope(config) != account_scope(self.config_provider()):
            raise ValueError('授权已切换，请重新核对支付')
        self._record(req.preview_id, config)
        row, claimed = self.store.claim_payment(row['id'], scope, req.confirmed_total_fen, channel)
        if not claimed:
            return {'ok': True, 'record': public_record(row)}
        payment = row['payment']
        try:
            state, message = client.pay_order(number, channel=payment['channel'],
                                              amount_fen=payment['amount_fen'], request_id=payment['request_id'])
        except Exception as exc:
            rejected = isinstance(exc, AlibabaApiRejected) or (isinstance(exc, ExternalRequestBlocked) and exc.details.get('definitively_rejected'))
            state = 'rejected' if rejected else 'unknown'
            message = str(exc) if isinstance(exc, AlibabaApiRejected) else (
                '1688 已拒绝本次支付，请检查授权配置后重试' if rejected else
                f'{payment_label(channel)}未取得确认回执，请查询原订单，勿重复支付')
        row = self.store.finish_payment(row['id'], scope, payment['request_id'], state, message)
        return {'ok': True, 'record': public_record(row)}

    def cashier(self, body):
        req = SelfPurchaseRecordRequest.model_validate(body)
        config = self.config_provider()
        row = self._record(req.preview_id, config)
        numbers = row["result"].get("order_numbers", [])
        if row["state"] != "created" or len(numbers) != 1:
            raise ValueError("请先成功创建并核验订单")
        return {"ok": True, "url": self.client_factory(config).get_cashier_url(numbers)}
