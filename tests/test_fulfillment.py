"""跨境履约关键回归：幂等、未知回执、采购数量、配送范围与取消确认。"""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from erp_web.schemas.fulfillment import FulfillmentError
from tests.receiving_warehouse_support import receiving_service, assign_receiving
from erp_web.schemas.orders import OrderEvent, OrderLine, OrderSnapshot
from erp_web.schemas.order_procurement import order_line_key
from erp_web.services.crossborderbus_client import CrossborderBusClient
from erp_web.services.fulfillment_service import FulfillmentService
from erp_web.services.order_procurement_service import OrderProcurementService
from erp_web.stores.fulfillment_store import FulfillmentStore
from erp_web.stores.order_notification_store import OrderNotificationStore
from erp_web.stores.order_procurement_store import OrderProcurementStore


class Bus:
    def __init__(self, store):
        self.store = store
        self.calls = []
        self.remote = None
        self.create_error = None
        self.cancel_error = None
        self.lookup = None
        self.account = "bus-a"
        self.next_order_id = 99
        self.catalog()

    def identity(self):
        return self.account

    def credentials(self):
        return {}

    def catalog(self):
        result = {"identity": self.account, "sections": [{"section_id": 1, "section_name": "Yandex", "storehouse_list": [{"id": 10, "name": "A仓"}, {"id": 20, "name": "B仓"}]}]}
        self.store.set_setting("catalog", result)
        return result

    def services(self, section_id, warehouse_id):
        return {"core_data": [{"id": 2, "name": "拆包验货"}], "optional_data": [{"id": 3, "name": "代贴标"}]}

    def request(self, path, body, **kwargs):
        self.calls.append((path, body))
        if path.endswith("createOrder"):
            if self.create_error:
                raise self.create_error
            self.remote = {"id": self.next_order_id, "order_status": 0, "data_status": 1, "is_delete": 0}
            self.lookup = {"id": self.next_order_id, "sid": body['sid'], "sheet_info": {"section": body['section_id'], "section_order": body["order_data"][0]["order_number"]}, "package_list": [{"status": 0}]}
            return {"data": [{"order_number": body["order_data"][0]["order_number"], "order_id": self.next_order_id}]}
        if path.endswith("status"):
            return {"data": self.remote}
        if path.endswith("cancelOrder"):
            if self.cancel_error:
                raise self.cancel_error
            return {"code": 1}
        return {"code": 1}

    def search(self, order_number, **kwargs):
        return self.lookup


@pytest.fixture
def domain(tmp_path):
    orders = OrderNotificationStore(tmp_path / "orders.sqlite3")
    line = OrderLine(sku="SKU-1", remote_id="SKU-1", title="手机支架", quantity=2)
    order = OrderSnapshot(platform="yandex", account_id="4", order_id="123", fulfillment="FBS", status="PROCESSING", state="pending_shipment", items=[line], delivery={"warehouse_id": "platform-warehouse", "method_id": "method-1", "method_name": "平台配送", "country": "RU"}, handover={"state": "ready", "shipments": [{"shipment_id": "batch-1", "shipment_type": "IMPORT", "destination": {"id": "handover-1", "name": "CEL 交货仓 A", "address": "义乌市示例路 1 号"}}]})
    def snapshot(value):
        event = OrderEvent(platform=value.platform, account_id=value.account_id, topic="fixture", resource=value.order_id, payload={"revision": time.time_ns()})
        orders.enqueue(event)
        job = orders.claim({value.platform: value.account_id}, now=time.time())
        orders.save_snapshot(job, value, now=time.time())
        orders.finish(job, now=time.time())
    snapshot(order)
    accounts = {"yandex": "4"}
    procurement = OrderProcurementService(OrderProcurementStore(orders), lambda: accounts, lambda p: "shop-a",
        yandex_images_provider=lambda account, ids: {"SKU-1": "https://platform.example/yandex-sku.jpg"})
    key = order_line_key(line)
    procurement.select_source({"order_id": order.identity, "line_key": key, "revision": 0, "source": {"supplier": "供应商", "source_platform": "1688", "product_url": "https://detail.1688.com/offer/123.html", "source_sku_id": "source-1", "specification": "银色"}})
    assign_receiving(receiving_service(procurement), order)
    procurement.record_purchase({"order_id": order.identity, "line_key": key, "revision": 1, "request_id": "purchase", "quantity": 2, "purchase_order_number": "PO-123", "receiving_revision": 1, "receiving_confirmed": True})
    store = FulfillmentStore(orders)
    bus = Bus(store)
    service = FulfillmentService(store, bus, lambda: accounts, procurement.detail, start_worker=False)
    rule = {"platform": "yandex", "account_id": "4", "fulfillment": "FBS", "platform_warehouse_id": "platform-warehouse", "delivery_method_id": "method-1", "delivery_method_name": "平台配送", "country": "RU", "section_id": 1, "warehouse_id": 10, "service_ids": [2, 3], "compatible_warehouse_ids": [10, 20], "confirmed": True}
    service.save_rule(rule)
    view = service.detail(order.identity)
    service.command("plan", {"order_id": order.identity, "revision": view["revision"],
        "plan": {"section_id": 1, "warehouse_id": 10, "service_ids": [2, 3]},
        "receiving_revision": view["receiving_warehouse"]["revision"]})
    purchase = procurement.detail(order.identity)["lines"][0]["records"][0]
    parcel = {"id": "parcel-1", "line_key": key, "purchase_record_id": purchase["id"], "carrier": "顺丰", "tracking_number": "SF123", "quantity": 2}
    return service, bus, order, parcel, snapshot, accounts


def command(service, order, action, **body):
    if action == "plan":
        view = service.detail(order.identity)
        body.update(receiving_revision=view["receiving_warehouse"]["revision"])
    return service.command(action, {"order_id": order.identity, "revision": service.detail(order.identity)["revision"], **body})


def ready(domain):
    service, bus, order, parcel, *_ = domain
    command(service, order, "label", label={"url": "https://example.com/label.pdf", "tracking_number": "RU123"}, country="RU")
    command(service, order, "parcels", parcels=[parcel])
    return service, bus, order


def due(service, order):
    value = service.store.get(order.identity)
    service.store.change(order.identity, value["revision"], {"next_attempt": 0})


def test_explicit_sync_reaches_erp_shipped(domain):
    service, bus, order = ready(domain)
    assert service.detail(order.identity)["blocked_reason"] == ""
    service.process_one(order)
    assert service.detail(order.identity)["crossborderbus_order_id"] == 99
    create = bus.calls[0][1]
    assert create["is_check_section_order"] == 1
    assert create["order_data"][0]["package_list"][0]["from_order"] == "PO-123"
    assert create["order_data"][0]["package_list"][0]["img"] == "https://platform.example/yandex-sku.jpg"
    assert "introduce" not in create["order_data"][0]
    bus.lookup["package_list"][0]["status"] = 1
    command(service, order, "sync")
    assert service.detail(order.identity)["fulfillment_status"] == "WAREHOUSE_RECEIVED"
    bus.remote["order_status"] = 1
    command(service, order, "sync")
    assert service.detail(order.identity)["fulfillment_status"] == "PACKING"
    bus.remote["order_status"] = 3
    command(service, order, "sync")
    assert service.detail(order.identity)["fulfillment_status"] == "SHIPPED"
    assert service.order_detail(order.identity)[0].state == "pending_shipment"


@pytest.mark.parametrize("remark", ["", "  \n ", "  请保留商品包装\n贴单时勿遮挡商品标签  "])
def test_optional_manual_remark_is_saved_and_sent_only_when_filled(domain, remark):
    service, bus, order = ready(domain)
    view = service.detail(order.identity)
    assert view["remark"] == ""
    command(service, order, "plan", plan=view["plan"], remark=remark)
    assert FulfillmentStore(service.store.orders).get(order.identity)["remark"] == remark.strip()
    assert service.detail(order.identity)["remark"] == remark.strip()
    service.process_one(order)
    created = next(body["order_data"][0] for path, body in bus.calls if path.endswith("createOrder"))
    if remark.strip():
        assert created["introduce"] == remark.strip()
    else:
        assert "introduce" not in created
    with pytest.raises(FulfillmentError):
        command(service, order, "plan", plan=view["plan"], remark="报单后不能修改")
    assert service.store.get(order.identity)["remark"] == remark.strip()


