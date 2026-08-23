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

from erp_web.runtime_units.draft_publish_context import draft_for_publish_target
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


PricingCalculator = Callable[[dict[str, Any]], dict[str, Any]]


def _target_key(platform: str, site: str) -> str:
    return f"{text(platform).lower()}:{text(site).lower()}"


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
    return bool(
        amount_valid
        and text(applied.get("currency")).upper()
        == text(target_draft.get("listing_currency")).upper()
        and basis
        and text(selected.get("calculation_fingerprint"))
    )


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
    if not bool(result.get("ok")):
        if errors:
            first = errors[0] if isinstance(errors[0], Mapping) else {}
            field = text(first.get("field")) or "pricing_input"
            message = text(first.get("message")) or "核价资料不完整。"
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
