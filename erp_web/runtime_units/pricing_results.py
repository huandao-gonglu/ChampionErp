"""核价结果与 Mercado 销售条件的纯校验和投影。"""
from copy import deepcopy
import math
from typing import Any
from erp_web.product_model import mercadolibre_sales_condition_basis, mercadolibre_sales_operation_keys, normalize_mercadolibre_sites_to_sell
from erp_web.product_model.sku_model import text
from erp_web.services.capability_errors import BusinessCapabilityError

def _sales_targets_from_selectors(selectors: Any) -> list[dict[str, str]]:
    """把受信多选值转为草稿持久化使用的规范销售目标列表。"""

    if not isinstance(selectors, (list, tuple)):
        # 请求 Schema 只接受数组；这里也不为旧单字符串保留兼容路径。
        return []
    rows: list[dict[str, str]] = []
    for selector in selectors:
        value = text(selector)
        site_id, separator, logistic_type = value.partition(":")
        if not separator:
            # 保留不完整事实交给统一 Mercado target contract 产生字段级错误，
            # 不能在多选中静默丢弃非法项后保存剩余目标。
            logistic_type = ""
        rows.append(
            {
                "site_id": site_id,
                "logistic_type": logistic_type,
            }
        )
    return normalize_mercadolibre_sites_to_sell(rows)


def _sales_targets_with_existing_conditions(
    selected_targets: Any,
    current_targets: Any,
) -> list[dict[str, Any]]:
    """保留仍被选中 operation 的非金额销售条件。"""

    existing_by_operation = {
        (target["site_id"], target["logistic_type"]): target
        for target in mercadolibre_sales_condition_basis(current_targets)
    }
    return [
        {
            **existing_by_operation.get(
                (selected["site_id"], selected["logistic_type"]),
                {},
            ),
            **selected,
        }
        for selected in normalize_mercadolibre_sites_to_sell(selected_targets)
    ]


def _pricing_target_is_usable(
    target_draft: dict[str, Any],
    selected: dict[str, Any],
) -> bool:
    applied = (
        selected.get("applied_price")
        if isinstance(selected.get("applied_price"), dict)
        else {}
    )
    basis = (
        selected.get("calculation_basis")
        if isinstance(selected.get("calculation_basis"), dict)
        else {}
    )
    try:
        amount_valid = float(text(applied.get("amount"))) > 0
    except (TypeError, ValueError):
        amount_valid = False
    usable = bool(
        amount_valid
        and text(applied.get("currency")).upper()
        == text(target_draft.get("listing_currency")).upper()
        and basis
        and text(selected.get("calculation_fingerprint"))
    )
    if not usable:
        return False
    if (
        text(target_draft.get("platform")).lower() == "mercadolibre"
        and text(target_draft.get("site")).upper() == "CBT"
    ):
        current_targets = normalize_mercadolibre_sites_to_sell(
            target_draft.get("sites_to_sell")
        )
        current_operations = mercadolibre_sales_operation_keys(current_targets)
        if not current_operations or mercadolibre_sales_condition_basis(
            basis.get("sites_to_sell")
        ) != mercadolibre_sales_condition_basis(current_targets):
            return False
        raw_modes = basis.get("destination_pricing_modes")
        modes = raw_modes if isinstance(raw_modes, list) else []
        mode_by_operation = {
            (
                text(item.get("site_id")).upper(),
                text(item.get("logistic_type")).lower(),
            ): text(item.get("pricing_model")).lower()
            for item in modes
            if isinstance(item, dict)
            and text(item.get("pricing_model")).lower() in {"price", "net_proceeds"}
        }
        raw_results = selected.get("destination_results")
        destination_results = raw_results if isinstance(raw_results, list) else []
        result_by_operation = {
            (
                text(item.get("site_id")).upper(),
                text(item.get("logistic_type")).lower(),
            ): item
            for item in destination_results
            if isinstance(item, dict)
        }
        if tuple(sorted(mode_by_operation)) != tuple(
            sorted(current_operations)
        ) or tuple(sorted(result_by_operation)) != tuple(sorted(current_operations)):
            return False
        expected_currency = text(target_draft.get("listing_currency")).upper()
        expected_fingerprint = text(selected.get("calculation_fingerprint"))
        for operation in current_operations:
            destination = result_by_operation[operation]
            pricing_model = text(destination.get("pricing_model")).lower()
            if pricing_model != mode_by_operation[operation]:
                return False
            selected_money = destination.get(pricing_model)
            opposite_money = destination.get(
                "price" if pricing_model == "net_proceeds" else "net_proceeds"
            )
            if not isinstance(selected_money, dict) or opposite_money not in (
                None,
                "",
            ):
                return False
            try:
                destination_amount_valid = float(text(selected_money.get("amount"))) > 0
            except (TypeError, ValueError):
                destination_amount_valid = False
            if (
                not destination_amount_valid
                or text(selected_money.get("currency")).upper() != expected_currency
                or text(destination.get("calculation_fingerprint"))
                != expected_fingerprint
            ):
                return False
            current_target = next(
                (
                    item
                    for item in current_targets
                    if (
                        item["site_id"],
                        item["logistic_type"],
                    )
                    == operation
                ),
                {},
            )
            if text(current_target.get(pricing_model)) != text(
                selected_money.get("amount")
            ):
                return False
            if current_target.get(
                "price" if pricing_model == "net_proceeds" else "net_proceeds"
            ) not in (None, ""):
                return False
        return True
    return True