def test_remark_can_be_cleared_and_stale_plan_cannot_overwrite_it(domain):
    service, _, order = ready(domain)
    old = service.detail(order.identity)
    command(service, order, "plan", plan=old["plan"], remark="手工备注")
    with pytest.raises(ValueError):
        service.command("plan", {"order_id": order.identity, "revision": old["revision"],
            "plan": old["plan"], "remark": "旧窗口备注", "receiving_revision": old["receiving_warehouse"]["revision"]})
    assert service.detail(order.identity)["remark"] == "手工备注"
    # 历史客户端未提供备注时保留原值；明确提交空字符串时清空。
    command(service, order, "plan", plan=old["plan"])
    assert service.detail(order.identity)["remark"] == "手工备注"
    command(service, order, "plan", plan=old["plan"], remark="")
    assert service.detail(order.identity)["remark"] == ""


def test_old_fulfillment_without_remark_does_not_generate_default_text(domain):
    import json

    service, bus, order = ready(domain)
    with service.store.orders.connect() as conn:
        raw = json.loads(conn.execute("SELECT value FROM fulfillments WHERE id=?", (order.identity,)).fetchone()[0])
        raw.pop("remark", None)
        conn.execute("UPDATE fulfillments SET value=? WHERE id=?", (json.dumps(raw), order.identity))
        conn.commit()
    assert service.detail(order.identity)["remark"] == ""
    service.process_one(order)
    assert "introduce" not in bus.calls[0][1]["order_data"][0]


def test_plan_request_contract_keeps_manual_remark():
    from erp_web.schemas.requests import validate_request_payload
    from erp_web.schemas.fulfillment import FulfillmentCommand

    body = validate_request_payload({"order_id": "order-1", "revision": 1, "remark": "  手工要求  "},
        endpoint="/api/orders/fulfillment/plan")
    assert FulfillmentCommand.model_validate(body).remark == "手工要求"


class PlatformLabels:
    def __init__(self, callback=None):
        self.calls = 0
        self.callback = callback

    def unsupported_reason(self, order):
        return ""

    def fetch(self, order):
        from erp_web.schemas.fulfillment import LabelInput
        self.calls += 1
        if self.callback:
            self.callback()
        return LabelInput(url="https://cdn.example/label.pdf", tracking_number="123-1")


def test_automatic_label_download_precedes_creation_once(domain):
    service, bus, order, parcel, *_ = domain
    provider = service.label_provider = PlatformLabels()
    command(service, order, "parcels", parcels=[parcel])
    assert service.process_one(order)
    assert not bus.calls
    value = service.detail(order.identity)
    assert value["platform_tracking_number"] == "123-1"
    assert value["platform_label"] == "https://cdn.example/label.pdf"
    assert service.process_one(order)
    assert service.detail(order.identity)["crossborderbus_order_id"] == 99
    assert provider.calls == 1
    assert bus.calls[0][1]["order_data"][0]["sheet_order_sn"] == "123-1"


def test_label_failure_requires_manual_retry_even_after_legacy_due_time(domain, monkeypatch):
    service, bus, order, parcel, *_ = domain
    def denied():
        raise FulfillmentError("Yandex 面单授权不足")
    provider = service.label_provider = PlatformLabels(denied)
    command(service, order, "parcels", parcels=[parcel])
    service.process_one(order)
    value = service.detail(order.identity)
    assert "授权不足" in value["label_error"]
    assert "授权不足" in value["blocked_reason"]
    assert not value["busy"]
    assert value["fulfillment_status"] == "NEW"
    import time
    now = time.time()
    with monkeypatch.context() as patch:
        patch.setattr(time, "time", lambda: now + 86400)
        assert not service.process_one(order)
    assert provider.calls == 1
    assert not bus.calls
    command(service, order, "fetch-label")
    assert provider.calls == 2
    provider.callback = None
    command(service, order, "fetch-label")
    assert service.detail(order.identity)["label_error"] == ""


def test_reacquiring_label_failure_preserves_last_label_and_warehouse_error(domain):
    service, _, order = ready(domain)
    def broken():
        raise FulfillmentError("平台面单尚未生成")
    service.label_provider = PlatformLabels(broken)
    value = service.store.get(order.identity)
    service.store.change(order.identity, value["revision"], {"error_message": "仓库状态查询失败"})
    result = command(service, order, "fetch-label")
    assert result["platform_label"] == "https://example.com/label.pdf"
    assert result["platform_tracking_number"] == "RU123"
    assert result["error_message"] == "仓库状态查询失败"
    assert result["label_error"] == "平台面单尚未生成"


def test_cancel_during_label_download_discards_result(domain):
    service, bus, order, *_ = domain
    provider = service.label_provider = PlatformLabels(lambda: command(service, order, "cancel"))
    result = command(service, order, "fetch-label")
    assert result["cancel_requested"]
    assert not result["platform_label"]
    assert not result["busy"]
    assert provider.calls == 1
    service.process_one(order)
    assert not bus.calls
    assert service.detail(order.identity)["fulfillment_status"] == "CANCELLED"


def test_stale_label_request_and_locked_order_do_not_call_platform(domain):
    service, _, order, *_ = domain
    provider = service.label_provider = PlatformLabels()
    value = service.detail(order.identity)
    with pytest.raises(FulfillmentError, match="变化或锁定"):
        service.fetch_label(order.identity, value["revision"] + 1)
    service.store.change(order.identity, value["revision"], {"fulfillment_status": "PACKING"})
    with pytest.raises(FulfillmentError, match="变化或锁定"):
        command(service, order, "fetch-label")
    assert provider.calls == 0


def test_duplicate_fetch_does_not_send_another_platform_request(domain):
    service, _, order, *_ = domain
    def duplicate():
        with pytest.raises(FulfillmentError, match="变化或锁定"):
            command(service, order, "fetch-label")
    provider = service.label_provider = PlatformLabels(duplicate)
    command(service, order, "fetch-label")
    assert provider.calls == 1


def test_getting_new_label_after_create_queues_bus_update(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    service.label_provider = PlatformLabels()
    result = command(service, order, "fetch-label")
    assert result["crossborderbus_order_id"] == 99
    assert result["update_pending"]
    service.process_one(order)
    updates = [body for path, body in bus.calls if path.endswith("updateOrderSheet")]
    assert updates[0]["sheet_order_sn"] == "123-1"
    assert len([path for path, _ in bus.calls if path.endswith("createOrder")]) == 1


def test_creation_timeout_never_replays(domain):
    service, bus, order = ready(domain)
    bus.create_error = FulfillmentError("创建超时", unknown=True)
    service.process_one(order)
    assert service.detail(order.identity)["create_unknown"]
    command(service, order, "retry")
    service.process_one(order)
    assert len([c for c in bus.calls if c[0].endswith("createOrder")]) == 1
    assert service.detail(order.identity)["create_unknown"]
    bus.lookup = {"id": 99, "sid": 10, "sheet_info": {"section": 1}}
    command(service, order, "sync")
    assert service.detail(order.identity)["crossborderbus_order_id"] == 99


def test_restart_after_create_claim_only_reconciles(domain):
    service, bus, order = ready(domain)
    value = service.store.get(order.identity)
    service.store.claim(order.identity, "create", value["revision"])
    with service.store.orders.connect() as conn:
        conn.execute("UPDATE fulfillments SET lease_until=0")
        conn.commit()
    # 模拟另一个进程重启；已落库的创建占位仍然阻止重放。
    other = FulfillmentService(FulfillmentStore(service.store.orders), bus, service.accounts_provider, service.detail_provider, start_worker=False)
    other.process_one(order)
    assert not any(c[0].endswith("createOrder") for c in bus.calls)
    assert other.detail(order.identity)["create_unknown"]


def test_atomic_claim_and_unique_platform_order(domain):
    service, _, order, *_ = domain
    values = list(ThreadPoolExecutor(8).map(lambda _: service.store.ensure(order), range(20)))
    assert len({v["order_number"] for v in values}) == 1
    revision = values[0]["revision"]
    claims = list(ThreadPoolExecutor(8).map(lambda _: service.store.claim(order.identity, "create", revision), range(20)))
    assert sum(c is not None for c in claims) == 1
    other_model = order.model_copy(update={"fulfillment": "rfbs"})
    assert service.store.ensure(other_model)["erp_order_id"] == order.identity


def test_report_cannot_override_purchase_receiving_warehouse(domain):
    service, _, order, *_ = domain
    with pytest.raises(FulfillmentError, match='采购收货仓一致'):
        command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 20, 'service_ids': [2]})
    assert service.detail(order.identity)['plan']['warehouse_id'] == 10


