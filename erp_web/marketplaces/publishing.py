from __future__ import annotations

"""Mercado Libre CBT 两种刊登模型的真实发布 I/O。

User Products 与传统 Global Items 只按授权阶段派生的显式 ``listing_model``
分发；任何远端错误都不会触发模型 fallback，且本模块不提供区域 ``/items``。
"""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from erp_web.marketplace_registry import platform_title_limit
from erp_web.product_model import (
    canonicalize_mercadolibre_siteless_user_product_id,
    mercadolibre_publication_from_response,
    mercadolibre_publication_has_failures,
    normalize_mercadolibre_publication,
)
from erp_web.services.mercadolibre_target_contract import (
    mercadolibre_payload_pricing_contract,
)
from erp_web.services.mercadolibre_listing_model import (
    MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS,
    MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS,
    require_mercadolibre_listing_model,
)

from .config_http import request_json
from .publisher import PublishAdapterError


MERCADOLIBRE_USER_PRODUCT_FAMILIES_ENDPOINT = (
    "https://api.mercadolibre.com/global/user-products/families"
)
MERCADOLIBRE_TRADITIONAL_GLOBAL_ITEMS_ENDPOINT = (
    "https://api.mercadolibre.com/global/items"
)
MERCADOLIBRE_USER_PRODUCT_TASK_ENDPOINT = (
    "https://api.mercadolibre.com/user-products-families/tasks/"
)

_SITE_BY_COUNTRY = {
    "AR": "MLA",
    "BR": "MLB",
    "CL": "MLC",
    "CO": "MCO",
    "MX": "MLM",
}

_NON_UPDATABLE_USER_PRODUCT_FIELDS = frozenset(
    {
        "sites_to_sell",
        "category_id",
        "currency_id",
        "description",
        "sale_terms",
    }
)

