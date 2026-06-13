from __future__ import annotations

from copy import deepcopy
from typing import Any

from .common import PLATFORMS, SOURCE_COMPAT_IMAGE_ORIGINS, normalize_list, parse_dimensions_text, text_or_empty
from .defaults import default_collect_diagnostics, default_draft, default_pricing, default_product_model, default_source
from .image_pool_model import image_pool_legacy_views, normalize_image_pool

def _merge_source(product: dict[str, Any]) -> dict[str, Any]:
    source = default_source()
    incoming = product.get("source") if isinstance(product.get("source"), dict) else {}
    legacy_fallback = not isinstance(product.get("source"), dict)
    source["source_url"] = str(incoming.get("source_url") or (product.get("source_url") if legacy_fallback else "") or "").strip()
    source["source_platform"] = str(incoming.get("source_platform") or (product.get("source_platform") if legacy_fallback else "") or "").strip()
    source["title"] = str(incoming.get("title") or (product.get("name") if legacy_fallback else "") or "").strip()
    source["price"] = str(incoming.get("price") or ((product.get("detected_price") or product.get("cost")) if legacy_fallback else "") or "").strip()
    source["currency"] = str(incoming.get("currency") or ((product.get("detected_currency") or product.get("currency_id")) if legacy_fallback else "") or "").strip()
    source["bullets"] = normalize_list(incoming.get("bullets") or (product.get("selling_points") if legacy_fallback else []))
    source["description"] = str(incoming.get("description") or (product.get("description") if legacy_fallback else "") or "").strip()
    image_pool = incoming.get("image_pool") if isinstance(incoming.get("image_pool"), list) else []
    legacy_images: list[Any] = []
    if isinstance(incoming.get("images"), list) and incoming.get("images"):
        legacy_images.extend(incoming.get("images"))
    elif legacy_fallback:
        legacy_images.extend(list(product.get("source_images") or []))
        legacy_images.extend(list(product.get("detail_images") or []))
        legacy_images.extend(list(product.get("source_image_urls") or []))
        legacy_images.extend(list(product.get("detail_image_urls") or []))
    source["image_pool"] = normalize_image_pool(image_pool, legacy_images, "source")
    pool_views = image_pool_legacy_views(source["image_pool"], SOURCE_COMPAT_IMAGE_ORIGINS)
    source["images"] = normalize_list(pool_views["images"] or legacy_images)
    raw_dimensions = incoming.get("dimensions") if isinstance(incoming.get("dimensions"), dict) else {}
    fallback_dimensions = parse_dimensions_text(product.get("dimensions") if legacy_fallback else "")
    fallback_package_dimensions = {
        "length_cm": text_or_empty(product.get("package_length_cm") if legacy_fallback else ""),
        "width_cm": text_or_empty(product.get("package_width_cm") if legacy_fallback else ""),
        "height_cm": text_or_empty(product.get("package_height_cm") if legacy_fallback else ""),
    }
    source["dimensions"] = {
        "length_cm": str(raw_dimensions.get("length_cm") or fallback_package_dimensions["length_cm"] or fallback_dimensions["length_cm"] or parse_dimensions_text(product.get("dimensions")).get("length_cm") or "").strip(),
        "width_cm": str(raw_dimensions.get("width_cm") or fallback_package_dimensions["width_cm"] or fallback_dimensions["width_cm"] or parse_dimensions_text(product.get("dimensions")).get("width_cm") or "").strip(),
        "height_cm": str(raw_dimensions.get("height_cm") or fallback_package_dimensions["height_cm"] or fallback_dimensions["height_cm"] or parse_dimensions_text(product.get("dimensions")).get("height_cm") or "").strip(),
    }
    source["weight_kg"] = str(incoming.get("weight_kg") or (product.get("weight_kg") if legacy_fallback else "") or "").strip()
    source["material"] = str(incoming.get("material") or ((product.get("materials") or [""])[0] if legacy_fallback else "") or "").strip()
    source["package_contents"] = normalize_list(incoming.get("package_contents") or (product.get("package_includes") if legacy_fallback else []))
    source["variants"] = deepcopy(incoming.get("variants") or (product.get("variations") if legacy_fallback else []) or [])
    source["skus"] = deepcopy(incoming.get("skus") or (product.get("sku_items") if legacy_fallback else []) or [])
    source["collect_status"] = str(incoming.get("collect_status") or (product.get("collect_status") if legacy_fallback else "") or "").strip()
    source["collect_logs"] = deepcopy(incoming.get("collect_logs") or (product.get("collect_logs") if legacy_fallback else []) or [])
    diagnostics = incoming.get("collect_diagnostics") if isinstance(incoming.get("collect_diagnostics"), dict) else {}
    source["collect_diagnostics"] = _merge_collect_diagnostics({}, diagnostics)
    return source


