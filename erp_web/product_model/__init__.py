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
    "default_image_pool_item",
    "default_pricing",
    "default_product_model",
    "default_source",
    "find_category_record",
    "image_pool_legacy_views",
    "load_category_cache",
    "merge_source_partial_result",
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
