from __future__ import annotations

"""目标市场准备流程中的确定性核价与草稿持久化。

``prepare_target_pricing`` 是核价落库的唯一路径：``draft_prepare_for_market``
与 focused write ``draft_pricing_apply`` 都通过它把确定性核价结果持久化为
平台草稿 ``pricing.targets[target_key]``，不保留第二条定价写入路径。
"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from erp_web.context import get_context
from erp_web.product_model import (
    mercadolibre_sales_condition_basis,
    mercadolibre_sales_operation_keys,
    normalize_mercadolibre_sites_to_sell,
)
from erp_web.runtime_units.draft_publish_context import (
    draft_for_publish_target,
    merge_target_listing_into_draft,
)
from erp_web.runtime_units.market_capability_support import (
    invalidate_target_publish_preparation,
    load_draft,
    raise_store_error,
    require_platform,
    select_target,
    text,
)
from erp_web.runtime_units.pricing_runtime import calculate_price
from erp_web.runtime_units.product_capabilities import ProductCapabilityStore
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputRequired,
)
from erp_web.services.listing_currency_service import (
    StoreCurrencyNotReadyError,
    require_store_listing_currency,
)
from erp_web.services.mercadolibre_target_contract import (
    MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED,
    mercadolibre_global_target_contract,
    mercadolibre_target_pricing_mode,
)
from erp_web.services.mercadolibre_listing_model import (
    MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS,
    require_mercadolibre_listing_model,
)


PricingCalculator = Callable[[dict[str, Any]], dict[str, Any]]
StoreConfigLoader = Callable[[], dict[str, Any]]


def _load_store_config() -> dict[str, Any]:
    return get_context().config.load_store_config()


def _target_key(platform: str, site: str) -> str:
    return f"{text(platform).lower()}:{text(site).lower()}"


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


def _selected_pricing_target(target_draft: dict[str, Any]) -> dict[str, Any]:
    """草稿当前生效的定价目标：优先 selected_pricing，回退 pricing.targets。"""

    selected = (
        target_draft.get("selected_pricing")
        if isinstance(target_draft.get("selected_pricing"), dict)
        else {}
    )
    if not selected:
        pricing = (
            target_draft.get("pricing")
            if isinstance(target_draft.get("pricing"), dict)
            else {}
        )
        targets = pricing.get("targets") if isinstance(pricing.get("targets"), dict) else {}
        key = _target_key(
            text(target_draft.get("platform")),
            text(target_draft.get("site")),
        )
        selected = targets.get(key) if isinstance(targets.get(key), dict) else {}
    return selected


def _pricing_target_is_usable(
    target_draft: dict[str, Any],
    selected: dict[str, Any],
) -> bool:
    applied = selected.get("applied_price") if isinstance(selected.get("applied_price"), dict) else {}
    basis = selected.get("calculation_basis") if isinstance(selected.get("calculation_basis"), dict) else {}
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
        if (
            not current_operations
            or mercadolibre_sales_condition_basis(basis.get("sites_to_sell"))
            != mercadolibre_sales_condition_basis(current_targets)
        ):
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
            and text(item.get("pricing_model")).lower()
            in {"price", "net_proceeds"}
        }
        raw_results = selected.get("destination_results")
        destination_results = (
            raw_results if isinstance(raw_results, list) else []
        )
        result_by_operation = {
            (
                text(item.get("site_id")).upper(),
                text(item.get("logistic_type")).lower(),
            ): item
            for item in destination_results
            if isinstance(item, dict)
        }
        if (
            tuple(sorted(mode_by_operation)) != tuple(sorted(current_operations))
            or tuple(sorted(result_by_operation))
            != tuple(sorted(current_operations))
        ):
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
                destination_amount_valid = (
                    float(text(selected_money.get("amount"))) > 0
                )
            except (TypeError, ValueError):
                destination_amount_valid = False
            if (
                not destination_amount_valid
                or text(selected_money.get("currency")).upper()
                != expected_currency
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


def _canonical_destination_pricing_modes(value: Any) -> tuple[tuple[str, str, str], ...]:
    rows = value if isinstance(value, list) else []
    return tuple(
        sorted(
            (
                text(item.get("site_id")).upper(),
                text(item.get("logistic_type")).lower(),
                text(item.get("pricing_model")).lower(),
            )
            for item in rows
            if isinstance(item, dict)
            and text(item.get("site_id"))
            and text(item.get("logistic_type"))
            and text(item.get("pricing_model")).lower()
            in {"price", "net_proceeds"}
        )
    )


def _current_mercadolibre_pricing_context(
    target: dict[str, Any],
    *,
    store_config: dict[str, Any],
) -> tuple[str, tuple[tuple[str, str, str], ...]]:
    """读取当前授权投影；无效/过期授权不得支持复用旧核价。"""

    store = (
        store_config.get("mercadolibre")
        if isinstance(store_config, dict)
        else {}
    )
    store = store if isinstance(store, dict) else {}
    try:
        listing_model = require_mercadolibre_listing_model(
            store.get("listing_model")
        )
    except RuntimeError:
        return "", ()
    targets = mercadolibre_sales_condition_basis(target.get("sites_to_sell"))
    _canonical, issues = mercadolibre_global_target_contract(
        targets,
        store.get("marketplace_bindings"),
        listing_model=listing_model,
        require_user_products=(
            listing_model == MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS
        ),
        language=text(target.get("language")),
    )
    if issues:
        return "", ()
    return listing_model, _canonical_destination_pricing_modes(
        [
            {
                "site_id": destination["site_id"],
                "logistic_type": destination["logistic_type"],
                "pricing_model": mercadolibre_target_pricing_mode(
                    destination,
                    store.get("marketplace_bindings"),
                ),
            }
            for destination in targets
        ]
    )


def _current_mercadolibre_pricing_context_matches(
    target: dict[str, Any],
    selected: dict[str, Any],
    *,
    store_config: dict[str, Any],
) -> bool:
    basis = (
        selected.get("calculation_basis")
        if isinstance(selected.get("calculation_basis"), dict)
        else {}
    )
    current_listing_model, current_modes = _current_mercadolibre_pricing_context(
        target,
        store_config=store_config,
    )
    return bool(
        current_listing_model
        and current_modes
        and text(basis.get("listing_model")) == current_listing_model
        and _canonical_destination_pricing_modes(
            basis.get("destination_pricing_modes")
        )
        == current_modes
    )


def _current_store_currency_context_matches(
    target: dict[str, Any],
    selected: dict[str, Any],
    *,
    store_config: dict[str, Any],
) -> bool:
    platform = text(target.get("platform")).lower()
    store = store_config.get(platform) if isinstance(store_config, dict) else {}
    store = store if isinstance(store, dict) else {}
    try:
        state = require_store_listing_currency(platform, store)
    except StoreCurrencyNotReadyError:
        return False
    basis = (
        selected.get("calculation_basis")
        if isinstance(selected.get("calculation_basis"), dict)
        else {}
    )
    current_currency = text(state.get("listing_currency")).upper()
    current_fingerprint = text(state.get("currency_fingerprint"))
    return bool(
        current_currency
        and current_fingerprint
        and text(selected.get("listing_currency")).upper() == current_currency
        and text(selected.get("currency_fingerprint")) == current_fingerprint
        and text(basis.get("listing_currency")).upper() == current_currency
        and text(basis.get("currency_fingerprint")) == current_fingerprint
    )


def _apply_mercadolibre_destination_results(
    target: dict[str, Any],
    pricing_target: dict[str, Any],
) -> list[dict[str, Any]]:
    """把已验证核价结果原子写回各 marketplace operation。"""

    current_targets = normalize_mercadolibre_sites_to_sell(
        target.get("sites_to_sell")
    )
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
    if not operations or tuple(sorted(result_by_operation)) != tuple(
        sorted(operations)
    ):
        raise BusinessCapabilityError(
            "PRICING_RESULT_INVALID",
            "CBT 核价结果没有完整覆盖当前销售国家与物流方式。",
        )
    expected_currency = text(
        pricing_target.get("listing_currency")
        or target.get("listing_currency")
    ).upper()
    expected_fingerprint = text(
        pricing_target.get("calculation_fingerprint")
    )
    applied_targets: list[dict[str, Any]] = []
    for current in current_targets:
        operation = (current["site_id"], current["logistic_type"])
        destination = result_by_operation[operation]
        pricing_model = text(destination.get("pricing_model")).lower()
        selected_money = destination.get(pricing_model)
        opposite_field = (
            "price" if pricing_model == "net_proceeds" else "net_proceeds"
        )
        if (
            pricing_model not in {"price", "net_proceeds"}
            or not isinstance(selected_money, dict)
            or destination.get(opposite_field) not in (None, "")
            or text(selected_money.get("currency")).upper()
            != expected_currency
            or text(destination.get("calculation_fingerprint"))
            != expected_fingerprint
        ):
            raise BusinessCapabilityError(
                "PRICING_RESULT_INVALID",
                f"销售目标 {current['site_id']} 的核价模式或币种无效。",
            )
        try:
            amount_valid = float(text(selected_money.get("amount"))) > 0
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


def _persist_sales_target_selection(
    *,
    draft: dict[str, Any],
    target: dict[str, Any],
    selected_sales_targets: list[dict[str, str]],
    target_draft_id: str,
    platform: str,
    site: str,
    product_store: ProductCapabilityStore,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """先单独保存用户选择，让 Store 清除与旧目的地绑定的定价与预检。"""

    selected_draft = merge_target_listing_into_draft(
        draft,
        target,
        {"sites_to_sell": selected_sales_targets},
    )
    saved_selection, selection_error, _selection_status = (
        product_store.save_draft_detail(selected_draft)
    )
    raise_store_error(
        selection_error,
        default_code="SALES_TARGET_PERSIST_FAILED",
        default_message="销售国家与物流方式保存失败。",
    )
    saved_selection_draft = (
        saved_selection.get("draft")
        if isinstance(saved_selection.get("draft"), dict)
        else {}
    )
    if text(saved_selection_draft.get("draft_id")) != target_draft_id:
        raise BusinessCapabilityError(
            "SALES_TARGET_PERSIST_INCOMPLETE",
            "销售目标保存后无法验证稳定目标草稿。",
        )
    reloaded_draft, reloaded_product = load_draft(
        product_store,
        target_draft_id,
    )
    reloaded_target = select_target(
        reloaded_draft,
        platform=platform,
        site=site,
    )
    return reloaded_draft, reloaded_product, reloaded_target


def _existing_pricing_is_usable(target_draft: dict[str, Any]) -> bool:
    selected = _selected_pricing_target(target_draft)
    return bool(selected) and _pricing_target_is_usable(target_draft, selected)


def _pricing_payload(
    pricing_input: Mapping[str, Any],
    *,
    product: dict[str, Any],
    draft: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any]:
    raw = deepcopy(dict(pricing_input))
    pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
    stored_common = pricing.get("common") if isinstance(pricing.get("common"), dict) else {}
    product_defaults = (
        product.get("pricing_defaults")
        if isinstance(product.get("pricing_defaults"), dict)
        else {}
    )
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    package = (
        draft.get("package_dimensions")
        if isinstance(draft.get("package_dimensions"), dict)
        else {}
    )
    raw_common = raw.get("common") if isinstance(raw.get("common"), dict) else {}
    common = {
        **deepcopy(product_defaults),
        **deepcopy(stored_common),
        "purchase_cost": text(product.get("cost"))
        or (
            text(source.get("price"))
            if text(source.get("currency")).upper() in {"", "CNY", "RMB"}
            else ""
        ),
        "weight_kg": text(package.get("weight_kg"))
        or text(source.get("weight_kg"))
        or text(product.get("weight_kg")),
        "length_cm": text(package.get("length_cm")),
        "width_cm": text(package.get("width_cm")),
        "height_cm": text(package.get("height_cm")),
        **deepcopy(raw_common),
    }
    key = _target_key(text(target.get("platform")), text(target.get("site")))
    stored_targets = pricing.get("targets") if isinstance(pricing.get("targets"), dict) else {}
    target_input = (
        deepcopy(stored_targets.get(key))
        if isinstance(stored_targets.get(key), dict)
        else {}
    )
    raw_target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
    raw_targets = raw.get("targets") if isinstance(raw.get("targets"), list) else []
    matching_raw = next(
        (
            item
            for item in raw_targets
            if isinstance(item, dict)
            and (
                not text(item.get("platform"))
                or text(item.get("platform")).lower()
                == text(target.get("platform")).lower()
            )
            and (
                not text(item.get("site") or item.get("site_id"))
                or text(item.get("site") or item.get("site_id")).casefold()
                == text(target.get("site")).casefold()
            )
        ),
        {},
    )
    target_input.update(deepcopy(raw_target))
    target_input.update(deepcopy(matching_raw))
    target_input.update(
        {
            "target_key": key,
            "platform": text(target.get("platform")).lower(),
            "site": text(target.get("site")),
            "language": text(target.get("language")),
            "listing_currency": text(target.get("listing_currency")).upper(),
            "currency_fingerprint": text(target.get("currency_fingerprint")),
        }
    )
    if (
        text(target.get("platform")).lower() == "mercadolibre"
        and text(target.get("site")).upper() == "CBT"
    ):
        # 销售国家是核价事实，不接受 pricing_input 覆盖草稿目标。
        target_input["sites_to_sell"] = normalize_mercadolibre_sites_to_sell(
            target.get("sites_to_sell")
        )
    passthrough = {
        key_name: deepcopy(value)
        for key_name, value in raw.items()
        if key_name not in {"common", "target", "targets"}
    }
    return {
        **passthrough,
        "common": common,
        "targets": [target_input],
    }


def prepare_target_pricing(
    *,
    target_draft_id: str,
    target_platform: str,
    site: str = "",
    sales_target: list[str] | tuple[str, ...] = (),
    pricing_input: Mapping[str, Any] | None = None,
    product_store: ProductCapabilityStore,
    pricing_calculator: PricingCalculator = calculate_price,
    store_config_loader: StoreConfigLoader = _load_store_config,
) -> dict[str, Any]:
    """确定性核价并持久化到草稿 ``pricing.targets``；返回生效的定价目标。

    未提供 ``pricing_input`` 且现有定价仍可用时直接复用现有目标（幂等），
    否则必须跑完整确定性核价并把结果落库。只计算不落库不是合法路径。
    """

    safe_input = dict(pricing_input or {})
    draft, product = load_draft(product_store, target_draft_id)
    platform = require_platform(target_platform)
    target = select_target(draft, platform=platform, site=site)
    is_mercadolibre_cbt = (
        platform == "mercadolibre" and text(target.get("site")).upper() == "CBT"
    )
    if sales_target and not is_mercadolibre_cbt:
        raise BusinessCapabilityError(
            "SALES_TARGET_NOT_APPLICABLE",
            "销售国家与物流方式选择只适用于 Mercado Libre CBT 草稿。",
        )
    selected_sales_targets = _sales_targets_with_existing_conditions(
        _sales_targets_from_selectors(sales_target),
        target.get("sites_to_sell"),
    )
    sales_target_changed = bool(sales_target) and (
        mercadolibre_sales_operation_keys(selected_sales_targets)
        != mercadolibre_sales_operation_keys(target.get("sites_to_sell"))
    )
    if sales_target:
        target = {**target, "sites_to_sell": selected_sales_targets}
    target_projection = draft_for_publish_target(draft, target)
    if not safe_input:
        selected = _selected_pricing_target(target_projection)
        if selected and _pricing_target_is_usable(target_projection, selected):
            if not is_mercadolibre_cbt:
                return deepcopy(selected)
            current_store_config = store_config_loader()
            if (
                _current_store_currency_context_matches(
                    target_projection,
                    selected,
                    store_config=current_store_config,
                )
                and _current_mercadolibre_pricing_context_matches(
                    target_projection,
                    selected,
                    store_config=current_store_config,
                )
            ):
                return deepcopy(selected)
    payload = _pricing_payload(
        safe_input,
        product=product,
        draft=target_projection,
        target=target,
    )
    try:
        result = pricing_calculator(payload)
    except (BusinessCapabilityError, CapabilityInputRequired):
        raise
    except Exception as exc:
        raise BusinessCapabilityError(
            "PRICING_CALCULATION_FAILED",
            f"确定性核价执行失败：{exc}",
            retryable=True,
        ) from exc
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    first_error = errors[0] if errors and isinstance(errors[0], Mapping) else {}
    error_field = text(first_error.get("field")) or "pricing_input"
    error_code = text(result.get("error_code"))
    sales_target_error = error_field == "sites_to_sell" or error_field.startswith(
        "sites_to_sell["
    )
    # 只要目标契约已通过，就立即保存用户的明确选择。这样后续即使还缺成本或
    # 物流报价，草稿也保留选择，并由 ProductStore 安全清除旧定价/预检。
    target_contract_passed = bool(result.get("ok")) or bool(
        errors
        and not sales_target_error
        and error_code != MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED
    )
    if sales_target_changed and target_contract_passed:
        draft, product, target = _persist_sales_target_selection(
            draft=draft,
            target=target,
            selected_sales_targets=selected_sales_targets,
            target_draft_id=target_draft_id,
            platform=platform,
            site=site,
            product_store=product_store,
        )
    if not bool(result.get("ok")):
        if errors:
            field = error_field
            message = text(first_error.get("message")) or "核价资料不完整。"
            if error_code == MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED:
                raise BusinessCapabilityError(error_code, message)
            sales_target_options = [
                text(option)
                for option in (
                    result.get("sales_target_options")
                    if isinstance(result.get("sales_target_options"), list)
                    else []
                )
                if text(option)
            ]
            if field == "sites_to_sell" or field.startswith("sites_to_sell["):
                if not sales_target_options:
                    raise BusinessCapabilityError(
                        error_code or "MERCADOLIBRE_SALES_TARGET_UNAVAILABLE",
                        message,
                    )
                raise CapabilityInputRequired(
                    error_code or "MERCADOLIBRE_SALES_TARGET_REQUIRED",
                    message,
                    key="sales_target",
                    label="销售国家与物流方式",
                    reason=(
                        "请选择一个或多个当前账号已开通的站点与物流组合；"
                        "同一销售市场只能选择一种物流方式。"
                    ),
                    options=sales_target_options,
                    input_type="multi_select",
                    input_owner="step",
                )
            labels = {
                "cost_cny": "采购成本（CNY）",
                "shipping_amount": "物流报价金额",
                "weight_or_dimensions": "重量或包装尺寸",
                "listing_currency": "发布币种",
                "currency_rate": "目标币种汇率",
                "usd_cny_rate": "USD/CNY 汇率",
                "manual_price": "手动售价",
            }
            raise CapabilityInputRequired(
                "PRICING_INPUT_REQUIRED",
                message,
                key=field,
                label=labels.get(field, field),
                reason="请补充核价资料后继续同一步骤。",
                input_owner="pricing_input",
            )
        raise BusinessCapabilityError(
            "PRICING_CALCULATION_FAILED",
            text(result.get("error")) or "确定性核价失败。",
            retryable=True,
        )
    results = result.get("results") if isinstance(result.get("results"), list) else []
    pricing_target = next(
        (item for item in results if isinstance(item, dict) and bool(item.get("ok"))),
        {},
    )
    if not pricing_target or not text(
        (pricing_target.get("applied_price") or {}).get("amount")
        if isinstance(pricing_target.get("applied_price"), dict)
        else ""
    ):
        raise BusinessCapabilityError(
            "PRICING_RESULT_INVALID",
            "核价完成但没有返回可验证的目标售价。",
        )
    if is_mercadolibre_cbt:
        applied_sales_targets = _apply_mercadolibre_destination_results(
            target,
            pricing_target,
        )
        target = {**target, "sites_to_sell": applied_sales_targets}
    pricing = deepcopy(
        draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
    )
    key = _target_key(platform, text(target.get("site")))
    stored_targets = pricing.get("targets") if isinstance(pricing.get("targets"), dict) else {}
    pricing.update(
        {
            "platform": platform,
            "common": deepcopy(
                (result.get("input") or {}).get("common")
                if isinstance(result.get("input"), dict)
                else payload.get("common")
            ),
            "targets": {**stored_targets, key: deepcopy(pricing_target)},
            "exchange_rates": deepcopy(result.get("exchange_rates"))
            if isinstance(result.get("exchange_rates"), dict)
            else {},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    updated = (
        merge_target_listing_into_draft(
            draft,
            target,
            {"sites_to_sell": target["sites_to_sell"]},
        )
        if is_mercadolibre_cbt
        else deepcopy(draft)
    )
    updated["pricing"] = pricing
    updated = invalidate_target_publish_preparation(
        product_store=product_store,
        product=product,
        draft=updated,
        target=target,
    )
    saved, error, _status = product_store.save_draft_detail(updated)
    raise_store_error(
        error,
        default_code="PRICING_PERSIST_FAILED",
        default_message="核价结果保存失败。",
    )
    saved_draft = saved.get("draft") if isinstance(saved.get("draft"), dict) else {}
    if text(saved_draft.get("draft_id")) != target_draft_id:
        raise BusinessCapabilityError(
            "PRICING_PERSIST_INCOMPLETE",
            "核价结果保存后无法验证稳定目标草稿。",
        )
    saved_pricing = (
        saved_draft.get("pricing")
        if isinstance(saved_draft.get("pricing"), dict)
        else {}
    )
    saved_targets = (
        saved_pricing.get("targets")
        if isinstance(saved_pricing.get("targets"), dict)
        else {}
    )
    persisted = saved_targets.get(key) if isinstance(saved_targets.get(key), dict) else {}
    return deepcopy(persisted) if persisted else deepcopy(pricing_target)


__all__ = ["PricingCalculator", "prepare_target_pricing"]
