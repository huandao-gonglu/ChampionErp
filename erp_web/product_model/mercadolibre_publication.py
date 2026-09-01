from __future__ import annotations

"""Mercado Libre 双刊登模型的规范化与响应合并。"""

from copy import deepcopy
from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def canonicalize_mercadolibre_siteless_user_product_id(value: Any) -> str:
    """统一官方等价的 ``U{id}`` / ``CBTU{id}`` Siteless 身份。"""

    normalized = _text(value).upper()
    if normalized.startswith("CBTU"):
        return normalized[3:]
    return normalized


def _market_key(value: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _text(value.get("site_id")).upper(),
        _text(value.get("seller_id")),
        _text(value.get("logistic_type")).lower(),
    )


def normalize_mercadolibre_market_publication(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    marketplace = (
        raw.get("marketplace")
        if isinstance(raw.get("marketplace"), dict)
        else {}
    )
    market: dict[str, Any] = {
        "site_id": _text(raw.get("site_id")).upper(),
        "seller_id": _text(raw.get("seller_id")),
        "logistic_type": _text(raw.get("logistic_type")).lower(),
        "item_id": _text(raw.get("item_id") or raw.get("id")),
        "user_product_id": _text(
            raw.get("user_product_id")
            or raw.get("local_user_product_id")
        ),
        "status": _text(raw.get("status")),
        "currency_id": _text(raw.get("currency_id")).upper(),
        "listing_type_id": _text(
            raw.get("listing_type_id")
            or raw.get("listing_type")
        ),
        "updated_at": _text(raw.get("updated_at")),
    }
    if raw.get("price") not in (None, ""):
        market["price"] = raw.get("price")
    net_proceeds = raw.get("net_proceeds")
    if net_proceeds not in (None, ""):
        market["net_proceeds"] = net_proceeds
    if raw.get("free_shipping") not in (None, ""):
        market["free_shipping"] = bool(raw.get("free_shipping"))
    elif marketplace.get("free_shipping") not in (None, ""):
        market["free_shipping"] = bool(marketplace.get("free_shipping"))
    sale_terms = raw.get("sale_terms") if isinstance(raw.get("sale_terms"), list) else None
    if sale_terms is not None:
        market["sale_terms"] = deepcopy(sale_terms)
    description = (
        raw.get("description")
        if isinstance(raw.get("description"), dict)
        else None
    )
    if description is not None:
        market["description"] = deepcopy(description)
    error = raw.get("error")
    if error in (None, "") and raw.get("errors") not in (None, "", []):
        error = raw.get("errors")
    if error not in (None, "", [], {}):
        market["error"] = deepcopy(error)
    if isinstance(raw.get("last_operation"), dict):
        market["last_operation"] = deepcopy(raw["last_operation"])
    return {
        key: item
        for key, item in market.items()
        if item not in (None, "", [], {})
        or (key == "sale_terms" and sale_terms is not None)
        or (key == "description" and description is not None)
    }


def normalize_mercadolibre_publication(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    model = _text(raw.get("model"))
    if model not in {"user_products", "traditional_global_items"}:
        # publication 是远端身份，模型缺失时不能猜测，否则可能把同一 ID
        # 发送到错误的写接口。Demo 阶段直接丢弃这类旧数据，不保留兼容路径。
        return {}
    raw_markets = raw.get("markets") if isinstance(raw.get("markets"), list) else []
    markets: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw_markets:
        market = normalize_mercadolibre_market_publication(item)
        key = _market_key(market)
        if not key[0] or key in seen:
            continue
        seen.add(key)
        markets.append(market)
    markets.sort(
        key=lambda item: (
            _text(item.get("site_id")),
            _text(item.get("logistic_type")),
            _text(item.get("seller_id")),
        )
    )
    publication: dict[str, Any] = {
        "model": model,
        "account_user_id": _text(raw.get("account_user_id")),
        "parent_item_id": _text(
            raw.get("parent_item_id")
            or raw.get("item_id")
        ),
        "parent_user_product_id": _text(raw.get("parent_user_product_id")),
        "siteless_user_product_id": _text(raw.get("siteless_user_product_id")),
        "siteless_family_id": _text(raw.get("siteless_family_id")),
        "seller_id": _text(raw.get("seller_id")),
        "family_name": _text(raw.get("family_name")),
        "status": _text(raw.get("status")),
        "markets": markets,
        "confirmed_payload": deepcopy(
            raw.get("confirmed_payload")
            if isinstance(raw.get("confirmed_payload"), dict)
            else {}
        ),
        "error": deepcopy(
            raw.get("error")
            if raw.get("error") not in (None, "", [], {})
            else raw.get("errors")
            if raw.get("errors") not in (None, "", [], {})
            else None
        ),
        "last_operation": deepcopy(
            raw.get("last_operation")
            if isinstance(raw.get("last_operation"), dict)
            else {}
        ),
        "updated_at": _text(raw.get("updated_at")),
    }
    has_parent_identity = bool(publication["parent_item_id"])
    has_siteless_identity = bool(publication["siteless_user_product_id"])
    if model == "user_products" and not has_siteless_identity and not markets:
        return {}
    # 传统创建前会用无远端 ID 的 markets 保存本次授权 binding 上下文；只有
    # 这些显式 model 的 transient seed 可以没有 parent_item_id。若 markets
    # 已含远端 ID，发布校验会要求 parent_item_id，避免重复 POST。
    if model == "traditional_global_items" and not has_parent_identity and not markets:
        return {}
    publication["siteless_user_product_id"] = (
        canonicalize_mercadolibre_siteless_user_product_id(
            publication["siteless_user_product_id"]
        )
    )
    return {
        key: item
        for key, item in publication.items()
        if item not in (None, "", [], {})
        or key in {"model", "markets", "confirmed_payload"}
    }


def mercadolibre_publication_from_response(
    response: Any,
    *,
    existing: Any = None,
    family_name: str = "",
    requested_sites: Any = None,
    updated_at: str = "",
    listing_model: str = "user_products",
) -> dict[str, Any]:
    """把创建、加市场或更新响应合并成一个稳定的 publication 快照。"""

    if isinstance(response, list):
        body = response[0] if response and isinstance(response[0], dict) else {}
    else:
        body = response if isinstance(response, dict) else {}
    previous = normalize_mercadolibre_publication(existing)
    existing_account_user_id = _text(
        existing.get("account_user_id")
        if isinstance(existing, dict)
        else ""
    )
    requested = [
        item
        for item in requested_sites
        if isinstance(item, dict)
    ] if isinstance(requested_sites, list) else []
    requested_by_operation = {
        (
            _text(item.get("site_id") or item.get("siteId")).upper(),
            _text(
                item.get("logistic_type") or item.get("logisticType")
            ).lower(),
        ): item
        for item in requested
        if _text(item.get("site_id") or item.get("siteId"))
    }
    requested_by_site: dict[str, list[dict[str, Any]]] = {}
    for item in requested:
        site_id = _text(item.get("site_id") or item.get("siteId")).upper()
        if site_id:
            requested_by_site.setdefault(site_id, []).append(item)

    previous_markets = {
        _market_key(item): dict(item)
        for item in previous.get("markets", [])
        if isinstance(item, dict)
    }
    previous_key_by_item_id = {
        _text(item.get("item_id")): key
        for key, item in previous_markets.items()
        if _text(item.get("item_id"))
    }
    raw_markets = (
        body.get("site_items")
        if isinstance(body.get("site_items"), list)
        else body.get("listing_sites")
        if isinstance(body.get("listing_sites"), list)
        else []
    )
    for raw in raw_markets:
        if not isinstance(raw, dict):
            continue
        response_item_id = _text(
            raw.get("item_id")
            or raw.get("listing_id")
            or raw.get("id")
        )
        matched_previous_key = previous_key_by_item_id.get(response_item_id)
        site_id = _text(raw.get("site_id") or raw.get("siteId")).upper()
        if not site_id and matched_previous_key is not None:
            # Global Update 的 listing_sites 响应通常只回传 listing id、
            # success 与 errors；必须用已持久化的 Item ID 找回所属市场，
            # 否则会静默丢掉市场级失败。
            site_id = matched_previous_key[0]
        previous_target = (
            previous_markets.get(matched_previous_key, {})
            if matched_previous_key is not None
            else {}
        )
        logistic_type = _text(
            raw.get("logistic_type")
            or raw.get("logisticType")
            or previous_target.get("logistic_type")
        ).lower()
        requested_target = requested_by_operation.get(
            (site_id, logistic_type),
            {},
        )
        if not requested_target:
            same_site_targets = requested_by_site.get(site_id, [])
            if len(same_site_targets) == 1:
                requested_target = same_site_targets[0]
        raw_errors = raw.get("errors")
        response_failed = (
            raw.get("success") is False
            or raw.get("error") not in (None, "", [], {})
            or raw_errors not in (None, "", [], {})
        )
        response_pending = bool(_text(raw.get("task_id")))
        merge_raw = (
            {
                key: deepcopy(value)
                for key, value in raw.items()
                if key
                in {
                    "id",
                    "item_id",
                    "listing_id",
                    "local_user_product_id",
                    "logistic_type",
                    "seller_id",
                    "site_id",
                    "task_id",
                    "user_product_id",
                }
            }
            if response_pending
            else raw
        )
        requested_context = (
            {
                key: value
                for key, value in requested_target.items()
                if key in {"site_id", "seller_id", "logistic_type"}
            }
            if response_failed or response_pending
            else requested_target
        )
        raw_marketplace = (
            merge_raw.get("marketplace")
            if isinstance(merge_raw.get("marketplace"), dict)
            else {}
        )
        requested_free_shipping = requested_context.get("free_shipping")
        response_free_shipping = (
            merge_raw.get("free_shipping")
            if merge_raw.get("free_shipping") not in (None, "")
            else merge_raw.get("freeShipping")
            if merge_raw.get("freeShipping") not in (None, "")
            else raw_marketplace.get("free_shipping")
            if raw_marketplace.get("free_shipping") not in (None, "")
            else raw_marketplace.get("freeShipping")
            if raw_marketplace.get("freeShipping") not in (None, "")
            else requested_free_shipping
        )
        enriched = {
            **previous_target,
            **requested_context,
            **merge_raw,
            "site_id": site_id,
            "item_id": response_item_id,
            "logistic_type": merge_raw.get("logistic_type")
            or merge_raw.get("logisticType")
            or requested_context.get("logistic_type"),
            "listing_type_id": merge_raw.get("listing_type_id")
            or merge_raw.get("listingTypeId")
            or merge_raw.get("listing_type")
            or requested_context.get("listing_type_id"),
            "price": merge_raw.get("price")
            if merge_raw.get("price") not in (None, "")
            else requested_context.get("price"),
            "net_proceeds": merge_raw.get("net_proceeds")
            if merge_raw.get("net_proceeds") not in (None, "")
            else merge_raw.get("netProceeds")
            if merge_raw.get("netProceeds") not in (None, "")
            else requested_context.get("net_proceeds"),
            "status": merge_raw.get("status") or "",
            "updated_at": updated_at,
        }
        if response_free_shipping not in (None, ""):
            enriched["free_shipping"] = bool(response_free_shipping)
        if isinstance(merge_raw.get("sale_terms"), list):
            enriched["sale_terms"] = merge_raw["sale_terms"]
        elif isinstance(merge_raw.get("saleTerms"), list):
            enriched["sale_terms"] = merge_raw["saleTerms"]
        elif isinstance(requested_context.get("sale_terms"), list):
            enriched["sale_terms"] = requested_context["sale_terms"]
        if isinstance(merge_raw.get("description"), dict):
            enriched["description"] = merge_raw["description"]
        elif isinstance(requested_context.get("description"), dict):
            enriched["description"] = requested_context["description"]
        market = normalize_mercadolibre_market_publication(enriched)
        key = _market_key(market)
        if not key[0]:
            continue
        if key not in previous_markets:
            # 部分 API 响应不回传 logistic/seller；此时优先覆盖同一 site 的
            # 既有 operation，避免产生一个信息更少的重复节点。
            same_site_key = next(
                (candidate for candidate in previous_markets if candidate[0] == key[0]),
                None,
            )
            if same_site_key is not None:
                key = same_site_key
        merged_market = {
            **previous_markets.get(key, {}),
            **market,
        }
        succeeded = (
            not response_pending
            and raw.get("success") is not False
            and raw.get("error") in (None, "", [], {})
            and raw_errors in (None, "", [], {})
            and bool(
                merged_market.get("item_id")
                or merged_market.get("user_product_id")
            )
        )
        if succeeded:
            merged_market.pop("error", None)
            merged_market["last_operation"] = {
                "status": "succeeded",
                "updated_at": updated_at,
            }
            if _text(merged_market.get("status")).lower() in {
                "",
                "failed",
                "error",
            }:
                merged_market["status"] = "active"
        elif response_failed:
            merged_market["last_operation"] = {
                "status": "failed",
                "error": deepcopy(
                    raw.get("error")
                    if raw.get("error") not in (None, "", [], {})
                    else raw_errors
                ),
                "updated_at": updated_at,
            }
        elif response_pending:
            merged_market["last_operation"] = {
                "status": "pending",
                "updated_at": updated_at,
            }
        previous_markets[key] = merged_market

    model = str(listing_model or "").strip() or "user_products"
    combined = {
        **previous,
        "model": model,
        "account_user_id": body.get("account_user_id")
        or previous.get("account_user_id")
        or existing_account_user_id
        or body.get("seller_id"),
        "parent_item_id": body.get("item_id")
        or body.get("parent_item_id")
        or (
            body.get("id")
            if model == "traditional_global_items"
            else ""
        )
        or previous.get("parent_item_id"),
        "parent_user_product_id": body.get("parent_user_product_id")
        or previous.get("parent_user_product_id"),
        "siteless_user_product_id": body.get("siteless_user_product_id")
        or (
            body.get("id")
            if model == "user_products"
            else ""
        )
        or previous.get("siteless_user_product_id"),
        "siteless_family_id": body.get("siteless_family_id")
        or previous.get("siteless_family_id"),
        "seller_id": body.get("seller_id") or previous.get("seller_id"),
        "family_name": family_name
        or body.get("family_name")
        or previous.get("family_name"),
        "status": body.get("status") or previous.get("status"),
        "markets": list(previous_markets.values()),
        "updated_at": updated_at or previous.get("updated_at"),
    }
    has_market_failure = any(
        isinstance(item, dict)
        and (
            item.get("error") not in (None, "", [], {})
            or _text(
                (item.get("last_operation") or {}).get("status")
                if isinstance(item.get("last_operation"), dict)
                else ""
            ).lower()
            in {"failed", "error"}
            or _text(item.get("status")).lower() in {"failed", "error"}
        )
        for item in combined["markets"]
    )
    has_publication_failure = (
        previous.get("error") not in (None, "", [], {})
        or _text(
            (previous.get("last_operation") or {}).get("status")
            if isinstance(previous.get("last_operation"), dict)
            else ""
        ).lower()
        in {"failed", "error"}
    )
    if has_market_failure or has_publication_failure:
        combined["status"] = "partial"
    elif combined.get("siteless_user_product_id") or combined.get("parent_item_id"):
        combined["status"] = _text(body.get("status")) or "active"
    return normalize_mercadolibre_publication(combined)


def mercadolibre_publication_has_failures(value: Any) -> bool:
    publication = normalize_mercadolibre_publication(value)
    return (
        publication.get("error") not in (None, "", [], {})
        or _text(
            (publication.get("last_operation") or {}).get("status")
            if isinstance(publication.get("last_operation"), dict)
            else ""
        ).lower()
        in {"failed", "error"}
        or _text(publication.get("status")).lower()
        in {
            "partial",
            "failed",
            "error",
        }
    ) or any(
        isinstance(item, dict)
        and (
            item.get("error") not in (None, "", [], {})
            or _text(
                (item.get("last_operation") or {}).get("status")
                if isinstance(item.get("last_operation"), dict)
                else ""
            ).lower()
            in {"failed", "error"}
            or _text(item.get("status")).lower() in {"failed", "error"}
        )
        for item in publication.get("markets", [])
    )


__all__ = [
    "canonicalize_mercadolibre_siteless_user_product_id",
    "mercadolibre_publication_from_response",
    "mercadolibre_publication_has_failures",
    "normalize_mercadolibre_market_publication",
    "normalize_mercadolibre_publication",
]
