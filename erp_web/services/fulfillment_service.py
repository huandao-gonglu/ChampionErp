"""跨境履约编排：配送匹配、资料校验、创建核实、取消及仓库状态同步。"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter

from erp_web.schemas.fulfillment import FulfillmentCommand, FulfillmentError, FulfillmentRule, FulfillmentView, LabelInput
from erp_web.schemas.orders import OrderView, utc_iso
from erp_web.services.external_request_context import request_operation

logger = logging.getLogger(__name__)
LOCKED_STATES = {"PACKING", "SHIPPED", "COMPLETED", "CANCELLED"}


def order_model(order):
    return order.delivery.fulfillment_model or order.fulfillment


class FulfillmentService:
    def __init__(self, store, client, accounts_provider, detail_provider, *, label_provider=None, purchase_progress=None, start_worker=True):
        self.store, self.client = store, client
        self.accounts_provider, self.detail_provider = accounts_provider, detail_provider
        self.label_provider = label_provider
        self.purchase_progress = purchase_progress
        self.stop_event = threading.Event()
        self.client.cancel = self.stop_event
        self.worker = None
        if start_worker:
            self.worker = threading.Thread(target=self._run, name="crossborder-fulfillment", daemon=True)
            self.worker.start()

    def order_detail(self, order_id):
        detail = self.detail_provider(order_id)
        return OrderView.model_validate(detail["order"]), detail

    def rule_for(self, order, country):
        d = order.delivery
        return next((r for r in self.store.rules() if r.get("bus_identity") == self.client.identity()
                     and r["platform"] == order.platform and r["account_id"] == order.account_id
                     and r["fulfillment"].lower() == order_model(order).lower()
                     and r["platform_warehouse_id"] == d.warehouse_id and r["delivery_method_id"] == d.method_id
                     and r["country"] == country and r["confirmed"]), None)

    def plan_for(self, value, rule):
        return value["plan"] if value["override"] or value["crossborderbus_order_id"] else ({k: rule[k] for k in ("section_id", "warehouse_id", "service_ids")} if rule else None)

    def validate_plan(self, plan, rule, *, refresh=False):
        if not rule or not plan or plan["section_id"] != rule["section_id"] or plan["warehouse_id"] not in rule["compatible_warehouse_ids"]:
            raise FulfillmentError("尚无可承接此订单配送方式的合作方案，请配置默认履约。")
        catalog = self.client.catalog() if refresh else self.store.setting("catalog", {})
        section = next((s for s in catalog.get("sections", []) if s["section_id"] == plan["section_id"]), None)
        if catalog.get("identity") != self.client.identity() or not section or not any(w["id"] == plan["warehouse_id"] for w in section.get("storehouse_list", [])):
            raise FulfillmentError("所选渠道或仓库不在当前账号的合作范围内。")
        if refresh:
            services = self.client.services(plan["section_id"], plan["warehouse_id"])
            required = {s["id"] for s in services["core_data"]}
            available = required | {s["id"] for s in services["optional_data"]}
            selected = set(plan["service_ids"])
            if not required <= selected or not selected <= available:
                raise FulfillmentError("增值服务已变化，请重新选择并保留所有必选服务。")

    def parcels_payload(self, parcels, detail, *, complete=False):
        counts, purchases, seen, result = Counter(), Counter(), set(), []
        lines = {line["selection"]["line_key"]: line for line in detail["lines"]}
        for parcel in parcels:
            line = lines.get(parcel["line_key"])
            record = next((r for r in (line or {}).get("records", []) if r["id"] == parcel["purchase_record_id"] and r["status"] == "purchased"), None)
            if parcel["id"] in seen or not line or not record:
                raise FulfillmentError("包裹必须关联当前订单的有效采购记录，且不能重复。")
            seen.add(parcel["id"])
            counts[parcel["line_key"]] += parcel["quantity"]
            purchases[record["id"]] += parcel["quantity"]
            if purchases[record["id"]] > record["quantity"] or counts[parcel["line_key"]] > line["line"]["quantity"]:
                raise FulfillmentError("包裹数量超过订单或采购数量，请核对。")
            source = {"1688": 1, "taobao": 2, "pinduoduo": 3}.get(record["source"]["source_platform"])
            if not source:
                raise FulfillmentError("当前只支持 1688、淘宝或拼多多采购包裹。")
            result.append({"package_of": 1, "num": parcel["quantity"], "describe": f'{line["line"]["sku"]} · {line["line"]["title"]} · {record["source"]["specification"]}', "img": line["line"].get("image_url", ""), "logistics_order": parcel["tracking_number"], "from_platform": source, "from_order": record["purchase_order_number"]})
        if complete and (not lines or any(counts[k] != line["line"]["quantity"] for k, line in lines.items())):
            raise FulfillmentError("请补齐全部商品的国内包裹及数量。")
        return result

    def readiness(self, order, detail, value, rule, plan, *, require_label=True):
        if order.state not in {"pending_shipment", "processing"}:
            return "当前平台订单不可预报，请同步并核对平台状态。"
        if order.state != "pending_shipment":
            return "平台尚未确认订单可发货，自动预报已暂停。"
        if order_model(order).lower() in {"fbo", "fby", "fulfillment"}:
            return "平台仓履约订单不进入跨境巴士预报。"
        if not self.client.identity():
            return "请先在授权配置中完成跨境巴士账号授权。"
        if not order.delivery.warehouse_id or not order.delivery.method_id:
            return "平台尚未提供实际仓库或配送方式，请同步订单。"
        if not value["country"] and not order.delivery.country:
            return "平台未提供目的国，请核对后补充目的国。"
        try:
            self.validate_plan(plan, rule)
            if require_label and (not value["platform_label"] or not value["platform_tracking_number"]):
                return value.get("label_error") or "等待平台面单，请获取平台面单或人工补充面单资料。"
            self.parcels_payload(value["parcels"], detail, complete=True)
        except FulfillmentError as exc:
            return str(exc)
        return ""

    def detail(self, order_id):
        order, detail = self.order_detail(order_id)
        value = self.store.ensure(order)
        country = value["country"] or order.delivery.country
        rule = self.rule_for(order, country)
        plan = self.plan_for(value, rule)
        catalog = self.store.setting("catalog", {})
        section = next((s for s in catalog.get("sections", []) if s["section_id"] == (plan or {}).get("section_id")), None)
        warehouse = next((w for w in (section or {}).get("storehouse_list", []) if w["id"] == (plan or {}).get("warehouse_id")), None)
        label_reason = self.label_provider.unsupported_reason(order) if self.label_provider else "当前平台尚未接入面单获取。"
        response = {"ok": True, **value, "delivery": order.delivery.model_dump(), "country": country,
                "plan": plan, "rule": rule, "section_name": (section or {}).get("section_name", ""), "warehouse_name": (warehouse or {}).get("name", ""),
                "blocked_reason": self.readiness(order, detail, value, rule, plan),
                "label_fetch_supported": not label_reason, "label_fetch_reason": label_reason,
                "editable": not value["busy"] and not value["create_unknown"] and value["fulfillment_status"] not in LOCKED_STATES and not value["cancel_requested"] and not value.get("warehouse_locked") and (not value["bus_identity"] or value["bus_identity"] == self.client.identity()),
                "plan_editable": not value["crossborderbus_order_id"] and not value["create_unknown"] and not value["busy"] and not value["cancel_requested"],
                "editing": value["editing_until"] > time.time()}
        return FulfillmentView.model_validate(response).model_dump(mode="json")

    def settings(self):
        credentials = self.client.credentials()
        accounts = self.accounts_provider()
        choices = {}
        for order in self.store.snapshots(accounts):
            d = order.delivery
            if order_model(order).lower() in {"fbo", "fby", "fulfillment"} or not d.method_id or not d.warehouse_id:
                continue
            key = (order.platform, order.account_id, order_model(order), d.warehouse_id, d.method_id, d.country)
            choices[key] = {"platform": order.platform, "account_id": order.account_id, "fulfillment": order_model(order), "platform_warehouse_id": d.warehouse_id, "platform_warehouse_name": d.warehouse_name, "delivery_method_id": d.method_id, "delivery_method_name": d.method_name or d.carrier or d.method_id, "country": d.country}
        return {"ok": True, "authorized": bool(self.client.identity()), "secret_configured": bool(credentials.get("client_secret")), "user_name": credentials.get("user_name", ""), "expires_at": credentials.get("expires_at", 0),
                "catalog": self.store.setting("catalog", {}) if self.store.setting("catalog", {}).get("identity") == self.client.identity() else {},
                "rules": [r for r in self.store.rules() if accounts.get(r["platform"]) == r["account_id"] and r.get("bus_identity") == self.client.identity()], "delivery_choices": list(choices.values())}

    def save_rule(self, body):
        rule = FulfillmentRule.model_validate(body).model_dump()
        if self.accounts_provider().get(rule["platform"]) != rule["account_id"] or not rule["confirmed"]:
            raise FulfillmentError("请选择当前授权店铺，并确认合作仓的承接范围。")
        source_keys = ("platform", "account_id", "fulfillment", "platform_warehouse_id", "delivery_method_id")
        if not any(all(choice[k] == rule[k] for k in source_keys) for choice in self.settings()["delivery_choices"]):
            raise FulfillmentError("平台配送来源已变化，请重新同步订单并选择来源。")
        if rule["warehouse_id"] not in rule["compatible_warehouse_ids"]:
            raise FulfillmentError("默认仓库必须位于已确认的兼容仓库范围内。")
        if rule["id"] and not any(r["id"] == rule["id"] for r in self.settings()["rules"]):
            raise FulfillmentError("默认方案不存在或不属于当前账号。")
        rule["bus_identity"] = self.client.identity()
        self.validate_plan(rule, rule, refresh=True)
        for warehouse_id in rule["compatible_warehouse_ids"]:
            self.validate_plan({**rule, "warehouse_id": warehouse_id}, rule)
        self.store.save_rule(rule)
        return self.settings()

    def merge_purchase_parcels(self, order_id, record_id, proposals, guard):
        """只新增明确归属的包裹；同运单幂等，人工差异和锁定资料留待处理。"""
        current = self.detail(order_id)
        existing = [p for p in current["parcels"] if p["purchase_record_id"] == record_id]
        def identity(parcel):
            return (parcel["carrier"], parcel["tracking_number"], parcel["quantity"])
        known = {identity(p) for p in existing}
        incoming = {identity(p) for p in proposals}
        if known == incoming:
            return "synced", "国内快递单号已同步"
        if not current["editable"] or current["editing"]:
            return "locked", "履约资料已锁定或正在编辑，已保留最新物流信息"
        if not known <= incoming:
            return "conflict", "运单与已保存的包裹不同，请确认包裹分配"
        merged = current["parcels"] + [p for p in proposals if identity(p) not in known]
        _, detail = self.order_detail(order_id)
        try:
            self.parcels_payload(merged, detail, complete=bool(current["crossborderbus_order_id"]))
            changes = {"parcels": merged, "error_message": "", "next_attempt": 0}
            if current["crossborderbus_order_id"]:
                changes["update_pending"] = True
            self.store.change(order_id, current["revision"], changes, guard=guard)
        except ValueError as exc:
            return "conflict", str(exc)
        return "synced", "国内快递单号已自动保存"

    def command(self, action, body):
        request = FulfillmentCommand.model_validate(body)
        if action == "fetch-label":
            return self.fetch_label(request.order_id, request.revision)
        if action == "sync":
            return self.sync(request.order_id, request.revision)
        order, detail = self.order_detail(request.order_id)
        value = self.store.ensure(order)
        if value["erp_order_id"] != request.order_id:
            raise FulfillmentError("当前订单身份已变化，请重新同步并核对原履约单。")
        current = self.detail(request.order_id)
        changes = {}
        if action == "cancel":
            changes = {"cancel_requested": True, "editing_until": 0, "next_attempt": 0, "error_message": ""}
        elif action in {"retry", "submit"}:
            if action == "retry" and value["create_unknown"]:
                return self.sync(request.order_id, request.revision)
            if action == "submit" and (current["blocked_reason"] or value["crossborderbus_order_id"] or value["create_unknown"] or current["editing"]):
                raise FulfillmentError(current["blocked_reason"] or "当前已有预报、正在编辑或创建结果待确认，不能再次提交。")
            changes = {"next_attempt": 0, "error_message": ""}
            if action == "submit":
                changes["manual_submit"] = True
            if action == "retry" and value.get("cancel_rejected"):
                changes["cancel_rejected"] = False
            if not value["crossborderbus_order_id"] and not value["create_unknown"]:
                changes["fulfillment_status"] = "NEW"
        elif action == "pause":
            if not current["plan_editable"]:
                raise FulfillmentError("预报已经开始，不能再修改本单方案。")
            changes = {"editing_until": time.time() + 300}
        elif action == "resume":
            changes = {"editing_until": 0}
        else:
            if not current["editable"]:
                raise FulfillmentError("资料已锁定或创建结果尚未确认，暂不能修改。")
            if action == "plan":
                if not current["plan_editable"] or request.plan is None:
                    raise FulfillmentError("仅报单前可以修改本单方案。")
                plan = request.plan.model_dump()
                self.validate_plan(plan, current["rule"], refresh=True)
                changes = {"plan": plan, "override": True, "editing_until": 0}
            elif action == "parcels":
                if request.parcels is None:
                    raise FulfillmentError("请提供完整国内包裹列表。")
                parcels = [p.model_dump() for p in request.parcels]
                self.parcels_payload(parcels, detail, complete=bool(value["crossborderbus_order_id"]))
                changes = {"parcels": parcels}
            elif action == "label":
                if request.label is None:
                    raise FulfillmentError("请填写面单地址与面单号。")
                changes = {"platform_label": request.label.url, "platform_tracking_number": request.label.tracking_number, "label_error": ""}
                if request.country:
                    if order.delivery.country and request.country != order.delivery.country:
                        raise FulfillmentError("目的国必须与平台订单一致。")
                    if value["crossborderbus_order_id"] and request.country != current["country"]:
                        raise FulfillmentError("预报后不能变更目的国。")
                    changes["country"] = request.country
            else:
                raise FulfillmentError("不支持的履约操作。")
            changes.update(error_message="", next_attempt=0)
            if value["crossborderbus_order_id"]:
                changes["update_pending"] = True
        guard = None
        if action == "parcels" and self.purchase_progress is not None:
            guard = lambda conn: self.purchase_progress.resolve(conn, request.order_id, changes["parcels"], self.accounts_provider())
        self.store.change(request.order_id, request.revision, changes, allow_busy=action == "cancel", guard=guard)
        return self.detail(request.order_id)

    def sync(self, order_id, revision):
        """页面进入或人工同步只执行一轮只读核实，不排队、不重试或代报单。"""
        order, _ = self.order_detail(order_id)
        value = self.store.ensure(order)
        if value["erp_order_id"] != order_id or value["revision"] != revision or value["busy"]:
            raise FulfillmentError("履约资料已变化或正在处理，请刷新后再同步。")
        if value["create_unknown"] or value["operation"] == "create":
            action = "reconcile"
        elif value["crossborderbus_order_id"]:
            action = "sync"
        else:
            return self.detail(order_id)
        if not self._execute(order, value, action):
            raise FulfillmentError("履约资料已变化或正在处理，请刷新后再同步。")
        return self.detail(order_id)

    def fetch_label(self, order_id, revision):
        """在履约占位下获取、托管面单；期间取消、账号变化或资料变化则丢弃结果。"""
        order, _ = self.order_detail(order_id)
        current = self.detail(order_id)
        if order.identity != order_id or not current["editable"] or current["revision"] != revision:
            raise FulfillmentError("履约资料已变化或锁定，请刷新后获取面单。")
        if not self.label_provider or not current["label_fetch_supported"]:
            raise FulfillmentError(current["label_fetch_reason"])
        claimed = self.store.claim(order_id, "fetch-label", revision)
        if not claimed:
            raise FulfillmentError("履约操作正在执行，请刷新后重试。")
        value, token = claimed
        changes = {"label_attempt_at": utc_iso()}
        try:
            with request_operation(__name__ + ":fetch-label", cancel=self.stop_event, deadline_at=time.time() + 150):
                label = self.label_provider.fetch(order)
            fresh_order, _ = self.order_detail(order_id)
            fresh = self.store.get(order_id)
            if (self.stop_event.is_set() or fresh_order.state != "pending_shipment"
                    or fresh_order.identity != order.identity or fresh["revision"] != value["revision"]
                    or fresh["cancel_requested"] or fresh["fulfillment_status"] in LOCKED_STATES
                    or fresh.get("warehouse_locked") or fresh["create_unknown"]
                    or self.accounts_provider().get(order.platform) != order.account_id):
                raise FulfillmentError("订单或履约状态在获取期间发生变化，已停止保存面单，请刷新核对。")
            if fresh["bus_identity"] and fresh["bus_identity"] != self.client.identity():
                raise FulfillmentError("跨境巴士授权已变化，已停止保存面单。")
            changes.update(platform_label=label.url, platform_tracking_number=label.tracking_number,
                           label_error="", next_attempt=0)
            if fresh["crossborderbus_order_id"]:
                changes["update_pending"] = True
        except FulfillmentError as exc:
            changes.update(label_error=str(exc))
        except Exception:
            # 不把第三方正文、PDF 内容或凭据写入履约错误。
            changes.update(label_error="平台面单获取失败，请检查授权、分箱及默认 S3 托管后重试。")
        finally:
            self.store.finish(order_id, token, changes, expected_revision=value["revision"])
        return self.detail(order_id)

    def process_one(self, order):
        value = self.store.ensure(order)
        if value["busy"] or value["next_attempt"] > time.time():
            return False
        cancelled = value["cancel_requested"] or order.state == "cancelled"
        if cancelled and not value["cancel_requested"]:
            value = self.store.change(value["erp_order_id"], value["revision"], {"cancel_requested": True, "error_message": ""})
        if value["fulfillment_status"] in {"CANCELLED", "COMPLETED"} or (value["fulfillment_status"] == "SHIPPED" and not cancelled):
            return False
        if not value["crossborderbus_order_id"] and cancelled and not value["create_unknown"]:
            self.store.change(value["erp_order_id"], value["revision"], {"fulfillment_status": "CANCELLED", "cancel_requested": True})
            return True
        if value["create_unknown"] or value["operation"] == "create":
            return False
        elif value["crossborderbus_order_id"]:
            # 后台只执行已请求的业务写入；状态查询和未知结果核实必须由页面触发。
            if value["error_message"]:
                return False
            if cancelled and not value.get("cancel_sent") and not value.get("cancel_rejected"):
                action = "cancel"
            elif not cancelled and value.get("update_pending"):
                action = "update"
            else:
                return False
        else:
            if value["editing_until"] > time.time() or value["fulfillment_status"] == "EXCEPTION":
                return False
            order, detail = self.order_detail(value["erp_order_id"])
            if not value["platform_label"] and order.delivery.label_url and order.delivery.tracking_number:
                label = LabelInput(url=order.delivery.label_url, tracking_number=order.delivery.tracking_number)
                value = self.store.change(value["erp_order_id"], value["revision"], {"platform_label": label.url, "platform_tracking_number": label.tracking_number})
            rule = self.rule_for(order, value["country"] or order.delivery.country)
            plan = self.plan_for(value, rule)
            if (self.label_provider and not self.label_provider.unsupported_reason(order)
                    and not value["platform_label"] and not value.get("label_error")
                    and rule and rule["auto_submit"]
                    and not self.readiness(order, detail, value, rule, plan, require_label=False)):
                self.fetch_label(value["erp_order_id"], value["revision"])
                # 下一轮读取已持久化的面单，再独立领取创建占位。
                return True
            if not rule or not (rule["auto_submit"] or value.get("manual_submit")) or self.readiness(order, detail, value, rule, plan):
                return False
            value = self.store.change(value["erp_order_id"], value["revision"], {"plan": plan, "country": value["country"] or order.delivery.country, "bus_identity": self.client.identity()})
            action = "create"
        return self._execute(order, value, action)

    def _execute(self, order, value, action):
        cancelled = value["cancel_requested"] or order.state == "cancelled"
        claimed = self.store.claim(value["erp_order_id"], action, value["revision"])
        if not claimed:
            return False
        value, token = claimed
        action = value["operation"]
        changes = {}
        try:
            if value["bus_identity"] and value["bus_identity"] != self.client.identity():
                raise FulfillmentError("跨境巴士授权账号已变化，请恢复原账号后处理此履约单。")
            if action == "create":
                order, detail = self.order_detail(value["erp_order_id"])
                if self.accounts_provider().get(order.platform) != order.account_id:
                    raise FulfillmentError("店铺授权账号已切换，本次预报停止。")
                rule = self.rule_for(order, value["country"])
                self.validate_plan(value["plan"], rule, refresh=True)
                if self.readiness(order, detail, value, rule, value["plan"]):
                    raise FulfillmentError("提交前订单或资料已变化，预报已停止。")
                data = self.client.request("/erpapi/order/createOrder", {"sid": value["plan"]["warehouse_id"], "section_id": value["plan"]["section_id"], "data_status": 1, "is_check_section_order": 1, "order_data": [{"order_number": value["order_number"], "country": value["country"], "sheet": value["platform_label"], "sheet_order_sn": value["platform_tracking_number"], "introduce": f'{order.platform} / {order.order_id}', "add_service": [{"id": i} for i in value["plan"]["service_ids"]], "package_list": self.parcels_payload(value["parcels"], detail, complete=True)}]}, expected_identity=value["bus_identity"]).get("data")
                if not isinstance(data, list) or len(data) != 1 or data[0].get("order_number") != value["order_number"] or not isinstance(data[0].get("order_id"), int):
                    raise FulfillmentError("创建回执缺少唯一订单 ID，请核实远端结果。", unknown=True)
                changes = {"crossborderbus_order_id": data[0]["order_id"], "create_unknown": False, "fulfillment_status": "FULFILLMENT_CREATED"}
            elif action == "reconcile":
                remote = self.client.search(value["order_number"], expected_identity=value["bus_identity"])
                if not remote:
                    raise FulfillmentError("尚未查到已创建订单，请人工核实；禁止重复创建。", unknown=True)
                if (remote.get("sid") != value["plan"]["warehouse_id"]
                        or (remote.get("sheet_info") or {}).get("section") != value["plan"]["section_id"]
                        or not isinstance(remote.get("id"), int) or remote["id"] <= 0):
                    raise FulfillmentError("远端订单的仓库或渠道与本单不一致，请人工核实。", unknown=True)
                changes = {"crossborderbus_order_id": remote["id"], "create_unknown": False, "fulfillment_status": "FULFILLMENT_CREATED"}
            else:
                remote = self.client.request("/erpapi/orderlist/status", {"order_id": value["crossborderbus_order_id"]}, expected_identity=value["bus_identity"]).get("data")
                if not isinstance(remote, dict) or remote.get("id") != value["crossborderbus_order_id"]:
                    raise FulfillmentError("仓库状态回执不完整，保留最近确认的进度。")
                changes = self.remote_status(remote, value)
                if changes["fulfillment_status"] == "WAITING_DOMESTIC_SHIPMENT":
                    info = self.client.search(value["order_number"], expected_identity=value["bus_identity"])
                    packages = (info or {}).get("package_list") or []
                    if packages and all(p.get("status") == 1 for p in packages):
                        changes["fulfillment_status"] = "WAREHOUSE_RECEIVED"
                if cancelled and changes["fulfillment_status"] == "SHIPPED":
                    raise FulfillmentError("取消未成功：仓库已发货，请联系仓库核实拦截或退回。")
                if action == "cancel" and changes["fulfillment_status"] != "CANCELLED":
                    self.store.checkpoint(value["erp_order_id"], token, {"cancel_sent": True})
                    value["cancel_sent"] = True
                    self.client.request("/erpapi/order/cancelOrder", {"order_number": value["order_number"]}, expected_identity=value["bus_identity"])
                    changes = {"cancel_requested": True}
                elif action == "update":
                    if changes["fulfillment_status"] in LOCKED_STATES or remote.get("order_status") in {1, 3}:
                        raise FulfillmentError("仓库已打包或完成履约，资料无法更新。")
                    _, detail = self.order_detail(value["erp_order_id"])
                    self.client.request("/erpapi/order/updateOrderSheet", {"order_number": value["order_number"], "sheet": value["platform_label"], "sheet_order_sn": value["platform_tracking_number"]}, expected_identity=value["bus_identity"])
                    self.client.request("/erpapi/order/updateOrderPackage", {"order_number": value["order_number"], "package_list": self.parcels_payload(value["parcels"], detail, complete=True)}, expected_identity=value["bus_identity"])
                    changes["update_pending"] = False
            changes.update(error_message=changes.get("error_message", ""), next_attempt=0)
        except FulfillmentError as exc:
            changes.update(error_message=str(exc), next_attempt=0)
            if action == "create":
                changes.update(create_unknown=exc.unknown or not exc.definitive, fulfillment_status="EXCEPTION")
            if action == "cancel" and exc.definitive and not exc.unknown:
                changes.update(cancel_sent=False, cancel_rejected=True)
        except Exception:
            changes.update(error_message="履约操作失败，请核对授权和资料后重试。", next_attempt=0)
            if action == "create":
                changes.update(create_unknown=True, fulfillment_status="EXCEPTION")
        self.store.finish(value["erp_order_id"], token, changes)
        return True

    @staticmethod
    def remote_status(remote, value):
        if remote.get("is_delete") in {1, 2} or remote.get("status_type") == "deleted":
            status = "CANCELLED"
        elif remote.get("order_status") == 3:
            status = "SHIPPED"
        elif remote.get("is_question") == 1:
            status = "EXCEPTION"
        elif remote.get("order_status") == 1:
            status = "PACKING"
        elif remote.get("order_status") == 0 and remote.get("data_status") == 1:
            status = "WAITING_DOMESTIC_SHIPMENT"
        else:
            raise FulfillmentError("仓库返回未识别的状态，保留最近确认的进度。")
        if value["fulfillment_status"] in {"SHIPPED", "PACKING", "WAREHOUSE_RECEIVED"} and status == "WAITING_DOMESTIC_SHIPMENT":
            status = value["fulfillment_status"]
        error = "仓库报告问题件，请联系仓库处理。" if status == "EXCEPTION" else ""
        if value.get("cancel_rejected") and status not in {"CANCELLED", "SHIPPED"}:
            error = value["error_message"] or "仓库尚未取消，请修正原因后重新请求取消。"
        return {"fulfillment_status": status, "warehouse_locked": bool(value.get("warehouse_locked") or remote.get("order_status") in {1, 3}),
                "last_synced_at": utc_iso(), "error_message": error}

    def _run(self):
        while not self.stop_event.is_set():
            try:
                for order in self.store.snapshots(self.accounts_provider()):
                    if self.stop_event.is_set():
                        break
                    if order.state == "pending_shipment" or self.store.tracked(order):
                        try:
                            self.process_one(order)
                        except ValueError:
                            # 前端编辑与领取发生冲突时跳过本单，下一轮读取最新版本。
                            continue
            except Exception as exc:
                logger.warning("跨境履约后台任务暂停：%s", type(exc).__name__)
            self.stop_event.wait(2)

    def close(self):
        self.stop_event.set()
        if self.worker:
            self.worker.join(timeout=2)
