from __future__ import annotations

"""Mercado Libre 授权身份与发布币种发现的统一服务。

``/users/me`` 身份同步、CBT 子市场能力映射、区域站点币种元数据读取与币种
状态写入收敛在这里；授权测试、token 刷新与发布前检查不再各自复制用户信息
更新逻辑。静态 ``site_id`` 只是默认目标站点设置，不参与账户身份或币种推断。
"""

from typing import Any

from erp_web import marketplaces as publisher
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.services.mercadolibre_listing_model import (
    mercadolibre_listing_model_from_user_tags,
)
from erp_web.services.listing_currency_service import (
    apply_currency_discovery,
    store_identity_for_platform,
    store_listing_currency_from_auth,
    write_currency_state,
)


def sync_mercadolibre_identity(
    store: dict[str, Any],
    token: str,
) -> dict[str, Any]:
    """``/users/me`` → user_id / nickname / account_site_id（只写内存段）。"""

    profile = publisher.fetch_mercadolibre_user_profile(token)
    user_id = str(profile.get("user_id") or "").strip()
    nickname = str(profile.get("nickname") or "").strip()
    account_site_id = str(profile.get("site_id") or "").strip().upper()
    tags = {
        str(tag or "").strip().casefold()
        for tag in profile.get("tags", [])
    } if isinstance(profile.get("tags"), list) else set()
    if user_id:
        store["user_id"] = user_id
    if nickname:
        store["nickname"] = nickname
    store["shop_name"] = nickname or user_id or str(store.get("shop_name") or "")
    if account_site_id:
        store["account_site_id"] = account_site_id
    store["site_id"] = "CBT"
    store["user_product_seller"] = "user_product_seller" in tags
    store["listing_model"] = (
        mercadolibre_listing_model_from_user_tags(
            tags,
            account_site_id=account_site_id,
        )
        or ""
    )
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
    """发现账号市场能力与店铺级发布币种。

    - ``account_site_id`` 来自 ``/users/me``；缺失时不推断，直接进入人工配置；
    - CBT 父账号先读取 ``/marketplace/users/{user_id}`` 并持久化规范化子市场
      binding；标准 Global Selling 合同币种固定为 USD，不请求不存在的
      ``/sites/CBT``；
    - 站点元数据返回币种 → 单值锁定 / 多值待选；
    - 区域账号继续读取 ``/sites/{site_id}``，且携带当前 Bearer token；
    - 站点不存在（404）或无币种字段 → 该店铺没有店铺级可查询能力 → 人工配置；
    - 其他请求失败 → ``refresh_failed``，绝不回退本地站点注册表。
    """

    site_id = str(store.get("account_site_id") or "").strip().upper()
    if not site_id:
        return {"supported": False}
    token = str(store.get("access_token") or "").strip()
    if site_id == "CBT":
        # 远端映射是发布目标与 Fully Managed 能力的唯一可信来源。读取失败
        # 时清空旧值，避免新授权账号继承上次成功同步的目标市场。
        store["marketplace_bindings"] = []
        user_id = str(store.get("user_id") or "").strip()
        if not user_id:
            return {
                "supported": True,
                "error_code": "MERCADOLIBRE_MARKETPLACE_USER_ID_MISSING",
                "error_message": "Mercado Libre CBT 父账号缺少 user_id，无法读取销售市场映射",
            }
        try:
            marketplace_user = publisher.fetch_mercadolibre_marketplace_user(
                user_id, token
            )
        except PublishAdapterError as exc:
            return {
                "supported": True,
                "error_code": str(
                    getattr(exc, "code", "")
                    or "MERCADOLIBRE_MARKETPLACE_BINDINGS_FAILED"
                ),
                "error_message": str(exc),
            }
        except Exception as exc:
            return {
                "supported": True,
                "error_code": "MERCADOLIBRE_MARKETPLACE_BINDINGS_FAILED",
                "error_message": str(exc),
            }
        mapped_user_id = str(marketplace_user.get("user_id") or "").strip()
        mapped_site_id = str(marketplace_user.get("site_id") or "").strip().upper()
        if mapped_user_id != user_id or mapped_site_id != "CBT":
            return {
                "supported": True,
                "error_code": "MERCADOLIBRE_MARKETPLACE_PARENT_MISMATCH",
                "error_message": (
                    "Mercado Libre 销售市场映射与当前 CBT 父账号不一致"
                ),
            }
        bindings = marketplace_user.get("marketplace_bindings")
        valid_bindings = [
            dict(binding)
            for binding in bindings
            if isinstance(binding, dict)
            and str(binding.get("seller_id") or "").strip()
            and str(binding.get("site_id") or "").strip().upper() not in {"", "CBT"}
            and str(binding.get("logistic_type") or "").strip()
        ] if isinstance(bindings, list) else []
        # 无论账号使用 User Products 还是传统 Global Items，都保留本次
        # 成功读取的子市场能力快照。模型只由 /users tags 派生，不按接口
        # 报错做 fallback。
        store["marketplace_bindings"] = valid_bindings
        if not valid_bindings:
            return {
                "supported": True,
                "error_code": "MERCADOLIBRE_MARKETPLACE_BINDINGS_EMPTY",
                "error_message": "Mercado Libre 未返回当前 CBT 账号可用的销售市场映射",
            }
        if (
            store.get("listing_model") == "user_products"
            and not any(
                binding.get("user_product") is not False
                for binding in valid_bindings
            )
        ):
            return {
                "supported": True,
                "error_code": "MERCADOLIBRE_USER_PRODUCTS_REQUIRED",
                "error_message": (
                    "Mercado Libre 明确返回所有子市场 operation 均未开通 User Products"
                ),
            }
        return {
            "supported": True,
            "currencies": ["USD"],
            "source": "global_selling_contract",
        }

    # 本项目的 Mercado Provider 只支持 CBT Global Selling。区域账号不是
    # Global 子市场授权的替代路径，必须重新授权父账号。
    store["marketplace_bindings"] = []
    return {
        "supported": True,
        "error_code": "MERCADOLIBRE_CBT_ACCOUNT_REQUIRED",
        "error_message": (
            f"当前授权账号属于 {site_id} 区域站点；"
            "请授权 CBT Global Selling 父账号"
        ),
    }


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
