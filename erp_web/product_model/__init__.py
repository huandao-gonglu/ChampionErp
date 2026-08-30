# -*- coding: utf-8 -*-
"""Public product model API."""

from __future__ import annotations

from .category_model import (
    apply_ai_attribute_fill,
    apply_category_selection,
    build_ai_attribute_fill,
    unresolved_required_category_attributes,
    validate_category_precheck,
)
from .common import (
    IMAGE_ORIGINS,
    IMAGE_USAGES,
    PLATFORMS,
    SOURCE_IMAGE_ORIGINS,
    normalize_list,
    parse_dimension_measurement,
    parse_dimensions_text,
    text_or_empty,
)
from .attribute_matching import source_package_dimensions
from .defaults import (
    default_collect_diagnostics,
    default_draft,
    default_pricing,
    default_product_model,
    default_source,
)
from .draft_image_model import (
    apply_created_image_refs_to_draft,
    default_draft_image_ref,
    draft_image_asset_ids,
    draft_image_refs_from_assets,
    draft_image_refs_from_pool,
    normalize_draft_image_ref,
    normalize_draft_image_refs,
    normalize_draft_image_role,
)
from .image_pool_model import (
    default_image_pool_item,
    image_pool_refs,
    normalize_image_pool,
    normalize_image_pool_item,
    normalize_platforms,
)
from .merge_model import (
    merge_source_partial_result,
    mercadolibre_sales_condition_basis,
    mercadolibre_sales_operation_keys,
    normalize_draft_target_site,
    normalize_mercadolibre_sites_to_sell,
    normalize_platform_draft,
    normalize_product_model,
    validate_platform_draft_root_fields,
    validate_product_root_fields,
)
from .mercadolibre_publication import (
    canonicalize_mercadolibre_siteless_user_product_id,
    mercadolibre_publication_from_response,
    mercadolibre_publication_has_failures,
    normalize_mercadolibre_market_publication,
    normalize_mercadolibre_publication,
)
from .platform_sku import (
    draft_has_remote_listing,
    generated_platform_sku,
    is_placeholder_sku,
    resolve_platform_draft_sku,
)

__all__ = [
    "IMAGE_ORIGINS",
    "IMAGE_USAGES",
    "PLATFORMS",
    "SOURCE_IMAGE_ORIGINS",
    "apply_ai_attribute_fill",
    "apply_category_selection",
    "build_ai_attribute_fill",
    "canonicalize_mercadolibre_siteless_user_product_id",
    "default_collect_diagnostics",
    "default_draft",
    "default_draft_image_ref",
    "default_image_pool_item",
    "default_pricing",
    "default_product_model",
    "default_source",
    "apply_created_image_refs_to_draft",
    "draft_image_asset_ids",
    "draft_image_refs_from_assets",
    "draft_image_refs_from_pool",
    "draft_has_remote_listing",
    "generated_platform_sku",
    "image_pool_refs",
    "merge_source_partial_result",
    "mercadolibre_sales_condition_basis",
    "mercadolibre_sales_operation_keys",
    "mercadolibre_publication_from_response",
    "mercadolibre_publication_has_failures",
    "normalize_draft_image_ref",
    "normalize_draft_image_refs",
    "normalize_draft_image_role",
    "normalize_draft_target_site",
    "normalize_mercadolibre_sites_to_sell",
    "normalize_mercadolibre_market_publication",
    "normalize_mercadolibre_publication",
    "normalize_image_pool",
    "normalize_image_pool_item",
    "normalize_list",
    "normalize_platform_draft",
    "normalize_platforms",
    "normalize_product_model",
    "parse_dimension_measurement",
    "parse_dimensions_text",
    "is_placeholder_sku",
    "resolve_platform_draft_sku",
    "source_package_dimensions",
    "text_or_empty",
    "unresolved_required_category_attributes",
    "validate_category_precheck",
    "validate_platform_draft_root_fields",
    "validate_product_root_fields",
]
