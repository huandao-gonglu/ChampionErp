# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import time
from copy import deepcopy
from typing import Any

import erp_db
import marketplace_publish as publisher
from erp_web import app_config as app_config_runtime
from product_model import PLATFORMS, default_product_model, normalize_product_model

from .category_store import ensure_sqlite_store, read_json, write_json
from .image_pool_core import (
    _display_image_ref,
    _source_pool_items,
    current_image_pool,
    enrich_product_image_dimensions,
)
from .runtime_common import APP_CONFIG_PATH, APP_DIR, STORE_CONFIG_PATH

def normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def mask_secret(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return f"{text[:2]}****"
    return f"{text[:4]}****{text[-4:]}"


def normalize_sku_items(product: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_items = product.get("sku_items")
    if isinstance(raw_items, list):
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "id": str(item.get("id") or index),
                    "selected": bool(item.get("selected", index == 0)),
                    "name": str(item.get("name") or item.get("sku") or item.get("spec") or f"SKU {index + 1}"),
                    "spec1": str(item.get("spec1") or item.get("variant1") or item.get("color") or ""),
                    "spec2": str(item.get("spec2") or item.get("variant2") or item.get("size") or ""),
                    "price": str(item.get("price") or ""),
                    "stock": str(item.get("stock") or ""),
                    "image": str(item.get("image") or item.get("image_url") or ""),
                    "sale_price": str(item.get("sale_price") or item.get("suggested_price") or ""),
                    "custom_stock": str(item.get("custom_stock") or item.get("publish_stock") or ""),
                }
            )
    if not rows:
        variations = product.get("variations")
        if isinstance(variations, list):
            for index, item in enumerate(variations):
                if not isinstance(item, dict):
                    continue
                attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
                rows.append(
                    {
                        "id": str(item.get("id") or index),
                        "selected": index == 0,
                        "name": str(item.get("title") or item.get("name") or attrs.get("name") or f"SKU {index + 1}"),
                        "spec1": str(item.get("spec1") or item.get("color") or attrs.get("color") or ""),
                        "spec2": str(item.get("spec2") or item.get("size") or attrs.get("size") or ""),
                        "price": str(item.get("price") or item.get("sale_price") or item.get("cost") or ""),
                        "stock": str(item.get("stock") or item.get("inventory") or ""),
                        "image": str(item.get("image") or item.get("image_url") or ""),
                        "sale_price": str(item.get("sale_price") or ""),
                        "custom_stock": str(item.get("custom_stock") or ""),
                    }
                )
    if not rows:
        rows.append(
            {
                "id": "0",
                "selected": True,
                "name": str(product.get("sku") or product.get("model") or product.get("name") or "SKU 1"),
                "spec1": "",
                "spec2": "",
                "price": str(product.get("detected_price") or product.get("cost") or ""),
                "stock": str(product.get("stock") or ""),
                "image": str((normalize_list(product.get("source_image_urls")) or [""])[0]),
                "sale_price": "",
                "custom_stock": "",
            }
        )
    return rows


