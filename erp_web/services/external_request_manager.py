"""外部 API 的唯一发送控制；纯内存并发/退避保险丝 + 纯观测审计。"""
from __future__ import annotations

from dataclasses import asdict
import http.client
import io
import logging
import re
import time
import urllib.error
import urllib.request

from erp_web.context import get_context
from erp_web.schemas.external_requests import (
    ExternalRequestBlocked,
    ExternalRequestNotSent,
    ExternalRequestOutcomeUnknown,
    RequestContext,
    RequestFailure,
)
from erp_web.services.external_request_context import request_context
from erp_web.services.external_request_limiter import ExternalRequestLimiter
from erp_web.services.platform_request_policy import (
    classify_response,
    platform_error_codes,
    response_quota_pause,
    response_rate_pause,
)


class BufferedResponse(io.BytesIO):
    def __init__(self, raw, *, headers, status, url):
        super().__init__(raw)
        self.headers, self.status, self.url = headers, status, url
        self.code = status

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """固定网关调用可在受控传输边界禁用 HTTP 重定向。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ExternalRequestManager:
    def __init__(
        self,
        store,
        *,
        limiter: ExternalRequestLimiter | None = None,
        on_auth_block=None,
        persisted_auth_check=None,
    ):
        self.store = store
        self.limiter = limiter or ExternalRequestLimiter()
        self._on_auth_block = on_auth_block
        self._persisted_auth_check = persisted_auth_check

    def start(self, ctx: RequestContext, method: str, *, parent_id="", attempt=1) -> str:
        return self.store.begin(ctx, method, parent_id=parent_id, attempt=attempt)

    def clear_auth_block(self, platform: str, account_id: str, *, credential_id: str | None = None) -> None:
        self.limiter.clear_auth_block(platform, account_id, credential_id=credential_id)

    def matching_status(self, contexts: list[RequestContext]) -> list[dict]:
        return self.limiter.matching_status(contexts, persisted_auth_check=self._persisted_auth_check)

    def result(self, request_id: str, ctx: RequestContext, status: int, headers, raw: bytes, *, method="", sent_at: float | None = None):
        failure = classify_response(ctx.platform, status, headers, raw, method=method, interface=ctx.interface)
        quota_pause = None if failure else response_quota_pause(ctx.platform, headers)
        rate_pause_until = None if failure else response_rate_pause(ctx.platform, headers)
        effective = failure
        if failure is not None:
            effective = self.limiter.record_failure(
                ctx,
                request_id,
                failure,
                sent_at=time.time() if sent_at is None else sent_at,
                has_prior_cred_read_success=lambda: self.store.has_credential_read_success(
                    ctx.platform, ctx.account_id, ctx.credential_id, ctx.interface
                ),
            )
            if effective.scope in {"account", "credential"}:
                logging.getLogger(__name__).warning(
                    "外部请求已熔断：平台=%s，账号=%s，范围=%s，原因=%s",
                    ctx.platform, ctx.account_id, effective.scope, effective.code,
                )
                if self._on_auth_block is not None:
                    try:
                        self._on_auth_block(ctx, effective)
                    except Exception:
                        logging.getLogger(__name__).exception("回写店铺授权异常状态失败")
        else:
            self.limiter.record_success(
                ctx,
                request_id,
                rate_pause_until=rate_pause_until,
                quota_pause=quota_pause,
            )
        # 不保存错误原文、URL 参数、请求体或任意响应字段。
        request_id_header = str(headers.get("X-Request-Id") or headers.get("Request-Id") or "")
        request_id_header = request_id_header if re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", request_id_header) else ""
        self.store.finish(
            request_id,
            result={
                "http_status": status,
                "platform_request_id": request_id_header,
                "platform_error_codes": platform_error_codes(raw, platform=ctx.platform) if effective else [],
                "outcome": "failed" if effective else "success",
                "failure": asdict(effective) if effective else None,
                "next_request_pause": asdict(quota_pause) if quota_pause else None,
            },
        )
        return effective

    def network_error(self, request_id: str, ctx: RequestContext, *, cancelled=False):
        result = {
            "outcome": "outcome_unknown" if ctx.semantics == "write" else "cancelled" if cancelled else "network_error",
            "cancelled": cancelled,
            "code": "EXTERNAL_NETWORK_INTERRUPTED",
        }
        self.store.finish(request_id, result=result)
        self.limiter.record_network_error(ctx, request_id, cancelled=cancelled)

    def _acquire_or_reject(self, request_id: str, ctx: RequestContext, deadline: float):
        if ctx.semantics == "write" and self.store.has_unknown_write(ctx):
            failure = RequestFailure("EXTERNAL_WRITE_OUTCOME_UNKNOWN", "同一操作的写入结果未知，请先查询业务回执", "request")
            self.store.finish(request_id, decision="rejected", result=asdict(failure))
            raise ExternalRequestBlocked(failure)
        try:
            return self.limiter.acquire(
                request_id,
                ctx,
                deadline,
                persisted_auth_check=self._persisted_auth_check,
            )
        except ExternalRequestBlocked as exc:
            cancelled = exc.code in {"EXTERNAL_REQUEST_CANCELLED", "EXTERNAL_REQUEST_DEADLINE"}
            self.store.finish(
                request_id,
                decision="cancelled" if cancelled else "rejected",
                result={"code": exc.code, "message": str(exc), **exc.details},
            )
            raise
        except BaseException:
            self.store.finish(request_id, decision="cancelled", result={"code": "EXTERNAL_REQUEST_CANCELLED"})
            raise

    def open(self, request, *, timeout=30, request_context_value=None, transport=None, source="external", follow_redirects=True, cookie_jar=None, **kwargs):
        if not follow_redirects or cookie_jar is not None:
            if transport is not None:
                raise ValueError("配置重定向或 Cookie 时不能覆盖受控传输")
            handlers = []
            if not follow_redirects:
                handlers.append(_NoRedirect())
            if cookie_jar is not None:
                handlers.append(urllib.request.HTTPCookieProcessor(cookie_jar))
            transport = urllib.request.build_opener(*handlers).open
        if isinstance(request, str):
            request = urllib.request.Request(request)
        ctx = request_context_value or request_context(
            request.full_url,
            dict(request.header_items()),
            data=request.data,
            method=request.get_method(),
            timeout=timeout,
            source=source,
        )
        attempt_timeout = min(timeout, ctx.timeout)
        attempts = max(1, ctx.max_attempts if ctx.semantics == "read" else 1)
        outer_deadline = ctx.deadline_at or float("inf")
        parent = ""
        for attempt in range(1, attempts + 1):
            attempt_deadline = min(time.time() + attempt_timeout, outer_deadline)
            request_id = self.start(ctx, request.get_method(), parent_id=parent, attempt=attempt)
            parent = parent or request_id
            lease = self._acquire_or_reject(request_id, ctx, attempt_deadline)
            retry_wait = ctx.retry_delay * attempt
            try:
                if ctx.before_send is not None:
                    try:
                        ctx.before_send(ctx)
                    except ExternalRequestBlocked as exc:
                        self.limiter.refund_operation_budget(ctx)
                        self.store.finish(request_id, decision="rejected", not_sent=True, result={"code": exc.code, "outcome": "not_sent"})
                        raise
                self.store.mark_sent(request_id, lease.sent_at)
                try:
                    with (transport or urllib.request.urlopen)(request, timeout=max(0.001, attempt_deadline - time.time()), **kwargs) as response:
                        raw = response.read(32 * 1024 * 1024 + 1)
                        if len(raw) > 32 * 1024 * 1024:
                            raise ValueError("外部响应超过 32 MiB 上限")
                        status = getattr(response, "status", None) or getattr(response, "code", None) or 200
                        headers = getattr(response, "headers", None)
                        if headers is None:
                            headers = {}
                        failure = self.result(request_id, ctx, status, headers, raw, method=request.get_method(), sent_at=lease.sent_at)
                        if failure and failure.scope and failure.scope != "request":
                            raise ExternalRequestBlocked(failure, sent=True)
                        return BufferedResponse(raw, headers=headers, status=status, url=request.full_url)
                except urllib.error.HTTPError as exc:
                    try:
                        raw = exc.read(32 * 1024 * 1024)
                    finally:
                        exc.close()
                    failure = self.result(request_id, ctx, exc.code, exc.headers or {}, raw, method=request.get_method(), sent_at=lease.sent_at)
                    if ctx.semantics == "write" and (exc.code >= 500 or exc.code == 408):
                        self.network_error(request_id, ctx)
                        raise ExternalRequestOutcomeUnknown(http_status=exc.code) from exc
                    if not (failure and failure.retryable and ctx.semantics == "read" and attempt < attempts and time.time() + retry_wait < outer_deadline):
                        rebuilt = urllib.error.HTTPError(exc.url, exc.code, exc.msg, exc.headers, io.BytesIO(raw))
                        if failure and failure.resume_at is not None:
                            rebuilt.external_resume_at = failure.resume_at
                        raise rebuilt from exc
                except ExternalRequestNotSent as exc:
                    self.limiter.refund_operation_budget(ctx)
                    self.store.finish(
                        request_id,
                        decision="rejected",
                        not_sent=True,
                        result={**asdict(exc.failure), "outcome": "not_sent"},
                    )
                    raise
                except ExternalRequestBlocked:
                    raise
                except BaseException as exc:
                    self.network_error(request_id, ctx)
                    if ctx.semantics == "write" and isinstance(exc, Exception):
                        raise ExternalRequestOutcomeUnknown() from exc
                    if not (isinstance(exc, (OSError, http.client.HTTPException)) and ctx.semantics == "read"
                            and attempt < attempts and time.time() + retry_wait < outer_deadline):
                        raise
            finally:
                self.limiter.release(lease)
            # 默认不重试；显式只读预算内的服务端或网络故障才会进入此分支，按退避等待并尊重取消。
            until = min(time.time() + retry_wait, outer_deadline)
            while time.time() < until:
                if ctx.cancellation_check is not None:
                    ctx.cancellation_check()
                if ctx.cancel and ctx.cancel.wait(min(0.05, until - time.time())):
                    break
                if not ctx.cancel:
                    time.sleep(min(0.05, max(0.0, until - time.time())))
        raise AssertionError("请求预算已耗尽")


def managed_urlopen(request, *, timeout=30, request_context=None, transport=None, source="external", **kwargs):
    return get_context().external_requests.open(
        request, timeout=timeout, request_context_value=request_context, transport=transport, source=source, **kwargs
    )
