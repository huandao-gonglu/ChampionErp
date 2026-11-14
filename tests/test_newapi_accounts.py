"""通过真实 ERP HTTP 边界验证交接、二次验证及可重试撤销。"""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import threading
import time

import pytest
import requests

from tests.test_accounts import accounts_http, register, PASSWORD


SECRET = "test-service-secret-that-is-longer-than-32-bytes"
VERIFIER = "browser-owned-verifier-with-high-entropy-for-tests"
CODE = "ABCDEF123456"


@pytest.fixture
def bridge_http(accounts_http):
    url, runtime, login = accounts_http
    state = {"target": "keys", "ticket": None, "fail_sync": False, "synced": []}

    class Peer(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.headers["Authorization"] == "Bearer " + SECRET
            assert not self.headers.get("Cookie")
            status, result = 200, {"success": True}
            if self.path == "/api/internal/erp/flow":
                assert body == {"code": CODE}
                result = {"flow_id": "browser-flow-for-test", "challenge": hashlib.sha256(VERIFIER.encode()).hexdigest(),
                          "target": state["target"], "expires_at": int(time.time()+180)}
            elif self.path == "/api/internal/erp/authorize":
                assert set(body) == {"flow_id", "challenge", "ticket"}
                state["ticket"] = body["ticket"]
            elif self.path == "/api/internal/erp/sync":
                if state["fail_sync"]:
                    status, result = 503, {"success": False}
                else:
                    state["synced"].append(body)
                    if state.pop("lose_ack", False):
                        # 平台已执行撤销，但在发送回执前连接中断。
                        self.close_connection = True
                        return
            else:
                raise AssertionError(self.path)
            raw = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    runtime.newapi.internal_url = f"http://127.0.0.1:{server.server_port}"
    runtime.newapi.public_url = "http://127.0.0.1:3000"
    runtime.newapi.secret = SECRET
    yield url, runtime, login, state
    server.shutdown()
    server.server_close()
    worker.join(timeout=5)


def exchange(url, ticket, *, verifier=VERIFIER):
    return requests.post(url + "/api/internal/newapi/exchange", json={"ticket": ticket, "verifier": verifier,
                         "flow_id": "browser-flow-for-test", "target": "keys"}, headers={"Authorization": "Bearer " + SECRET}, timeout=5)


def test_ticket_requires_browser_service_session_and_is_one_use(bridge_http):
    url, runtime, login, peer = bridge_http
    a, b = register(url, "a@example.com"), register(url, "b@example.com")
    client = login("a@example.com")
    assert client.post(url + "/api/account/newapi/authorize", json={"code": CODE, "user_id": b["id"]}).status_code == 400
    response = client.post(url + "/api/account/newapi/authorize", json={"code": CODE})
    assert response.status_code == 200
    ticket = peer["ticket"]
    assert ticket and ticket not in response.text
    with runtime.store._connection() as db:
        assert db.execute("SELECT ticket_hash FROM newapi_tickets").fetchone()[0] != ticket
    assert requests.post(url + "/api/internal/newapi/exchange", json={}).status_code == 401
    assert client.post(url + "/api/internal/newapi/exchange", json={}, headers={"Authorization": "Bearer " + SECRET}).status_code == 401
    assert exchange(url, ticket, verifier="other-browser").status_code == 401
    result = exchange(url, ticket)
    assert result.status_code == 200
    identity = result.json()
    assert identity["user_id"] == a["id"] and identity["user_id"] != b["id"]
    assert not {"password", "password_hash", "email", "csrf_token", "role"} & identity.keys()
    assert identity["session_ref"] != client.cookies.get("champion_session")
    assert exchange(url, ticket).status_code == 401
    assert runtime._contexts == {}


def test_expired_ticket_and_logout_do_not_allow_exchange(bridge_http):
    url, runtime, login, peer = bridge_http
    client = login()
    assert client.post(url + "/api/account/newapi/authorize", json={"code": CODE}).status_code == 200
    with runtime.store._connection() as db:
        db.execute("UPDATE newapi_tickets SET expires_at=?", (time.time()-1,))
    assert exchange(url, peer["ticket"]).status_code == 401
    assert client.post(url + "/api/account/newapi/authorize", json={"code": CODE}).status_code == 200
    assert client.post(url + "/api/account/logout", json={}).status_code == 200
    assert exchange(url, peer["ticket"]).status_code == 401


def test_sensitive_flow_requires_fresh_password_without_sending_it_to_newapi(bridge_http):
    url, _, login, peer = bridge_http
    peer["target"] = "proof"
    client = login()
    assert client.post(url + "/api/account/newapi/authorize", json={"code": CODE}).status_code == 400
    assert client.post(url + "/api/account/newapi/authorize", json={"code": CODE, "password": "incorrect"}).status_code == 403
    assert peer["ticket"] is None
    assert client.post(url + "/api/account/newapi/authorize", json={"code": CODE, "password": PASSWORD}).status_code == 200
    response = requests.post(url + "/api/internal/newapi/exchange", json={"ticket": peer["ticket"], "verifier": VERIFIER,
                             "flow_id": "browser-flow-for-test", "target": "proof"}, headers={"Authorization": "Bearer " + SECRET}, timeout=5)
    assert response.json()["reauthenticated"] is True


def test_logout_requires_ack_and_only_retries_the_current_session(bridge_http, monkeypatch):
    url, runtime, login, peer = bridge_http
    a, b = login(), login()
    session = runtime.store.session(a.cookies.get("champion_session"))
    ref = runtime.store.newapi_session_ref(session.token_hash)
    payload = {"user_id": session.user["id"], "session_ref": ref, "action": "revoke"}
    peer["lose_ack"] = True
    response = a.post(url + "/api/account/logout", json={})
    assert response.status_code == 503 and response.json()["error_code"] == "NEWAPI_LOGOUT_UNCONFIRMED"
    assert a.get(url + "/api/account/session").json()["user"]["id"] == session.user["id"]
    assert runtime.store.newapi_pending() == []
    reached, release = threading.Event(), threading.Event()
    call = runtime.newapi.call

    def await_reply(path, body):
        result = call(path, body)
        reached.set()
        assert release.wait(4)
        return result

    with monkeypatch.context() as patch, ThreadPoolExecutor(1) as pool:
        patch.setattr(runtime.newapi, "call", await_reply)
        logout = pool.submit(a.post, url + "/api/account/logout", json={})
        try:
            assert reached.wait(4)
            # 回执尚未收到时 ERP 仍然有效，但新交接不能在远端撤销之后建立关联登录。
            assert runtime.store.session_valid(session.token_hash)
            check = requests.post(url + "/api/internal/newapi/session",
                                  headers={"Authorization": "Bearer " + SECRET},
                                  json={"user_id": session.user["id"], "session_ref": ref}, timeout=3)
            assert check.json()["active"] is False
        finally:
            release.set()
        assert logout.result().status_code == 200
    assert a.get(url + "/api/account/session").json()["user"] is None
    assert b.get(url + "/api/account/session").json()["user"]["id"] == session.user["id"]
    assert peer["synced"] == [payload, payload]
    assert runtime.store.newapi_pending() == []


def test_account_page_and_logout_do_not_process_others_pending_notifications(bridge_http):
    url, runtime, login, peer = bridge_http
    user = register(url)
    admin = login()
    client = login("user@example.com")
    session = runtime.store.session(client.cookies.get("champion_session"))
    old = {"user_id": user["id"], "session_ref": "old-session", "action": "revoke"}
    with runtime.store._connection() as db:
        db.execute("INSERT INTO newapi_sync(user_id,session_ref,action) VALUES (?,?,?)", tuple(old.values()))
    assert admin.get(url + "/api/account/newapi").json()["sync_pending"] is False
    assert client.get(url + "/api/account/newapi").json()["sync_pending"] is True
    assert peer["synced"] == []
    assert client.post(url + "/api/account/logout", json={}).status_code == 200
    assert peer["synced"] == [{"user_id": user["id"], "action": "revoke",
                               "session_ref": runtime.store.newapi_session_ref(session.token_hash)}]
    assert runtime.newapi.sync() is False
    assert peer["synced"][-1] == old


def test_switch_account_requires_old_session_logout_ack(bridge_http):
    url, runtime, login, peer = bridge_http
    user = register(url)
    client = login()
    original = client.get(url + "/api/account/session").json()["user"]["id"]
    credentials = {"email": "user@example.com", "password": PASSWORD}
    peer["fail_sync"] = True
    assert client.post(url + "/api/account/login", json=credentials).status_code == 503
    assert client.get(url + "/api/account/session").json()["user"]["id"] == original
    with runtime.store._connection() as db:
        assert db.execute("SELECT count(*) FROM sessions WHERE user_id=?", (user["id"],)).fetchone()[0] == 0
    peer["fail_sync"] = False
    assert client.post(url + "/api/account/login", json=credentials).status_code == 200
    assert client.get(url + "/api/account/session").json()["user"]["id"] == user["id"]
    assert runtime.store.newapi_pending() == []


def test_disable_and_password_change_enqueue_revocations_atomically(bridge_http):
    url, runtime, login, peer = bridge_http
    user = register(url)
    client, admin = login("user@example.com"), login()
    session = runtime.store.session(client.cookies.get("champion_session"))
    peer["fail_sync"] = True
    response = admin.post(url + "/api/admin/users/status", json={"user_id": user["id"], "status": "disabled"})
    assert response.status_code == 200 and response.json()["newapi_sync_pending"] is True
    assert not runtime.store.newapi_session_active(user["id"], runtime.store.newapi_session_ref(session.token_hash))
    assert runtime.store.newapi_pending()[0]["action"] == "disable"
    assert admin.post(url + "/api/admin/users/status", json={"user_id": user["id"], "status": "enabled"}).status_code == 200
    peer["fail_sync"] = False
    assert runtime.newapi.sync() is False
    assert [item["action"] for item in peer["synced"]] == ["disable", "enable"]
    client = login("user@example.com")
    response = client.post(url + "/api/account/password", json={"current_password": PASSWORD, "password": "新的独立测试密码必须足够长123456"})
    assert response.status_code == 200
    assert peer["synced"][-1] == {"action": "revoke", "user_id": user["id"], "session_ref": ""}


def test_auto_launch_is_bound_to_current_user_origin_and_one_use(bridge_http):
    url, runtime, login, _ = bridge_http
    user = register(url, "auto@example.com")
    client = login("auto@example.com")
    response = client.post(url + "/api/account/newapi/launch", json={"target": "wallet"}, headers={"Origin": url})
    assert response.status_code == 200
    launch = response.json()
    assert launch["url"] == "http://127.0.0.1:3000/api/erp/login"
    assert "ticket" not in launch["url"] and 0 < launch["expires_at"]-time.time() <= 60
    assert client.post(url + "/api/account/newapi/launch", json={"target": "keys", "user_id": "other"}).status_code == 400
    assert client.post(url + "/api/account/newapi/launch", json={"target": "proof"}).status_code == 400
    assert client.post(url + "/api/account/newapi/launch", json={"target": "keys", "source_origin": "https://attacker.invalid"}).status_code == 400
    endpoint = url + "/api/internal/newapi/launch"
    headers = {"Authorization": "Bearer " + SECRET}
    assert requests.post(endpoint, json={"ticket": launch["ticket"], "origin": "https://attacker.invalid"}, headers=headers).status_code == 401
    result = requests.post(endpoint, json={"ticket": launch["ticket"], "origin": url}, headers=headers)
    assert result.status_code == 200
    assert result.json()["user_id"] == user["id"] and result.json()["target"] == "wallet"
    assert not {"password", "role", "email", "csrf_token"} & result.json().keys()
    assert requests.post(endpoint, json={"ticket": launch["ticket"], "origin": url}, headers=headers).status_code == 401
    assert runtime._contexts == {}


def test_auto_launch_desktop_origin_expiry_and_logout(bridge_http):
    url, runtime, login, _ = bridge_http
    client = login()
    for action in ("expired", "logout"):
        launch = client.post(url + "/api/account/newapi/launch", json={"target": "keys", "source_origin": "http://127.0.0.1:43210"}).json()
        assert "ticket" in launch
        if action == "expired":
            with runtime.store._connection() as db:
                db.execute("UPDATE newapi_tickets SET expires_at=?", (time.time()-1,))
        else:
            assert client.post(url + "/api/account/logout", json={}).status_code == 200
        assert requests.post(url + "/api/internal/newapi/launch", json={"ticket": launch["ticket"], "origin": "http://127.0.0.1:43210"}, headers={"Authorization": "Bearer " + SECRET}).status_code == 401
