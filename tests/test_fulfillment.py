"""跨境履约关键回归：幂等、未知回执、采购数量、配送范围与取消确认。"""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from erp_web.schemas.fulfillment import FulfillmentError
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
        self.catalog()

    def identity(self):
        return self.account

    def credentials(self):
        return {}

    def catalog(self):
        result = {"identity": self.account, "sections": [{"section_id": 1, "section_name": "合作渠道", "storehouse_list": [{"id": 10, "name": "A仓"}, {"id": 20, "name": "B仓"}]}]}
        self.store.set_setting("catalog", result)
        return result

    def services(self, section_id, warehouse_id):
        return {"core_data": [{"id": 2, "name": "拆包验货"}], "optional_data": [{"id": 3, "name": "代贴标"}]}

    def request(self, path, body, **kwargs):
        self.calls.append((path, body))
        if path.endswith("createOrder"):
            if self.create_error:
                raise self.create_error
            self.remote = {"id": 99, "order_status": 0, "data_status": 1, "is_delete": 0}
            self.lookup = {"id": 99, "sid": 10, "sheet_info": {"section": 1, "section_order": body["order_data"][0]["order_number"]}, "package_list": [{"status": 0}]}
            return {"data": [{"order_number": body["order_data"][0]["order_number"], "order_id": 99}]}
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
    order = OrderSnapshot(platform="yandex", account_id="4", order_id="123", fulfillment="FBS", status="PROCESSING", state="pending_shipment", items=[line], delivery={"warehouse_id": "platform-warehouse", "method_id": "method-1", "method_name": "平台配送", "country": "RU"})
    def snapshot(value):
        event = OrderEvent(platform=value.platform, account_id=value.account_id, topic="fixture", resource=value.order_id, payload={"revision": time.time_ns()})
        orders.enqueue(event)
        job = orders.claim({value.platform: value.account_id}, now=time.time())
        orders.save_snapshot(job, value, now=time.time())
        orders.finish(job, now=time.time())
    snapshot(order)
    accounts = {"yandex": "4"}
    procurement = OrderProcurementService(OrderProcurementStore(orders), lambda: accounts, lambda p: "shop-a")
    key = order_line_key(line)
    procurement.select_source({"order_id": order.identity, "line_key": key, "revision": 0, "source": {"supplier": "供应商", "source_platform": "1688", "product_url": "https://detail.1688.com/offer/123.html", "source_sku_id": "source-1", "specification": "银色"}})
    procurement.record_purchase({"order_id": order.identity, "line_key": key, "revision": 1, "request_id": "purchase", "quantity": 2, "purchase_order_number": "PO-123"})
    store = FulfillmentStore(orders)
    bus = Bus(store)
    service = FulfillmentService(store, bus, lambda: accounts, procurement.detail, start_worker=False)
    rule = {"platform": "yandex", "account_id": "4", "fulfillment": "FBS", "platform_warehouse_id": "platform-warehouse", "delivery_method_id": "method-1", "delivery_method_name": "平台配送", "country": "RU", "section_id": 1, "warehouse_id": 10, "service_ids": [2, 3], "compatible_warehouse_ids": [10, 20], "confirmed": True}
    service.save_rule(rule)
    purchase = procurement.detail(order.identity)["lines"][0]["records"][0]
    parcel = {"id": "parcel-1", "line_key": key, "purchase_record_id": purchase["id"], "carrier": "顺丰", "tracking_number": "SF123", "quantity": 2}
    return service, bus, order, parcel, snapshot, accounts


def command(service, order, action, **body):
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


def test_override_stays_in_compatible_warehouses_and_preserves_default(domain):
    service, _, order = ready(domain)
    command(service, order, "pause")
    assert not service.process_one(order)
    command(service, order, "plan", plan={"section_id": 1, "warehouse_id": 20, "service_ids": [2]})
    assert service.detail(order.identity)["plan"]["warehouse_id"] == 20
    assert service.store.rules()[0]["warehouse_id"] == 10
    with pytest.raises(FulfillmentError):
        command(service, order, "plan", plan={"section_id": 2, "warehouse_id": 20, "service_ids": [2]})
    with pytest.raises(FulfillmentError):
        command(service, order, "plan", plan={"section_id": 1, "warehouse_id": 999, "service_ids": [2]})
    with pytest.raises(FulfillmentError):
        command(service, order, "plan", plan={"section_id": 1, "warehouse_id": 20, "service_ids": []})


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


def test_missing_delivery_never_infers_route_from_country(domain):
    service, bus, order = ready(domain)
    domain[4](order.model_copy(update={"delivery": order.delivery.model_copy(update={"method_id": ""})}))
    assert "配送方式" in service.detail(order.identity)["blocked_reason"]
    service.process_one(order)
    assert not bus.calls


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
    assert set(summary) == {"fulfillment_status", "crossborderbus_order_id", "busy", "operation", "create_unknown", "cancel_requested", "cancel_rejected", "error_message", "label_error", "has_domestic_waybill"}
    assert view.state == "pending_shipment"


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
    view = service.order_detail(order.identity)[0]
    assert service.store.summaries([view]) == {}
    assert not service.store.tracked(order)
    value = service.store.ensure(view)
    value = service.store.change(view.id, value["revision"], {"label_error": "面单获取失败"})
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
