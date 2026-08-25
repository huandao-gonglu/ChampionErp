# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from typing import Any

from erp_web.context import AppContext, get_context
from erp_web.marketplace_registry import marketplace_site, marketplace_spec
from erp_web.product_model import (
    PLATFORMS,
    normalize_mercadolibre_sites_to_sell,
    normalize_platform_draft,
)
from erp_web.schemas.currency import StorePublishContext
from erp_web.services.listing_currency_service import (
    require_store_listing_currency,
    store_identity_for_platform,
    store_listing_currency_from_auth,
)
from erp_web.stores.product_store import normalize_product_fields


ResponseWithStatus = tuple[dict[str, Any], int]
TARGET_LISTING_KEYS = (
    "category_id",
    "description_category_id",
    "category_path",
    "attributes",
    "validation_errors",
    "category_precheck",
    "publish_status",
    "status",
    "last_precheck",
    "last_precheck_target",
    "last_publish_task",
    "sites_to_sell",
)

PRECHECK_TARGET_SNAPSHOT_KEYS = (
    "platform",
    "site",
    "language",
    "listing_currency",
    "category_id",
    "description_category_id",
    "category_path",
)


def _target_key(platform: str, site: str) -> str:
    return f"{str(platform or '').strip().lower()}:{str(site or '').strip().lower()}"


def _normalized_target(
    platform: str,
    site: str = "",
    *,
    context: AppContext | None = None,
) -> dict[str, Any]:
    platform_key = str(platform or "").strip().lower()
    raw_site = str(site or "").strip()
    spec = marketplace_spec(platform_key)
    if raw_site and spec is not None and not any(
        str(item.get("key") or "").casefold() == raw_site.casefold()
        or str(item.get("code") or "").casefold() == raw_site.casefold()
        for item in spec.sites
    ):
        return {
            "platform": "",
            "site": "",
            "language": "",
            "listing_currency": "",
            "currency_fingerprint": "",
        }
    selected_site = marketplace_site(platform_key, site)
    if platform_key not in PLATFORMS or not selected_site.get("code"):
        return {
            "platform": "",
            "site": "",
            "language": "",
            "listing_currency": "",
            "currency_fingerprint": "",
        }
    active_context = context or get_context()
    store_config = active_context.config.load_store_config()
    store = (
        store_config.get(platform_key)
        if isinstance(store_config.get(platform_key), dict)
        else {}
    )
    identity = store_identity_for_platform(platform_key, store)
    state = store_listing_currency_from_auth(platform_key, identity, store)
    return {
        "platform": platform_key,
        "site": selected_site["code"],
        "language": selected_site["language"],
        "listing_currency": state["listing_currency"],
        "currency_fingerprint": state["currency_fingerprint"],
    }


def build_store_publish_context(
    platform: str,
    site: str = "",
    *,
    context: AppContext | None = None,
) -> StorePublishContext:
    """发布前构造不可变店铺发布上下文。

    只从店铺授权配置读取发布币种；未就绪时抛出
    ``StoreCurrencyNotReadyError``，由调用方转为确定性阻断。
    """

    platform_key = str(platform or "").strip().lower()
    selected_site = marketplace_site(platform_key, site)
    active_context = context or get_context()
    store_config = active_context.config.load_store_config()
    store = (
        store_config.get(platform_key)
        if isinstance(store_config.get(platform_key), dict)
        else {}
    )
    state = require_store_listing_currency(platform_key, store)
    return {
        "platform": platform_key,
        "site": str(selected_site.get("code") or site or "").strip(),
        "store_identity": store_identity_for_platform(platform_key, store),
        "listing_currency": state["listing_currency"],
        "currency_fingerprint": state["currency_fingerprint"],
    }