def normalize_product_fields(product: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_product_model(product)
    for key in ["materials", "colors", "selling_points", "package_includes", "avoid_claims"]:
        normalized[key] = normalize_list(normalized.get(key))
    normalized.setdefault("sku", "")
    normalized.setdefault("model", "")
    normalized.setdefault("attributes", {})
    normalized.setdefault("detail_images", [])
    normalized.setdefault("detail_image_urls", [])
    normalized.setdefault("marketplace_terms", {})
    normalized.setdefault("listing_overrides", {})
    normalized.setdefault("copy_results", {})
    normalized.setdefault("sku_items", [])
    normalized.setdefault("selected_sku_indices", [])
    normalized.setdefault("pricing_defaults", {})
    normalized.setdefault("publish_preview", {})
    if normalized.get("detected_price") and normalized.get("detected_currency"):
        normalized["detected_price_display"] = f"{normalized['detected_price']} {normalized['detected_currency']}"
    else:
        normalized.setdefault("detected_price_display", "")
    if not isinstance(normalized.get("listing_overrides"), dict):
        normalized["listing_overrides"] = {}
    if not isinstance(normalized.get("copy_results"), dict):
        normalized["copy_results"] = {}
    if not isinstance(normalized.get("pricing_defaults"), dict):
        normalized["pricing_defaults"] = {}
    if not isinstance(normalized.get("publish_preview"), dict):
        normalized["publish_preview"] = {}
    normalized["sku_items"] = normalize_sku_items(normalized)
    if not normalized.get("selected_sku_indices"):
        normalized["selected_sku_indices"] = [0] if normalized["sku_items"] else []
    return normalized


def load_product() -> dict[str, Any]:
    ensure_sqlite_store()
    records = erp_db.list_product_records(APP_DIR, limit=1)
    if records:
        loaded = erp_db.load_product_model(APP_DIR, records[0]["product_id"])
        if loaded:
            return normalize_product_fields(loaded)
    return normalize_product_fields(default_product_model())


def save_product(data: dict[str, Any]) -> dict[str, Any]:
    product = sync_product_workflow_statuses(enrich_product_image_dimensions(normalize_product_fields(data)))
    product["product_id"] = product_identity(product)
    ensure_sqlite_store()
    product["product_id"] = erp_db.upsert_product_model(APP_DIR, product)
    return product


def product_identity(product: dict[str, Any]) -> str:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    existing = str(product.get("product_id") or product.get("id") or source.get("product_id") or "").strip()
    if existing:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", existing)[:80] or "product"
    raw = "|".join(
        [
            str(source.get("source_url") or product.get("source_url") or "").strip(),
            str(source.get("title") or product.get("name") or "").strip(),
            str(source.get("created_at") or product.get("created_at") or "").strip(),
        ]
    )
    if not raw.strip("|"):
        raw = str(time.time())
    import hashlib

    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _draft_copy_ready(draft: dict[str, Any]) -> bool:
    return bool(
        draft.get("copy_generated_at")
        or draft.get("ai_copy_ready")
        or str(draft.get("copy_source") or "").lower() in {"ai", "deepseek", "openai", "fallback_ai"}
    ) and bool(str(draft.get("title") or "").strip() and str(draft.get("description") or "").strip())


def _draft_images_ready(product: dict[str, Any], platform: str, draft: dict[str, Any]) -> bool:
    images = normalize_list(draft.get("images"))
    if images:
        return True
    pool = current_image_pool(product)
    return any(
        isinstance(item, dict)
        and str(item.get("status") or "").strip().lower() != "empty"
        and str(item.get("origin") or "").strip().lower() in {"ai_generated", "chatgpt_import", "local_upload"}
        for item in pool
    )


def _draft_publish_fields_ready(draft: dict[str, Any]) -> bool:
    attrs = draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {}
    pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
    return all(
        [
            str(draft.get("category_id") or "").strip(),
            bool(attrs),
            str(draft.get("price") or pricing.get("suggested_price") or "").strip(),
            str(draft.get("stock") or "").strip(),
        ]
    )


def _draft_precheck_ready(product: dict[str, Any], platform: str, draft: dict[str, Any]) -> bool:
    preview_map = product.get("publish_preview") if isinstance(product.get("publish_preview"), dict) else {}
    preview = preview_map.get(platform) if isinstance(preview_map.get(platform), dict) else {}
    publish_status = str(draft.get("publish_status") or "").strip().lower()
    return bool(preview.get("ok") is True or publish_status in {"ready", "published", "real_publish_success", "success"})


def draft_workflow_status(product: dict[str, Any], platform: str = "mercadolibre") -> str:
    product = normalize_product_fields(product or {})
    platform = str(platform or "mercadolibre").strip().lower() or "mercadolibre"
    draft = (product.get("drafts") or {}).get(platform) if isinstance(product.get("drafts"), dict) else {}
    draft = draft if isinstance(draft, dict) else {}
    publish_status = str(draft.get("publish_status") or "").strip().lower()
    if publish_status in {"published", "real_publish_success", "success"}:
        return "published"
    if not (draft.get("enabled") or draft.get("title") or draft.get("category_id") or draft.get("status")):
        return "collected"
    if _draft_publish_fields_ready(draft) and _draft_precheck_ready(product, platform, draft):
        return "ready_to_publish"
    if _draft_copy_ready(draft) and _draft_images_ready(product, platform, draft) and _draft_publish_fields_ready(draft) and _draft_precheck_ready(product, platform, draft):
        return "ready_to_publish"
    if _draft_copy_ready(draft) and _draft_images_ready(product, platform, draft):
        return "images_ready"
    if _draft_copy_ready(draft):
        return "copy_ready"
    return "claimed"


def publish_queue_platforms(product: dict[str, Any], requested_platforms: list[str] | None = None) -> list[str]:
    product = sync_product_workflow_statuses(product or {})
    targets = requested_platforms or list(PLATFORMS)
    normalized_targets = [str(platform or "").strip().lower() for platform in targets if str(platform or "").strip().lower() in PLATFORMS]
    eligible: list[str] = []
    for platform in normalized_targets:
        draft = (product.get("drafts") or {}).get(platform) if isinstance(product.get("drafts"), dict) else {}
        draft = draft if isinstance(draft, dict) else {}
        if draft_workflow_status(product, platform) == "ready_to_publish" or _draft_precheck_ready(product, platform, draft):
            eligible.append(platform)
    return eligible


def sync_product_workflow_statuses(product: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_product_fields(product or {})
    drafts = normalized.get("drafts") if isinstance(normalized.get("drafts"), dict) else {}
    for platform, draft in list(drafts.items()):
        if platform not in PLATFORMS or not isinstance(draft, dict):
            continue
        draft["status"] = draft_workflow_status(normalized, platform)
        drafts[platform] = draft
    normalized["workflow_statuses"] = {
        platform: drafts[platform].get("status", "collected")
        for platform in PLATFORMS
        if isinstance(drafts.get(platform), dict)
    }
    return normalized


def product_index_status(product: dict[str, Any], platform: str = "mercadolibre") -> dict[str, Any]:
    product = sync_product_workflow_statuses(product)
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    draft = (product.get("drafts") or {}).get(platform) if isinstance(product.get("drafts"), dict) else {}
    draft = draft if isinstance(draft, dict) else {}
    pool = _source_pool_items(product)
    workflow_status = draft_workflow_status(product, platform)
    has_copy = workflow_status in {"copy_ready", "images_ready", "ready_to_publish", "published"}
    has_generated_image = any(str(item.get("origin") or "") in {"ai_generated", "chatgpt_import"} for item in pool)
    queue_platforms = publish_queue_platforms(product, [platform])
    return {
        "collect_status": source.get("collect_status") or ("success" if source.get("title") else "pending"),
        "workflow_status": workflow_status,
        "draft_statuses": product.get("workflow_statuses") or {},
        "ai_copy_status": "done" if has_copy else "pending",
        "image_status": "done" if workflow_status in {"images_ready", "ready_to_publish", "published"} or pool else "pending",
        "category_status": "done" if draft.get("category_id") else "pending",
        "attributes_status": "done" if isinstance(draft.get("attributes"), dict) and draft.get("attributes") else "pending",
        "pricing_status": "done" if draft.get("price") or (isinstance(draft.get("pricing"), dict) and draft["pricing"].get("suggested_price")) else "pending",
        "precheck_status": ((product.get("publish_preview") or {}).get(platform) or {}).get("ok", "pending") if isinstance(product.get("publish_preview"), dict) else "pending",
        "publish_status": draft.get("publish_status") or "not_ready",
        "publish_queue_ready": bool(queue_platforms),
        "publish_queue_platforms": queue_platforms,
        "optimized": bool(has_copy or has_generated_image),
    }


def sanitize_products_index(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        record = dict(item)
        record["main_image"] = _display_image_ref(str(record.get("main_image") or ""))
        sanitized.append(record)
    return sanitized


def load_products_index() -> list[dict[str, Any]]:
    ensure_sqlite_store()
    return sanitize_products_index(erp_db.list_product_records(APP_DIR))


def load_drafts_index(scope: str = "active") -> list[dict[str, Any]]:
    ensure_sqlite_store()
    return sanitize_products_index(erp_db.list_draft_records(APP_DIR, scope=scope))


def delete_products_from_index(product_ids: list[Any]) -> dict[str, Any]:
    seen: set[str] = set()
    ids: list[str] = []
    for value in product_ids:
        product_id = str(value or "").strip()
        if product_id and product_id not in seen:
            ids.append(product_id)
            seen.add(product_id)
    if not ids:
        return {"ok": False, "error": "请先选择要删除的商品。", "deleted": 0, "deletedIds": [], "productsIndex": load_products_index()}

    ensure_sqlite_store()
    deleted_ids: list[str] = []
    missing_ids: list[str] = []
    for product_id in ids:
        deleted = erp_db.delete_product_model(APP_DIR, product_id)
        if deleted:
            deleted_ids.append(product_id)
        else:
            missing_ids.append(product_id)

    products_index = load_products_index()
    product = load_product()

    return {
        "ok": True,
        "deleted": len(deleted_ids),
        "deletedIds": deleted_ids,
        "missingIds": missing_ids,
        "productsIndex": products_index,
        "product": product,
        "imagePool": current_image_pool(product),
        "message": f"已删除 {len(deleted_ids)} 个商品。",
    }


def load_product_from_index(product_id: str = "", file_path: str = "") -> dict[str, Any]:
    product_id = str(product_id or "").strip()
    file_path = str(file_path or "").strip()
    ensure_sqlite_store()
    sqlite_product_id = product_id
    if not sqlite_product_id and file_path.startswith("sqlite://products/"):
        sqlite_product_id = file_path.rsplit("/", 1)[-1]
    if sqlite_product_id:
        loaded = erp_db.load_product_model(APP_DIR, sqlite_product_id)
        if loaded:
            return normalize_product_fields(loaded)
    return load_product()


def load_draft_from_index(draft_id: str) -> dict[str, Any]:
    draft_id = str(draft_id or "").strip()
    ensure_sqlite_store()
    if draft_id:
        loaded = erp_db.load_product_for_draft(APP_DIR, draft_id)
        if loaded:
            return normalize_product_fields(loaded)
    return load_product()


def load_app_config() -> dict[str, Any]:
    raw = read_json(APP_CONFIG_PATH, default_app_config())
    config = normalize_app_config(raw)
    if not APP_CONFIG_PATH.exists():
        write_json(APP_CONFIG_PATH, config)
    return config


def save_app_config(config: dict[str, Any]) -> None:
    config = normalize_app_config(config)
    write_json(APP_CONFIG_PATH, config)
    # Runtime secrets live only under config/ so they are never mirrored into
    # packaged web assets.


def load_store_config() -> dict[str, Any]:
    return normalize_store_config(publisher.load_store_config(STORE_CONFIG_PATH))


_STORE_SENSITIVE_FIELDS = {
    "app_id",
    "client_id",
    "app_secret",
    "client_secret",
    "code_verifier",
    "access_token",
    "refresh_token",
    "redirect_uri",
    "content_token",
    "prices_token",
    "marketplace_token",
    "stocks_token",
    "api_key",
}


def default_store_config() -> dict[str, Any]:
    return publisher.load_store_config(STORE_CONFIG_PATH.with_name("__default_store_config__.json"))


def _sync_mercadolibre_secret_aliases(store: dict[str, Any]) -> None:
    app_secret = str(store.get("app_secret") or "").strip()
    client_secret = str(store.get("client_secret") or "").strip()
    if client_secret and not app_secret:
        store["app_secret"] = client_secret
    if app_secret and not client_secret:
        store["client_secret"] = app_secret


def merge_store_config_fields(
    base: dict[str, Any] | None,
    updates: dict[str, Any] | None,
    *,
    preserve_empty_sensitive: bool = True,
) -> dict[str, Any]:
    merged = deepcopy(base if isinstance(base, dict) else default_store_config())
    updates = updates if isinstance(updates, dict) else {}
    for section_key, section_updates in updates.items():
        if not isinstance(section_updates, dict):
            merged[section_key] = deepcopy(section_updates)
            continue
        section = merged.setdefault(section_key, {})
        if not isinstance(section, dict):
            section = {}
            merged[section_key] = section
        for field, value in section_updates.items():
            if (
                preserve_empty_sensitive
                and field in _STORE_SENSITIVE_FIELDS
                and value in (None, "")
                and str(section.get(field) or "").strip()
            ):
                continue
            section[field] = deepcopy(value)
        if section_key == "mercadolibre":
            _sync_mercadolibre_secret_aliases(section)
    return merged


def normalize_store_config(config: dict[str, Any] | None) -> dict[str, Any]:
    normalized = merge_store_config_fields(default_store_config(), config, preserve_empty_sensitive=False)
    ml = normalized.get("mercadolibre") if isinstance(normalized.get("mercadolibre"), dict) else {}
    if isinstance(ml, dict) and not str(ml.get("code_verifier") or "").strip():
        ml.pop("code_verifier", None)
    return normalized


def update_store_config_fields(platform: str, fields: dict[str, Any], *, preserve_empty_sensitive: bool = True) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    config = load_store_config()
    updated = merge_store_config_fields(config, {platform: fields}, preserve_empty_sensitive=preserve_empty_sensitive)
    save_store_config(updated)
    return updated


def save_store_config(config: dict[str, Any], *, preserve_empty_sensitive: bool = True) -> None:
    STORE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if preserve_empty_sensitive:
        existing = publisher.load_store_config(STORE_CONFIG_PATH) if STORE_CONFIG_PATH.exists() else default_store_config()
        merged = merge_store_config_fields(existing, config, preserve_empty_sensitive=True)
    else:
        merged = normalize_store_config(config)
    publisher.save_store_config(STORE_CONFIG_PATH, merged)


def _auth_status_label(status: Any, store: dict[str, Any]) -> str:
    text = str(status or "").strip().lower()
    error_code = str(store.get("auth_error_code") or "").strip().lower()
    error_message = str(store.get("auth_error_message") or "").strip().lower()
    has_credentials = any(
        str(store.get(key) or "").strip()
        for key in (
            "access_token",
            "refresh_token",
            "app_id",
            "app_secret",
            "code_verifier",
            "content_token",
            "prices_token",
            "marketplace_token",
            "stocks_token",
            "client_id",
            "api_key",
        )
    )
    if text in {"ok", "success", "tested", "测试成功"}:
        return "测试成功"
    if text in {"failed", "error", "测试失败"}:
        if "429" in error_code or "429" in error_message or "rate" in error_code or "too many requests" in error_message:
            return "被限流"
        if "expired" in error_code or "expired" in error_message:
            return "Token 过期"
        if "permission" in error_code or "401" in error_message or "403" in error_message or "unauthorized" in error_message:
            return "权限不足"
        return "测试失败"
    if text in {"saved", "pending", "saved_not_tested", "已保存，未测试"}:
        return "已保存，未测试"
    if has_credentials:
        return "已保存，未测试"
    return "未配置"


def _auth_next_action(platform: str, status_label: str, error_code: str, error_message: str) -> str:
    platform = str(platform or "").strip().lower()
    error_code_l = str(error_code or "").strip().lower()
    error_message_l = str(error_message or "").strip().lower()
    if status_label == "测试成功":
        return "已可用于发布"
    if status_label == "被限流":
        return "等待一段时间后重新测试"
    if status_label == "Token 过期":
        if platform == "mercadolibre":
            return "使用刷新 token 更新 access token"
        return "重新生成并保存 token"
    if status_label == "权限不足":
        if platform == "mercadolibre":
            return "检查 App 权限和授权范围"
        return "检查 Token 权限是否包含当前接口"
    if "redirect_uri" in error_code_l or "redirect_uri" in error_message_l:
        return "检查 Redirect URI 是否与开发者后台完全一致"
    if "invalid_client" in error_code_l:
        return "检查 App ID / Client Secret 是否正确"
    if "invalid_grant" in error_code_l or "refresh token invalid" in error_message_l:
        return "重新生成授权链接并重新授权"
    if "callback" in error_code_l or "callback" in error_message_l:
        return "确认回调地址可访问且已正确注册"
    if "network" in error_code_l or "ssl" in error_message_l or "unexpected_eof" in error_message_l or "eof occurred" in error_message_l:
        return "检查本机网络、代理或防火墙后重试 Mercado Libre 授权接口"
    if platform == "mercadolibre":
        return "重新发起授权并检查回调地址"
    if platform == "wildberries":
        return "确认 Token 已复制完整且接口权限正确"
    if platform == "ozon":
        return "确认 Client ID 和 API Key 已保存且未过期"
    return "检查配置后重新测试"


def explain_mercadolibre_auth_error(error_code: str = "", error_message: str = "") -> dict[str, str]:
    from .publish_logs_runtime import _mercadolibre_test_error_code

    code = str(error_code or "").strip()
    message = str(error_message or "").strip()
    text = f"{code} {message}".lower()
    normalized = _mercadolibre_test_error_code(text) if code.lower() not in {
        "invalid_grant",
        "redirect_uri_mismatch",
        "code_verifier_missing",
        "token_expired",
        "refresh_token_invalid",
        "invalid_client",
    } else code.lower()
    if "code_verifier" in text:
        normalized = "code_verifier_missing"
    if "redirect_uri" in text and ("mismatch" in text or "different" in text or "does not match" in text):
        normalized = "redirect_uri_mismatch"
    if "expired" in text and "token" in text:
        normalized = "token_expired"
    if "ssl" in text or "unexpected_eof" in text or "eof occurred" in text or "urlopen error" in text:
        normalized = "network_tls_failed"
    if normalized == "invalid_grant":
        return {
            "platform": "mercadolibre",
            "code": "invalid_grant",
            "title": "授权 code 已失效或已被使用",
            "plain_message": "Mercado Libre 的 code 是一次性的，通常几分钟内有效；粘贴慢了、重复使用、或重新生成过授权链接都会导致这个错误。",
            "next_action": "重新生成授权链接，用已登录店铺主账号的浏览器打开，授权后立刻复制地址栏里的 code 回 ERP 换 token。",
        }
    if normalized == "redirect_uri_mismatch":
        return {
            "platform": "mercadolibre",
            "code": "redirect_uri_mismatch",
            "title": "Redirect URI 不一致",
            "plain_message": "ERP 里填写的 Redirect URI 必须和 Mercado Libre Developers 后台应用里保存的地址完全一致，包括 https、路径和末尾斜杠。",
            "next_action": "检查 ERP 和 Mercado Libre Developers 后台的 Redirect URI，保持完全一致后重新生成授权链接。",
        }
    if normalized == "code_verifier_missing":
        return {
            "platform": "mercadolibre",
            "code": "CODE_VERIFIER_MISSING",
            "title": "缺少本次授权链接对应的 code_verifier",
            "plain_message": "PKCE 授权要求“生成授权链接”和“用 code 换 token”必须来自同一次流程。重启 ERP、清空配置或直接粘旧 code 都可能缺这个值。",
            "next_action": "重新生成授权链接，不要复用旧 code；授权后直接回到当前 ERP 页面换 token。",
        }
    if normalized in {"token_expired", "refresh_token_invalid"}:
        return {
            "platform": "mercadolibre",
            "code": normalized,
            "title": "Token 已过期或 Refresh Token 不可用",
            "plain_message": "当前保存的 Mercado Libre token 不能继续调用接口，可能是过期、被后台撤销，或复制了不完整的 token。",
            "next_action": "先点击刷新 token；如果仍失败，重新生成授权链接并重新授权。",
        }
    if normalized == "invalid_client":
        return {
            "platform": "mercadolibre",
            "code": "invalid_client",
            "title": "App ID 或 Client Secret 不正确",
            "plain_message": "Mercado Libre 不认可当前应用信息，通常是 App ID、Client Secret 填错，或复制时多了空格。",
            "next_action": "回 Mercado Libre Developers 应用详情复制 App ID 和 Client Secret，保存后重新生成授权链接。",
        }
    if normalized in {"NETWORK_BLOCKED", "NETWORK_TIMEOUT", "network_tls_failed"}:
        return {
            "platform": "mercadolibre",
            "code": normalized,
            "title": "Mercado Libre 授权接口网络连接失败",
            "plain_message": "ERP 已请求 Mercado Libre token 接口，但 HTTPS/TLS 连接在读取响应时被提前断开，常见原因是代理、VPN、公司网络 TLS 拦截、防火墙或临时网络抖动。",
            "next_action": "确认当前电脑能稳定访问 https://api.mercadolibre.com，关闭会拦截 HTTPS 的代理/抓包工具后重试；如果必须走代理，请让 Python/系统网络也使用同一代理。",
        }
    return {
        "platform": "mercadolibre",
        "code": normalized or code or "mercadolibre_auth_failed",
        "title": "Mercado Libre 授权失败",
        "plain_message": message or "授权接口返回失败，但没有提供更具体的错误原因。",
        "next_action": _auth_next_action("mercadolibre", "测试失败", normalized or code, message),
    }


def mercadolibre_auth_checklist(config: dict[str, Any] | None = None) -> dict[str, Any]:
    ml = config if isinstance(config, dict) else load_store_config().get("mercadolibre", {})
    app_id = str(ml.get("app_id") or ml.get("client_id") or "").strip()
    app_secret = str(ml.get("app_secret") or ml.get("client_secret") or "").strip()
    redirect_uri = str(ml.get("redirect_uri") or "").strip()
    site_id = str(ml.get("site_id") or "MLM").strip() or "MLM"
    code_verifier = str(ml.get("code_verifier") or "").strip()
    access_token = str(ml.get("access_token") or "").strip()
    refresh_token = str(ml.get("refresh_token") or "").strip()
    missing: list[str] = []
    if not app_id:
        missing.append("APP_ID_MISSING")
    if not app_secret:
        missing.append("CLIENT_SECRET_MISSING")
    if not redirect_uri:
        missing.append("REDIRECT_URI_MISSING")
    elif not redirect_uri.lower().startswith("https://"):
        missing.append("REDIRECT_URI_MUST_BE_HTTPS")
    ready_for_auth_link = not any(code in missing for code in {"APP_ID_MISSING", "CLIENT_SECRET_MISSING", "REDIRECT_URI_MISSING", "REDIRECT_URI_MUST_BE_HTTPS"})
    token_ready = bool(access_token and refresh_token)
    if not ready_for_auth_link:
        if "APP_ID_MISSING" in missing:
            next_action = "填写 Mercado Libre Developers 里的 App ID / Client ID。"
        elif "CLIENT_SECRET_MISSING" in missing:
            next_action = "填写 Mercado Libre Developers 里的 Client Secret。"
        elif "REDIRECT_URI_MISSING" in missing:
            next_action = "填写 Redirect URI，默认可用 https://example.com/callback。"
        else:
            next_action = "Redirect URI 必须以 https:// 开头，并与 Developers 后台完全一致。"
    elif not token_ready:
        next_action = "生成授权链接，用店铺主账号浏览器打开，复制 code 回 ERP 换 token。"
    else:
        next_action = "授权配置已具备。可直接点击授权页里的“立即刷新类目缓存”，同步 Mercado Libre 官方类目和必填属性。"
    fields = [
        {"key": "app_id", "label": "App ID / Client ID", "ok": bool(app_id), "value": mask_secret(app_id) if app_id else "缺失"},
        {"key": "app_secret", "label": "Client Secret", "ok": bool(app_secret), "value": mask_secret(app_secret) if app_secret else "缺失"},
        {"key": "redirect_uri", "label": "Redirect URI", "ok": bool(redirect_uri) and redirect_uri.lower().startswith("https://"), "value": redirect_uri or "缺失"},
        {"key": "site_id", "label": "Site", "ok": bool(site_id), "value": site_id},
        {"key": "code_verifier", "label": "code_verifier", "ok": bool(code_verifier), "value": "已生成，等待 code 换 token" if code_verifier else "未生成"},
        {"key": "access_token", "label": "Access Token", "ok": bool(access_token), "value": mask_secret(access_token) if access_token else "未保存"},
        {"key": "refresh_token", "label": "Refresh Token", "ok": bool(refresh_token), "value": mask_secret(refresh_token) if refresh_token else "未保存"},
    ]
    lines = ["Mercado Libre 授权配置检查清单"]
    lines.extend([f"- {item['label']}：{'OK' if item['ok'] else '缺失/需检查'}（{item['value']}）" for item in fields])
    lines.append(f"- 下一步：{next_action}")
    return {
        "platform": "mercadolibre",
        "ready_for_auth_link": ready_for_auth_link,
        "token_ready": token_ready,
        "missing_codes": missing,
        "fields": fields,
        "next_action": next_action,
        "copy_text": "\n".join(lines),
    }


def summarize_store_auth(platform: str, store: dict[str, Any]) -> dict[str, Any]:
    platform = str(platform or "").strip().lower()
    store = store if isinstance(store, dict) else {}
    status_label = _auth_status_label(store.get("auth_status"), store)
    error_code = str(store.get("auth_error_code") or "").strip()
    error_message = str(store.get("auth_error_message") or "").strip()
    masked_account = str(store.get("auth_masked_account") or "").strip()
    if not masked_account:
        if platform == "mercadolibre":
            masked_account = str(store.get("shop_name") or store.get("user_id") or "").strip()
        elif platform == "wildberries":
            masked_account = str(store.get("shop_name") or store.get("subject_id") or "").strip()
        elif platform == "ozon":
            masked_account = str(store.get("shop_name") or store.get("client_id") or "").strip()
    if not masked_account:
        candidates = [
            store.get("access_token"),
            store.get("refresh_token"),
            store.get("content_token"),
            store.get("prices_token"),
            store.get("marketplace_token"),
            store.get("stocks_token"),
            store.get("api_key"),
            store.get("app_secret"),
        ]
        for candidate in candidates:
            if str(candidate or "").strip():
                masked_account = mask_secret(candidate)
                break
    return {
        "platform": platform,
        "status": status_label,
        "checked_at": str(store.get("auth_checked_at") or "").strip(),
        "masked_account": masked_account,
        "error_code": error_code,
        "error_message": error_message,
        "next_action": str(store.get("auth_next_action") or _auth_next_action(platform, status_label, error_code, error_message)).strip(),
        "shop_name": str(store.get("shop_name") or "").strip(),
        "site_id": str(store.get("site_id") or store.get("country") or "").strip(),
        "bound": status_label in {"测试成功", "已绑定"},
    }


def summarize_store_auth_states(store_config: dict[str, Any]) -> dict[str, Any]:
    store_config = store_config if isinstance(store_config, dict) else {}
    return {
        platform: summarize_store_auth(platform, store_config.get(platform, {}))
        for platform in ("mercadolibre", "wildberries", "ozon")
    }


def store_auth_failure_code(platform: str, message: str) -> str:
    text = str(message or "").lower()
    platform = str(platform or "").strip().lower()
    if platform == "mercadolibre":
        if "redirect_uri" in text and "mismatch" in text:
            return "redirect_uri_mismatch"
        if "invalid_client" in text or "client_id" in text and "invalid" in text:
            return "invalid_client"
        if "invalid_grant" in text:
            return "invalid_grant"
        if "refresh token" in text and "invalid" in text:
            return "refresh_token_invalid"
        if "expired" in text and "token" in text:
            return "token_expired"
        if "callback" in text:
            return "callback_not_received"
        return "mercadolibre_auth_failed"
    if platform == "wildberries":
        if "429" in text or "too many requests" in text:
            return "rate_limited"
        if "401" in text or "403" in text or "unauthorized" in text:
            return "permission_denied"
        return "wildberries_auth_failed"
    if platform == "ozon":
        if "429" in text or "too many requests" in text:
            return "rate_limited"
        if "401" in text or "403" in text or "unauthorized" in text:
            return "permission_denied"
        return "ozon_auth_failed"
    return "auth_failed"


def _store_auth_result_fields(
    platform: str,
    status: str,
    account: str = "",
    error_code: str = "",
    error_message: str = "",
    next_action: str = "",
) -> dict[str, str]:
    from .collect_helpers import collect_time_iso

    platform = str(platform or "").strip().lower()
    account_text = str(account or "").strip()
    error_code_text = str(error_code or "").strip()
    error_message_text = str(error_message or "").strip()
    next_action_text = str(next_action or "").strip()
    return {
        "auth_status": status,
        "auth_checked_at": collect_time_iso(),
        "auth_masked_account": account_text,
        "auth_error_code": error_code_text,
        "auth_error_message": error_message_text,
        "auth_next_action": next_action_text or _auth_next_action(platform, status, error_code_text, error_message_text),
    }


def _clear_store_auth_result() -> dict[str, str]:
    return {
        "auth_status": "",
        "auth_checked_at": "",
        "auth_masked_account": "",
        "auth_error_code": "",
        "auth_error_message": "",
        "auth_next_action": "",
    }



AI_CONFIG_ALIAS_KEYS_TO_DROP = app_config_runtime.AI_CONFIG_ALIAS_KEYS_TO_DROP

def default_app_config() -> dict[str, Any]:
    return app_config_runtime.default_app_config()


def normalize_ai_section(section: Any, defaults: dict[str, str], include_quality: bool = False) -> dict[str, str]:
    return app_config_runtime.normalize_ai_section(section, defaults, include_quality=include_quality)


def normalize_app_config(config: dict[str, Any]) -> dict[str, Any]:
    return app_config_runtime.normalize_app_config(config)


__all__ = [
    "AI_CONFIG_ALIAS_KEYS_TO_DROP",
    "delete_products_from_index",
    "explain_mercadolibre_auth_error",
    "load_app_config",
    "load_draft_from_index",
    "load_drafts_index",
    "load_product",
    "load_product_from_index",
    "load_products_index",
    "load_store_config",
    "mercadolibre_auth_checklist",
    "merge_store_config_fields",
    "normalize_app_config",
    "normalize_product_fields",
    "product_identity",
    "publish_queue_platforms",
    "save_app_config",
    "save_product",
    "save_store_config",
    "summarize_store_auth_states",
    "sync_product_workflow_statuses",
]