def _draft_sources(product: dict[str, Any], platform: str) -> dict[str, Any]:
    drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
    current = deepcopy(drafts.get(platform)) if isinstance(drafts.get(platform), dict) else default_draft(platform)
    overrides = product.get("listing_overrides") if isinstance(product.get("listing_overrides"), dict) else {}
    copy_results = product.get("copy_results") if isinstance(product.get("copy_results"), dict) else {}
    copy = copy_results.get(platform) if isinstance(copy_results.get(platform), dict) else {}
    override = overrides.get(platform) if isinstance(overrides.get(platform), dict) else {}
    for field in ["title", "description", "bullets", "search_terms", "language"]:
        value = copy.get(field) if copy.get(field) not in (None, "") else override.get(field)
        if value not in (None, ""):
            current[field] = deepcopy(value)
    return current


def _apply_source_mappings_to_draft(product: dict[str, Any], platform: str, current: dict[str, Any]) -> dict[str, Any]:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    current = deepcopy(current if isinstance(current, dict) else default_draft(platform))

    if platform == "mercadolibre":
        local_categories = product.get("local_platform_categories") if isinstance(product.get("local_platform_categories"), dict) else {}
        selected = local_categories.get(platform) if isinstance(local_categories.get(platform), dict) else {}
        current["category_id"] = str(current.get("category_id") or selected.get("category_id") or product.get("category_id") or "").strip()
        current["category_path"] = str(current.get("category_path") or selected.get("category_path") or product.get("category_path") or "").strip()
        current["language"] = str(current.get("language") or product.get("marketplace_terms", {}).get("mercadolibre", {}).get("language") or "es-MX").strip()
        current["site"] = str(current.get("site") or selected.get("site") or "MLM").strip()
        current["country"] = str(current.get("country") or selected.get("country") or "MX").strip()
    elif platform == "wildberries":
        local_categories = product.get("local_platform_categories") if isinstance(product.get("local_platform_categories"), dict) else {}
        selected = local_categories.get(platform) if isinstance(local_categories.get(platform), dict) else {}
        current["category_id"] = str(current.get("category_id") or selected.get("category_id") or product.get("wb_subject_id") or "").strip()
        current["category_path"] = str(current.get("category_path") or selected.get("category_path") or product.get("category_path") or "").strip()
        current["language"] = str(current.get("language") or product.get("marketplace_terms", {}).get("wildberries", {}).get("language") or "ru-RU").strip()
        current["site"] = str(current.get("site") or selected.get("site") or "WB").strip()
        current["country"] = str(current.get("country") or selected.get("country") or "RU").strip()
    elif platform == "ozon":
        local_categories = product.get("local_platform_categories") if isinstance(product.get("local_platform_categories"), dict) else {}
        selected = local_categories.get(platform) if isinstance(local_categories.get(platform), dict) else {}
        current["category_id"] = str(current.get("category_id") or selected.get("category_id") or product.get("ozon_category_id") or "").strip()
        current["category_path"] = str(current.get("category_path") or selected.get("category_path") or product.get("category_path") or "").strip()
        current["language"] = str(current.get("language") or "ru-RU").strip()
        current["site"] = str(current.get("site") or selected.get("site") or "OZON").strip()
        current["country"] = str(current.get("country") or selected.get("country") or "RU").strip()

    current["brand"] = str(current.get("brand") or product.get("brand") or source.get("brand") or "Generic").strip() or "Generic"
    current["model"] = str(current.get("model") or product.get("model") or source.get("model") or "General").strip() or "General"
    current["sku"] = str(current.get("sku") or product.get("sku") or "").strip()
    current["upc"] = str(current.get("upc") or product.get("upc") or "").strip()
    current["gtin"] = str(current.get("gtin") or current["upc"] or "").strip()
    current["barcode"] = str(current.get("barcode") or current["upc"] or "").strip()
    current["price"] = str(current.get("price") or source.get("price") or product.get("detected_price") or "").strip()
    current["stock"] = str(current.get("stock") or product.get("stock") or "").strip()
    current_pkg = current.get("package_dimensions") if isinstance(current.get("package_dimensions"), dict) else {}
    source_dims = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    current["package_dimensions"] = {
        "length_cm": str(current_pkg.get("length_cm") or source_dims.get("length_cm") or product.get("package_length_cm") or "").strip(),
        "width_cm": str(current_pkg.get("width_cm") or source_dims.get("width_cm") or product.get("package_width_cm") or "").strip(),
        "height_cm": str(current_pkg.get("height_cm") or source_dims.get("height_cm") or product.get("package_height_cm") or "").strip(),
        "weight_kg": str(current_pkg.get("weight_kg") or source.get("weight_kg") or product.get("weight_kg") or "").strip(),
    }
    current["attributes"] = deepcopy(current.get("attributes") or product.get("attributes") or {})
    return current


