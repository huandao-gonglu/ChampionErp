from __future__ import annotations

"""Mercado Libre 父刊登与销售市场的确定性预检投影。"""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from erp_web.services.mercadolibre_publish_error_codes import (
    MERCADOLIBRE_PACKAGE_CARRIER_LIMIT_EXCEEDED,
)
from erp_web.services.mercadolibre_target_contract import (
    MERCADOLIBRE_CN_INTERNATIONAL_DROPSHIPPING_BUSINESS_MODEL,
    mercadolibre_binding_for_target,
)

MERCADOLIBRE_PACKAGE_DIMENSION_BELOW_MINIMUM = (
    "MERCADOLIBRE_PACKAGE_DIMENSION_BELOW_MINIMUM"
)
MERCADOLIBRE_PACKAGE_WEIGHT_BELOW_MINIMUM = (
    "MERCADOLIBRE_PACKAGE_WEIGHT_BELOW_MINIMUM"
)
MERCADOLIBRE_PACKAGE_VOLUME_BELOW_MINIMUM = (
    "MERCADOLIBRE_PACKAGE_VOLUME_BELOW_MINIMUM"
)

# Global Items 官方包装下限：三边均不小于 3cm、重量不小于 50g，且
# width * height * length / 5000 必须大于 0.02。
# https://global-selling.mercadolibre.com/devsite/manage-questions-answers-global-selling/sync-and-modify-listings-gs
_MINIMUM_PACKAGE_SIDE_CM = Decimal("3")
_MINIMUM_PACKAGE_WEIGHT_KG = Decimal("0.05")
_MINIMUM_PACKAGE_VOLUME_FACTOR = Decimal("0.02")
_PACKAGE_VOLUME_DIVISOR = Decimal("5000")
_TARGET_FIELD_PATTERN = re.compile(r"^sites_to_sell\[(\d+)\](?=\.|$)")
_MARKET_SCOPE_KEY = "_precheck_scope"


# 当前中国始发 Cainiao 路线的官方限制：
# https://global-selling.mercadolibre.com/knowledge-hub/37370
# 旧的 DHL/MailAmericas PDF 属于不同承运商上下文，不与当前路线并存。
_CAINIAO_MAX_LENGTH_CM = Decimal("60")
_CAINIAO_MAX_WIDTH_CM = Decimal("40")
_CAINIAO_MAX_HEIGHT_CM = Decimal("35")
_CAINIAO_MAX_SIDE_SUM_CM = Decimal("135")
_CAINIAO_MAX_WEIGHT_KG_BY_SITE = {
    "MLM": Decimal("15"),
    "MLC": Decimal("15"),
    "MCO": Decimal("15"),
    "MLB": Decimal("15"),
    "MLA": Decimal("15"),
    "MLU": Decimal("20"),
}


def _precheck_item(
    code: str,
    field: str,
    message: str,
    severity: str,
    next_action: str,
    *,
    market_index: int | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "code": code,
        "field": field,
        "message": message,
        "severity": severity,
        "next_action": next_action,
    }
    if market_index is not None:
        issue[_MARKET_SCOPE_KEY] = {"kind": "market", "index": market_index}
    return issue


def _positive_decimal(value: Any) -> tuple[str, Decimal | None]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "missing", None
    try:
        parsed = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return "invalid", None
    if not parsed.is_finite() or parsed <= 0:
        return "invalid", None
    return "valid", parsed


def mercadolibre_parent_package_errors(
    package_dimensions: Any,
) -> list[dict[str, Any]]:
    """返回父刊登包装的确定性错误，不叠加缺失/非法与下限错误。"""

    package = package_dimensions if isinstance(package_dimensions, dict) else {}
    errors: list[dict[str, Any]] = []
    dimensions: dict[str, Decimal] = {}
    dimension_labels = {
        "length_cm": "物流包装长度",
        "width_cm": "物流包装宽度",
        "height_cm": "物流包装高度",
    }
    for field, label in dimension_labels.items():
        state, value = _positive_decimal(package.get(field))
        issue_field = f"package_dimensions.{field}"
        if state != "valid" or value is None:
            errors.append(
                _precheck_item(
                    "PACKAGE_DIMENSIONS_MISSING",
                    issue_field,
                    f"{label}缺失或无效",
                    "error",
                    "填写实际发货包装尺寸后重新核价。",
                )
            )
            continue
        dimensions[field] = value
        if value < _MINIMUM_PACKAGE_SIDE_CM:
            errors.append(
                _precheck_item(
                    MERCADOLIBRE_PACKAGE_DIMENSION_BELOW_MINIMUM,
                    issue_field,
                    f"{label}不能小于 3cm",
                    "error",
                    "修正发货包装尺寸后重新核价。",
                )
            )

    weight_state, weight = _positive_decimal(package.get("weight_kg"))
    if weight_state != "valid" or weight is None:
        errors.append(
            _precheck_item(
                "WEIGHT_MISSING",
                "package_dimensions.weight_kg",
                "物流包装重量缺失或无效",
                "error",
                "填写实际发货包装重量后重新核价。",
            )
        )
    elif weight < _MINIMUM_PACKAGE_WEIGHT_KG:
        errors.append(
            _precheck_item(
                MERCADOLIBRE_PACKAGE_WEIGHT_BELOW_MINIMUM,
                "package_dimensions.weight_kg",
                "物流包装重量不能小于 0.05kg",
                "error",
                "修正发货包装重量后重新核价。",
            )
        )

    if len(dimensions) == 3:
        volume_factor = (
            dimensions["length_cm"]
            * dimensions["width_cm"]
            * dimensions["height_cm"]
            / _PACKAGE_VOLUME_DIVISOR
        )
        if volume_factor <= _MINIMUM_PACKAGE_VOLUME_FACTOR:
            errors.append(
                _precheck_item(
                    MERCADOLIBRE_PACKAGE_VOLUME_BELOW_MINIMUM,
                    "package_dimensions",
                    "物流包装体积过小：长×宽×高÷5000 必须大于 0.02",
                    "error",
                    "修正发货包装尺寸后重新核价。",
                )
            )
    return errors


