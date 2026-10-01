"""SDK 原生 HTTPX Transport 注入；不额外执行模型或重放推理请求。"""
from __future__ import annotations

import asyncio
import time

import httpx2 as httpx

from erp_web.context import get_context
from erp_web.services.external_request_context import request_context


class AuditedStream(httpx.AsyncByteStream):
    def __init__(self, response, transport, manager, request_id, ctx):
        self.response, self.transport = response, transport
        self.manager, self.request_id, self.ctx = manager, request_id, ctx
        self.finished = False
        self.closed = False

    def _finish(self, *, closed=False, error=None):
        if self.finished:
            return
        # aclose 与异步生成器回收都可能到达这里，只允许第一次结算。
        self.finished = True
        if error is not None:
            self.manager.network_error(
                self.request_id, self.ctx,
                cancelled=isinstance(error, asyncio.CancelledError),
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
        transport = None
        allowed = False
        try:
            while delay := manager.check(request_id,ctx,deadline):
                await asyncio.sleep(delay)
            allowed = True
            remaining = max(.001,deadline-time.time())
            request.extensions["timeout"] = {key:min(value,remaining) if value is not None else remaining for key,value in request.extensions.get("timeout",{}).items()}
            transport = self.transport_factory()
            response = await transport.handle_async_request(request)
            if "text/event-stream" in response.headers.get("content-type", "") and response.status_code < 400:
                return httpx.Response(response.status_code,headers=response.headers,extensions=response.extensions,
                    stream=AuditedStream(response,transport,manager,request_id,ctx))
            raw = await response.aread()
            manager.result(request_id,ctx,response.status_code,response.headers,raw)
            await response.aclose()
            await transport.aclose()
            return httpx.Response(response.status_code,headers=response.headers,content=raw,extensions=response.extensions)
        except BaseException as exc:
            if allowed:
                manager.network_error(request_id,ctx,cancelled=isinstance(exc,asyncio.CancelledError))
            elif isinstance(exc, asyncio.CancelledError):
                manager.store.finish(request_id, decision="cancelled", result={"code":"EXTERNAL_REQUEST_CANCELLED"})
            if transport is not None:
                await transport.aclose()
            raise
