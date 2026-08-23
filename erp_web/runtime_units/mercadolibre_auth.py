from __future__ import annotations

"""Mercado Libre 授权身份与发布币种发现的统一服务。

``/users/me`` 身份同步、远端站点币种元数据读取与币种状态写入收敛在这里；
授权测试、token 刷新与发布前检查不再各自复制用户信息更新逻辑。静态
``site_id`` 只是默认目标站点设置，不参与账户身份或币种推断。
"""

from typing import Any

from erp_web import marketplaces as publisher
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.services.listing_currency_service import (
    apply_currency_discovery,
    store_identity_for_platform,
    store_listing_currency_from_auth,
    write_currency_state,
)


def sync_mercadolibre_identity(
    store: dict[str, Any],
    token: str,
) -> dict[str, str]:
    """``/users/me`` → user_id / nickname / account_site_id（只写内存段）。"""

    profile = publisher.fetch_mercadolibre_user_profile(token)
    user_id = str(profile.get("user_id") or "").strip()
    nickname = str(profile.get("nickname") or "").strip()
    account_site_id = str(profile.get("site_id") or "").strip()
    if user_id:
        store["user_id"] = user_id
    if nickname:
        store["nickname"] = nickname
    store["shop_name"] = nickname or user_id or str(store.get("shop_name") or "")
    if account_site_id:
        store["account_site_id"] = account_site_id
    return profile


def _mercadolibre_site_currencies(site_data: dict[str, Any]) -> list[str]:
    """从站点元数据提取店铺级可选发布币种（保持远端顺序、去重、大写）。"""

    currencies: list[str] = []

    def add(value: Any) -> None:
        code = str(value or "").strip().upper()
        if len(code) == 3 and code.isalpha() and code not in currencies:
            currencies.append(code)

    raw_list = site_data.get("currencies")
    if isinstance(raw_list, list):
        for item in raw_list:
            add(item.get("id") if isinstance(item, dict) else item)
    add(site_data.get("default_currency_id"))
    currency = site_data.get("currency")
    if isinstance(currency, dict):
        add(currency.get("id"))
    else:
        add(currency)
    return currencies


def discover_mercadolibre_listing_currency(
    store: dict[str, Any],
) -> dict[str, Any]:
    """店铺级发布币种发现：只读远端站点元数据。

    - ``account_site_id`` 来自 ``/users/me``；缺失时不推断，直接进入人工配置；
    - 站点元数据返回币种 → 单值锁定 / 多值待选；
    - 站点不存在（404）或无币种字段（如 CBT/Global Selling）→ 该店铺没有
      店铺级可查询能力 → 人工配置；
    - 其他请求失败 → ``refresh_failed``，绝不回退本地站点注册表。
    """

    site_id = str(store.get("account_site_id") or "").strip()
    if not site_id:
        return {"supported": False}
    try:
        site_data = publisher.fetch_mercadolibre_site_listing(site_id)
    except PublishAdapterError as exc:
        if str(getattr(exc, "code", "") or "").endswith("NOT_FOUND"):
            return {"supported": False}
        return {
            "supported": True,
            "error_code": str(getattr(exc, "code", "") or "MERCADOLIBRE_SITE_FAILED"),
            "error_message": str(exc),
        }
    except Exception as exc:
        return {
            "supported": True,
            "error_code": "MERCADOLIBRE_SITE_FAILED",
            "error_message": str(exc),
        }
    currencies = _mercadolibre_site_currencies(site_data)
    if not currencies:
        return {"supported": False}
    return {"supported": True, "currencies": currencies, "source": "site_api"}


def sync_mercadolibre_auth_and_currency(
    config: dict[str, Any],
) -> dict[str, Any]:
    """token 换取/刷新成功后的统一收尾：用户信息 + 币种发现 + 状态写入。

    身份或币种读取失败不会抛出：授权 token 已换取成功时，远端读取失败按
    ``refresh_failed`` 持久化，由用户在授权页重试，而不是静默降级。
    返回最新币种状态（未配置 token 时返回空 dict）。
    """

    store = config.setdefault("mercadolibre", {})
    token = str(store.get("access_token") or "").strip()
    if not token:
        return {}
    try:
        sync_mercadolibre_identity(store, token)
        discovery: dict[str, Any] = discover_mercadolibre_listing_currency(store)
    except PublishAdapterError as exc:
        discovery = {
            "supported": True,
            "error_code": str(getattr(exc, "code", "") or "MERCADOLIBRE_AUTH_SYNC_FAILED"),
            "error_message": str(exc),
        }
    except Exception as exc:
        discovery = {
            "supported": True,
            "error_code": "MERCADOLIBRE_AUTH_SYNC_FAILED",
            "error_message": str(exc),
        }
    identity = store_identity_for_platform("mercadolibre", store)
    previous = store_listing_currency_from_auth("mercadolibre", identity, store)
    state = apply_currency_discovery(
        "mercadolibre",
        identity,
        discovery,
        previous=previous,
    )
    write_currency_state(store, state)
    return state


__all__ = [
    "discover_mercadolibre_listing_currency",
    "sync_mercadolibre_auth_and_currency",
    "sync_mercadolibre_identity",
]
