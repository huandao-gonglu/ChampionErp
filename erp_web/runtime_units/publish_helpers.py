# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from typing import Any

from erp_web import marketplaces as publisher
from erp_web.context import get_context
from erp_web.marketplace_registry import platform_title_limit
from erp_web.product_model import (
    default_draft,
    normalize_mercadolibre_publication,
    normalize_draft_image_refs,
    normalize_draft_target_site,
    normalize_mercadolibre_sites_to_sell,
    validate_category_precheck,
)
from erp_web.services.listing_currency_service import require_store_listing_currency
from erp_web.services.mercadolibre_listing_model import (
    MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS,
    MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS,
    require_mercadolibre_listing_model,
)
from erp_web.services.mercadolibre_target_contract import (
    mercadolibre_global_target_contract,
    mercadolibre_payload_pricing_contract,
)
from erp_web.schemas.category_definition import CategoryDefinition
from erp_web.services.mercadolibre_attribute_contract import (
    compile_mercadolibre_attributes,
)
from erp_web.stores.config_store import summarize_store_auth_states
from erp_web.stores.product_store import normalize_product_fields

from .copy_generation import apply_product_drafts_to_plan, build_plan_for_platform
from .draft_publish_context import draft_for_publish_target
from .image_pool_core import (
    _source_pool_items,
    current_image_pool,
    image_pool_refs_for_platform,
)

def assign_upc() -> dict[str, Any]:
    """在同一事务内为当前商品占用 UPC 并保存商品/草稿。"""
    product = normalize_product_fields(get_context().products.load_product())
    value, saved = get_context().products.assign_upc_to_product(product)
    if not value:
        return {"ok": False, "error": "UPC 池为空，请先在设置中导入 UPC"}
    return {
        "ok": True,
        "upc": value,
        "product": saved,
        "productsIndex": get_context().products.load_products_index(),
        "imagePool": current_image_pool(saved),
        "upcPool": get_context().db.upc_pool_stats(),
        "message": f"UPC 已分配：{value}",
    }


