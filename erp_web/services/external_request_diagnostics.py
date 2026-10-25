"""外部请求失败的有界诊断；不记录异常原文、局部变量或请求/响应内容。"""
from __future__ import annotations

import errno
import re
import socket
import ssl
from pathlib import Path


def _identifier(value: str) -> str:
    return value[:160] if re.fullmatch(r"[a-zA-Z0-9_.<>/-]+", value) else "unknown"


def exception_diagnostics(error: BaseException) -> dict:
    """沿原生 cause/context 提取类型、系统错误码及栈位置，不依赖 SDK 异常层级。"""
    chain = []
    seen = set()
    request = None
    current = error
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        item = {"type": _identifier(type(current).__module__ + "." + type(current).__name__)}
        if isinstance(current, OSError) and isinstance(current.errno, int):
            item["errno"] = current.errno
            if isinstance(current, socket.gaierror):
                item["reason"] = "DNS_RESOLUTION_FAILED"
            elif current.errno in errno.errorcode:
                item["reason"] = errno.errorcode[current.errno]
        if isinstance(current, ssl.SSLError):
            if isinstance(getattr(current, "verify_code", None), int):
                item["tls_verify_code"] = current.verify_code
            if isinstance(getattr(current, "reason", None), str):
                item["tls_reason"] = _identifier(current.reason)
        # 只识别固定诊断，不抄录可能含 URL、凭据、请求体的异常消息。
        message = str(current).lower()
        for text, reason in (
            ("event loop is closed", "EVENT_LOOP_CLOSED"),
            ("different event loop", "DIFFERENT_EVENT_LOOP"),
            ("server disconnected without sending a response", "SERVER_DISCONNECTED_BEFORE_RESPONSE"),
            ("incomplete chunked read", "INCOMPLETE_CHUNKED_READ"),
            ("illegal header value", "ILLEGAL_HEADER_VALUE"),
        ):
            if text in message:
                item["reason"] = reason
                break
        frames = []
        tb = current.__traceback__
        while tb is not None:
            code = tb.tb_frame.f_code
            frames.append({"file": _identifier(Path(code.co_filename).name),
                           "function": _identifier(code.co_name), "line": tb.tb_lineno})
            tb = tb.tb_next
        item["frames"] = frames[-12:]
        chain.append(item)
        if request is None:
            request = getattr(current, "_erp_external_request_diagnostic", None)
        cause = current.__cause__
        if cause is None and not current.__suppress_context__:
            cause = current.__context__
        # urllib 的 URLError 将系统异常保存在 reason 而非 cause 中。
        if cause is None and isinstance(getattr(current, "reason", None), BaseException):
            cause = current.reason
        current = cause
    result = {"exception_chain": chain}
    if isinstance(request, dict):
        result["external_request"] = request
    return result


__all__ = ["exception_diagnostics"]