def _precheck_target_snapshot(raw: Any) -> dict[str, str]:
    """只保留预检目标身份，禁止把历史目标再次嵌入草稿。"""

    if not isinstance(raw, dict):
        return {}
    aliases = {
        "category_id": "categoryId",
        "description_category_id": "descriptionCategoryId",
        "category_path": "categoryPath",
    }
    snapshot: dict[str, str] = {}
    for key in PRECHECK_TARGET_SNAPSHOT_KEYS:
        value = str(raw.get(key) or raw.get(aliases.get(key, "")) or "").strip()
        if value:
            snapshot[key] = value
    return snapshot


def _target_listing_fields(raw: dict[str, Any], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = fallback if isinstance(fallback, dict) else {}
    raw_attributes = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
    fallback_attributes = fallback.get("attributes") if isinstance(fallback.get("attributes"), dict) else {}
    attributes = raw_attributes if raw_attributes else fallback_attributes
    validation_errors = raw.get("validation_errors") if isinstance(raw.get("validation_errors"), list) else raw.get("validationErrors")
    fallback_validation_errors = fallback.get("validation_errors") if isinstance(fallback.get("validation_errors"), list) else []
    if not isinstance(validation_errors, list) or not validation_errors:
        validation_errors = fallback_validation_errors
    return {
        "category_id": str(raw.get("category_id") or raw.get("categoryId") or fallback.get("category_id") or "").strip(),
        "description_category_id": str(raw.get("description_category_id") or raw.get("descriptionCategoryId") or fallback.get("description_category_id") or "").strip(),
        "category_path": str(raw.get("category_path") or raw.get("categoryPath") or fallback.get("category_path") or "").strip(),
        "attributes": deepcopy(attributes),
        "validation_errors": deepcopy(validation_errors),
        "category_precheck": deepcopy(raw.get("category_precheck") if isinstance(raw.get("category_precheck"), dict) else raw.get("categoryPrecheck") if isinstance(raw.get("categoryPrecheck"), dict) else fallback.get("category_precheck") if isinstance(fallback.get("category_precheck"), dict) else {}),
        "publish_status": str(raw.get("publish_status") or raw.get("publishStatus") or fallback.get("publish_status") or "").strip(),
        "status": str(raw.get("status") or fallback.get("status") or "").strip(),
        "last_precheck": deepcopy(raw.get("last_precheck") if isinstance(raw.get("last_precheck"), dict) else raw.get("lastPrecheck") if isinstance(raw.get("lastPrecheck"), dict) else fallback.get("last_precheck") if isinstance(fallback.get("last_precheck"), dict) else {}),
        "last_precheck_target": _precheck_target_snapshot(
            raw.get("last_precheck_target")
            if isinstance(raw.get("last_precheck_target"), dict)
            else raw.get("lastPrecheckTarget")
            if isinstance(raw.get("lastPrecheckTarget"), dict)
            else fallback.get("last_precheck_target")
            if isinstance(fallback.get("last_precheck_target"), dict)
            else {}
        ),
        "last_publish_task": deepcopy(
            raw.get("last_publish_task")
            if isinstance(raw.get("last_publish_task"), dict)
            else raw.get("lastPublishTask")
            if isinstance(raw.get("lastPublishTask"), dict)
            else fallback.get("last_publish_task")
            if isinstance(fallback.get("last_publish_task"), dict)
            else {}
        ),
        "sites_to_sell": normalize_mercadolibre_sites_to_sell(
            raw.get("sites_to_sell")
            if isinstance(raw.get("sites_to_sell"), list)
            else raw.get("sitesToSell")
            if isinstance(raw.get("sitesToSell"), list)
            else []
        ),
    }


def draft_publish_targets(
    draft: dict[str, Any],
    *,
    context: AppContext | None = None,
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    raw_targets = draft.get("target_sites") if isinstance(draft.get("target_sites"), list) else draft.get("targetSites")
    for index, raw in enumerate(raw_targets if isinstance(raw_targets, list) else []):
        item = raw if isinstance(raw, dict) else {}
        target = _normalized_target(
            str(item.get("platform") or ""),
            str(item.get("site") or item.get("site_id") or ""),
            context=context,
        )
        if target["platform"] and _target_key(target["platform"], target["site"]) not in {_target_key(t["platform"], t["site"]) for t in targets}:
            target.update(_target_listing_fields(item, draft if index == 0 else None))
            targets.append(target)
    if targets:
        return targets
    target = _normalized_target(
        str(draft.get("platform") or ""),
        str(draft.get("site") or draft.get("site_id") or ""),
        context=context,
    )
    target.update(_target_listing_fields({}, draft))
    return [target] if target["platform"] else []


def _select_target(
    draft: dict[str, Any],
    platform: str,
    site: str,
    *,
    context: AppContext | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    targets = draft_publish_targets(draft, context=context)
    if not targets:
        return {}, {"ok": False, "error": "当前草稿没有可发布目标站点", "error_code": "DRAFT_TARGET_MISSING"}, 400
    requested_platform = str(platform or "").strip().lower()
    requested_site = str(site or "").strip()
    if not requested_platform and not requested_site:
        if len(targets) > 1:
            return {}, {
                "ok": False,
                "error": "当前草稿包含多个发布目标，请明确目标平台和站点。",
                "error_code": "DRAFT_TARGET_AMBIGUOUS",
                "allowed_targets": targets,
            }, 400
        return targets[0], None, 200
    if requested_platform and requested_platform not in PLATFORMS:
        return {}, {"ok": False, "error": "目标平台或站点不支持", "error_code": "TARGET_UNSUPPORTED"}, 400
    candidates = [
        target
        for target in targets
        if (
            not requested_platform
            or str(target.get("platform") or "").strip().lower()
            == requested_platform
        )
        and (
            not requested_site
            or str(target.get("site") or "").strip().casefold()
            == requested_site.casefold()
        )
    ]
    if not candidates:
        return {}, {
            "ok": False,
            "error": "预检目标不属于当前草稿的目标站点",
            "error_code": "TARGET_NOT_IN_DRAFT",
            "target": {
                "platform": requested_platform,
                "site": requested_site,
            },
            "allowed_targets": targets,
        }, 400
    if len(candidates) > 1:
        site_ambiguous = bool(requested_platform)
        return {}, {
            "ok": False,
            "error": (
                "当前平台包含多个发布站点，请明确目标站点。"
                if site_ambiguous
                else "当前草稿包含多个发布目标，请明确目标平台。"
            ),
            "error_code": (
                "DRAFT_TARGET_SITE_AMBIGUOUS"
                if site_ambiguous
                else "DRAFT_TARGET_AMBIGUOUS"
            ),
            "allowed_targets": candidates,
        }, 400
    return candidates[0], None, 200


def draft_for_publish_target(draft: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    target_draft = deepcopy(draft)
    # 已退役的平台规则副本不得进入发布投影（即使旧输入仍携带）。
    for retired in ("category_attribute_schema", "categoryAttributeSchema"):
        target_draft.pop(retired, None)
    target_draft["platform"] = target["platform"]
    target_draft["site"] = target["site"]
    target_draft["language"] = target["language"]
    target_draft["listing_currency"] = target["listing_currency"]
    target_draft["currency_fingerprint"] = target.get("currency_fingerprint", "")
    target_key = _target_key(target["platform"], target["site"])
    pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
    pricing_targets = pricing.get("targets") if isinstance(pricing.get("targets"), dict) else {}
    pricing_target = (
        pricing_targets.get(target_key)
        if isinstance(pricing_targets.get(target_key), dict)
        else {}
    )
    applied = (
        pricing_target.get("applied_price")
        if isinstance(pricing_target.get("applied_price"), dict)
        else {}
    )
    applied_currency = str(applied.get("currency") or "").strip().upper()
    applied_amount = str(applied.get("amount") or "").strip()
    if applied_amount and applied_currency == str(target["listing_currency"]).upper():
        # Adapter payload builders consume this selected-target projection only;
        # the persisted draft has no ambiguous top-level price.
        target_draft["price"] = applied_amount
    else:
        target_draft["price"] = ""
    target_draft["selected_pricing"] = deepcopy(pricing_target)
    for key in TARGET_LISTING_KEYS:
        if key in target:
            target_draft[key] = deepcopy(target[key])
    # 发布上下文只服务当前目标；保留全部历史 target_sites 会把其他站点与旧
    # 预检快照重复写入 job/result，且旧数据可能含递归 last_precheck_target。
    target_draft["target_sites"] = [deepcopy(target)]
    return target_draft


def _target_update_from_draft(draft: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(draft[key]) for key in TARGET_LISTING_KEYS if key in draft}


def merge_target_listing_into_draft(
    draft: dict[str, Any],
    target: dict[str, Any],
    updates: dict[str, Any],
    *,
    context: AppContext | None = None,
) -> dict[str, Any]:
    merged = deepcopy(draft)
    selected_key = _target_key(str(target.get("platform") or ""), str(target.get("site") or ""))
    targets = draft_publish_targets(merged, context=context)
    if not targets and target.get("platform") and target.get("site"):
        targets = [deepcopy(target)]
    next_targets: list[dict[str, Any]] = []
    matched = False
    for item in targets:
        if _target_key(str(item.get("platform") or ""), str(item.get("site") or "")) == selected_key:
            item = {**item, **_target_update_from_draft(updates)}
            matched = True
        next_targets.append(item)
    if not matched and target.get("platform") and target.get("site"):
        next_targets.append({**deepcopy(target), **_target_update_from_draft(updates)})
    merged["target_sites"] = next_targets
    if selected_key == _target_key(str(merged.get("platform") or ""), str(merged.get("site") or merged.get("site_id") or "")):
        for key in TARGET_LISTING_KEYS:
            # sites_to_sell 只属于 target_sites[]；根字段仅存在于当前发布投影。
            if key != "sites_to_sell" and key in updates:
                merged[key] = deepcopy(updates[key])
    return merged


def _save_updated_draft(
    draft: dict[str, Any],
    publish_context: dict[str, Any],
    *,
    context: AppContext | None = None,
) -> dict[str, Any]:
    active_context = context or get_context()
    db = active_context.db
    product_id = str(draft.get("product_id") or publish_context.get("product", {}).get("product_id") or "").strip()
    platform = str(draft.get("platform") or publish_context.get("platform") or "").strip().lower()
    canonical_draft = normalize_platform_draft(
        draft,
        platform,
        {"product_id": product_id},
    )
    saved_draft_id = db.upsert_draft_model(
        product_id,
        platform,
        canonical_draft,
    )
    saved_draft = db.load_draft_model(saved_draft_id)
    source_product = db.load_product_model(str(saved_draft.get("source_product_id") or saved_draft.get("product_id") or product_id))
    return {
        "ok": True,
        "draft": saved_draft,
        "productContext": active_context.products.draft_product_context(
            source_product
        ),
        "productsIndex": active_context.products.load_products_index(),
        "draftsIndex": active_context.products.load_drafts_index(),
    }


def load_required_draft_publish_context(
    body: dict[str, Any],
    *,
    context: AppContext | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    draft_id = str(body.get("draft_id") or body.get("draftId") or "").strip()
    if not draft_id:
        return {}, {"ok": False, "error": "draft_id 不能为空", "error_code": "DRAFT_ID_REQUIRED"}, 400
    active_context = context or get_context()
    db = active_context.db
    draft = db.load_draft_model(draft_id)
    if not draft:
        return {}, {"ok": False, "error": "草稿不存在", "error_code": "DRAFT_NOT_FOUND", "draft_id": draft_id}, 404
    stored_product_id = str(draft.get("product_id") or "").strip()
    source_product_id = str(draft.get("source_product_id") or "").strip()
    if (
        stored_product_id
        and source_product_id
        and stored_product_id != source_product_id
    ):
        return {}, {
            "ok": False,
            "error": "草稿关联商品不一致，已阻止发布；请先修复草稿归属。",
            "error_code": "DRAFT_PRODUCT_MISMATCH",
            "draft_id": draft_id,
            "product_id": stored_product_id,
            "source_product_id": source_product_id,
        }, 409
    product_id = source_product_id or stored_product_id
    product = db.load_product_model(product_id)
    if not product:
        return {}, {"ok": False, "error": "草稿关联商品不存在", "error_code": "DRAFT_PRODUCT_NOT_FOUND", "draft_id": draft_id}, 404
    target, error_response, status = _select_target(
        draft,
        str(body.get("platform") or ""),
        str(body.get("site") or body.get("site_id") or ""),
        context=active_context,
    )
    if error_response:
        return {}, error_response, status

    product_for_publish = normalize_product_fields(product)
    target_draft = draft_for_publish_target(draft, target)
    product_for_publish.setdefault("drafts", {})[target["platform"]] = target_draft
    return {
        "draft": draft,
        "product": product_for_publish,
        "productContext": active_context.products.draft_product_context(product),
        "target": target,
        "targets": draft_publish_targets(draft, context=active_context),
        "platform": target["platform"],
        "site": target["site"],
    }, None, 200


def save_draft_precheck_result(
    publish_context: dict[str, Any],
    precheck: dict[str, Any],
    *,
    context: AppContext | None = None,
) -> dict[str, Any]:
    active_context = context or get_context()
    draft = deepcopy(publish_context.get("draft") if isinstance(publish_context.get("draft"), dict) else {})
    target = publish_context.get("target") if isinstance(publish_context.get("target"), dict) else {}
    errors = list(precheck.get("errors") or [])
    warnings = list(precheck.get("warnings") or [])
    requested_status = "ready" if precheck.get("ok") else "not_ready"
    current_publish_status = str(target.get("publish_status") or draft.get("publish_status") or "").strip().lower()
    if current_publish_status in {"published", "real_publish_success", "success"}:
        publish_status = current_publish_status
    else:
        publish_status = requested_status
    # 预检历史不再内嵌 draft_json；publish_logs 统一存 SQLite 表。
    target_updates = {
        "validation_errors": errors + warnings,
        "publish_status": publish_status,
        "last_precheck_target": _precheck_target_snapshot(target),
        "last_precheck": precheck,
    }
    if precheck.get("ok") and publish_status not in {"published", "real_publish_success", "success"}:
        target_updates["status"] = "ready_to_publish"
    elif not precheck.get("ok"):
        target_updates["status"] = "not_ready"
    draft = merge_target_listing_into_draft(
        draft,
        target,
        target_updates,
        context=active_context,
    )
    return _save_updated_draft(
        draft,
        publish_context,
        context=active_context,
    )


def save_draft_target_listing_result(
    publish_context: dict[str, Any],
    target_draft: dict[str, Any],
    *,
    context: AppContext | None = None,
) -> dict[str, Any]:
    active_context = context or get_context()
    draft = deepcopy(publish_context.get("draft") if isinstance(publish_context.get("draft"), dict) else {})
    target = publish_context.get("target") if isinstance(publish_context.get("target"), dict) else {}
    draft = merge_target_listing_into_draft(
        draft,
        target,
        target_draft,
        context=active_context,
    )
    return _save_updated_draft(
        draft,
        publish_context,
        context=active_context,
    )


__all__ = [
    "draft_publish_targets",
    "draft_for_publish_target",
    "load_required_draft_publish_context",
    "merge_target_listing_into_draft",
    "save_draft_precheck_result",
    "save_draft_target_listing_result",
]
