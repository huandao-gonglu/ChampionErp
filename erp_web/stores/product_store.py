# -*- coding: utf-8 -*-
from __future__ import annotations

"""Product/draft store: CRUD, index views and workflow status.

``ProductStore`` owns every product/draft read-write path on top of
``ErpDatabase`` plus the workflow-status derivation used by the index views.
Pure normalization helpers stay module-level so they can be used without a
database handle. This module never imports ``erp_web.context`` — the store is
constructed by ``AppContext`` and handed its database.
"""

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterator

from erp_web.db import ErpDatabase, product_identity
from erp_web.marketplace_registry import marketplace_site
from erp_web.product_model import (
    PLATFORMS,
    apply_created_image_refs_to_draft,
    default_product_model,
    normalize_draft_target_site,
    normalize_draft_image_refs,
    normalize_mercadolibre_sites_to_sell,
    normalize_platform_draft,
    normalize_product_model,
    validate_product_root_fields,
)
from erp_web.product_model.common import normalize_list
from erp_web.runtime_units.image_pool_core import (
    _display_image_ref,
    _source_pool_items,
    current_image_pool,
    enrich_product_image_dimensions,
)
from erp_web.schemas.product import PRODUCT_SCHEMA_VERSION


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def mask_secret(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return f"{text[:2]}****"
    return f"{text[:4]}****{text[-4:]}"


def product_id_from_body(body: dict[str, Any]) -> str:
    return str(body.get("product_id") or "").strip()


RETIRED_CATEGORY_SCHEMA_FIELD = "RETIRED_CATEGORY_SCHEMA_FIELD"

_RETIRED_PRODUCT_CATEGORY_KEYS = ("local_platform_categories", "localPlatformCategories")
_RETIRED_DRAFT_SCHEMA_KEYS = ("category_attribute_schema", "categoryAttributeSchema")


class RetiredCategorySchemaFieldError(ValueError):
    """保存入口收到已退役的平台规则副本字段；调用方须重新读取当前契约。"""

    def __init__(self, fields: list[str]) -> None:
        self.code = RETIRED_CATEGORY_SCHEMA_FIELD
        self.fields = list(fields)
        super().__init__(
            "请求包含已退役的类目规则字段："
            + "、".join(self.fields)
            + "；平台类目规则改由 CategoryCatalog 实时读取，请移除这些字段。"
        )


def reject_retired_product_category_fields(data: dict[str, Any]) -> None:
    found = [key for key in _RETIRED_PRODUCT_CATEGORY_KEYS if key in data]
    if found:
        raise RetiredCategorySchemaFieldError(found)


def reject_retired_draft_schema_fields(draft_payload: dict[str, Any]) -> None:
    found: list[str] = []
    for key in _RETIRED_DRAFT_SCHEMA_KEYS:
        if key in draft_payload:
            found.append(key)
    raw_targets = (
        draft_payload.get("target_sites")
        if isinstance(draft_payload.get("target_sites"), list)
        else draft_payload.get("targetSites")
        if isinstance(draft_payload.get("targetSites"), list)
        else []
    )
    for site in raw_targets:
        if not isinstance(site, dict):
            continue
        for key in _RETIRED_DRAFT_SCHEMA_KEYS:
            if key in site:
                found.append(f"target_sites[].{key}")
    if found:
        raise RetiredCategorySchemaFieldError(sorted(set(found)))


def normalize_sku_items(product: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
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
                "price": str(source.get("price") or product.get("cost") or ""),
                "stock": str(product.get("stock") or ""),
                "image": str(
                    (
                        normalize_list(source.get("images"))
                        or [
                            str(item.get("url") or item.get("path") or "")
                            for item in source.get("image_pool", [])
                            if isinstance(item, dict)
                            and str(item.get("url") or item.get("path") or "").strip()
                        ]
                        or [""]
                    )[0]
                ),
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
    normalized.setdefault("marketplace_terms", {})
    normalized.setdefault("listing_overrides", {})
    normalized.setdefault("copy_results", {})
    normalized.setdefault("sku_items", [])
    normalized.setdefault("selected_sku_indices", [])
    normalized.setdefault("pricing_defaults", {})
    normalized.setdefault("publish_preview", {})
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


def normalize_persisted_product_fields(
    product: dict[str, Any],
) -> dict[str, Any]:
    validate_product_root_fields(
        product,
        require_schema_version=True,
    )
    raw_version = product.get("schema_version")
    try:
        schema_version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "持久化产品缺少有效 schema_version；请清空开发数据并按当前 schema 重建"
        ) from exc
    if schema_version != PRODUCT_SCHEMA_VERSION:
        raise ValueError(
            "持久化产品 schema_version "
            f"{schema_version} 不是当前版本 {PRODUCT_SCHEMA_VERSION}；"
            "请清空开发数据并按当前 schema 重建"
        )
    return normalize_product_fields(product)


# -- workflow-status helpers (pure) -----------------------------------------

def _draft_copy_ready(draft: dict[str, Any]) -> bool:
    return bool(
        draft.get("copy_generated_at")
        or draft.get("ai_copy_ready")
        or str(draft.get("copy_source") or "").lower() in {"ai", "deepseek", "openai", "fallback_ai"}
    ) and bool(str(draft.get("title") or "").strip() and str(draft.get("description") or "").strip())


def _draft_images_ready(draft: dict[str, Any]) -> bool:
    return bool(normalize_draft_image_refs(draft.get("images")))


def _draft_pricing_ready(draft: dict[str, Any]) -> bool:
    pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
    targets = pricing.get("targets") if isinstance(pricing.get("targets"), dict) else {}
    return any(
        isinstance(item, dict)
        and isinstance(item.get("applied_price"), dict)
        and str(item["applied_price"].get("amount") or "").strip()
        for item in targets.values()
    )


def _draft_publish_fields_ready(draft: dict[str, Any]) -> bool:
    attrs = draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {}
    return all(
        [
            str(draft.get("category_id") or "").strip(),
            bool(attrs),
            _draft_pricing_ready(draft),
            str(draft.get("stock") or "").strip(),
        ]
    )


def _draft_precheck_ready(product: dict[str, Any], platform: str, draft: dict[str, Any]) -> bool:
    preview_map = product.get("publish_preview") if isinstance(product.get("publish_preview"), dict) else {}
    preview = preview_map.get(platform) if isinstance(preview_map.get(platform), dict) else {}
    publish_status = str(draft.get("publish_status") or "").strip().lower()
    return bool(preview.get("ok") is True or publish_status in {"ready", "published", "real_publish_success", "success"})


def _normalize_delete_ids(value: Any) -> list[str]:
    raw_ids = value if isinstance(value, list) else [value]
    ids: list[str] = []
    seen: set[str] = set()
    for raw_id in raw_ids:
        normalized_id = str(raw_id or "").strip()
        if normalized_id and normalized_id not in seen:
            ids.append(normalized_id)
            seen.add(normalized_id)
    return ids


def _normalized_target_payload(target: dict[str, Any], platform: str, selected_site: dict[str, Any]) -> dict[str, Any]:
    # 发布币种唯一事实源是店铺授权配置；站点注册表不再为草稿目标提供币种。
    return normalize_draft_target_site(
        target,
        platform,
        {
            "platform": platform,
            "site": selected_site["code"],
            "language": selected_site["language"],
        },
    )


def _draft_target_identity(
    target: dict[str, Any],
    *,
    fallback_platform: str = "",
    fallback_site: str = "",
) -> tuple[str, str]:
    return (
        str(target.get("platform") or fallback_platform).strip().lower(),
        str(
            target.get("site")
            or target.get("site_id")
            or fallback_site
        ).strip().upper(),
    )


def _changed_mercadolibre_cbt_targets(
    existing: dict[str, Any],
    incoming_targets: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Return CBT targets whose canonical sales-country selection changed."""

    existing_platform = str(existing.get("platform") or "").strip().lower()
    existing_site = str(existing.get("site") or "").strip()
    raw_existing_targets = (
        existing.get("target_sites")
        if isinstance(existing.get("target_sites"), list)
        else []
    )
    existing_targets = {
        _draft_target_identity(
            target,
            fallback_platform=existing_platform,
            fallback_site=existing_site,
        ): target
        for target in raw_existing_targets
        if isinstance(target, dict)
    }
    changed: dict[int, dict[str, Any]] = {}
    for index, target in enumerate(incoming_targets):
        identity = _draft_target_identity(target)
        if identity != ("mercadolibre", "CBT"):
            continue
        previous = existing_targets.get(identity)
        if not isinstance(previous, dict):
            continue
        previous_sites = normalize_mercadolibre_sites_to_sell(
            previous.get("sites_to_sell")
        )
        incoming_sites = normalize_mercadolibre_sites_to_sell(
            target.get("sites_to_sell")
        )
        if previous_sites != incoming_sites:
            changed[index] = previous
    return changed


class ProductStore:
    """Product/draft CRUD + index + workflow status over ``ErpDatabase``."""

    def __init__(self, db: ErpDatabase) -> None:
        self._db = db

    # -- workflow status -----------------------------------------------------

    def draft_workflow_status(self, product: dict[str, Any], platform: str = "mercadolibre") -> str:
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
        if _draft_copy_ready(draft) and _draft_images_ready(draft):
            return "images_ready"
        if _draft_copy_ready(draft):
            return "copy_ready"
        return "claimed"

    def publish_queue_platforms(self, product: dict[str, Any], requested_platforms: list[str] | None = None) -> list[str]:
        product = self.sync_product_workflow_statuses(product or {})
        targets = requested_platforms or list(PLATFORMS)
        normalized_targets = [str(platform or "").strip().lower() for platform in targets if str(platform or "").strip().lower() in PLATFORMS]
        eligible: list[str] = []
        for platform in normalized_targets:
            draft = (product.get("drafts") or {}).get(platform) if isinstance(product.get("drafts"), dict) else {}
            draft = draft if isinstance(draft, dict) else {}
            if self.draft_workflow_status(product, platform) == "ready_to_publish" or _draft_precheck_ready(product, platform, draft):
                eligible.append(platform)
        return eligible

    def sync_product_workflow_statuses(self, product: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_product_fields(product or {})
        drafts = normalized.get("drafts") if isinstance(normalized.get("drafts"), dict) else {}
        for platform, draft in list(drafts.items()):
            if platform not in PLATFORMS or not isinstance(draft, dict):
                continue
            draft["status"] = self.draft_workflow_status(normalized, platform)
            drafts[platform] = draft
        normalized["workflow_statuses"] = {
            platform: drafts[platform].get("status", "collected")
            for platform in PLATFORMS
            if isinstance(drafts.get(platform), dict)
        }
        return normalized

    def product_index_status(self, product: dict[str, Any], platform: str = "mercadolibre") -> dict[str, Any]:
        product = self.sync_product_workflow_statuses(product)
        source = product.get("source") if isinstance(product.get("source"), dict) else {}
        draft = (product.get("drafts") or {}).get(platform) if isinstance(product.get("drafts"), dict) else {}
        draft = draft if isinstance(draft, dict) else {}
        pool = _source_pool_items(product)
        workflow_status = self.draft_workflow_status(product, platform)
        has_copy = workflow_status in {"copy_ready", "images_ready", "ready_to_publish", "published"}
        has_generated_image = any(str(item.get("origin") or "") in {"ai_generated", "chatgpt_import"} for item in pool)
        queue_platforms = self.publish_queue_platforms(product, [platform])
        return {
            "collect_status": source.get("collect_status") or ("success" if source.get("title") else "pending"),
            "workflow_status": workflow_status,
            "draft_statuses": product.get("workflow_statuses") or {},
            "ai_copy_status": "done" if has_copy else "pending",
            "image_status": "done" if workflow_status in {"images_ready", "ready_to_publish", "published"} or pool else "pending",
            "category_status": "done" if draft.get("category_id") else "pending",
            "attributes_status": "done" if isinstance(draft.get("attributes"), dict) and draft.get("attributes") else "pending",
            "pricing_status": "done" if _draft_pricing_ready(draft) else "pending",
            "precheck_status": ((product.get("publish_preview") or {}).get(platform) or {}).get("ok", "pending") if isinstance(product.get("publish_preview"), dict) else "pending",
            "publish_status": draft.get("publish_status") or "not_ready",
            "publish_queue_ready": bool(queue_platforms),
            "publish_queue_platforms": queue_platforms,
            "optimized": bool(has_copy or has_generated_image),
        }

    # -- product CRUD ----------------------------------------------------------

    def load_product(self) -> dict[str, Any]:
        records = self._db.list_product_records(limit=1)
        if records:
            loaded = self._db.load_product_model(records[0]["product_id"])
            if loaded:
                return normalize_persisted_product_fields(loaded)
        return normalize_product_fields(default_product_model())

    def save_product(self, data: dict[str, Any]) -> dict[str, Any]:
        reject_retired_product_category_fields(data)
        validate_product_root_fields(data)
        product = self.sync_product_workflow_statuses(enrich_product_image_dimensions(normalize_product_fields(data)))
        product["product_id"] = product_identity(product)
        product_id = self._db.upsert_product_model(product)
        return normalize_persisted_product_fields(
            self._db.load_product_model(product_id)
        )

    def assign_upc_to_product(
        self,
        data: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Atomically claim a UPC and persist the canonical product."""
        product = self.sync_product_workflow_statuses(
            enrich_product_image_dimensions(
                normalize_product_fields(data)
            )
        )
        upc, product_id = self._db.assign_upc_to_product_model(product)
        if not upc:
            return "", product
        return upc, self.load_product_from_index(product_id, "")

    def save_product_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        product_data = dict(data or {})
        product_data.pop("drafts", None)
        product_id = str(product_data.get("product_id") or "").strip()
        existing = self.load_product_from_index(product_id, "") if product_id else {}
        if str(existing.get("product_id") or "").strip() == product_id:
            existing.pop("drafts", None)
            existing_source = (
                existing.get("source")
                if isinstance(existing.get("source"), dict)
                else {}
            )
            patch_source = (
                product_data.get("source")
                if isinstance(product_data.get("source"), dict)
                else {}
            )
            product_data = {**existing, **product_data}
            product_data["source"] = {**existing_source, **patch_source}
        source = product_data.get("source") if isinstance(product_data.get("source"), dict) else None
        if source is not None and "name" in product_data:
            source["title"] = str(product_data.get("name") or source.get("title") or "").strip()
        return self.save_product(product_data)

    # -- index views -------------------------------------------------------------

    def sanitize_products_index(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sanitized: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            record = dict(item)
            record["main_image"] = _display_image_ref(str(record.get("main_image") or ""))
            sanitized.append(record)
        return sanitized

    def load_products_index(self) -> list[dict[str, Any]]:
        return self.sanitize_products_index(self._db.list_product_records())

    def load_drafts_index(self, scope: str = "active") -> list[dict[str, Any]]:
        return self.sanitize_products_index(self._db.list_draft_records(scope=scope))

    def iter_drafts_index(self, scope: str = "active") -> Iterator[dict[str, Any]]:
        """流式读取完整草稿集合；供需要精确 total/count 的服务使用。"""

        for item in self._db.iter_draft_records(scope=scope):
            sanitized = self.sanitize_products_index([item])
            if sanitized:
                yield sanitized[0]

    def delete_products_from_index(self, product_ids: list[Any]) -> dict[str, Any]:
        ids = _normalize_delete_ids(product_ids if isinstance(product_ids, list) else [product_ids])
        if not ids:
            return {"ok": False, "error": "请先选择要删除的商品。", "deleted": 0, "deletedIds": [], "productsIndex": self.load_products_index()}

        deleted_ids: list[str] = []
        missing_ids: list[str] = []
        for product_id in ids:
            deleted = self._db.delete_product_model(product_id)
            if deleted:
                deleted_ids.append(product_id)
            else:
                missing_ids.append(product_id)

        products_index = self.load_products_index()
        product = self.load_product()

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

    def delete_draft_from_index(self, draft_id: Any) -> dict[str, Any]:
        normalized_ids = _normalize_delete_ids(draft_id)
        if not normalized_ids:
            return {
                "ok": False,
                "error": "请先选择要删除的草稿。",
                "deleted": 0,
                "deletedDraftId": "",
                "deletedDraftIds": [],
                "deletedIds": [],
                "missingIds": [],
                "draftsIndex": self.load_drafts_index(),
            }

        deleted_ids: list[str] = []
        missing_ids: list[str] = []
        affected_product_ids: list[str] = []
        for normalized_id in normalized_ids:
            draft = self._db.load_draft_model(normalized_id)
            product_id = str(draft.get("product_id") or "")
            deleted = self._db.delete_draft_model(normalized_id)
            if deleted:
                deleted_ids.append(normalized_id)
                if product_id and product_id not in affected_product_ids:
                    affected_product_ids.append(product_id)
            else:
                missing_ids.append(normalized_id)

        product = self.load_product_from_index(affected_product_ids[0], "") if len(affected_product_ids) == 1 else self.load_product()
        deleted_count = len(deleted_ids)
        message = "草稿已删除。" if deleted_count == 1 else f"已删除 {deleted_count} 个草稿。"
        if not deleted_count:
            message = "草稿不存在或已被删除。"

        return {
            "ok": deleted_count > 0,
            "deleted": deleted_count,
            "deletedDraftId": deleted_ids[0] if deleted_count == 1 else "",
            "deletedDraftIds": deleted_ids,
            "deletedIds": deleted_ids,
            "missingIds": missing_ids,
            "affectedProductIds": affected_product_ids,
            "product": product,
            "productsIndex": self.load_products_index(),
            "draftsIndex": self.load_drafts_index(),
            "imagePool": current_image_pool(product),
            "message": message,
            "error": "" if deleted_count else "草稿不存在或已被删除。",
        }

    def load_product_from_index(self, product_id: str = "", file_path: str = "") -> dict[str, Any]:
        product_id = str(product_id or "").strip()
        file_path = str(file_path or "").strip()
        sqlite_product_id = product_id
        if not sqlite_product_id and file_path.startswith("sqlite://products/"):
            sqlite_product_id = file_path.rsplit("/", 1)[-1]
        if sqlite_product_id:
            loaded = self._db.load_product_model(sqlite_product_id)
            if loaded:
                return normalize_persisted_product_fields(loaded)
        return self.load_product()

    def load_required_product_from_body(self, body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
        product_id = product_id_from_body(body)
        if not product_id:
            return {}, {"ok": False, "error": "product_id 不能为空"}, 400
        product = self.load_product_from_index(product_id, "")
        loaded_id = str(product.get("product_id") or "").strip()
        if loaded_id != product_id:
            return {}, {"ok": False, "error": "商品不存在", "product_id": product_id}, 404
        return product, None, 200

    def load_draft_from_index(self, draft_id: str) -> dict[str, Any]:
        draft_id = str(draft_id or "").strip()
        if draft_id:
            loaded = self._db.load_product_for_draft(draft_id)
            if loaded:
                current_draft_id = str(
                    loaded.pop("current_draft_id", "")
                    or draft_id
                )
                current_draft_platform = str(
                    loaded.pop("current_draft_platform", "")
                )
                product = normalize_persisted_product_fields(loaded)
                product["current_draft_id"] = current_draft_id
                product["current_draft_platform"] = (
                    current_draft_platform
                )
                return product
        return self.load_product()

    # -- draft detail --------------------------------------------------------------

    def draft_product_context(self, product: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_product_fields(product or {})
        source = normalized.get("source") if isinstance(normalized.get("source"), dict) else {}
        dimensions = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
        return {
            "product_id": str(normalized.get("product_id") or ""),
            "source_product_id": str(normalized.get("source_product_id") or normalized.get("product_id") or ""),
            "title": str(normalized.get("name") or source.get("title") or ""),
            "source_title": str(source.get("title") or normalized.get("name") or ""),
            "source_platform": str(source.get("source_platform") or ""),
            "source_url": str(source.get("source_url") or ""),
            "brand": str(normalized.get("brand") or source.get("brand") or ""),
            "model": str(normalized.get("model") or source.get("model") or ""),
            "sku": str(normalized.get("sku") or ""),
            "stock": str(normalized.get("stock") or ""),
            "cost": str(normalized.get("cost") or normalized.get("source_price_cny_for_cost") or source.get("price") or ""),
            "source_price": str(source.get("price") or ""),
            "currency": str(source.get("currency") or ""),
            "weight_kg": str(source.get("weight_kg") or normalized.get("weight_kg") or ""),
            "dimensions": {
                "length_cm": str(dimensions.get("length_cm") or dimensions.get("lengthCm") or ""),
                "width_cm": str(dimensions.get("width_cm") or dimensions.get("widthCm") or ""),
                "height_cm": str(dimensions.get("height_cm") or dimensions.get("heightCm") or ""),
            },
            "image_pool": current_image_pool(normalized),
            "raw": normalized,
        }

    def load_draft_detail_from_index(self, draft_id: str) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
        draft_id = str(draft_id or "").strip()
        if not draft_id:
            return {}, {"ok": False, "error": "draft_id 不能为空"}, 400
        draft = self._db.load_draft_model(draft_id)
        if not draft:
            return {}, {"ok": False, "error": "草稿不存在", "draft_id": draft_id}, 404
        product = self._db.load_product_model(str(draft.get("source_product_id") or draft.get("product_id") or ""))
        if not product:
            return {}, {"ok": False, "error": "草稿关联商品不存在", "draft_id": draft_id}, 404
        product = normalize_persisted_product_fields(product)
        return {
            "ok": True,
            "draft": draft,
            "productContext": self.draft_product_context(product),
            "productsIndex": self.load_products_index(),
            "draftsIndex": self.load_drafts_index(),
        }, None, 200

    def save_draft_detail(self, draft_payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
        draft_id = str(draft_payload.get("draft_id") or draft_payload.get("draftId") or "").strip()
        if not draft_id:
            return {}, {"ok": False, "error": "draft_id 不能为空"}, 400
        try:
            reject_retired_draft_schema_fields(draft_payload)
        except RetiredCategorySchemaFieldError as exc:
            return (
                {},
                {
                    "ok": False,
                    "error": str(exc),
                    "error_code": exc.code,
                    "draft_id": draft_id,
                },
                400,
            )
        existing = self._db.load_draft_model(draft_id)
        if not existing:
            return {}, {"ok": False, "error": "草稿不存在", "draft_id": draft_id}, 404
        product_id = str(existing.get("product_id") or "").strip()
        source_product_id = str(existing.get("source_product_id") or product_id).strip()
        existing_platform = str(existing.get("platform") or "").strip().lower()
        requested_language = str(draft_payload.get("language") or existing.get("language") or "").strip()
        raw_targets = draft_payload.get("target_sites") if isinstance(draft_payload.get("target_sites"), list) else draft_payload.get("targetSites")
        requested_primary_target = (
            raw_targets[0]
            if isinstance(raw_targets, list)
            and raw_targets
            and isinstance(raw_targets[0], dict)
            else {}
        )
        requested_primary_platform = str(
            requested_primary_target.get("platform")
            or draft_payload.get("platform")
            or existing_platform
        ).strip().lower()
        requested_primary_site = str(
            requested_primary_target.get("site")
            or requested_primary_target.get("site_id")
            or draft_payload.get("site")
            or existing.get("site")
            or ""
        ).strip().upper()
        # 迁移旧 CBT/es 草稿：Global Selling 全局刊登必须使用英语。
        if (
            requested_primary_platform == "mercadolibre"
            and requested_primary_site == "CBT"
        ):
            requested_language = str(
                marketplace_site("mercadolibre", "CBT").get("language") or "en-US"
            )
        targets: list[dict[str, Any]] = []
        for raw_target in raw_targets if isinstance(raw_targets, list) else []:
            target = raw_target if isinstance(raw_target, dict) else {}
            target_platform = str(target.get("platform") or "").strip().lower()
            selected_site = marketplace_site(target_platform, str(target.get("site") or target.get("site_id") or ""))
            if target_platform not in PLATFORMS or not selected_site.get("code"):
                continue
            if requested_language and selected_site["language"].lower() != requested_language.lower():
                continue
            if not any(item["platform"] == target_platform and item["site"] == selected_site["code"] for item in targets):
                targets.append(_normalized_target_payload(target, target_platform, selected_site))
        if not targets:
            requested_platform = str(draft_payload.get("platform") or existing_platform).strip().lower()
            platform = requested_platform if requested_platform in PLATFORMS else existing_platform
            selected_site = marketplace_site(platform, str(draft_payload.get("site") or existing.get("site") or ""))
            if platform not in PLATFORMS or not selected_site.get("code"):
                return {}, {"ok": False, "error": "草稿站点不支持", "draft_id": draft_id}, 400
            fallback_target = {}
            existing_targets = existing.get("target_sites") if isinstance(existing.get("target_sites"), list) else []
            if existing_targets:
                fallback_target = existing_targets[0] if isinstance(existing_targets[0], dict) else {}
            targets = [_normalized_target_payload(fallback_target, platform, selected_site)]
        changed_cbt_targets = _changed_mercadolibre_cbt_targets(
            existing,
            targets,
        )
        for index, previous_target in changed_cbt_targets.items():
            previous_publish_task = (
                previous_target.get("last_publish_task")
                if isinstance(previous_target.get("last_publish_task"), dict)
                else {}
            )
            if not previous_publish_task:
                previous_publish_task = (
                    existing.get("last_publish_task")
                    if isinstance(existing.get("last_publish_task"), dict)
                    else {}
                )
            targets[index] = {
                **targets[index],
                "validation_errors": [],
                "last_precheck": {},
                "last_precheck_target": {},
                "publish_status": "",
                "status": "category_ready",
                # 远端商品身份是已发生的发布事实，不随新销售目标失效。
                "last_publish_task": deepcopy(previous_publish_task),
            }
        primary_target = targets[0]
        platform = primary_target["platform"]
        platforms = []
        for target in targets:
            target_platform = str(target.get("platform") or "").strip().lower()
            if target_platform in PLATFORMS and target_platform not in platforms:
                platforms.append(target_platform)
        merged = {
            **existing,
            **dict(draft_payload),
            "draft_id": draft_id,
            "product_id": product_id,
            "source_product_id": source_product_id,
            "platform": platform,
            "platforms": platforms or [platform],
            "site": primary_target["site"],
            "target_sites": targets,
            "language": primary_target["language"],
        }
        if changed_cbt_targets:
            merged.update(
                {
                    "validation_errors": [],
                    "last_precheck": {},
                    "last_precheck_target": {},
                    "publish_status": "",
                    "status": "category_ready",
                    "pricing": {},
                    "last_publish_task": deepcopy(
                        existing.get("last_publish_task")
                        if isinstance(existing.get("last_publish_task"), dict)
                        else {}
                    ),
                }
            )
        merged["images"] = normalize_draft_image_refs(merged.get("images"))
        merged = normalize_platform_draft(
            merged,
            platform,
            {"product_id": product_id},
        )
        saved_draft_id = self._db.upsert_draft_model(product_id, platform, merged)
        draft = self._db.load_draft_model(saved_draft_id)
        product = self._db.load_product_model(source_product_id or product_id)
        if changed_cbt_targets and isinstance(product, dict):
            publish_preview = (
                dict(product.get("publish_preview"))
                if isinstance(product.get("publish_preview"), dict)
                else {}
            )
            if "mercadolibre" in publish_preview:
                publish_preview.pop("mercadolibre", None)
                product["publish_preview"] = publish_preview
                self._db.upsert_product_model(product)
                draft = self._db.load_draft_model(saved_draft_id)
                product = self._db.load_product_model(
                    source_product_id or product_id
                )
        product = normalize_persisted_product_fields(product)
        return {
            "ok": True,
            "draft": draft,
            "productContext": self.draft_product_context(product),
            "productsIndex": self.load_products_index(),
            "draftsIndex": self.load_drafts_index(),
            "message": "草稿已保存。",
        }, None, 200

    def apply_image_assets_to_draft(
        self,
        draft_id: str,
        created_items: list[dict[str, Any]],
        strategy: str = "append",
    ) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
        draft_id = str(draft_id or "").strip()
        if not draft_id:
            return {}, {"ok": False, "error": "draft_id 不能为空"}, 400
        existing = self._db.load_draft_model(draft_id)
        if not existing:
            return {}, {"ok": False, "error": "草稿不存在", "draft_id": draft_id}, 404
        product_id = str(existing.get("product_id") or "").strip()
        platform = str(existing.get("platform") or "").strip().lower()
        if not product_id or platform not in PLATFORMS:
            return {}, {"ok": False, "error": "草稿关联商品或平台无效", "draft_id": draft_id}, 400
        product = self._db.load_product_model(str(existing.get("source_product_id") or product_id))
        product = normalize_persisted_product_fields(product)
        next_images = apply_created_image_refs_to_draft(existing.get("images"), created_items, strategy)
        merged = {**existing, "images": next_images}
        product_for_status = dict(product or {})
        drafts = product_for_status.get("drafts") if isinstance(product_for_status.get("drafts"), dict) else {}
        product_for_status["drafts"] = {**drafts, platform: merged}
        merged["status"] = self.draft_workflow_status(product_for_status, platform)
        saved_draft_id = self._db.upsert_draft_model(product_id, platform, merged)
        draft = self._db.load_draft_model(saved_draft_id)
        product = self._db.load_product_model(str(draft.get("source_product_id") or product_id))
        product = normalize_persisted_product_fields(product)
        return {
            "ok": True,
            "draft": draft,
            "productContext": self.draft_product_context(product),
            "productsIndex": self.load_products_index(),
            "draftsIndex": self.load_drafts_index(),
            "message": "草稿图片已更新。",
        }, None, 200

    def save_draft_copy_result(self, product: dict[str, Any], target_market: str, copy: dict[str, Any]) -> dict[str, Any]:
        product = normalize_product_fields(product or {})
        product_id = str(product.get("product_id") or "").strip()
        target_key = str(target_market or "").strip().lower() or "mercadolibre"
        if not product_id:
            raise RuntimeError("product_id 不能为空")
        if target_key not in PLATFORMS:
            raise RuntimeError("不支持的平台")
        drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
        draft = dict(drafts.get(target_key) if isinstance(drafts.get(target_key), dict) else {})
        draft.update(
            {
                "title": copy.get("title", ""),
                "description": copy.get("description", ""),
                "bullets": normalize_list(copy.get("bullets")),
                "search_terms": normalize_list(copy.get("search_keywords")),
                "language": str(copy.get("language") or draft.get("language") or ""),
                "copy_source": "ai",
                "copy_generated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        draft.setdefault("platform", target_key)
        draft.setdefault("platforms", [target_key])
        product_for_status = dict(product)
        merged_drafts = dict(drafts)
        merged_drafts[target_key] = draft
        product_for_status["drafts"] = merged_drafts
        draft["status"] = self.draft_workflow_status(product_for_status, target_key)
        saved_draft_id = self._db.upsert_draft_model(product_id, target_key, draft)
        saved = self.load_product_from_index(product_id, "")
        saved["current_draft_id"] = saved_draft_id
        saved["current_draft_platform"] = target_key
        return saved


__all__ = [
    "ProductStore",
    "mask_secret",
    "normalize_product_fields",
    "normalize_persisted_product_fields",
    "normalize_sku_items",
    "normalize_space",
    "product_id_from_body",
]
