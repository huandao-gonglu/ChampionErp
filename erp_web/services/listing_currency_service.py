from __future__ import annotations

"""店铺发布币种纯领域服务（唯一事实源状态机）。

本模块是纯服务：不执行网络请求、不持久化、不按国家/站点/注册表推断币种，
也不出现平台特定分支。店铺授权配置（``store_auth.auth_detail_json``）中的
``listing_currency`` 是核价与发布的唯一币种来源。
"""

import hashlib
import time
from typing import Any, cast

from erp_web.schemas.currency import (
    CurrencyMode,
    CurrencyStatus,
    StoreListingCurrency,
    StoreListingCurrencyDiscovery,
)

# ---------------------------------------------------------------------------
# 错误码与异常
# ---------------------------------------------------------------------------

STORE_CURRENCY_UNRESOLVED = "STORE_CURRENCY_UNRESOLVED"
STORE_CURRENCY_SELECTION_REQUIRED = "STORE_CURRENCY_SELECTION_REQUIRED"
STORE_CURRENCY_MANUAL_REQUIRED = "STORE_CURRENCY_MANUAL_REQUIRED"
STORE_CURRENCY_REFRESH_FAILED = "STORE_CURRENCY_REFRESH_FAILED"
STORE_CURRENCY_CHANGED = "STORE_CURRENCY_CHANGED"
PRICING_STALE = "PRICING_STALE"

_STATUS_ERROR_CODES: dict[str, str] = {
    "unresolved": STORE_CURRENCY_UNRESOLVED,
    "selection_required": STORE_CURRENCY_SELECTION_REQUIRED,
    "manual_required": STORE_CURRENCY_MANUAL_REQUIRED,
    "refresh_failed": STORE_CURRENCY_REFRESH_FAILED,
}

_STATUS_MESSAGES: dict[str, str] = {
    "unresolved": "店铺发布币种未解析，请先测试授权并读取发布货币",
    "selection_required": "店铺发布币种待选择，请在授权页从允许币种列表中确认",
    "manual_required": "平台不提供币种查询能力，请在授权页人工填写 ISO 4217 币种代码",
    "refresh_failed": "店铺发布币种读取失败，请在授权页重新验证授权并读取币种",
}

CURRENCY_MODES: frozenset[str] = frozenset({"locked", "selectable", "manual", "unresolved"})
CURRENCY_STATUSES: frozenset[str] = frozenset(
    {"ready", "selection_required", "manual_required", "refresh_failed", "unresolved"}
)


class StoreCurrencyNotReadyError(RuntimeError):
    """店铺发布币种未就绪；核价与发布必须被确定性阻断。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CurrencySelectionError(ValueError):
    """人工选择/填写发布币种被状态机规则拒绝。"""


# ---------------------------------------------------------------------------
# ISO 4217 校验
# ---------------------------------------------------------------------------

_ISO4217_CODES = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND
    BOB BOV BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU
    CRC CUC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS
    GIP GMD GNF GTQ GYD HKD HNL HRK HTG HUF IDR ILS INR IQD IRR ISK JMD JOD
    JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL
    MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR
    NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG
    SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY
    TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAF XAG
    XAU XBA XBB XBC XBD XCD XDR XOF XPD XPF XPT XSU XUA XXX YER ZAR ZMW ZWG
    """.split()
)


def normalize_currency_code(value: Any) -> str:
    return str(value or "").strip().upper()


def is_iso4217_code(value: Any) -> bool:
    code = normalize_currency_code(value)
    return len(code) == 3 and code.isalpha() and code in _ISO4217_CODES


def utc_now_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# 指纹
# ---------------------------------------------------------------------------