@pytest.mark.parametrize('change_warehouse', [False, True])
def test_configuration_change_blocks_report_until_binding_and_services_are_reconfirmed(domain, change_warehouse):
    from tests.test_logistics_settings import edit, select

    service, bus, order = ready(domain)
    original = service.detail(order.identity)
    configured = service.receiving.logistics.detail()['addresses'][0]
    changes = {'address': {**configured['address'], 'address': '新的收货地址'}}
    if change_warehouse:
        changes['warehouse_id'] = 20
    changed = edit(service.receiving, configured, **changes)
    stale = service.detail(order.identity)
    assert stale['receiving_binding_stale'] and '原绑定已失效' in stale['blocked_reason']
    for action in ('submit', 'retry'):
        with pytest.raises(ValueError, match='原绑定已失效'):
            command(service, order, action)
    with pytest.raises(ValueError, match='原绑定已失效'):
        command(service, order, 'plan', plan=original['plan'])
    assert not service.process_one(order)
    assert not bus.calls
    select(service.receiving, order, changed, existing_purchase_confirmed=True)
    rebound = service.detail(order.identity)
    assert not rebound['receiving_binding_stale']
    assert rebound['plan']['warehouse_id'] == changed['warehouse_id']
    assert rebound['plan']['service_ids'] == [] and rebound['selected_services'] == []
    assert '选择并保存本单服务' in rebound['blocked_reason']
    assert not service.process_one(order)
    with pytest.raises(ValueError, match='收货仓资料已变化'):
        service.command('plan', {'order_id': order.identity, 'revision': rebound['revision'],
                                'plan': original['plan'], 'receiving_revision': original['receiving_warehouse']['revision']})
    command(service, order, 'plan', plan={**rebound['plan'], 'service_ids': [2]})
    assert not service.detail(order.identity)['blocked_reason']
    assert service.process_one(order)
    sent = [body for path, body in bus.calls if path.endswith('createOrder')]
    assert len(sent) == 1 and sent[0]['sid'] == changed['warehouse_id']


def test_config_change_between_readiness_and_create_claim_prevents_sending(domain, monkeypatch):
    from tests.test_logistics_settings import edit

    service, bus, order = ready(domain)
    configured = service.receiving.logistics.detail()['addresses'][0]
    claim = service.store.claim
    def change_then_claim(order_id, action, revision):
        if action == 'create':
            edit(service.receiving, configured, warehouse_id=20)
        return claim(order_id, action, revision)
    monkeypatch.setattr(service.store, 'claim', change_then_claim)
    assert not service.process_one(order)
    assert not bus.calls
    assert service.detail(order.identity)['receiving_binding_stale']
    assert not service.store.get(order.identity)['create_unknown']


def test_shared_configuration_invalidates_every_bound_order_and_keeps_other_addresses_valid(domain):
    from tests.test_logistics_settings import configure, edit, select

    service, _, order, _, snapshot, _ = domain
    shared = service.receiving.logistics.detail()['addresses'][0]
    second = order.model_copy(update={'order_id': 'another-order'})
    third = order.model_copy(update={'order_id': 'separate-address-order'})
    snapshot(second)
    snapshot(third)
    select(service.receiving, second, shared)
    separate = configure(service.receiving, name='独立地址', warehouse_id=20)
    select(service.receiving, third, separate)
    edit(service.receiving, shared, warehouse_id=20, address={**shared['address'], 'address': '修改后的地址'})
    for bound in (order, second):
        assert service.receiving.detail(bound.identity)['binding_stale']
        with pytest.raises(ValueError, match='原绑定已失效'):
            service.receiving.checked(bound.identity)
    assert not service.receiving.detail(third.identity)['binding_stale']
    assert service.receiving.checked(third.identity)['address_id'] == separate['id']


def test_create_claim_protects_configuration_until_result_is_known(domain):
    from tests.test_logistics_settings import edit

    service, _, order = ready(domain)
    configured = service.receiving.logistics.detail()['addresses'][0]
    value = service.store.get(order.identity)
    claimed, token = service.store.claim(order.identity, 'create', value['revision'])
    with pytest.raises(ValueError, match='创建结果待核验'):
        edit(service.receiving, configured, warehouse_id=20)
    # 过期占位也不能把未知发送当成未发送。
    service.store.finish(order.identity, token, {'create_unknown': True})
    with pytest.raises(ValueError, match='创建结果待核验'):
        edit(service.receiving, configured, warehouse_id=20)
    value = service.store.get(order.identity)
    service.store.change(order.identity, value['revision'], {'create_unknown': False})
    assert edit(service.receiving, configured, warehouse_id=20)['warehouse_id'] == 20


def test_remote_report_keeps_original_plan_and_blocks_rebinding_after_config_edit(domain):
    from tests.test_logistics_settings import edit, select

    service, bus, order = ready(domain)
    service.process_one(order)
    original = service.detail(order.identity)
    configured = service.receiving.logistics.detail()['addresses'][0]
    changed = edit(service.receiving, configured, warehouse_id=20)
    view = service.receiving.detail(order.identity)
    assert view['binding_stale'] and view['locked'] and not view['can_confirm']
    assert view['needs_rebind']  # 需要重绑不等于允许绕过已有报单。
    with pytest.raises(ValueError, match='预报已开始'):
        select(service.receiving, order, changed, existing_purchase_confirmed=True)
    command(service, order, 'sync')
    current = service.detail(order.identity)
    assert current['crossborderbus_order_id'] == original['crossborderbus_order_id']
    assert current['plan'] == original['plan']
    assert sum(path.endswith('createOrder') for path, _ in bus.calls) == 1


def test_parcels_validate_purchase_allocation_and_account(domain):
    service, _, order, parcel, _, accounts = domain
    with pytest.raises(FulfillmentError):
        command(service, order, "parcels", parcels=[{**parcel, "quantity": 3}])
    with pytest.raises(FulfillmentError):
        command(service, order, "parcels", parcels=[{**parcel, "purchase_record_id": "foreign"}])
    with pytest.raises(FulfillmentError):
        command(service, order, "parcels", parcels=[parcel, parcel])
    accounts["yandex"] = "other"
    with pytest.raises(ValueError, match="当前店铺"):
        service.detail(order.identity)


def test_missing_platform_carrier_blocks_new_report_without_changing_address(domain):
    service, bus, order = ready(domain)
    before = service.detail(order.identity)['receiving_warehouse']
    domain[4](order.model_copy(update={'handover': None}))
    current = service.detail(order.identity)
    assert current['blocked_reason']
    assert current['receiving_warehouse']['address'] == before['address']
    service.process_one(order)
    assert not any(path.endswith('createOrder') for path, _ in bus.calls)


