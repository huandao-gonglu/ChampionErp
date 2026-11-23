"""统一外部请求管理的离线验收：纯内存限流、结构化分类、授权联动与 v3 审计迁移。"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from email.message import Message
import io
import json
import sqlite3
from threading import Barrier, Event, Lock
import time
import urllib.error
import urllib.request

import pytest

from erp_web.context import get_context
from erp_web.schemas.external_requests import (
    ExternalRequestBlocked,
    ExternalRequestNotSent,
    ExternalRequestOutcomeUnknown,
    RequestContext,
    RequestFailure,
)
from erp_web.services.external_request_context import credential_fingerprint, request_operation
from erp_web.services.external_request_limiter import ExternalRequestLimiter
from erp_web.services.external_request_manager import ExternalRequestManager
from erp_web.stores.external_request_store import ExternalRequestStore


class Response(io.BytesIO):
    def __init__(self, body=b"{}", status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value


def context(**kwargs):
    return RequestContext(**{
        "platform": "ozon",
        "account_id": "shop-1",
        "interface": "/tree",
        "source": "category",
        "credential_id": "key-version-1",
        "semantics": "read",
        "timeout": 2,
        **kwargs,
    })


def request(manager, ctx, transport):
    return manager.open(
        urllib.request.Request("https://api-seller.ozon.ru/tree"),
        request_context_value=ctx,
        timeout=ctx.timeout,
        transport=transport,
    )


def rejected(status, body, headers=None):
    def send(*args, **kwargs):
        raise urllib.error.HTTPError("https://test.invalid", status, "rejected", headers or {}, io.BytesIO(body))
    return send


def test_fixed_gateway_can_disable_redirects_at_managed_boundary():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/must-not-follow")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            get_context().external_requests.open(
                f"http://127.0.0.1:{server.server_port}/gateway",
                timeout=2,
                request_context_value=context(),
                follow_redirects=False,
            )
        assert error.value.code == 302
        error.value.close()
        assert paths == ["/gateway"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_managed_public_document_redirect_keeps_region_cookie():
    from http.cookiejar import CookieJar
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    cookies = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            cookies.append(self.headers.get("Cookie", ""))
            if "region=ready" not in cookies[-1]:
                self.send_response(307)
                self.send_header("Set-Cookie", "region=ready; Path=/")
                self.send_header("Location", "/document")
            else:
                self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with get_context().external_requests.open(
            f"http://127.0.0.1:{server.server_port}/document",
            timeout=2,
            request_context_value=context(),
            cookie_jar=CookieJar(),
        ) as result:
            assert result.read() == b"{}"
        assert cookies == ["", "region=ready"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_ozon_default_concurrency_cap_and_ratelimit_remaining_pause():
    manager = ExternalRequestManager(
        ExternalRequestStore(get_context().paths.data_dir / "ozon-concurrency.sqlite3"),
        limiter=ExternalRequestLimiter(jitter=lambda: 0.0),
    )
    active = 0
    peak = 0
    lock = Lock()

    def slow(*args, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return Response()

    with ThreadPoolExecutor(6) as pool:
        futures = [pool.submit(request, manager, context(interface=f"/p/{i}"), slow) for i in range(6)]
        for f in futures:
            f.result().close()
    assert peak <= 3

    # Ratelimit-Remaining: 0 只暂停进程内存令牌桶，不写持久阻断，等待后自动放行。
    start = time.time()
    request(
        manager,
        context(interface="/p/rem"),
        lambda *a, **k: Response(headers={"Ratelimit-Remaining": "0", "Ratelimit-Reset": "0.12"}),
    ).close()
    request(manager, context(interface="/p/next"), lambda *a, **k: Response()).close()
    assert time.time() - start >= 0.08


def test_429_backoff_is_memory_auto_recovering_and_ignores_inflight_burst():
    now = [1000.0]
    limiter = ExternalRequestLimiter(clock=lambda: now[0], monotonic=lambda: now[0], jitter=lambda: 0.0)
    store = ExternalRequestStore(get_context().paths.data_dir / "backoff.sqlite3")
    manager = ExternalRequestManager(store, limiter=limiter)

    # 模拟 3 个同时在途请求都收到 Ozon 429 code 8（无 Retry-After）：只算 1 级（2s），不得连跳升级。
    ctx1 = context(interface="/v3/product/list")
    lease1 = limiter.acquire("r1", ctx1, now[0] + 10)
    lease2 = limiter.acquire("r2", ctx1, now[0] + 10)
    lease3 = limiter.acquire("r3", ctx1, now[0] + 10)
    f1 = limiter.record_failure(ctx1, "r1", RequestFailure("OZON_RATE_LIMITED", "限流", "account", status=429), sent_at=lease1.sent_at)
    f2 = limiter.record_failure(ctx1, "r2", RequestFailure("OZON_RATE_LIMITED", "限流", "account", status=429), sent_at=lease2.sent_at)
    f3 = limiter.record_failure(ctx1, "r3", RequestFailure("OZON_RATE_LIMITED", "限流", "account", status=429), sent_at=lease3.sent_at)
    limiter.release(lease1)
    limiter.release(lease2)
    limiter.release(lease3)
    assert f1.resume_at == pytest.approx(1002.0)
    assert f2.resume_at == pytest.approx(1002.0)
    assert f3.resume_at == pytest.approx(1002.0)

    # 冷却未到期且超出 deadline 时本地拒绝。
    with pytest.raises(ExternalRequestBlocked) as err:
        limiter.acquire("r4", ctx1, now[0] + 1.0)
    assert err.value.code == "OZON_RATE_LIMITED"

    # 冷却到期后允许单个写请求探测并再次失败 -> 升级到第 2 级（5s）。
    now[0] = 1002.1
    write_ctx = context(interface="/v3/product/import", semantics="write")
    probe = limiter.acquire("r5", write_ctx, now[0] + 10)
    f5 = limiter.record_failure(write_ctx, "r5", RequestFailure("OZON_RATE_LIMITED", "限流", "account", status=429), sent_at=probe.sent_at)
    limiter.release(probe)
    assert f5.resume_at == pytest.approx(1007.1)

    # 第二级到期后成功 -> 恢复正常。
    now[0] = 1007.2
    probe2 = limiter.acquire("r6", write_ctx, now[0] + 10)
    limiter.record_success(write_ctx, "r6")
    limiter.release(probe2)
    assert manager.matching_status([ctx1]) == []


def test_ozon_429_propagates_computed_resume_at_through_config_http(monkeypatch):
    from erp_web.marketplaces import config_http
    from erp_web.schemas.platform_errors import PublishAdapterError

    manager = get_context().external_requests
    monkeypatch.setattr(
        config_http,
        "managed_urlopen",
        lambda req, **kw: manager.open(
            req,
            transport=rejected(429, b'{"code":8,"message":"rate limit"}'),
            **kw,
        ),
    )
    with pytest.raises(PublishAdapterError) as exc:
        config_http.request_ozon_json("POST", "https://api-seller.ozon.ru/v3/product/list", "1001", "secret", {})
    assert exc.value.code == "OZON_RATE_LIMITED"
    assert exc.value.details["resume_at"] is not None
    assert exc.value.details["resume_at"] > time.time()


def test_ozon_403_escalation_and_auth_settings_recovery():
    app = get_context()
    app.config.save_store_config({
        "ozon": {"client_id": "shop-ozon", "api_key": "key-v1", "auth_status": "ok"},
    })
    manager = app.external_requests
    cred1 = credential_fingerprint("key-v1")

    # 1. 单个从未成功过的接口 403 code 7：仅暂停该接口，不影响其他接口，也不锁整店。
    with pytest.raises(urllib.error.HTTPError):
        request(manager, context(account_id="shop-ozon", credential_id=cred1, interface="/v3/posting/fbo/list"),
                rejected(403, b'{"code":7,"message":"PermissionDenied"}'))
    request(manager, context(account_id="shop-ozon", credential_id=cred1, interface="/v3/product/list"),
            lambda *a, **k: Response()).close()
    assert app.config.load_store_config()["ozon"]["auth_status"] == "ok"

    # 2. 同一凭据此前成功过的只读接口突然返回 403 code 7 -> 升级为整个账号停用并持久化到店铺授权状态！
    with pytest.raises(urllib.error.HTTPError):
        request(manager, context(account_id="shop-ozon", credential_id=cred1, interface="/v3/product/list"),
                rejected(403, b'{"code":7,"message":"\xd0\x94\xd0\xbe\xd1\x81\xd1\x82\xd1\x83\xd0\xbf \xd0\xb7\xd0\xb0\xd0\xbf\xd1\x80\xd0\xb5\xd1\x89\xd0\xb5\xd0\xbd"}'))
    assert app.config.load_store_config()["ozon"]["auth_status"] == "failed"
    assert app.config.load_store_config()["ozon"]["auth_error_code"] == "OZON_ACCOUNT_DISABLED"

    # 其他模块立即被阻止。
    with pytest.raises(ExternalRequestBlocked):
        request(manager, context(account_id="shop-ozon", credential_id=cred1, interface="/v5/product/info/prices"),
                lambda *a, **k: pytest.fail("账号已停用不得继续外发"))

    # 重启（新建管理器）后仍根据持久化 auth_status 保持阻止。
    app._external_requests = None
    restarted = app.external_requests
    with pytest.raises(ExternalRequestBlocked):
        request(restarted, context(account_id="shop-ozon", credential_id=cred1, interface="/v4/posting/fbs/list"),
                lambda *a, **k: pytest.fail("重启后仍应阻止已停用账号"))

    # 授权测试（bypass_auth_block=True）可穿透验证，成功后清除熔断。
    with request_operation("auth_test", bypass_auth_block=True):
        request(restarted, context(account_id="shop-ozon", credential_id=cred1, interface="/v1/seller/info", bypass_auth_block=True),
                lambda *a, **k: Response()).close()
    app.config.update_store_config_fields("ozon", {"auth_status": "ok", "auth_error_code": "", "auth_error_message": ""})
    restarted.clear_auth_block("ozon", "shop-ozon")
    request(restarted, context(account_id="shop-ozon", credential_id=cred1, interface="/v4/posting/fbs/list"),
            lambda *a, **k: Response()).close()


def test_ozon_403_three_distinct_interfaces_escalates_and_new_key_does_not_inherit_old_success():
    manager = get_context().external_requests
    cred_old = credential_fingerprint("old-admin-key")
    cred_new = credential_fingerprint("new-limited-key")

    # 旧 Key 在 /v3/product/list 成功过，换成新 Key 调同一接口报 403 不得把旧 Key 成功当成新 Key 突遭封禁。
    request(manager, context(account_id="shop-2", credential_id=cred_old, interface="/v3/product/list"),
            lambda *a, **k: Response()).close()
    with pytest.raises(urllib.error.HTTPError):
        request(manager, context(account_id="shop-2", credential_id=cred_new, interface="/v3/product/list"),
                rejected(403, b'{"code":7}'))
    # 未升级为整号停用，仍可调用第 2 个接口。
    with pytest.raises(urllib.error.HTTPError):
        request(manager, context(account_id="shop-2", credential_id=cred_new, interface="/v4/posting/fbs/list"),
                rejected(403, b'{"code":7}'))
    # 10 分钟内无任何成功且第 3 个不同接口也 403 -> 升级为整号停用。
    with pytest.raises(urllib.error.HTTPError):
        request(manager, context(account_id="shop-2", credential_id=cred_new, interface="/v5/product/info/prices"),
                rejected(403, b'{"code":7}'))
    with pytest.raises(ExternalRequestBlocked) as err:
        request(manager, context(account_id="shop-2", credential_id=cred_new, interface="/v1/description-category/tree"),
                lambda *a, **k: pytest.fail("3 个不同接口 403 后整号应停用"))
    assert err.value.code == "OZON_ACCOUNT_DISABLED"


def test_v2_external_requests_db_migrates_losslessly_to_v3_and_carries_account_disable():
    app = get_context()
    app.config.save_store_config({"ozon": {"client_id": "legacy-shop", "api_key": "k", "auth_status": "ok"}})
    db_path = app.paths.data_dir / "external-requests.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    app._external_requests = None
    if db_path.exists():
        db_path.unlink()
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE external_attempts (
                id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, parent_id TEXT NOT NULL,
                attempt INTEGER NOT NULL, platform TEXT NOT NULL, account_id TEXT NOT NULL,
                credential_id TEXT NOT NULL, interface TEXT NOT NULL, source TEXT NOT NULL,
                trigger TEXT NOT NULL, quota_key TEXT NOT NULL, method TEXT NOT NULL, semantics TEXT NOT NULL,
                created REAL NOT NULL, released REAL, completed REAL, sent INTEGER NOT NULL DEFAULT 0,
                decision TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}', lease_until REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE external_blocks (
                platform TEXT NOT NULL, account_id TEXT NOT NULL, scope TEXT NOT NULL, scope_key TEXT NOT NULL,
                failure TEXT NOT NULL, created REAL NOT NULL, blocked_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(platform, account_id, scope, scope_key)
            );
            CREATE TABLE external_recoveries (id TEXT PRIMARY KEY, platform TEXT, account_id TEXT, scope TEXT, scope_key TEXT, created REAL, reason TEXT, kind TEXT NOT NULL DEFAULT 'manual');
            CREATE TABLE external_policies (platform TEXT NOT NULL, interface TEXT NOT NULL, concurrency INTEGER, requests_per_minute INTEGER, consecutive_failure_limit INTEGER, PRIMARY KEY(platform, interface));
            PRAGMA user_version=2;
        """)
        conn.execute(
            "INSERT INTO external_attempts VALUES('att-1','op-1','',1,'ozon','legacy-shop','c1','/tree','s','manual','b','POST','read',?,?,?,1,'allowed','{\"outcome\":\"success\"}',0)",
            (time.time(), time.time(), time.time()),
        )
        conn.execute(
            "INSERT INTO external_blocks VALUES('ozon','legacy-shop','account','',?, ?, 9)",
            (json.dumps({"code": "OZON_ACCOUNT_DISABLED", "message": "旧版封禁记录", "scope": "account", "resume_at": None}), time.time()),
        )
        conn.commit()

    manager = app.external_requests
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"external_attempts"}
    assert manager.store.query()["stats"]["attempts"] == 1
    assert app.config.load_store_config()["ozon"]["auth_status"] == "failed"
    assert app.config.load_store_config()["ozon"]["auth_error_code"] == "OZON_ACCOUNT_DISABLED"
    with pytest.raises(ExternalRequestBlocked):
        request(manager, context(account_id="legacy-shop"), lambda *a, **k: pytest.fail("迁移后的封禁账号不得放行"))


