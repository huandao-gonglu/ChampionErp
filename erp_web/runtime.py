# -*- coding: utf-8 -*-
from __future__ import annotations

"""Legacy compatibility facade over the runtime unit modules.

兼容层，禁止新增使用（AGENTS.md）。新代码必须直接 import
``erp_web.runtime_units.*`` 或对应 facade，不得再从本模块取名字。

This module used to copy every runtime-unit symbol into its own namespace and
re-inject its globals back into all unit modules before each wrapped call.
That snapshot injection silently reverted module global rebinds and forced
dict-container workarounds in the units. It is gone.

What remains is a stateless, lazy forwarding table (PEP 562): attribute access
resolves the *current* binding in the owning unit module at call time. Nothing
is cached here and nothing is written back into the units, so monkeypatching
``erp_web.runtime`` no longer affects unit internals — patch the owning module
instead.
"""

from importlib import import_module
from typing import Any

# Symbol surface captured from the old aggregator (name -> owning unit module).
# Ownership replicates the historical "last module in _RUNTIME_UNITS order
# wins" rule; re-imported names resolve to the same objects either way.
_EXPORTS_BY_MODULE: dict[str, tuple[str, ...]] = {
    "runtime_common": (
        "AI_IMAGE_REQUEST_TIMEOUT_SECONDS", "AI_TEXT_REQUEST_TIMEOUT_SECONDS",
        "AMAZON_VERIFY_MARKERS", "APP_CONFIG_PATH", "APP_DIR", "Any", "BROWSER_DEBUG_PORT",
        "BROWSER_DEBUG_PROFILE_DIR", "BROWSER_PROFILE_DIR", "BaseHTTPRequestHandler", "CACHE_DIR",
        "CHATGPT_DIR", "COLLECT_DEBUG_DIR", "CONFIG_DIR", "DATA_DIR",
        "DEFAULT_EXCHANGE_RATE_API_URL", "DIST_DIR", "DRAFT_WORKFLOW_STATUSES",
        "EXPORTS_DIR", "FRONT_DIR", "FRONT_DIST_DIR",
        "FRONT_DIST_INDEX_PATH", "IMAGES_DIR", "LEGACY_APP_CONFIG_PATHS",
        "LEGACY_STORE_CONFIG_PATHS", "LOGS_DIR", "OUTPUT_DIR", "PLATFORMS",
        "Path", "PublishingBus", "REMOVED_LEGACY_CONFIG_PATHS",
        "SOURCE_COMPAT_IMAGE_ORIGINS", "SOURCE_DIR", "STORE_CONFIG_PATH", "TASK_DIR", "UPLOAD_DIR",
        "VERIFY_MARKERS", "WEB_PORT", "WEB_TEMPLATE_PATH", "annotations", "app_config_runtime",
        "apply_ai_attribute_fill", "apply_category_selection", "base64", "collect_service",
        "config_service", "copy_service", "dataclass", "deepcopy", "default_collect_diagnostics",
        "default_draft", "default_product_model", "erp_db", "generator", "image_pool_legacy_views",
        "image_service", "json", "legacy", "merge_source_partial_result", "normalize_image_pool",
        "normalize_image_pool_item", "normalize_platforms", "normalize_product_model", "os",
        "parse_dimensions_text", "pricing_service", "publisher", "re", "shutil", "socket",
        "struct", "subprocess", "sys", "threading", "time", "urllib", "uuid",
        "validate_category_precheck", "webbrowser",
    ),
    "category_store": (
        "_CATEGORY_AI_KEYWORD_MAP", "_CATEGORY_AI_STOPWORDS",
        "_category_suggest_query", "_category_suggest_terms",
        "_path_text", "fetch_category_attributes", "fetch_category_record", "read_json",
        "search_categories_live", "suggest_category_ids",
    ),
    "product_store": (
        "apply_image_assets_to_draft", "default_app_config", "default_store_config",
        "delete_draft_from_index", "delete_products_from_index", "draft_workflow_status",
        "explain_mercadolibre_auth_error", "load_draft_detail_from_index",
        "load_draft_from_index", "load_required_product_from_body", "mercadolibre_auth_checklist",
        "merge_app_config_fields", "merge_store_config_fields", "normalize_app_config",
        "normalize_sku_items", "normalize_store_config", "product_id_from_body",
        "product_identity", "product_index_status", "publish_queue_platforms",
        "sanitize_products_index", "save_app_config", "save_draft_detail", "save_product_profile",
        "summarize_store_auth", "update_store_config_fields",
    ),
    "image_pool": (
        "_decode_data_url", "_display_image_ref", "_image_pool_item_from_path",
        "_pool_display_item", "_uploaded_image_path", "append_images_to_product_pool",
        "apply_service_image_pool", "current_collect_debug_files", "current_generated_images",
        "default_source", "enrich_image_pool_item_dimensions", "enrich_product_image_dimensions",
        "image_files", "image_items_from_paths", "save_image_pool_for_product",
        "sync_draft_images_from_pool", "sync_generated_images_into_pool",
    ),
    "collect_helpers": (
        "claim_products_to_platforms", "collect_debug_file_url", "collect_debug_path",
        "collect_field_summary", "collect_next_action", "draft_copy_from_product",
        "draft_image_refs_from_pool", "productImages_from_source",
        "sync_product_workflow_statuses", "write_collect_debug_text",
    ),
    "publish_bus": (
        "append_publish_bus_terminal_log",
        "apply_publish_bus_result_to_product", "load_publish_logs",
        "persist_publish_bus_terminal_results", "publish_bus_log_exists",
        "publish_bus_terminal_status",
    ),
    "browser_debug": (
        "file_url", "find_named_browser_path", "open_auth_link_in_browser", "pick_web_port",
    ),
    "category_refresh": (
        "JsonClient", "http_client", "mercadolibre_category_attributes",
        "mercadolibre_category_detail", "mercadolibre_category_record", "ml_attr_required",
        "normalize_ml_attribute",
    ),
    "source_collect_browser": (
        "CdpWebSocket", "browser_debug_commands", "browser_debug_next_action",
        "cdp_target_for_url", "find_chrome_path", "normalize_browser_tab", "parse_cookie_header",
        "save_collect_snapshot_artifacts", "wait_for_cdp",
    ),
    "source_collect_parsers": (
        "HTMLParser", "_AttributeTableParser", "_add_attribute", "_clean_attribute_key",
        "_clean_attribute_value", "_extract_balanced_json_after", "_iter_cpv_rows",
        "_json_object_after", "_section_after_id", "_weight_text_to_kg",
        "extract_1688_attribute_table", "extract_1688_context_data", "html_module",
        "normalize_space",
    ),
    "source_collect_workflows": (
        "ManualCollectRequested", "apply_claimed_platform_drafts", "choose_browser_tab",
        "collect_1688_product_via_api", "collect_error_code", "collect_image_origin",
        "current_source_images", "detect_source_platform",
        "finalize_collect_diagnostics", "http_json", "normalize_collect_mode", "normalize_collect_source_images",
        "page_snapshot_from_html", "parse_collect_urls", "snapshot_field_flags",
        "snapshot_from_cdp_target", "write_collect_debug_html",
    ),
    "source_collect": (
        "browser_debug_status", "collect_1688_product", "collect_batch_products",
        "collect_extension_payload", "collect_from_browser_tab", "collect_product_image_urls",
        "collect_source_product", "extract_1688_attributes", "extract_1688_sku",
        "extract_text_pattern", "fetch_1688_page_snapshot_with_browser_session", "fetch_page_html",
        "fetch_page_html_with_browser_session", "fetch_page_html_with_status",
        "fetch_page_snapshot_with_browser_session", "infer_list_from_text",
        "maybe_fetch_page_html_with_playwright", "open_browser_debug_session",
        "parse_1688_product", "parse_amazon_product", "parse_generic_product",
        "populate_source_from_legacy_product",
    ),
    "copy_generation": (
        "_source_only_pool_items", "batch_generate_copy_for_products", "build_copy_preview",
        "build_image_prompt_pack", "default_marketplace_site", "generate_ai_copy_bundle",
        "load_product_from_index", "save_copy_result", "save_draft_copy_result",
    ),
    "draft_publish_context": (
        "ResponseWithStatus", "TARGET_LISTING_KEYS", "_normalized_target", "_save_updated_draft",
        "_select_target", "_target_key", "_target_listing_fields", "_target_update_from_draft",
        "draft_for_publish_target", "draft_product_context", "draft_publish_targets",
        "load_drafts_index", "load_required_draft_publish_context", "marketplace_site",
        "merge_target_listing_into_draft", "save_draft_precheck_result",
        "save_draft_target_listing_result",
    ),
    "auth_runtime": (
        "_merge_saved_ai_model_config", "_update_store_auth_state", "ai_gateway",
        "ai_model_config", "build_mercadolibre_auth_link", "exchange_mercadolibre_code_from_body",
        "test_ai_model_config", "test_api_config", "test_store_auth",
    ),
    "pricing_runtime": (
        "_extract_usd_rates", "_pricing_exchange_rate_config", "calculate_price",
        "fetch_pricing_exchange_rates",
    ),
    "publish_helpers": (
        "apply_product_drafts_to_plan", "build_mercadolibre_publish_payload",
        "build_plan_for_platform", "build_publish_payload", "current_image_pool",
        "load_products_index", "normalize_draft_image_refs",
        "validate_mercadolibre_publish_payload", "validate_publish_payload",
    ),
    "publish_validation": (
        "_draft_images", "_has_main_image", "_local_category_record", "_masked_auth_status",
        "_required_attribute_summary", "_review_attr_field", "_review_attr_id",
        "_review_field_from_item", "_review_precheck_items", "load_app_config",
        "validate_platform_draft",
    ),
    "publish_logs_runtime": (
        "_product_id_for_log", "_publish_artifact_paths", "mask_secret",
    ),
    "publish_mercadolibre": (
        "Callable", "_07D_MODE_HANDLERS", "_07d_all", "_07d_auth_link", "_07d_category_attrs",
        "_07d_image_upload", "_07d_payload_generate", "_07d_refresh_token", "_07d_user_info",
        "_is_mock_mercadolibre_category_id", "_local_path_from_image_item",
        "_mercadolibre_app_secret", "_mercadolibre_category_id_from_product",
        "_mercadolibre_image_candidates", "_mercadolibre_item_summary", "_mercadolibre_picture_id",
        "_mercadolibre_publish_result_error_map", "_mercadolibre_publish_result_ok",
        "_mercadolibre_required_attr_ids", "_mercadolibre_response_item_id",
        "_mercadolibre_site_item_errors", "_sanitize_for_log",
        "_source_pool_items", "_store_auth_result_fields", "auth_next_action",
        "image_pool_refs_for_platform",
        "load_product", "load_store_config", "mercadolibre_config_for_payload",
        "mercadolibre_picture_upload_error_message", "mercadolibre_product_for_payload",
        "mercadolibre_test_error_code",
        "preview_mercadolibre_auth_link", "refresh_mercadolibre_token_from_body",
        "save_store_config", "store_auth_failure_code", "summarize_store_auth_states",
    ),
    "publish_adapter": (
        "get_publishing_bus", "logger", "logging",
    ),
    "publish_runtime": (
        "MercadoLibrePublishingAdapter", "append_ml_auth_test_log", "append_ml_publish_log",
        "assign_upc", "build_mercadolibre_payload_preview", "compact_precheck",
        "compact_precheck_items", "compact_publish_failure_response",
        "ensure_mercadolibre_auth_ready", "ensure_mercadolibre_pictures_uploaded",
        "mercadolibre_close_remote_item", "mercadolibre_real_publish",
        "mercadolibre_remote_items", "resume_pending_publish_jobs", "run_mercadolibre_07d_test",
        "validate_mercadolibre_draft", "validate_ozon_draft", "validate_yandex_draft",
    ),
    "runtime_api": (
        "_draft_for_platform", "_field_error_map", "_write_publish_artifacts",
        "append_publish_log", "apply_precheck_to_product",
        "collect_time_iso", "html_page", "list_presets",
        "normalize_list", "normalize_product_fields", "platform_to_preset_key", "precheck_item",
        "publish_product", "safe_json_body", "save_product", "save_task_bundle",
        "write_json",
    ),
}

_EXPORTS: dict[str, str] = {
    name: module_name
    for module_name, names in _EXPORTS_BY_MODULE.items()
    for name in names
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(f"{__name__.rsplit('.', 1)[0]}.runtime_units.{module_name}")
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = sorted(name for name in _EXPORTS if not name.startswith("_"))