def test_platform_cancel_propagates_and_requires_remote_confirmation(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    cancelled = order.model_copy(update={"state": "cancelled", "status": "CANCELLED"})
    domain[4](cancelled)
    due(service, order); service.process_one(cancelled)
    assert service.detail(order.identity)["fulfillment_status"] != "CANCELLED"
    assert any(c[0].endswith("cancelOrder") for c in bus.calls)
    command(service, order, "sync")
    assert sum(c[0].endswith("cancelOrder") for c in bus.calls) == 1
    bus.remote["is_delete"] = 1
    command(service, order, "sync")
    assert service.detail(order.identity)["fulfillment_status"] == "CANCELLED"


def test_cancel_timeout_does_not_replay_cancel(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    bus.cancel_error = FulfillmentError("取消超时", unknown=True, definitive=False)
    command(service, order, "cancel")
    service.process_one(order)
    command(service, order, "sync")
    service.process_one(order)
    assert sum(c[0].endswith("cancelOrder") for c in bus.calls) == 1


def test_shipped_cancel_conflict_keeps_shipped(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    bus.remote["order_status"] = 3
    command(service, order, "cancel")
    service.process_one(order)
    value = service.detail(order.identity)
    assert value["fulfillment_status"] == "SHIPPED"
    assert "取消未成功" in value["error_message"]
    assert not any(c[0].endswith("cancelOrder") for c in bus.calls)
    command(service, order, "sync")
    service.process_one(order)
    assert "取消未成功" in service.detail(order.identity)["error_message"]


def test_status_failure_keeps_last_known_progress(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    bus.remote["order_status"] = 1
    command(service, order, "sync")
    bus.remote = {"id": 1}
    command(service, order, "sync")
    assert service.detail(order.identity)["fulfillment_status"] == "PACKING"
    assert service.detail(order.identity)["error_message"]


def test_account_switch_blocks_existing_remote_operations(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    before = len(bus.calls)
    bus.account = "bus-b"
    command(service, order, "sync")
    assert len(bus.calls) == before
    assert "授权账号已变化" in service.detail(order.identity)["error_message"]


def test_client_token_refresh_uses_persisted_refresh_token(domain, monkeypatch):
    store = domain[0].store
    store.set_setting("credentials", {"client_secret": "enterprise-secret", "app_uid": 4, "access_token": "old", "expires_token": "refresh", "expires_at": 0})
    client = CrossborderBusClient(store)
    calls = []
    def response(path, body, headers=None):
        calls.append((path, body))
        return {"code": 1, "access_token": "new", "expires_token": "next", "expires_in": 15552000}
    monkeypatch.setattr(client, "_request", response)
    assert client.token() == "new"
    assert client.token() == "new"
    assert calls == [("/erpapi/token/refresh", {"client_secret": "enterprise-secret", "expires_token": "refresh"})]
    assert store.setting("credentials")["expires_token"] == "next"


def test_bus_search_matches_exact_section_order_and_checks_all_pages(domain, monkeypatch):
    client = CrossborderBusClient(domain[0].store)
    pages = []
    def request(path, body, **kwargs):
        pages.append(body["page"])
        return {"data": {"count": 101, "list": [{"id": body["page"], "sheet_info": {"section_order": "key-extra" if body["page"] == 1 else "key"}}]}}
    monkeypatch.setattr(client, "request", request)
    assert client.search("key")["id"] == 2
    assert pages == [1, 2]


def test_remote_reconciliation_rejects_other_warehouse(domain):
    service, bus, order = ready(domain)
    bus.create_error = FulfillmentError("创建超时", unknown=True, definitive=False)
    service.process_one(order)
    bus.lookup = {"id": 99, "sid": 20, "sheet_info": {"section": 1}}
    command(service, order, "sync")
    service.process_one(order)
    assert service.detail(order.identity)["create_unknown"]
    assert service.detail(order.identity)["crossborderbus_order_id"] is None


def test_platform_country_cannot_be_overwritten(domain):
    service, _, order, *_ = domain
    with pytest.raises(FulfillmentError, match="目的国必须"):
        command(service, order, "label", label={"url": "https://example.com/label.pdf", "tracking_number": "RU123"}, country="CN")


def test_packed_problem_parcel_stays_locked(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    bus.remote.update(order_status=1, is_question=1)
    command(service, order, "sync")
    assert service.detail(order.identity)["fulfillment_status"] == "EXCEPTION"
    assert not service.detail(order.identity)["editable"]


def test_late_pending_response_does_not_erase_received_progress(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    bus.lookup["package_list"][0]["status"] = 1
    command(service, order, "sync")
    bus.lookup["package_list"] = []
    command(service, order, "sync")
    assert service.detail(order.identity)["fulfillment_status"] == "WAREHOUSE_RECEIVED"


def test_update_sends_full_package_list_and_locks_after_packing(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    parcel = {**domain[3], "tracking_number": "SF456"}
    command(service, order, "parcels", parcels=[parcel])
    service.process_one(order)
    updates = [body for path, body in bus.calls if path.endswith("updateOrderPackage")]
    assert updates[0]["package_list"][0]["logistics_order"] == "SF456"
    bus.remote["order_status"] = 1
    command(service, order, "label", label={"url": "https://example.com/next.pdf", "tracking_number": "RU456"})
    service.process_one(order)
    assert len([path for path, _ in bus.calls if path.endswith("updateOrderPackage")]) == 1
    assert not service.detail(order.identity)["editable"]


def test_settings_never_return_bus_secrets(domain):
    service = domain[0]
    service.store.set_setting("credentials", {"client_secret": "private-secret", "access_token": "private-token", "expires_token": "private-refresh"})
    import json
    value = json.dumps(service.settings())
    assert "private-secret" not in value
    assert "private-token" not in value
    assert "private-refresh" not in value


def test_pdf_upload_checks_size_and_format():
    import base64
    from erp_web.services.fulfillment_label_service import label_pdf
    content = b"%PDF-1.7\nfixture\n%%EOF"
    assert label_pdf(base64.b64encode(content).decode()) == content
    for data in (b"plain text", b"%PDF-1.7\nbroken", b"x" * (8 * 1024 * 1024 + 1)):
        with pytest.raises(FulfillmentError):
            label_pdf(base64.b64encode(data).decode())


def test_label_rejects_nonpublic_addresses():
    from pydantic import ValidationError
    from erp_web.schemas.fulfillment import LabelInput
    for url in ("http://example.com/a.pdf", "https://localhost/a.pdf", "https://127.0.0.1/a.pdf", "https://user:secret@example.com/a.pdf"):
        with pytest.raises(ValidationError):
            LabelInput(url=url, tracking_number="123")


def test_pdf_delivery_verifies_public_content(monkeypatch):
    import base64
    import io
    from erp_web.schemas.external_requests import RequestContext
    from erp_web.services import fulfillment_label_service as labels
    content = b"%PDF-1.7\nfixture\n%%EOF"
    profile = {"id": "hosting"}
    delivered = []
    class Storage:
        def __init__(self, value):
            assert value == profile
        def deliver_pdf(self, *, data, scope):
            delivered.append((data, scope))
            return "https://cdn.example/labels/file.pdf"
    monkeypatch.setattr(labels, "default_profile", lambda config: profile)
    monkeypatch.setattr(labels, "S3ImageStorage", Storage)
    monkeypatch.setattr(labels, "request_context", lambda *args, **kw: RequestContext(platform="image_hosting:public", account_id="hosting", interface="/labels/file.pdf", source="test", semantics="read"))
    monkeypatch.setattr(labels, "managed_urlopen", lambda *args, **kw: io.BytesIO(content))
    encoded = base64.b64encode(content).decode()
    assert labels.deliver_label({}, "order123", encoded)["url"].startswith("https://")
    assert delivered[0][0] == content
    assert "order123" not in delivered[0][1]
    monkeypatch.setattr(labels, "managed_urlopen", lambda *args, **kw: io.BytesIO(b"different"))
    with pytest.raises(FulfillmentError, match="不一致"):
        labels.deliver_label({}, "order123", encoded)


def test_pdf_storage_reuses_matching_object_without_upload(monkeypatch):
    from erp_web.services.s3_image_storage import S3ImageStorage
    storage = S3ImageStorage.__new__(S3ImageStorage)
    storage.profile = {"key_prefix": "erp", "public_base_url": "https://cdn.example"}
    monkeypatch.setattr(storage, "_head", lambda *args: True)
    monkeypatch.setattr(storage, "_put_image", lambda *args, **kw: pytest.fail("已有同内容对象不应再次上传"))
    url = storage.deliver_pdf(data=b"%PDF-1.7\n%%EOF", scope="a" * 64)
    assert "/fulfillment-labels/" in url and url.endswith(".pdf")


def test_unknown_write_cannot_bypass_request_manager(domain, monkeypatch):
    from erp_web.schemas.external_requests import ExternalRequestOutcomeUnknown
    from erp_web.services import crossborderbus_client as module
    client = CrossborderBusClient(domain[0].store)
    def unknown(*args, **kwargs):
        raise ExternalRequestOutcomeUnknown()
    monkeypatch.setattr(module, "managed_urlopen", unknown)
    with pytest.raises(FulfillmentError) as error:
        client._request("/erpapi/order/createOrder", {"order_data": []})
    assert error.value.unknown and not error.value.definitive


def test_remote_search_checks_authorization_identity(domain):
    client = CrossborderBusClient(domain[0].store)
    with pytest.raises(FulfillmentError, match="授权账号已变化"):
        client.search("order", expected_identity="previous-account")


def test_no_unpack_parcel_conflict_explains_rejection_without_exposing_response(domain, monkeypatch):
    import io
    import json
    from erp_web.services import crossborderbus_client as module

    response = {"code": 2, "message": "快递单号YT123已提交过不拆包订单(合单号515167),只能作为一个国际包裹发出。请先将订单修改为拆包验货订单。private-secret"}
    monkeypatch.setattr(module, "managed_urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(response).encode()))
    client = module.CrossborderBusClient(domain[0].store)
    with pytest.raises(FulfillmentError, match="同一国内快递单号已用于不拆包订单") as error:
        client._request("/erpapi/order/createOrder", {"order_data": []})
    assert error.value.definitive and not error.value.unknown
    assert "拆包验货" in str(error.value)
    assert "private-secret" not in str(error.value) and "YT123" not in str(error.value)


def test_fulfillment_http_contract_rejects_before_dispatch(monkeypatch):
    from urllib.parse import urlsplit
    from erp_web.http_route_units import fulfillment_routes as routes
    calls = []
    monkeypatch.setattr(routes.facade, "command", lambda *args: calls.append(args))
    class Handler:
        path = "/api/orders/fulfillment/parcels"
        def read_body(self):
            return {"order_id": "123", "revision": "not-integer", "parcels": []}
        def send_json(self, result, status=200):
            self.result, self.status = result, status
    handler = Handler()
    assert routes.handle_post(handler, urlsplit(handler.path))
    assert handler.status == 400 and not calls


def test_list_summary_keeps_platform_status_independent(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    bus.remote["order_status"] = 3
    command(service, order, "sync")
    view = service.order_detail(order.identity)[0]
    summary = service.store.summaries([view])[view.id]
    assert summary["fulfillment_status"] == "SHIPPED"
    assert summary["crossborderbus_order_id"]
    assert summary["has_domestic_waybill"] is True
    assert set(summary) == {"fulfillment_status", "crossborderbus_order_id", "busy", "operation", "create_unknown", "cancel_requested", "cancel_rejected", "error_message", "label_error", "has_domestic_waybill",
                            "last_synced_at", "section_name", "warehouse_name", "registered_packages", "selected_services", "consolidation_service_id"}
    assert summary["last_synced_at"]
    assert summary["section_name"] == "Yandex"
    assert summary["warehouse_name"] == "A仓"
    assert summary["registered_packages"] == [{"carrier": "顺丰", "tracking_number": "SF123"}]
    assert [s["name"] for s in summary["selected_services"]] == ["拆包验货", "代贴标"]
    assert view.state == "pending_shipment"


def test_list_hint_deduplicates_registered_packages_and_preserves_sync_time(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    value = service.store.get(order.identity)
    service.store.change(order.identity, value["revision"], {
        "parcels": [*value["parcels"], {**value["parcels"][0], "tracking_number": " sf123 "}],
        "plan": {**value["plan"], "consolidation_service_id": 3},
        "last_synced_at": "2026-10-13T12:00:00Z", "last_attempt_at": "2026-10-14T12:00:00Z",
        "error_message": "仓库查询失败，保留最近结果", "fulfillment_status": "PACKING",
    })
    view = service.order_detail(order.identity)[0]
    calls = list(bus.calls)
    summary = service.store.summaries([view])[view.id]
    assert summary["last_synced_at"] == "2026-10-13T12:00:00Z"
    assert summary["error_message"] == "仓库查询失败，保留最近结果"
    assert summary["fulfillment_status"] == "PACKING"
    assert len(summary["registered_packages"]) == 1
    assert summary["consolidation_service_id"] == 3
    assert bus.calls == calls
    bus.account = "bus-b"
    bus.catalog()
    summary = service.store.summaries([view])[view.id]
    assert summary["warehouse_name"] == summary["section_name"] == ""
    assert summary["last_synced_at"] == "2026-10-13T12:00:00Z"


def test_manual_sync_can_refresh_shipped_order(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    bus.remote["order_status"] = 3
    command(service, order, "sync")
    assert not service.process_one(order)
    before = len(bus.calls)
    command(service, order, "sync")
    assert len(bus.calls) == before + 1
    assert not service.process_one(order)


def test_failed_authorization_preserves_previous_credentials(domain, monkeypatch):
    store = domain[0].store
    old = {"client_secret": "old-secret", "app_uid": 4, "user_name": "old-user", "access_token": "old-token", "expires_token": "old-refresh", "expires_at": time.time() + 7200}
    store.set_setting("credentials", old)
    client = CrossborderBusClient(store)
    def response(path, body, headers=None):
        if path.endswith("index"):
            return {"access_token": "new-token", "expires_token": "new-refresh", "expires_in": 7200}
        raise FulfillmentError("新账号授权失败")
    monkeypatch.setattr(client, "_request", response)
    with pytest.raises(FulfillmentError):
        client.authorize("new-secret", "new-user", "password")
    assert store.setting("credentials") == old
    assert client.credentials() == old


def test_authorization_saves_app_uid_but_not_password(domain, monkeypatch):
    client = CrossborderBusClient(domain[0].store)
    def response(path, body, headers=None):
        if path.endswith("index"):
            return {"access_token": "token", "expires_token": "refresh", "expires_in": 7200}
        if path.endswith("authorization"):
            return {"app_uid": 42}
        return {"data": []}
    monkeypatch.setattr(client, "_request", response)
    client.authorize("secret", "user", "private-password")
    saved = client.credentials()
    assert saved["app_uid"] == 42 and saved["user_name"] == "user"
    assert "private-password" not in str(saved)


def test_cancel_rejection_remains_visible_until_explicit_retry(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    bus.cancel_error = FulfillmentError("合作仓库拒绝取消")
    command(service, order, "cancel")
    service.process_one(order)
    command(service, order, "sync")
    value = service.detail(order.identity)
    assert value["cancel_rejected"] and "拒绝取消" in value["error_message"]
    assert sum(path.endswith("cancelOrder") for path, _ in bus.calls) == 1
    bus.cancel_error = None
    command(service, order, "retry")
    service.process_one(order)
    assert sum(path.endswith("cancelOrder") for path, _ in bus.calls) == 2


def test_catalog_normalizes_string_ids_from_actual_api(domain, monkeypatch):
    client = CrossborderBusClient(domain[0].store)
    raw = [{"section_id": "143", "section_name": "Yandex", "storehouse_list": [{"id": "54", "name": "122义乌优易仓", "code": "122"}]}]
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: {"data": raw})
    catalog = client.catalog()
    assert catalog["sections"][0]["section_id"] == 143
    assert catalog["sections"][0]["storehouse_list"][0]["id"] == 54
    assert raw[0]["section_id"] == "143"


def test_existing_catalog_is_read_without_losing_string_ids(domain):
    service, _, order = ready(domain)
    catalog = service.store.setting("catalog")
    catalog["sections"][0]["section_id"] = "1"
    for warehouse in catalog["sections"][0]["storehouse_list"]:
        warehouse["id"] = str(warehouse["id"])
    service.store.set_setting("catalog", catalog)
    settings = service.settings()
    assert settings["catalog"]["sections"][0]["section_id"] == 1
    assert settings["catalog"]["sections"][0]["storehouse_list"][0]["id"] == 10
    assert service.detail(order.identity)["warehouse_name"] == "A仓"
    assert service.detail(order.identity)["blocked_reason"] == ""


def test_services_normalize_ids_and_string_fees(domain, monkeypatch):
    client = CrossborderBusClient(domain[0].store)
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: {"data": {"core_data": [{"id": "2", "name": "验货", "gold": "0.50"}], "optional_data": []}})
    assert client.services(1, 10)["core_data"] == [{"id": 2, "name": "验货", "gold": 0.5}]


def test_invalid_catalog_id_is_not_silently_dropped(domain, monkeypatch):
    client = CrossborderBusClient(domain[0].store)
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: {"data": [{"section_id": "invalid", "section_name": "Yandex", "storehouse_list": []}]})
    with pytest.raises(FulfillmentError, match="回执字段无效"):
        client.catalog()


def test_list_summary_does_not_create_records_and_reads_operation_facts(domain):
    service, _, order, *_ = domain
    with service.store.orders.connect() as conn:
        conn.execute("DELETE FROM fulfillments WHERE id=?", (order.identity,))
        conn.commit()
    view = service.order_detail(order.identity)[0]
    assert service.store.summaries([view]) == {}
    assert not service.store.tracked(order)
    value = service.store.ensure(view)
    value = service.store.change(view.id, value["revision"], {"label_error": "面单获取失败"})
    value = command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2]})
    claimed, _ = service.store.claim(view.id, "create", value["revision"])
    summary = service.store.summaries([view])[view.id]
    assert summary["busy"] is True
    assert summary["operation"] == "create"
    assert summary["create_unknown"] is True
    assert summary["label_error"] == "面单获取失败"
    assert "revision" not in summary and "platform_label" not in summary
    assert claimed["create_unknown"] is True


def test_worker_never_polls_linked_order_even_with_legacy_schedule(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    service.process_one(order)
    assert service.detail(order.identity)["platform_link_state"] == "linked"
    before = len(bus.calls)
    value = service.store.get(order.identity)
    service.store.change(order.identity, value["revision"], {"next_attempt": 0, "force_sync": True})
    for _ in range(5):
        assert not service.process_one(order)
    assert len(bus.calls) == before
    result = command(service, order, "sync")
    assert result["fulfillment_status"] == "WAITING_DOMESTIC_SHIPMENT"
    assert len(bus.calls) == before + 1
    assert not service.process_one(order)


def test_unknown_creation_waits_for_page_query_without_background_reconciliation(domain, monkeypatch):
    service, bus, order = ready(domain)
    bus.create_error = FulfillmentError("创建超时", unknown=True)
    service.process_one(order)
    lookups = []
    monkeypatch.setattr(bus, "search", lambda *args, **kwargs: lookups.append(args))
    for _ in range(5):
        assert not service.process_one(order)
    assert lookups == []
    result = command(service, order, "sync")
    assert result["create_unknown"] and result["error_message"]
    assert len(lookups) == 1
    assert not service.process_one(order)
    assert len(lookups) == 1


def test_sync_is_read_only_and_stale_or_concurrent_requests_do_not_send(domain):
    service, bus, order = ready(domain)
    command(service, order, "sync")
    assert bus.calls == []
    service.process_one(order)
    value = service.store.get(order.identity)
    before = len(bus.calls)
    with pytest.raises(FulfillmentError, match="变化或正在处理"):
        service.sync(order.identity, value["revision"] - 1)
    claimed, token = service.store.claim(order.identity, "sync", value["revision"])
    with pytest.raises(FulfillmentError, match="变化或正在处理"):
        service.sync(order.identity, claimed["revision"])
    assert len(bus.calls) == before
    service.store.finish(order.identity, token, {"update_pending": True, "cancel_requested": True})
    command(service, order, "sync")
    assert all(path.endswith("status") for path, _ in bus.calls[before:])


def test_failed_sync_never_retries_in_background_and_preserves_progress(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    bus.remote["order_status"] = 1
    command(service, order, "sync")
    bus.remote = {"id": 12345}
    result = command(service, order, "sync")
    assert result["fulfillment_status"] == "PACKING" and result["error_message"]
    before = len(bus.calls)
    for _ in range(5):
        assert not service.process_one(order)
    assert len(bus.calls) == before


@pytest.mark.parametrize('platform_cancel', [False, True])
def test_new_cancel_request_can_proceed_after_previous_sync_failure(domain, platform_cancel):
    service, bus, order = ready(domain)
    service.process_one(order)
    value = service.store.get(order.identity)
    service.store.change(order.identity, value['revision'], {'error_message': '上次状态查询失败'})
    if platform_cancel:
        order = order.model_copy(update={'state': 'cancelled', 'status': 'CANCELLED'})
        domain[4](order)
    else:
        command(service, order, 'cancel')
    assert service.process_one(order)
    assert sum(path.endswith('cancelOrder') for path, _ in bus.calls) == 1
    before = len(bus.calls)
    for _ in range(3):
        assert not service.process_one(order)
    assert len(bus.calls) == before


def test_update_preflight_failure_does_not_turn_into_background_polling(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    command(service, order, 'parcels', parcels=[{**domain[3], 'tracking_number': 'SF456'}])
    bus.remote = {'id': 12345}
    assert service.process_one(order)
    before = len(bus.calls)
    for _ in range(3):
        assert not service.process_one(order)
    assert len(bus.calls) == before
    assert not any(path.endswith('updateOrderPackage') for path, _ in bus.calls)


def test_multiple_actual_parcels_require_explicit_consolidation(domain):
    service, bus, order, parcel, *_ = domain
    command(service, order, 'label', label={'url': 'https://example.com/label.pdf', 'tracking_number': 'RU123'})
    parcels = [{**parcel, 'quantity': 1}, {**parcel, 'id': 'parcel-2', 'quantity': 1, 'tracking_number': 'YT456'}]
    view = command(service, order, 'parcels', parcels=parcels)
    assert '2 个国内包裹' in view['blocked_reason']
    service.process_one(order)
    assert not any(path.endswith('createOrder') for path, _ in bus.calls)
    with pytest.raises(ValueError, match='合单服务'):
        command(service, order, 'submit')
    bus.services = lambda *_: {'core_data': [{'id': 2, 'name': '拆包验货'}], 'optional_data': [{'id': 3, 'name': '合单打包'}]}
    view = command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2, 3], 'consolidation_service_id': 3})
    assert not view['blocked_reason']
    service.process_one(order)
    created = next(body['order_data'][0] for path, body in bus.calls if path.endswith('createOrder'))
    assert {p['logistics_order'] for p in created['package_list']} == {'SF123', 'YT456'}
    assert len(created['package_list']) == 2
    assert sum(p['num'] for p in created['package_list']) == 2
    assert created['add_service'] == [{'id': 2}, {'id': 3}]


def test_same_waybill_multiple_lines_does_not_require_consolidation(domain):
    service, bus, order, parcel, *_ = domain
    command(service, order, 'label', label={'url': 'https://example.com/label.pdf', 'tracking_number': 'RU123'})
    view = command(service, order, 'parcels', parcels=[{**parcel, 'quantity': 1}, {**parcel, 'id': 'parcel-2', 'quantity': 1}])
    assert not view['blocked_reason']
    service.process_one(order)
    created = next(body['order_data'][0] for path, body in bus.calls if path.endswith('createOrder'))
    package, = created['package_list']
    assert package['logistics_order'] == 'SF123'
    assert package['num'] == 2


def test_consolidation_service_must_be_selected_and_available(domain):
    service, _, order, *_ = domain
    with pytest.raises(ValueError, match='合单服务'):
        command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2], 'consolidation_service_id': 3})
    with pytest.raises(ValueError, match='服务已变化'):
        command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2, 999], 'consolidation_service_id': 999})


def cancelled_fulfillment(domain):
    service, bus, order = ready(domain)
    service.process_one(order)
    bus.remote['is_delete'] = 1
    command(service, order, 'sync')
    return service, bus, order


@pytest.mark.parametrize('state, bound, cancelled, stale, needs_rebind, can_confirm', [
    ('cancelled', False, True, False, False, False),
    ('cancelled', True, True, False, False, False),
    ('shipped', False, False, False, False, False),
    ('delivered', True, True, False, False, False),
    ('pending_shipment', False, True, False, False, True),
    ('pending_shipment', False, False, False, False, True),
    ('pending_shipment', True, True, False, False, True),
    ('pending_shipment', True, False, False, False, True),
])
def test_receiving_rebind_todo_distinguishes_platform_state_and_existing_binding(
    domain, state, bound, cancelled, stale, needs_rebind, can_confirm,
):
    service, bus, order, _, snapshot, _ = domain
    if not bound:
        with service.store.orders.connect() as conn:
            conn.execute('DELETE FROM order_receiving_warehouses WHERE order_id=?', (order.identity,))
            conn.execute('DELETE FROM order_purchases WHERE order_id=?', (order.identity,))
            conn.commit()
    snapshot(order.model_copy(update={'state': state}))
    if cancelled:
        current = service.store.get(order.identity)
        service.store.change(order.identity, current['revision'], {'fulfillment_status': 'CANCELLED'})
    view = service.receiving.detail(order.identity)
    assert view['binding_stale'] is stale
    assert view['needs_rebind'] is needs_rebind
    assert view['can_confirm'] is can_confirm
    assert service.detail(order.identity)['receiving_binding_stale'] is stale
    if state == 'cancelled':
        assert '平台订单已取消' in view['blocked_reason']
    if state in {'shipped', 'delivered'}:
        assert '不列入报单重绑待办' in view['blocked_reason']
    assert bus.calls == []


def test_cancelled_attempt_keeps_binding_and_plan_save_requires_explicit_submit(domain):
    service, bus, order = cancelled_fulfillment(domain)
    purchases = service.detail_provider(order.identity)['lines']
    old = service.detail(order.identity)
    binding = service.store.receiving.get(order.identity)
    receiving = service.receiving.detail(order.identity)
    assert receiving['locked'] and not receiving['binding_stale'] and not receiving['needs_rebind']
    assert receiving['cancelled_fulfillment'] and not receiving['blocked_reason']
    assert not old['receiving_binding_stale'] and old['plan_editable'] and not old['editable']
    assert old['plan']['service_ids'] == []
    assert not old['attempt_history'] and not service.process_one(order)
    with pytest.raises(ValueError, match='不能更换收货仓'):
        assign_receiving(service.receiving, order, warehouse_id=20)
    with pytest.raises(ValueError, match='设置本次报单方案'):
        command(service, order, 'submit')
    before_calls = len(bus.calls)
    rebound = command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2]})
    assert [path for path, _ in bus.calls[before_calls:]] == ['/erpapi/orderlist/status']
    assert rebound['crossborderbus_order_id'] is None and rebound['fulfillment_status'] == 'NEW'
    assert rebound['awaiting_resubmission'] and not rebound['receiving_binding_stale']
    assert rebound['parcels'] == old['parcels'] and rebound['platform_label'] == old['platform_label']
    assert rebound['attempt_history'][0]['crossborderbus_order_id'] == 99
    assert rebound['attempt_history'][0]['receiving_warehouse'] == old['receiving_warehouse']
    assert 'bus_identity' not in rebound['attempt_history'][0]
    assert service.store.receiving.get(order.identity) == binding
    assert not service.process_one(order)
    command(service, order, 'submit')
    bus.next_order_id = 100
    assert service.process_one(order)
    current = service.detail(order.identity)
    assert current['crossborderbus_order_id'] == 100
    assert current['attempt_history'][0]['crossborderbus_order_id'] == 99
    assert service.detail_provider(order.identity)['lines'] == purchases
    create = [body for path, body in bus.calls if path.endswith('createOrder')][-1]
    assert create['sid'] == 10 and create['order_data'][0]['order_number'] == order.order_id
    assert create['is_check_section_order'] == 1
    assert create['order_data'][0]['add_service'] == [{'id': 2}]
    bus.remote['is_delete'] = 1
    command(service, order, 'sync')
    command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2]})
    assert [item['crossborderbus_order_id'] for item in service.detail(order.identity)['attempt_history']] == [99, 100]
    raw = FulfillmentStore(service.store.orders).get(order.identity)
    assert all('attempt_history' not in item for item in raw['attempt_history'])