def build_mercadolibre_publish_payload(
    product: dict[str, Any],
    config: dict[str, Any],
    picture_refs: list[str] | None = None,
    *,
    category_definition: CategoryDefinition | None = None,
) -> dict[str, Any]:
    plan = apply_product_drafts_to_plan(product, build_plan_for_platform(product, "mercadolibre"))
    draft = _draft_for_selected_target(product, "mercadolibre")
    payload_config = deepcopy(config)
    store = payload_config.setdefault("mercadolibre", {})
    store["category_id"] = str(draft.get("category_id") or "").strip()
    site_id = str(draft.get("site") or draft.get("site_id") or "").strip().upper()
    if site_id:
        store["site_id"] = site_id
    listing = payload_config.setdefault("listing", {})
    if category_definition is None:
        raise RuntimeError(
            "MERCADOLIBRE_CATEGORY_DEFINITION_REQUIRED: "
            "构建 payload 前必须加载当前 Mercado Libre 类目定义"
        )
    if (
        category_definition.platform != "mercadolibre"
        or category_definition.category_id
        != str(draft.get("category_id") or "").strip()
    ):
        raise RuntimeError(
            "MERCADOLIBRE_CATEGORY_DEFINITION_MISMATCH: "
            "类目定义与当前 Mercado Libre 草稿不一致"
        )
    selected_price, listing_currency = _selected_price_and_currency(
        draft, "mercadolibre", site_id
    )
    # 以下字段的唯一事实源是当前草稿/核价投影。即使值为空也必须覆盖配置
    # 副本，否则用户清空字段后会悄悄回落到旧 listing 配置并生成陈旧 payload。
    listing.update({
        "mercadolibre_price": selected_price,
        "price": selected_price,
        "currency_id": listing_currency,
        "stock": draft.get("stock"),
        "sku": draft.get("sku"),
        "upc": draft.get("upc"),
        "model": draft.get("model"),
        "mercadolibre_title": draft.get("title"),
        "mercadolibre_global_title": draft.get("global_title"),
        "mercadolibre_language": draft.get("language"),
    })
    # 空列表也必须覆盖配置副本，避免旧配置意外成为 Global Selling fallback。
    listing["mercadolibre_sites_to_sell"] = (
        normalize_mercadolibre_sites_to_sell(draft.get("sites_to_sell"))
    )
    listing["mercadolibre_sale_terms"] = (
        deepcopy(draft.get("sale_terms"))
        if isinstance(draft.get("sale_terms"), list)
        else []
    )
    refs = (
        image_pool_refs_for_platform(product, "mercadolibre")
        if picture_refs is None
        else list(picture_refs)
    )
    listing_model = require_mercadolibre_listing_model(store.get("listing_model"))
    compilation = compile_mercadolibre_attributes(
        draft,
        category_definition,
        listing_model=listing_model,
    )
    if compilation.issues:
        raise RuntimeError(
            "MERCADOLIBRE_ATTRIBUTE_CONTRACT_INVALID: "
            + "；".join(issue.message for issue in compilation.issues)
        )
    payload = publisher.build_mercadolibre_payload(
        product,
        plan,
        payload_config,
        refs,
        category_attributes=list(compilation.attributes),
    )
    publication = normalize_mercadolibre_publication(draft.get("publication"))
    account_user_id = str(store.get("user_id") or "").strip()
    if listing_model == MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS:
        parent_item_id = str(
            publication.get("parent_item_id")
            or ""
        ).strip()
        bindings = (
            store.get("marketplace_bindings")
            if isinstance(store.get("marketplace_bindings"), list)
            else []
        )
        existing_markets = {
            (
                str(item.get("site_id") or "").strip().upper(),
                str(item.get("logistic_type") or "").strip().lower(),
            ): item
            for item in publication.get("markets", [])
            if isinstance(item, dict)
        }
        for target in normalize_mercadolibre_sites_to_sell(
            draft.get("sites_to_sell")
        ):
            key = (target["site_id"], target["logistic_type"])
            binding = next(
                (
                    item
                    for item in bindings
                    if isinstance(item, dict)
                    and str(item.get("site_id") or "").strip().upper() == key[0]
                    and str(item.get("logistic_type") or "").strip().lower()
                    == key[1]
                ),
                {},
            )
            existing_markets[key] = {
                **existing_markets.get(key, {}),
                "site_id": key[0],
                "logistic_type": key[1],
                "seller_id": str(
                    existing_markets.get(key, {}).get("seller_id")
                    or binding.get("seller_id")
                    or ""
                ).strip(),
            }
        has_remote_publication = bool(
            parent_item_id
            or any(
                isinstance(item, dict)
                and (
                    item.get("item_id")
                    or item.get("user_product_id")
                )
                for item in publication.get("markets", [])
            )
        )
        publication_model = str(publication.get("model") or "").strip()
        payload["_publication"] = {
            **publication,
            "model": (
                publication_model
                if has_remote_publication and publication_model
                else listing_model
            ),
            "account_user_id": str(
                publication.get("account_user_id")
                or ("" if has_remote_publication else account_user_id)
            ).strip(),
            "parent_item_id": parent_item_id,
            "markets": list(existing_markets.values()),
        }
        return payload
    siteless_id = str(
        publication.get("siteless_user_product_id") or ""
    ).strip()
    publication_account_user_id = str(
        publication.get("account_user_id") or ""
    ).strip()
    seed_account_user_id = (
        publication_account_user_id if siteless_id else account_user_id
    )
    bindings = (
        store.get("marketplace_bindings")
        if isinstance(store.get("marketplace_bindings"), list)
        else []
    )
    existing_markets = {
        (
            str(item.get("site_id") or "").strip().upper(),
            str(item.get("logistic_type") or "").strip().lower(),
        ): item
        for item in publication.get("markets", [])
        if isinstance(item, dict)
    }
    seed_market_map = {
        key: dict(item) for key, item in existing_markets.items()
    }
    for target in normalize_mercadolibre_sites_to_sell(
        draft.get("sites_to_sell")
    ):
        key = (target["site_id"], target["logistic_type"])
        binding = next(
            (
                item
                for item in bindings
                if isinstance(item, dict)
                and str(item.get("site_id") or "").strip().upper() == key[0]
                and str(item.get("logistic_type") or "").strip().lower()
                == key[1]
            ),
            {},
        )
        seed_market_map[key] = {
            **existing_markets.get(key, {}),
            "site_id": key[0],
            "logistic_type": key[1],
            "seller_id": str(
                existing_markets.get(key, {}).get("seller_id")
                or binding.get("seller_id")
                or ""
            ).strip(),
        }
    seed_markets = list(seed_market_map.values())
    if publication or seed_account_user_id or seed_markets:
        payload["_publication"] = {
            **publication,
            "model": listing_model,
            "account_user_id": seed_account_user_id,
            "markets": seed_markets,
        }
    return payload


