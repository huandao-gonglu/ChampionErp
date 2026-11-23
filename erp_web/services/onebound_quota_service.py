"""万邦 1688 额度业务规则：接口分级、api_info 解析、GMV 入账与视图组装。"""
from __future__ import annotations

from decimal import Decimal, DecimalException
import re
from threading import Lock
import time

from erp_web.schemas.onebound_quota import (
    DEFAULT_INITIAL_GRANT,
    OneBoundQuotaView,
    SHOP_BIND_THROTTLE_SECONDS,
)
from erp_web.services.external_request_context import credential_fingerprint
from erp_web.stores.onebound_quota_store import OneBoundQuotaStore, next_cst_midnight

# 未列出的万邦接口默认归为 collect（宁可多拦，不要漏拦）。
CATEGORY: dict[str, str] = {
    "alibaba.createOrder.preview": "trade",
    "alibaba.trade.fastCreateOrder": "trade",
    "alibaba.trade.pay.protocolPay.preparePay": "trade",
    "alibaba.trade.pay.protocolPay.isopen": "trade",
    "alibaba.trade.get.buyerView": "trade",
    "alibaba.trade.getBuyerOrderList": "trade",
    "alibaba.trade.getLogisticsInfos.buyerView": "trade",
    "alibaba.trade.getLogisticsTraceInfo.buyerView": "trade",
    "alibaba.trade.receiveAddress.get": "trade",
    "alibaba.alipay.url.get": "trade",
    "buyer_token_create": "auth",
    "1688global/buyer_token_create": "auth",
    "alibaba.account.basic": "auth",
    "shop_bind_list": "auth",
    "sys/shop_bind_list": "auth",
}

_TODAY_RE = re.compile(r"\btoday\s*:\s*(\d{1,12})\b")
_MAX_RE = re.compile(r"\bmax\s*:\s*(\d{1,12})\b")
_EXPIRES_RE = re.compile(r"\bexpires\s*:\s*(\d{4}-\d{2}-\d{2})\b")
_ALL_RE = re.compile(r"\ball\[([^\]\r\n]{0,194})\]")

_SHOP_BIND_LOCK = Lock()
_SHOP_BIND_LAST_SYNC: dict[tuple[str, str, str], float] = {}


def key_fingerprint(api_key: str) -> str:
    value = str(api_key or "").strip()
    return credential_fingerprint(value) if value else ""


def api_category(interface_or_api: str) -> str:
    name = str(interface_or_api or "").strip()
    if name in CATEGORY:
        return CATEGORY[name]
    leaf = name.rsplit("/", 1)[-1]
    return CATEGORY.get(leaf, "collect")