@pytest.mark.parametrize('remote', [None, {'id': 99, 'is_delete': 0, 'order_status': 0, 'data_status': 1}, {'id': 100, 'is_delete': 1}])
def test_new_plan_requires_fresh_matching_cancelled_receipt(domain, remote):
    service, bus, order = cancelled_fulfillment(domain)
    old_binding = service.receiving.detail(order.identity)
    bus.remote = remote
    with pytest.raises(ValueError, match='尚未确认'):
        command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2]})
    value = service.detail(order.identity)
    assert value['crossborderbus_order_id'] == 99 and not value['busy'] and not value['attempt_history']
    assert service.receiving.detail(order.identity) == old_binding
    assert not service.process_one(order)


@pytest.mark.parametrize('change', ['order', 'account', 'bus', 'revision', 'configuration', 'binding'])
def test_new_plan_rechecks_changes_during_remote_query(domain, monkeypatch, change):
    service, bus, order = cancelled_fulfillment(domain)
    request = bus.request
    def changed(path, body, **kwargs):
        result = request(path, body, **kwargs)
        if path.endswith('status'):
            if change == 'order':
                domain[4](order.model_copy(update={'state': 'cancelled'}))
            elif change == 'account':
                domain[5]['yandex'] = 'other'
            elif change == 'bus':
                bus.account = 'other'
            elif change == 'configuration':
                from tests.test_logistics_settings import edit
                configured = service.receiving.logistics.detail()['addresses'][0]
                edit(service.receiving, configured, address={**configured['address'], 'address': '变更地址'})
            elif change == 'binding':
                assign_receiving(service.receiving, order)
            else:
                value = service.store.get(order.identity)
                service.store.change(order.identity, value['revision'], {'cancel_requested': True}, allow_busy=True)
        return result
    monkeypatch.setattr(bus, 'request', changed)
    with pytest.raises(ValueError):
        command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2]})
    value = service.store.get(order.identity)
    assert value['crossborderbus_order_id'] == 99 and not value['busy']
    assert not value.get('attempt_history')
    assert not service.store.receiving.get(order.identity).get('confirmation_required')


