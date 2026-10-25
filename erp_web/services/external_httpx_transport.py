"""SDK 原生 HTTPX Transport 注入；不额外执行模型或重放推理请求。"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx2 as httpx

from erp_web.context import get_context
from erp_web.services.external_request_context import request_context

_logger = logging.getLogger(__name__)


class RequestDiagnostics:
    """复用 HTTPX 原生 trace 扩展，仅保留阶段名称，不保存 trace 的敏感参数。"""

    def __init__(self, request, data):
        self.started = time.monotonic()
        self.phase = "transport_setup"
        self.request_bytes = len(data) if data is not None else None
        self.response_bytes = 0
        self.http_status = None
        self.trace_event = ""
        self.previous_trace = request.extensions.get("trace")
        request.extensions["trace"] = self.trace

    async def trace(self, name, info):
        parts = name.split(".")
        if len(parts) == 3 and parts[1] in {
            "connect_tcp", "connect_unix_socket", "start_tls", "send_request_headers",
            "send_request_body", "receive_response_headers", "receive_response_body",
        } and parts[2] in {"started", "complete", "failed"}:
            self.trace_event = ".".join(parts[1:])
        if self.previous_trace is not None:
            await self.previous_trace(name, info)

    def snapshot(self):
        values = {"phase": self.phase, "trace_event": self.trace_event,
                  "elapsed_ms": round((time.monotonic() - self.started) * 1000),
                  "request_bytes": self.request_bytes, "response_bytes": self.response_bytes,
                  "http_status": self.http_status}
        return {key: value for key, value in values.items() if value is not None}


class AuditedStream(httpx.AsyncByteStream):
    def __init__(self, response, transport, manager, request_id, ctx, diagnostics):
        self.response, self.transport = response, transport
        self.manager, self.request_id, self.ctx = manager, request_id, ctx
        self.finished = False
        self.closed = False
        self.diagnostics = diagnostics

    def _finish(self, *, closed=False, error=None):
        if self.finished:
            return
        # aclose 与异步生成器回收都可能到达这里，只允许第一次结算。
        self.finished = True
        if error is not None:
            self.manager.network_error(
                self.request_id, self.ctx,
                cancelled=isinstance(error, asyncio.CancelledError),
                error=error, diagnostics=self.diagnostics.snapshot(),
            )
        elif closed:
            self.manager.stream_closed(
                self.request_id, self.response.status_code,
            )
        else:
            self.manager.result(
                self.request_id, self.ctx, self.response.status_code,
                self.response.headers, b"",
            )

    async def __aiter__(self):
        try:
            async for chunk in self.response.aiter_raw():
                self.diagnostics.response_bytes += len(chunk)
                yield chunk
            self._finish()
        except GeneratorExit:
            # SDK 可在协议终止事件后关闭迭代器，无需继续读取 HTTP EOF。
            self._finish(closed=True)
            raise
        except BaseException as exc:
            self._finish(error=exc)
            raise

    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        try:
            try:
                await self.response.aclose()
            except BaseException as exc:
                self.diagnostics.phase = "response_close"
                self._finish(error=exc)
                raise
            else:
                self._finish(closed=True)
        finally:
            await self.transport.aclose()


class ManagedAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, platform, account_id, transport_factory=None):
        self.platform, self.account_id = platform, account_id
        self.transport_factory = transport_factory or (lambda: httpx.AsyncHTTPTransport(retries=0))

    async def handle_async_request(self, request):
        manager = get_context().external_requests
        timeout = request.extensions.get("timeout", {}).get("read") or 60
        try:
            data = request.content
        except httpx.RequestNotRead:
            data = None
        ctx = request_context(str(request.url),dict(request.headers),method=request.method,data=data,timeout=timeout,
                              platform=self.platform,account_id=self.account_id,source="ai_provider",semantics="write")
        deadline = min(time.time()+timeout,ctx.deadline_at or float("inf"))
        request_id = manager.start(ctx,request.method)
        diagnostics = RequestDiagnostics(request, data)
        transport = None
        response = None
        stream_owned = False
        allowed = False
        try:
            while delay := manager.check(request_id,ctx,deadline):
                await asyncio.sleep(delay)
            allowed = True
            remaining = max(.001,deadline-time.time())
            request.extensions["timeout"] = {key:min(value,remaining) if value is not None else remaining for key,value in request.extensions.get("timeout",{}).items()}
            transport = self.transport_factory()
            diagnostics.phase = "await_response_headers"
            response = await transport.handle_async_request(request)
            diagnostics.http_status = response.status_code
            diagnostics.phase = "read_response_body"
            if "text/event-stream" in response.headers.get("content-type", "") and response.status_code < 400:
                wrapped = httpx.Response(response.status_code,headers=response.headers,extensions=response.extensions,
                    stream=AuditedStream(response,transport,manager,request_id,ctx,diagnostics))
                stream_owned = True
                return wrapped
            raw = await response.aread()
        except BaseException as exc:
            if allowed:
                if response is not None:
                    diagnostics.response_bytes = response.num_bytes_downloaded
                manager.network_error(request_id,ctx,cancelled=isinstance(exc,asyncio.CancelledError),
                                      error=exc,diagnostics=diagnostics.snapshot())
            elif isinstance(exc, asyncio.CancelledError):
                manager.store.finish(request_id, decision="cancelled", result={"code":"EXTERNAL_REQUEST_CANCELLED"})
            raise
        else:
            # HTTPX 已缓存解码后的正文。直接交还原响应，保留状态、头和请求关联；
            # 用解码正文配原 Content-Encoding 重建响应会再次解压并掩盖 HTTP 错误。
            # 响应审计不在网络异常捕获范围内，不能被重复结算成写入结果未知。
            manager.result(request_id,ctx,response.status_code,response.headers,raw)
            return response
        finally:
            if not stream_owned:
                for resource in (response, transport):
                    if resource is not None:
                        try:
                            await resource.aclose()
                        except Exception as exc:
                            # 清理失败不能覆盖已收到的响应或原始网络异常；不记录敏感原文。
                            _logger.warning("外部请求资源关闭失败：%s", type(exc).__name__)
