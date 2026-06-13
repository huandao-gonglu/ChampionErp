# -*- coding: utf-8 -*-
from __future__ import annotations

from .source_collect_browser import (
    browser_debug_status,
    fetch_1688_page_snapshot_with_browser_session,
    fetch_page_html,
    fetch_page_html_with_browser_session,
    fetch_page_html_with_status,
    fetch_page_snapshot_with_browser_session,
    maybe_fetch_page_html_with_playwright,
    open_browser_debug_session,
)
from .source_collect_parsers import (
    collect_product_image_urls,
    extract_1688_attributes,
    extract_1688_sku,
    extract_text_pattern,
    infer_list_from_text,
    parse_1688_product,
    parse_amazon_product,
    parse_generic_product,
    populate_source_from_legacy_product,
)
from .source_collect_workflows import (
    collect_1688_product,
    collect_batch_products,
    collect_extension_payload,
    collect_from_browser_tab,
    collect_source_product,
)

__all__ = [
    "browser_debug_status",
    "collect_1688_product",
    "collect_batch_products",
    "collect_extension_payload",
    "collect_from_browser_tab",
    "collect_product_image_urls",
    "collect_source_product",
    "extract_1688_attributes",
    "extract_1688_sku",
    "extract_text_pattern",
    "fetch_1688_page_snapshot_with_browser_session",
    "fetch_page_html",
    "fetch_page_html_with_browser_session",
    "fetch_page_html_with_status",
    "fetch_page_snapshot_with_browser_session",
    "infer_list_from_text",
    "maybe_fetch_page_html_with_playwright",
    "open_browser_debug_session",
    "parse_1688_product",
    "parse_amazon_product",
    "parse_generic_product",
    "populate_source_from_legacy_product",
]