def test_local_cancel_keeps_binding_and_rejects_stale_plan_revision(domain):
    service, bus, order = ready(domain)
    binding = service.store.receiving.get(order.identity)
    command(service, order, 'cancel')
    service.process_one(order)
    with pytest.raises(ValueError, match='设置本次报单方案'):
        command(service, order, 'retry')
    current = service.detail(order.identity)
    body = {'order_id': order.identity, 'revision': current['revision'] - 1,
            'plan': {'section_id': 1, 'warehouse_id': 10, 'service_ids': [2]},
            'receiving_revision': binding['revision']}
    with pytest.raises(ValueError, match='履约资料已更新'):
        service.command('plan', body)
    assert not service.detail(order.identity)['attempt_history']
    command(service, order, 'plan', plan=body['plan'])
    assert len(service.detail(order.identity)['attempt_history']) == 1
    assert service.store.receiving.get(order.identity) == binding
    assert not service.process_one(order) and bus.calls == []


def test_reconciliation_never_reattaches_archived_cancelled_order(domain):
    service, bus, order = cancelled_fulfillment(domain)
    command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2]})
    command(service, order, 'submit')
    bus.create_error = FulfillmentError('请求超时', unknown=True)
    service.process_one(order)
    command(service, order, 'sync')
    current = service.detail(order.identity)
    assert current['create_unknown'] and current['crossborderbus_order_id'] is None
    assert not service.process_one(order)
    bus.lookup = {**bus.lookup, 'id': 100}
    current = command(service, order, 'sync')
    assert current['crossborderbus_order_id'] == 100 and not current['create_unknown']