def remote_publish_identity(result: Any) -> dict[str, Any]:
    """从各平台包装层中提取可持久化的远端刊登身份。"""

    current = result if isinstance(result, dict) else {}
    candidates: list[dict[str, Any]] = []
    for _ in range(4):
        if not isinstance(current, dict) or current in candidates:
            break
        candidates.append(current)
        nested = current.get("result")
        if not isinstance(nested, dict):
            break
        current = nested

    identity: dict[str, Any] = {}
    for candidate in candidates:
        siteless_id = candidate.get("siteless_user_product_id")
        item_id = candidate.get("item_id") or candidate.get("id")
        product_id = candidate.get("product_id")
        offer_id = candidate.get("offer_id")
        external_id = (
            candidate.get("external_id")
            or siteless_id
            or item_id
            or product_id
        )
        operation = candidate.get("operation")
        if item_id not in (None, "") and "item_id" not in identity:
            identity["item_id"] = str(item_id)
        if siteless_id not in (None, "") and "siteless_user_product_id" not in identity:
            identity["siteless_user_product_id"] = str(siteless_id)
        family_id = candidate.get("siteless_family_id")
        if family_id not in (None, "") and "siteless_family_id" not in identity:
            identity["siteless_family_id"] = str(family_id)
        if product_id not in (None, "", 0) and "product_id" not in identity:
            identity["product_id"] = product_id
        if offer_id not in (None, "") and "offer_id" not in identity:
            identity["offer_id"] = str(offer_id)
        if external_id not in (None, "", 0) and "external_id" not in identity:
            identity["external_id"] = str(external_id)
        if operation not in (None, "") and "operation" not in identity:
            identity["operation"] = str(operation)
    return identity


def mercadolibre_publication_from_result(result: Any) -> dict[str, Any]:
    """从发布队列的多层包装中提取 Mercado User Products 映射。"""

    current = result if isinstance(result, dict) else {}
    for _ in range(5):
        publication = normalize_mercadolibre_publication(
            current.get("publication")
        )
        if publication:
            return publication
        nested = current.get("result")
        if not isinstance(nested, dict):
            break
        current = nested
    return {}