def test_ai_operation_request_budget_stops_runaway_loop():
    manager = get_context().external_requests
    calls = []
    with request_operation("ai_tool:demo", operation_id="ai-run-1", trigger="agent_tool", max_operation_requests=3):
        for i in range(3):
            ctx = context(operation_id="ai-run-1", interface=f"/ep/{i}", max_operation_requests=3)
            request(manager, ctx, lambda *a, **k: (calls.append(1), Response())[1]).close()
        with pytest.raises(ExternalRequestBlocked) as err:
            request(manager, context(operation_id="ai-run-1", interface="/ep/overflow", max_operation_requests=3),
                    lambda *a, **k: pytest.fail("超预算不得外发"))
    assert err.value.code == "AI_EXTERNAL_REQUEST_BUDGET"
    assert len(calls) == 3


def test_waiting_obeys_retry_after_and_can_be_cancelled():
    manager = get_context().external_requests
    with pytest.raises(urllib.error.HTTPError):
        request(manager, context(), rejected(429, b"{}", {"Retry-After": "0.15"}))
    start = time.time()
    request(manager, context(), lambda *a, **k: Response()).close()
    assert time.time() - start >= 0.10
    with pytest.raises(urllib.error.HTTPError):
        request(manager, context(), rejected(429, b"{}", {"Retry-After": "1"}))
    cancel = Event()
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(request, manager, context(cancel=cancel), lambda *a, **k: pytest.fail("取消后不得外发"))
        cancel.set()
        with pytest.raises(ExternalRequestBlocked, match="取消"):
            future.result()
    last = manager.store.query(limit=1)["items"][0]
    assert last["decision"] == "cancelled" and last["sent"] == 0