_LISTING_SITE_MUTABLE_FIELDS = frozenset(
    {
        "description",
        "free_shipping",
        "listing_type_id",
        "net_proceeds",
        "price",
        "sale_terms",
        "status",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_request_json(
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | list[Any],
    *,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any] | list[Any]:
    """执行 Mercado 写请求；发出后结果不确定时禁止 PublishingBus 重放。"""

    try:
        if extra_headers:
            return request_json(
                method,
                url,
                token,
                payload,
                extra_headers=extra_headers,
            )
        return request_json(method, url, token, payload)
    except PublishAdapterError as exc:
        if not exc.retryable:
            raise
        details = dict(exc.details)
        status_code = int(details.get("http_status") or 0)
        details.update(
            {
                "remote_write_dispatched": True,
                # 420/429 明确表示限流；网络中断与 5xx 则可能已落地。
                "outcome_unknown": status_code not in {420, 429},
            }
        )
        raise PublishAdapterError(
            exc.code,
            str(exc),
            retryable=False,
            details=details,
        ) from exc


def _response_body(response: Any) -> dict[str, Any]:
    if (
        isinstance(response, list)
        and response
        and isinstance(response[0], dict)
    ):
        return dict(response[0])
    return dict(response) if isinstance(response, dict) else {}


def _response_task_ids(response: Any) -> list[str]:
    body = _response_body(response)
    task_ids: list[str] = []

    def add(value: Any) -> None:
        task_id = str(value or "").strip()
        if task_id and task_id not in task_ids:
            task_ids.append(task_id)

    add(body.get("task_id"))
    for key in ("listing_sites", "site_items", "variants"):
        values = body.get(key) if isinstance(body.get(key), list) else []
        for value in values:
            if isinstance(value, dict):
                add(value.get("task_id"))
    return task_ids


def _response_errors(response: Any) -> list[Any]:
    body = _response_body(response)
    errors: list[Any] = []
    root_errors = body.get("errors")
    if isinstance(root_errors, list):
        errors.extend(root_errors)
    elif root_errors not in (None, "", {}):
        errors.append(root_errors)
    if body.get("error") not in (None, "", [], {}):
        errors.append(body.get("error"))
    if body.get("success") is False and not errors:
        errors.append(body.get("message") or "Mercado Libre 返回 success=false")
    return errors


def _mutation_contract_error(
    code: str,
    message: str,
    *,
    operation: str,
) -> PublishAdapterError:
    """写请求已经发出但响应身份不可信；禁止重试并进入对账状态。"""

    return PublishAdapterError(
        code,
        message,
        retryable=False,
        details={
            "remote_write_dispatched": True,
            "outcome_unknown": True,
            "operation": operation,
        },
    )


def _response_siteless_id(response: Any) -> str:
    body = _response_body(response)
    return canonicalize_mercadolibre_siteless_user_product_id(
        body.get("siteless_user_product_id") or body.get("id") or ""
    )


def _validate_mutation_response(
    response: Any,
    *,
    operation: str,
    expected_siteless_id: str = "",
    publication: dict[str, Any] | None = None,
    requested_sites: list[dict[str, Any]] | None = None,
) -> None:
    """验证 Mercado 写响应仍属于本次账号、User Product 与市场闭包。"""

    body = _response_body(response)
    if not body:
        raise _mutation_contract_error(
            "MERCADOLIBRE_MUTATION_RESPONSE_INVALID",
            "Mercado Libre 写请求返回空或畸形响应，无法确认远端结果。",
            operation=operation,
        )

    returned_id = _response_siteless_id(body)
    expected_id = canonicalize_mercadolibre_siteless_user_product_id(
        expected_siteless_id
    )
    if returned_id and expected_id and returned_id != expected_id:
        raise _mutation_contract_error(
            "MERCADOLIBRE_RESPONSE_IDENTITY_MISMATCH",
            (
                "Mercado Libre 响应的 Siteless User Product ID 与请求目标不一致："
                f"expected={expected_id}, actual={returned_id}"
            ),
            operation=operation,
        )

    has_errors = bool(_response_errors(body))
    task_ids = _response_task_ids(body)
    if not has_errors and not task_ids:
        if expected_id and returned_id != expected_id:
            raise _mutation_contract_error(
                "MERCADOLIBRE_MUTATION_RESPONSE_INVALID",
                "Mercado Libre 成功响应缺少匹配的 Siteless User Product ID。",
                operation=operation,
            )
        if not expected_id and not returned_id:
            raise _mutation_contract_error(
                "MERCADOLIBRE_MUTATION_RESPONSE_INVALID",
                "Mercado Libre 创建响应缺少 Siteless User Product ID。",
                operation=operation,
            )

    raw_markets = (
        body.get("site_items")
        if isinstance(body.get("site_items"), list)
        else body.get("listing_sites")
        if isinstance(body.get("listing_sites"), list)
        else []
    )
    if not raw_markets:
        if (
            operation in {"create", "add_marketplaces"}
            and requested_sites
            and not task_ids
            and not has_errors
        ):
            raise _mutation_contract_error(
                "MERCADOLIBRE_MARKET_RESPONSE_CARDINALITY_MISMATCH",
                "Mercado Libre 成功响应未返回本次请求的市场映射。",
                operation=operation,
            )
        return

    known_publication = normalize_mercadolibre_publication(publication)
    allowed_markets = [
        dict(item)
        for item in known_publication.get("markets", [])
        if isinstance(item, dict)
    ]
    for requested in requested_sites or []:
        if not isinstance(requested, dict):
            continue
        requested_key = _market_key(requested)
        prior = next(
            (
                item
                for item in allowed_markets
                if _market_key(item) == requested_key
            ),
            {},
        )
        allowed_markets = [
            item for item in allowed_markets if _market_key(item) != requested_key
        ]
        allowed_markets.append({**prior, **requested})

    allowed_by_item_id = {
        str(item.get("item_id") or "").strip(): item
        for item in allowed_markets
        if str(item.get("item_id") or "").strip()
    }
    seen_market_keys: set[tuple[str, str]] = set()
    seen_item_ids: set[str] = set()
    seen_user_product_ids: set[str] = set()
    for raw in raw_markets:
        if not isinstance(raw, dict):
            raise _mutation_contract_error(
                "MERCADOLIBRE_MARKET_RESPONSE_INVALID",
                "Mercado Libre 市场响应包含非 object 项。",
                operation=operation,
            )
        item_id = str(
            raw.get("item_id") or raw.get("listing_id") or raw.get("id") or ""
        ).strip()
        prior = allowed_by_item_id.get(item_id, {})
        site_id = str(
            raw.get("site_id") or prior.get("site_id") or ""
        ).strip().upper()
        logistic_type = str(
            raw.get("logistic_type") or prior.get("logistic_type") or ""
        ).strip().lower()
        candidates = [
            item
            for item in allowed_markets
            if str(item.get("site_id") or "").strip().upper() == site_id
            and (
                not logistic_type
                or not str(item.get("logistic_type") or "").strip()
                or str(item.get("logistic_type") or "").strip().lower()
                == logistic_type
            )
        ]
        if not site_id or not candidates:
            raise _mutation_contract_error(
                "MERCADOLIBRE_MARKET_RESPONSE_OUT_OF_SCOPE",
                (
                    "Mercado Libre 响应包含未请求或未持久化的市场 operation："
                    f"{site_id or '-'}:{logistic_type or '-'}"
                ),
                operation=operation,
            )
        if not logistic_type and len(candidates) == 1:
            logistic_type = str(
                candidates[0].get("logistic_type") or ""
            ).strip().lower()
        market_key = (site_id, logistic_type)
        if market_key in seen_market_keys:
            raise _mutation_contract_error(
                "MERCADOLIBRE_MARKET_RESPONSE_DUPLICATED",
                (
                    "Mercado Libre 响应对同一市场 operation 返回了多条映射："
                    f"{site_id}:{logistic_type or '-'}"
                ),
                operation=operation,
            )
        seen_market_keys.add(market_key)
        user_product_id = str(
            raw.get("user_product_id") or raw.get("local_user_product_id") or ""
        ).strip()
        if item_id and item_id in seen_item_ids:
            raise _mutation_contract_error(
                "MERCADOLIBRE_MARKET_ITEM_ID_DUPLICATED",
                f"Mercado Libre 响应重复使用 Item ID：{item_id}",
                operation=operation,
            )
        if user_product_id and user_product_id in seen_user_product_ids:
            raise _mutation_contract_error(
                "MERCADOLIBRE_MARKET_USER_PRODUCT_ID_DUPLICATED",
                (
                    "Mercado Libre 响应重复使用 Local User Product ID："
                    f"{user_product_id}"
                ),
                operation=operation,
            )
        if item_id:
            seen_item_ids.add(item_id)
        if user_product_id:
            seen_user_product_ids.add(user_product_id)
        if operation in {"create", "add_marketplaces"} and (
            not item_id or not user_product_id
        ):
            raise _mutation_contract_error(
                "MERCADOLIBRE_MARKET_MAPPING_INCOMPLETE",
                (
                    f"Mercado Libre 市场 {site_id} 响应未同时返回 Item ID 与"
                    " Local User Product ID。"
                ),
                operation=operation,
            )
        returned_seller_id = str(raw.get("seller_id") or "").strip()
        expected_seller_ids = {
            str(item.get("seller_id") or "").strip()
            for item in candidates
            if str(item.get("seller_id") or "").strip()
        }
        if (
            returned_seller_id
            and expected_seller_ids
            and returned_seller_id not in expected_seller_ids
        ):
            raise _mutation_contract_error(
                "MERCADOLIBRE_MARKET_SELLER_MISMATCH",
                (
                    f"Mercado Libre 市场 {site_id} 响应 seller_id 不属于当前授权："
                    f"{returned_seller_id}"
                ),
                operation=operation,
            )

    if operation in {"create", "add_marketplaces"}:
        requested_keys = {
            _market_key(item)
            for item in requested_sites or []
            if isinstance(item, dict) and _market_key(item)[0]
        }
        if seen_market_keys != requested_keys:
            raise _mutation_contract_error(
                "MERCADOLIBRE_MARKET_RESPONSE_CARDINALITY_MISMATCH",
                (
                    "Mercado Libre 响应市场映射与本次请求不是一一对应关系："
                    f"requested={sorted(requested_keys)}, "
                    f"actual={sorted(seen_market_keys)}"
                ),
                operation=operation,
            )


def _market_key(value: dict[str, Any]) -> tuple[str, str]:
    return (
        str(value.get("site_id") or "").strip().upper(),
        str(value.get("logistic_type") or "").strip().lower(),
    )


def _public_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    item_payload = dict(payload)
    listing_model = require_mercadolibre_listing_model(
        item_payload.pop("_listing_model", None)
    )
    if listing_model != MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS:
        raise RuntimeError(
            "MERCADOLIBRE_LISTING_MODEL_MISMATCH: User Products 路由收到传统 payload"
        )
    raw_publication = item_payload.pop("_publication", {})
    if isinstance(raw_publication, dict) and (
        (
            bool(raw_publication)
            and str(raw_publication.get("model") or "").strip()
            != MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS
        )
        or (
            raw_publication.get("parent_item_id")
            and not raw_publication.get("siteless_user_product_id")
        )
    ):
        raise RuntimeError(
            "MERCADOLIBRE_PUBLICATION_MODEL_MISMATCH: 传统 publication 禁止走 User Products 路由"
        )
    publication = normalize_mercadolibre_publication(raw_publication)
    if isinstance(raw_publication, dict):
        account_user_id = str(
            raw_publication.get("account_user_id") or ""
        ).strip()
        if account_user_id:
            publication["model"] = "user_products"
            publication["account_user_id"] = account_user_id
    unknown_internal = sorted(
        key for key in item_payload if str(key).startswith("_")
    )
    if unknown_internal:
        raise RuntimeError(
            "Mercado Libre payload 包含未知内部字段："
            + "、".join(unknown_internal)
        )
    _, pricing_issues = mercadolibre_payload_pricing_contract(item_payload)
    if pricing_issues:
        issue = pricing_issues[0]
        raise RuntimeError(f"{issue['code']}: {issue['message']}")
    return item_payload, publication


def _existing_market_by_key(
    publication: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        _market_key(item): item
        for item in publication.get("markets", [])
        if isinstance(item, dict)
        and _market_key(item)[0]
        and (
            str(item.get("item_id") or "").strip()
            or str(item.get("user_product_id") or "").strip()
        )
    }


def _publication_with_response_errors(
    publication: dict[str, Any],
    response: Any,
) -> dict[str, Any]:
    """把可归属的错误写入 operation，其余错误留在 publication 根。"""

    errors = _response_errors(response)
    if not errors:
        return publication
    updated_at = _now_iso()
    markets = [
        dict(item)
        for item in publication.get("markets", [])
        if isinstance(item, dict)
    ]
    root_errors: list[Any] = []
    for raw_error in errors:
        country = (
            str(raw_error.get("country_id") or "").strip().upper()
            if isinstance(raw_error, dict)
            else ""
        )
        site_id = _SITE_BY_COUNTRY.get(country, "")
        candidates = (
            [
                index
                for index, market in enumerate(markets)
                if str(market.get("site_id") or "").strip().upper()
                == site_id
            ]
            if site_id
            else []
        )
        if not candidates:
            root_errors.append(raw_error)
            continue
        for index in candidates:
            error = deepcopy(raw_error)
            markets[index]["error"] = error
            markets[index]["last_operation"] = {
                "status": "failed",
                "error": error,
                "updated_at": updated_at,
            }
            markets[index]["updated_at"] = updated_at
    combined: dict[str, Any] = {
        **publication,
        "status": "partial",
        "markets": markets,
        "updated_at": updated_at,
    }
    if root_errors:
        root_error: Any = (
            root_errors[0] if len(root_errors) == 1 else root_errors
        )
        combined["error"] = deepcopy(root_error)
        combined["last_operation"] = {
            "status": "failed",
            "error": deepcopy(root_error),
            "updated_at": updated_at,
        }
    return normalize_mercadolibre_publication(combined)


def _task_user_product_id(value: Any) -> str:
    raw = value if isinstance(value, dict) else {}
    return str(
        raw.get("id") or raw.get("user_product_id") or ""
    ).strip().upper()


def _task_entry_match(
    publication: dict[str, Any],
    user_product: dict[str, Any],
) -> tuple[str, int | None] | None:
    """只按当前 Siteless/Local UP 身份匹配 family task entry。"""

    entry_id = _task_user_product_id(user_product)
    if not entry_id:
        return None
    markets = publication.get("markets", [])
    for index, market in enumerate(markets if isinstance(markets, list) else []):
        if (
            isinstance(market, dict)
            and str(market.get("user_product_id") or "").strip().upper()
            == entry_id
        ):
            return "market", index
    canonical_entry_id = canonicalize_mercadolibre_siteless_user_product_id(
        entry_id
    )
    current_root_ids = {
        canonicalize_mercadolibre_siteless_user_product_id(
            publication.get("siteless_user_product_id")
        ),
        canonicalize_mercadolibre_siteless_user_product_id(
            publication.get("parent_user_product_id")
        ),
    }
    current_root_ids.discard("")
    if canonical_entry_id in current_root_ids:
        return "publication", None
    return None


def _publication_with_task_results(
    publication: dict[str, Any],
    task_results: list[dict[str, Any]],
    *,
    pending_listing_updates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    publication = normalize_mercadolibre_publication(publication)
    updated_at = _now_iso()
    markets = [
        dict(item)
        for item in publication.get("markets", [])
        if isinstance(item, dict)
    ]
    root_errors: list[Any] = []
    root_succeeded = False
    changed = False
    pending_updates = [
        dict(item)
        for item in pending_listing_updates or []
        if isinstance(item, dict)
    ]

    def matching_pending_updates(
        market: dict[str, Any],
        *,
        task_id: str,
    ) -> list[dict[str, Any]]:
        market_item_id = str(market.get("item_id") or "").strip()
        market_user_product_id = str(
            market.get("user_product_id") or ""
        ).strip().upper()
        market_key = _market_key(market)
        matched: list[dict[str, Any]] = []
        for pending_update in pending_updates:
            pending_task_id = str(
                pending_update.get("task_id") or ""
            ).strip()
            if pending_task_id and pending_task_id != task_id:
                continue
            pending_item_id = str(
                pending_update.get("item_id") or ""
            ).strip()
            pending_user_product_id = str(
                pending_update.get("user_product_id") or ""
            ).strip().upper()
            identity_matches = (
                bool(pending_item_id and pending_item_id == market_item_id)
                or bool(
                    pending_user_product_id
                    and pending_user_product_id == market_user_product_id
                )
                or _market_key(pending_update) == market_key
            )
            if identity_matches:
                matched.append(pending_update)
        return matched

    def apply_pending_update(
        market: dict[str, Any],
        *,
        task_id: str,
    ) -> bool:
        matched = matching_pending_updates(market, task_id=task_id)
        for pending_update in matched:
            for field in _LISTING_SITE_MUTABLE_FIELDS:
                if field in pending_update:
                    market[field] = deepcopy(pending_update[field])
        return bool(matched)

    for task in task_results:
        task_id = str(task.get("task_id") or "").strip()
        task_status = str(task.get("status") or "").strip().lower()
        if task_status in {"failed", "error"}:
            task_error = deepcopy(
                task.get("errors")
                or task.get("error")
                or task.get("message")
                or task
            )
            root_errors.append(task_error)
            for market in markets:
                if not matching_pending_updates(market, task_id=task_id):
                    continue
                market["error"] = deepcopy(task_error)
                market["last_operation"] = {
                    "status": "failed",
                    "error": deepcopy(task_error),
                    "updated_at": updated_at,
                }
                market["updated_at"] = updated_at
            changed = True
        user_products = (
            task.get("user_products")
            if isinstance(task.get("user_products"), list)
            else []
        )
        for user_product in user_products:
            if not isinstance(user_product, dict):
                continue
            status = str(user_product.get("status") or "").strip().lower()
            matching = _task_entry_match(publication, user_product)
            if matching is None:
                continue
            if status == "succeeded":
                if matching[0] == "publication":
                    root_succeeded = True
                    for market in markets:
                        if not apply_pending_update(
                            market,
                            task_id=task_id,
                        ):
                            continue
                        market.pop("error", None)
                        market["last_operation"] = {
                            "status": "succeeded",
                            "updated_at": updated_at,
                        }
                        market["updated_at"] = updated_at
                else:
                    index = matching[1]
                    if index is None or index >= len(markets):
                        continue
                    apply_pending_update(markets[index], task_id=task_id)
                    markets[index].pop("error", None)
                    markets[index]["last_operation"] = {
                        "status": "succeeded",
                        "updated_at": updated_at,
                    }
                    markets[index]["updated_at"] = updated_at
                changed = True
                continue
            if status not in {"failed", "error"}:
                continue
            error = deepcopy(user_product.get("reasons") or user_product)
            if matching[0] == "publication":
                root_errors.append(error)
            else:
                index = matching[1]
                if index is None or index >= len(markets):
                    continue
                markets[index]["error"] = error
                markets[index]["last_operation"] = {
                    "status": "failed",
                    "error": deepcopy(error),
                    "updated_at": updated_at,
                }
                markets[index]["updated_at"] = updated_at
            changed = True
    if not changed:
        return publication
    combined: dict[str, Any] = {
        **publication,
        "markets": markets,
        "updated_at": updated_at,
    }
    if root_errors:
        root_error: Any = (
            root_errors[0] if len(root_errors) == 1 else root_errors
        )
        combined["error"] = deepcopy(root_error)
        combined["last_operation"] = {
            "status": "failed",
            "error": deepcopy(root_error),
            "updated_at": updated_at,
        }
    elif root_succeeded:
        combined.pop("error", None)
        combined["last_operation"] = {
            "status": "succeeded",
            "updated_at": updated_at,
        }
    has_market_failure = any(
        market.get("error") not in (None, "", [], {})
        or str(
            (market.get("last_operation") or {}).get("status")
            if isinstance(market.get("last_operation"), dict)
            else ""
        ).strip().lower()
        in {"failed", "error"}
        or str(market.get("status") or "").strip().lower()
        in {"failed", "error"}
        for market in markets
    )
    has_root_failure = (
        combined.get("error") not in (None, "", [], {})
        or str(
            (combined.get("last_operation") or {}).get("status")
            if isinstance(combined.get("last_operation"), dict)
            else ""
        ).strip().lower()
        in {"failed", "error"}
    )
    if has_market_failure or has_root_failure:
        combined["status"] = "partial"
    elif str(combined.get("status") or "").strip().lower() in {
        "partial",
        "failed",
        "error",
    }:
        combined["status"] = "active"
    return normalize_mercadolibre_publication(combined)


def _publication_with_missing_requested_markets(
    publication: dict[str, Any],
    requested_sites: Any,
) -> dict[str, Any]:
    markets = [
        dict(item)
        for item in publication.get("markets", [])
        if isinstance(item, dict)
    ]
    by_key = {_market_key(item): item for item in markets}
    changed = False
    for requested in (
        requested_sites if isinstance(requested_sites, list) else []
    ):
        if not isinstance(requested, dict):
            continue
        key = _market_key(requested)
        if not key[0]:
            continue
        market = by_key.get(key)
        if market is not None and (
            str(market.get("item_id") or "").strip()
            and str(market.get("user_product_id") or "").strip()
        ):
            continue
        if market is None:
            market = {
                **deepcopy(requested),
                "site_id": key[0],
                "logistic_type": key[1],
            }
            markets.append(market)
            by_key[key] = market
        error = {
            "code": "MERCADOLIBRE_MARKET_MAPPING_MISSING",
            "message": "Mercado Libre 响应未返回该目标市场的 Item/UP 映射",
        }
        updated_at = _now_iso()
        market.update(
            {
                "error": error,
                "last_operation": {
                    "status": "failed",
                    "error": deepcopy(error),
                    "updated_at": updated_at,
                },
                "updated_at": updated_at,
            }
        )
        changed = True
    if not changed:
        return publication
    return normalize_mercadolibre_publication(
        {
            **publication,
            "status": "partial",
            "markets": markets,
            "updated_at": _now_iso(),
        }
    )


def _listing_site_update(
    target: dict[str, Any],
    market: dict[str, Any],
) -> dict[str, Any]:
    listing_id = str(market.get("item_id") or "").strip()
    if not listing_id:
        return {}
    update: dict[str, Any] = {}
    for field in (
        "price",
        "net_proceeds",
        "listing_type_id",
        "status",
        "description",
        "sale_terms",
    ):
        if field in target and not _confirmed_value_equal(
            field,
            market.get(field),
            target[field],
        ):
            update[field] = (
                _update_attribute_entries(target[field])
                if field == "sale_terms"
                else deepcopy(target[field])
            )
    if "free_shipping" in target and not _confirmed_value_equal(
        "free_shipping",
        market.get("free_shipping"),
        target["free_shipping"],
    ):
        update["marketplace"] = {
            "free_shipping": bool(target.get("free_shipping"))
        }
    if update:
        update = {"listing_id": listing_id, **update}
    return update


def _update_attribute_value(raw: Any) -> dict[str, str]:
    value = raw if isinstance(raw, dict) else {}
    value_id = str(value.get("id") or value.get("value_id") or "").strip()
    value_name = str(
        value.get("name") or value.get("value_name") or ""
    ).strip()
    return {
        **({"id": value_id} if value_id else {}),
        **({"name": value_name} if value_name else {}),
    }


def _update_attribute_entries(value: Any) -> list[dict[str, Any]]:
    """把 create 的 scalar attributes/sale_terms 转成 Global PUT values。"""

    entries: list[dict[str, Any]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        attribute_id = str(raw.get("id") or "").strip()
        if not attribute_id:
            continue
        has_values = isinstance(raw.get("values"), list)
        raw_values = raw.get("values") if has_values else [raw]
        values = [
            normalized_value
            for item in raw_values
            if (
                normalized_value := _update_attribute_value(
                    item
                    if has_values
                    else {
                        "id": raw.get("value_id"),
                        "name": raw.get("value_name") or raw.get("name"),
                    }
                )
            )
        ]
        if values:
            entries.append({"id": attribute_id, "values": values})
    return entries


def _attribute_entries_signature(value: Any) -> tuple[Any, ...] | None:
    if not isinstance(value, list):
        return None
    entries: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for raw in value:
        if not isinstance(raw, dict):
            return None
        attribute_id = str(raw.get("id") or "").strip()
        if not attribute_id:
            return None
        has_values = isinstance(raw.get("values"), list)
        raw_values = raw.get("values") if has_values else [raw]
        values: list[tuple[str, str]] = []
        for item in raw_values:
            normalized = _update_attribute_value(
                item
                if has_values
                else {
                    "id": raw.get("value_id"),
                    "name": raw.get("value_name") or raw.get("name"),
                }
            )
            if normalized.get("id"):
                # 平台枚举 ID 是语义身份；显示名可能随 locale 改变。
                values.append(("id", normalized["id"]))
            elif normalized.get("name"):
                values.append(("name", normalized["name"]))
            else:
                return None
        entries.append((attribute_id, tuple(sorted(values))))
    return tuple(sorted(entries))


def _confirmed_value_equal(field: str, confirmed: Any, desired: Any) -> bool:
    if confirmed == desired:
        return True
    if field in {"attributes", "sale_terms"}:
        confirmed_signature = _attribute_entries_signature(confirmed)
        desired_signature = _attribute_entries_signature(desired)
        return (
            confirmed_signature is not None
            and desired_signature is not None
            and confirmed_signature == desired_signature
        )
    if field in {"price", "net_proceeds"}:
        try:
            return Decimal(str(confirmed)) == Decimal(str(desired))
        except (InvalidOperation, TypeError, ValueError):
            return False
    if field in {"listing_type_id", "status"}:
        return str(confirmed or "").strip().casefold() == str(
            desired or ""
        ).strip().casefold()
    return False


def _user_product_fields(item_payload: dict[str, Any]) -> dict[str, Any]:
    fields = {
        key: deepcopy(value)
        for key, value in item_payload.items()
        if key not in _NON_UPDATABLE_USER_PRODUCT_FIELDS
    }
    if isinstance(fields.get("attributes"), list):
        fields["attributes"] = _update_attribute_entries(
            fields["attributes"]
        )
    return fields


def _user_product_confirmed_create_fields(
    item_payload: dict[str, Any],
) -> dict[str, Any]:
    """保存语义 canonical 快照，不把 create/PUT wire 差异写成脏状态。"""

    confirmed = _user_product_fields(item_payload)
    if isinstance(item_payload.get("sale_terms"), list):
        confirmed["sale_terms"] = _update_attribute_entries(
            item_payload["sale_terms"]
        )
    if isinstance(item_payload.get("description"), dict):
        confirmed["description"] = deepcopy(item_payload["description"])
    return confirmed


def _user_product_listing_targets(
    item_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """把 create 根描述/保修投影到既有 UP 的 marketplace 字段。"""

    root_sale_terms = (
        item_payload.get("sale_terms")
        if isinstance(item_payload.get("sale_terms"), list)
        else None
    )
    root_description = (
        item_payload.get("description")
        if isinstance(item_payload.get("description"), dict)
        else None
    )
    targets: list[dict[str, Any]] = []
    for raw in item_payload.get("sites_to_sell", []):
        if not isinstance(raw, dict):
            continue
        target = deepcopy(raw)
        if (
            not isinstance(target.get("sale_terms"), list)
            and root_sale_terms is not None
        ):
            target["sale_terms"] = deepcopy(root_sale_terms)
        if (
            not isinstance(target.get("description"), dict)
            and root_description is not None
        ):
            target["description"] = deepcopy(root_description)
        targets.append(target)
    return targets


def _publication_with_confirmed_payload(
    publication: dict[str, Any],
    confirmed_fields: dict[str, Any],
) -> dict[str, Any]:
    if not confirmed_fields:
        return publication
    confirmed_payload = (
        deepcopy(publication.get("confirmed_payload"))
        if isinstance(publication.get("confirmed_payload"), dict)
        else {}
    )
    canonical_fields = deepcopy(confirmed_fields)
    for field in ("attributes", "sale_terms"):
        if isinstance(canonical_fields.get(field), list):
            canonical_fields[field] = _update_attribute_entries(
                canonical_fields[field]
            )
    confirmed_payload.update(canonical_fields)
    updated_at = _now_iso()
    combined = {
        **publication,
        "confirmed_payload": confirmed_payload,
        "last_operation": {
            "status": "succeeded",
            "updated_at": updated_at,
        },
        "updated_at": updated_at,
    }
    combined.pop("error", None)
    return normalize_mercadolibre_publication(combined)


def _user_product_update_payload(
    item_payload: dict[str, Any],
    publication: dict[str, Any],
) -> dict[str, Any]:
    confirmed_payload = (
        publication.get("confirmed_payload")
        if isinstance(publication.get("confirmed_payload"), dict)
        else {}
    )
    update: dict[str, Any] = {}
    for key, value in _user_product_fields(item_payload).items():
        if key in confirmed_payload:
            confirmed_value = confirmed_payload[key]
        elif key == "family_name" and publication.get("family_name") not in (
            None,
            "",
        ):
            confirmed_value = publication.get("family_name")
        else:
            # 没有远端确认快照时不能把整份草稿当作差异重发。
            continue
        if not _confirmed_value_equal(key, confirmed_value, value):
            update[key] = deepcopy(value)
    existing_markets = _existing_market_by_key(publication)
    confirmed_sale_terms = (
        confirmed_payload.get("sale_terms")
        if isinstance(confirmed_payload.get("sale_terms"), list)
        else None
    )
    confirmed_description = (
        confirmed_payload.get("description")
        if isinstance(confirmed_payload.get("description"), dict)
        else None
    )
    listing_sites = [
        _listing_site_update(
            target,
            {
                **(
                    {"sale_terms": deepcopy(confirmed_sale_terms)}
                    if confirmed_sale_terms is not None
                    else {}
                ),
                **(
                    {"description": deepcopy(confirmed_description)}
                    if confirmed_description is not None
                    else {}
                ),
                **existing_markets.get(_market_key(target), {}),
            },
        )
        for target in _user_product_listing_targets(item_payload)
        if _market_key(target) in existing_markets
    ]
    listing_sites = [item for item in listing_sites if item]
    if listing_sites:
        update["listing_sites"] = listing_sites
    return update


def _pending_listing_updates(
    update_payload: dict[str, Any],
    update_response: Any,
    *,
    listing_targets: list[dict[str, Any]],
    publication: dict[str, Any],
) -> list[dict[str, Any]]:
    """保存异步站点 mutation 的期望值，终态成功前不得写入 publication。"""

    body = _response_body(update_response)
    response_sites = (
        body.get("listing_sites")
        if isinstance(body.get("listing_sites"), list)
        else []
    )
    task_by_listing_id = {
        str(
            item.get("listing_id")
            or item.get("item_id")
            or item.get("id")
            or ""
        ).strip(): str(item.get("task_id") or "").strip()
        for item in response_sites
        if isinstance(item, dict) and str(item.get("task_id") or "").strip()
    }
    existing_by_item_id = {
        str(item.get("item_id") or "").strip(): item
        for item in publication.get("markets", [])
        if isinstance(item, dict) and str(item.get("item_id") or "").strip()
    }
    targets_by_key = {
        _market_key(item): item
        for item in listing_targets
        if isinstance(item, dict)
    }
    pending: list[dict[str, Any]] = []
    for wire_update in update_payload.get("listing_sites", []):
        if not isinstance(wire_update, dict):
            continue
        listing_id = str(wire_update.get("listing_id") or "").strip()
        task_id = task_by_listing_id.get(listing_id, "")
        if not task_id:
            continue
        market = existing_by_item_id.get(listing_id, {})
        target = targets_by_key.get(_market_key(market), {})
        desired: dict[str, Any] = {
            "task_id": task_id,
            "item_id": listing_id,
            "user_product_id": str(
                market.get("user_product_id") or ""
            ).strip(),
            "site_id": str(market.get("site_id") or "").strip().upper(),
            "seller_id": str(market.get("seller_id") or "").strip(),
            "logistic_type": str(
                market.get("logistic_type") or ""
            ).strip().lower(),
        }
        for field in _LISTING_SITE_MUTABLE_FIELDS - {"free_shipping"}:
            if field not in wire_update or field not in target:
                continue
            desired[field] = (
                _update_attribute_entries(target[field])
                if field == "sale_terms"
                else deepcopy(target[field])
            )
        marketplace = (
            wire_update.get("marketplace")
            if isinstance(wire_update.get("marketplace"), dict)
            else {}
        )
        if "free_shipping" in marketplace:
            desired["free_shipping"] = bool(
                target.get("free_shipping")
            )
        pending.append(desired)
    return pending


def _publication_result(
    response: Any,
    *,
    existing: dict[str, Any],
    item_payload: dict[str, Any],
    operation: str,
) -> dict[str, Any]:
    body = _response_body(response) or {"response": response}
    publication = mercadolibre_publication_from_response(
        body,
        existing=existing,
        family_name=str(item_payload.get("family_name") or ""),
        requested_sites=item_payload.get("sites_to_sell"),
        updated_at=_now_iso(),
    )
    publication = _publication_with_response_errors(publication, body)
    publication = _publication_with_missing_requested_markets(
        publication,
        item_payload.get("sites_to_sell"),
    )
    if (
        operation == "created"
        and publication.get("siteless_user_product_id")
        and not _response_errors(body)
    ):
        publication = _publication_with_confirmed_payload(
            publication,
            _user_product_confirmed_create_fields(item_payload),
        )
    body["publication"] = publication
    body["siteless_user_product_id"] = publication.get(
        "siteless_user_product_id", ""
    )
    body["siteless_family_id"] = publication.get("siteless_family_id", "")
    body["item_id"] = publication.get("parent_item_id", "")
    body["site_items"] = publication.get("markets", [])
    body["operation"] = operation
    failed = mercadolibre_publication_has_failures(publication)
    body["ok"] = bool(
        publication.get("siteless_user_product_id") and not failed
    )
    body["status"] = "partial" if failed else "published"
    if failed:
        failed_sites = [
            str(item.get("site_id") or "").strip().upper()
            for item in publication.get("markets", [])
            if isinstance(item, dict)
            and (
                item.get("error") not in (None, "", [], {})
                or str(
                    (item.get("last_operation") or {}).get("status")
                    if isinstance(item.get("last_operation"), dict)
                    else ""
                ).strip().lower()
                in {"failed", "error"}
                or str(item.get("status") or "").strip().lower()
                in {"failed", "error"}
            )
        ]
        if failed_sites:
            body["error"] = (
                "以下 Mercado 市场刊登失败：" + "、".join(failed_sites)
            )
        else:
            body["error"] = str(
                publication.get("error")
                or body.get("error")
                or "Mercado Libre 当前操作失败"
            )
    return body


def _pending_update_result(
    *,
    task_ids: list[str],
    publication: dict[str, Any],
    item_payload: dict[str, Any],
    additions: list[dict[str, Any]],
    confirmed_family_name: str,
    pending_confirmed_fields: dict[str, Any],
    pending_listing_updates: list[dict[str, Any]],
    added_site_ids: list[str] | None = None,
) -> dict[str, Any]:
    siteless_id = str(
        publication.get("siteless_user_product_id") or ""
    ).strip()
    return {
        "ok": True,
        "status": "pending_confirmation",
        "operation": "update_pending",
        "task_id": task_ids[0],
        "task_ids": task_ids,
        "siteless_user_product_id": siteless_id,
        "siteless_family_id": publication.get("siteless_family_id", ""),
        "item_id": publication.get("parent_item_id", ""),
        "publication": publication,
        "site_items": publication.get("markets", []),
        "continuation": {
            "siteless_user_product_id": siteless_id,
            "family_name": str(item_payload.get("family_name") or ""),
            # family_name 属于异步字段。任务成功前，publication 只能保存
            # 平台已确认值；失败时也必须回退到该值，不能把期望值伪装成事实。
            "confirmed_family_name": str(confirmed_family_name or ""),
            "pending_confirmed_fields": deepcopy(pending_confirmed_fields),
            "pending_listing_updates": deepcopy(pending_listing_updates),
            "requested_sites": [
                dict(item)
                for item in item_payload.get("sites_to_sell", [])
                if isinstance(item, dict)
            ],
            "additions": [dict(item) for item in additions],
            "added_sites": list(added_site_ids or []),
        },
        "task_results": [],
        "confirmation_started_at": _now_iso(),
        "confirmation_poll_count": 0,
    }


def _confirmation_outcome_unknown(
    pending: dict[str, Any],
    *,
    publication: dict[str, Any],
    task_ids: list[str],
    task_results: list[dict[str, Any]],
    error_code: str,
    error: str,
) -> dict[str, Any]:
    return {
        **pending,
        "ok": False,
        "status": "outcome_unknown",
        "operation": "update_confirmation_unknown",
        "error_code": error_code,
        "error": error,
        "outcome_unknown": True,
        "task_id": task_ids[0] if task_ids else "",
        "task_ids": task_ids,
        "task_results": task_results,
        "publication": publication,
        "site_items": publication.get("markets", []),
        "confirmation_checked_at": _now_iso(),
    }


def _finish_existing_update(
    *,
    response: Any,
    publication: dict[str, Any],
    family_name: str,
    requested_sites: list[dict[str, Any]],
    additions: list[dict[str, Any]],
    token: str,
) -> dict[str, Any]:
    del token
    if additions:
        # 旧 pending continuation 可能把第二次写操作藏在 task poll 内；恢复时
        # 绝不能重放。新流程会在进入异步确认前完成新增市场 mutation。
        raise RuntimeError(
            "Mercado Libre 待确认结果包含未持久化的新增市场写操作，"
            "已阻止恢复重放并要求人工对账。"
        )
    item_payload = {
        "family_name": family_name,
        "sites_to_sell": requested_sites,
    }
    publication = _publication_with_response_errors(publication, response)
    if mercadolibre_publication_has_failures(publication):
        result = _publication_result(
            response,
            existing=publication,
            item_payload=item_payload,
            operation="updated",
        )
        result["added_sites"] = []
        return result

    result = _publication_result(
        response,
        existing=publication,
        item_payload=item_payload,
        operation="updated",
    )
    result["added_sites"] = []
    return result


def _publish_mercadolibre_user_products(payload: dict[str, Any], token: str) -> dict[str, Any]:
    """创建或更新一个 Siteless User Product 及其 marketplace projections。"""

    item_payload, publication = _public_payload(payload)
    siteless_id = str(
        publication.get("siteless_user_product_id") or ""
    ).strip()
    if not siteless_id:
        response = _write_request_json(
            "POST",
            MERCADOLIBRE_USER_PRODUCT_FAMILIES_ENDPOINT,
            token,
            [item_payload],
        )
        if not (
            isinstance(response, list)
            and len(response) == 1
            and isinstance(response[0], dict)
        ):
            raise _mutation_contract_error(
                "MERCADOLIBRE_CREATE_CARDINALITY_MISMATCH",
                (
                    "Mercado Libre families 单商品创建响应必须恰好包含一个 entry，"
                    "无法确认远端创建数量。"
                ),
                operation="create",
            )
        _validate_mutation_response(
            response,
            operation="create",
            publication=publication,
            requested_sites=[
                dict(item)
                for item in item_payload.get("sites_to_sell", [])
                if isinstance(item, dict)
            ],
        )
        return _publication_result(
            response,
            existing=publication,
            item_payload=item_payload,
            operation="created",
        )

    endpoint = (
        "https://api.mercadolibre.com/global/user-products/"
        + quote(siteless_id, safe="")
    )
    existing_markets = _existing_market_by_key(publication)
    requested_sites = [
        item
        for item in item_payload.get("sites_to_sell", [])
        if isinstance(item, dict)
    ]
    listing_update_targets = _user_product_listing_targets(item_payload)
    additions = [
        item
        for item in requested_sites
        if _market_key(item) not in existing_markets
    ]

    added_site_ids: list[str] = []
    if additions:
        add_response = _write_request_json(
            "POST",
            endpoint,
            token,
            {"sites_to_sell": additions},
        )
        _validate_mutation_response(
            add_response,
            operation="add_marketplaces",
            expected_siteless_id=siteless_id,
            publication=publication,
            requested_sites=additions,
        )
        if _response_task_ids(add_response):
            raise _mutation_contract_error(
                "MERCADOLIBRE_MARKET_ADDITION_ASYNC_UNSUPPORTED",
                (
                    "Mercado Libre 新增市场返回异步 task；当前无法在不重放写请求的"
                    "前提下自动续跑，必须先对账。"
                ),
                operation="add_marketplaces",
            )
        publication = mercadolibre_publication_from_response(
            add_response,
            existing=publication,
            family_name=str(publication.get("family_name") or ""),
            requested_sites=additions,
            updated_at=_now_iso(),
        )
        publication = _publication_with_response_errors(
            publication,
            add_response,
        )
        added_site_ids = [
            str(item.get("site_id") or "").strip().upper()
            for item in additions
        ]
        if mercadolibre_publication_has_failures(publication):
            result = _publication_result(
                add_response,
                existing=publication,
                item_payload=item_payload,
                operation="marketplaces_added",
            )
            result["added_sites"] = added_site_ids
            return result

    update_payload = _user_product_update_payload(item_payload, publication)
    update_response: Any = {}
    if update_payload:
        confirmed_family_name = str(
            publication.get("family_name") or ""
        ).strip()
        try:
            update_response = _write_request_json(
                "PUT",
                endpoint,
                token,
                update_payload,
            )
            _validate_mutation_response(
                update_response,
                operation="update",
                expected_siteless_id=siteless_id,
                publication=publication,
                requested_sites=requested_sites,
            )
            task_ids = _response_task_ids(update_response)
            confirmed_update_fields = {
                key: deepcopy(value)
                for key, value in update_payload.items()
                if key != "listing_sites"
            }
            pending_listing_updates = (
                _pending_listing_updates(
                    update_payload,
                    update_response,
                    listing_targets=listing_update_targets,
                    publication=publication,
                )
                if task_ids
                else []
            )
            publication = mercadolibre_publication_from_response(
                update_response,
                existing=publication,
                family_name=(
                    confirmed_family_name
                    if task_ids
                    else str(item_payload.get("family_name") or "")
                ),
                requested_sites=listing_update_targets,
                updated_at=_now_iso(),
            )
            if task_ids:
                publication = normalize_mercadolibre_publication(
                    {
                        **publication,
                        "family_name": confirmed_family_name,
                    }
                )
            publication = _publication_with_response_errors(
                publication,
                update_response,
            )
            if task_ids:
                return _pending_update_result(
                    task_ids=task_ids,
                    publication=publication,
                    item_payload=item_payload,
                    additions=[],
                    confirmed_family_name=confirmed_family_name,
                    pending_confirmed_fields=confirmed_update_fields,
                    pending_listing_updates=pending_listing_updates,
                    added_site_ids=added_site_ids,
                )
            if not _response_errors(update_response):
                publication = _publication_with_confirmed_payload(
                    publication,
                    confirmed_update_fields,
                )
        except Exception as exc:
            if not added_site_ids:
                raise
            typed = exc if isinstance(exc, PublishAdapterError) else None
            known_rejection = bool(
                typed is not None
                and typed.details.get("outcome_unknown") is not True
            )
            outcome_unknown = not known_rejection
            details = dict(typed.details) if typed is not None else {}
            details.update(
                {
                    "remote_write_dispatched": True,
                    "outcome_unknown": outcome_unknown,
                    "prior_mutation": "add_marketplaces",
                }
            )
            return {
                "ok": False,
                "status": "failed" if known_rejection else "outcome_unknown",
                "operation": (
                    "marketplaces_added_update_failed"
                    if known_rejection
                    else "marketplaces_added_update_unknown"
                ),
                "error_code": (
                    typed.code
                    if typed is not None
                    else "MERCADOLIBRE_POST_ADD_UPDATE_UNKNOWN"
                ),
                "error": str(exc),
                **({"outcome_unknown": True} if outcome_unknown else {}),
                "siteless_user_product_id": siteless_id,
                "publication": publication,
                "site_items": publication.get("markets", []),
                "added_sites": added_site_ids,
                "details": details,
            }

    result = _finish_existing_update(
        response=update_response,
        publication=publication,
        family_name=str(item_payload.get("family_name") or ""),
        requested_sites=requested_sites,
        additions=[],
        token=token,
    )
    if added_site_ids:
        result["operation"] = "marketplaces_added"
        result["added_sites"] = added_site_ids
    return result


def _traditional_global_item_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """剥离内部元数据并执行传统 ``/global/items`` wire 契约校验。"""

    item_payload = dict(payload)
    listing_model = require_mercadolibre_listing_model(
        item_payload.pop("_listing_model", None)
    )
    if listing_model != MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS:
        raise RuntimeError(
            "MERCADOLIBRE_LISTING_MODEL_MISMATCH: 传统路由收到 User Products payload"
        )
    raw_publication = item_payload.pop("_publication", {})
    if isinstance(raw_publication, dict) and (
        raw_publication.get("siteless_user_product_id")
        or (
            bool(raw_publication)
            and str(raw_publication.get("model") or "").strip()
            != MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS
        )
    ):
        raise RuntimeError(
            "MERCADOLIBRE_PUBLICATION_MODEL_MISMATCH: User Products publication 禁止走传统路由"
        )
    publication = normalize_mercadolibre_publication(raw_publication)
    unknown_internal = sorted(
        key for key in item_payload if str(key).startswith("_")
    )
    if unknown_internal:
        raise RuntimeError(
            "Mercado Libre 传统 payload 包含未知内部字段："
            + "、".join(unknown_internal)
        )
    forbidden = sorted(
        field
        for field in ("family_name", "global_net_proceeds", "variations")
        if field in item_payload
    )
    if forbidden:
        raise RuntimeError(
            "传统 Global Items payload 禁止字段：" + "、".join(forbidden)
        )
    for field in ("title", "category_id", "price", "currency_id", "attributes"):
        if item_payload.get(field) in (None, "", [], {}):
            raise RuntimeError(f"传统 Global Items payload 缺少 {field}")
    if not str(item_payload.get("category_id") or "").strip().upper().startswith("CBT"):
        raise RuntimeError("传统 Global Items 必须使用 CBT 类目 ID")
    if str(item_payload.get("currency_id") or "").strip().upper() != "USD":
        raise RuntimeError("传统 Global Items 刊登币种必须为 USD")
    sites = item_payload.get("sites_to_sell")
    if not isinstance(sites, list) or not sites:
        raise RuntimeError("传统 Global Items payload 缺少 sites_to_sell")
    if any(
        not isinstance(site, dict)
        or not str(site.get("site_id") or "").strip()
        or str(site.get("site_id") or "").strip().upper() == "CBT"
        or not str(site.get("logistic_type") or "").strip()
        or not str(site.get("title") or "").strip()
        or site.get("price") in (None, "")
        for site in sites
    ):
        raise RuntimeError(
            "传统 Global Items 每个 sites_to_sell 必须包含国家站点、物流、"
            "title 与 price"
        )
    pictures = item_payload.get("pictures")
    if (
        not isinstance(pictures, list)
        or not pictures
        or any(
            not isinstance(picture, dict)
            or not str(picture.get("id") or "").strip()
            or "source" in picture
            for picture in pictures
        )
    ):
        raise RuntimeError(
            "传统 Global Items 根级 pictures 必须包含 Mercado picture ID"
        )
    if any(isinstance(site, dict) and "pictures" in site for site in sites):
        raise RuntimeError(
            "传统 Global Items pictures 只能位于 payload 根级"
        )
    marketplace_title_limit = platform_title_limit("mercadolibre")
    if len(str(item_payload.get("title") or "").strip()) > marketplace_title_limit:
        raise RuntimeError(
            "MERCADOLIBRE_GLOBAL_TITLE_TOO_LONG: 传统 Global Items 根标题超过 "
            f"{marketplace_title_limit} 字符限制"
        )
    oversized_title_sites = sorted(
        {
            str(site.get("site_id") or "").strip().upper()
            for site in sites
            if isinstance(site, dict)
            and len(str(site.get("title") or "").strip())
            > marketplace_title_limit
        }
    )
    if oversized_title_sites:
        raise RuntimeError(
            "MERCADOLIBRE_MARKETPLACE_TITLE_TOO_LONG: "
            "传统 Global Items 目标市场标题超过 "
            f"{marketplace_title_limit} 字符限制："
            + "、".join(oversized_title_sites)
        )
    attributes = {
        str(item.get("id") or "").strip(): item
        for item in item_payload.get("attributes", [])
        if isinstance(item, dict)
    }
    condition = attributes.get("ITEM_CONDITION", {})
    if (
        not str(condition.get("value_id") or "").strip()
        or "values" in condition
    ):
        raise RuntimeError(
            "传统 Global Items ITEM_CONDITION 必须使用 value_id/value_name"
        )
    return item_payload, publication


def _traditional_response_entry_error(value: dict[str, Any]) -> bool:
    return bool(
        value.get("error") not in (None, "", [], {})
        or value.get("errors") not in (None, "", [], {})
        or value.get("success") is False
    )


def _traditional_response_root_error(response: dict[str, Any]) -> str:
    if response.get("error") not in (None, "", [], {}):
        return f"Mercado Libre 返回 error：{response.get('error')}"
    if response.get("errors") not in (None, "", [], {}):
        return f"Mercado Libre 返回 errors：{response.get('errors')}"
    if response.get("success") is False:
        return "Mercado Libre 明确返回 success=false"
    return ""


def _traditional_response_error_messages(value: Any) -> list[str]:
    """从 Mercado 的嵌套 error/cause 结构提取稳定、可读的消息。"""

    messages: list[str] = []
    if isinstance(value, list):
        for item in value:
            messages.extend(_traditional_response_error_messages(item))
    elif isinstance(value, dict):
        cause = value.get("cause")
        if isinstance(cause, (dict, list)):
            messages.extend(_traditional_response_error_messages(cause))
        if not messages:
            for key in ("message", "error", "code"):
                text = str(value.get(key) or "").strip()
                if text:
                    messages.append(text)
                    break
        if not messages and value.get("success") is False:
            messages.append("success=false")
    else:
        text = str(value or "").strip()
        if text:
            messages.append(text)
    return list(dict.fromkeys(messages))


def _traditional_site_item_error_detail(
    item: dict[str, Any],
    *,
    index: int,
) -> dict[str, Any]:
    raw_error = (
        item.get("error")
        if item.get("error") not in (None, "", [], {})
        else item.get("errors")
        if item.get("errors") not in (None, "", [], {})
        else {"success": False}
    )
    messages = _traditional_response_error_messages(raw_error)
    if not messages:
        messages = ["Mercado Libre 未说明失败原因"]
    return {
        "index": index,
        "site_id": str(
            item.get("site_id") or item.get("siteId") or ""
        ).strip().upper(),
        "logistic_type": str(
            item.get("logistic_type") or item.get("logisticType") or ""
        ).strip().lower(),
        "messages": messages,
        "error": deepcopy(raw_error),
        "raw": deepcopy(item),
    }


def _traditional_response_failure(
    response: dict[str, Any],
    *,
    error_code: str,
    summary: str,
    field_errors: dict[str, list[str]] | None = None,
    site_item_errors: list[dict[str, Any]] | None = None,
    next_action: str = "核对 Mercado Libre 返回内容后再重试。",
    outcome_unknown: bool = False,
    merge_publication: bool = False,
) -> dict[str, Any]:
    error_map: dict[str, Any] = {
        "summary": summary,
        "error_code": error_code,
        "retryable": False,
        "field_errors": dict(field_errors or {}),
        "next_action": next_action,
        # 顶层结果需要用规范化 error 覆盖远端 error；原始响应在这里完整保留。
        "raw": deepcopy(response),
    }
    if site_item_errors:
        error_map["site_item_errors"] = deepcopy(site_item_errors)
    return {
        "error_code": error_code,
        "error": summary,
        "error_map": error_map,
        "outcome_unknown": outcome_unknown,
        "merge_publication": merge_publication,
    }


def _traditional_publication_markets(
    publication: dict[str, Any],
) -> tuple[
    dict[tuple[str, str], dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    by_operation: dict[tuple[str, str], dict[str, Any]] = {}
    by_item_id: dict[str, dict[str, Any]] = {}
    for market in publication.get("markets", []):
        if not isinstance(market, dict):
            continue
        key = _market_key(market)
        if key[0] and key[1]:
            by_operation[key] = market
        item_id = str(market.get("item_id") or "").strip()
        if item_id:
            by_item_id[item_id] = market
    return by_operation, by_item_id


def _traditional_create_response_failure(
    response: dict[str, Any],
    *,
    publication: dict[str, Any],
    requested_sites: list[dict[str, Any]],
) -> dict[str, Any]:
    root_error = _traditional_response_root_error(response)
    if root_error:
        return _traditional_response_failure(
            response,
            error_code="MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID",
            summary=root_error,
        )

    expected_keys = [_market_key(site) for site in requested_sites]
    if (
        not expected_keys
        or any(not key[0] or not key[1] for key in expected_keys)
        or len(set(expected_keys)) != len(expected_keys)
    ):
        return _traditional_response_failure(
            response,
            error_code="MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID",
            summary="传统 Global Items 创建请求包含无效或重复的销售 operation",
        )
    publication_by_operation, _ = _traditional_publication_markets(publication)
    expected_sellers: dict[tuple[str, str], str] = {}
    for key in expected_keys:
        seller_id = str(
            publication_by_operation.get(key, {}).get("seller_id") or ""
        ).strip()
        if not seller_id:
            return _traditional_response_failure(
                response,
                error_code="MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID",
                summary=(
                    f"传统 Global Items 创建缺少 {key[0]} + {key[1]} "
                    "的 binding seller_id"
                ),
            )
        expected_sellers[key] = seller_id

    account_user_id = str(publication.get("account_user_id") or "").strip()
    response_parent_seller = str(response.get("seller_id") or "").strip()
    if (
        account_user_id
        and response_parent_seller
        and response_parent_seller != account_user_id
    ):
        return _traditional_response_failure(
            response,
            error_code="MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID",
            summary="传统 Global Items 响应 parent seller_id 与当前 CBT 父账号不一致",
        )

    site_items = response.get("site_items")
    if not isinstance(site_items, list) or len(site_items) != len(expected_keys):
        return _traditional_response_failure(
            response,
            error_code="MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID",
            summary="传统 Global Items 创建响应与请求的销售 operation 数量不一致",
            outcome_unknown=True,
            next_action=(
                "响应未完整覆盖本次销售 operation；请先在 Mercado Libre 后台"
                "核对是否已创建远端 Item，确认前不要重新发布。"
            ),
        )
    seen: set[tuple[str, str]] = set()
    failed_items: list[dict[str, Any]] = []
    successful_items = 0
    for index, item in enumerate(site_items):
        if not isinstance(item, dict):
            return _traditional_response_failure(
                response,
                error_code="MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID",
                summary="传统 Global Items 创建响应包含非 object 的 site_item",
                outcome_unknown=True,
            )
        key = _market_key(item)
        if key not in expected_sellers or key in seen:
            return _traditional_response_failure(
                response,
                error_code="MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID",
                summary="传统 Global Items 创建响应包含缺失、重复或未请求的销售 operation",
                outcome_unknown=True,
            )
        seen.add(key)
        if _traditional_response_entry_error(item):
            failed_items.append(
                _traditional_site_item_error_detail(item, index=index)
            )
            continue
        if not str(item.get("item_id") or item.get("id") or "").strip():
            return _traditional_response_failure(
                response,
                error_code="MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID",
                summary=(
                    f"传统 Global Items 创建响应的 {key[0]} + {key[1]} "
                    "缺少 item_id"
                ),
                outcome_unknown=True,
            )
        seller_id = str(item.get("seller_id") or "").strip()
        if seller_id != expected_sellers[key]:
            return _traditional_response_failure(
                response,
                error_code="MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID",
                summary=(
                    f"传统 Global Items 响应的 {key[0]} + {key[1]} "
                    "seller_id 与 binding 不一致"
                ),
                outcome_unknown=True,
            )
        successful_items += 1
    if seen != set(expected_keys):
        return _traditional_response_failure(
            response,
            error_code="MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID",
            summary="传统 Global Items 创建响应未覆盖全部请求的销售 operation",
            outcome_unknown=True,
        )

    returned_parent_id = str(
        response.get("item_id") or response.get("id") or ""
    ).strip()
    if failed_items:
        site_messages = [
            (
                f"{item.get('site_id') or '-'}"
                f"/{item.get('logistic_type') or '-'}："
                + "；".join(item.get("messages") or [])
            )
            for item in failed_items
        ]
        summary = "传统 Global Items 市场创建失败：" + "；".join(site_messages)
        if successful_items and not returned_parent_id:
            return _traditional_response_failure(
                response,
                error_code="MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID",
                summary=(
                    summary
                    + "；其余市场返回成功，但响应缺少远端 parent item_id，"
                    "无法安全确认创建结果"
                ),
                field_errors={"sites_to_sell": site_messages},
                site_item_errors=failed_items,
                next_action=(
                    "响应同时包含成功与失败市场但缺少父身份；请先在 Mercado Libre "
                    "后台核对远端 Item，确认前不要重新发布。"
                ),
                outcome_unknown=True,
            )
        return _traditional_response_failure(
            response,
            error_code="MERCADOLIBRE_TRADITIONAL_SITE_ITEMS_FAILED",
            summary=summary,
            field_errors={"sites_to_sell": site_messages},
            site_item_errors=failed_items,
            next_action=(
                "按各销售市场返回的原因修复或移除不可发布目标；限流目标稍后"
                "重新预览并人工确认。"
            ),
            # 只在远端父身份明确时构建 partial publication，供终态落库并让
            # 下一次发布走 PUT，避免重复创建 Global Item。
            merge_publication=bool(returned_parent_id),
        )
    if not returned_parent_id:
        return _traditional_response_failure(
            response,
            error_code="MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID",
            summary="传统 Global Items 创建响应缺少远端 parent item_id",
            outcome_unknown=True,
            next_action=(
                "市场结果看似成功但父身份缺失；请先在 Mercado Libre 后台"
                "核对是否已创建远端 Item，确认前不要重新发布。"
            ),
        )
    return {}


def _traditional_update_response_error(
    response: dict[str, Any],
    *,
    publication: dict[str, Any],
    parent_item_id: str,
) -> str:
    root_error = _traditional_response_root_error(response)
    if root_error:
        return root_error
    returned_parent_id = str(
        response.get("item_id") or response.get("id") or ""
    ).strip()
    if returned_parent_id and returned_parent_id != parent_item_id:
        return "传统 Global Items 更新响应的 parent item_id 与请求不一致"

    publication_by_operation, publication_by_item_id = (
        _traditional_publication_markets(publication)
    )
    raw_items = (
        response.get("site_items")
        if isinstance(response.get("site_items"), list)
        else response.get("listing_sites")
        if isinstance(response.get("listing_sites"), list)
        else []
    )
    has_site_proof = False
    for item in raw_items:
        if not isinstance(item, dict) or _traditional_response_entry_error(item):
            return "传统 Global Items 更新响应包含失败或无效的市场结果"
        item_id = str(item.get("item_id") or item.get("id") or "").strip()
        key = _market_key(item)
        existing_market = publication_by_item_id.get(item_id)
        if existing_market is None and key[0] and key[1]:
            existing_market = publication_by_operation.get(key)
        if not item_id or existing_market is None:
            return "传统 Global Items 更新响应包含无法归属到既有 publication 的市场结果"
        response_seller_id = str(item.get("seller_id") or "").strip()
        expected_seller_id = str(existing_market.get("seller_id") or "").strip()
        if (
            response_seller_id
            and expected_seller_id
            and response_seller_id != expected_seller_id
        ):
            return "传统 Global Items 更新响应的 seller_id 与既有 publication 不一致"
        has_site_proof = True
    if not returned_parent_id and not has_site_proof:
        return "传统 Global Items 更新响应没有返回可验证的远端 item 身份"
    return ""


def _publish_mercadolibre_traditional_global_items(
    payload: dict[str, Any], token: str
) -> dict[str, Any]:
    item_payload, publication = _traditional_global_item_payload(payload)
    parent_item_id = str(publication.get("parent_item_id") or "").strip()
    operation = "updated" if parent_item_id else "created"
    endpoint = MERCADOLIBRE_TRADITIONAL_GLOBAL_ITEMS_ENDPOINT
    method = "POST"
    headers: dict[str, str] | None = {"parent-item-info": "true"}
    if parent_item_id:
        endpoint += "/" + quote(parent_item_id, safe="")
        method = "PUT"
        headers = None
    raw = _write_request_json(
        method,
        endpoint,
        token,
        item_payload,
        extra_headers=headers,
    )
    if not isinstance(raw, dict):
        raise RuntimeError("传统 Global Items 写入响应必须是 JSON object")
    response = dict(raw)
    requested_sites = [
        dict(site)
        for site in item_payload.get("sites_to_sell", [])
        if isinstance(site, dict)
    ]
    if parent_item_id:
        update_error = _traditional_update_response_error(
            response,
            publication=publication,
            parent_item_id=parent_item_id,
        )
        response_failure = (
            _traditional_response_failure(
                response,
                error_code="MERCADOLIBRE_TRADITIONAL_RESPONSE_INVALID",
                summary=update_error,
            )
            if update_error
            else {}
        )
    else:
        response_failure = _traditional_create_response_failure(
            response,
            publication=publication,
            requested_sites=requested_sites,
        )
    if response_failure:
        result = {
            **response,
            "ok": False,
            "operation": operation,
            "listing_model": MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS,
            "error_code": response_failure["error_code"],
            "error": response_failure["error"],
            "error_map": response_failure["error_map"],
        }
        # 远端字段不能伪装成本地可信 publication；只有通过完整 operation
        # 闭包校验且返回父身份的部分成功响应才允许落库。
        result.pop("publication", None)
        if response_failure.get("outcome_unknown"):
            result.update(
                {
                    "status": "outcome_unknown",
                    "outcome_unknown": True,
                    "remote_write_dispatched": True,
                }
            )
        if response_failure.get("merge_publication"):
            result["publication"] = mercadolibre_publication_from_response(
                response,
                existing=publication,
                family_name=str(item_payload.get("title") or ""),
                requested_sites=requested_sites,
                updated_at=_now_iso(),
                listing_model=MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS,
            )
        return result
    merge_response = dict(response)
    if parent_item_id:
        # 仅在原始响应已通过远端身份校验后，为 publication merge 补齐父 ID。
        merge_response.setdefault("item_id", parent_item_id)
    merged_publication = mercadolibre_publication_from_response(
        merge_response,
        existing=publication,
        family_name=str(item_payload.get("title") or ""),
        requested_sites=requested_sites,
        updated_at=_now_iso(),
        listing_model=MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS,
    )
    return {
        **response,
        "ok": True,
        "operation": operation,
        "listing_model": MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS,
        "publication": merged_publication,
    }


def publish_mercadolibre(payload: dict[str, Any], token: str) -> dict[str, Any]:
    """按可信 ``_listing_model`` 显式分发；远端错误绝不切换模型。"""

    listing_model = require_mercadolibre_listing_model(
        payload.get("_listing_model") if isinstance(payload, dict) else None
    )
    if listing_model == MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS:
        return _publish_mercadolibre_user_products(payload, token)
    if listing_model == MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS:
        return _publish_mercadolibre_traditional_global_items(payload, token)
    raise AssertionError("unreachable Mercado Libre listing model")


def poll_mercadolibre_publish_status(
    pending: dict[str, Any],
    token: str,
    *,
    max_confirmation_polls: int = 300,
) -> dict[str, Any]:
    """确认 Global Update 异步任务；成功后再执行尚未添加的 marketplace。"""

    task_ids = [
        str(item or "").strip()
        for item in pending.get("task_ids", [])
        if str(item or "").strip()
    ]
    if not task_ids:
        task_id = str(pending.get("task_id") or "").strip()
        task_ids = [task_id] if task_id else []
    if not task_ids:
        raise RuntimeError("Mercado Libre 待确认更新缺少 task_id")

    publication = normalize_mercadolibre_publication(
        pending.get("publication")
    )
    task_results: list[dict[str, Any]] = []
    still_pending = False
    task_failed = False
    task_unconfirmed = False
    pending_statuses = {"pending", "processing", "queued"}
    for task_id in task_ids:
        response = request_json(
            "GET",
            MERCADOLIBRE_USER_PRODUCT_TASK_ENDPOINT
            + quote(task_id, safe=""),
            token,
        )
        task = dict(response) if isinstance(response, dict) else {}
        task_results.append(task)
        returned_task_id = str(task.get("task_id") or "").strip()
        if returned_task_id != task_id:
            task_unconfirmed = True
        task_status = str(task.get("status") or "").strip().lower()
        user_products = (
            task.get("user_products")
            if isinstance(task.get("user_products"), list)
            else []
        )
        relevant_user_products = [
            item
            for item in user_products
            if isinstance(item, dict)
            and _task_entry_match(publication, item) is not None
        ]
        relevant_status_values = [
            str(item.get("status") or "").strip().lower()
            for item in relevant_user_products
        ]
        relevant_statuses = set(relevant_status_values)

        if task_status in pending_statuses:
            still_pending = True
            continue
        if task_status in {"failed", "error"}:
            task_failed = True
            continue
        # family task 会包含兄弟变体；只允许当前 Siteless/Local UP 的精确
        # identity 参与本 publication 的终态判断。
        if task_status != "finished" or not relevant_user_products:
            task_unconfirmed = True
        elif relevant_statuses.intersection(pending_statuses):
            still_pending = True
        elif (
            relevant_statuses <= {"succeeded", "failed", "error"}
            and relevant_statuses.intersection({"failed", "error"})
        ):
            task_failed = True
        elif not all(
            status == "succeeded" for status in relevant_status_values
        ):
            task_unconfirmed = True
    poll_count = max(
        0,
        int(pending.get("confirmation_poll_count") or 0),
    ) + 1
    bounded_max_polls = max(1, int(max_confirmation_polls or 300))
    if task_unconfirmed:
        return _confirmation_outcome_unknown(
            pending,
            publication=publication,
            task_ids=task_ids,
            task_results=task_results,
            error_code="MERCADOLIBRE_CONFIRMATION_RESPONSE_INVALID",
            error=(
                "Mercado Libre 异步任务未返回可验证的 finished + "
                "user_products[].status=succeeded 终态，必须先对账。"
            ),
        )
    # 根任务或当前 User Product 仍在 processing 时必须继续只读轮询；
    # sibling 的 processing/failed 状态不属于当前 publication。
    if still_pending:
        if poll_count >= bounded_max_polls:
            return _confirmation_outcome_unknown(
                pending,
                publication=publication,
                task_ids=task_ids,
                task_results=task_results,
                error_code="MERCADOLIBRE_CONFIRMATION_TIMEOUT",
                error=(
                    "Mercado Libre 异步任务超过最大确认轮次仍未结束，"
                    "已停止等待且禁止重放写请求。"
                ),
            )
        return {
            **pending,
            "ok": True,
            "status": "pending_confirmation",
            "task_results": task_results,
            "confirmation_poll_count": poll_count,
        }

    continuation = (
        pending.get("continuation")
        if isinstance(pending.get("continuation"), dict)
        else {}
    )
    pending_listing_updates = (
        continuation.get("pending_listing_updates")
        if isinstance(continuation.get("pending_listing_updates"), list)
        else []
    )
    publication = _publication_with_task_results(
        publication,
        task_results,
        pending_listing_updates=pending_listing_updates,
    )
    requested_sites = [
        dict(item)
        for item in continuation.get("requested_sites", [])
        if isinstance(item, dict)
    ]
    additions = [
        dict(item)
        for item in continuation.get("additions", [])
        if isinstance(item, dict)
    ]
    already_added_sites = [
        str(item or "").strip().upper()
        for item in continuation.get("added_sites", [])
        if str(item or "").strip()
    ]
    if not task_failed:
        pending_confirmed_fields = (
            continuation.get("pending_confirmed_fields")
            if isinstance(
                continuation.get("pending_confirmed_fields"),
                dict,
            )
            else {}
        )
        publication = _publication_with_confirmed_payload(
            publication,
            pending_confirmed_fields,
        )
    family_name = str(
        continuation.get(
            "confirmed_family_name" if task_failed else "family_name"
        )
        or ""
    )
    result = _finish_existing_update(
        response={
            "id": continuation.get("siteless_user_product_id")
            or publication.get("siteless_user_product_id"),
            # 精确失败已落到 publication；中性响应避免 family task 结果
            # 再被 response-error 路径扩散到无关市场。
            "success": True,
        },
        publication=publication,
        family_name=family_name,
        requested_sites=requested_sites,
        additions=[] if task_failed else additions,
        token=token,
    )
    result.update(
        {
            "task_id": task_ids[0],
            "task_ids": task_ids,
            "task_results": task_results,
            "confirmation_started_at": str(
                pending.get("confirmation_started_at") or ""
            ),
            "confirmation_checked_at": _now_iso(),
        }
    )
    if not task_failed:
        result["confirmed_at"] = result["confirmation_checked_at"]
    if already_added_sites:
        result["operation"] = "marketplaces_added"
        result["added_sites"] = already_added_sites
    return result


__all__ = [
    "MERCADOLIBRE_TRADITIONAL_GLOBAL_ITEMS_ENDPOINT",
    "MERCADOLIBRE_USER_PRODUCT_FAMILIES_ENDPOINT",
    "MERCADOLIBRE_USER_PRODUCT_TASK_ENDPOINT",
    "poll_mercadolibre_publish_status",
    "publish_mercadolibre",
]
