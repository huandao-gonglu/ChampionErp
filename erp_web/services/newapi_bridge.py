"""固定 New API 服务的账号交接与可重试撤销与账号专属模型凭据。"""
import os
import re
import hashlib
import secrets
import time
from threading import Lock
from urllib.parse import urlsplit

from argon2.exceptions import VerificationError, InvalidHashError
from pydantic import ValidationError

from erp_web.schemas.accounts import AccountError
from erp_web.schemas.newapi import NewAPIFlow
from erp_web.services.newapi_http import post_newapi


class NewAPIBridge:
    def __init__(self, store, account_service):
        self.store = store
        self.account_service = account_service
        self.public_url = os.environ.get("NEWAPI_PUBLIC_URL", "").rstrip("/")
        self.internal_url = os.environ.get("NEWAPI_INTERNAL_URL", "").rstrip("/")
        self.secret = os.environ.get("NEWAPI_SERVICE_SECRET", "")
        self._sync_lock = Lock()
        self._logout_lock = Lock()
        self._logging_out = set()
        if any((self.public_url, self.internal_url, self.secret)):
            for value in (self.public_url, self.internal_url):
                parsed = urlsplit(value)
                if (not parsed.hostname or parsed.username or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                        or (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}))):
                    raise RuntimeError("New API 地址必须是固定 HTTPS 站点或本机 HTTP 地址。")
            if len(self.secret) < 32:
                raise RuntimeError("New API 服务凭据必须使用至少 32 字节的随机值。")

    @property
    def configured(self):
        return bool(self.public_url and self.internal_url and self.secret)

    def call(self, path, body):
        if not self.configured:
            raise AccountError("New API 尚未配置。", status_code=503)
        return post_newapi(self.internal_url, self.secret, path, body)

    def model_key(self, user_id, display_name, erp_email=""):
        payload = {"user_id": user_id, "display_name": display_name}
        if erp_email:
            payload["erp_email"] = erp_email
        result = self.call("/api/internal/erp/model-key", payload)
        key = result.get("key")
        if not isinstance(key, str) or not key.startswith("sk-erp.v1."):
            raise AccountError("New API 返回的账号凭据无效。", status_code=502)
        return key

    def flow(self, code):
        if not isinstance(code, str) or re.fullmatch(r"[A-Z0-9]{12}", code) is None:
            raise AccountError("请输入浏览器显示的 12 位授权码。")
        value = self.call("/api/internal/erp/flow", {"code": code})
        try:
            return NewAPIFlow.model_validate(value).model_dump()
        except ValidationError as exc:
            raise AccountError("New API 交接数据无效。", status_code=502) from exc

    def launch(self, session, body, origin, ip):
        if not self.configured:
            raise AccountError("New API 尚未配置。", status_code=503)
        self.account_service.limit("newapi", session.user["email"], ip)
        source = body.get("source_origin") or origin
        try:
            parsed = urlsplit(source)
            local = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"} and bool(parsed.port)
        except ValueError as exc:
            raise AccountError("登录交接来源无效。") from exc
        if (not source or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                or source != f"{parsed.scheme}://{parsed.netloc}" or (source != origin and not local)):
            raise AccountError("登录交接来源无效。")
        target = body["target"]
        if target not in {"keys", "wallet", "usage"}:
            raise AccountError("登录目标无效。")
        expires_at = int(time.time()) + 60
        ticket = self.store.newapi_ticket(session, {
            "flow_id": "launch-" + secrets.token_urlsafe(32),
            "challenge": hashlib.sha256(source.encode()).hexdigest(),
            "target": target, "expires_at": expires_at,
        })
        return {"ok": True, "url": self.public_url + "/api/erp/login", "ticket": ticket, "expires_at": expires_at}

    def authorize(self, session, body, ip):
        self.account_service.limit("newapi", session.user["email"], ip)
        flow = self.flow(body["code"])
        reauthenticated = flow["target"] == "proof"
        if reauthenticated:
            password = body.get("password", "")
            if not 1 <= len(password) <= 128:
                raise AccountError("敏感操作需要重新输入 ERP 密码。")
            try:
                self.account_service.hasher.verify(session.user["password_hash"], password)
            except (VerificationError, InvalidHashError) as exc:
                raise AccountError("密码验证失败。", status_code=403) from exc
        ticket = self.store.newapi_ticket(session, flow, reauthenticated=reauthenticated)
        self.call("/api/internal/erp/authorize", {"flow_id": flow["flow_id"], "challenge": flow["challenge"], "ticket": ticket})
        return {"ok": True}

    def session_active(self, user_id, session_ref):
        # New API 签发网页登录后会再核验一次，退出期间不能建立新的关联登录。
        with self._logout_lock:
            if session_ref in self._logging_out:
                return False
        return self.store.newapi_session_active(user_id, session_ref)

    def logout(self, session):
        if not self.configured:
            self.store.revoke_session(session.token_hash)
            return
        ref = self.store.newapi_session_ref(session.token_hash)
        with self._logout_lock:
            if ref in self._logging_out:
                raise AccountError("本次登录正在退出，请稍候。", status_code=409)
            self._logging_out.add(ref)
        try:
            body = {"user_id": session.user["id"], "session_ref": ref, "action": "revoke"}
            try:
                acknowledged = self.call("/api/internal/erp/sync", body).get("success") is True
            except AccountError:
                acknowledged = False
            if not acknowledged:
                raise AccountError("未收到 New API 的退出确认，ERP 仍保持登录，请重新退出。",
                                   status_code=503, error_code="NEWAPI_LOGOUT_UNCONFIRMED")
            self.store.revoke_session(session.token_hash)
        finally:
            with self._logout_lock:
                self._logging_out.remove(ref)

    def sync(self, user_id=None):
        """按账号库中的顺序重试，失败保留原记录；网络等待不持有数据库事务。"""
        if not self.configured:
            return False
        if not self._sync_lock.acquire(blocking=False):
            return bool(self.store.newapi_pending(user_id))
        try:
            for item in self.store.newapi_pending(user_id):
                try:
                    self.call("/api/internal/erp/sync", {key: item[key] for key in ("user_id", "session_ref", "action")})
                except AccountError:
                    return True
                self.store.newapi_ack(item["id"])
            return bool(self.store.newapi_pending(user_id))
        finally:
            self._sync_lock.release()
