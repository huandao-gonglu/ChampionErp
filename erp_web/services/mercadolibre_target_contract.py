from __future__ import annotations

"""Mercado Libre Global Selling 销售目标的纯契约校验。"""

from typing import Any

from erp_web.marketplace_registry import marketplace_site
from erp_web.product_model import normalize_mercadolibre_sites_to_sell
from erp_web.services.mercadolibre_listing_model import (
    MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS,
    MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS,
)


MERCADOLIBRE_CBT_CURRENCY_INVALID = "MERCADOLIBRE_CBT_CURRENCY_INVALID"
MERCADOLIBRE_SITES_TO_SELL_REQUIRED = "MERCADOLIBRE_SITES_TO_SELL_REQUIRED"
MERCADOLIBRE_SALES_TARGET_INVALID = "MERCADOLIBRE_SALES_TARGET_INVALID"
MERCADOLIBRE_SALES_TARGET_CBT_INVALID = "MERCADOLIBRE_SALES_TARGET_CBT_INVALID"
MERCADOLIBRE_LOGISTIC_TYPE_REQUIRED = "MERCADOLIBRE_LOGISTIC_TYPE_REQUIRED"
MERCADOLIBRE_MARKET_OPERATION_AMBIGUOUS = (
    "MERCADOLIBRE_MARKET_OPERATION_AMBIGUOUS"
)
MERCADOLIBRE_PRICE_NET_PROCEEDS_CONFLICT = (
    "MERCADOLIBRE_PRICE_NET_PROCEEDS_CONFLICT"
)
MERCADOLIBRE_PRICING_MODEL_MISMATCH = (
    "MERCADOLIBRE_PRICING_MODEL_MISMATCH"
)
MERCADOLIBRE_PRICING_MODEL_UNRESOLVED = (
    "MERCADOLIBRE_PRICING_MODEL_UNRESOLVED"
)
MERCADOLIBRE_PRICING_AMOUNT_REQUIRED = (
    "MERCADOLIBRE_PRICING_AMOUNT_REQUIRED"
)
MERCADOLIBRE_PRICING_MODE_MIXED = "MERCADOLIBRE_PRICING_MODE_MIXED"
MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED = (
    "MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED"
)
MERCADOLIBRE_SALES_TARGET_LANGUAGE_MISMATCH = (
    "MERCADOLIBRE_SALES_TARGET_LANGUAGE_MISMATCH"
)
MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED = (
    "MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED"
)
MERCADOLIBRE_FULLY_MANAGED_BUSINESS_MODEL = "CBT CN Fulfillment Managed"
MERCADOLIBRE_USER_PRODUCTS_REQUIRED = "MERCADOLIBRE_USER_PRODUCTS_REQUIRED"


def _mercadolibre_seller_is_fully_managed(marketplace_bindings: Any) -> bool:
    return any(
        isinstance(binding, dict)
        and str(binding.get("business_model") or "").strip().casefold()
        == MERCADOLIBRE_FULLY_MANAGED_BUSINESS_MODEL.casefold()
        for binding in (
            marketplace_bindings
            if isinstance(marketplace_bindings, list)
            else []
        )
    )


def mercadolibre_sales_target_selectors(
    marketplace_bindings: Any,
    *,
    require_user_products: bool = True,
    listing_model: str = "",
    language: str = "",
) -> list[str]:
    """返回当前文案语言可选的 ``SITE_ID:logistic_type`` 稳定值。"""

    bindings = (
        marketplace_bindings
        if isinstance(marketplace_bindings, list)
        else []
    )
    # Fully Managed 是 CBT seller 级经营模式，不是可与标准 price operation
    # 混选的单个市场能力。任一 child binding 命中即不得展示标准核价目标。
    if _mercadolibre_seller_is_fully_managed(bindings):
        return []
    language_key = str(language or "").strip().replace("_", "-").casefold()
    selectors: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        site_id = str(binding.get("site_id") or "").strip().upper()
        logistic_type = str(
            binding.get("logistic_type") or ""
        ).strip().lower()
        registered_site = marketplace_site("mercadolibre", site_id)
        registered_site_id = str(registered_site.get("code") or "").strip().upper()
        site_language = str(
            registered_site.get("language") or ""
        ).strip().replace("_", "-").casefold()
        language_matches = (
            registered_site_id == site_id
            and (
                not language_key
                or site_language == language_key
                or site_language.split("-", 1)[0]
                == language_key.split("-", 1)[0]
            )
        )
        if (
            site_id
            and site_id != "CBT"
            and logistic_type
            and mercadolibre_binding_pricing_mode(binding)
            and not (
                listing_model
                == MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS
                and str(binding.get("pricing_model") or "").strip().lower()
                == "global_net_proceeds"
            )
            and language_matches
            and (
                not require_user_products
                or binding.get("user_product") is not False
            )
        ):
            selectors.add(f"{site_id}:{logistic_type}")
    return sorted(selectors)