def validate_mercadolibre_publish_payload(payload: Any, config: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    payload = payload if isinstance(payload, dict) else {}
    store = config.get("mercadolibre") if isinstance(config.get("mercadolibre"), dict) else {}
    listing = config.get("listing") if isinstance(config.get("listing"), dict) else {}
    if not store.get("access_token"):
        missing.append("Mercado Libre Access Token")
    try:
        listing_model = require_mercadolibre_listing_model(
            payload.get("_listing_model")
        )
    except RuntimeError as exc:
        missing.append(str(exc))
        return missing
    if listing_model != str(store.get("listing_model") or "").strip():
        missing.append("payload 刊登模型与当前授权账号 listing_model 不一致")
    if str(store.get("account_site_id") or "").strip().upper() != "CBT":
        missing.append("必须授权 CBT Global Selling 父账号")
    if (
        listing_model == MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS
        and store.get("user_product_seller") is not True
    ):
        missing.append("当前账号未开通 User Products")
    if not payload.get("category_id"):
        missing.append("类目 ID")
    if not str(payload.get("category_id") or "").strip().upper().startswith("CBT"):
        missing.append("Global Selling 必须使用 CBT 类目 ID")
    if str(payload.get("currency_id") or "").strip().upper() != "USD":
        missing.append("标准 CBT Global Selling 刊登币种必须为 USD")
    pricing_mode, pricing_issues = mercadolibre_payload_pricing_contract(
        payload,
        listing_model=listing_model,
    )
    missing.extend(issue["message"] for issue in pricing_issues)
    if not payload.get("attributes"):
        missing.append("类目属性")
    sites_to_sell = (
        payload.get("sites_to_sell")
        if isinstance(payload.get("sites_to_sell"), list)
        else []
    )
    publication = normalize_mercadolibre_publication(
        payload.get("_publication")
    )
    raw_publication = (
        payload.get("_publication")
        if isinstance(payload.get("_publication"), dict)
        else {}
    )
    current_account_user_id = str(store.get("user_id") or "").strip()
    publication_account_user_id = str(
        publication.get("account_user_id") or ""
    ).strip()
    if not current_account_user_id:
        missing.append("当前 CBT 父账号缺少稳定 user_id")
    remote_parent_id = str(
        (
            publication.get("siteless_user_product_id")
            if listing_model == MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS
            else publication.get("parent_item_id")
        )
        or ""
    ).strip()
    has_remote_market_identity = any(
        isinstance(item, dict)
        and (
            str(item.get("item_id") or "").strip()
            or str(item.get("user_product_id") or "").strip()
        )
        for item in publication.get("markets", [])
    )
    if remote_parent_id and not publication_account_user_id:
        missing.append("已发布 Mercado publication 缺少 CBT 父账号归属，禁止更新")
    elif (
        remote_parent_id
        and current_account_user_id
        and publication_account_user_id != current_account_user_id
    ):
        missing.append("已发布 Mercado publication 不属于当前 CBT 父账号")

    if listing_model == MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS:
        if raw_publication and raw_publication.get("model") != listing_model:
            missing.append("User Products publication.model 缺失或不匹配")
        if not payload.get("family_name"):
            missing.append("产品族名称 family_name")
        for field in ("title", "variations", "buying_mode", "catalog_listing"):
            if field in payload:
                missing.append(f"User Products payload 禁止 {field} 字段")
        siteless_id = str(publication.get("siteless_user_product_id") or "").strip()
        if (
            publication.get("parent_item_id")
            and not siteless_id
        ):
            missing.append("传统 Global Items publication 不能通过 User Products 路由更新")
        if siteless_id and publication.get("model") != listing_model:
            missing.append("已发布 publication 模型不是 user_products")
    else:
        if not payload.get("title"):
            missing.append("标题 title")
        elif len(str(payload.get("title") or "").strip()) > platform_title_limit(
            "mercadolibre"
        ):
            missing.append("传统 Global Items 根 title 超过平台字符限制")
        for field in ("family_name", "global_net_proceeds", "variations"):
            if field in payload:
                missing.append(f"传统 Global Items payload 禁止 {field} 字段")
        if payload.get("_publication") and publication.get("model") != listing_model:
            missing.append("传统 Global Items publication.model 不匹配")
        if has_remote_market_identity and not remote_parent_id:
            missing.append(
                "已发布传统 Global Items publication 缺少 parent_item_id，禁止重复创建"
            )
        parent_payload_error = (
            publisher.mercadolibre_traditional_parent_payload_error(
                payload,
                publication,
            )
        )
        if parent_payload_error:
            parent_error_text = (
                f"{parent_payload_error['error_code']}: "
                f"{parent_payload_error['message']}"
            )
            details = (
                parent_payload_error.get("details")
                if isinstance(parent_payload_error.get("details"), dict)
                else {}
            )
            next_action = str(details.get("next_action") or "").strip()
            if next_action:
                parent_error_text += f" 处理建议：{next_action}"
            missing.append(parent_error_text)

    _, target_issues = mercadolibre_global_target_contract(
        sites_to_sell,
        store.get("marketplace_bindings"),
        listing_model=listing_model,
        required_pricing_mode=pricing_mode,
        require_user_products=(
            listing_model == MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS
        ),
        require_pricing_amounts=True,
        language=str(listing.get("mercadolibre_language") or "").strip(),
    )
    for issue in target_issues:
        if issue["message"] not in missing:
            missing.append(issue["message"])
    sale_terms = (
        payload.get("sale_terms")
        if isinstance(payload.get("sale_terms"), list)
        else []
    )
    warranty_type = next(
        (
            item
            for item in sale_terms
            if isinstance(item, dict)
            and str(item.get("id") or "").strip() == "WARRANTY_TYPE"
        ),
        {},
    )
    if not warranty_type or not str(
        warranty_type.get("value_id")
        or warranty_type.get("value_name")
        or ""
    ).strip():
        missing.append("sale_terms / warranty 尚未配置完整")
    if listing_model == MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS:
        pictures = payload.get("pictures")
        if not isinstance(pictures, list) or not pictures:
            missing.append("传统 Global Items 根级 pictures 不能为空")
            pictures = []
        if any(
            isinstance(site, dict) and "pictures" in site
            for site in sites_to_sell
        ):
            missing.append("传统 Global Items pictures 只能位于 payload 根级")
    else:
        pictures = payload.get("pictures")
        if not isinstance(pictures, list) or not pictures:
            missing.append("图片")
            pictures = []
    if pictures and any(
        not isinstance(picture, dict)
        or not str(picture.get("id") or "").strip()
        or picture.get("source")
        for picture in pictures
    ):
        missing.append("Mercado 图片必须先上传并只使用 picture ID")

    attributes = {
        str(item.get("id") or "").strip(): item
        for item in payload.get("attributes", [])
        if isinstance(item, dict)
    } if isinstance(payload.get("attributes"), list) else {}
    raw_attribute_ids = [
        str(item.get("id") or "").strip()
        for item in payload.get("attributes", [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ] if isinstance(payload.get("attributes"), list) else []
    if len(raw_attribute_ids) != len(set(raw_attribute_ids)):
        missing.append("类目属性 ID 不能重复")
    condition = attributes.get("ITEM_CONDITION", {})
    if listing_model == MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS:
        values = condition.get("values") if isinstance(condition, dict) else None
        if not isinstance(values, list) or not values or "value_id" in condition:
            missing.append("User Products ITEM_CONDITION 必须使用 values 结构")
        if any(isinstance(site, dict) and "title" in site for site in sites_to_sell):
            missing.append("User Products sites_to_sell 禁止 title 字段")
    else:
        if (
            not isinstance(condition, dict)
            or not str(condition.get("value_id") or "").strip()
            or "values" in condition
        ):
            missing.append("传统 Global Items ITEM_CONDITION 必须使用 value_id/value_name")
        if any(
            not isinstance(site, dict) or not str(site.get("title") or "").strip()
            for site in sites_to_sell
        ):
            missing.append("传统 Global Items 每个 sites_to_sell 必须包含 title")
    return missing


def validate_publish_payload(platform: str, payload: Any, config: dict[str, Any]) -> list[str]:
    from .publish_adapter import require_publishing_adapter

    return require_publishing_adapter(platform).validate_payload(payload, config)


def precheck_item(code: str, field: str, message: str, severity: str = "error", next_action: str = "") -> dict[str, str]:
    return {
        "code": str(code or "").strip(),
        "field": str(field or "").strip(),
        "message": str(message or "").strip(),
        "severity": str(severity or "error").strip() or "error",
        "next_action": str(next_action or "").strip(),
    }


def compact_precheck_items(items: list[Any]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    index_by_key: dict[tuple[str, str, str, str, str], int] = {}
    counts: list[int] = []
    for raw in items:
        if not isinstance(raw, dict):
            raw = precheck_item("", "", str(raw or ""))
        item = precheck_item(
            str(raw.get("code") or ""),
            str(raw.get("field") or ""),
            str(raw.get("message") or ""),
            str(raw.get("severity") or "error"),
            str(raw.get("next_action") or ""),
        )
        key = (item["code"], item["field"], item["message"], item["severity"], item["next_action"])
        if key in index_by_key:
            idx = index_by_key[key]
            counts[idx] += 1
            compacted[idx]["message"] = f"{key[2]}（共 {counts[idx]} 次）"
            compacted[idx]["count"] = counts[idx]
            continue
        index_by_key[key] = len(compacted)
        counts.append(1)
        item["count"] = 1
        compacted.append(item)
    return compacted


def compact_precheck(precheck: dict[str, Any]) -> dict[str, Any]:
    errors = list(precheck.get("errors") or [])
    warnings = list(precheck.get("warnings") or [])
    compacted = dict(precheck)
    compacted["errors"] = compact_precheck_items(errors)
    compacted["warnings"] = compact_precheck_items(warnings)
    compacted["error_count"] = sum(int(item.get("count") or 1) for item in compacted["errors"])
    compacted["warning_count"] = sum(int(item.get("count") or 1) for item in compacted["warnings"])
    return compacted


def mercadolibre_picture_upload_error_message(exc: Exception) -> str:
    raw = str(exc)
    if "File not compatible with pictures engine" in raw:
        return "Mercado Libre 图片上传失败：图片文件格式或内容不兼容 Mercado Libre 图片引擎"
    if len(raw) > 240:
        raw = raw[:237].rstrip() + "..."
    return f"Mercado Libre 图片上传失败：{raw}"


def compact_publish_failure_response(status: str, error: str, saved: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    response: dict[str, Any] = {"ok": False, "status": status, "error": error}
    precheck = extra.pop("precheck", None)
    if isinstance(precheck, dict):
        response["precheck"] = compact_precheck(precheck)
    if saved:
        response["product_id"] = str(saved.get("product_id") or "")
        response["productsIndex"] = (
            get_context().products.load_products_index()
        )
    for key, value in extra.items():
        if value not in (None, "", [], {}):
            response[key] = value
    return response


def _draft_for_platform(product: dict[str, Any], platform: str) -> dict[str, Any]:
    drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
    draft = drafts.get(platform) if isinstance(drafts, dict) else {}
    return draft if isinstance(draft, dict) else default_draft(platform)


def _draft_for_selected_target(
    product: dict[str, Any], platform: str
) -> dict[str, Any]:
    """把持久化 target_sites[] 的当前目标投影到临时 draft 根。"""

    draft = _draft_for_platform(product, platform)
    targets = (
        draft.get("target_sites")
        if isinstance(draft.get("target_sites"), list)
        else []
    )
    if not targets:
        return draft
    platform_key = str(platform or "").strip().lower()
    site_id = str(draft.get("site") or draft.get("site_id") or "").strip()
    target = next(
        (
            item
            for item in targets
            if isinstance(item, dict)
            and str(item.get("platform") or platform_key).strip().lower()
            == platform_key
            and (
                not site_id
                or str(item.get("site") or item.get("site_id") or "")
                .strip()
                .casefold()
                == site_id.casefold()
            )
        ),
        None,
    )
    if target is None:
        return draft
    return draft_for_publish_target(
        draft,
        normalize_draft_target_site(target, platform_key),
    )


def _selected_price_and_currency(
    draft: dict[str, Any], platform: str, site: str
) -> tuple[str, str]:
    """发布 payload 的价格与币种。

    币种只从店铺授权配置（发布上下文）读取，不从草稿/注册表猜值；价格取
    已核价的生效售价或草稿投影价，其币种必须与店铺币种一致。店铺币种未
    就绪时抛出 ``StoreCurrencyNotReadyError``，确定性阻断发布。
    """

    platform_key = str(platform or "").strip().lower()
    store_config = get_context().config.load_store_config()
    store = (
        store_config.get(platform_key)
        if isinstance(store_config.get(platform_key), dict)
        else {}
    )
    state = require_store_listing_currency(platform_key, store)
    currency = state["listing_currency"]

    price = str(draft.get("price") or "").strip()
    if price:
        return price, currency
    target_key = f"{platform_key}:{str(site).strip().lower()}"
    pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
    targets = pricing.get("targets") if isinstance(pricing.get("targets"), dict) else {}
    record = next(
        (item for key, item in targets.items() if str(key).strip().lower() == target_key and isinstance(item, dict)),
        {},
    )
    applied = record.get("applied_price") if isinstance(record.get("applied_price"), dict) else {}
    if str(applied.get("currency") or "").strip().upper() != currency:
        return "", currency
    return str(applied.get("amount") or "").strip(), currency


def _draft_images(product: dict[str, Any], platform: str, draft: dict[str, Any]) -> list[str]:
    refs = normalize_draft_image_refs(draft.get("images"))
    if not refs:
        return image_pool_refs_for_platform(product, platform)
    # 发布必须读取 canonical 图片池。展示图片池会为了本地预览把 URL 转成
    # /file?...，不能作为平台 payload 的来源。
    pool = _source_pool_items(product)
    asset_ref_map = {
        str(item.get("id") or item.get("asset_id") or "").strip(): str(item.get("url") or item.get("path") or item.get("preview_url") or "").strip()
        for item in pool
        if isinstance(item, dict)
    }
    images = [asset_ref_map.get(str(ref.get("asset_id") or "").strip(), "") for ref in refs]
    return [image for image in images if image]


def _has_main_image(product: dict[str, Any], platform: str, draft: dict[str, Any]) -> bool:
    draft_refs = normalize_draft_image_refs(draft.get("images"))
    if draft_refs:
        return any(ref.get("role") == "main" for ref in draft_refs)
    pool = current_image_pool(product)
    platform_items = []
    for item in pool:
        platforms = [str(value).strip().lower() for value in (item.get("platforms") or [])]
        if platforms and platform not in platforms:
            continue
        if str(item.get("status") or "").strip().lower() == "empty":
            continue
        platform_items.append(item)
        if bool(item.get("is_main")):
            return True
    if platform_items:
        return False
    return bool(_draft_images(product, platform, draft))


def _field_error_map(items: list[dict[str, Any]]) -> dict[str, list[str]]:
    mapped: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        if not field:
            continue
        message = str(item.get("message") or item.get("code") or "").strip()
        mapped.setdefault(field, [])
        if message:
            mapped[field].append(message)
    return mapped


def _required_attribute_summary(
    product: dict[str, Any],
    platform: str,
    category_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """必填属性摘要；规则只来自当次注入的定义，不再读商品/草稿副本。"""

    draft = _draft_for_platform(product, platform)
    category_id = str(draft.get("category_id") or "").strip()
    record = category_record if isinstance(category_record, dict) else None
    record_id = str((record or {}).get("category_id") or (record or {}).get("subject_id") or (record or {}).get("type_id") or "").strip()
    if category_id and record_id and record_id != category_id:
        record = None
    if not isinstance(record, dict):
        return {"required_count": 0, "filled_count": 0, "missing": []}
    missing = validate_category_precheck(product, platform, record)
    required_fields = [item for item in missing if str(item).startswith("attributes.")]
    attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    required_schema = [
        attr for attr in (attrs.get("required") or [])
        if isinstance(attr, dict) and bool(attr.get("required"))
    ]
    required_count = len(required_schema)
    return {
        "required_count": required_count,
        "filled_count": max(0, required_count - len(required_fields)),
        "missing": required_fields,
    }


def _masked_auth_status(platform: str, config: dict[str, Any]) -> tuple[str, str]:
    summary = summarize_store_auth_states(config).get(platform, {})
    return str(summary.get("status") or "未配置"), str(summary.get("next_action") or "")


__all__ = [
    "_draft_for_platform",
    "_draft_for_selected_target",
    "_draft_images",
    "_field_error_map",
    "_has_main_image",
    "_masked_auth_status",
    "_required_attribute_summary",
    "assign_upc",
    "build_mercadolibre_publish_payload",
    "compact_precheck",
    "compact_precheck_items",
    "compact_publish_failure_response",
    "precheck_item",
    "validate_mercadolibre_publish_payload",
    "validate_publish_payload",
]
