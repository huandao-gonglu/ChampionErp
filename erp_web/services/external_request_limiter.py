"""外部请求的进程内并发闸门、速率令牌桶与共享背压冷却（零 SQLite 写锁）。"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, replace
import random
from threading import Condition
import time
from typing import Callable, Literal

from erp_web.schemas.external_requests import ExternalRequestBlocked, RequestContext, RequestFailure

RATE_BACKOFF_STEPS = (2.0, 5.0, 15.0, 60.0)
TRANSIENT_COOLDOWN_INITIAL = 60.0
TRANSIENT_COOLDOWN_MAX = 600.0
INTERFACE_403_COOLDOWN = 60.0
OZON_403_ESCALATION_WINDOW = 600.0
OZON_403_ESCALATION_INTERFACES = 3
DEFAULT_CONSECUTIVE_FAILURE_LIMIT = 5
FAILURE_STREAK_WINDOW = 600.0


@dataclass(frozen=True)
class PlatformLimit:
    """保守代码默认值，按平台文档或公开资料留足余量，不替代官方合同条款。"""

    concurrency: int | None = None
    rps: float | None = None
    concurrency_by_quota_key: bool = False
    consecutive_failure_limit: int = DEFAULT_CONSECUTIVE_FAILURE_LIMIT


# Ozon 公开资料称单 Client-Id 总上限约 50 RPS；这里保守设 3 并发 / 15 RPS，为多系统共用留余量。
# Yandex 按官方限额规则对同 campaign/business 最多放行 4 并发。
# Mercado Libre 不猜测官方 RPS，仅保留保守并发 4。
PLATFORM_LIMITS: dict[str, PlatformLimit] = {
    "ozon": PlatformLimit(concurrency=3, rps=15.0),
    "yandex": PlatformLimit(concurrency=4, rps=None, concurrency_by_quota_key=True),
    "mercadolibre": PlatformLimit(concurrency=4, rps=None),
}


@dataclass
class _CooldownState:
    failure: RequestFailure
    until: float
    level: int
    created_at: float
    kind: Literal["rate_limit", "transient", "interface_403"]
    probe_id: str = ""
    probe_until: float = 0.0


@dataclass(frozen=True)
class LimiterLease:
    request_id: str
    concurrency_key: tuple[str, ...] | None
    probe_key: tuple[str, str, str, str] | None
    sent_at: float


class ExternalRequestLimiter:
    """进程内统一发送保险丝：并发、RPS、共享退避、短熔断、单轮预算与鉴权熔断。"""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] | None = None,
    ):
        self._clock = clock
        self._monotonic = monotonic
        self._jitter = jitter or (lambda: random.uniform(0.0, 0.25))
        self._cond = Condition()
        self._limits: dict[str, PlatformLimit] = dict(PLATFORM_LIMITS)
        self._inflight: dict[tuple[str, ...], int] = {}
        self._tokens: dict[tuple[str, str], tuple[float, float]] = {}
        self._rate_pause_until: dict[tuple[str, str], float] = {}
        self._cooldowns: dict[tuple[str, str, str, str], _CooldownState] = {}
        self._auth_blocks: dict[tuple[str, str, str, str], RequestFailure] = {}
        self._request_invalid: OrderedDict[tuple[str, str, str, str, str], tuple[float, RequestFailure]] = OrderedDict()
        self._operation_counts: OrderedDict[str, int] = OrderedDict()
        self._streaks: dict[tuple[str, str, str], deque[tuple[float, str, str]]] = {}
        self._cred_read_success: set[tuple[str, str, str, str]] = set()
        self._cred_last_success: dict[tuple[str, str, str], float] = {}
        self._cred_403_interfaces: dict[tuple[str, str, str], dict[str, float]] = {}

    def set_limit(
        self,
        platform: str,
        *,
        concurrency: int | None = None,
        rps: float | None = None,
        concurrency_by_quota_key: bool | None = None,
        consecutive_failure_limit: int | None = None,
    ) -> None:
        with self._cond:
            base = self._limits.get(platform, PlatformLimit())
            self._limits[platform] = PlatformLimit(
                concurrency=base.concurrency if concurrency is None else concurrency,
                rps=base.rps if rps is None else rps,
                concurrency_by_quota_key=base.concurrency_by_quota_key if concurrency_by_quota_key is None else concurrency_by_quota_key,
                consecutive_failure_limit=base.consecutive_failure_limit if consecutive_failure_limit is None else consecutive_failure_limit,
            )
            self._cond.notify_all()

    @staticmethod
    def _scope_key(ctx: RequestContext, scope: str) -> str:
        return {
            "account": "",
            "credential": ctx.credential_id,
            "interface": ctx.interface,
            "quota": ctx.quota_key,
            "operation": ctx.operation_id,
            "request": f"{ctx.operation_id}:{ctx.interface}:{ctx.fingerprint}",
        }.get(scope, "")

    def _concurrency_key(self, ctx: RequestContext, limit: PlatformLimit) -> tuple[str, ...] | None:
        if not limit.concurrency:
            return None
        if limit.concurrency_by_quota_key:
            return (ctx.platform, ctx.account_id, ctx.quota_key)
        return (ctx.platform, ctx.account_id)

    def _matching_scopes(self, ctx: RequestContext) -> tuple[tuple[str, str, str, str], ...]:
        return (
            (ctx.platform, ctx.account_id, "account", ""),
            (ctx.platform, ctx.account_id, "credential", ctx.credential_id),
            (ctx.platform, ctx.account_id, "quota", ctx.quota_key),
            (ctx.platform, ctx.account_id, "interface", ctx.interface),
        )

    def _wait(self, ctx: RequestContext, seconds: float, deadline: float) -> None:
        remaining = min(seconds, max(0.0, deadline - self._clock()))
        if remaining <= 0:
            return
        if ctx.cancel is not None or ctx.cancellation_check is not None:
            # 协作式取消需要快速响应外部 Event/callback。
            step = min(remaining, 0.05)
            self._cond.wait(timeout=step)
        else:
            self._cond.wait(timeout=remaining)

    def _check_cancel_and_deadline(self, ctx: RequestContext, deadline: float) -> None:
        if ctx.cancellation_check is not None:
            ctx.cancellation_check()
        if ctx.cancel is not None and ctx.cancel.is_set():
            raise ExternalRequestBlocked(RequestFailure("EXTERNAL_REQUEST_CANCELLED", "请求已取消，尚未发送"))
        if self._clock() >= deadline:
            raise ExternalRequestBlocked(RequestFailure("EXTERNAL_REQUEST_DEADLINE", "请求等待超过任务时间边界，尚未发送"))

    def _consume_token(self, ctx: RequestContext, limit: PlatformLimit) -> float:
        """返回需要等待的秒数；返回 0 表示已消耗一个令牌。"""
        now_wall = self._clock()
        bucket_key = (ctx.platform, ctx.account_id)
        pause_until = self._rate_pause_until.get(bucket_key, 0.0)
        if pause_until > now_wall:
            return pause_until - now_wall
        if not limit.rps or limit.rps <= 0:
            return 0.0
        now_mono = self._monotonic()
        capacity = max(1.0, float(limit.concurrency or 1))
        tokens, last = self._tokens.get(bucket_key, (capacity, now_mono))
        tokens = min(capacity, tokens + max(0.0, now_mono - last) * limit.rps)
        if tokens >= 1.0:
            self._tokens[bucket_key] = (tokens - 1.0, now_mono)
            return 0.0
        self._tokens[bucket_key] = (tokens, now_mono)
        return (1.0 - tokens) / limit.rps

    def acquire(
        self,
        request_id: str,
        ctx: RequestContext,
        deadline: float,
        *,
        persisted_auth_check: Callable[[RequestContext], RequestFailure | None] | None = None,
    ) -> LimiterLease:
        with self._cond:
            while True:
                self._check_cancel_and_deadline(ctx, deadline)
                now = self._clock()

                # 1. 同操作确定性参数错误保护（仅内存 1 小时有效）。
                inv_key = (ctx.platform, ctx.account_id, ctx.operation_id, ctx.interface, ctx.fingerprint)
                invalid = self._request_invalid.get(inv_key)
                if invalid is not None:
                    if now - invalid[0] <= 3600:
                        raise ExternalRequestBlocked(invalid[1])
                    self._request_invalid.pop(inv_key, None)

                # 2. 鉴权/账号熔断（授权测试允许穿透验证恢复）。
                if not ctx.bypass_auth_block:
                    for key in (
                        (ctx.platform, ctx.account_id, "account", ""),
                        (ctx.platform, ctx.account_id, "credential", ctx.credential_id),
                    ):
                        auth_failure = self._auth_blocks.get(key)
                        if auth_failure is not None:
                            raise ExternalRequestBlocked(auth_failure)
                    if persisted_auth_check is not None:
                        persisted = persisted_auth_check(ctx)
                        if persisted is not None:
                            self._auth_blocks[(ctx.platform, ctx.account_id, "account", "")] = persisted
                            raise ExternalRequestBlocked(persisted)

                # 3. 单轮外部请求硬上限（防止 AI 循环意外打爆平台）。
                if ctx.max_operation_requests is not None:
                    used = self._operation_counts.get(ctx.operation_id, 0)
                    if used >= ctx.max_operation_requests:
                        raise ExternalRequestBlocked(
                            RequestFailure(
                                "AI_EXTERNAL_REQUEST_BUDGET",
                                f"单轮 AI 外部请求次数已达上限（{ctx.max_operation_requests} 次），已停止继续调用",
                                "operation",
                            )
                        )

                # 4. 共享冷却 / 瞬时熔断检查（单飞探测防惊群）。
                probe_key: tuple[str, str, str, str] | None = None
                wait_seconds = 0.0
                for key in self._matching_scopes(ctx):
                    cd = self._cooldowns.get(key)
                    if cd is None:
                        continue
                    if cd.probe_id and cd.probe_until <= now:
                        # 探测超时/崩溃自动回收，进入下一级冷却。
                        cd.probe_id = ""
                        cd.probe_until = 0.0
                        if cd.kind == "transient":
                            step = min(TRANSIENT_COOLDOWN_MAX, TRANSIENT_COOLDOWN_INITIAL * (2 ** min(cd.level + 1, 4)))
                            cd.level = min(cd.level + 1, 4)
                            cd.until = now + step
                            cd.failure = replace(cd.failure, resume_at=cd.until)
                    if now < cd.until:
                        if cd.kind == "interface_403":
                            raise ExternalRequestBlocked(replace(cd.failure, resume_at=cd.until))
                        if cd.until < deadline:
                            wait_seconds = max(wait_seconds, cd.until - now)
                            continue
                        raise ExternalRequestBlocked(replace(cd.failure, resume_at=cd.until))
                    # 冷却已到期：
                    if cd.kind == "interface_403":
                        self._cooldowns.pop(key, None)
                        continue
                    if cd.kind == "transient" and ctx.semantics != "read":
                        raise ExternalRequestBlocked(replace(cd.failure, resume_at=max(cd.until, cd.probe_until)))
                    if cd.probe_id:
                        if cd.probe_until < deadline:
                            wait_seconds = max(wait_seconds, min(0.1, cd.probe_until - now))
                            continue
                        raise ExternalRequestBlocked(replace(cd.failure, resume_at=cd.probe_until))
                    probe_key = key

                if wait_seconds > 0:
                    self._wait(ctx, wait_seconds, deadline)
                    continue

                # 5. 并发闸门。
                limit = self._limits.get(ctx.platform, PlatformLimit())
                conc_key = self._concurrency_key(ctx, limit)
                if conc_key is not None and self._inflight.get(conc_key, 0) >= (limit.concurrency or 1):
                    self._wait(ctx, 0.05, deadline)
                    continue

                # 6. 速率令牌桶 + Ratelimit-Remaining 暂停。
                token_wait = self._consume_token(ctx, limit)
                if token_wait > 0:
                    if now + token_wait >= deadline:
                        raise ExternalRequestBlocked(RequestFailure("EXTERNAL_REQUEST_DEADLINE", "请求限速排队超过任务时间边界，尚未发送"))
                    self._wait(ctx, token_wait, deadline)
                    continue

                # 全部通过：占并发位、占单飞探测位、计 AI 预算。
                if conc_key is not None:
                    self._inflight[conc_key] = self._inflight.get(conc_key, 0) + 1
                if probe_key is not None:
                    cd = self._cooldowns[probe_key]
                    cd.probe_id = request_id
                    cd.probe_until = deadline
                if ctx.max_operation_requests is not None:
                    self._operation_counts[ctx.operation_id] = self._operation_counts.get(ctx.operation_id, 0) + 1
                    self._operation_counts.move_to_end(ctx.operation_id)
                    while len(self._operation_counts) > 2000:
                        self._operation_counts.popitem(last=False)
                return LimiterLease(request_id=request_id, concurrency_key=conc_key, probe_key=probe_key, sent_at=now)

    def release(self, lease: LimiterLease) -> None:
        with self._cond:
            if lease.concurrency_key is not None:
                count = self._inflight.get(lease.concurrency_key, 0)
                if count <= 1:
                    self._inflight.pop(lease.concurrency_key, None)
                else:
                    self._inflight[lease.concurrency_key] = count - 1
            if lease.probe_key is not None:
                cd = self._cooldowns.get(lease.probe_key)
                if cd is not None and cd.probe_id == lease.request_id:
                    # 若调用方未先 settle（如异常退出/取消），释放探测位防止死锁。
                    cd.probe_id = ""
                    cd.probe_until = 0.0
            self._cond.notify_all()

    def refund_operation_budget(self, ctx: RequestContext) -> None:
        """before_send/传输层确认未发送时退回单轮计数。"""
        if ctx.max_operation_requests is None:
            return
        with self._cond:
            used = self._operation_counts.get(ctx.operation_id, 0)
            if used <= 1:
                self._operation_counts.pop(ctx.operation_id, None)
            else:
                self._operation_counts[ctx.operation_id] = used - 1

    def record_success(
        self,
        ctx: RequestContext,
        request_id: str,
        *,
        rate_pause_until: float | None = None,
        quota_pause: RequestFailure | None = None,
    ) -> None:
        now = self._clock()
        with self._cond:
            # 成功响应解除所有匹配的冷却与故障序列。
            for key in self._matching_scopes(ctx):
                cd = self._cooldowns.get(key)
                if cd is not None and (cd.probe_id == request_id or now >= cd.until):
                    self._cooldowns.pop(key, None)
            self._streaks.pop((ctx.platform, ctx.account_id, ctx.interface), None)
            cred_key = (ctx.platform, ctx.account_id, ctx.credential_id)
            self._cred_last_success[cred_key] = now
            self._cred_403_interfaces.pop(cred_key, None)
            if ctx.semantics == "read" and ctx.credential_id:
                self._cred_read_success.add((ctx.platform, ctx.account_id, ctx.credential_id, ctx.interface))
            if ctx.bypass_auth_block:
                self._clear_auth_unlocked(ctx.platform, ctx.account_id, ctx.credential_id)
            if rate_pause_until is not None and rate_pause_until > now:
                bucket_key = (ctx.platform, ctx.account_id)
                self._rate_pause_until[bucket_key] = max(self._rate_pause_until.get(bucket_key, 0.0), rate_pause_until)
            if quota_pause is not None and quota_pause.scope:
                until = quota_pause.resume_at if quota_pause.resume_at and quota_pause.resume_at > now else now + RATE_BACKOFF_STEPS[0]
                key = (ctx.platform, ctx.account_id, quota_pause.scope, self._scope_key(ctx, quota_pause.scope))
                self._cooldowns[key] = _CooldownState(
                    failure=replace(quota_pause, resume_at=until),
                    until=until,
                    level=0,
                    created_at=now,
                    kind="rate_limit",
                )
            self._cond.notify_all()

    def _record_streak_unlocked(self, ctx: RequestContext, outcome: str, *, retryable: bool, now: float) -> None:
        streak_key = (ctx.platform, ctx.account_id, ctx.interface)
        rows = self._streaks.setdefault(streak_key, deque())
        rows.append((now, outcome, ctx.semantics))
        while rows and now - rows[0][0] > FAILURE_STREAK_WINDOW:
            rows.popleft()
        limit = self._limits.get(ctx.platform, PlatformLimit()).consecutive_failure_limit
        if len(rows) < limit:
            return
        recent = list(rows)[-limit:]
        if not all(item[1] in {"network_error", "server_error", "outcome_unknown"} for item in recent):
            return
        cd_key = (ctx.platform, ctx.account_id, "interface", ctx.interface)
        existing = self._cooldowns.get(cd_key)
        if existing is not None and existing.kind == "transient":
            return
        until = now + TRANSIENT_COOLDOWN_INITIAL
        self._cooldowns[cd_key] = _CooldownState(
            failure=RequestFailure(
                "EXTERNAL_TRANSIENT_FAILURE",
                "接口连续失败，已短暂暂停；冷却后将自动尝试恢复",
                "interface",
                resume_at=until,
                retryable=True,
            ),
            until=until,
            level=0,
            created_at=now,
            kind="transient",
        )

    def record_failure(
        self,
        ctx: RequestContext,
        request_id: str,
        failure: RequestFailure,
        *,
        sent_at: float,
        has_prior_cred_read_success: Callable[[], bool] | None = None,
    ) -> RequestFailure:
        now = self._clock()
        with self._cond:
            effective = failure
            # 1. 限流（429 / 420）：纯内存共享冷却，按轮次退避，绝不把同批在途响应多次升级。
            if failure.code.endswith("_RATE_LIMITED") or failure.code == "YANDEX_RESOURCE_LIMIT":
                scope = failure.scope or "account"
                key = (ctx.platform, ctx.account_id, scope, self._scope_key(ctx, scope))
                existing = self._cooldowns.get(key)
                if failure.resume_at is not None and failure.resume_at > now:
                    until = max(existing.until, failure.resume_at) if existing else failure.resume_at
                    level = existing.level if existing else 0
                    created_at = existing.created_at if (existing and sent_at < existing.created_at) else now
                elif existing is not None and now - existing.until <= 600:
                    if sent_at <= existing.created_at or (existing.probe_id != request_id and sent_at < existing.until):
                        # 并发在途的晚到 429：不重复升级，复用当前冷却。
                        level = existing.level
                        until = existing.until
                        created_at = existing.created_at
                    else:
                        level = min(existing.level + 1, len(RATE_BACKOFF_STEPS) - 1)
                        until = now + RATE_BACKOFF_STEPS[level] + max(0.0, self._jitter())
                        created_at = now
                else:
                    level = 0
                    until = now + RATE_BACKOFF_STEPS[0] + max(0.0, self._jitter())
                    created_at = now
                effective = replace(failure, scope=scope, resume_at=until)
                self._cooldowns[key] = _CooldownState(
                    failure=effective,
                    until=until,
                    level=level,
                    created_at=created_at,
                    kind="rate_limit",
                )
                self._cond.notify_all()
                return effective

            # 2. 明确账号停用 / 凭据失效：内存立即熔断。
            if failure.scope == "account":
                self._auth_blocks[(ctx.platform, ctx.account_id, "account", "")] = failure
                self._cond.notify_all()
                return failure
            if failure.scope == "credential":
                self._auth_blocks[(ctx.platform, ctx.account_id, "credential", ctx.credential_id)] = failure
                self._cond.notify_all()
                return failure

            # 3. 接口 403 / 权限不足：Ozon 按同凭据历史成功或跨多接口升级为账号停用；否则短时单接口防重撞。
            if failure.scope == "interface":
                escalated = False
                if ctx.platform == "ozon" and failure.status == 403:
                    cred_key = (ctx.platform, ctx.account_id, ctx.credential_id)
                    prior_read_ok = (
                        ctx.semantics == "read"
                        and bool(ctx.credential_id)
                        and (
                            (ctx.platform, ctx.account_id, ctx.credential_id, ctx.interface) in self._cred_read_success
                            or bool(has_prior_cred_read_success and has_prior_cred_read_success())
                        )
                    )
                    by_iface = self._cred_403_interfaces.setdefault(cred_key, {})
                    by_iface[ctx.interface] = now
                    for iface, ts in list(by_iface.items()):
                        if now - ts > OZON_403_ESCALATION_WINDOW:
                            by_iface.pop(iface, None)
                    recent_success = now - self._cred_last_success.get(cred_key, 0.0) <= OZON_403_ESCALATION_WINDOW
                    if prior_read_ok or (len(by_iface) >= OZON_403_ESCALATION_INTERFACES and not recent_success):
                        escalated = True
                if escalated:
                    effective = RequestFailure(
                        "OZON_ACCOUNT_DISABLED",
                        "Ozon 凭据或账号访问受限，已停止全部模块调用，请在授权配置核对并重新测试连接",
                        "account",
                        status=403,
                    )
                    self._auth_blocks[(ctx.platform, ctx.account_id, "account", "")] = effective
                    self._cond.notify_all()
                    return effective
                until = now + INTERFACE_403_COOLDOWN
                key = (ctx.platform, ctx.account_id, "interface", ctx.interface)
                effective = replace(failure, resume_at=until)
                self._cooldowns[key] = _CooldownState(
                    failure=effective,
                    until=until,
                    level=0,
                    created_at=now,
                    kind="interface_403",
                )
                self._cond.notify_all()
                return effective

            # 4. 请求级确定性业务拒绝（400 / 402 / 参数错误）：不触发接口熔断，只阻止同操作同参数重发，并中断 5xx 序列。
            if failure.scope == "request":
                self._streaks.pop((ctx.platform, ctx.account_id, ctx.interface), None)
                # 若此次请求本身是 5xx 探测，收到明确业务拒绝说明接口本身已通，解除瞬时冷却。
                iface_key = (ctx.platform, ctx.account_id, "interface", ctx.interface)
                cd = self._cooldowns.get(iface_key)
                if cd is not None and cd.kind == "transient" and cd.probe_id == request_id:
                    self._cooldowns.pop(iface_key, None)
                inv_key = (ctx.platform, ctx.account_id, ctx.operation_id, ctx.interface, ctx.fingerprint)
                self._request_invalid[inv_key] = (now, failure)
                self._request_invalid.move_to_end(inv_key)
                while len(self._request_invalid) > 2000:
                    self._request_invalid.popitem(last=False)
                self._cond.notify_all()
                return failure

            # 5. 5xx / 408 可重试服务故障：若是探测则升级冷却，否则累计连续失败。
            iface_key = (ctx.platform, ctx.account_id, "interface", ctx.interface)
            cd = self._cooldowns.get(iface_key)
            if cd is not None and cd.kind == "transient" and cd.probe_id == request_id:
                cd.level = min(cd.level + 1, 4)
                delay = min(TRANSIENT_COOLDOWN_MAX, TRANSIENT_COOLDOWN_INITIAL * (2 ** cd.level))
                cd.until = now + delay
                cd.probe_id = ""
                cd.probe_until = 0.0
                cd.failure = replace(cd.failure, resume_at=cd.until)
            elif failure.retryable:
                self._record_streak_unlocked(ctx, "server_error", retryable=True, now=now)
            self._cond.notify_all()
            return effective

    def record_network_error(self, ctx: RequestContext, request_id: str, *, cancelled: bool = False) -> None:
        now = self._clock()
        with self._cond:
            iface_key = (ctx.platform, ctx.account_id, "interface", ctx.interface)
            cd = self._cooldowns.get(iface_key)
            if cd is not None and cd.kind == "transient" and cd.probe_id == request_id:
                if cancelled:
                    cd.probe_id = ""
                    cd.probe_until = 0.0
                else:
                    cd.level = min(cd.level + 1, 4)
                    delay = min(TRANSIENT_COOLDOWN_MAX, TRANSIENT_COOLDOWN_INITIAL * (2 ** cd.level))
                    cd.until = now + delay
                    cd.probe_id = ""
                    cd.probe_until = 0.0
                    cd.failure = replace(cd.failure, resume_at=cd.until)
            elif not cancelled:
                outcome = "outcome_unknown" if ctx.semantics == "write" else "network_error"
                self._record_streak_unlocked(ctx, outcome, retryable=ctx.semantics == "read", now=now)
            self._cond.notify_all()

    def _clear_auth_unlocked(self, platform: str, account_id: str, credential_id: str | None = None) -> None:
        for key in list(self._auth_blocks):
            if key[0] != platform or key[1] != account_id:
                continue
            if key[2] == "account" or credential_id is None or key[3] == credential_id:
                self._auth_blocks.pop(key, None)
        for key in list(self._cooldowns):
            if key[0] == platform and key[1] == account_id and self._cooldowns[key].kind == "interface_403":
                self._cooldowns.pop(key, None)
        for key in list(self._cred_403_interfaces):
            if key[0] == platform and key[1] == account_id:
                self._cred_403_interfaces.pop(key, None)

    def clear_auth_block(self, platform: str, account_id: str, *, credential_id: str | None = None) -> None:
        with self._cond:
            self._clear_auth_unlocked(platform, account_id, credential_id)
            self._cond.notify_all()

    def matching_status(
        self,
        contexts: list[RequestContext],
        *,
        persisted_auth_check: Callable[[RequestContext], RequestFailure | None] | None = None,
    ) -> list[dict]:
        """返回给定业务上下文当前命中的内存冷却或鉴权阻断摘要（供订单同步状态展示）。"""
        now = self._clock()
        matches: list[dict] = []
        seen: set[tuple[str, str, str, str]] = set()
        with self._cond:
            for ctx in contexts:
                for key in (
                    (ctx.platform, ctx.account_id, "account", ""),
                    (ctx.platform, ctx.account_id, "credential", ctx.credential_id),
                ):
                    failure = self._auth_blocks.get(key)
                    if failure is None and key[2] == "account" and persisted_auth_check is not None:
                        failure = persisted_auth_check(ctx)
                    if failure is not None and key not in seen:
                        seen.add(key)
                        matches.append({"hard": True, "resume_at": None, "code": failure.code, "message": failure.message})
                for key in self._matching_scopes(ctx):
                    cd = self._cooldowns.get(key)
                    if cd is None or key in seen:
                        continue
                    effective_until = max(cd.until, cd.probe_until)
                    if effective_until > now:
                        seen.add(key)
                        matches.append({
                            "hard": False,
                            "resume_at": effective_until,
                            "code": cd.failure.code,
                            "message": cd.failure.message,
                        })
        return matches