def _apply_mercadolibre_destination_results(
    target: dict[str, Any],
    pricing_target: dict[str, Any],
) -> list[dict[str, Any]]:
    """把已验证核价结果原子写回各 marketplace operation。"""

    current_targets = normalize_mercadolibre_sites_to_sell(target.get("sites_to_sell"))
    raw_results = pricing_target.get("destination_results")
    results = raw_results if isinstance(raw_results, list) else []
    result_by_operation = {
        (
            text(item.get("site_id")).upper(),
            text(item.get("logistic_type")).lower(),
        ): item
        for item in results
        if isinstance(item, dict)
    }
    operations = mercadolibre_sales_operation_keys(current_targets)
    if not operations or len(results) != len(operations) or tuple(sorted(result_by_operation)) != tuple(
        sorted(operations)
    ):
        raise BusinessCapabilityError(
            "PRICING_RESULT_INVALID",
            "CBT 核价结果没有完整覆盖当前销售国家与物流方式。",
        )
    expected_currency = text(
        pricing_target.get("listing_currency") or target.get("listing_currency")
    ).upper()
    expected_fingerprint = text(pricing_target.get("calculation_fingerprint"))
    mode_by_operation = {
        (text(item.get("site_id")).upper(), text(item.get("logistic_type")).lower()): item.get("pricing_model")
        for item in pricing_target.get("calculation_basis", {}).get("destination_pricing_modes", [])
    }
    applied_targets: list[dict[str, Any]] = []
    for current in current_targets:
        operation = (current["site_id"], current["logistic_type"])
        destination = result_by_operation[operation]
        pricing_model = text(destination.get("pricing_model")).lower()
        selected_money = destination.get(pricing_model)
        opposite_field = "price" if pricing_model == "net_proceeds" else "net_proceeds"
        if (
            pricing_model not in {"price", "net_proceeds"}
            or pricing_model != mode_by_operation.get(operation)
            or not isinstance(selected_money, dict)
            or destination.get(opposite_field) not in (None, "")
            or text(selected_money.get("currency")).upper() != expected_currency
            or text(destination.get("calculation_fingerprint")) != expected_fingerprint
        ):
            raise BusinessCapabilityError(
                "PRICING_RESULT_INVALID",
                f"销售目标 {current['site_id']} 的核价模式或币种无效。",
            )
        try:
            amount = float(text(selected_money.get("amount")))
            amount_valid = math.isfinite(amount) and amount > 0
        except (TypeError, ValueError):
            amount_valid = False
        if not amount_valid:
            raise BusinessCapabilityError(
                "PRICING_RESULT_INVALID",
                f"销售目标 {current['site_id']} 的核价金额无效。",
            )
        applied = deepcopy(current)
        applied.pop("price", None)
        applied.pop("net_proceeds", None)
        # 草稿 marketplace condition 与 Mercado wire 都使用标量金额；币种由
        # CBT listing_currency=USD 统一约束。Money 仅保留在核价结果边界。
        applied[pricing_model] = text(selected_money.get("amount"))
        applied_targets.append(applied)
    return applied_targets