def _valid_package_measurements(
    package_dimensions: Any,
) -> dict[str, Decimal] | None:
    package = package_dimensions if isinstance(package_dimensions, dict) else {}
    measurements: dict[str, Decimal] = {}
    for field in ("length_cm", "width_cm", "height_cm", "weight_kg"):
        state, value = _positive_decimal(package.get(field))
        if state != "valid" or value is None:
            return None
        measurements[field] = value
    return measurements


def _decimal_text(value: Decimal) -> str:
    normalized = format(value.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _cainiao_package_violations(
    measurements: dict[str, Decimal],
    *,
    max_weight_kg: Decimal,
) -> list[str]:
    side_sum = (
        measurements["length_cm"]
        + measurements["width_cm"]
        + measurements["height_cm"]
    )
    checks = (
        ("长", measurements["length_cm"], _CAINIAO_MAX_LENGTH_CM, "cm"),
        ("宽", measurements["width_cm"], _CAINIAO_MAX_WIDTH_CM, "cm"),
        ("高", measurements["height_cm"], _CAINIAO_MAX_HEIGHT_CM, "cm"),
        ("三边合计", side_sum, _CAINIAO_MAX_SIDE_SUM_CM, "cm"),
        ("包装重量", measurements["weight_kg"], max_weight_kg, "kg"),
    )
    return [
        (
            f"{label} {_decimal_text(actual)}{unit}"
            f"（上限 {_decimal_text(limit)}{unit}）"
        )
        for label, actual, limit, unit in checks
        if actual > limit
    ]


def mercadolibre_market_rule_errors(
    raw_sites_to_sell: Any,
    package_dimensions: Any,
    *,
    marketplace_bindings: Any,
) -> list[dict[str, Any]]:
    """返回当前授权 Cainiao operation 的公开包装限制。"""

    raw_markets = raw_sites_to_sell if isinstance(raw_sites_to_sell, list) else []
    measurements = _valid_package_measurements(package_dimensions)
    errors: list[dict[str, Any]] = []

    for index, raw_market in enumerate(raw_markets):
        site_id, logistic_type = _raw_market_identity(raw_market)
        if not site_id or logistic_type != "remote":
            continue

        binding = mercadolibre_binding_for_target(
            {"site_id": site_id, "logistic_type": logistic_type},
            marketplace_bindings,
        )
        is_cn_cainiao_route = bool(
            binding
            and str(binding.get("business_model") or "").strip().casefold()
            == MERCADOLIBRE_CN_INTERNATIONAL_DROPSHIPPING_BUSINESS_MODEL.casefold()
        )
        max_weight_kg = _CAINIAO_MAX_WEIGHT_KG_BY_SITE.get(site_id)
        violations = (
            _cainiao_package_violations(
                measurements,
                max_weight_kg=max_weight_kg,
            )
            if measurements is not None
            and is_cn_cainiao_route
            and max_weight_kg is not None
            else []
        )
        if violations:
            errors.append(
                _precheck_item(
                    MERCADOLIBRE_PACKAGE_CARRIER_LIMIT_EXCEEDED,
                    "package_dimensions",
                    f"Cainiao 物流包装超限：{'、'.join(violations)}",
                    "error",
                    "修正发货包装后重新核价。",
                    market_index=index,
                )
            )
    return errors


def _target_index(issue: dict[str, Any], market_count: int) -> int | None:
    scope = issue.get(_MARKET_SCOPE_KEY)
    if isinstance(scope, dict) and scope.get("kind") == "market":
        try:
            scoped_index = int(scope.get("index"))
        except (TypeError, ValueError):
            scoped_index = -1
        return scoped_index if 0 <= scoped_index < market_count else None
    match = _TARGET_FIELD_PATTERN.match(str(issue.get("field") or "").strip())
    if match is None:
        return None
    index = int(match.group(1))
    return index if index < market_count else None


def _public_issue(issue: dict[str, Any]) -> dict[str, str]:
    """移除只用于后端投影的 scope 元数据。"""

    return {
        key: str(issue.get(key) or "")
        for key in ("code", "field", "message", "severity", "next_action")
    }


def mercadolibre_public_precheck_issues(
    issues: list[dict[str, Any]],
) -> list[dict[str, str]]:
    return [_public_issue(issue) for issue in issues]


def _raw_market_identity(raw: Any) -> tuple[str, str]:
    if not isinstance(raw, dict):
        return "", ""
    site_id = str(raw.get("site_id") or "").strip().upper()
    logistic_type = str(raw.get("logistic_type") or "").strip().lower()
    return site_id, logistic_type


def mercadolibre_selected_raw_sites_to_sell(product: Any) -> list[Any]:
    """在商品规范化前读取当前 CBT 目标的原始市场顺序。"""

    raw_product = product if isinstance(product, dict) else {}
    drafts = (
        raw_product.get("drafts")
        if isinstance(raw_product.get("drafts"), dict)
        else {}
    )
    draft = (
        drafts.get("mercadolibre")
        if isinstance(drafts.get("mercadolibre"), dict)
        else {}
    )
    raw_targets = (
        draft.get("target_sites")
        if isinstance(draft.get("target_sites"), list)
        else []
    )
    site_id = str(draft.get("site") or draft.get("site_id") or "").strip()
    selected_target = next(
        (
            target
            for target in raw_targets
            if isinstance(target, dict)
            and str(target.get("platform") or "mercadolibre").strip().lower()
            == "mercadolibre"
            and (
                not site_id
                or str(target.get("site") or target.get("site_id") or "")
                .strip()
                .casefold()
                == site_id.casefold()
            )
        ),
        None,
    )
    source = selected_target if selected_target is not None else draft
    raw_markets = (
        source.get("sites_to_sell")
        if isinstance(source.get("sites_to_sell"), list)
        else []
    )
    return deepcopy(raw_markets)


def _section_status(errors: list[dict[str, str]]) -> str:
    return "blocked" if errors else "passed"


def build_mercadolibre_market_precheck(
    raw_sites_to_sell: Any,
    *,
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    """按原始销售目标顺序，将扁平预检结果投影为父级与市场级结果。"""

    raw_markets = raw_sites_to_sell if isinstance(raw_sites_to_sell, list) else []
    market_errors: list[list[dict[str, str]]] = [
        [] for _ in raw_markets
    ]
    market_warnings: list[list[dict[str, str]]] = [
        [] for _ in raw_markets
    ]
    parent_errors: list[dict[str, str]] = []
    parent_warnings: list[dict[str, str]] = []

    for issue in errors:
        index = _target_index(issue, len(raw_markets))
        if index is None:
            parent_errors.append(_public_issue(issue))
        else:
            market_errors[index].append(_public_issue(issue))
    for issue in warnings:
        index = _target_index(issue, len(raw_markets))
        if index is None:
            parent_warnings.append(_public_issue(issue))
        else:
            market_warnings[index].append(_public_issue(issue))

    parent = {
        "ok": not parent_errors,
        "status": _section_status(parent_errors),
        "errors": parent_errors,
        "warnings": parent_warnings,
    }

    markets: list[dict[str, Any]] = []
    for index, raw_market in enumerate(raw_markets):
        site_id, logistic_type = _raw_market_identity(raw_market)
        current_errors = market_errors[index]
        current_warnings = market_warnings[index]
        markets.append(
            {
                "site_id": site_id,
                "logistic_type": logistic_type,
                "ok": not current_errors,
                "status": _section_status(current_errors),
                "errors": current_errors,
                "warnings": current_warnings,
            }
        )
    return {"parent": parent, "markets": markets}


__all__ = [
    "MERCADOLIBRE_PACKAGE_DIMENSION_BELOW_MINIMUM",
    "MERCADOLIBRE_PACKAGE_VOLUME_BELOW_MINIMUM",
    "MERCADOLIBRE_PACKAGE_WEIGHT_BELOW_MINIMUM",
    "build_mercadolibre_market_precheck",
    "mercadolibre_market_rule_errors",
    "mercadolibre_parent_package_errors",
    "mercadolibre_public_precheck_issues",
    "mercadolibre_selected_raw_sites_to_sell",
]
