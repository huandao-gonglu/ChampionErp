"""公开注册的邮箱所有权验证。发码前由服务器核验 Turnstile。"""
import hashlib
import hmac
import os
import re
import secrets
import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage
from email.errors import HeaderParseError
from urllib.parse import urlsplit

import httpx

from erp_web.schemas.accounts import AccountError, normalize_email


def _setting(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise AccountError("注册发码服务尚未配置，请联系管理员。", status_code=503)
    return value


def verify_human(token, ip):
    if not token or len(token) > 2048:
        raise AccountError("请完成人机验证。")
    fields = {"secret": _setting("ERP_TURNSTILE_SECRET"), "response": token, "remoteip": ip}
    hostname = urlsplit(_setting("ERP_PUBLIC_URL")).hostname
    try:
        with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
            response = client.post("https://challenges.cloudflare.com/turnstile/v0/siteverify", data=fields)
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError):
        raise AccountError("暂时无法连接人机验证服务，请稍后重试。", status_code=503) from None
    if (not isinstance(result, dict) or result.get("success") is not True
            or result.get("action") != "register_email" or result.get("hostname") != hostname):
        raise AccountError("人机验证已失效，请重新验证。")


def _digest(email, code):
    return hmac.new(_setting("ERP_TURNSTILE_SECRET").encode(), f"{email}\0{code}".encode(), hashlib.sha256).hexdigest()


def send_email(email, code):
    sender = _setting("ERP_SMTP_USER")
    password = _setting("ERP_SMTP_PASSWORD")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = email
    message["Subject"] = "gongluErp 注册验证码"
    message.set_content(f"你的注册验证码为 {code}，10 分钟内有效。")
    try:
        with smtplib.SMTP_SSL(_setting("ERP_SMTP_HOST"), int(os.environ.get("ERP_SMTP_PORT", "465")),
                              timeout=12, context=ssl.create_default_context()) as smtp:
            smtp.login(sender, password)
            smtp.send_message(message, from_addr=sender, to_addrs=[email])
    except (ValueError, OSError, smtplib.SMTPException):
        raise AccountError("验证码邮件发送失败，请稍后重试或联系管理员。", status_code=503) from None


def _new_code():
    return f"{secrets.randbelow(1000000):06d}"


def issue_code(service, body, ip):
    email = normalize_email(body["email"])
    try:
        mailbox = Address(addr_spec=email)
        if mailbox.addr_spec != email or not email.isascii():
            raise ValueError
    except (ValueError, HeaderParseError):
        raise AccountError("请输入有效的登录邮箱。") from None
    service.limit("registration-code", email, ip)
    verify_human(body["human_token"], ip)
    if service.store.by_email(email):
        raise AccountError("该邮箱已注册。", status_code=409)
    code = _new_code()
    digest = _digest(email, code)
    service.store.reserve_registration_code(email, digest)
    try:
        send_email(email, code)
    except AccountError:
        service.store.discard_registration_code(email, digest)
        raise
    return {"ok": True}


def verified_digest(email, code):
    if not re.fullmatch(r"[0-9]{6}", code):
        raise AccountError("请输入 6 位邮箱验证码。")
    return _digest(normalize_email(email), code)