def test_bus_search_filters_history_but_still_rejects_multiple_new_attempts(domain, monkeypatch):
    client = CrossborderBusClient(domain[0].store)
    rows = [{'id': n, 'sheet_info': {'section_order': '123'}} for n in [99, 100]]
    monkeypatch.setattr(client, 'request', lambda *a, **kw: {'data': {'count': len(rows), 'list': rows}})
    assert client.search('123', exclude_order_ids={99})['id'] == 100
    assert client.search('123', expected_order_id=99)['id'] == 99
    rows.append({'id': 101, 'sheet_info': {'section_order': '123'}})
    with pytest.raises(FulfillmentError, match='重复'):
        client.search('123', exclude_order_ids={99})
    assert client.search('123', expected_order_id=100)['id'] == 100
    assert client.search('123', exclude_order_ids={99, 100, 101}) is None


@pytest.mark.parametrize('blocked', ['active', 'unknown', 'busy', 'reserved'])
def test_rebinding_does_not_bypass_remote_or_purchase_locks(domain, blocked):
    service, bus, order = cancelled_fulfillment(domain)
    value = service.store.get(order.identity)
    if blocked == 'reserved':
        with service.store.orders.connect() as conn:
            conn.execute("INSERT INTO order_purchase_reservations VALUES (?,?,?,?,?,'reserved')",
                         ('reservation', order.identity, domain[3]['line_key'], 1, '{}'))
            conn.commit()
    elif blocked == 'busy':
        service.store.claim(order.identity, 'sync', value['revision'])
    else:
        changes = {'fulfillment_status': 'WAITING_DOMESTIC_SHIPMENT'} if blocked == 'active' else {'create_unknown': True}
        service.store.change(order.identity, value['revision'], changes)
    before = len(bus.calls)
    assert not service.receiving.detail(order.identity)['can_confirm']
    with pytest.raises(ValueError):
        command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2]})
    assert len(bus.calls) == before
    assert service.store.get(order.identity)['crossborderbus_order_id'] == 99


