# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from typing import Any

from erp_web import db as erp_db
from erp_web.marketplace_registry import marketplace_site

from .collect_helpers import collect_time_iso
from .product_store import draft_product_context, load_drafts_index, load_products_index, normalize_product_fields
from .runtime_common import APP_DIR, PLATFORMS

ResponseWithStatus = tuple[dict[str, Any], int]
TARGET_LISTING_KEYS = (
    "category_id",
    "category_path",
    "attributes",
    "validation_errors",
    "category_precheck",
    "publish_status",
    "status",
    "last_precheck",
    "last_precheck_target",
    "publish_logs",
)


def _target_key(platform: str, site: str) -> str:
    return f"{str(platform or '').strip().lower()}:{str(site or '').strip().lower()}"


def _normalized_target(platform: str, site: str = "") -> dict[str, str]:
    platform_key = str(platform or "").strip().lower()
    selected_site = marketplace_site(platform_key, site)
    if platform_key not in PLATFORMS or not selected_site.get("code"):
        return {"platform": "", "site": "", "language": "", "currency": ""}
    return {
        "platform": platform_key,
        "site": selected_site["code"],
        "language": selected_site["language"],
        "currency": selected_site["currency"],
    }


def _target_listing_fields(raw: dict[str, Any], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = fallback if isinstance(fallback, dict) else {}
    raw_attributes = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
    fallback_attributes = fallback.get("attributes") if isinstance(fallback.get("attributes"), dict) else {}
    attributes = raw_attributes if raw_attributes else fallback_attributes
    validation_errors = raw.get("validation_errors") if isinstance(raw.get("validation_errors"), list) else raw.get("validationErrors")
    fallback_validation_errors = fallback.get("validation_errors") if isinstance(fallback.get("validation_errors"), list) else []
    if not isinstance(validation_errors, list) or not validation_errors:
        validation_errors = fallback_validation_errors
    publish_logs = raw.get("publish_logs") if isinstance(raw.get("publish_logs"), list) else raw.get("publishLogs")
    if not isinstance(publish_logs, list):
        publish_logs = fallback.get("publish_logs") if isinstance(fallback.get("publish_logs"), list) else []
    return {
        "category_id": str(raw.get("category_id") or raw.get("categoryId") or fallback.get("category_id") or "").strip(),
        "category_path": str(raw.get("category_path") or raw.get("categoryPath") or fallback.get("category_path") or "").strip(),
        "attributes": deepcopy(attributes),
        "validation_errors": deepcopy(validation_errors),
        "category_precheck": deepcopy(raw.get("category_precheck") if isinstance(raw.get("category_precheck"), dict) else raw.get("categoryPrecheck") if isinstance(raw.get("categoryPrecheck"), dict) else fallback.get("category_precheck") if isinstance(fallback.get("category_precheck"), dict) else {}),
        "publish_status": str(raw.get("publish_status") or raw.get("publishStatus") or fallback.get("publish_status") or "").strip(),
        "status": str(raw.get("status") or fallback.get("status") or "").strip(),
        "last_precheck": deepcopy(raw.get("last_precheck") if isinstance(raw.get("last_precheck"), dict) else raw.get("lastPrecheck") if isinstance(raw.get("lastPrecheck"), dict) else fallback.get("last_precheck") if isinstance(fallback.get("last_precheck"), dict) else {}),
        "last_precheck_target": deepcopy(raw.get("last_precheck_target") if isinstance(raw.get("last_precheck_target"), dict) else raw.get("lastPrecheckTarget") if isinstance(raw.get("lastPrecheckTarget"), dict) else fallback.get("last_precheck_target") if isinstance(fallback.get("last_precheck_target"), dict) else {}),
        "publish_logs": deepcopy(publish_logs),
    }


def draft_publish_targets(draft: dict[str, Any]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    raw_targets = draft.get("target_sites") if isinstance(draft.get("target_sites"), list) else draft.get("targetSites")
    for index, raw in enumerate(raw_targets if isinstance(raw_targets, list) else []):
        item = raw if isinstance(raw, dict) else {}
        target = _normalized_target(str(item.get("platform") or ""), str(item.get("site") or item.get("site_id") or ""))
        if target["platform"] and _target_key(target["platform"], target["site"]) not in {_target_key(t["platform"], t["site"]) for t in targets}:
            target.update(_target_listing_fields(item, draft if index == 0 else None))
            targets.append(target)
    if targets:
        return targets
    target = _normalized_target(str(draft.get("platform") or ""), str(draft.get("site") or draft.get("site_id") or ""))
    target.update(_target_listing_fields({}, draft))
    return [target] if target["platform"] else []


def _select_target(draft: dict[str, Any], platform: str, site: str) -> tuple[dict[str, str], dict[str, Any] | None, int]:
    targets = draft_publish_targets(draft)
    if not targets:
        return {}, {"ok": False, "error": "当前草稿没有可发布目标站点", "error_code": "DRAFT_TARGET_MISSING"}, 400
    requested_platform = str(platform or "").strip().lower()
    requested_site = str(site or "").strip()
    if not requested_platform and not requested_site:
        return targets[0], None, 200
    normalized = _normalized_target(requested_platform or targets[0]["platform"], requested_site)
    if not normalized["platform"]:
        return {}, {"ok": False, "error": "目标平台或站点不支持", "error_code": "TARGET_UNSUPPORTED"}, 400
    normalized_key = _target_key(normalized["platform"], normalized["site"])
    selected = next((target for target in targets if _target_key(target["platform"], target["site"]) == normalized_key), None)
    if selected is None:
        return {}, {
            "ok": False,
            "error": "预检目标不属于当前草稿的目标站点",
            "error_code": "TARGET_NOT_IN_DRAFT",
            "target": normalized,
            "allowed_targets": targets,
        }, 400
    return selected, None, 200


def draft_for_publish_target(draft: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    target_draft = deepcopy(draft)
    target_draft["platform"] = target["platform"]
    target_draft["site"] = target["site"]
    target_draft["language"] = target["language"]
    target_draft["currency"] = target["currency"]
    target_draft["target_site"] = target
    for key in TARGET_LISTING_KEYS:
        if key in target:
            target_draft[key] = deepcopy(target[key])
    return target_draft


def _target_update_from_draft(draft: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(draft[key]) for key in TARGET_LISTING_KEYS if key in draft}


def merge_target_listing_into_draft(draft: dict[str, Any], target: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(draft)
    selected_key = _target_key(str(target.get("platform") or ""), str(target.get("site") or ""))
    targets = draft_publish_targets(merged)
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
            if key in updates:
                merged[key] = deepcopy(updates[key])
    return merged


def _save_updated_draft(draft: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    product_id = str(draft.get("product_id") or context.get("product", {}).get("product_id") or "").strip()
    platform = str(draft.get("platform") or context.get("platform") or "").strip().lower()
    saved_draft_id = erp_db.upsert_draft_model(APP_DIR, product_id, platform, draft)
    saved_draft = erp_db.load_draft_model(APP_DIR, saved_draft_id)
    source_product = erp_db.load_product_model(APP_DIR, str(saved_draft.get("source_product_id") or saved_draft.get("product_id") or product_id))
    return {
        "ok": True,
        "draft": saved_draft,
        "productContext": draft_product_context(source_product),
        "productsIndex": load_products_index(),
        "draftsIndex": load_drafts_index(),
    }


def load_required_draft_publish_context(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
    draft_id = str(body.get("draft_id") or body.get("draftId") or "").strip()
    if not draft_id:
        return {}, {"ok": False, "error": "draft_id 不能为空", "error_code": "DRAFT_ID_REQUIRED"}, 400
    draft = erp_db.load_draft_model(APP_DIR, draft_id)
    if not draft:
        return {}, {"ok": False, "error": "草稿不存在", "error_code": "DRAFT_NOT_FOUND", "draft_id": draft_id}, 404
    product_id = str(draft.get("source_product_id") or draft.get("product_id") or "").strip()
    product = erp_db.load_product_model(APP_DIR, product_id)
    if not product:
        return {}, {"ok": False, "error": "草稿关联商品不存在", "error_code": "DRAFT_PRODUCT_NOT_FOUND", "draft_id": draft_id}, 404
    target, error_response, status = _select_target(draft, str(body.get("platform") or ""), str(body.get("site") or body.get("site_id") or ""))
    if error_response:
        return {}, error_response, status

    product_for_publish = normalize_product_fields(product)
    target_draft = draft_for_publish_target(draft, target)
    product_for_publish.setdefault("drafts", {})[target["platform"]] = target_draft
    return {
        "draft": draft,
        "product": product_for_publish,
        "productContext": draft_product_context(product),
        "target": target,
        "targets": draft_publish_targets(draft),
        "platform": target["platform"],
        "site": target["site"],
    }, None, 200


def save_draft_precheck_result(context: dict[str, Any], precheck: dict[str, Any]) -> dict[str, Any]:
    draft = deepcopy(context.get("draft") if isinstance(context.get("draft"), dict) else {})
    target = context.get("target") if isinstance(context.get("target"), dict) else {}
    errors = list(precheck.get("errors") or [])
    warnings = list(precheck.get("warnings") or [])
    requested_status = "ready" if precheck.get("ok") else "not_ready"
    current_publish_status = str(target.get("publish_status") or draft.get("publish_status") or "").strip().lower()
    if current_publish_status in {"published", "real_publish_success", "success"}:
        publish_status = current_publish_status
    else:
        publish_status = requested_status
    publish_logs = target.get("publish_logs") if isinstance(target.get("publish_logs"), list) else draft.get("publish_logs") if isinstance(draft.get("publish_logs"), list) else []
    publish_logs.insert(
        0,
        {
            "time": collect_time_iso(),
            "status": requested_status,
            "platform": target.get("platform", ""),
            "site": target.get("site", ""),
            "error_count": len(errors),
            "warning_count": len(warnings),
        },
    )
    target_updates = {
        "validation_errors": errors + warnings,
        "publish_status": publish_status,
        "publish_logs": publish_logs[:20],
        "last_precheck_target": target,
        "last_precheck": precheck,
    }
    if precheck.get("ok") and publish_status not in {"published", "real_publish_success", "success"}:
        target_updates["status"] = "ready_to_publish"
    elif not precheck.get("ok"):
        target_updates["status"] = "not_ready"
    draft = merge_target_listing_into_draft(draft, target, target_updates)
    return _save_updated_draft(draft, context)


def save_draft_target_listing_result(context: dict[str, Any], target_draft: dict[str, Any]) -> dict[str, Any]:
    draft = deepcopy(context.get("draft") if isinstance(context.get("draft"), dict) else {})
    target = context.get("target") if isinstance(context.get("target"), dict) else {}
    draft = merge_target_listing_into_draft(draft, target, target_draft)
    return _save_updated_draft(draft, context)


__all__ = [
    "draft_publish_targets",
    "draft_for_publish_target",
    "load_required_draft_publish_context",
    "merge_target_listing_into_draft",
    "save_draft_precheck_result",
    "save_draft_target_listing_result",
]
