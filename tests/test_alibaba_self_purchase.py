"""自购只用替身创建订单；覆盖重复提交、未知回执、来源及金额变化。"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import io
import json
from urllib.parse import parse_qs
import pytest

from erp_web.context import get_context
from erp_web.services import alibaba_api_client as api
from erp_web.services.alibaba_self_purchase_client import AlibabaSelfPurchaseClient, ADDRESSES, PREVIEW, CREATE, ORDER_LIST, PAY_URL
from erp_web.services.alibaba_self_purchase_service import AlibabaSelfPurchaseService, account_scope, normalized_preview, default_address
from erp_web.stores.alibaba_self_purchase_store import AlibabaSelfPurchaseStore

CONFIG = {"app_key": "123", "app_secret": "test-secret", "access_token": "test-token"}
CANDIDATE = {"id": "1", "offer_id": "12345", "sku_id": "67890", "specification": "蓝色", "product_url": "https://detail.1688.com/offer/12345.html"}
NUMBER = "3317081160226242182"
ADDRESS = {"fullName": "测试用户", "mobilePhone": "13800000000", "phone": "", "addressCodeText": "四川省 成都市 武侯区", "address": "测试路1号", "post": "610000", "isDefault": True}


def order_payload(number=NUMBER):
    return {"success": True, "result": {"baseInfo": {"idOfStr": number, "status": "waitbuyerpay"},
        "productItems": [{"productID": 12345, "skuID": 67890, "specId": "spec-one"}]}}


def preview_payload():
    # 实际预览不会在 cargoList 中回传 quantity。
    return {"success": True, "orderPreviewResuslt": [{"status": True, "flowFlag": "fenxiaonew",
        "tradeModeNameList": ["assureTrade"], "sumPaymentNoCarriage": 980, "sumCarriage": 600, "sumPayment": 1580,
        "cargoList": [{"offerId": 12345, "skuId": 67890, "specId": "spec-one", "amount": 9.8, "finalUnitPrice": 9.8}],
        "payChannelInfos": [{"name": "alipay"}, {"name": "shegou"}]}]}


class Client:
    def __init__(self):
        self.calls = []
        self.pre = preview_payload()
        self.creation = {"success": True, "result": {"orderIdList": [int(NUMBER)]}}
        self.orders = []
        self.order = order_payload()
        self.fail_create = False
        self.pay_url = "https://trade.1688.com/order/cashier.htm?orderId=" + NUMBER

    def query(self, name, number):
        self.calls.append((name, number))
        assert name == api.ORDER_DETAIL
        return deepcopy(self.order)

    def call(self, name, params):
        self.calls.append((name, deepcopy(params)))
        if name == ADDRESSES: return {"success": True, "result": {"receiveAddressItems": [ADDRESS]}}
        if name == PREVIEW: return deepcopy(self.pre)
        if name == CREATE:
            if self.fail_create: raise TimeoutError("不可公开的底层请求信息")
            return self.creation
        if name == ORDER_LIST: return {"success": True, "result": {"orderList": self.orders}}
        if name == PAY_URL: return {"success": True, "payUrl": self.pay_url}
        pytest.fail("未批准的接口")


from tests.test_order_procurement import domain, purchase_body, job
from erp_web.product_model.sku_model import collected_skus
from erp_web.runtime_units.order_source_bindings import bindings_from_publish_job


@pytest.fixture
def setup_purchase(domain):
    procurement, order, key = domain
    config, client = deepcopy(CONFIG), Client()
    frozen = job(source_id="67890")
    source = {"source_platform": "1688", "source_url": CANDIDATE["product_url"], "currency": "CNY",
              "skus": [{"id": "67890", "offer_id": "12345", "spec_id": "spec-one", "name": "蓝色", "options": {}}]}
    facts = collected_skus(source)
    frozen["product"].update(source=source, sku_items=facts)
    frozen["platforms"]["yandex"]["result"]["sku_results"][0]["sku_id"] = facts[0]["id"]
    bindings = bindings_from_publish_job(frozen)
    procurement.store.add_bindings(bindings)
    procurement.select_source({"order_id": order.identity, "line_key": key, "revision": 0,
                               "candidate_id": bindings[0].identity})
    service = AlibabaSelfPurchaseService(AlibabaSelfPurchaseStore(get_context().paths.data_dir / "alibaba-self-purchases.sqlite3"), lambda: config, procurement, client_factory=lambda _: client)
    return service, client, config, (order, key)


def target(service):
    with service.procurement.store.orders.connect() as conn:
        row = conn.execute("SELECT order_id,line_key FROM order_sources LIMIT 1").fetchone()
    return {"order_id": row["order_id"], "line_key": row["line_key"]}


def preview(service, quantity=1):
    return service.preview({**target(service), "candidate_id": "1", "quantity": quantity})["record"]


def create(service, record):
    return service.create({"preview_id": record["id"], "pay_channel": "shegou"})["record"]


def test_preview_uses_frozen_sku_default_address_and_platform_selected_flow(setup_purchase):
    service, client, _, _ = setup_purchase
    record = preview(service)
    assert record["preview"]["total_fen"] == 1580
    assert record["preview"]["flow"] == "fenxiaonew"
    params = next(p for n, p in client.calls if n == PREVIEW)
    assert "flow" not in params
    assert params["cargoParamList"] == [{"offerId": 12345, "specId": "spec-one", "quantity": 1}]
    assert not any(n in (CREATE, api.ORDER_DETAIL) for n, _ in client.calls)
    assert CONFIG["access_token"] not in json.dumps(service.options(**target(service)))


def test_create_is_idempotent_and_never_pays(setup_purchase):
    service, client, _, _ = setup_purchase
    record = preview(service)
    first, second = create(service, record), create(service, record)
    assert first == second and first["state"] == "created"
    assert first["order_numbers"] == [NUMBER]
    calls = [p for n, p in client.calls if n == CREATE]
    assert len(calls) == 1
    assert calls[0]["outOrderId"] == record["id"]
    assert calls[0]["flow"] == "fenxiaonew" and calls[0]["preSelectPayChannel"] == "shegou"
    assert calls[0]["tradeType"] == "assureTrade"
    assert not any("preparePay" in n for n, _ in client.calls)
    assert first["purchase_record_id"]
    assert service.options(**target(service))["remaining_quantity"] == 1
    assert preview(service)["state"] == "preview"


def test_concurrent_submit_has_one_owner(setup_purchase):
    service, client, _, _ = setup_purchase
    record = preview(service)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(service, record), range(2)))
    assert len([1 for n, _ in client.calls if n == CREATE]) == 1
    assert {r["state"] for r in results} <= {"created", "submitting"}


def test_timeout_survives_restart_and_cannot_be_replayed(setup_purchase):
    service, client, config, _ = setup_purchase
    record = preview(service); client.fail_create = True
    result = create(service, record)
    assert result["state"] == "unknown" and "不可公开" not in result["message"]
    service.store = AlibabaSelfPurchaseStore(get_context().paths.data_dir / "alibaba-self-purchases.sqlite3")
    assert create(service, record)["state"] == "unknown"
    assert len([1 for n, _ in client.calls if n == CREATE]) == 1
    assert service.options(**target(service))["records"][0]["state"] == "unknown"
    read = service.reconcile({"preview_id": record["id"]})["record"]
    assert read["state"] == "unknown"
    client.orders = [{**order_payload()["result"], "exAttributes": {"outOrderId": record["id"]}}]
    read = service.reconcile({"preview_id": record["id"]})["record"]
    assert read["state"] == "created" and read["order_numbers"] == [NUMBER]


@pytest.mark.parametrize("change", ["price", "flow", "channel", "source"])
def test_changed_preview_never_creates(setup_purchase, change):
    service, client, _, (order, key) = setup_purchase
    record = preview(service)
    if change == "price":
        client.pre["orderPreviewResuslt"][0].update(sumPaymentNoCarriage=1080, sumPayment=1680)
    elif change == "flow": client.pre["orderPreviewResuslt"][0]["flowFlag"] = "general"
    elif change == "channel": client.pre["orderPreviewResuslt"][0]["payChannelInfos"] = [{"name": "alipay"}]
    else:
        service.procurement.select_source({**target(service), "revision": 1, "source": {"product_url": CANDIDATE["product_url"], "source_sku_id": "99999", "specification": "新规格"}})
    result = create(service, record)
    assert result["state"] == "failed"
    assert not any(n == CREATE for n, _ in client.calls)


def test_preview_rejects_platform_sku_mismatch_without_history_lookup(setup_purchase):
    service, client, _, _ = setup_purchase
    client.pre["orderPreviewResuslt"][0]["cargoList"][0]["skuId"] = 99999
    with pytest.raises(api.AlibabaApiError, match="SKU"):
        preview(service)
    assert not any(n in (api.ORDER_DETAIL, CREATE) for n, _ in client.calls)


def test_changed_credentials_cannot_submit_old_preview(setup_purchase):
    service, client, config, _ = setup_purchase
    record = preview(service); config["access_token"] = "other-account"
    with pytest.raises(ValueError, match="切换"):
        create(service, record)
    assert not any(n == CREATE for n, _ in client.calls)


def test_old_preview_expired_by_new_preview(setup_purchase):
    service, client, _, _ = setup_purchase
    old, new = preview(service), preview(service)
    assert create(service, old)["state"] == "expired"
    assert create(service, new)["state"] == "created"
    assert len([1 for n, _ in client.calls if n == CREATE]) == 1


def test_expiry_rejects_submission(setup_purchase, monkeypatch):
    service, client, _, _ = setup_purchase
    record = preview(service)
    monkeypatch.setattr("erp_web.stores.alibaba_self_purchase_store.time.time", lambda: record["preview"]["expires_at"] + 1)
    with pytest.raises(ValueError, match="过期"):
        create(service, record)
    assert not any(n == CREATE for n, _ in client.calls)


def test_ambiguous_create_receipt_is_not_success(setup_purchase):
    service, client, _, _ = setup_purchase
    record = preview(service); client.creation = {"success": True, "result": {}}
    assert create(service, record)["state"] == "unknown"


def test_remote_cancel_releases_duplicate_guard(setup_purchase):
    service, client, _, _ = setup_purchase
    record = preview(service); create(service, record)
    client.order["result"]["baseInfo"]["status"] = "cancel"
    result = service.reconcile({"preview_id": record["id"]})["record"]
    assert result["state"] == "closed"
    assert preview(service)["state"] == "preview"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://alipay.com.bad.test", "http://trade.1688.com/x", "https://user:password@trade.1688.com/x"])
def test_cashier_rejects_untrusted_url(setup_purchase, url):
    service, client, _, _ = setup_purchase
    record = preview(service); create(service, record); client.pay_url = url
    with pytest.raises(api.AlibabaApiError, match="官方收银台"):
        service.cashier({"preview_id": record["id"]})


def test_cashier_returns_official_url_without_paying(setup_purchase):
    service, client, _, _ = setup_purchase
    record = preview(service); create(service, record)
    assert service.cashier({"preview_id": record["id"]})["url"] == client.pay_url
    assert client.calls[-1] == (PAY_URL, {"orderIds": [int(NUMBER)], "payPlatformType": "PC"})


def test_write_transport_is_signed_non_retrying_and_not_available_via_query(monkeypatch):
    calls = []
    def send(req, **kwargs):
        calls.append((req, kwargs)); return io.BytesIO(b'{"success":true,"result":{}}')
    monkeypatch.setattr(api, "managed_urlopen", send)
    client = AlibabaSelfPurchaseClient(CONFIG)
    client.call(CREATE, {"outOrderId": "fixed-key", "cargoParamList": [{"offerId": 12345, "specId": "spec-one", "quantity": 1}]})
    req, kwargs = calls[0]
    assert kwargs["request_context"].semantics == "write"
    assert kwargs["request_context"].max_attempts == 1 and kwargs["follow_redirects"] is False
    assert parse_qs(req.data.decode())["outOrderId"] == ["fixed-key"]
    with pytest.raises(api.AlibabaApiError): client.query(CREATE, NUMBER)
    with pytest.raises(api.AlibabaApiError): client.call("alibaba.trade.pay.protocolPay.preparePay", {})


def test_default_address_must_be_unique():
    with pytest.raises(ValueError, match="唯一"):
        default_address({"result": {"receiveAddressItems": [ADDRESS, ADDRESS]}})


@pytest.mark.parametrize("field,value", [("sumPayment", "1580"), ("sumPayment", -1), ("sumCarriage", 100)])
def test_malformed_money_rejected(field, value):
    payload = preview_payload(); payload["orderPreviewResuslt"][0][field] = value
    with pytest.raises(api.AlibabaApiError, match="金额"):
        normalized_preview(payload, CANDIDATE, default_address({"result": {"receiveAddressItems": [ADDRESS]}}), 1, "spec-one")


def test_order_and_current_store_ownership_required(setup_purchase):
    service, client, _, _ = setup_purchase
    record = preview(service)
    current = target(service)
    with pytest.raises(ValueError, match="不存在"):
        service.options("missing-order", current["line_key"])
    with pytest.raises(ValueError, match="唯一"):
        service.options(current["order_id"], "missing-line")
    service.procurement.accounts_provider = lambda: {"yandex": "other-account"}
    with pytest.raises(ValueError, match="不属于"):
        create(service, record)
    assert not any(n == CREATE for n, _ in client.calls)


def test_changed_credentials_do_not_bypass_old_pending_purchase(setup_purchase):
    service, client, config, _ = setup_purchase
    client.fail_create = True
    create(service, preview(service))
    config["access_token"] = "refreshed-token"
    with pytest.raises(ValueError, match="已有待处理"):
        preview(service)
    assert len([1 for n, _ in client.calls if n == CREATE]) == 1


def test_known_acl_failure_is_definite_and_does_not_leak_response(monkeypatch):
    from urllib.error import HTTPError
    def send(*args, **kwargs):
        raise HTTPError('https://gw.open.1688.com/', 400, 'bad request', {}, io.BytesIO(b'{"error_code":"gw.APIACLDecline","error_message":"test-secret"}'))
    monkeypatch.setattr(api, "managed_urlopen", send)
    with pytest.raises(api.AlibabaApiRejected, match="接口权限") as error:
        AlibabaSelfPurchaseClient(CONFIG).call(CREATE, {})
    assert "test-secret" not in str(error.value)


def test_route_validates_body_and_preserves_useful_safe_errors(monkeypatch):
    from types import SimpleNamespace
    from erp_web.http_route_units import order_routes as routes
    responses = []
    handler = SimpleNamespace(path="/api/orders/alibaba-purchase/create", read_body=lambda: {"preview_id": "p", "pay_channel": "unknown"},
        send_json=lambda body, status=200: responses.append((body, status)))
    calls = []
    monkeypatch.setattr(routes.alibaba_self_purchase_facade, "command", lambda *args: calls.append(args))
    routes.handle_self_purchase_create(handler)
    assert not calls and responses[-1][1] == 400
    handler.read_body = lambda: {"preview_id": "p", "pay_channel": "shegou"}
    def reject(*_): raise ValueError("价格已变化，请重新预览")
    monkeypatch.setattr(routes.alibaba_self_purchase_facade, "command", reject)
    routes.handle_self_purchase_create(handler)
    assert responses[-1] == ({"ok": False, "error": "价格已变化，请重新预览"}, 400)


def test_self_purchase_database_does_not_modify_main_schema_and_reopens(setup_purchase):
    from erp_web.db import ErpDatabase
    service, client, _, _ = setup_purchase
    record = preview(service)
    created = create(service, record)
    context = get_context()
    ErpDatabase(context.paths.db_path)
    reopened = AlibabaSelfPurchaseStore(service.store.path)
    result = reopened.get(record["id"], account_scope(CONFIG))
    assert result["result"]["order_numbers"] == created["order_numbers"]
    assert result["state"] == "created"
    assert reopened.path.stat().st_mode & 0o777 == 0o600
    with context.db._connect() as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='alibaba_self_purchases'").fetchone()


def test_automatic_order_attaches_once_and_supports_existing_logistics_lookup(setup_purchase):
    service, _, _, (order, key) = setup_purchase
    record = preview(service, quantity=2)
    result = create(service, record)
    assert result["purchase_record_id"]
    create(service, record)
    item = service.procurement.detail(order.identity)["lines"][0]
    assert item["purchased_quantity"] == 2 and item["remaining_quantity"] == 0
    assert len(item["records"]) == 1
    linked = service.procurement.store.purchase_record(order.identity, result["purchase_record_id"], {"yandex": "4"})
    assert linked.purchase_order_number == NUMBER and linked.source.source_sku_id == "67890"
    with pytest.raises(ValueError, match="已采购齐全"):
        preview(service)
    with pytest.raises(ValueError, match="1688 取消"):
        service.procurement.cancel_purchase({"order_id": order.identity, "record_id": linked.id})


def test_manual_purchase_after_preview_prevents_overbuy(setup_purchase):
    service, client, _, (order, key) = setup_purchase
    record = preview(service, quantity=2)
    service.procurement.record_purchase(purchase_body(order, key))
    assert create(service, record)["state"] == "failed"
    assert not any(n == CREATE for n, _ in client.calls)


def test_unknown_purchase_reserves_quantity_and_source_until_reconciled(setup_purchase):
    service, client, _, (order, key) = setup_purchase
    client.fail_create = True
    record = create(service, preview(service, quantity=2))
    assert record["state"] == "unknown"
    with pytest.raises(ValueError, match="剩余"):
        service.procurement.record_purchase(purchase_body(order, key))
    with pytest.raises(ValueError, match="等待核验"):
        service.procurement.select_source({**target(service), "revision": 1, "source": {"product_url": CANDIDATE["product_url"], "specification": "新来源"}})
    client.orders = [{**order_payload()["result"], "exAttributes": {"outOrderId": record["id"]}}]
    client.order["result"]["baseInfo"]["status"] = "cancel"
    service.reconcile({"preview_id": record["id"]})
    item = service.procurement.detail(order.identity)["lines"][0]
    assert item["purchased_quantity"] == 0 and item["records"][0]["status"] == "cancelled"
    service.procurement.record_purchase(purchase_body(order, key))


def test_definite_preflight_failure_releases_quantity(setup_purchase):
    service, client, _, (order, key) = setup_purchase
    record = preview(service, quantity=2)
    client.pre["orderPreviewResuslt"][0]["flowFlag"] = "general"
    assert create(service, record)["state"] == "failed"
    service.procurement.record_purchase(purchase_body(order, key, quantity=2))


def test_remote_receipt_survives_local_attachment_failure(setup_purchase, monkeypatch):
    service, client, _, (order, key) = setup_purchase
    record = preview(service, quantity=2)
    complete = service.procurement.store.complete_purchase
    def fail(*args, **kwargs): raise RuntimeError("本地临时失败")
    monkeypatch.setattr(service.procurement.store, "complete_purchase", fail)
    result = create(service, record)
    assert result["state"] == "created" and result["order_numbers"] == [NUMBER]
    assert not result["purchase_record_id"] and "关联" in result["message"]
    with pytest.raises(ValueError, match="剩余"):
        service.procurement.record_purchase(purchase_body(order, key))
    monkeypatch.setattr(service.procurement.store, "complete_purchase", complete)
    result = service.reconcile({"preview_id": record["id"]})["record"]
    assert result["purchase_record_id"]
    assert len([n for n, _ in client.calls if n == CREATE]) == 1


def test_old_unbound_preview_cannot_create_order(setup_purchase):
    service, client, _, _ = setup_purchase
    record = preview(service)
    row = service.store.get(record["id"], account_scope(CONFIG))
    del row["payload"]["binding"]
    old = service.store.preview("legacy-listing", account_scope(CONFIG), row["payload"])
    with pytest.raises(ValueError, match="未关联销售订单"):
        create(service, old)
    assert not any(n == CREATE for n, _ in client.calls)


def test_legacy_receipt_database_migrates_without_losing_data(tmp_path):
    import sqlite3
    path = tmp_path / "receipts.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE alibaba_self_purchases (id TEXT PRIMARY KEY,account TEXT,listing_id TEXT,state TEXT,payload TEXT,result TEXT,created_at TEXT,updated_at TEXT)")
        conn.execute("INSERT INTO alibaba_self_purchases VALUES ('old','account','listing','preview','{}','{}','','')")
        conn.execute("PRAGMA user_version=1")
    store = AlibabaSelfPurchaseStore(path)
    assert store.get("old", "account")["target_key"] == "listing"
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_order_cancelled_during_preflight_does_not_create(setup_purchase):
    service, client, _, (order, key) = setup_purchase
    record = preview(service)
    call = client.call
    def change_order(name, params):
        if name == PREVIEW:
            with service.procurement.store.orders.connect() as conn:
                snapshot = order.model_copy(update={"state": "cancelled"})
                conn.execute("UPDATE orders SET snapshot=? WHERE id=?", (snapshot.model_dump_json(), order.identity))
                conn.commit()
        return call(name, params)
    client.call = change_order
    result = create(service, record)
    assert result["state"] == "failed" and "状态" in result["message"]
    assert not any(n == CREATE for n, _ in client.calls)


def test_different_sales_orders_do_not_share_purchase_guard(setup_purchase):
    service, client, _, (order, key) = setup_purchase
    client.fail_create = True
    create(service, preview(service))
    other = order.model_copy(update={"order_id": "456"})
    with service.procurement.store.orders.connect() as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(orders)")]
        old = dict(conn.execute("SELECT * FROM orders WHERE id=?", (order.identity,)).fetchone())
        old.update(id=other.identity, order_id="456", snapshot=other.model_dump_json())
        conn.execute("INSERT INTO orders (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")", [old[c] for c in columns])
        conn.commit()
    service.procurement.select_source({"order_id": other.identity, "line_key": key, "revision": 0, "source": service.procurement.detail(order.identity)["lines"][0]["selection"]["source"]})
    assert service.preview({"order_id": other.identity, "line_key": key, "candidate_id": "1", "quantity": 1})["record"]["state"] == "preview"


@pytest.mark.parametrize("change,reason", [
    ("missing_spec", "specId"), ("wrong_offer", "商品编号"),
    ("wrong_selection", "规格标识不一致"), ("missing_binding", "已上架商品"),
    ("changed_options", "颜色、尺寸"),
])
def test_incomplete_or_conflicting_published_identity_blocks_all_remote_calls(setup_purchase, change, reason):
    service, client, _, _ = setup_purchase
    with service.procurement.store.orders.connect() as conn:
        row = conn.execute("SELECT id,binding_json FROM sales_sku_bindings").fetchone()
        binding = json.loads(row["binding_json"])
        if change == "missing_spec":
            binding["source"]["source_spec_id"] = ""
            conn.execute("UPDATE order_sources SET source_json=json_set(source_json,'$.source_spec_id','')")
        elif change == "wrong_offer":
            binding["source"]["source_offer_id"] = "other-offer"
            conn.execute("UPDATE order_sources SET source_json=json_set(source_json,'$.source_offer_id','other-offer')")
        elif change == "wrong_selection":
            conn.execute("UPDATE order_sources SET source_json=json_set(source_json,'$.source_spec_id','different-spec')")
        elif change == "changed_options":
            binding["source"]["purchase_block_reason"] = "上架规格已修改，与采集规格不一致，请核对颜色、尺寸等选项"
        if change == "missing_binding":
            conn.execute("DELETE FROM sales_sku_bindings")
        else:
            conn.execute("UPDATE sales_sku_bindings SET binding_json=? WHERE id=?", (json.dumps(binding), row["id"]))
        conn.commit()
    options = service.options(**target(service))
    assert not options["can_purchase"] and reason in options["blocked_reason"]
    with pytest.raises(ValueError, match="不能自动采购"):
        preview(service)
    assert client.calls == []


def test_legacy_preview_with_unverified_spec_cannot_be_submitted(setup_purchase):
    service, client, _, _ = setup_purchase
    record = preview(service)
    with service.store.connect() as conn:
        conn.execute("UPDATE alibaba_self_purchases SET payload=json_set(payload,'$.params.cargoParamList[0].specId','history-derived') WHERE id=?", (record["id"],))
        conn.commit()
    result = create(service, record)
    assert result["state"] == "failed"
    assert not any(n == CREATE for n, _ in client.calls)
