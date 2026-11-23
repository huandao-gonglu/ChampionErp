"""万邦错误码分类、api_info 快照、两层额度账本、分级放行、GMV 入账与 BusinessQuery。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import json
from types import SimpleNamespace
import urllib.request

import pytest

from erp_web.business_query_composition import build_business_data_catalog
from erp_web.context import bind_context, get_context
from erp_web.facades import alibaba_authorization_facade, business_data_facade
from erp_web.runtime_units.business_data_capabilities import BusinessDataScope, business_data_read
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.business_data import DataReadRequest
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.services import onebound_api_transport as transport
from erp_web.services import onebound_quota_service as quota_service
from erp_web.services.alibaba_api_client import (
    AlibabaApiError,
    AlibabaApiRejected,
    CREATE,
    ORDER_DETAIL,
    PRODUCT_DETAIL,
    alibaba_provider,
)
from erp_web.services.alibaba_authorization_service import AlibabaAuthorizationService
from erp_web.services.alibaba_credentials import application_fingerprint
from erp_web.services.external_request_limiter import ExternalRequestLimiter
from erp_web.services.external_request_manager import ExternalRequestManager
from erp_web.services.platform_request_policy import classify_response
from erp_web.stores.external_request_store import ExternalRequestStore
from erp_web.stores.onebound_quota_store import OneBoundQuotaStore, next_cst_midnight
from tests.test_accounts import accounts_http, register
from tests.test_alibaba_self_purchase import domain, setup_purchase
from tests.test_onebound_api import CONFIG, DIRECTORY_CONFIG, configure

_CST = timezone(timedelta(hours=8))


class _FakeHttpResponse(io.BytesIO):
    status = 200
    headers = {}


def _json_response(payload: dict) -> _FakeHttpResponse:
    return _FakeHttpResponse(json.dumps(payload).encode("utf-8"))


# -- 1. api_info 解析 ----------------------------------------------------------


def test_parse_api_info_extracts_whitelisted_fields_and_ignores_invalid():
    parsed = quota_service.parse_api_info(
        "today:166 max:10000 all[585=166+39+380];expires:2030-02-19"
    )
    assert parsed == {
        "today": 166,
        "max": 10000,
        "expires": "2030-02-19",
        "raw_all": "all[585=166+39+380]",
    }
    assert quota_service.parse_api_info("today:12") == {
        "today": 12,
        "max": None,
        "expires": "",
        "raw_all": "",
    }
    assert quota_service.parse_api_info("expires:2099-99-99") is None
    assert quota_service.parse_api_info("garbage without fields") is None
    assert quota_service.parse_api_info(None) is None
    long_all = "today:5 max:100 all[" + "x" * 300 + "];expires:2030-01-01"
    parsed_long = quota_service.parse_api_info(long_all)
    assert parsed_long is not None and len(parsed_long["raw_all"]) <= 200


# -- 2. 万邦错误码分类（含 4013 时钟校验）--------------------------------------


def test_classify_onebound_error_codes_and_cst_midnight():
    now = datetime(2026, 11, 22, 15, 30, 0, tzinfo=_CST).timestamp()
    expected_midnight = datetime(2026, 11, 23, 0, 0, 0, tzinfo=_CST).timestamp()

    f_4013 = classify_response("onebound", 200, {}, json.dumps({"error_code": "4013"}).encode(), now=now)
    assert f_4013 is not None
    assert f_4013.code == "ONEBOUND_DAILY_RATE_LIMITED"
    assert f_4013.scope == "account"
    assert f_4013.resume_at == expected_midnight

    for code in ("4016", "4007", "4005"):
        failure = classify_response("onebound", 200, {}, json.dumps({"error_code": code}).encode(), now=now)
        assert failure is not None and failure.code == "ONEBOUND_ACCOUNT_DISABLED" and failure.scope == "account"
        assert code in failure.message

    for code in ("4006", "4012"):
        failure = classify_response("onebound", 200, {}, json.dumps({"error_code": code}).encode(), now=now)
        assert failure is not None and failure.code == "ONEBOUND_AUTH_FAILED" and failure.scope == "interface"

    f_4008 = classify_response("onebound", 200, {}, json.dumps({"error_code": "4008"}).encode(), now=now)
    assert f_4008 is not None and f_4008.code == "ONEBOUND_RATE_LIMITED" and f_4008.scope == "account"

    f_4009 = classify_response("onebound", 200, {}, json.dumps({"error_code": "4009"}).encode(), now=now)
    assert f_4009 is not None and f_4009.code == "ONEBOUND_SERVER_ERROR" and f_4009.scope == "" and f_4009.retryable

    f_4003 = classify_response("onebound", 200, {}, json.dumps({"error_code": "4003"}).encode(), now=now)
    assert f_4003 is not None and f_4003.code == "ONEBOUND_REQUEST_INVALID" and f_4003.scope == "request"

    # shop_bind_list 的 2000 是正常空结果，不是失败；业务接口 2000 走 request 级拒绝。
    assert classify_response(
        "onebound", 200, {}, json.dumps({"error_code": "2000", "reason": "empty"}).encode(),
        interface="sys/shop_bind_list", now=now,
    ) is None
    f_2000 = classify_response(
        "onebound", 200, {}, json.dumps({"error_code": "2000", "reason": "Request need user authorized"}).encode(),
        interface="com.alibaba.fenxiao.crossborder/" + PRODUCT_DETAIL, now=now,
    )
    assert f_2000 is not None and f_2000.scope == "request"
    assert classify_response("onebound", 200, {}, json.dumps({"error_code": "0000", "response": {}}).encode()) is None


# -- 3. 账本、分级放行、并发安全与跨日重置 --------------------------------------


def test_quota_store_grant_tiered_admission_reserve_and_day_rollover(tmp_path):
    store = OneBoundQuotaStore(tmp_path / "onebound-quota.sqlite3")
    fp = quota_service.key_fingerprint("vendor-key")
    day1 = datetime(2026, 11, 22, 10, 0, 0, tzinfo=_CST).timestamp()
    day2 = datetime(2026, 11, 23, 0, 1, 0, tzinfo=_CST).timestamp()

    # 1. 新买家首次准入只发放一次初始额度（2 次用于快速耗尽）。
    store.admit_and_spend(fp, "buyer-a", "collect", initial_grant=2, now=day1)
    store.admit_and_spend(fp, "buyer-a", "collect", initial_grant=200, now=day1 + 1)
    assert store.read_buyer_summary(fp, "buyer-a")["balance"] == 0
    assert store.read_buyer_summary(fp, "buyer-a")["granted"] == 2

    # 2. 余额为 0 时：collect 本地拒绝；trade 和 auth 照常放行且余额可为负。
    with pytest.raises(ExternalRequestBlocked) as err:
        store.admit_and_spend(fp, "buyer-a", "collect", initial_grant=200, now=day1 + 2)
    assert err.value.code == "ONEBOUND_QUOTA_EXHAUSTED"
    assert err.value.details["local_rejection"] is True

    store.admit_and_spend(fp, "buyer-a", "trade", initial_grant=200, now=day1 + 3)
    store.admit_and_spend(fp, "buyer-a", "auth", initial_grant=200, now=day1 + 4)
    summary = store.read_buyer_summary(fp, "buyer-a")
    assert summary["balance"] == -2
    assert (summary["spent_collect"], summary["spent_trade"], summary["spent_auth"]) == (2, 1, 1)

    # 3. 平台层保留额度：max=100 -> reserve=50；today=50 时拒绝 collect，放行 trade/auth。
    store.credit_earn(fp, "buyer-a", "order-1", 500, now=day1 + 5)
    assert store.read_buyer_summary(fp, "buyer-a")["balance"] == 498
    store.observe_snapshot(fp, today=50, daily_max=100, expires="2026-11-25", now=day1 + 6)
    status = store.read_key_status(fp, configured=True, now=day1 + 6)
    assert status["reserve"] == 50 and status["expiring_soon"] is True and status["days_until_expiry"] == 3

    with pytest.raises(ExternalRequestBlocked) as err_reserve:
        store.admit_and_spend(fp, "buyer-a", "collect", now=day1 + 7)
    assert err_reserve.value.code == "ONEBOUND_DAILY_RESERVE_PROTECTED"
    store.admit_and_spend(fp, "buyer-a", "trade", now=day1 + 8)
    store.admit_and_spend(fp, "buyer-a", "auth", now=day1 + 9)

    # 4. 达到平台每日上限（today_est == 100）：全部类别拒绝，次日北京时间 00:00 后自动恢复。
    store.observe_snapshot(fp, today=100, daily_max=100, expires="2026-11-25", now=day1 + 10)
    for cat in ("collect", "trade", "auth"):
        with pytest.raises(ExternalRequestBlocked) as err_max:
            store.admit_and_spend(fp, "buyer-a", cat, now=day1 + 11)
        assert err_max.value.code == "ONEBOUND_DAILY_RATE_LIMITED"
        assert err_max.value.details["resume_at"] == next_cst_midnight(day1 + 11)

    # 跨日后自动恢复放行。
    store.admit_and_spend(fp, "buyer-a", "collect", now=day2)
    assert store.read_key_status(fp, configured=True, now=day2 + 1)["today_estimated"] == 1

    # 5. Key 过期后全部类别拒绝。
    expired_day = datetime(2026, 11, 26, 1, 0, 0, tzinfo=_CST).timestamp()
    with pytest.raises(ExternalRequestBlocked) as err_exp:
        store.admit_and_spend(fp, "buyer-a", "trade", now=expired_day)
    assert err_exp.value.code == "ONEBOUND_KEY_EXPIRED"


def test_concurrent_collect_admission_never_overspends(tmp_path):
    store = OneBoundQuotaStore(tmp_path / "onebound-quota.sqlite3")
    fp = quota_service.key_fingerprint("vendor-key")
    now = datetime(2026, 11, 22, 12, 0, 0, tzinfo=_CST).timestamp()

    def attempt(_):
        try:
            store.admit_and_spend(fp, "buyer-c", "collect", initial_grant=5, now=now)
            return True
        except ExternalRequestBlocked as exc:
            assert exc.code == "ONEBOUND_QUOTA_EXHAUSTED"
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, range(20)))
    assert results.count(True) == 5
    assert results.count(False) == 15
    assert store.read_buyer_summary(fp, "buyer-c")["balance"] == 0


# -- 4. 完整传输链路：4013 / 4016 / 4007 / 4008 / 4006 / 4009 + 重启持久拦截 --


def test_onebound_4013_and_4016_persist_across_restart_and_keep_write_rejection_semantics(monkeypatch, tmp_path):
    ctx = get_context()
    configure(ctx.config)
    now_ref = [datetime(2026, 11, 22, 22, 0, 0, tzinfo=_CST).timestamp()]
    sent_urls: list[str] = []
    next_payload = [{}]

    def opener(*_args):
        def fake_open(req, timeout=20):
            sent_urls.append(req.full_url)
            return _json_response(next_payload[0])
        return SimpleNamespace(open=fake_open)

    monkeypatch.setattr(urllib.request, "build_opener", opener)

    # 1) 正常响应附带 api_info 写入快照。
    next_payload[0] = {
        "error_code": "0000",
        "api_info": "today:12 max:5000 all[99=12+87];expires:2030-02-19",
        "response": {"result": {"success": True, "result": {"offerId": 123, "subject": "测品"}}},
    }
    provider = alibaba_provider({**DIRECTORY_CONFIG, "initial_grant": 200})
    assert provider.get_product("123", "https://detail.1688.com/offer/123.html")["product"]["api_offer_id"] == "123"
    fp = quota_service.key_fingerprint(DIRECTORY_CONFIG["api_key"])
    key_status = ctx.onebound_quota.read_key_status(fp, configured=True)
    assert key_status["today_snapshot"] == 12 and key_status["daily_max"] == 5000
    assert key_status["expires"] == "2030-02-19"

    # 2) 4013 每日上限：首次下单收到 4013 仍转化为 AlibabaApiRejected（可释放占位），后续请求不再外发，且重启后仍拦截。
    next_payload[0] = {"error_code": "4013", "error": "daily limit reached"}
    with pytest.raises(AlibabaApiRejected, match="4013"):
        provider.create_order({"fullName": "收货人"}, [{"offer_id": "123", "spec_id": "s1", "quantity": 1}],
                              flow="general", channel="shegou", external_id="order-4013")
    count_after_4013 = len(sent_urls)
    # 同一进程内后续请求被本地拦截。
    with pytest.raises(ExternalRequestBlocked):
        provider.get_product("124", "https://detail.1688.com/offer/124.html")
    assert len(sent_urls) == count_after_4013

    # 模拟重启：换全新的纯内存 limiter 和 ExternalRequestManager，但共享同一个 onebound-quota.sqlite3。
    fresh_manager = ExternalRequestManager(
        ExternalRequestStore(tmp_path / "fresh-audit.sqlite3"),
        limiter=ExternalRequestLimiter(clock=lambda: now_ref[0]),
    )
    monkeypatch.setattr(ctx, "_external_requests", fresh_manager)
    with pytest.raises(ExternalRequestBlocked) as err_restart:
        provider.get_product("125", "https://detail.1688.com/offer/125.html")
    assert err_restart.value.code == "ONEBOUND_DAILY_RATE_LIMITED"
    assert len(sent_urls) == count_after_4013

    # 管理员恢复后清除冷却，继续测 4016 永久停用。
    assert AlibabaAuthorizationService(ctx.config).restore_onebound_key(
        ctx.onebound_quota, clear_auth_block=ctx.external_requests.clear_auth_block
    )

    # 3) 4016 余额不足：首次转 AlibabaApiRejected，持久停用且跨重启拦截，管理员恢复后立刻放行。
    next_payload[0] = {"error_code": "4016", "error": "insufficient balance"}
    with pytest.raises(AlibabaApiRejected, match="4016"):
        provider.get_order("9001")
    count_after_4016 = len(sent_urls)

    # 模拟重启。
    fresh_manager_2 = ExternalRequestManager(
        ExternalRequestStore(tmp_path / "fresh-audit-2.sqlite3"),
        limiter=ExternalRequestLimiter(),
    )
    monkeypatch.setattr(ctx, "_external_requests", fresh_manager_2)
    with pytest.raises(ExternalRequestBlocked) as err_disabled:
        provider.get_order("9002")
    assert err_disabled.value.code == "ONEBOUND_ACCOUNT_DISABLED"
    assert len(sent_urls) == count_after_4016

    # 管理员点恢复：不发任何探测请求，直接清除持久停用和内存熔断。
    payload, status_code = alibaba_authorization_facade.restore_onebound_key_payload()
    assert status_code == 200 and payload["restored"] is True
    assert payload["quota"]["key"]["disabled"] is False
    assert len(sent_urls) == count_after_4016

    next_payload[0] = {"error_code": "0000", "response": {"baseInfo": {"idOfStr": "9003", "status": "success"}}}
    assert provider.get_order("9003").status["order_number"] == "9003"
    assert len(sent_urls) == count_after_4016 + 1


def test_onebound_4006_isolates_interface_and_4009_stays_outcome_unknown(monkeypatch):
    ctx = get_context()
    configure(ctx.config)
    replies = []

    def opener(*_args):
        def fake_open(req, timeout=20):
            return _json_response(replies.pop(0))
        return SimpleNamespace(open=fake_open)

    monkeypatch.setattr(urllib.request, "build_opener", opener)
    provider = alibaba_provider({**DIRECTORY_CONFIG, "initial_grant": 200})

    # 4006 只隔离单个接口，不影响其他接口。
    replies.append({"error_code": "4006", "error": "api disabled"})
    with pytest.raises(AlibabaApiRejected, match="4006"):
        provider.get_product("123", "https://detail.1688.com/offer/123.html")
    replies.append({"error_code": "0000", "response": {"baseInfo": {"idOfStr": "888", "status": "success"}}})
    assert provider.get_order("888").status["order_number"] == "888"

    # 4009 维护中：写入不转成 AlibabaApiRejected（保留结果未知语义）。
    replies.append({"error_code": "4009", "error": "maintenance"})
    with pytest.raises(AlibabaApiError) as err_4009:
        provider.create_order({"fullName": "收货人"}, [{"offer_id": "123", "spec_id": "s1", "quantity": 1}],
                              flow="general", channel="shegou", external_id="order-4009")
    assert not isinstance(err_4009.value, AlibabaApiRejected)
    assert "4009" in str(err_4009.value)


# -- 5. GMV 入账（自采购 pay & reconcile）--------------------------------------


def test_credit_paid_order_formula_and_idempotency(tmp_path):
    store = OneBoundQuotaStore(tmp_path / "onebound-quota.sqlite3")
    onebound_cfg = {**DIRECTORY_CONFIG, "initial_grant": 200}
    official_cfg = {"application_type": "alibaba", "app_key": "123", "member_id": "buyer-a"}

    # 15.80 元 -> floor(15.80) * 2 = 30 次；同一订单号不重复入账。
    assert quota_service.credit_paid_order(store, onebound_cfg, "10001", 1580) == 30
    assert quota_service.credit_paid_order(store, onebound_cfg, "10001", Decimal("1580.00")) == 0
    # 不足 1 元不入账。
    assert quota_service.credit_paid_order(store, onebound_cfg, "10002", 99) == 0
    # 非万邦方案不入账。
    assert quota_service.credit_paid_order(store, official_cfg, "10003", 50000) == 0

    fp = quota_service.key_fingerprint(DIRECTORY_CONFIG["api_key"])
    summary = store.read_buyer_summary(fp, "buyer-a")
    assert summary["granted"] == 200 and summary["earned"] == 30 and summary["balance"] == 230


def test_self_purchase_pay_and_reconcile_credit_onebound_quota_once(setup_purchase, tmp_path):
    from tests.test_alibaba_purchase_payment import agreement
    from tests.test_alibaba_self_purchase import NUMBER, preview
    from erp_web.services.alibaba_api_client import PAY, PAY_STATUS

    service, client, config, _ = setup_purchase
    quota_store = OneBoundQuotaStore(tmp_path / "quota.sqlite3")
    config.clear()
    config.update({**DIRECTORY_CONFIG, "account_scope": "onebound-scope", "initial_grant": 200})
    service.quota_credit = lambda cfg, num, fen: quota_service.credit_paid_order(quota_store, cfg, num, fen)
    client.order["result"]["baseInfo"]["totalAmount"] = "15.80"
    client.order["result"]["productItems"][0]["quantity"] = 1
    original_call = client.call

    def call(name, values):
        if name == PAY_STATUS:
            return agreement(channel="shegou")
        if name == PAY:
            return {"success": True, "result": {"paySuccess": True, "payChannel": "SHEGOU"}}
        return original_call(name, values)

    client.call = call

    rec = service.create({"preview_id": preview(service)["id"], "pay_channel": "shegou"})["record"]
    # 下单未付款前不入账。
    fp = quota_service.key_fingerprint(config["api_key"])
    assert quota_store.read_buyer_summary(fp, "buyer-a")["earned"] == 0

    # 免密支付成功 -> 入账 floor(15.80) * 2 = 30 次。
    paid = service.pay({"preview_id": rec["id"], "confirmed_total_fen": 1580})["record"]
    assert paid["payment"]["state"] == "succeeded"
    assert quota_store.read_buyer_summary(fp, "buyer-a")["earned"] == 30

    # 后续 reconcile 看到已付款状态（waitsellersend），同一订单号不重复入账。
    client.order["result"]["baseInfo"]["status"] = "waitsellersend"
    service.reconcile({"preview_id": rec["id"]})
    assert quota_store.read_buyer_summary(fp, "buyer-a")["earned"] == 30

    # 另一笔订单走收银台手动付款后点 reconcile -> 直接入账 floor(50.90) * 2 = 100 次。
    second_number = "900000002"
    client.creation = {"success": True, "result": {"orderId": second_number, "totalSuccessAmount": 5090, "postFee": 0}}
    client.order["result"]["baseInfo"]["idOfStr"] = second_number
    client.order["result"]["baseInfo"]["totalAmount"] = "50.90"
    client.order["result"]["baseInfo"]["status"] = "waitbuyerpay"
    rec2 = service.create({"preview_id": preview(service)["id"], "pay_channel": "shegou"})["record"]
    assert quota_store.read_buyer_summary(fp, "buyer-a")["earned"] == 30
    client.order["result"]["baseInfo"]["status"] = "waitsellersend"
    service.reconcile({"preview_id": rec2["id"]})
    assert quota_store.read_buyer_summary(fp, "buyer-a")["earned"] == 30 + 100


# -- 6. shop_bind_list 10 分钟节流 ---------------------------------------------


def test_shop_bind_list_throttle_limits_buyer_sync_within_ten_minutes(tmp_path):
    store = OneBoundQuotaStore(tmp_path / "quota.sqlite3")
    assert quota_service.check_shop_bind_throttle(store, "vendor-key", "buyer-t", now=1000.0) is True
    assert quota_service.check_shop_bind_throttle(store, "vendor-key", "buyer-t", now=1300.0) is False
    # 其他买家不受影响。
    assert quota_service.check_shop_bind_throttle(store, "vendor-key", "buyer-other", now=1300.0) is True
    # 超过 10 分钟后恢复放行。
    assert quota_service.check_shop_bind_throttle(store, "vendor-key", "buyer-t", now=1601.0) is True


# -- 7. BusinessQuery 权限隔离与多用户共用同一 member_id 余额 ------------------


def test_onebound_quota_business_query_and_shared_buyer_ledger(accounts_http):
    origin, runtime, login = accounts_http
    configure(runtime.root.config)
    user_a = register(origin, "quota-a@example.com")
    user_b = register(origin, "quota-b@example.com")
    admin_client = login()
    client_a = login(user_a["email"])
    client_b = login(user_b["email"])

    app = runtime.root.config.alibaba_application("onebound")
    app_fp = application_fingerprint(app)
    ctx_a = runtime.context_for(runtime.store.by_email(user_a["email"]))
    ctx_b = runtime.context_for(runtime.store.by_email(user_b["email"]))
    # user_a 与 user_b 授权同一个 1688 买家 shared-buyer，管理员授权 admin-buyer。
    for ctx, mid, name in (
        (ctx_a, "shared-buyer", "共用买家"),
        (ctx_b, "shared-buyer", "共用买家"),
        (runtime.root, "admin-buyer", "管理员买家"),
    ):
        ctx.config.update_alibaba_credentials(
            {"authorization": {
                "access_token": f"tok-{mid}", "credential_type": "access_token",
                "member_id": mid, "account_name": name, "expires_at": 4_000_000_000,
                "account_scope": f"scope-{mid}", "application_fingerprint": app_fp,
            }},
            provider="onebound",
        )

    fp = quota_service.key_fingerprint(app["api_key"])
    runtime.root.onebound_quota.admit_and_spend(fp, "shared-buyer", "collect", initial_grant=200)
    runtime.root.onebound_quota.admit_and_spend(fp, "admin-buyer", "trade", initial_grant=200)
    runtime.root.onebound_quota.observe_snapshot(fp, today=5, daily_max=1000, expires="2030-01-01")

    # 两个授权同一买家的普通用户看到同一份余额（199），且看不到 key 状态或管理员买家。
    for client in (client_a, client_b):
        res = client.get(origin + "/api/business-queries/alibaba.onebound_quota")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["is_admin"] is False
        assert data["active_provider"] == "onebound"
        assert data["key"] is None
        assert data["buyer"]["member_id"] == "shared-buyer"
        assert data["buyer"]["balance"] == 199
        assert [b["member_id"] for b in data["buyers"]] == ["shared-buyer"]
        assert all(e["member_id"] == "shared-buyer" for e in data["entries"])
        assert "vendor-key" not in res.text and "vendor-secret" not in res.text

    # 管理员看到全部买家和 Key 状态，并且 AI 可通过 BusinessDataCatalog 分页读 entries。
    admin_res = admin_client.get(origin + "/api/business-queries/alibaba.onebound_quota")
    assert admin_res.status_code == 200
    admin_data = admin_res.json()
    assert admin_data["is_admin"] is True
    assert admin_data["key"]["daily_max"] == 1000 and admin_data["key"]["reserve"] == 50
    assert {b["member_id"] for b in admin_data["buyers"]} == {"admin-buyer", "shared-buyer"}

    with bind_context(runtime.root):
        catalog = build_business_data_catalog(runtime.root)
        scope = BusinessDataScope(lambda: catalog)
        exec_ctx = AiExecutionContext.create(timeout_seconds=30, budget_profile="test", permissions={"business.read", "order.read"})
        summary = business_data_read(DataReadRequest(resource="alibaba.onebound_quota"), scope, exec_ctx)
        assert summary.data["buyer"]["member_id"] == "admin-buyer"
        assert summary.data["key"]["daily_max"] == 1000
        entries_page = business_data_read(DataReadRequest(resource="alibaba.onebound_quota", path="entries", limit=2), scope, exec_ctx)
        assert len(entries_page.data) == 2 and entries_page.next_offset == 2

    # 普通用户调用管理员恢复 Key 接口返回 403。
    assert client_a.post(origin + "/api/alibaba/onebound/key/restore", json={}).status_code == 403
    assert admin_client.post(origin + "/api/alibaba/onebound/key/restore", json={}).status_code == 200