def _binding_for_target(
    target: dict[str, str],
    bindings: Any,
) -> dict[str, Any] | None:
    site_id = str(target.get("site_id") or "").strip().upper()
    logistic_type = str(target.get("logistic_type") or "").strip().lower()
    for raw in bindings if isinstance(bindings, list) else []:
        if not isinstance(raw, dict):
            continue
        if (
            str(raw.get("site_id") or "").strip().upper() == site_id
            and str(raw.get("logistic_type") or "").strip().lower()
            == logistic_type
        ):
            return raw
    return None


def mercadolibre_binding_pricing_mode(binding: dict[str, Any]) -> str:
    pricing_model = str(binding.get("pricing_model") or "").strip().lower()
    if pricing_model in {"net_proceeds", "global_net_proceeds"}:
        return "net_proceeds"
    if pricing_model in {"price", "listing_price"}:
        return "price"
    return ""


def mercadolibre_target_pricing_mode(
    target: dict[str, Any],
    marketplace_bindings: Any,
) -> str:
    """读取指定 marketplace operation 的可信计价模式。"""

    binding = _binding_for_target(target, marketplace_bindings)
    return mercadolibre_binding_pricing_mode(binding) if binding is not None else ""


def _raw_sales_target_shape_issues(value: Any) -> list[dict[str, str]]:
    rows = value if isinstance(value, list) else []
    if not rows:
        return [
            {
                "code": MERCADOLIBRE_SITES_TO_SELL_REQUIRED,
                "field": "sites_to_sell",
                "message": "CBT 草稿尚未选择实际销售国家与物流方式",
            }
        ]
    issues: list[dict[str, str]] = []
    selected_sites: set[str] = set()
    for index, target in enumerate(rows):
        field = f"sites_to_sell[{index}]"
        if not isinstance(target, dict):
            issues.append(
                {
                    "code": MERCADOLIBRE_SALES_TARGET_INVALID,
                    "field": field,
                    "message": f"{field} 必须是 marketplace object",
                }
            )
            continue
        site_id = str(
            target.get("site_id") or target.get("siteId") or ""
        ).strip().upper()
        if site_id and site_id in selected_sites:
            issues.append(
                {
                    "code": MERCADOLIBRE_MARKET_OPERATION_AMBIGUOUS,
                    "field": field,
                    "message": (
                        f"销售市场 {site_id} 同时选择了多个物流 operation；"
                        "一次 publication 只能选择一个"
                    ),
                }
            )
        if site_id:
            selected_sites.add(site_id)
    return issues