def compute_currency_fingerprint(
    platform: str,
    store_identity: str,
    listing_currency: str,
    allowed_currencies: list[str] | None,
    currency_mode: str,
    currency_source: str,
) -> str:
    """币种配置指纹。

    由 平台 + 稳定店铺身份 + listing_currency + 排序后的 allowed_currencies +
    currency_mode + currency_source 规范化计算，不包含时间戳：同一配置重新
    验证不会让旧核价无故失效；店铺身份、币种、允许集或模式变化时指纹必变。
    """
    allowed = sorted(
        {
            normalize_currency_code(item)
            for item in (allowed_currencies or [])
            if normalize_currency_code(item)
        }
    )
    canonical = "\n".join(
        (
            str(platform or "").strip().lower(),
            str(store_identity or "").strip(),
            normalize_currency_code(listing_currency),
            ",".join(allowed),
            str(currency_mode or "").strip(),
            str(currency_source or "").strip(),
        )
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def store_identity_for_platform(platform: str, store_section: dict[str, Any] | None) -> str:
    """从店铺配置段提取稳定店铺身份（用于指纹与发布上下文）。"""
    store = store_section if isinstance(store_section, dict) else {}
    platform_key = str(platform or "").strip().lower()
    if platform_key == "ozon":
        return str(store.get("client_id") or "").strip()
    if platform_key == "yandex":
        return str(store.get("business_id") or store.get("campaign_id") or "").strip()
    if platform_key == "mercadolibre":
        return str(store.get("user_id") or store.get("seller_id") or "").strip()
    return ""


# ---------------------------------------------------------------------------
# 只读投影
# ---------------------------------------------------------------------------


def empty_store_listing_currency() -> StoreListingCurrency:
    return {
        "listing_currency": "",
        "allowed_currencies": [],
        "currency_mode": "unresolved",
        "currency_status": "unresolved",
        "currency_source": "",
        "currency_verified_at": "",
        "currency_fingerprint": compute_currency_fingerprint("", "", "", [], "unresolved", ""),
        "currency_error_code": "",
        "currency_error_message": "",
    }


def _natural_status_for_mode(mode: str, listing_currency: str) -> CurrencyStatus:
    if listing_currency:
        return "ready"
    if mode == "selectable":
        return "selection_required"
    if mode == "manual":
        return "manual_required"
    return "unresolved"


def store_listing_currency_from_auth(
    platform: str,
    store_identity: str,
    auth_detail: dict[str, Any] | None,
) -> StoreListingCurrency:
    """从店铺授权配置段构造只读 ``StoreListingCurrency``。

    只做规范化和不变量防御，不执行任何远端请求或持久化。
    """
    detail = auth_detail if isinstance(auth_detail, dict) else {}
    listing_currency = normalize_currency_code(detail.get("listing_currency"))
    allowed: list[str] = []
    raw_allowed = detail.get("allowed_currencies")
    if isinstance(raw_allowed, (list, tuple)):
        for item in raw_allowed:
            code = normalize_currency_code(item)
            if code and code not in allowed:
                allowed.append(code)

    mode_raw = str(detail.get("currency_mode") or "").strip()
    mode: CurrencyMode = cast(CurrencyMode, mode_raw if mode_raw in CURRENCY_MODES else "unresolved")
    status_raw = str(detail.get("currency_status") or "").strip()
    status: CurrencyStatus = cast(
        CurrencyStatus, status_raw if status_raw in CURRENCY_STATUSES else "unresolved"
    )

    if mode == "locked" and listing_currency:
        allowed = [listing_currency]
    if not listing_currency and status == "ready":
        status = _natural_status_for_mode(mode, "")

    source = str(detail.get("currency_source") or "").strip()
    return {
        "listing_currency": listing_currency,
        "allowed_currencies": allowed,
        "currency_mode": mode,
        "currency_status": status,
        "currency_source": source,
        "currency_verified_at": str(detail.get("currency_verified_at") or "").strip(),
        "currency_fingerprint": compute_currency_fingerprint(
            platform, store_identity, listing_currency, allowed, mode, source
        ),
        "currency_error_code": str(detail.get("currency_error_code") or "").strip(),
        "currency_error_message": str(detail.get("currency_error_message") or "").strip(),
    }


def store_listing_currency_ready(state: StoreListingCurrency) -> bool:
    return state["currency_status"] == "ready" and bool(state["listing_currency"])


def require_store_listing_currency(
    platform: str,
    store_section: dict[str, Any] | None,
) -> StoreListingCurrency:
    """核价/发布共用的就绪检查；未就绪时抛出带错误码的确定性异常。"""
    identity = store_identity_for_platform(platform, store_section)
    state = store_listing_currency_from_auth(platform, identity, store_section)
    if store_listing_currency_ready(state):
        return state
    status = state["currency_status"]
    code = _STATUS_ERROR_CODES.get(status, STORE_CURRENCY_UNRESOLVED)
    message = _STATUS_MESSAGES.get(status, _STATUS_MESSAGES["unresolved"])
    if status == "refresh_failed" and state["currency_error_message"]:
        message = f"{message}（{state['currency_error_message']}）"
    raise StoreCurrencyNotReadyError(code, message)


# ---------------------------------------------------------------------------
# 发现状态机
# ---------------------------------------------------------------------------


def _state(
    platform: str,
    store_identity: str,
    *,
    listing_currency: str,
    allowed_currencies: list[str],
    currency_mode: CurrencyMode,
    currency_status: CurrencyStatus,
    currency_source: str,
    currency_verified_at: str,
    currency_error_code: str = "",
    currency_error_message: str = "",
) -> StoreListingCurrency:
    return {
        "listing_currency": listing_currency,
        "allowed_currencies": allowed_currencies,
        "currency_mode": currency_mode,
        "currency_status": currency_status,
        "currency_source": currency_source,
        "currency_verified_at": currency_verified_at,
        "currency_fingerprint": compute_currency_fingerprint(
            platform,
            store_identity,
            listing_currency,
            allowed_currencies,
            currency_mode,
            currency_source,
        ),
        "currency_error_code": currency_error_code,
        "currency_error_message": currency_error_message,
    }


def apply_currency_discovery(
    platform: str,
    store_identity: str,
    discovery: StoreListingCurrencyDiscovery | dict[str, Any] | None,
    previous: StoreListingCurrency | None = None,
) -> StoreListingCurrency:
    """把平台 tester 的远端发现结果归一化为店铺币种状态。

    状态机（见迁移方案 §6）：

    - 远端返回 1 个币种 → ``locked`` + ``ready``；
    - 远端返回多个币种 → ``selectable``；旧选择仍在允许集则保留，否则清空；
    - 平台明确不支持店铺币种查询 → ``manual`` + ``manual_required``；
    - 平台声明支持但请求失败或响应无效 → ``refresh_failed``（保留上次展示值，
      不转 manual、不使用默认值）。
    """
    prev = previous or empty_store_listing_currency()
    result = discovery if isinstance(discovery, dict) else {}
    source = str(result.get("source") or "").strip()
    error_code = str(result.get("error_code") or "").strip()
    error_message = str(result.get("error_message") or "").strip()

    currencies: list[str] = []
    raw_currencies = result.get("currencies")
    if isinstance(raw_currencies, (list, tuple)):
        for item in raw_currencies:
            code = normalize_currency_code(item)
            if code and code not in currencies:
                currencies.append(code)

    if error_code or error_message:
        # 平台声明支持，但本次请求失败或响应无效：不转 manual，不使用默认值。
        return _state(
            platform,
            store_identity,
            listing_currency=prev["listing_currency"],
            allowed_currencies=list(prev["allowed_currencies"]),
            currency_mode=prev["currency_mode"],
            currency_status="refresh_failed",
            currency_source=prev["currency_source"],
            currency_verified_at=prev["currency_verified_at"],
            currency_error_code=error_code or "CURRENCY_DISCOVERY_FAILED",
            currency_error_message=error_message,
        )

    if len(currencies) == 1:
        return _state(
            platform,
            store_identity,
            listing_currency=currencies[0],
            allowed_currencies=[currencies[0]],
            currency_mode="locked",
            currency_status="ready",
            currency_source=source,
            currency_verified_at=utc_now_timestamp(),
        )

    if len(currencies) > 1:
        selection = prev["listing_currency"] if prev["listing_currency"] in currencies else ""
        return _state(
            platform,
            store_identity,
            listing_currency=selection,
            allowed_currencies=currencies,
            currency_mode="selectable",
            currency_status="ready" if selection else "selection_required",
            currency_source=source,
            currency_verified_at=utc_now_timestamp(),
        )

    if not bool(result.get("supported", True)):
        # 平台/店铺明确不提供店铺级币种查询：保持空值，等待人工配置。
        return _state(
            platform,
            store_identity,
            listing_currency="",
            allowed_currencies=[],
            currency_mode="manual",
            currency_status="manual_required",
            currency_source="",
            currency_verified_at=utc_now_timestamp(),
        )

    # 声明支持但远端未返回任何币种：按响应无效处理。
    return _state(
        platform,
        store_identity,
        listing_currency=prev["listing_currency"],
        allowed_currencies=list(prev["allowed_currencies"]),
        currency_mode=prev["currency_mode"],
        currency_status="refresh_failed",
        currency_source=prev["currency_source"],
        currency_verified_at=prev["currency_verified_at"],
        currency_error_code="CURRENCY_DISCOVERY_EMPTY",
        currency_error_message="远端未返回可用的发布币种",
    )


# ---------------------------------------------------------------------------
# 人工选择 / 手工填写
# ---------------------------------------------------------------------------


def apply_currency_selection(
    platform: str,
    store_identity: str,
    current: StoreListingCurrency,
    listing_currency: str,
) -> StoreListingCurrency:
    """受控人工选择接口背后的状态机规则（见迁移方案 §9.2）。"""
    mode = current["currency_mode"]
    if current["currency_status"] == "refresh_failed":
        raise CurrencySelectionError("发布币种读取失败，请先重新验证授权并读取币种")
    if mode == "unresolved":
        raise CurrencySelectionError("请先完成店铺授权测试，再配置发布币种")
    if mode == "locked":
        raise CurrencySelectionError("发布币种由平台账户锁定，不允许修改")

    code = normalize_currency_code(listing_currency)
    if not code:
        raise CurrencySelectionError("发布币种不能为空")

    if mode == "selectable":
        if code not in current["allowed_currencies"]:
            raise CurrencySelectionError(
                "发布币种必须属于平台允许集：" + ", ".join(current["allowed_currencies"])
            )
        return _state(
            platform,
            store_identity,
            listing_currency=code,
            allowed_currencies=list(current["allowed_currencies"]),
            currency_mode="selectable",
            currency_status="ready",
            currency_source=current["currency_source"],
            currency_verified_at=utc_now_timestamp(),
        )

    # mode == "manual"
    if not is_iso4217_code(code):
        raise CurrencySelectionError("发布币种必须是有效的 ISO 4217 三位字母代码")
    return _state(
        platform,
        store_identity,
        listing_currency=code,
        allowed_currencies=[],
        currency_mode="manual",
        currency_status="ready",
        currency_source="manual",
        currency_verified_at=utc_now_timestamp(),
    )


def reset_currency_state(platform: str, store_identity: str) -> StoreListingCurrency:
    """授权失败或身份变化时清除旧币种可信状态。"""
    return _state(
        platform,
        store_identity,
        listing_currency="",
        allowed_currencies=[],
        currency_mode="unresolved",
        currency_status="unresolved",
        currency_source="",
        currency_verified_at="",
    )


# 持久化到 store_auth.auth_detail_json 的币种状态字段（ConfigStore allowlist
# 与写入方共同维护该清单）。
CURRENCY_STATE_FIELDS: tuple[str, ...] = (
    "listing_currency",
    "allowed_currencies",
    "currency_mode",
    "currency_status",
    "currency_source",
    "currency_verified_at",
    "currency_fingerprint",
    "currency_error_code",
    "currency_error_message",
)


def write_currency_state(
    store_section: dict[str, Any],
    state: StoreListingCurrency,
) -> None:
    """把状态机结果写入内存店铺配置段（持久化由 ConfigStore 统一完成）。"""
    if not isinstance(store_section, dict):
        return
    store_section["listing_currency"] = state["listing_currency"]
    store_section["allowed_currencies"] = list(state["allowed_currencies"])
    store_section["currency_mode"] = state["currency_mode"]
    store_section["currency_status"] = state["currency_status"]
    store_section["currency_source"] = state["currency_source"]
    store_section["currency_verified_at"] = state["currency_verified_at"]
    store_section["currency_fingerprint"] = state["currency_fingerprint"]
    store_section["currency_error_code"] = state["currency_error_code"]
    store_section["currency_error_message"] = state["currency_error_message"]


def reset_currency_state_in_store(
    platform: str,
    store_section: dict[str, Any],
) -> None:
    """授权失败/清除授权时把币种状态重置为 unresolved。"""
    if not isinstance(store_section, dict):
        return
    identity = store_identity_for_platform(platform, store_section)
    write_currency_state(store_section, reset_currency_state(platform, identity))


def public_currency_configuration(state: StoreListingCurrency) -> dict[str, Any]:
    """授权测试/人工选择响应中的公开 currencyConfiguration。"""
    return {
        "listing_currency": state["listing_currency"],
        "allowed_currencies": list(state["allowed_currencies"]),
        "currency_mode": state["currency_mode"],
        "currency_status": state["currency_status"],
        "currency_source": state["currency_source"],
        "currency_verified_at": state["currency_verified_at"],
        "currency_error_code": state["currency_error_code"],
        "currency_error_message": state["currency_error_message"],
    }


__all__ = [
    "CURRENCY_STATE_FIELDS",
    "PRICING_STALE",
    "STORE_CURRENCY_CHANGED",
    "STORE_CURRENCY_MANUAL_REQUIRED",
    "STORE_CURRENCY_REFRESH_FAILED",
    "STORE_CURRENCY_SELECTION_REQUIRED",
    "STORE_CURRENCY_UNRESOLVED",
    "CurrencySelectionError",
    "StoreCurrencyNotReadyError",
    "apply_currency_discovery",
    "apply_currency_selection",
    "compute_currency_fingerprint",
    "empty_store_listing_currency",
    "is_iso4217_code",
    "normalize_currency_code",
    "public_currency_configuration",
    "require_store_listing_currency",
    "reset_currency_state",
    "reset_currency_state_in_store",
    "store_identity_for_platform",
    "store_listing_currency_from_auth",
    "store_listing_currency_ready",
    "utc_now_timestamp",
    "write_currency_state",
]
