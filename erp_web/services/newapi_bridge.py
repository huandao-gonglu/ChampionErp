"""固定 New API 服务的账号交接与可重试撤销；不管理模型 Key 或账务。"""
import os
import re
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

    def flow(self, code):
        if not isinstance(code, str) or re.fullmatch(r"[A-Z0-9]{12}", code) is None:
            raise AccountError("请输入浏览器显示的 12 位授权码。")
        value = self.call("/api/internal/erp/flow", {"code": code})
        try:
            return NewAPIFlow.model_validate(value).model_dump()
        except ValidationError as exc:
            raise AccountError("New API 交接数据无效。", status_code=502) from exc

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

    def sync(self):
        """按账号库中的顺序重试，失败保留原记录；网络等待不持有数据库事务。"""
        if not self.configured:
            return False
        if not self._sync_lock.acquire(blocking=False):
            return bool(self.store.newapi_pending())
        try:
            for item in self.store.newapi_pending():
                try:
                    self.call("/api/internal/erp/sync", {key: item[key] for key in ("user_id", "session_ref", "action")})
                except AccountError:
                    return True
                self.store.newapi_ack(item["id"])
            return bool(self.store.newapi_pending())
        finally:
            self._sync_lock.release()
