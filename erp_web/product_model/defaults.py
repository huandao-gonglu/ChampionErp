from __future__ import annotations

from typing import Any

from .common import PLATFORMS
from .image_pool_model import default_image_pool_item
from erp_web.marketplace_registry import default_marketplace_site
from erp_web.schemas.product import PRODUCT_SCHEMA_VERSION

def default_source() -> dict[str, Any]:
    return {
        "source_url": "",
        "source_platform": "",
        "title": "",
        "price": "",
        "currency": "",
        "bullets": [],
        "description": "",
        "images": [],
        "image_pool": [],
        "dimensions": {
            "length_cm": "",
            "width_cm": "",
            "height_cm": "",
        },
        "weight_kg": "",
        "material": "",
        "package_contents": [],
        "variants": [],
        "skus": [],
        "attributes": {},
        "attribute_matches": {},
        "brand": "",
        "model": "",
        "sku": "",
        "collect_status": "",
        "collect_logs": [],
        "collect_diagnostics": default_collect_diagnostics(),
    }


def default_collect_diagnostics() -> dict[str, Any]:
    return {
        "collect_mode": "",
        "source_url": "",
        "normalized_url": "",
        "platform_detected": "",
        "started_at": "",
        "finished_at": "",
        "success": False,
        "partial_success": False,
        "error_code": "",
        "error_message": "",
        "page_title": "",
        "final_url": "",
        "http_status": "",
        "is_login_page": False,
        "is_captcha_page": False,
        "is_security_check_page": False,
        "images_found_count": 0,
        "title_found": False,
        "price_found": False,
        "bullets_found_count": 0,
        "sku_found_count": 0,
        "dimensions_found": False,
        "weight_found": False,
        "html_snapshot_path": "",
        "screenshot_path": "",
        "collected_fields": [],
        "missing_fields": [],
        "next_action": "",
        "checked_at": "",
        "parser_version": "collect-v2",
    }


def default_pricing(platform: str = "") -> dict[str, Any]:
    platform_key = str(platform or "").strip().lower()
    return {
        "platform": platform_key,
        "common": {},
        "targets": {},
        "exchange_rates": {},
        "updated_at": "",
    }


def default_draft(platform: str) -> dict[str, Any]:
    site = default_marketplace_site(platform)
    return {
        "platform": platform,
        "platforms": [platform],
        "target_sites": [{
            "platform": platform,
            "site": site["code"],
            "language": site["language"],
            # 默认草稿币种为空：发布币种只能来自店铺授权配置，不从站点
            # 注册表复制静态值。
            "listing_currency": "",
            "currency_fingerprint": "",
        }],
        "enabled": True,
        "title": "",
        "description": "",
        "bullets": [],
        "search_terms": [],
        "language": site["language"],
        "country": "",
        "site": site["code"],
        "category_id": "",
        "description_category_id": "",
        "category_path": "",
        "attributes": {},
        "brand": "",
        "model": "",
        "sku": "",
        "upc": "",
        "stock": "",
        "images": [],
        "package_dimensions": {
            "length_cm": "",
            "width_cm": "",
            "height_cm": "",
            "weight_kg": "",
        },
        "pricing": default_pricing(platform),
        "validation_errors": [],
        "status": "collected",
        "publish_status": "",
        "copy_operation_key": "",
    }


def default_product_model() -> dict[str, Any]:
    return {
        "schema_version": PRODUCT_SCHEMA_VERSION,
        "product_id": "",
        "name": "",
        "brand": "",
        "category": "",
        "target_customer": "",
        "materials": [],
        "dimensions": "",
        "colors": [],
        "selling_points": [],
        "package_includes": [],
        "avoid_claims": [],
        "marketplace_terms": {
            "mercadolibre": {
                "language": "es",
                "product_type": "",
                "primary_keywords": [],
                "attribute_keywords": [],
            },
            "yandex": {
                "language": "ru-RU",
                "product_type": "",
                "primary_keywords": [],
                "attribute_keywords": [],
            },
            "ozon": {
                "language": "ru-RU",
                "product_type": "",
                "primary_keywords": [],
                "attribute_keywords": [],
            },
        },
        "attributes": {},
        "listing_overrides": {},
        "copy_results": {},
        "sku_items": [],
        "selected_sku_indices": [],
        "pricing_defaults": {},
        "publish_preview": {},
        "sku": "",
        "model": "",
        "weight_kg": "",
        "stock": "",
        "upc": "",
        "collect_status": "",
        "collect_logs": [],
        "description": "",
        "source": default_source(),
        "drafts": {platform: default_draft(platform) for platform in PLATFORMS},
    }