def test_http_200_business_rejection_is_audited_and_secret_free():
    manager = get_context().external_requests
    secret = "secret-api-key-MUST-NOT-APPEAR"
    payload = json.dumps({"status": "ERROR", "errors": [{"code": "FORBIDDEN", "message": secret}]}).encode()
    with pytest.raises(ExternalRequestBlocked):
        request(manager, context(), lambda *a, **k: Response(payload))
    audit = manager.store.query()
    assert secret not in json.dumps(audit)
    assert audit["items"][0]["result"]["http_status"] == 200
    assert audit["items"][0]["result"]["outcome"] == "failed"
    assert audit["items"][0]["result"]["platform_error_codes"] == ["FORBIDDEN"]


def test_retry_has_one_shared_budget_and_separate_audit_rows():
    manager = get_context().external_requests
    calls = []

    def send(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return rejected(503, b"{}")()
        return Response()

    request(manager, context(max_attempts=2, retry_delay=0.01), send).close()
    rows = manager.store.query()["items"]
    assert len(calls) == 2 and {r["attempt"] for r in rows} == {1, 2}
    assert len({r["operation_id"] for r in rows}) == 1
    assert rows[0]["parent_id"] == rows[1]["id"]


def test_write_timeout_is_unknown_and_never_replayed_across_restart():
    db_path = get_context().paths.data_dir / "unknown-write.sqlite3"
    manager = ExternalRequestManager(ExternalRequestStore(db_path))
    calls = []

    def send(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("敏感平台错误原文")

    ctx = context(semantics="write", max_attempts=3, fingerprint="fp-write-1")
    with pytest.raises(ExternalRequestOutcomeUnknown) as error:
        request(manager, ctx, send)
    rows = manager.store.query()["items"]
    assert len(calls) == 1
    assert rows[0]["result"]["outcome"] == "outcome_unknown"
    assert "敏感平台" not in json.dumps(rows)
    assert error.value.details["outcome_unknown"] is True

    # 进程重启后（新实例）同操作同指纹写入仍被阻止重放。
    restarted = ExternalRequestManager(ExternalRequestStore(db_path))
    with pytest.raises(ExternalRequestBlocked) as blocked:
        request(restarted, ctx, send)
    assert blocked.value.details["outcome_unknown"] is True
    assert blocked.value.details["definitively_rejected"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("max_attempts", [1, 3])
def test_read_network_retry_requires_explicit_budget(max_attempts):
    import http.client

    manager = get_context().external_requests
    calls = []

    def send(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise http.client.RemoteDisconnected("敏感网络错误")
        return Response()

    ctx = context(max_attempts=max_attempts, retry_delay=0.01)
    if max_attempts == 1:
        with pytest.raises(http.client.RemoteDisconnected):
            request(manager, ctx, send)
    else:
        request(manager, ctx, send).close()
    rows = manager.store.query()["items"]
    assert len(calls) == (1 if max_attempts == 1 else 2)
    assert len({row["operation_id"] for row in rows}) == 1
    assert "敏感网络错误" not in json.dumps(rows)
    assert {row["attempt"] for row in rows} == set(range(1, len(calls) + 1))


@pytest.mark.parametrize("semantics", ["read", "write"])
def test_transport_rejection_before_http_is_not_an_unknown_write(semantics):
    manager = get_context().external_requests
    ctx = context(semantics=semantics, max_attempts=3, fingerprint="not-sent-fp")
    calls = []

    def reject(*args, **kwargs):
        calls.append(1)
        raise ExternalRequestNotSent(RequestFailure("IMAGE_DNS_NONPUBLIC", "域名解析到非公开地址，已阻止发送"))

    with pytest.raises(ExternalRequestNotSent) as error:
        request(manager, ctx, reject)
    assert calls == [1]
    assert error.value.details["definitively_rejected"] is True
    assert not error.value.details.get("outcome_unknown")
    audit = manager.store.query()
    assert audit["stats"]["network_attempts"] == 0
    assert audit["stats"]["local_rejections"] == 1
    row = audit["items"][0]
    assert row["sent"] == 0 and row["released"] is None
    assert row["result"]["outcome"] == "not_sent"
    request(manager, ctx, lambda *args, **kwargs: Response()).close()


@pytest.mark.parametrize("status", [401, 403])
def test_anonymous_image_auth_failure_does_not_disable_future_checks(status):
    manager = get_context().external_requests
    ctx = context(platform="image_hosting:public", account_id="images-main", credential_id="public", max_attempts=3)
    with pytest.raises(urllib.error.HTTPError) as error:
        request(manager, ctx, rejected(status, b"{}"))
    assert error.value.code == status
    # 相同地址和账号的下一次显式检查可发送，不要求刷新不存在的匿名凭据。
    request(manager, replace(ctx, operation_id="after-public-access-change"), lambda *a, **kw: Response()).close()
    assert manager.store.query()["stats"]["network_attempts"] == 2


def test_token_expiry_only_blocks_old_credential():
    manager = get_context().external_requests
    with pytest.raises(urllib.error.HTTPError):
        request(manager, context(), rejected(401, b"{}"))
    request(manager, context(credential_id="refreshed-token"), lambda *a, **k: Response()).close()
    with pytest.raises(ExternalRequestBlocked):
        request(manager, context(), lambda *a, **k: Response())


def test_repeated_bad_parameters_and_transient_failure_streak_stop_locally():
    manager = get_context().external_requests
    ctx = context(fingerprint="same-parameters")
    with pytest.raises(urllib.error.HTTPError):
        request(manager, ctx, rejected(400, b"{}"))
    with pytest.raises(ExternalRequestBlocked):
        request(manager, ctx, lambda *a, **k: pytest.fail("相同任务的错误参数不能再次发送"))
    request(manager, replace(ctx, fingerprint="corrected-parameters"), lambda *a, **k: Response()).close()

    manager.limiter.set_limit("ozon", consecutive_failure_limit=2)
    for _ in range(2):
        with pytest.raises(urllib.error.HTTPError):
            request(manager, context(interface="/unstable"), rejected(503, b"{}"))
    with pytest.raises(ExternalRequestBlocked, match="连续失败"):
        request(manager, context(interface="/unstable"), lambda *a, **k: Response())


@pytest.mark.parametrize("status", [400, 200])
def test_yandex_forbidden_parameter_message_does_not_block_corrected_tariff_request(status):
    manager = get_context().external_requests
    ctx = context(platform="yandex", interface="/v2/tariffs/calculate", fingerprint="campaign-and-currency")
    body = json.dumps({"status": "ERROR", "errors": [{"code": "BAD_REQUEST", "message":
        'Forbidden to use "campaignId" parameter with "sellingProgram" or "currency".'}]}).encode()
    if status == 400:
        with pytest.raises(urllib.error.HTTPError) as error:
            request(manager, ctx, rejected(status, body))
        assert error.value.read() == body
        error.value.close()
    else:
        with request(manager, ctx, lambda *a, **k: Response(body)) as response:
            assert response.read() == body
    result = manager.store.query(platform="yandex")["items"][0]["result"]
    assert result["http_status"] == status
    assert result["platform_error_codes"] == ["BAD_REQUEST"]
    assert result["failure"]["code"] == "YANDEX_REQUEST_INVALID"
    assert result["failure"]["scope"] == "request"
    assert result["failure"]["status"] == status
    with pytest.raises(ExternalRequestBlocked):
        request(manager, ctx, lambda *a, **k: pytest.fail("相同错误参数不得再次发送"))
    request(manager, replace(ctx, fingerprint="campaign-only"), lambda *a, **k: Response()).close()
    assert manager.store.query(platform="yandex")["stats"]["network_attempts"] == 2


def test_s3_head_not_found_does_not_block_same_operation_post_upload_check():
    manager = get_context().external_requests
    ctx = context(platform="image_hosting:s3", account_id="hosting", interface="/resource/object", operation_id="pdf-upload", fingerprint="same-head")
    head = urllib.request.Request("https://s3.example.test/bucket/label.pdf", method="HEAD")
    with pytest.raises(urllib.error.HTTPError) as error:
        manager.open(head, request_context_value=ctx, transport=rejected(404, b""))
    assert error.value.code == 404
    with manager.open(head, request_context_value=ctx, transport=lambda *a, **kw: Response(b"", status=200)) as response:
        assert response.status == 200
    audit = manager.store.query(platform="image_hosting:s3")
    assert audit["stats"]["network_attempts"] == 2
    assert all(item["result"]["outcome"] == "success" for item in audit["items"])
    assert {item["result"]["http_status"] for item in audit["items"]} == {200, 404}


@pytest.mark.parametrize("platform,method,status", [("image_hosting:s3", "PUT", 404), ("image_hosting:s3", "GET", 404), ("image_hosting:public", "HEAD", 404), ("ozon", "HEAD", 404), ("image_hosting:s3", "HEAD", 403), ("image_hosting:s3", "HEAD", 500)])
def test_only_s3_head_404_is_an_expected_not_found_result(platform, method, status):
    from erp_web.services.platform_request_policy import classify_response
    assert classify_response(platform, status, {}, b"", method=method) is not None


def test_mercado_ordinary_getter_performs_no_identity_or_config_write(monkeypatch):
    from erp_web.runtime_units.store_credentials import get_mercadolibre_access_token
    from erp_web import marketplaces
    ctx = get_context()
    ctx.config.save_store_config({"mercadolibre": {"access_token": "valid", "user_id": "user-1"}})
    monkeypatch.setattr(marketplaces, "fetch_mercadolibre_user_profile", lambda *a: pytest.fail("普通读取不得查询身份"))
    monkeypatch.setattr(ctx.config, "update_store_config_fields", lambda *a: pytest.fail("普通读取不得回写配置"))
    assert [get_mercadolibre_access_token() for _ in range(5)] == ["valid"] * 5


def test_ozon_64_keywords_share_one_failure(monkeypatch):
    from erp_web.runtime_units import ozon_category_api as api
    from erp_web.runtime_units.category_providers import OzonCategoryProvider
    from erp_web.runtime_units.category_searchers import OzonCategorySearcher
    from erp_web.runtime_units.category_keyword_search import CategoryKeywordBatchSearch
    from erp_web.schemas.category import CategoryCandidateLedger
    from erp_web.schemas.ai_trace import AiExecutionContext
    from erp_web.schemas.platform_errors import PublishAdapterError
    monkeypatch.setattr(api, "_ozon_credentials", lambda: ("isolated-client", "key"))
    calls = []

    def send(*a, **k):
        calls.append(1)
        raise PublishAdapterError("OZON_AUTH_FAILED", "访问被拒绝", details={"http_status": 403})

    monkeypatch.setattr(api, "request_ozon_json", send)
    batch = CategoryKeywordBatchSearch(
        searcher=OzonCategorySearcher(OzonCategoryProvider(), "global"),
        ledger=CategoryCandidateLedger(),
        search_language="ru",
    )
    result = batch.execute({"keywords": [f"вентилятор {i}" for i in range(64)]}, AiExecutionContext.create(timeout_seconds=5, budget_profile="test"))
    assert len(calls) == 1 and len(result["errors"]) == 64
    assert all(not error["retryable"] for error in result["errors"])


def test_yandex_resource_limit_does_not_stop_other_endpoints():
    from erp_web.services.platform_request_policy import classify_response
    from email.utils import formatdate
    resume = formatdate(time.time() + 60, usegmt=True)
    limited = classify_response("yandex", 420, {"X-RateLimit-Resource-Until": resume}, b'{"message":"Hit rate limit for resource"}')
    assert limited.scope == "interface" and limited.resume_at > time.time()
    concurrent = classify_response("yandex", 420, {}, b'{"message":"Hit rate limit of 4 parallel requests for campaignId 123"}')
    assert concurrent.scope == "quota"
    manager = get_context().external_requests
    ctx = context(platform="yandex", quota_key="campaign:123")
    request(manager, ctx, lambda *a, **k: Response(headers={"X-RateLimit-Resource-Remaining": "0", "X-RateLimit-Resource-Until": resume})).close()
    with pytest.raises(ExternalRequestBlocked):
        request(manager, ctx, lambda *a, **k: pytest.fail("资源配额已用完"))
    request(manager, replace(ctx, interface="/orders"), lambda *a, **k: Response()).close()


def test_yandex_adapter_constructor_uses_saved_binding_without_probe(monkeypatch):
    from erp_web.runtime_units.online_yandex import YandexOnlineAdapter
    from erp_web.marketplaces import yandex_http
    monkeypatch.setattr(yandex_http, "fetch_yandex_campaign", lambda *a: pytest.fail("构造适配器不得查店铺归属"))
    config = {"yandex": {"api_token": "token", "business_id": "1", "campaign_id": "2"}}
    assert [YandexOnlineAdapter(config).account_id for _ in range(5)] == ["1:2"] * 5


def test_yandex_sync_stops_remaining_batches_after_access_rejection(monkeypatch):
    from types import SimpleNamespace
    from erp_web.runtime_units import online_yandex_read as read
    from erp_web.schemas.platform_errors import PublishAdapterError
    mappings = [{"offer": {"offerId": str(i)}} for i in range(201)]
    monkeypatch.setattr(read, "catalog_pages", lambda *a, **k: [mappings])
    monkeypatch.setattr(read, "catalog_listing", lambda row, **k: row)
    monkeypatch.setattr(read, "hidden_ids", lambda *a, **k: set())
    calls = []

    def send(*a, **k):
        calls.append(a[0])
        raise PublishAdapterError("YANDEX_AUTH_FAILED", "拒绝", details={"http_status": 403})

    adapter = SimpleNamespace(account_id="1:2", campaign="2", business="1", settings={}, mode="campaign_warehouses", request=send)
    with pytest.raises(PublishAdapterError):
        list(read.sync_yandex(adapter))
    assert 1 <= len(calls) <= 3


def test_mercado_confirmation_reads_selected_price_scope_only():
    from types import SimpleNamespace
    from erp_web.runtime_units.online_change_confirmation import mercado_change
    from erp_web.schemas.online_products import ChangeRequest, OnlineListing, PriceScope, MarketSnapshot
    listing = OnlineListing(
        id="listing", platform="mercadolibre", account_id="1", remote_id="CBT1", model="traditional_global_items",
        prices=[PriceScope(id="MLM1", label="墨西哥", amount="1", currency="MXN")],
        markets=[MarketSnapshot(id="MLM1", site_id="MLM", seller_id="2")],
    )
    paths = []

    def get(path):
        paths.append(path)
        return {"id": "MLM1", "seller_id": "2", "site_id": "MLM", "cbt_item_id": "CBT1", "price": 5, "currency_id": "MXN"}

    change = ChangeRequest(listing_id="listing", version="version", operation="price", scope_id="MLM1", changes={"amount": "5", "currency": "MXN"}, idempotency_key="offline-confirmation")
    result = mercado_change(SimpleNamespace(account_id="1", get=get), listing, change)
    assert result.prices[0].amount == "5" and len(paths) == 1
    assert paths[0].startswith("/marketplace/items/MLM1?attributes=")
    assert "pictures" not in paths[0] and "attributes=available_quantity" not in paths[0]


def test_ozon_dictionary_next_cursor_reuses_downloaded_values(monkeypatch, tmp_path):
    from erp_web.runtime_units import ozon_category_api as api
    from erp_web.stores.category_catalog_store import CategoryCatalogStore
    calls = []
    store = CategoryCatalogStore(tmp_path / "ozon-dict.sqlite3")
    monkeypatch.setattr(api, "_catalog_store", lambda: store)

    def fetch(*a, **k):
        calls.append(a)
        return {"result": [{"id": i, "value": str(i)} for i in range(1, 2001)], "has_next": True}

    monkeypatch.setattr(api, "request_ozon_json", fetch)
    options = dict(
        description_category_id=1,
        type_id=2,
        attribute_id=3,
        client_id="isolated",
        api_key="secret",
        timeout_seconds=2,
        skip_zh=True,
    )
    first, more = api._attribute_value_page(last_value_id=0, **options)
    second, more = api._attribute_value_page(last_value_id=50, **options)
    assert len(calls) == 1 and len(first) == 2000 and len(second) == 1950
    assert second[0]["id"] == "51" and more


def test_category_definition_and_candidates_share_raw_response(tmp_path):
    from erp_web.stores.category_catalog_store import CategoryCatalogStore
    store = CategoryCatalogStore(tmp_path / "http-cache.sqlite3")
    calls = []

    def fetch():
        calls.append(1)
        return [{"id": "BRAND", "values": [{"id": "1", "name": "品牌"}]}]

    url = "https://api.mercadolibre.com/categories/CBT1/attributes"
    first = store.load_cached_value(
        f"ml:GET:scope-a:{url}",
        platform="mercadolibre",
        scope_key="scope-a",
        loader=fetch,
        ttl_seconds=3600,
    )
    first[0]["values"].clear()
    second = store.load_cached_value(
        f"ml:GET:scope-a:{url}",
        platform="mercadolibre",
        scope_key="scope-a",
        loader=fetch,
        ttl_seconds=3600,
    )
    assert len(calls) == 1 and second[0]["values"]
    store.load_cached_value(
        f"ml:GET:scope-b:{url}",
        platform="mercadolibre",
        scope_key="scope-b",
        loader=fetch,
        ttl_seconds=3600,
    )
    assert len(calls) == 2


def test_uncertain_writes_trip_failure_limit_without_becoming_definite_rejections():
    manager = get_context().external_requests
    manager.limiter.set_limit("ozon", consecutive_failure_limit=2)
    first = context(semantics="write", fingerprint="write-fp-1")

    def send(*args, **kwargs):
        raise TimeoutError("写入响应丢失")

    with pytest.raises(ExternalRequestOutcomeUnknown):
        request(manager, first, send)
    with pytest.raises(ExternalRequestBlocked) as blocked:
        request(manager, first, send)
    assert blocked.value.details["outcome_unknown"] is True
    assert blocked.value.details["definitively_rejected"] is False
    with pytest.raises(ExternalRequestOutcomeUnknown):
        request(manager, context(semantics="write", fingerprint="write-fp-2"), send)
    with pytest.raises(ExternalRequestBlocked, match="连续失败"):
        request(manager, context(semantics="write", fingerprint="write-fp-3"), send)
    assert manager.store.query()["stats"]["network_attempts"] == 2


def test_read_max_attempts_applies_only_to_reads_and_resets_per_attempt_timeout():
    import http.client
    from erp_web.services.external_request_context import request_context

    manager = get_context().external_requests
    with request_operation("online_products", operation_id="sync-op", trigger="background", read_max_attempts=3):
        read_ctx = request_context("https://api.partner.market.yandex.ru/v2/businesses/1/offer-mappings", method="POST")
        write_ctx = request_context("https://api.partner.market.yandex.ru/v2/businesses/1/offer-mappings/update", method="POST")
    assert read_ctx.semantics == "read" and read_ctx.max_attempts == 3
    assert write_ctx.semantics == "write" and write_ctx.max_attempts == 1

    # 首轮耗尽单次超时后，第二轮仍获得完整 per-attempt 超时并在瞬时中断后恢复。
    seen_timeouts = []

    def flaky(req, timeout=None, **kw):
        seen_timeouts.append(timeout)
        if len(seen_timeouts) == 1:
            time.sleep(0.06)
            raise http.client.IncompleteRead(b"partial")
        if len(seen_timeouts) == 2:
            raise TimeoutError("瞬时读超时")
        return Response()

    ctx = context(max_attempts=3, timeout=0.05, retry_delay=0.01)
    request(manager, ctx, flaky).close()
    assert len(seen_timeouts) == 3
    assert all(t >= 0.03 for t in seen_timeouts)

    # 存在外层 deadline_at 时不得越界继续重试。
    attempts = []

    def always_timeout(req, timeout=None, **kw):
        attempts.append(1)
        time.sleep(0.04)
        raise TimeoutError("超时")

    with pytest.raises(TimeoutError):
        request(manager, context(max_attempts=3, timeout=0.05, retry_delay=0.05, deadline_at=time.time() + 0.06), always_timeout)
    assert len(attempts) == 1
