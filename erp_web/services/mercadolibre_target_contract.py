from __future__ import annotations

"""Mercado Libre Global Selling 销售目标的纯契约校验。"""

from typing import Any

from erp_web.product_model import normalize_mercadolibre_sites_to_sell


MERCADOLIBRE_CBT_CURRENCY_INVALID = "MERCADOLIBRE_CBT_CURRENCY_INVALID"
MERCADOLIBRE_SITES_TO_SELL_REQUIRED = "MERCADOLIBRE_SITES_TO_SELL_REQUIRED"
MERCADOLIBRE_SALES_TARGET_INVALID = "MERCADOLIBRE_SALES_TARGET_INVALID"
MERCADOLIBRE_SALES_TARGET_CBT_INVALID = "MERCADOLIBRE_SALES_TARGET_CBT_INVALID"
MERCADOLIBRE_LOGISTIC_TYPE_REQUIRED = "MERCADOLIBRE_LOGISTIC_TYPE_REQUIRED"
MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED = (
    "MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED"
)
MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED = (
    "MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED"
)
MERCADOLIBRE_FULLY_MANAGED_BUSINESS_MODEL = "CBT CN Fulfillment Managed"


def mercadolibre_sales_target_selectors(
    marketplace_bindings: Any,
) -> list[str]:
    """返回可供补资料界面选择的 ``SITE_ID:logistic_type`` 稳定值。"""

    bindings = (
        marketplace_bindings
        if isinstance(marketplace_bindings, list)
        else []
    )
    if any(
        isinstance(binding, dict)
        and str(binding.get("business_model") or "").strip().casefold()
        == MERCADOLIBRE_FULLY_MANAGED_BUSINESS_MODEL.casefold()
        for binding in bindings
    ):
        return []
    selectors: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        site_id = str(binding.get("site_id") or "").strip().upper()
        logistic_type = str(
            binding.get("logistic_type") or ""
        ).strip().lower()
        if site_id and site_id != "CBT" and logistic_type:
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


def mercadolibre_global_target_contract(
    sites_to_sell: Any,
    marketplace_bindings: Any,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """返回规范销售目标及确定性契约错误，供核价、预检与 payload 共用。"""

    targets = normalize_mercadolibre_sites_to_sell(sites_to_sell)
    bindings = (
        marketplace_bindings if isinstance(marketplace_bindings, list) else []
    )
    fully_managed_binding = next(
        (
            binding
            for binding in bindings
            if isinstance(binding, dict)
            and str(binding.get("business_model") or "").strip().casefold()
            == MERCADOLIBRE_FULLY_MANAGED_BUSINESS_MODEL.casefold()
        ),
        None,
    )
    if fully_managed_binding is not None:
        return targets, [
            {
                "code": MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED,
                "field": "marketplace_bindings",
                "message": (
                    "当前 CBT 卖家账号使用 "
                    f"{MERCADOLIBRE_FULLY_MANAGED_BUSINESS_MODEL}，"
                    "标准售价流程不支持该模式所需的 global_net_proceeds"
                ),
            }
        ]
    if not targets:
        return targets, [
            {
                "code": MERCADOLIBRE_SITES_TO_SELL_REQUIRED,
                "field": "sites_to_sell",
                "message": "CBT 草稿尚未选择实际销售国家与物流方式",
            }
        ]
    issues: list[dict[str, str]] = []
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
        if not logistic_type:
            issues.append(
                {
                    "code": MERCADOLIBRE_LOGISTIC_TYPE_REQUIRED,
                    "field": f"{field}.logistic_type",
                    "message": f"销售目标 {site_id} 缺少物流方式",
                }
            )
            continue
        if _binding_for_target(target, bindings) is None:
            issues.append(
                {
                    "code": MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED,
                    "field": field,
                    "message": (
                        f"当前账号未开通销售目标 {site_id} + {logistic_type}"
                    ),
                }
            )
    return targets, issues


__all__ = [
    "MERCADOLIBRE_CBT_CURRENCY_INVALID",
    "MERCADOLIBRE_FULLY_MANAGED_BUSINESS_MODEL",
    "MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED",
    "MERCADOLIBRE_LOGISTIC_TYPE_REQUIRED",
    "MERCADOLIBRE_SALES_TARGET_CBT_INVALID",
    "MERCADOLIBRE_SALES_TARGET_INVALID",
    "MERCADOLIBRE_SALES_TARGET_NOT_AUTHORIZED",
    "MERCADOLIBRE_SITES_TO_SELL_REQUIRED",
    "mercadolibre_global_target_contract",
    "mercadolibre_sales_target_selectors",
]
