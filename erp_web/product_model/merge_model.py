from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from erp_web.marketplace_registry import marketplace_site
from erp_web.schemas.product import (
    PRODUCT_SCHEMA_VERSION,
    DraftTargetSite,
    PlatformDraft,
    Product,
)

from .attribute_matching import infer_source_attribute_matches, source_package_dimensions
from .common import PLATFORMS, SOURCE_IMAGE_ORIGINS, normalize_list, parse_dimensions_text, text_or_empty
from .defaults import default_collect_diagnostics, default_draft, default_pricing, default_product_model, default_source
from .draft_image_model import normalize_draft_image_refs
from .image_pool_model import image_pool_refs, normalize_image_pool
from .platform_sku import resolve_platform_draft_sku
from .mercadolibre_publication import normalize_mercadolibre_publication


_REMOVED_PRODUCT_FIELDS = {
    "id",
    "title",
    "source_images",
    "source_image_urls",
    "detail_images",
    "detail_image_urls",
    "gtin",
    "barcode",
    "detected_price",
    "detected_currency",
    "detected_price_display",
    "source_url",
    "source_platform",
    "category_id",
    "yandex_category_id",
    "ozon_category_id",
    "sale_price",
    "local_platform_categories",
}
_REMOVED_DRAFT_FIELDS = {
    "barcode",
    "gtin",
    "category_attribute_schema",
    "categoryAttributeSchema",
}
_CANONICAL_PRODUCT_FIELDS = frozenset(Product.__annotations__)
_CANONICAL_DRAFT_FIELDS = frozenset(PlatformDraft.__annotations__)
_CANONICAL_TARGET_FIELDS = frozenset(DraftTargetSite.__annotations__)
_PRIMARY_TARGET_ROOT_FIELDS = (
    "category_id",
    "description_category_id",
    "category_path",
    "attributes",
)


def _strip_publish_logs(value: Any) -> Any:
    """Keep publish history in its table instead of product JSON."""
    if isinstance(value, dict):
        return {
            key: _strip_publish_logs(item)
            for key, item in value.items()
            if key != "publish_logs"
        }
    if isinstance(value, list):
        return [_strip_publish_logs(item) for item in value]
    return value


def _canonical_product_output(product: dict[str, Any]) -> dict[str, Any]:
    """Return only fields owned by the current product schema."""
    canonical = {
        key: value
        for key, value in product.items()
        if key in _CANONICAL_PRODUCT_FIELDS
    }
    return _strip_publish_logs(canonical)


def _reject_removed_product_fields(product: dict[str, Any]) -> None:
    removed = sorted(_REMOVED_PRODUCT_FIELDS.intersection(product))
    if removed:
        raise ValueError(
            "产品包含已删除的历史字段："
            + "、".join(removed)
            + "；请使用当前 canonical schema"
        )


def validate_product_root_fields(
    product: dict[str, Any],
    *,
    require_schema_version: bool = False,
) -> None:
    """Reject retired or unknown root keys at persistence boundaries."""

    _reject_removed_product_fields(product)
    unknown = sorted(set(product).difference(_CANONICAL_PRODUCT_FIELDS))
    if unknown:
        raise ValueError(
            "产品包含非 canonical 根字段："
            + "、".join(unknown)
        )
    if require_schema_version and product.get("schema_version") in (None, ""):
        raise ValueError(
            "持久化产品缺少有效 schema_version；"
            "请清空开发数据并按当前 schema 重建"
        )


def validate_platform_draft_root_fields(
    draft: dict[str, Any],
) -> None:
    """Reject retired or unknown draft root keys at persistence boundaries."""

    removed = sorted(_REMOVED_DRAFT_FIELDS.intersection(draft))
    if removed:
        raise ValueError(
            "草稿包含已删除的历史字段："
            + "、".join(removed)
            + "；请使用当前 canonical schema"
        )
    unknown = sorted(set(draft).difference(_CANONICAL_DRAFT_FIELDS))
    if unknown:
        raise ValueError(
            "草稿包含非 canonical 根字段："
            + "、".join(unknown)
        )


def _target_text(
    raw: dict[str, Any],
    field: str,
    alias: str,
) -> str:
    """读取 target 自身字段；缺失与显式空值都归一化为空字符串。"""

    if field in raw:
        value = raw.get(field)
    elif alias in raw:
        value = raw.get(alias)
    else:
        value = ""
    return str(value or "").strip()


