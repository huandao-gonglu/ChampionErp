"""通过真实 HTTP 验证个人账号、权限和跨空间隔离。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import quote
from urllib.parse import parse_qs, urlsplit
import asyncio
import subprocess
import os

import pytest
import requests

from erp_web.account_runtime import AccountRuntime
from erp_web.accounts import initialize_admin
from erp_web.context import bind_context, get_context
from erp_web.http_handler import Handler
from erp_web.server import ERPHTTPServer
from erp_web.schemas.accounts import SESSION_COOKIE
from erp_web.services import ai_model_config, ai_prompt_templates

PASSWORD = "这是用于账号回归测试的完整密码123"
NEW_PASSWORD = "这是修改后的独立账号回归测试密码456"


@pytest.fixture
def accounts_http():
    runtime = AccountRuntime(get_context())
    runtime.service.create({"email": "admin@example.com", "display_name": "初始管理员", "password": PASSWORD}, admin=True)
    server = ERPHTTPServer(("127.0.0.1", 0), Handler)
    server.account_runtime = runtime
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}"

    def login(email="admin@example.com", password=PASSWORD):
        client = requests.Session()
        response = client.post(url + "/api/account/login", json={"email": email, "password": password}, timeout=5)
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return client

    yield url, runtime, login
    server.shutdown()
    server.server_close()
    worker.join(timeout=5)
    runtime.close()


def register(url, email="user@example.com"):
    response = requests.post(url + "/api/account/register", json={"email": email, "display_name": "普通用户", "password": PASSWORD}, timeout=5)
    assert response.status_code == 200, response.text
    return response.json()["user"]


def test_public_session_and_all_business_entries_require_login(accounts_http):
    url, runtime, _ = accounts_http
    assert requests.get(url + "/api/account/session").json()["user"] is None
    for path in ("/api/state", "/api/products-index", "/file?path=/tmp/example.png", "/api/ai/conversations/missing/events", "/api/admin/users"):
        assert requests.get(url + path).status_code == 401
    for path in ("/api/desktop/connect", "/api/collect", "/api/account/logout"):
        assert requests.post(url + path, json={}).status_code == 401
    assert runtime._contexts == {}


def test_registration_contract_and_normalized_email(accounts_http):
    url, runtime, login = accounts_http
    response = requests.post(url + "/api/account/register", json={"email": "First@Example.com ", "display_name": " 小明 ", "password": PASSWORD, "role": "admin"})
    assert response.status_code == 400
    user = register(url, "First@Example.com ")
    assert user["role"] == "user" and user["email"] == "first@example.com"
    assert "password_hash" not in user and "workspace" not in user
    duplicate = requests.post(url + "/api/account/register", json={"email": "FIRST@example.com", "display_name": "重复", "password": PASSWORD})
    assert duplicate.status_code == 409
    assert login("first@example.com").get(url + "/api/admin/users").status_code == 403
    assert not (runtime.root.paths.app_dir / "users" / user["id"]).exists()


def test_password_policy_and_session_hash_cookie(accounts_http, monkeypatch):
    url, runtime, _ = accounts_http
    monkeypatch.setenv("ERP_PUBLIC_URL", "https://erp.example.com")
    response = requests.post(url + "/api/account/login", json={"email": "admin@example.com", "password": PASSWORD})
    cookie = response.headers["Set-Cookie"]
    assert all(value in cookie for value in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/", "Max-Age=604800"))
    raw = response.cookies[SESSION_COOKIE]
    with runtime.store._connection() as db:
        row = db.execute("SELECT * FROM sessions").fetchone()
        assert row["token_hash"] != raw and raw not in str(tuple(row))
        assert db.execute("SELECT password_hash FROM users").fetchone()[0].startswith("$argon2id$")
    for password in ("too-short", "x" * 129):
        result = requests.post(url + "/api/account/register", json={"email": "short@example.com", "display_name": "普通用户", "password": password})
        assert result.status_code == 400


def test_csrf_and_logout_revoke_only_current_session(accounts_http):
    url, runtime, login = accounts_http
    first, second = login(), login()
    for invalid in ("wrong", "é"):
        assert first.post(url + "/api/account/logout", json={}, headers={"X-CSRF-Token": invalid}).status_code == 403
    assert first.post(url + "/api/account/logout", json={}).status_code == 200
    assert first.get(url + "/api/state").status_code == 401
    assert second.get(url + "/api/account/session").json()["user"]["role"] == "admin"
    assert runtime.store.admin() is not None


def test_change_reset_and_disable_revoke_all_sessions(accounts_http):
    url, _, login = accounts_http
    user = register(url)
    admin = login()
    first, second = login("user@example.com"), login("user@example.com")
    assert first.post(url + "/api/admin/users/status", json={"user_id": user["id"], "status": "disabled"}).status_code == 403
    assert first.post(url + "/api/account/password", json={"current_password": "wrong", "password": NEW_PASSWORD}).status_code == 400
    assert first.post(url + "/api/account/password", json={"current_password": PASSWORD, "password": NEW_PASSWORD}).status_code == 200
    for client in (first, second):
        assert client.get(url + "/api/state").status_code == 401
    changed = login("user@example.com", NEW_PASSWORD)
    assert admin.post(url + "/api/admin/users/password", json={"user_id": user["id"], "password": PASSWORD}).status_code == 200
    assert changed.get(url + "/api/state").status_code == 401
    current = login("user@example.com")
    assert admin.post(url + "/api/admin/users/status", json={"user_id": user["id"], "status": "disabled"}).status_code == 200
    assert current.get(url + "/api/state").status_code == 401
    assert requests.post(url + "/api/account/login", json={"email": "user@example.com", "password": PASSWORD}).status_code == 401
    assert admin.post(url + "/api/admin/users/status", json={"user_id": user["id"], "status": "enabled"}).status_code == 200
    assert login("user@example.com").get(url + "/api/account/session").json()["user"]["id"] == user["id"]
    admin_id = admin.get(url + "/api/account/session").json()["user"]["id"]
    assert admin.post(url + "/api/admin/users/password", json={"user_id": admin_id, "password": PASSWORD}).status_code == 403


def test_expired_session_and_login_rate_limit(accounts_http):
    url, runtime, login = accounts_http
    client = login()
    with runtime.store._connection() as db:
        db.execute("UPDATE sessions SET expires_at=?", (time.time() - 1,))
    assert client.get(url + "/api/state").status_code == 401
    statuses = [requests.post(url + "/api/account/login", json={"email": "unknown@example.com", "password": PASSWORD}).status_code for _ in range(11)]
    assert statuses[:10] == [401] * 10 and statuses[-1] == 429


def test_two_users_have_concurrent_independent_business_files_and_config(accounts_http, monkeypatch):
    url, runtime, login = accounts_http
    a, b = register(url, "a@example.com"), register(url, "b@example.com")
    clients = [login("a@example.com"), login("b@example.com")]
    contexts = [runtime.context_for(runtime.store.by_email(email)) for email in ("a@example.com", "b@example.com")]
    product_ids = []
    for context, name in zip(contexts, ("甲", "乙")):
        with bind_context(context):
            saved = context.products.save_product({"name": name, "source": {"title": name}})
            product_ids.append(saved["product_id"])
            context.config.save_system_settings({"orders_auto_sync_interval_hours": 9 if name == "甲" else 6})
        context.paths.images_dir.mkdir(parents=True, exist_ok=True)
        (context.paths.images_dir / "private.png").write_bytes(name.encode())
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [(index, pool.submit(clients[index % 2].get, url + "/api/products-index?user_id=" + (b if index % 2 == 0 else a)["id"])) for index in range(16)]
        for index, future in futures:
            response = future.result()
            assert response.status_code == 200, response.text
            assert len(response.json()["items"]) == 1
            item = response.json()["items"][0]
            assert (item.get("product_id") or item.get("id")) == product_ids[index % 2]
    for index, client in enumerate(clients):
        own = contexts[index].paths.images_dir / "private.png"
        other = contexts[1-index].paths.images_dir / "private.png"
        assert client.get(url + "/file?path=" + quote(str(own))).status_code == 200
        assert client.get(url + "/file?path=" + quote(str(other))).status_code == 404
        imported = client.post(url + "/api/image-pool/upload", json={"product_id": product_ids[index], "uploads": [{"path": str(other)}]})
        assert imported.status_code == 403, imported.text
        symlink = contexts[index].paths.images_dir / "escape.png"
        symlink.symlink_to(other)
        assert client.get(url + "/file?path=" + quote(str(symlink))).status_code == 404
        assert client.get(url + "/api/system-settings").json()["orders_auto_sync_interval_hours"] == (9 if index == 0 else 6)
    monkeypatch.setenv("OPENAI_API_KEY", "admin-only-secret")
    for context in contexts:
        with bind_context(context):
            assert ai_model_config.model_api_key({"api_key_env": "OPENAI_API_KEY"}) == ""
            assert ai_model_config.model_api_key({"api_key": "own-key"}) == "own-key"
            with pytest.raises(ValueError, match="当前账号"):
                ai_prompt_templates.write_prompt_json(context.paths.app_dir, str(runtime.root.paths.config_dir / "stolen.json"), system="越界")
    assert runtime.context_for(runtime.store.admin()).paths.app_dir == runtime.root.paths.app_dir


def test_fixed_models_use_only_current_account_credentials(accounts_http, monkeypatch):
    url, runtime, login = accounts_http
    monkeypatch.setattr(runtime.newapi, "internal_url", "http://127.0.0.1:3000")
    calls = []
    def issue(user_id, name):
        calls.append(user_id)
        return "sk-erp.v1.test-" + user_id
    monkeypatch.setattr(runtime.newapi, "model_key", issue)
    for email in ("admin@example.com", "client@example.com"):
        if email.startswith("client"):
            register(url, email)
        client = login(email)
        user = runtime.store.by_email(email)
        context = runtime.context_for(user)
        public = client.get(url + "/api/ai-config").json()["config"]
        rows = public["ai_models"]
        sys_text, sys_image = rows[:2]
        assert [m["name"] for m in rows if m.get("system_managed")] == ["dsflash", "qwenflash"]
        assert all("api_key" not in m and "api_key_env" not in m for m in (sys_text, sys_image))
        def save(models, **changes):
            return client.post(url + "/api/ai-config/save", json={"config": {"ai_models": models, **changes}})

        for models in (rows[1:], rows + [sys_text], [{**sys_text, "base_url": "https://example.org"}, *rows[1:]]):
            assert save(models).status_code == 403
        assert client.post(url + "/api/test-ai-model", json={"model": {**sys_image, "api_key": "custom"}}).status_code == 403
        custom = {"id": "custom", "name": "个人连接", "provider_id": "openai", "model": "custom-chat",
                  "base_url": "https://example.org", "api_key": "personal-secret", "capabilities": ["chat", "tool_calling"]}
        bindings = {"global.chat": {"model_id": "custom"}, "image.translate": {"model_id": sys_image["id"]}}
        result = save([*rows, custom], ai_use_case_bindings=bindings)
        assert result.status_code == 200, result.text
        public_models = result.json()["config"]["ai_models"]
        assert "personal-secret" not in result.text
        assert save([{**m, "name": "重命名"} if m["id"] == sys_image["id"] else m for m in public_models]).status_code == 200
        with bind_context(context):
            cfg = context.config.load_app_config()
            selected = ai_model_config.resolve_ai_model(cfg, "global.chat")
            assert selected["id"] == "custom" and ai_model_config.model_api_key(selected) == "personal-secret"
            assert ai_model_config.resolve_ai_model(cfg, "image.translate")["name"] == "重命名"
        assert client.post(url + "/api/save-settings", json={"appConfig": {"ai_models": []}}).status_code == 403
        general = {"appConfig": {"ai_models": rows, "ai_use_case_bindings": {"global.chat": {"model_id": sys_text["id"]}}}}
        assert client.post(url + "/api/save-settings", json=general).status_code == 200
        assert "custom" not in {m["id"] for m in context.config.load_app_config()["ai_models"]}
        assert save(rows, ai_use_case_bindings={}).status_code == 200
        assert not any(m["id"] == "custom" for m in context.config.load_app_config()["ai_models"])
        assert not calls
        with bind_context(context):
            cfg = context.config.load_app_config()
            for use_case, name in (("global.chat", "deepseek-flash"), ("image.translate", "qwen-image-3.0")):
                model = ai_model_config.resolve_ai_model(cfg, use_case)
                assert model["model"] == name and model["base_url"] == "http://127.0.0.1:3000/v1"
                assert model["api_style"] == "openai_compatible"
                assert ai_model_config.model_api_key(model) == "sk-erp.v1.test-" + user["id"]
                with pytest.raises(ValueError, match="当前账号"):
                    ai_model_config.model_api_key({**model, "base_url": "https://example.org/v1"})
        assert calls == [user["id"], user["id"]]
        calls.clear()
        with pytest.raises(ValueError, match="当前账号"):
            ai_model_config.model_api_key(model)


def test_desktop_connection_is_bound_to_user_and_session(accounts_http):
    url, runtime, login = accounts_http
    register(url, "a@example.com"); register(url, "b@example.com")
    first, second, other = login("a@example.com"), login("a@example.com"), login("b@example.com")
    connection = first.post(url + "/api/desktop/connect", json={}).json()["connection_id"]
    headers = {"X-Champion-Desktop": connection}
    for client in (second, other):
        assert client.post(url + "/api/desktop/disconnect", json={}, headers=headers).status_code == 409
    assert first.post(url + "/api/account/logout", json={}, headers=headers).status_code == 200
    context = runtime.context_for(runtime.store.by_email("a@example.com"))
    assert connection not in context.desktop_connections.connections


def test_bootstrap_backs_up_and_preserves_existing_business_idempotently(tmp_path):
    root = tmp_path / "legacy"
    (root / "config").mkdir(parents=True)
    (root / "config" / "private.json").write_text('{"secret":"原凭据"}')
    with sqlite3.connect(root / "erp.sqlite3") as db:
        db.execute("CREATE TABLE original (id TEXT)")
        db.execute("INSERT INTO original VALUES ('existing-id')")
    answers = iter([PASSWORD, PASSWORD])
    admin, backup = initialize_admin(root, "Admin@Example.com", "初始管理员", lambda _: next(answers))
    assert backup and backup.exists()
    assert admin["role"] == "admin"
    existing, repeated_backup = initialize_admin(root, "admin@example.com", "不会覆盖", lambda _: pytest.fail("重复执行不应读取密码"))
    assert existing["id"] == admin["id"] and repeated_backup is None
    with sqlite3.connect(root / "erp.sqlite3") as db:
        assert db.execute("SELECT id FROM original").fetchone()[0] == "existing-id"


def test_callbacks_select_owner_and_still_verify_domain_credentials(accounts_http):
    url, runtime, _ = accounts_http
    users = [register(url, email) for email in ("a@example.com", "b@example.com")]
    contexts = [runtime.context_for(runtime.store.by_email(email)) for email in ("a@example.com", "b@example.com")]
    tokens = []
    for index, context in enumerate(contexts):
        with bind_context(context):
            context.config.save_store_config({"yandex": {"campaign_id": str(index + 1), "business_id": str(index + 10), "api_token": "test-platform-token"}})
            context.config.save_order_notification_public_url("https://erp.example.com")
            tokens.append(context.config.order_notification_token("yandex"))
            context.order_notifications
    for index, context in enumerate(contexts):
        with bind_context(contexts[1-index]):
            callback = next(row["callback_url"] for row in context.order_notifications.integrations()["platforms"] if row["platform"] == "yandex")
            assert parse_qs(urlsplit(callback).query)["owner"] == [users[index]["id"]]
    for index, user in enumerate(users):
        response = requests.post(url + f"/api/yandex/notifications?owner={user['id']}&token={tokens[index]}", json={"notificationType": "PING"})
        assert response.status_code == 200, response.text
        denied = requests.post(url + f"/api/yandex/notifications?owner={user['id']}&token={tokens[1-index]}", json={"notificationType": "PING"})
        assert denied.status_code == 403
        # owner 和平台 token 都不是 ERP 登录凭据。
        assert requests.get(url + f"/api/state?owner={user['id']}&token={tokens[index]}").status_code == 401
    admin = runtime.context_for(runtime.store.admin())
    with bind_context(admin):
        admin.config.save_store_config({"yandex": {"campaign_id": "20", "business_id": "21", "api_token": "test-admin-platform-token"}})
        old_token = admin.config.order_notification_token("yandex")
    assert requests.post(url + f"/api/yandex/notifications?token={old_token}", json={"notificationType": "PING"}).status_code == 200


def test_oauth_callback_routes_and_consumes_state_in_correct_space(accounts_http, monkeypatch):
    url, runtime, login = accounts_http
    monkeypatch.setenv("ERP_ALIBABA_APP_KEY", "123456")
    monkeypatch.setenv("ERP_ALIBABA_APP_SECRET", "deployment-application-secret")
    monkeypatch.setenv("ERP_ALIBABA_REDIRECT_URI", url + "/auth/alibaba/callback")
    from erp_web.services.alibaba_authorization_service import AlibabaAuthorizationService
    monkeypatch.setattr(AlibabaAuthorizationService, "_token", staticmethod(lambda application, values: {
        "access_token": "buyer-token-" + values["code"], "memberId": values["code"], "expires_in": 3600,
    }))
    states = []
    users = []
    for email in ("a@example.com", "b@example.com"):
        user = register(url, email)
        users.append(user)
        response = login(email).post(url + "/api/alibaba/authorization/start", json={})
        assert response.status_code == 200
        state = parse_qs(urlsplit(response.json()["url"]).query)["state"][0]
        assert state.startswith(f"account.{user['id']}.")
        states.append(state)
    forged = states[1].replace(users[1]["id"], users[0]["id"], 1)
    assert requests.get(url + "/auth/alibaba/callback", params={"code": "b", "state": forged}).status_code == 400
    for index, state in enumerate(states):
        callback = url + "/auth/alibaba/callback"
        response = requests.get(callback, params={"code": str(index), "state": state})
        assert response.status_code == 200, response.text
        assert requests.get(callback, params={"code": str(index), "state": state}).status_code == 400
        context = runtime.context_for(runtime.store.by_email(("a@example.com", "b@example.com")[index]))
        with bind_context(context):
            assert context.config.alibaba_credentials()["authorization"]["member_id"] == str(index)


def test_background_threads_and_detached_coroutines_keep_original_space(accounts_http, monkeypatch):
    _, runtime, _ = accounts_http
    from erp_web.services import product_research_service
    from erp_web.services.ai_chat_detached_runner import get_detached_chat_runner
    contexts = []
    for email in ("a@example.com", "b@example.com"):
        runtime.service.create({"email": email, "display_name": email, "password": PASSWORD})
        contexts.append(runtime.context_for(runtime.store.by_email(email)))
    barrier = threading.Barrier(3)
    finished = threading.Event()
    seen = []
    lock = threading.Lock()
    monkeypatch.setattr(product_research_service, "_store_hot_product_run", lambda *a, **k: (None, {"run_id": "same-business-id"}, {}, {}))
    def work(*args):
        barrier.wait(timeout=5)
        with lock:
            seen.append(get_context().user_id)
            if len(seen) == 2:
                finished.set()
    monkeypatch.setattr(product_research_service, "_run_hot_product_worker", work)
    futures = []
    async def detached():
        await asyncio.sleep(0.01)
        return get_context().user_id
    for context in contexts:
        with bind_context(context):
            product_research_service.create_hot_product_run_async(context.paths.app_dir, {}, {})
            futures.append(get_detached_chat_runner().submit(detached()))
    barrier.wait(timeout=5)
    assert finished.wait(5)
    assert set(seen) == {context.user_id for context in contexts}
    assert [future.result(timeout=5) for future in futures] == [context.user_id for context in contexts]


def test_electron_proxy_logs_into_real_backend_and_shares_web_data(accounts_http):
    url, runtime, login = accounts_http
    user = register(url, "desktop@example.com")
    context = runtime.context_for(runtime.store.by_email(user["email"]))
    with bind_context(context):
        product = context.products.save_product({"name": "网页桌面共用", "source": {"title": "网页桌面共用"}})
    source = r"""
