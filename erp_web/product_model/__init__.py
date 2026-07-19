# -*- coding: utf-8 -*-
"""Public product model API."""

from __future__ import annotations

from .category_model import (
    apply_ai_attribute_fill,
    apply_category_selection,
    build_ai_attribute_fill,
    category_cache_status,
    find_category_record,
    load_category_cache,
    search_category_cache,
    validate_category_precheck,
)
from .common import (
    IMAGE_ORIGINS,
    IMAGE_USAGES,
    PLATFORMS,
    SOURCE_COMPAT_IMAGE_ORIGINS,
    normalize_list,
    parse_dimensions_text,
    text_or_empty,
)
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
    image_pool_legacy_views,
    normalize_image_pool,
    normalize_image_pool_item,
    normalize_platforms,
)
from .merge_model import merge_source_partial_result, normalize_product_model

__all__ = [
    "IMAGE_ORIGINS",
    "IMAGE_USAGES",
    "PLATFORMS",
    "SOURCE_COMPAT_IMAGE_ORIGINS",
    "apply_ai_attribute_fill",
    "apply_category_selection",
    "build_ai_attribute_fill",
    "category_cache_status",
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
    "find_category_record",
    "image_pool_legacy_views",
    "load_category_cache",
    "merge_source_partial_result",
    "normalize_draft_image_ref",
    "normalize_draft_image_refs",
    "normalize_draft_image_role",
    "normalize_image_pool",
    "normalize_image_pool_item",
    "normalize_list",
    "normalize_platforms",
    "normalize_product_model",
    "parse_dimensions_text",
    "search_category_cache",
    "text_or_empty",
    "validate_category_precheck",
]