def mercadolibre_payload_pricing_contract(
    payload: Any,
    *,
    listing_model: str,
) -> tuple[str, list[dict[str, str]]]:
    """校验待外发 payload 的根计价模式与每市场销售条件一致。"""

    raw = payload if isinstance(payload, dict) else {}
    has_price = raw.get("price") not in (None, "")
    has_global_net_proceeds = raw.get("global_net_proceeds") not in (
        None,
        "",
    )
    issues: list[dict[str, str]] = []
    raw_targets = raw.get("sites_to_sell")
    issues.extend(_raw_sales_target_shape_issues(raw_targets))
    targets = raw_targets if isinstance(raw_targets, list) else []
    target_modes: set[str] = set()
    target_facts: list[tuple[int, str, bool, bool]] = []
    for index, target in enumerate(targets):
        field = f"sites_to_sell[{index}]"
        if not isinstance(target, dict):
            continue
        site_id = str(
            target.get("site_id") or target.get("siteId") or ""
        ).strip().upper()
        target_has_price = target.get("price") not in (None, "")
        target_net_proceeds = target.get("net_proceeds")
        if target_net_proceeds in (None, ""):
            target_net_proceeds = target.get("netProceeds")
        target_has_net_proceeds = target_net_proceeds not in (None, "")
        if target_has_price:
            target_modes.add("price")
        if target_has_net_proceeds:
            target_modes.add("net_proceeds")
        target_facts.append(
            (index, site_id, target_has_price, target_has_net_proceeds)
        )
        if target_has_price and target_has_net_proceeds:
            issues.append(
                {
                    "code": MERCADOLIBRE_PRICE_NET_PROCEEDS_CONFLICT,
                    "field": field,
                    "message": (
                        f"销售目标 {site_id or index} 的 price 与 net_proceeds "
                        "互斥，只能选择一种计价方式"
                    ),
                }
            )
    pricing_mode = ""
    if listing_model == "traditional_global_items":
        if has_global_net_proceeds:
            issues.append(
                {
                    "code": MERCADOLIBRE_PRICE_NET_PROCEEDS_CONFLICT,
                    "field": "global_net_proceeds",
                    "message": (
                        "传统 Global Items 禁止 global_net_proceeds；"
                        "Remote 净回款必须逐市场填写 net_proceeds"
                    ),
                }
            )
        if has_price:
            pricing_mode = "price"
        elif target_modes == {"net_proceeds"}:
            pricing_mode = "net_proceeds"
        else:
            issues.append(
                {
                    "code": MERCADOLIBRE_PRICE_NET_PROCEEDS_CONFLICT,
                    "field": "price",
                    "message": (
                        "传统 Global Items 必须使用根 price，或为每个 Remote "
                        "市场填写 net_proceeds"
                    ),
                }
            )
    else:
        if has_price == has_global_net_proceeds:
            issues.append(
                {
                    "code": MERCADOLIBRE_PRICE_NET_PROCEEDS_CONFLICT,
                    "field": "price",
                    "message": "price 与 global_net_proceeds 必须且只能提供一个",
                }
            )
        else:
            pricing_mode = (
                "net_proceeds" if has_global_net_proceeds else "price"
            )

    if len(target_modes) > 1:
        issues.append(
            {
                "code": MERCADOLIBRE_PRICING_MODE_MIXED,
                "field": "sites_to_sell",
                "message": "同一次发布不能混用 price 与 net_proceeds",
            }
        )
    for index, site_id, target_has_price, target_has_net_proceeds in target_facts:
        field = f"sites_to_sell[{index}]"
        if pricing_mode == "price" and target_has_net_proceeds:
            issues.append(
                {
                    "code": MERCADOLIBRE_PRICING_MODEL_MISMATCH,
                    "field": f"{field}.net_proceeds",
                    "message": (
                        f"销售目标 {site_id or index} 与 price 计价模式不一致"
                    ),
                }
            )
        elif pricing_mode == "net_proceeds" and target_has_price:
            issues.append(
                {
                    "code": MERCADOLIBRE_PRICING_MODEL_MISMATCH,
                    "field": field,
                    "message": (
                        f"销售目标 {site_id or index} 使用 net_proceeds 时不能提供 price"
                    ),
                }
            )
        elif (
            listing_model == "traditional_global_items"
            and pricing_mode == "net_proceeds"
            and not target_has_net_proceeds
        ):
            issues.append(
                {
                    "code": MERCADOLIBRE_PRICING_AMOUNT_REQUIRED,
                    "field": f"{field}.net_proceeds",
                    "message": f"销售目标 {site_id or index} 缺少 net_proceeds",
                }
            )
    return pricing_mode, issues