const http = require('node:http');
const assert = require('node:assert/strict');
const { AccountSession, proxyBackendRequest } = require(process.argv[4] + '/account-session.cjs');
const { connectDesktop } = require(process.argv[4] + '/connection.cjs');
(async () => {
  const session = new AccountSession();
  let connection;
  const proxy = http.createServer((req, res) => proxyBackendRequest(req, res, process.argv[1], {
    session, connectionId: () => connection?.id || '',
    onLogin: async () => { connection = await connectDesktop(process.argv[1], { cancel() {}, perform() {} }, { session }); },
    onLogout: () => { void connection?.stop(); },
  }));
  await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve));
  try {
    const base = 'http://127.0.0.1:' + proxy.address().port;
    const login = await fetch(base + '/api/account/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: 'desktop@example.com', password: process.argv[2] }) });
    assert.equal(login.status, 200);
    assert.equal(login.headers.get('set-cookie'), null);
    const data = await login.json();
    assert.ok(connection.id);
    const products = await (await fetch(base + '/api/products-index')).json();
    assert.equal(products.items[0].product_id || products.items[0].id, process.argv[3]);
    const logout = await fetch(base + '/api/account/logout', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': data.csrf_token }, body: '{}' });
    assert.equal(logout.status, 200);
    assert.equal(session.cookie, '');
    assert.equal((await fetch(base + '/api/products-index')).status, 401);
  } finally { await connection?.stop(); proxy.closeAllConnections(); proxy.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    root = Path(__file__).resolve().parents[1]
    executable = os.environ.get("ERP_TEST_ELECTRON_EXECUTABLE", "node")
    modules = os.environ.get("ERP_TEST_DESKTOP_MODULE_ROOT", str(root / "front" / "desktop"))
    result = subprocess.run([executable, "-e", source, url, PASSWORD, product["product_id"], modules], cwd=root,
                            env={**os.environ, "ELECTRON_RUN_AS_NODE": "1"}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert login(user["email"]).get(url + "/api/products-index").json()["items"]


def test_conversation_and_approval_credentials_do_not_cross_users(accounts_http):
    url, runtime, login = accounts_http
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
    register(url, "a@example.com")
    register(url, "b@example.com")
    first, second = login("a@example.com"), login("b@example.com")
    context = runtime.context_for(runtime.store.by_email("a@example.com"))
    conversation = "conversation_global_chat_" + "a" * 32
    with bind_context(context):
        context.pydantic_messages.save(conversation, [ModelRequest(parts=[UserPromptPart(content="甲的私有历史")]), ModelResponse(parts=[TextPart(content="甲的回复")])])
    path = f"/api/v1/ai-work/conversations/{conversation}/ui-messages"
    assert first.get(url + path).status_code == 200
    assert second.get(url + path).status_code == 404
    assert first.get(url + "/api/state").json()["approvalToken"] != second.get(url + "/api/state").json()["approvalToken"]


def test_only_loopback_https_proxy_can_supply_rate_limit_ip(monkeypatch):
    from types import SimpleNamespace
    from erp_web.services.account_request import client_ip
    monkeypatch.setenv("ERP_PUBLIC_URL", "https://erp.example.com")
    handler = SimpleNamespace(client_address=("127.0.0.1", 1234), headers={"X-Real-IP": "198.51.100.2"})
    assert client_ip(handler) == "198.51.100.2"
    handler.client_address = ("198.51.100.3", 1234)
    assert client_ip(handler) == "198.51.100.3"
    handler.client_address = ("127.0.0.1", 1234)
    handler.headers["X-Real-IP"] = "invalid"
    assert client_ip(handler) == "127.0.0.1"


def test_account_and_callback_requests_cannot_claim_business_presentations(accounts_http, monkeypatch):
    url, _, login = accounts_http
    from erp_web import http_routes
    claims = []
    monkeypatch.setattr(http_routes, "_claim_presentation_scope", lambda handler: claims.append(handler.path))
    response = requests.post(url + "/api/account/register", json={"email": "a@example.com", "display_name": "甲", "password": PASSWORD}, headers={"X-AI-Presentation-ID": "another-users-presentation"})
    assert response.status_code == 200
    client = login("a@example.com")
    assert requests.post(url + "/api/yandex/notifications?token=wrong", json={"notificationType": "PING"}, headers={"X-AI-Presentation-ID": "another-users-presentation"}).status_code == 403
    assert claims == []
    response = client.post(url + "/api/save-product", json={"product": {"name": "甲", "source": {"title": "甲"}}})
    assert response.status_code == 200, response.text
    assert claims == ["/api/save-product"]


def test_release_pauses_new_requests_and_preserves_reads_and_desktop_results(accounts_http, release_guard_dir):
    from erp_web.release_guard import locked_file, release_guard

    url, _, login = accounts_http
    client = login()
    with release_guard.serving(release_guard_dir), locked_file(release_guard_dir, 'admission', exclusive=True):
        blocked = client.post(url + '/api/publish-bus/retry', json={}, timeout=2)
        assert blocked.status_code == 503
        assert blocked.json()['error_code'] == 'RELEASE_DRAINING'
        assert client.get(url + '/api/publish-bus/jobs', timeout=2).status_code == 200
        invalid_reply = client.post(url + '/api/desktop/result', json={}, timeout=2)
        assert invalid_reply.status_code == 400
    assert client.post(url + '/api/publish-bus/retry', json={}, timeout=2).status_code != 503


def test_desktop_minimum_rejects_new_registration_but_allows_existing_receipts(accounts_http, tmp_path, monkeypatch):
    import json
    from erp_web.services import desktop_version_policy

    url, _, login = accounts_http
    client = login()
    monkeypatch.setattr(desktop_version_policy, 'RELEASES', tmp_path)
    folder = tmp_path / 'darwin-arm64'
    folder.mkdir()
    (folder / 'latest.json').write_text(json.dumps({'platform': 'darwin', 'arch': 'arm64', 'minimum': '1.2.9'}))
    headers = {'X-Champion-Client': 'darwin-arm64/1.2.8'}
    for endpoint in ('/api/desktop/connect', '/api/publish-bus/retry'):
        response = client.post(url + endpoint, json={}, headers=headers, timeout=3)
        assert response.status_code == 426 and response.json()['error_code'] == 'DESKTOP_UPDATE_REQUIRED'
    assert client.post(url + '/api/desktop/result', json={}, headers=headers, timeout=3).status_code != 426
    assert client.post(url + '/api/desktop/connect', json={}, timeout=3).status_code == 200