def _merge_platform_draft(product: dict[str, Any], platform: str) -> dict[str, Any]:
    current = _apply_source_mappings_to_draft(product, platform, _draft_sources(product, platform))
    current["enabled"] = bool(current.get("enabled", True))
    current["images"] = normalize_list(current.get("images") or product.get("source_image_urls") or product.get("detail_image_urls"))
    current["bullets"] = normalize_list(current.get("bullets"))
    current["search_terms"] = normalize_list(current.get("search_terms"))
    current["publish_logs"] = deepcopy(current.get("publish_logs") or [])
    current["validation_errors"] = deepcopy(current.get("validation_errors") or [])
    current["attributes"] = deepcopy(current.get("attributes") or {})
    pricing = current.get("pricing") if isinstance(current.get("pricing"), dict) else {}
    merged_pricing = default_pricing(platform)
    merged_pricing.update({key: deepcopy(value) for key, value in pricing.items() if key in merged_pricing and value not in (None, "")})
    merged_pricing["platform"] = platform
    merged_pricing["weight_kg"] = str(merged_pricing.get("weight_kg") or current["package_dimensions"].get("weight_kg") or "").strip()
    merged_pricing["length_cm"] = str(merged_pricing.get("length_cm") or current["package_dimensions"].get("length_cm") or "").strip()
    merged_pricing["width_cm"] = str(merged_pricing.get("width_cm") or current["package_dimensions"].get("width_cm") or "").strip()
    merged_pricing["height_cm"] = str(merged_pricing.get("height_cm") or current["package_dimensions"].get("height_cm") or "").strip()
    current["pricing"] = merged_pricing
    return current


def _merge_collect_diagnostics(existing: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any]:
    merged = default_collect_diagnostics()
    existing = existing if isinstance(existing, dict) else {}
    incoming = incoming if isinstance(incoming, dict) else {}
    merged.update({key: value for key, value in existing.items() if key in merged and value not in (None, "")})
    for key, value in incoming.items():
        if key not in merged:
            merged[key] = deepcopy(value)
            continue
        if isinstance(merged[key], bool):
            merged[key] = bool(value)
        elif isinstance(merged[key], int):
            try:
                merged[key] = int(value)
            except Exception:
                pass
        elif value not in (None, ""):
            merged[key] = deepcopy(value)
    return merged