def mercadolibre_global_target_contract(
    sites_to_sell: Any,
    marketplace_bindings: Any,
    *,
    listing_model: str,
    required_pricing_mode: str = "",
    require_user_products: bool = True,
    require_pricing_amounts: bool = False,
    language: str = "",
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """返回规范销售目标及确定性契约错误，供核价、预检与 payload 共用。"""

    raw_shape_issues = _raw_sales_target_shape_issues(sites_to_sell)
    targets = normalize_mercadolibre_sites_to_sell(sites_to_sell)
    bindings = (
        marketplace_bindings if isinstance(marketplace_bindings, list) else []
    )
    if _mercadolibre_seller_is_fully_managed(bindings):
        return targets, [
            {
                "code": MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED,
                "field": "marketplace_bindings",
                "message": (
                    "当前 CBT 卖家属于 Fully Managed；标准 price 流程不可用，"
                    "必须使用 global_net_proceeds"
                ),
            }
        ]
    if not targets:
        return targets, raw_shape_issues
    issues: list[dict[str, str]] = list(raw_shape_issues)
    language_key = str(language or "").strip().replace("_", "-").casefold()
    selected_sites: set[str] = set()
    selected_pricing_modes: set[str] = set()
    for index, target in enumerate(targets):
        field = f"sites_to_sell[{index}]"
        site_id = target["site_id"]
        logistic_type = target["logistic_type"]
        if not site_id:
            issues.append(
                {
                    "code": MERCADOLIBRE_SALES_TARGET_INVALID,
                    "field": f"{field}.site_id",
                    "message": "销售目标缺少实际国家站点 ID",
                }
            )
            continue
        if site_id == "CBT":
            issues.append(
                {
                    "code": MERCADOLIBRE_SALES_TARGET_CBT_INVALID,
                    "field": f"{field}.site_id",
                    "message": (
                        "CBT 是全局刊登模式，不能作为 sites_to_sell 销售国家"
                    ),
                }
            )
            continue
        registered_site = marketplace_site("mercadolibre", site_id)
        registered_site_id = str(
            registered_site.get("code") or ""
        ).strip().upper()
        if registered_site_id != site_id:
            issues.append(
                {
                    "code": MERCADOLIBRE_SALES_TARGET_INVALID,
                    "field": f"{field}.site_id",
                    "message": f"销售目标 {site_id} 不是已注册的 Mercado 子市场",
                }
            )
            continue
        site_language = str(
            registered_site.get("language") or ""
        ).strip().replace("_", "-").casefold()
        if language_key and (
            not site_language
            or site_language.split("-", 1)[0]
            != language_key.split("-", 1)[0]
        ):
            issues.append(
                {
                    "code": MERCADOLIBRE_SALES_TARGET_LANGUAGE_MISMATCH,
                    "field": f"{field}.site_id",
                    "message": (
                        f"销售市场 {site_id} 的语言 {site_language or '未配置'} "
                        f"与当前草稿语言 {language} 不一致"
                    ),
                }
            )
            continue
        if site_id in selected_sites:
            issues.append(
                {
                    "code": MERCADOLIBRE_MARKET_OPERATION_AMBIGUOUS,
                    "field": field,
                    "message": (
                        f"销售市场 {site_id} 同时选择了多个物流 operation；"
                        "一次 publication 只能选择一个"
                    ),
                }
            )
            continue
        selected_sites.add(site_id)
        if not logistic_type:
            issues.append(
                {
                    "code": MERCADOLIBRE_LOGISTIC_TYPE_REQUIRED,
                    "field": f"{field}.logistic_type",
                    "message": f"销售目标 {site_id} 缺少物流方式",
                }
            )
            continue
        binding = _binding_for_target(target, bindings)
        if binding is None:
            issues.append(
                {
                    "code": MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED,
                    "field": field,
                    "message": (
                        f"当前账号未开通销售目标 {site_id} + {logistic_type}"
                    ),
                }
            )
            continue
        if require_user_products and binding.get("user_product") is False:
            issues.append(
                {
                    "code": MERCADOLIBRE_USER_PRODUCTS_REQUIRED,
                    "field": field,
                    "message": f"销售目标 {site_id} 尚未开通 User Products",
                }
            )
            continue
        has_price = target.get("price") not in (None, "")
        has_net_proceeds = target.get("net_proceeds") not in (None, "")
        if has_price and has_net_proceeds:
            issues.append(
                {
                    "code": MERCADOLIBRE_PRICE_NET_PROCEEDS_CONFLICT,
                    "field": field,
                    "message": (
                        f"销售目标 {site_id} 的 price 与 net_proceeds 互斥，"
                        "只能选择一种计价方式"
                    ),
                }
            )
            continue
        pricing_mode = mercadolibre_binding_pricing_mode(binding)
        if not pricing_mode:
            issues.append(
                {
                    "code": MERCADOLIBRE_PRICING_MODEL_UNRESOLVED,
                    "field": field,
                    "message": (
                        f"销售目标 {site_id} 的 pricing_model 尚未解析，"
                        "请重新验证授权并同步市场能力"
                    ),
                }
            )
            continue
        raw_pricing_model = str(
            binding.get("pricing_model") or ""
        ).strip().lower()
        if (
            listing_model
            == MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS
            and raw_pricing_model == "global_net_proceeds"
        ):
            issues.append(
                {
                    "code": MERCADOLIBRE_PRICING_MODEL_MISMATCH,
                    "field": field,
                    "message": (
                        f"销售目标 {site_id} 只提供 global_net_proceeds；"
                        "传统 Global Items 只能使用市场级 net_proceeds"
                    ),
                }
            )
            continue
        if pricing_mode == "net_proceeds" and logistic_type != "remote":
            issues.append(
                {
                    "code": MERCADOLIBRE_PRICING_MODEL_MISMATCH,
                    "field": field,
                    "message": (
                        f"销售目标 {site_id} 的 {logistic_type} 物流不支持 "
                        "net_proceeds；Fulfillment 必须使用 price"
                    ),
                }
            )
            continue
        selected_pricing_modes.add(pricing_mode)
        if required_pricing_mode and pricing_mode != required_pricing_mode:
            issues.append(
                {
                    "code": MERCADOLIBRE_PRICING_MODEL_MISMATCH,
                    "field": field,
                    "message": (
                        f"销售目标 {site_id} 的账号 pricing_model={pricing_mode}，"
                        f"与 payload 根计价模式 {required_pricing_mode} 不一致"
                    ),
                }
            )
            continue
        if (
            pricing_mode == "net_proceeds"
            and not has_net_proceeds
            and require_pricing_amounts
            and listing_model
            == MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS
        ):
            issues.append(
                {
                    "code": MERCADOLIBRE_PRICING_AMOUNT_REQUIRED,
                    "field": f"{field}.net_proceeds",
                    "message": (
                        f"销售目标 {site_id} 的账号 pricing_model=net_proceeds，"
                        "必须显式填写 net_proceeds"
                    ),
                }
            )
            continue
        if pricing_mode == "price" and has_net_proceeds:
            issues.append(
                {
                    "code": MERCADOLIBRE_PRICING_MODEL_MISMATCH,
                    "field": f"{field}.net_proceeds",
                    "message": (
                        f"销售目标 {site_id} 未启用 net_proceeds 计价，"
                        "必须使用 price"
                    ),
                }
            )
        elif pricing_mode == "price" and not has_price and require_pricing_amounts:
            issues.append(
                {
                    "code": MERCADOLIBRE_PRICING_AMOUNT_REQUIRED,
                    "field": f"{field}.price",
                    "message": (
                        f"销售目标 {site_id} 缺少已应用的市场售价，请重新核价"
                    ),
                }
            )
    if len(selected_pricing_modes) > 1:
        issues.append(
            {
                "code": MERCADOLIBRE_PRICING_MODE_MIXED,
                "field": "sites_to_sell",
                "message": (
                    "同一个 Siteless User Product 不能混用 price 与 "
                    "net_proceeds 账号计价模式"
                ),
            }
        )
    return targets, issues


__all__ = [
    "MERCADOLIBRE_CBT_CURRENCY_INVALID",
    "MERCADOLIBRE_FULLY_MANAGED_BUSINESS_MODEL",
    "MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED",
    "MERCADOLIBRE_LOGISTIC_TYPE_REQUIRED",
    "MERCADOLIBRE_MARKET_OPERATION_AMBIGUOUS",
    "MERCADOLIBRE_PRICE_NET_PROCEEDS_CONFLICT",
    "MERCADOLIBRE_PRICING_AMOUNT_REQUIRED",
    "MERCADOLIBRE_PRICING_MODEL_UNRESOLVED",
    "MERCADOLIBRE_PRICING_MODEL_MISMATCH",
    "MERCADOLIBRE_PRICING_MODE_MIXED",
    "MERCADOLIBRE_SALES_TARGET_CBT_INVALID",
    "MERCADOLIBRE_SALES_TARGET_INVALID",
    "MERCADOLIBRE_SALES_TARGET_LANGUAGE_MISMATCH",
    "MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED",
    "MERCADOLIBRE_SITES_TO_SELL_REQUIRED",
    "MERCADOLIBRE_USER_PRODUCTS_REQUIRED",
    "mercadolibre_binding_pricing_mode",
    "mercadolibre_global_target_contract",
    "mercadolibre_payload_pricing_contract",
    "mercadolibre_sales_target_selectors",
    "mercadolibre_target_pricing_mode",
]
