"""外部 API 的唯一发送控制；不编排业务或 Agent 生命周期。"""
from __future__ import annotations

from dataclasses import asdict
import io
import logging
import re
import time
import urllib.error
import urllib.request

from erp_web.context import get_context
from erp_web.services.platform_request_policy import classify_response, platform_error_codes, response_quota_pause
from erp_web.schemas.external_requests import ExternalRequestBlocked, ExternalRequestNotSent, ExternalRequestOutcomeUnknown, RequestFailure
from erp_web.services.external_request_context import request_context


class BufferedResponse(io.BytesIO):
    def __init__(self, raw, *, headers, status, url):
        super().__init__(raw)
        self.headers, self.status, self.url = headers, status, url
        self.code = status

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url


class ExternalRequestManager:
    def __init__(self, store):
        self.store = store

    def start(self, ctx, method, *, parent_id="", attempt=1):
        return self.store.begin(ctx,method,parent_id=parent_id,attempt=attempt)

    def check(self, request_id, ctx, deadline):
        if ctx.cancellation_check is not None:
            try:
                ctx.cancellation_check()
            except BaseException:
                self.store.finish(request_id,decision="cancelled",result={"code":"EXTERNAL_REQUEST_CANCELLED"})
                raise
        if ctx.cancel is not None and ctx.cancel.is_set():
            failure = RequestFailure("EXTERNAL_REQUEST_CANCELLED", "请求已取消，尚未发送")
            self.store.finish(request_id,decision="cancelled",result=asdict(failure))
            raise ExternalRequestBlocked(failure)
        if time.time() >= deadline:
            failure = RequestFailure("EXTERNAL_REQUEST_DEADLINE", "请求等待超过任务时间边界，尚未发送")
            self.store.finish(request_id,decision="cancelled",result=asdict(failure))
            raise ExternalRequestBlocked(failure)
        failure, delay = self.store.acquire(request_id,ctx,deadline)
        if failure:
            self.store.finish(request_id,decision="rejected",result=asdict(failure))
            raise ExternalRequestBlocked(failure)
        return delay

    def result(self, request_id, ctx, status, headers, raw):
        failure = classify_response(ctx.platform,status,headers,raw)
        pause = failure or response_quota_pause(ctx.platform,headers)
        if pause:
            if self.store.block(ctx,pause):
                logging.getLogger(__name__).warning("外部请求已阻断：平台=%s，账号=%s，范围=%s，原因=%s",ctx.platform,ctx.account_id,pause.scope,pause.code)
        # 不保存错误原文、URL 参数、请求体或任意响应字段。
        request_id_header = str(headers.get("X-Request-Id") or headers.get("Request-Id") or "")
        request_id_header = request_id_header if re.fullmatch(r"[a-zA-Z0-9_-]{1,100}",request_id_header) else ""
        self.store.finish(request_id,result={"http_status":status,"platform_request_id":request_id_header,"platform_error_codes":platform_error_codes(raw) if failure else [],
            "outcome":"failed" if failure else "success", "failure":asdict(failure) if failure else None,"next_request_pause":asdict(pause) if pause and not failure else None})
        if failure:
            self.store.record_failure_streak(ctx)
        return failure

    def stream_closed(self, request_id, status):
        """记录调用方主动关闭；传输层不推测模型完成、取消或业务写入结果。"""
        self.store.finish(request_id, result={
            "http_status": status, "outcome": "stream_closed",
        })

    def network_error(self, request_id, ctx, *, cancelled=False):
        self.store.finish(request_id,result={"outcome":"outcome_unknown" if ctx.semantics == "write" else "cancelled" if cancelled else "network_error", "cancelled":cancelled,
            "code":"EXTERNAL_NETWORK_INTERRUPTED"})
        if not cancelled:
            self.store.record_failure_streak(ctx)
        if ctx.semantics == "write":
            self.store.block(ctx, RequestFailure("EXTERNAL_WRITE_OUTCOME_UNKNOWN", "同一操作的写入结果未知，请先查询业务回执", "request"))

    def open(self, request, *, timeout=30, request_context_value=None, transport=None, source="external", **kwargs):
        if isinstance(request,str):
            request = urllib.request.Request(request)
        ctx = request_context_value or request_context(request.full_url,dict(request.header_items()),data=request.data,
                                         method=request.get_method(),timeout=timeout,source=source)
        deadline = min(time.time()+min(timeout,ctx.timeout),ctx.deadline_at or float("inf"))
        parent = ""
        for attempt in range(1, max(1,ctx.max_attempts if ctx.semantics == "read" else 1)+1):
            request_id = self.start(ctx,request.get_method(),parent_id=parent,attempt=attempt)
            parent = parent or request_id
            while delay := self.check(request_id,ctx,deadline):
                if ctx.cancel:
                    ctx.cancel.wait(delay)
                else:
                    time.sleep(delay)
            try:
                with (transport or urllib.request.urlopen)(request,timeout=max(.001,deadline-time.time()),**kwargs) as response:
                    raw = response.read(32 * 1024 * 1024 + 1)
                    if len(raw) > 32 * 1024 * 1024:
                        raise ValueError("外部响应超过 32 MiB 上限")
                    status = getattr(response,"status",None) or getattr(response,"code",None) or 200
                    headers = getattr(response,"headers",None)
                    if headers is None:
                        headers = {}
                    failure = self.result(request_id,ctx,status,headers,raw)
                    if failure and failure.scope and failure.scope != "request":
                        raise ExternalRequestBlocked(failure,sent=True)
                    return BufferedResponse(raw,headers=headers,status=status,url=request.full_url)
            except urllib.error.HTTPError as exc:
                try:
                    raw = exc.read(32 * 1024 * 1024)
                finally:
                    exc.close()
                failure = self.result(request_id,ctx,exc.code,exc.headers or {},raw)
                if ctx.semantics == "write" and (exc.code >= 500 or exc.code == 408):
                    self.network_error(request_id, ctx)
                    raise ExternalRequestOutcomeUnknown(http_status=exc.code) from exc
                if not (failure and failure.retryable and ctx.semantics == "read" and attempt < ctx.max_attempts and time.time()+ctx.retry_delay < deadline):
                    raise urllib.error.HTTPError(exc.url, exc.code, exc.msg, exc.headers, io.BytesIO(raw)) from exc
            except ExternalRequestNotSent as exc:
                self.store.finish(request_id, decision="rejected", not_sent=True,
                                  result={**asdict(exc.failure), "outcome": "not_sent"})
                raise
            except ExternalRequestBlocked:
                raise
            except BaseException as exc:
                self.network_error(request_id,ctx)
                if ctx.semantics == "write" and isinstance(exc, Exception):
                    raise ExternalRequestOutcomeUnknown() from exc
                raise
            # 默认不重试；显式只读预算内的服务端失败才会进入此分支。
            until = time.time()+ctx.retry_delay
            while time.time() < until:
                if ctx.cancel and ctx.cancel.wait(min(.05,until-time.time())):
                    break
                if not ctx.cancel:
                    time.sleep(min(.05,max(0,until-time.time())))
        raise AssertionError("请求预算已耗尽")


def managed_urlopen(request, *, timeout=30, request_context=None, transport=None, source="external", **kwargs):
    return get_context().external_requests.open(request,timeout=timeout,request_context_value=request_context,transport=transport,source=source,**kwargs)
