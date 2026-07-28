from __future__ import annotations


# 设计审查报告共 48 个问题：一 12 项、二 10 项、三 12 项、四 A 9 项、四 B 5 项。
# 每项必须至少映射到一个可执行测试；一个问题需要跨层验证时可映射多个测试。
REPORT_ISSUE_TESTS: dict[str, tuple[str, ...]] = {
    "一-01": ("test_stateful_services.py::test_runtime_snapshot_injection_is_removed",),
    "一-02": ("test_stateful_services.py::test_publishing_bus_is_owned_by_app_context",),
    "一-03": ("test_stateful_services.py::test_erp_database_owns_schema_and_connection_policy",),
    "一-04": ("test_stateful_services.py::test_product_research_runs_are_registry_owned",),
    "一-05": ("test_stateful_services.py::test_app_paths_is_a_frozen_environment_value",),
    "一-06": ("test_stateful_services.py::test_category_store_has_no_sqlite_init_workaround",),
    "一-07": ("test_stateful_services.py::test_exchange_rate_cache_is_service_owned",),
    "一-08": ("test_stateful_services.py::test_ai_work_conditions_are_journal_owned_and_released",),
    "一-09": ("test_stateful_services.py::test_ai_provider_client_replaces_parameter_tunneling",),
    "一-10": ("test_stateful_services.py::test_product_and_config_stores_own_io",),
    "一-11": (
        "test_stateful_services.py::test_publish_logs_are_database_backed",
        "test_persistence_migrations.py::test_publish_logs_use_sqlite_as_single_index",
    ),
    "一-12": ("test_stateful_services.py::test_ozon_category_cache_uses_thread_safe_ttl_cache",),
    "二-01": ("test_platform_abstractions.py::test_publish_capabilities_have_real_platform_publishers",),
    "二-02": ("test_platform_abstractions.py::test_marketplace_registry_declares_capabilities",),
    "二-03": ("test_platform_abstractions.py::test_collection_sources_use_source_site_registry",),
    "二-04": ("test_platform_abstractions.py::test_ai_capability_probes_are_provider_polymorphic",),
    "二-05": ("test_platform_abstractions.py::test_chat_and_responses_protocol_logic_is_provider_owned",),
    "二-06": ("test_platform_abstractions.py::test_category_apis_use_category_provider_contract",),
    "二-07": ("test_platform_abstractions.py::test_business_ai_use_cases_share_one_executor",),
    "二-08": ("test_platform_abstractions.py::test_store_credentials_are_registry_driven",),
    "二-09": ("test_platform_abstractions.py::test_platform_field_mappings_are_registry_owned",),
    "二-10": (
        "test_platform_abstractions.py::test_facades_are_thin_adapters",
        "test_platform_abstractions.py::test_wildberries_is_registered_or_removed",
    ),
    "三-01": (
        "test_architecture_contracts.py::test_production_code_does_not_import_runtime_aggregator",
        "test_stateful_services.py::test_runtime_snapshot_injection_is_removed",
    ),
    "三-02": (
        "test_architecture_contracts.py::test_product_migration_reads_old_and_writes_only_schema_v1",
        "test_architecture_contracts.py::test_frontend_workflow_types_are_generated_from_schema",
    ),
    "三-03": (
        "test_architecture_contracts.py::test_services_do_not_import_runtime_units",
        "test_architecture_contracts.py::test_http_routes_do_not_import_business_runtime_units",
        "test_platform_abstractions.py::test_facades_are_thin_adapters",
    ),
    "三-04": (
        "test_architecture_contracts.py::test_state_endpoint_is_versioned_validated_and_redacted",
        "test_http_contracts.py::test_real_state_endpoint_is_small_versioned_and_redacted",
    ),
    "三-05": (
        "test_architecture_contracts.py::test_write_endpoints_use_runtime_schema_validation",
        "test_http_contracts.py::test_write_endpoint_rejects_invalid_json_objects",
    ),
    "三-06": (
        "test_architecture_contracts.py::test_frontend_workflow_modules_are_focused",
        "test_architecture_contracts.py::test_state_endpoint_is_not_a_god_payload",
        "test_http_contracts.py::test_real_state_endpoint_is_small_versioned_and_redacted",
    ),
    "三-07": ("test_platform_abstractions.py::test_unsupported_publish_paths_never_report_success_or_create_jobs",),
    "三-08": ("test_architecture_contracts.py::test_fake_app_auth_is_removed_and_store_credentials_are_named_clearly",),
    "三-09": ("test_architecture_contracts.py::test_workflow_has_one_real_route_and_query_tabs",),
    "三-10": ("test_architecture_contracts.py::test_publish_queue_has_real_required_attribute_gate",),
    "三-11": (
        "test_architecture_contracts.py::test_app_config_updates_are_whitelisted",
        "test_http_contracts.py::test_real_settings_endpoint_ignores_unknown_top_level_fields",
    ),
    "三-12": ("test_architecture_contracts.py::test_dead_root_packages_are_deleted_and_guarded",),
    "四A-01": (
        "test_persistence_migrations.py::test_required_persistence_tables_exist",
        "test_persistence_migrations.py::test_upc_assignment_is_atomic_under_concurrency",
        "test_persistence_migrations.py::test_upc_assignment_public_flow_is_database_backed_and_survives_restart",
    ),
    "四A-02": ("test_persistence_migrations.py::test_order_notifications_are_append_only_database_rows",),
    "四A-03": ("test_persistence_migrations.py::test_legacy_store_credentials_are_migrated_and_scrubbed",),
    "四A-04": ("test_persistence_migrations.py::test_publish_logs_use_sqlite_as_single_index",),
    "四A-05": ("test_persistence_migrations.py::test_publish_jobs_never_persist_credentials",),
    "四A-06": ("test_persistence_migrations.py::test_product_research_results_survive_registry_restart",),
    "四A-07": ("test_persistence_migrations.py::test_ai_work_metadata_is_database_indexed",),
    "四A-08": ("test_persistence_migrations.py::test_app_config_never_persists_runtime_secrets",),
    "四A-09": ("test_persistence_migrations.py::test_exchange_rates_survive_service_restart",),
    "四B-01": ("test_persistence_migrations.py::test_publish_logs_are_not_embedded_in_product_or_draft_json",),
    "四B-02": ("test_persistence_migrations.py::test_platform_drafts_have_one_source_of_truth",),
    "四B-03": ("test_persistence_migrations.py::test_draft_id_aliases_are_removed",),
    "四B-04": ("test_persistence_migrations.py::test_dead_category_cache_storage_is_removed",),
    "四B-05": ("test_persistence_migrations.py::test_only_one_database_location_exists",),
}


EXPECTED_REPORT_ISSUES = {
    *(f"一-{index:02d}" for index in range(1, 13)),
    *(f"二-{index:02d}" for index in range(1, 11)),
    *(f"三-{index:02d}" for index in range(1, 13)),
    *(f"四A-{index:02d}" for index in range(1, 10)),
    *(f"四B-{index:02d}" for index in range(1, 6)),
}