def merge_source_partial_result(
    product: dict[str, Any] | None,
    source_updates: dict[str, Any] | None,
    diagnostics_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_product_model(product or {})
    source = deepcopy(normalized.get("source") or default_source())
    updates = source_updates if isinstance(source_updates, dict) else {}
    diagnostics = diagnostics_updates if isinstance(diagnostics_updates, dict) else updates.get("collect_diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    try:
        diagnostics_image_count = int(diagnostics.get("images_found_count") or 0)
    except (TypeError, ValueError):
        diagnostics_image_count = 0
    has_incoming_images = bool(updates.get("images")) or bool(updates.get("image_pool"))
    should_clear_collect_images = (
        not has_incoming_images
        and bool(diagnostics)
        and not bool(diagnostics.get("success"))
        and diagnostics_image_count <= 0
        and (bool(diagnostics.get("error_code")) or "images_found_count" in diagnostics)
    )

    def apply_if_present(target: dict[str, Any], key: str, value: Any) -> None:
        if value in (None, "", [], {}):
            return
        if isinstance(value, dict):
            if not isinstance(target.get(key), dict):
                target[key] = {}
            for nested_key, nested_value in value.items():
                if nested_value in (None, "", [], {}):
                    continue
                target[key][nested_key] = deepcopy(nested_value)
            return
        if isinstance(value, list):
            if value:
                target[key] = deepcopy(value)
            return
        target[key] = deepcopy(value)

    for field in ["source_url", "source_platform", "title", "price", "currency", "description", "weight_kg", "material", "collect_status"]:
        apply_if_present(source, field, updates.get(field))
    for field in ["bullets", "images", "image_pool", "package_contents", "variants", "skus", "collect_logs"]:
        apply_if_present(source, field, updates.get(field))
    apply_if_present(source, "dimensions", updates.get("dimensions"))
    if should_clear_collect_images:
        kept_pool: list[dict[str, Any]] = []
        for item in source.get("image_pool") if isinstance(source.get("image_pool"), list) else []:
            if not isinstance(item, dict):
                continue
            origin = text_or_empty(item.get("origin")) or "source"
            if origin not in SOURCE_COMPAT_IMAGE_ORIGINS:
                kept_pool.append(deepcopy(item))
        source["image_pool"] = kept_pool
        source["images"] = []
        kept_refs = {
            ref
            for item in kept_pool
            for ref in [text_or_empty(item.get("url") or item.get("path") or item.get("preview_url"))]
            if ref
        }
        drafts = normalized.get("drafts") if isinstance(normalized.get("drafts"), dict) else {}
        for draft in drafts.values():
            if not isinstance(draft, dict):
                continue
            draft["images"] = [ref for ref in normalize_list(draft.get("images")) if ref in kept_refs] if kept_refs else []
        for sku_item in normalized.get("sku_items") if isinstance(normalized.get("sku_items"), list) else []:
            if isinstance(sku_item, dict) and text_or_empty(sku_item.get("image")) not in kept_refs:
                sku_item["image"] = ""
        for field in ["source_images", "source_image_urls", "detail_images", "detail_image_urls"]:
            normalized[field] = []
    if isinstance(source.get("image_pool"), list):
        pool_views = image_pool_legacy_views(normalize_image_pool(source["image_pool"], [], "source"), SOURCE_COMPAT_IMAGE_ORIGINS)
        source["images"] = pool_views["images"] or source.get("images", [])

    current_diag = source.get("collect_diagnostics") if isinstance(source.get("collect_diagnostics"), dict) else default_collect_diagnostics()
    source["collect_diagnostics"] = _merge_collect_diagnostics(current_diag, diagnostics_updates or updates.get("collect_diagnostics"))

    normalized["source"] = source
    normalized["name"] = str(source.get("title") or normalized.get("name") or "").strip()
    normalized["source_url"] = str(source.get("source_url") or normalized.get("source_url") or "").strip()
    normalized["source_platform"] = str(source.get("source_platform") or normalized.get("source_platform") or "").strip()
    normalized["materials"] = normalize_list(normalized.get("materials") or [source.get("material")])
    normalized["selling_points"] = normalize_list(normalized.get("selling_points") or source.get("bullets"))
    normalized["package_includes"] = normalize_list(normalized.get("package_includes") or source.get("package_contents"))
    normalized["source_images"] = normalize_list(normalized.get("source_images") or source.get("images"))
    normalized["source_image_urls"] = normalize_list(normalized.get("source_image_urls") or source.get("images"))
    normalized["description"] = str(normalized.get("description") or source.get("description") or "").strip()
    normalized["weight_kg"] = str(normalized.get("weight_kg") or source.get("weight_kg") or "").strip()
    normalized["collect_status"] = str(normalized.get("collect_status") or source.get("collect_status") or "").strip()
    normalized["collect_logs"] = deepcopy(normalized.get("collect_logs") or source.get("collect_logs") or [])
    normalized["detected_price"] = str(normalized.get("detected_price") or source.get("price") or "").strip()
    normalized["detected_currency"] = str(normalized.get("detected_currency") or source.get("currency") or "").strip()
    if normalized.get("detected_price") and normalized.get("detected_currency"):
        normalized["detected_price_display"] = f"{normalized['detected_price']} {normalized['detected_currency']}"
    dimensions = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    if any(str(dimensions.get(part) or "").strip() for part in ["length_cm", "width_cm", "height_cm"]):
        normalized["dimensions"] = " x ".join(
            str(dimensions.get(part) or "").strip() for part in ["length_cm", "width_cm", "height_cm"] if str(dimensions.get(part) or "").strip()
        ) + (" cm" if all(str(dimensions.get(part) or "").strip() for part in ["length_cm", "width_cm", "height_cm"]) else "")
    return normalized


def normalize_product_model(product: dict[str, Any] | None) -> dict[str, Any]:
    incoming = deepcopy(product or {})
    normalized = default_product_model()
    normalized.update({key: value for key, value in incoming.items() if key not in {"source", "drafts"}})
    normalized["source"] = _merge_source(incoming)
    normalized["drafts"] = {platform: _merge_platform_draft(incoming, platform) for platform in PLATFORMS}

    normalized["name"] = str(normalized["source"].get("title") or normalized.get("name") or "").strip()
    normalized["source_url"] = str(normalized["source"].get("source_url") or normalized.get("source_url") or "").strip()
    normalized["source_platform"] = str(normalized["source"].get("source_platform") or normalized.get("source_platform") or "").strip()
    normalized["materials"] = normalize_list(normalized.get("materials") or [normalized["source"].get("material")])
    normalized["selling_points"] = normalize_list(normalized.get("selling_points") or normalized["source"].get("bullets"))
    normalized["package_includes"] = normalize_list(normalized.get("package_includes") or normalized["source"].get("package_contents"))
    pool_views = image_pool_legacy_views(
        normalized["source"].get("image_pool") if isinstance(normalized["source"].get("image_pool"), list) else [],
        SOURCE_COMPAT_IMAGE_ORIGINS,
    )
    source_images = normalize_list(normalized.get("source_images"))
    source_image_urls = normalize_list(normalized.get("source_image_urls"))
    detail_images = normalize_list(normalized.get("detail_images"))
    detail_image_urls = normalize_list(normalized.get("detail_image_urls"))
    normalized["source_images"] = source_images or pool_views["source_images"] or normalize_list(normalized["source"].get("images"))
    normalized["source_image_urls"] = source_image_urls or normalized["source_images"] or pool_views["source_image_urls"] or normalize_list(normalized["source"].get("images"))
    normalized["detail_images"] = detail_images or pool_views["detail_images"]
    normalized["detail_image_urls"] = detail_image_urls or normalized["detail_images"] or pool_views["detail_image_urls"]
    normalized["description"] = str(normalized.get("description") or normalized["drafts"]["mercadolibre"].get("description") or normalized["source"].get("description") or "").strip()

    if not normalized.get("detected_price") and normalized["source"].get("price"):
        normalized["detected_price"] = str(normalized["source"].get("price"))
    if not normalized.get("detected_currency") and normalized["source"].get("currency"):
        normalized["detected_currency"] = str(normalized["source"].get("currency"))
    if normalized.get("detected_price") and normalized.get("detected_currency"):
        normalized["detected_price_display"] = f"{normalized['detected_price']} {normalized['detected_currency']}"

    normalized["collect_status"] = str(normalized.get("collect_status") or normalized["source"].get("collect_status") or "").strip()
    normalized["collect_logs"] = deepcopy(normalized.get("collect_logs") or normalized["source"].get("collect_logs") or [])
    if not isinstance(normalized["source"].get("collect_diagnostics"), dict):
        normalized["source"]["collect_diagnostics"] = default_collect_diagnostics()
    else:
        normalized["source"]["collect_diagnostics"] = _merge_collect_diagnostics(default_collect_diagnostics(), normalized["source"].get("collect_diagnostics"))

    normalized["category_id"] = str(normalized.get("category_id") or normalized["drafts"]["mercadolibre"].get("category_id") or "").strip()
    normalized["wb_subject_id"] = str(normalized.get("wb_subject_id") or normalized["drafts"]["wildberries"].get("category_id") or "").strip()
    normalized["ozon_category_id"] = str(normalized.get("ozon_category_id") or normalized["drafts"]["ozon"].get("category_id") or "").strip()

    return normalized
