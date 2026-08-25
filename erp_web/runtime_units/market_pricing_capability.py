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

from erp_web.product_model import normalize_mercadolibre_sites_to_sell
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
from erp_web.services.mercadolibre_target_contract import (
    MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED,
)


PricingCalculator = Callable[[dict[str, Any]], dict[str, Any]]


def _target_key(platform: str, site: str) -> str:
    return f"{text(platform).lower()}:{text(site).lower()}"


def _sales_target_from_selector(selector: str) -> list[dict[str, str]]:
    """把受信下拉值转为草稿持久化使用的规范销售目标。"""

    value = text(selector)
    if not value or value.count(":") != 1:
        return []
    site_id, logistic_type = value.split(":", 1)
    if not text(site_id) or not text(logistic_type):
        return []
    return normalize_mercadolibre_sites_to_sell(
        [
            {
                "site_id": site_id,
                "logistic_type": logistic_type,
            }
        ]
    )


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
        return bool(current_targets) and normalize_mercadolibre_sites_to_sell(
            basis.get("sites_to_sell")
        ) == current_targets
    return True


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
    sales_target: str = "",
    pricing_input: Mapping[str, Any] | None = None,
    product_store: ProductCapabilityStore,
    pricing_calculator: PricingCalculator = calculate_price,
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
    selected_sales_targets = _sales_target_from_selector(sales_target)
    sales_target_changed = bool(sales_target) and (
        selected_sales_targets
        != normalize_mercadolibre_sites_to_sell(target.get("sites_to_sell"))
    )
    if sales_target:
        target = {**target, "sites_to_sell": selected_sales_targets}
    target_projection = draft_for_publish_target(draft, target)
    if not safe_input:
        selected = _selected_pricing_target(target_projection)
        if selected and _pricing_target_is_usable(target_projection, selected):
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
                    reason="请选择一个当前账号已开通的站点与物流组合。",
                    options=sales_target_options,
                    input_type="select",
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
    updated = deepcopy(draft)
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