def parse_api_info(raw: object) -> dict | None:
    """只按白名单解析 today/max/expires，不透传可能含凭据的未知文本。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw[:1024]
    today_match = _TODAY_RE.search(text)
    max_match = _MAX_RE.search(text)
    expires_match = _EXPIRES_RE.search(text)
    today = int(today_match.group(1)) if today_match else None
    daily_max = int(max_match.group(1)) if max_match else None
    expires = expires_match.group(1) if expires_match else ""
    if expires:
        # 校验合法日历日期，拒绝 2099-99-99 等无效格式。
        from datetime import date
        try:
            date.fromisoformat(expires)
        except ValueError:
            expires = ""
    if today is None and daily_max is None and not expires:
        return None
    all_match = _ALL_RE.search(text)
    raw_all = f"all[{all_match.group(1)}]"[:200] if all_match else ""
    return {
        "today": today,
        "max": daily_max,
        "expires": expires,
        "raw_all": raw_all,
    }


def observe_api_info(
    store: OneBoundQuotaStore,
    api_key: str,
    raw_api_info: object,
    *,
    request_id: str = "",
    now: float | None = None,
) -> bool:
    fp = key_fingerprint(api_key)
    parsed = parse_api_info(raw_api_info)
    if not fp or parsed is None:
        return False
    store.observe_snapshot(
        fp,
        today=parsed["today"],
        daily_max=parsed["max"],
        expires=parsed["expires"],
        raw_all=parsed["raw_all"],
        request_id=request_id,
        now=now,
    )
    return True


def admission_hook(
    store: OneBoundQuotaStore,
    api_key: str,
    member_id: str,
    interface_or_api: str,
    *,
    initial_grant: int = DEFAULT_INITIAL_GRANT,
    clock=time.time,
):
    """生成用于 RequestContext.before_send 的先扣后发准入钩子。"""
    fp = key_fingerprint(api_key)
    category = api_category(interface_or_api)
    buyer = str(member_id or "").strip()

    def check(_ctx) -> None:
        if not fp:
            return
        store.admit_and_spend(
            fp,
            buyer,
            category,
            initial_grant=initial_grant,
            now=clock(),
        )

    return check


def disable_key(
    store: OneBoundQuotaStore,
    api_key: str,
    *,
    code: str,
    message: str,
    now: float | None = None,
) -> None:
    fp = key_fingerprint(api_key)
    if fp:
        store.disable_key(fp, code=code, message=message, now=now)


def mark_daily_exhausted(
    store: OneBoundQuotaStore,
    api_key: str,
    *,
    resume_at: float | None = None,
    now: float | None = None,
) -> None:
    fp = key_fingerprint(api_key)
    if not fp:
        return
    current = time.time() if now is None else now
    until = float(resume_at) if resume_at and float(resume_at) > current else next_cst_midnight(current)
    store.mark_daily_exhausted(fp, until=until)


def restore_key(
    store: OneBoundQuotaStore,
    api_key: str,
    *,
    clear_auth_block=None,
) -> bool:
    fp = key_fingerprint(api_key)
    if not fp:
        return False
    restored = store.restore_key(fp)
    if callable(clear_auth_block):
        clear_auth_block("onebound", "key:" + fp)
    return restored


def ensure_buyer_grant(
    store: OneBoundQuotaStore,
    api_key: str,
    member_id: str,
    *,
    initial_grant: int = DEFAULT_INITIAL_GRANT,
    now: float | None = None,
) -> bool:
    fp = key_fingerprint(api_key)
    buyer = str(member_id or "").strip()
    if not fp or not buyer:
        return False
    return store.ensure_initial_grant(fp, buyer, initial_grant=initial_grant, now=now)


def set_buyer_target_balance(
    store: OneBoundQuotaStore,
    api_key: str,
    member_id: str,
    target_balance: int,
    *,
    expected_balance: int | None = None,
    note: str = "",
    initial_grant: int = DEFAULT_INITIAL_GRANT,
    now: float | None = None,
) -> dict:
    fp = key_fingerprint(api_key)
    buyer = str(member_id or "").strip()
    if not fp or not buyer:
        raise ValueError("万邦企业配置或 1688 买家身份缺失")
    return store.set_target_balance(
        fp,
        buyer,
        target_balance,
        expected_balance=expected_balance,
        note=note,
        initial_grant=initial_grant,
        now=now,
    )


def credit_paid_order(
    store: OneBoundQuotaStore,
    config: dict,
    order_number: str,
    total_fen: int | Decimal | None,
    *,
    initial_grant: int = DEFAULT_INITIAL_GRANT,
    now: float | None = None,
) -> int:
    """仅对万邦已付款订单按 floor(实付元) × 2 入账，同一订单号去重。"""
    if not isinstance(config, dict) or config.get("application_type") != "onebound":
        return 0
    fp = key_fingerprint(str(config.get("api_key") or ""))
    member_id = str(config.get("member_id") or "").strip()
    order_ref = str(order_number or "").strip()
    if not fp or not member_id or not order_ref or total_fen is None:
        return 0
    try:
        fen = Decimal(str(total_fen))
    except (TypeError, ValueError, DecimalException):
        return 0
    if not fen.is_finite() or fen < 100:
        return 0
    yuan = int(fen // 100)
    earned = yuan * 2
    if earned <= 0:
        return 0
    grant_value = config.get("initial_grant", initial_grant)
    try:
        grant_int = max(0, int(grant_value))
    except (TypeError, ValueError):
        grant_int = DEFAULT_INITIAL_GRANT
    inserted = store.credit_earn(
        fp,
        member_id,
        order_ref,
        earned,
        initial_grant=grant_int,
        now=now,
    )
    return earned if inserted else 0


def check_shop_bind_throttle(
    store: OneBoundQuotaStore,
    api_key: str,
    member_id: str,
    *,
    now: float | None = None,
    window_seconds: float = SHOP_BIND_THROTTLE_SECONDS,
) -> bool:
    """同一企业 Key 下的同一买家 10 分钟内最多触发一次 shop_bind_list 同步。"""
    fp = key_fingerprint(api_key)
    buyer = str(member_id or "").strip()
    if not fp or not buyer:
        return True
    current = time.monotonic() if now is None else float(now)
    bucket = (str(store.path), fp, buyer)
    with _SHOP_BIND_LOCK:
        previous = _SHOP_BIND_LAST_SYNC.get(bucket)
        if previous is not None and current - previous < window_seconds:
            return False
        _SHOP_BIND_LAST_SYNC[bucket] = current
        if len(_SHOP_BIND_LAST_SYNC) > 2048:
            cutoff = current - window_seconds
            for key in [k for k, ts in _SHOP_BIND_LAST_SYNC.items() if ts < cutoff]:
                _SHOP_BIND_LAST_SYNC.pop(key, None)
        return True


def build_quota_view(
    store: OneBoundQuotaStore,
    *,
    is_admin: bool,
    active_provider: str,
    application: dict,
    authorized: bool,
    member_id: str,
    account_name: str,
    now: float | None = None,
) -> OneBoundQuotaView:
    api_key = str(application.get("api_key") or "").strip()
    configured = bool(api_key and application.get("api_secret"))
    fp = key_fingerprint(api_key) if configured else ""
    try:
        initial_grant = max(0, int(application.get("initial_grant", DEFAULT_INITIAL_GRANT)))
    except (TypeError, ValueError):
        initial_grant = DEFAULT_INITIAL_GRANT
    member = str(member_id or "").strip() if authorized else ""
    buyer = (
        store.read_buyer_summary(fp, member, account_name=account_name, initial_grant=initial_grant)
        if configured and fp and member
        else None
    )
    if is_admin:
        buyers = (
            store.read_all_buyers(
                fp,
                current_member_id=member,
                current_account_name=account_name,
                initial_grant=initial_grant,
            )
            if configured and fp
            else []
        )
        key_status = store.read_key_status(fp, configured=configured, initial_grant=initial_grant, now=now)
        entries = store.read_entries(fp) if configured and fp else []
    else:
        buyers = [buyer] if buyer is not None else []
        key_status = None
        entries = store.read_entries(fp, member_id=member) if configured and fp and member else []
    return {
        "ok": True,
        "configured": configured,
        "is_admin": is_admin,
        "active_provider": active_provider,
        "authorized": bool(authorized and member),
        "buyer": buyer,
        "buyers": buyers,
        "key": key_status,
        "entries": entries,
    }


__all__ = [
    "CATEGORY",
    "admission_hook",
    "api_category",
    "build_quota_view",
    "check_shop_bind_throttle",
    "credit_paid_order",
    "disable_key",
    "ensure_buyer_grant",
    "key_fingerprint",
    "mark_daily_exhausted",
    "observe_api_info",
    "parse_api_info",
    "restore_key",
    "set_buyer_target_balance",
]