def test_new_plan_rolls_back_when_purchase_reservation_appears_during_verification(domain, monkeypatch):
    service, bus, order = cancelled_fulfillment(domain)
    request = bus.request
    def reserve(path, body, **kwargs):
        result = request(path, body, **kwargs)
        with service.store.orders.connect() as conn:
            conn.execute("INSERT INTO order_purchase_reservations VALUES (?,?,?,?,?,'reserved')",
                         ('reservation', order.identity, domain[3]['line_key'], 1, '{}'))
            conn.commit()
        return result
    monkeypatch.setattr(bus, 'request', reserve)
    with pytest.raises(ValueError, match='采购正在提交'):
        command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2]})
    current = service.store.get(order.identity)
    assert not current.get('attempt_history') and current['crossborderbus_order_id'] == 99 and not current['busy']
    assert not service.store.receiving.get(order.identity).get('confirmation_required')


def test_failed_plan_write_rolls_back_cancelled_archive(domain):
    import sqlite3

    service, _, order = cancelled_fulfillment(domain)
    old = service.store.receiving.get(order.identity)
    with service.store.orders.connect() as conn:
        conn.execute("CREATE TRIGGER reject_plan BEFORE UPDATE ON fulfillments BEGIN SELECT RAISE(ABORT, '模拟新方案写入失败'); END")
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match='模拟新方案写入失败'):
        command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2]})
    current = service.detail(order.identity)
    assert current['crossborderbus_order_id'] == 99 and current['fulfillment_status'] == 'CANCELLED'
    assert not current['attempt_history']
    assert service.store.receiving.get(order.identity) == old


def test_simultaneous_cancelled_plans_archive_only_once(domain, monkeypatch):
    from threading import Barrier

    service, bus, order = cancelled_fulfillment(domain)
    view = service.detail(order.identity)
    body = {'order_id': order.identity, 'revision': view['revision'],
            'receiving_revision': view['receiving_warehouse']['revision'],
            'plan': {'section_id': 1, 'warehouse_id': 10, 'service_ids': [2]}}
    barrier = Barrier(2)
    request = bus.request
    def query(path, data, **kwargs):
        response = request(path, data, **kwargs)
        barrier.wait(timeout=10)
        return response
    monkeypatch.setattr(bus, 'request', query)
    def save():
        try:
            service.command('plan', body)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: save(), range(2)))
    assert sorted(results) == [False, True]
    assert len(service.detail(order.identity)['attempt_history']) == 1
    assert service.store.receiving.get(order.identity)['revision'] == view['receiving_warehouse']['revision']


def test_configuration_change_after_cancellation_still_requires_rebinding_without_restarting(domain):
    from tests.test_logistics_settings import edit

    service, bus, order = cancelled_fulfillment(domain)
    old = service.detail(order.identity)
    configured = service.receiving.logistics.detail()['addresses'][0]
    edit(service.receiving, configured, warehouse_id=20)
    receiving = service.receiving.detail(order.identity)
    assert receiving['binding_stale'] and receiving['needs_rebind'] and receiving['can_confirm']
    before = len(bus.calls)
    with pytest.raises(ValueError, match='原绑定已失效'):
        command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 20, 'service_ids': [2]})
    with pytest.raises(ValueError, match='采购包裹'):
        assign_receiving(service.receiving, order, warehouse_id=20, existing_purchase_confirmed=False)
    assign_receiving(service.receiving, order, warehouse_id=20, fulfillment_revision=0)
    rebound = service.detail(order.identity)
    assert rebound['fulfillment_status'] == 'CANCELLED' and rebound['crossborderbus_order_id'] == 99
    assert rebound['revision'] == old['revision'] and not rebound['attempt_history']
    assert rebound['plan']['warehouse_id'] == 20 and rebound['plan']['service_ids'] == []
    assert len(bus.calls) == before and not service.process_one(order)
    current = command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 20, 'service_ids': [2]})
    assert current['attempt_history'][0]['receiving_warehouse'] == old['receiving_warehouse']
    assert current['receiving_warehouse']['warehouse_id'] == 20
    assert not service.process_one(order)


def test_legacy_cancellation_confirmation_flag_does_not_invalidate_valid_binding(domain):
    import json
    from tests.test_logistics_settings import edit

    service, _, order = cancelled_fulfillment(domain)
    binding = service.store.receiving.get(order.identity)
    with service.store.orders.connect() as conn:
        conn.execute('UPDATE order_receiving_warehouses SET value=? WHERE order_id=?',
                     (json.dumps({**binding, 'confirmation_required': True}), order.identity))
        conn.commit()
    assert not service.receiving.detail(order.identity)['needs_rebind']
    assert service.receiving.checked(order.identity)['revision'] == binding['revision']
    configured = service.receiving.logistics.detail()['addresses'][0]
    edit(service.receiving, configured, enabled=False)
    assert service.receiving.detail(order.identity)['needs_rebind']
    with pytest.raises(ValueError, match='原绑定已失效'):
        service.receiving.checked(order.identity)


def test_new_attempt_releases_expired_claim_and_ignores_old_sync_result(domain):
    service, _, order = cancelled_fulfillment(domain)
    current = service.store.get(order.identity)
    claimed, token = service.store.claim(order.identity, 'sync', current['revision'])
    with service.store.orders.connect() as conn:
        conn.execute('UPDATE fulfillments SET lease_until=0 WHERE id=?', (order.identity,))
        conn.commit()
    new = command(service, order, 'plan', plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2]})
    service.store.finish(order.identity, token, {'fulfillment_status': 'CANCELLED', 'crossborderbus_order_id': 99})
    assert service.detail(order.identity) == new
    assert new['fulfillment_status'] == 'NEW' and new['crossborderbus_order_id'] is None