def normalize_draft_target_site(
    value: Any,
    platform: str,
    identity_defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize a target-site boundary payload to current schema fields."""
    raw = value if isinstance(value, dict) else {}
    defaults = (
        identity_defaults
        if isinstance(identity_defaults, dict)
        else {}
    )
    target_platform = str(
        raw.get("platform")
        or defaults.get("platform")
        or platform
    ).strip().lower()
    raw_site = str(
        raw.get("site")
        or raw.get("site_id")
        or defaults.get("site")
        or defaults.get("site_id")
        or ""
    ).strip()
    selected = marketplace_site(target_platform, raw_site)
    if (
        target_platform == "mercadolibre"
        and str(selected.get("code") or raw_site).strip().upper() != "CBT"
    ):
        raise ValueError(
            "Mercado Libre Global Selling 只允许 CBT/Siteless 一级草稿；"
            "MLM、MLB、MLC、MCO 等站点只能通过 sites_to_sell 选择"
        )
    # target_sites[] 是刊登事实的唯一 owner；defaults 仅提供身份与语言默认。
    raw_publish_status = (
        raw.get("publish_status")
        if "publish_status" in raw
        else raw.get("publishStatus")
        if "publishStatus" in raw
        else ""
    )
    raw_status = (
        raw.get("status")
        if "status" in raw
        else ""
    )
    canonical = {
        "platform": target_platform,
        "site": str(selected.get("code") or raw_site),
        "language": str(
            raw.get("language")
            or defaults.get("language")
            or selected.get("language")
            or ""
        ),
        "listing_currency": str(
            raw.get("listing_currency")
            or raw.get("listingCurrency")
            or (
                raw.get("currency")
                if target_platform != "ozon"
                else ""
            )
            or ""
        ).strip().upper(),
        # 币种指纹是核价时的店铺配置快照，只透传，不由静态来源生成。
        "currency_fingerprint": str(
            raw.get("currency_fingerprint")
            or raw.get("currencyFingerprint")
            or ""
        ).strip(),
        "category_id": _target_text(
            raw,
            "category_id",
            "categoryId",
        ),
        "description_category_id": _target_text(
            raw,
            "description_category_id",
            "descriptionCategoryId",
        ),
        "category_path": _target_text(
            raw,
            "category_path",
            "categoryPath",
        ),
        # Mercado Libre Global Selling 的实际销售国家属于当前刊登目标；
        # 不得从 target.site=CBT 或草稿 shipping 自动推导。
        "sites_to_sell": normalize_mercadolibre_sites_to_sell(
            raw.get("sites_to_sell")
            if isinstance(raw.get("sites_to_sell"), list)
            else raw.get("sitesToSell")
            if isinstance(raw.get("sitesToSell"), list)
            else []
        ),
        "attributes": deepcopy(
            raw.get("attributes")
            if isinstance(raw.get("attributes"), dict)
            else {}
        ),
        "validation_errors": deepcopy(
            raw.get("validation_errors")
            if isinstance(raw.get("validation_errors"), list)
            else raw.get("validationErrors")
            if isinstance(raw.get("validationErrors"), list)
            else []
        ),
        "category_precheck": deepcopy(
            raw.get("category_precheck")
            if isinstance(raw.get("category_precheck"), dict)
            else raw.get("categoryPrecheck")
            if isinstance(raw.get("categoryPrecheck"), dict)
            else {}
        ),
        "publish_status": str(
            raw_publish_status or ""
        ).strip(),
        "status": str(
            raw_status or ""
        ).strip(),
        "last_precheck": deepcopy(
            raw.get("last_precheck")
            if isinstance(raw.get("last_precheck"), dict)
            else raw.get("lastPrecheck")
            if isinstance(raw.get("lastPrecheck"), dict)
            else {}
        ),
        "last_precheck_target": deepcopy(
            raw.get("last_precheck_target")
            if isinstance(raw.get("last_precheck_target"), dict)
            else raw.get("lastPrecheckTarget")
            if isinstance(raw.get("lastPrecheckTarget"), dict)
            else {}
        ),
        "last_publish_task": deepcopy(
            raw.get("last_publish_task")
            if isinstance(raw.get("last_publish_task"), dict)
            else raw.get("lastPublishTask")
            if isinstance(raw.get("lastPublishTask"), dict)
            else {}
        ),
    }
    return {
        key: item
        for key, item in canonical.items()
        if key in _CANONICAL_TARGET_FIELDS
    }


def normalize_mercadolibre_sites_to_sell(value: Any) -> list[dict[str, Any]]:
    """规范化 Global Selling 销售目标及其独立销售条件。

    ``site_id`` 与 ``logistic_type`` 标识一个 marketplace operation；价格、
    listing type、配送和 sale terms 都属于该 operation，不能在产品持久化时
    丢弃。缺省值由 payload builder 在发布边界补齐，这里只保留显式事实。
    """

    rows = value if isinstance(value, list) else []
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        site_id = str(
            raw.get("site_id")
            or raw.get("siteId")
            or raw.get("site")
            or ""
        ).strip().upper()
        logistic_type = str(
            raw.get("logistic_type")
            or raw.get("logisticType")
            or ""
        ).strip().lower()
        if not site_id and not logistic_type:
            continue
        key = (site_id, logistic_type)
        if key in seen:
            continue
        seen.add(key)
        target: dict[str, Any] = {
            "site_id": site_id,
            "logistic_type": logistic_type,
        }
        price = raw.get("price")
        if price not in (None, ""):
            target["price"] = price
        net_proceeds = raw.get("net_proceeds")
        if net_proceeds in (None, ""):
            net_proceeds = raw.get("netProceeds")
        if net_proceeds not in (None, ""):
            target["net_proceeds"] = net_proceeds
        listing_type_id = str(
            raw.get("listing_type_id")
            or raw.get("listingTypeId")
            or raw.get("listing_type")
            or ""
        ).strip()
        if listing_type_id:
            target["listing_type_id"] = listing_type_id
        status = str(raw.get("status") or "").strip().lower()
        if status:
            target["status"] = status
        if raw.get("free_shipping") not in (None, ""):
            target["free_shipping"] = bool(raw.get("free_shipping"))
        elif raw.get("freeShipping") not in (None, ""):
            target["free_shipping"] = bool(raw.get("freeShipping"))
        sale_terms = (
            raw.get("sale_terms")
            if isinstance(raw.get("sale_terms"), list)
            else raw.get("saleTerms")
            if isinstance(raw.get("saleTerms"), list)
            else None
        )
        if sale_terms is not None:
            target["sale_terms"] = deepcopy(sale_terms)
        normalized.append(target)
    return sorted(
        normalized,
        key=lambda item: (item["site_id"], item["logistic_type"]),
    )


def mercadolibre_sales_operation_keys(value: Any) -> tuple[tuple[str, str], ...]:
    """返回 CBT 销售 operation 身份，不把派生价格当作市场选择变化。"""

    return tuple(
        (item["site_id"], item["logistic_type"])
        for item in normalize_mercadolibre_sites_to_sell(value)
    )


def mercadolibre_sales_condition_basis(value: Any) -> list[dict[str, Any]]:
    """返回会影响 CBT 核价有效性的销售条件，排除本轮派生金额。"""

    return [
        {
            key: deepcopy(item)
            for key, item in target.items()
            if key not in {"price", "net_proceeds"}
        }
        for target in normalize_mercadolibre_sites_to_sell(value)
    ]


def _snake_pricing_key(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", str(value)).lower()


def _canonical_pricing_mapping(
    value: Any,
    *,
    preserve_mapping_keys: bool = False,
) -> Any:
    if isinstance(value, list):
        return [_canonical_pricing_mapping(item) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    result: dict[str, Any] = {}
    for key, item in value.items():
        normalized_key = (
            str(key)
            if preserve_mapping_keys
            else _snake_pricing_key(str(key))
        )
        if normalized_key == key:
            result[normalized_key] = _canonical_pricing_mapping(
                item,
                preserve_mapping_keys=normalized_key
                in {"targets", "exchange_rates"},
            )
    for key, item in value.items():
        normalized_key = (
            str(key)
            if preserve_mapping_keys
            else _snake_pricing_key(str(key))
        )
        result.setdefault(
            normalized_key,
            _canonical_pricing_mapping(
                item,
                preserve_mapping_keys=normalized_key
                in {"targets", "exchange_rates"},
            ),
        )
    return result


def _invalidate_legacy_pricing_targets(pricing: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(pricing)
    targets = result.get("targets") if isinstance(result.get("targets"), dict) else {}
    normalized_targets: dict[str, Any] = {}
    for key, value in targets.items():
        record = deepcopy(value) if isinstance(value, dict) else {}
        applied = record.get("applied_price") if isinstance(record.get("applied_price"), dict) else {}
        basis = record.get("calculation_basis") if isinstance(record.get("calculation_basis"), dict) else {}
        fingerprint = str(record.get("calculation_fingerprint") or "").strip()
        valid_current_contract = bool(
            str(applied.get("amount") or "").strip()
            and str(applied.get("currency") or "").strip()
            and basis
            and fingerprint
        )
        if not valid_current_contract:
            for field in (
                "suggested_price",
                "applied_price",
                "minimum_price",
                "converted_prices",
                "calculation_basis",
                "calculation_fingerprint",
            ):
                record.pop(field, None)
            record["stale_reason"] = "legacy_pricing_contract"
        normalized_targets[str(key).strip().lower()] = record
    result["targets"] = normalized_targets
    return result


def _canonical_platform_draft_output(
    draft: dict[str, Any],
) -> dict[str, Any]:
    return _strip_publish_logs(
        {
            key: value
            for key, value in draft.items()
            if key in _CANONICAL_DRAFT_FIELDS
        }
    )


def _merge_source(product: dict[str, Any]) -> dict[str, Any]:
    source = default_source()
    incoming = product.get("source") if isinstance(product.get("source"), dict) else {}
    source["source_url"] = str(
        incoming.get("source_url")
        or ""
    ).strip()
    source["source_platform"] = str(
        incoming.get("source_platform")
        or ""
    ).strip()
    source["title"] = str(
        incoming.get("title")
        or product.get("name")
        or ""
    ).strip()
    source["price"] = str(
        incoming.get("price")
        or product.get("cost")
        or ""
    ).strip()
    source["currency"] = str(incoming.get("currency") or "").strip()
    source["bullets"] = normalize_list(
        incoming.get("bullets")
        or product.get("selling_points")
    )
    source["description"] = str(
        incoming.get("description")
        or product.get("description")
        or ""
    ).strip()
    image_pool = incoming.get("image_pool") if isinstance(incoming.get("image_pool"), list) else []
    source_images = (
        incoming.get("images")
        if isinstance(incoming.get("images"), list)
        else []
    )
    source["image_pool"] = normalize_image_pool(
        image_pool or source_images,
        "source",
    )
    source["images"] = image_pool_refs(
        source["image_pool"],
        SOURCE_IMAGE_ORIGINS,
    )
    source["attributes"] = deepcopy(incoming.get("attributes") if isinstance(incoming.get("attributes"), dict) else product.get("attributes") if isinstance(product.get("attributes"), dict) else {})
    source["attribute_matches"] = infer_source_attribute_matches(source["attributes"])
    dimension_match = source["attribute_matches"].get("dimensions") if isinstance(source["attribute_matches"].get("dimensions"), dict) else {}
    matched_dimensions = dimension_match.get("normalized") if isinstance(dimension_match.get("normalized"), dict) else {}
    raw_dimensions = incoming.get("dimensions") if isinstance(incoming.get("dimensions"), dict) else {}
    fallback_dimensions = parse_dimensions_text(
        product.get("dimensions"),
        default_unit="mm" if source["source_platform"].lower() == "1688" else "cm",
    )
    source["dimensions"] = {
        "length_cm": str(raw_dimensions.get("length_cm") or fallback_dimensions["length_cm"] or matched_dimensions.get("length_cm") or "").strip(),
        "width_cm": str(raw_dimensions.get("width_cm") or fallback_dimensions["width_cm"] or matched_dimensions.get("width_cm") or "").strip(),
        "height_cm": str(raw_dimensions.get("height_cm") or fallback_dimensions["height_cm"] or matched_dimensions.get("height_cm") or "").strip(),
    }
    source["weight_kg"] = str(incoming.get("weight_kg") or product.get("weight_kg") or "").strip()
    source["material"] = str(incoming.get("material") or ((product.get("materials") or [""])[0]) or "").strip()
    source["package_contents"] = normalize_list(incoming.get("package_contents") or product.get("package_includes"))
    source["variants"] = deepcopy(incoming.get("variants") or [])
    source["skus"] = deepcopy(incoming.get("skus") or product.get("sku_items") or [])
    source["brand"] = str(incoming.get("brand") or product.get("brand") or "").strip()
    source["model"] = str(incoming.get("model") or product.get("model") or "").strip()
    source["sku"] = str(incoming.get("sku") or product.get("sku") or "").strip()
    source["collect_status"] = str(incoming.get("collect_status") or product.get("collect_status") or "").strip()
    source["collect_logs"] = deepcopy(incoming.get("collect_logs") or product.get("collect_logs") or [])
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
    site_config = marketplace_site(
        platform,
        str(
            current.get("site")
            or current.get("site_id")
            or ""
        ),
    )

    current["category_id"] = str(current.get("category_id") or "").strip()
    current["category_path"] = str(current.get("category_path") or "").strip()

    if site_config["code"]:
        current["site"] = site_config["code"]
        current["language"] = str(current.get("language") or product.get("marketplace_terms", {}).get(platform, {}).get("language") or site_config["language"]).strip()
        current["country"] = str(current.get("country") or site_config["label"]).strip()

    current["brand"] = str(current.get("brand") or product.get("brand") or source.get("brand") or "Generic").strip() or "Generic"
    current["model"] = str(current.get("model") or product.get("model") or source.get("model") or "General").strip() or "General"
    # 来源 SKU 是供应商数据，不是平台刊登身份；平台草稿单独拥有 SKU。
    current["sku"] = str(current.get("sku") or "").strip()
    # UPC/GTIN 是平台草稿自己的发布事实。显式清空必须保留，否则图片上传等
    # 整商品归一化路径会把商品主档旧 UPC 回填，令预览与发布重校验生成不同
    # payload。购买 UPC 的分配流程会显式写入各草稿，不需要这里提供隐式回退。
    current["upc"] = str(
        current.get("upc")
        or current.get("gtin")
        or current.get("barcode")
        or ""
    ).strip()
    current["stock"] = str(current.get("stock") or product.get("stock") or "").strip()
    current_pkg = (
        current.get("package_dimensions")
        if isinstance(current.get("package_dimensions"), dict)
        else current.get("packageDimensions")
        if isinstance(current.get("packageDimensions"), dict)
        else {}
    )
    source_dims = source_package_dimensions(source)
    current["package_dimensions"] = {
        "length_cm": str(current_pkg.get("length_cm") or current_pkg.get("lengthCm") or source_dims.get("length_cm") or product.get("package_length_cm") or "").strip(),
        "width_cm": str(current_pkg.get("width_cm") or current_pkg.get("widthCm") or source_dims.get("width_cm") or product.get("package_width_cm") or "").strip(),
        "height_cm": str(current_pkg.get("height_cm") or current_pkg.get("heightCm") or source_dims.get("height_cm") or product.get("package_height_cm") or "").strip(),
        "weight_kg": str(current_pkg.get("weight_kg") or current_pkg.get("weightKg") or source.get("weight_kg") or product.get("weight_kg") or "").strip(),
    }
    # 平台属性只属于当前平台草稿。来源站属性保留在 source.attributes，
    # 不得因为草稿为空而复制进 Mercado/Ozon/Yandex 的发布字段。
    current["attributes"] = deepcopy(current.get("attributes") or {})
    return current


def _merge_platform_draft(product: dict[str, Any], platform: str) -> dict[str, Any]:
    current = _apply_source_mappings_to_draft(product, platform, _draft_sources(product, platform))
    platform_values = []
    for item in normalize_list(
        current.get("platforms")
        or current.get("platforms_json")
    ):
        value = str(item or "").strip().lower()
        if value in PLATFORMS and value not in platform_values:
            platform_values.append(value)
    current["platform"] = platform
    current["platforms"] = platform_values or [platform]
    current["draft_id"] = str(
        current.get("draft_id")
        or current.get("draftId")
        or ""
    ).strip()
    current["product_id"] = str(
        current.get("product_id")
        or product.get("product_id")
        or ""
    ).strip()
    current["source_product_id"] = str(
        current.get("source_product_id")
        or current.get("sourceProductId")
        or current["product_id"]
    ).strip()
    current["enabled"] = bool(current.get("enabled", True))
    current["site"] = str(
        current.get("site")
        or current.get("site_id")
        or ""
    ).strip()
    current["category_id"] = str(
        current.get("category_id")
        or current.get("categoryId")
        or ""
    ).strip()
    current["description_category_id"] = str(
        current.get("description_category_id")
        or current.get("descriptionCategoryId")
        or ""
    ).strip()
    current["category_path"] = str(
        current.get("category_path")
        or current.get("categoryPath")
        or ""
    ).strip()
    current["global_title"] = str(
        current.get("global_title")
        or current.get("globalTitle")
        or ""
    ).strip()
    current["images"] = normalize_draft_image_refs(current.get("images"))
    current["bullets"] = normalize_list(current.get("bullets"))
    current["search_terms"] = normalize_list(
        current.get("search_terms")
        or current.get("searchTerms")
    )
    current["validation_errors"] = deepcopy(
        current.get("validation_errors")
        if isinstance(current.get("validation_errors"), list)
        else current.get("validationErrors")
        if isinstance(current.get("validationErrors"), list)
        else []
    )
    current["attributes"] = deepcopy(current.get("attributes") or {})
    pricing = (
        _invalidate_legacy_pricing_targets(
            _canonical_pricing_mapping(current.get("pricing"))
        )
        if isinstance(current.get("pricing"), dict)
        else {}
    )
    merged_pricing = default_pricing(platform)
    merged_pricing.update({key: deepcopy(value) for key, value in pricing.items() if key in merged_pricing and value not in (None, "")})
    merged_pricing["platform"] = platform
    current["pricing"] = merged_pricing
    current["allow_gtin_exemption"] = bool(
        current.get("allow_gtin_exemption")
        or current.get("allowGtinExemption")
        or current.get("gtin_exempt")
    )
    current["sale_terms"] = deepcopy(
        current.get("sale_terms")
        if isinstance(current.get("sale_terms"), list)
        else current.get("saleTerms")
        if isinstance(current.get("saleTerms"), list)
        else current.get("warranty")
        if isinstance(current.get("warranty"), list)
        else []
    )
    current["shipping"] = deepcopy(
        current.get("shipping")
        if isinstance(current.get("shipping"), dict)
        else {}
    )
    package_dimensions = (
        current.get("package_dimensions")
        if isinstance(current.get("package_dimensions"), dict)
        else current.get("packageDimensions")
        if isinstance(current.get("packageDimensions"), dict)
        else {}
    )
    current["package_dimensions"] = {
        "length_cm": str(
            package_dimensions.get("length_cm")
            or package_dimensions.get("lengthCm")
            or ""
        ).strip(),
        "width_cm": str(
            package_dimensions.get("width_cm")
            or package_dimensions.get("widthCm")
            or ""
        ).strip(),
        "height_cm": str(
            package_dimensions.get("height_cm")
            or package_dimensions.get("heightCm")
            or ""
        ).strip(),
        "weight_kg": str(
            package_dimensions.get("weight_kg")
            or package_dimensions.get("weightKg")
            or ""
        ).strip(),
    }
    current["publish_status"] = str(
        current.get("publish_status")
        or current.get("publishStatus")
        or ""
    ).strip()
    raw_targets = (
        current.get("target_sites")
        if isinstance(current.get("target_sites"), list)
        else current.get("targetSites")
        if isinstance(current.get("targetSites"), list)
        else []
    )
    normalized_targets = [
        normalize_draft_target_site(item, platform)
        for item in raw_targets
        if isinstance(item, dict)
    ]
    identity_defaults = {
        "platform": current.get("platform") or platform,
        "site": current.get("site") or current.get("site_id") or "",
    }
    current["target_sites"] = normalized_targets or [
        normalize_draft_target_site(
            {},
            platform,
            identity_defaults,
        )
    ]
    primary_target = current["target_sites"][0]
    primary_platform = str(
        primary_target.get("platform") or ""
    ).strip().lower()
    if primary_platform != platform:
        raise ValueError(
            "草稿 primary target 与草稿平台不一致："
            f"{primary_platform or '<empty>'} != {platform}"
        )
    current["site"] = str(primary_target.get("site") or "").strip()
    current["language"] = str(primary_target.get("language") or "").strip()
    for field, alias in (
        ("category_precheck", "categoryPrecheck"),
        ("last_precheck", "lastPrecheck"),
        ("last_precheck_target", "lastPrecheckTarget"),
        ("last_publish_task", "lastPublishTask"),
    ):
        current[field] = deepcopy(
            current.get(field)
            if isinstance(current.get(field), dict)
            else current.get(alias)
            if isinstance(current.get(alias), dict)
            else {}
        )
    current["publication"] = (
        normalize_mercadolibre_publication(
            current.get("publication")
            if isinstance(current.get("publication"), dict)
            else {}
        )
        if platform == "mercadolibre"
        else {}
    )
    # target_sites[] 是平台类目内容的唯一事实来源。根类目字段只是首个
    # （primary）目标的派生投影，不能保留编辑器当前选中兄弟市场的值；
    # 否则会形成 platform=yandex、category_id=Ozon 的混合草稿。根状态与
    # 校验结果仍表示整份多市场草稿，不在这里改写。
    for field in _PRIMARY_TARGET_ROOT_FIELDS:
        current[field] = deepcopy(primary_target[field])
    current["ai_copy_ready"] = bool(
        current.get("ai_copy_ready")
        or current.get("aiCopyReady")
    )
    for field, alias in (
        ("copy_generated_at", "copyGeneratedAt"),
        ("copy_source", "copySource"),
        ("copy_operation_key", "copyOperationKey"),
        ("created_at", "createdAt"),
        ("updated_at", "updatedAt"),
    ):
        current[field] = str(
            current.get(field)
            or current.get(alias)
            or ""
        ).strip()
    current["sku"] = resolve_platform_draft_sku(
        current,
        platform,
        product_id=current.get("product_id") or product.get("product_id"),
    )
    return _canonical_platform_draft_output(current)


def normalize_platform_draft(
    draft: dict[str, Any] | None,
    platform: str,
    product_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonicalize a standalone draft at every persistence boundary.

    Current HTTP boundary aliases are normalized here; the returned mapping
    contains only ``PlatformDraft`` fields.
    """
    platform_key = str(platform or "").strip().lower()
    if platform_key not in PLATFORMS:
        raise ValueError(f"不支持的平台：{platform_key or '<empty>'}")
    product = deepcopy(
        product_context
        if isinstance(product_context, dict)
        else {}
    )
    drafts = (
        deepcopy(product.get("drafts"))
        if isinstance(product.get("drafts"), dict)
        else {}
    )
    drafts[platform_key] = deepcopy(
        draft
        if isinstance(draft, dict)
        else {}
    )
    product["drafts"] = drafts
    return _merge_platform_draft(product, platform_key)


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

    for field in ["source_url", "source_platform", "title", "price", "currency", "description", "weight_kg", "material", "brand", "model", "sku", "collect_status"]:
        apply_if_present(source, field, updates.get(field))
    for field in ["bullets", "images", "image_pool", "package_contents", "variants", "skus", "collect_logs"]:
        apply_if_present(source, field, updates.get(field))
    apply_if_present(source, "attributes", updates.get("attributes"))
    apply_if_present(source, "dimensions", updates.get("dimensions"))
    if should_clear_collect_images:
        kept_pool: list[dict[str, Any]] = []
        for item in source.get("image_pool") if isinstance(source.get("image_pool"), list) else []:
            if not isinstance(item, dict):
                continue
            origin = text_or_empty(item.get("origin")) or "source"
            if origin not in SOURCE_IMAGE_ORIGINS:
                kept_pool.append(deepcopy(item))
        source["image_pool"] = kept_pool
        source["images"] = []
        kept_asset_ids = {
            asset_id
            for item in kept_pool
            for asset_id in [text_or_empty(item.get("id") or item.get("asset_id"))]
            if asset_id
        }
        kept_image_refs = {
            ref
            for item in kept_pool
            for ref in [text_or_empty(item.get("url") or item.get("path") or item.get("preview_url"))]
            if ref
        }
        drafts = normalized.get("drafts") if isinstance(normalized.get("drafts"), dict) else {}
        for draft in drafts.values():
            if not isinstance(draft, dict):
                continue
            draft["images"] = [
                ref
                for ref in normalize_draft_image_refs(draft.get("images"))
                if text_or_empty(ref.get("asset_id")) in kept_asset_ids
            ] if kept_asset_ids else []
        for sku_items in (
            normalized.get("sku_items"),
            source.get("skus"),
        ):
            for sku_item in sku_items if isinstance(sku_items, list) else []:
                if (
                    isinstance(sku_item, dict)
                    and text_or_empty(sku_item.get("image"))
                    not in kept_image_refs
                ):
                    sku_item["image"] = ""
    if isinstance(source.get("image_pool"), list):
        source["images"] = image_pool_refs(
            normalize_image_pool(source["image_pool"], "source"),
            SOURCE_IMAGE_ORIGINS,
        ) or source.get("images", [])

    current_diag = source.get("collect_diagnostics") if isinstance(source.get("collect_diagnostics"), dict) else default_collect_diagnostics()
    source["collect_diagnostics"] = _merge_collect_diagnostics(current_diag, diagnostics_updates or updates.get("collect_diagnostics"))

    normalized["source"] = source
    normalized["name"] = str(source.get("title") or normalized.get("name") or "").strip()
    normalized["brand"] = str(source.get("brand") or normalized.get("brand") or "").strip()
    normalized["model"] = str(source.get("model") or normalized.get("model") or "").strip()
    normalized["sku"] = str(source.get("sku") or normalized.get("sku") or "").strip()
    if isinstance(source.get("attributes"), dict) and source.get("attributes"):
        normalized["attributes"] = deepcopy(source["attributes"])
    if isinstance(source.get("skus"), list) and source["skus"]:
        normalized["sku_items"] = deepcopy(source["skus"])
    normalized["materials"] = normalize_list(normalized.get("materials") or [source.get("material")])
    normalized["selling_points"] = normalize_list(normalized.get("selling_points") or source.get("bullets"))
    normalized["package_includes"] = normalize_list(normalized.get("package_includes") or source.get("package_contents"))
    normalized["description"] = str(normalized.get("description") or source.get("description") or "").strip()
    normalized["weight_kg"] = str(normalized.get("weight_kg") or source.get("weight_kg") or "").strip()
    normalized["collect_status"] = str(normalized.get("collect_status") or source.get("collect_status") or "").strip()
    normalized["collect_logs"] = deepcopy(normalized.get("collect_logs") or source.get("collect_logs") or [])
    dimensions = source.get("dimensions") if isinstance(source.get("dimensions"), dict) else {}
    if any(str(dimensions.get(part) or "").strip() for part in ["length_cm", "width_cm", "height_cm"]):
        normalized["dimensions"] = " x ".join(
            str(dimensions.get(part) or "").strip() for part in ["length_cm", "width_cm", "height_cm"] if str(dimensions.get(part) or "").strip()
        ) + (" cm" if all(str(dimensions.get(part) or "").strip() for part in ["length_cm", "width_cm", "height_cm"]) else "")
    return _canonical_product_output(normalized)


def normalize_product_model(product: dict[str, Any] | None) -> dict[str, Any]:
    incoming = deepcopy(product or {})
    _reject_removed_product_fields(incoming)
    raw_schema_version = incoming.get("schema_version")
    if raw_schema_version not in (None, ""):
        try:
            incoming_schema_version = int(raw_schema_version)
        except (TypeError, ValueError) as exc:
            raise ValueError("产品 schema_version 必须是整数") from exc
        if incoming_schema_version < 1 or incoming_schema_version > PRODUCT_SCHEMA_VERSION:
            raise ValueError(
                "产品 schema_version "
                f"{incoming_schema_version} 不在可迁移范围 1.."
                f"{PRODUCT_SCHEMA_VERSION}，拒绝降级写入或读取"
            )
    normalized = default_product_model()
    normalized.update({key: value for key, value in incoming.items() if key not in {"source", "drafts"}})
    normalized["schema_version"] = PRODUCT_SCHEMA_VERSION
    normalized["product_id"] = str(
        incoming.get("product_id") or ""
    ).strip()
    normalized["upc"] = str(
        incoming.get("upc") or ""
    ).strip()
    normalized["source"] = _merge_source(incoming)
    draft_input = deepcopy(incoming)
    draft_input["source"] = normalized["source"]
    normalized["drafts"] = {
        platform: _merge_platform_draft(draft_input, platform)
        for platform in PLATFORMS
    }

    normalized["name"] = str(normalized["source"].get("title") or normalized.get("name") or "").strip()
    category_match = normalized["source"].get("attribute_matches", {}).get("category") if isinstance(normalized["source"].get("attribute_matches"), dict) else {}
    normalized["category"] = str(normalized.get("category") or (category_match.get("value") if isinstance(category_match, dict) else "") or "").strip()
    normalized["materials"] = normalize_list(normalized.get("materials") or [normalized["source"].get("material")])
    normalized["selling_points"] = normalize_list(normalized.get("selling_points") or normalized["source"].get("bullets"))
    normalized["package_includes"] = normalize_list(normalized.get("package_includes") or normalized["source"].get("package_contents"))
    normalized["description"] = str(normalized.get("description") or normalized["drafts"]["mercadolibre"].get("description") or normalized["source"].get("description") or "").strip()

    normalized["collect_status"] = str(normalized.get("collect_status") or normalized["source"].get("collect_status") or "").strip()
    normalized["collect_logs"] = deepcopy(normalized.get("collect_logs") or normalized["source"].get("collect_logs") or [])
    if not isinstance(normalized["source"].get("collect_diagnostics"), dict):
        normalized["source"]["collect_diagnostics"] = default_collect_diagnostics()
    else:
        normalized["source"]["collect_diagnostics"] = _merge_collect_diagnostics(default_collect_diagnostics(), normalized["source"].get("collect_diagnostics"))

    return _canonical_product_output(normalized)
