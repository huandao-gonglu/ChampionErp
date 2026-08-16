from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def python_files(*folders: str) -> list[Path]:
    files: list[Path] = []
    for folder in folders:
        files.extend((ROOT / folder).glob("*.py"))
    return sorted(files)


def imported_targets(paths: list[Path]) -> list[tuple[Path, str]]:
    targets: list[tuple[Path, str]] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets.extend((path, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module:
                    targets.append((path, module))
                targets.extend(
                    (path, f"{module}.{alias.name}" if module else alias.name)
                    for alias in node.names
                )
    return targets


def assigned_string_set(path: Path, variable_name: str) -> set[str]:
    """读取模块顶层由 frozenset/set/tuple/list 声明的字符串清单。"""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == variable_name
            for target in targets
        ):
            continue
        value = node.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in {"frozenset", "set", "tuple", "list"}
            and len(value.args) == 1
        ):
            value = value.args[0]
        assert isinstance(value, (ast.Set, ast.Tuple, ast.List)), (
            f"{path.relative_to(ROOT)}::{variable_name} 必须是显式静态清单"
        )
        assert all(
            isinstance(item, ast.Constant) and isinstance(item.value, str)
            for item in value.elts
        ), f"{path.relative_to(ROOT)}::{variable_name} 只能包含字符串字面量"
        return {str(item.value) for item in value.elts if isinstance(item, ast.Constant)}
    raise AssertionError(f"{path.relative_to(ROOT)} 缺少 {variable_name}")


def test_compatibility_runtime_entry_points_are_not_imported() -> None:
    production_paths = sorted((ROOT / "erp_web").rglob("*.py"))
    test_paths = sorted((ROOT / "tests").rglob("*.py"))
    runtime_offenders = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path, target in imported_targets(production_paths + test_paths)
        if target == "erp_web.runtime" or target.startswith("erp_web.runtime.")
    ]
    auth_alias_offenders = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path, target in imported_targets(production_paths + test_paths)
        if target == "erp_web.runtime_units.auth_runtime"
        or target.startswith("erp_web.runtime_units.auth_runtime.")
    ]
    assert not runtime_offenders, (
        "生产代码和测试必须导入真实 owner，不得依赖 erp_web.runtime：\n"
        + "\n".join(runtime_offenders)
    )
    assert not auth_alias_offenders, (
        "生产代码和测试不得依赖 auth_runtime 旧别名：\n"
        + "\n".join(auth_alias_offenders)
    )


def test_removed_runtime_compatibility_files_stay_removed() -> None:
    compatibility_paths = [
        ROOT / "erp_web/runtime.py",
        ROOT / "erp_web/runtime_units/auth_runtime.py",
        ROOT / "erp_web/runtime_units/browser_debug.py",
        ROOT / "erp_web/runtime_units/product_store.py",
        ROOT / "erp_web/runtime_units/publish_runtime.py",
        ROOT / "erp_web/runtime_units/runtime_common.py",
        ROOT / "erp_web/runtime_units/source_collect.py",
    ]
    existing = [str(path.relative_to(ROOT)) for path in compatibility_paths if path.exists()]
    assert not existing, f"旧 runtime 兼容入口仍存在：{existing}"

    runtime_units_init = (ROOT / "erp_web/runtime_units/__init__.py").read_text(
        encoding="utf-8"
    )
    assert "__getattr__" not in runtime_units_init
    assert "auth_runtime" not in runtime_units_init


def test_agents_guidance_points_to_current_architecture_owners() -> None:
    guidance = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "erp_web/runtime.py` is a runtime namespace aggregator" not in guidance
    assert "erp_web/runtime_units/product_store.py`:" not in guidance
    for current_owner in (
        "erp_web/stores/product_store.py",
        "erp_web/app_config.py",
        "erp_web/runtime_units/store_credentials.py",
        "erp_web/runtime_units/publish_workflows.py",
        "erp_web/services/ai_tool_declaration.py",
        "erp_web/services/ai_tool_compiler.py",
        "erp_web/services/ai_tool_catalog.py",
        "erp_web/services/ai_tool_runtime.py",
    ):
        assert current_owner in guidance
    assert "erp_web/services/ai_tool_annotation.py" not in guidance
    assert "validate_request_payload(..., endpoint=handler.path)" in guidance
    assert ".venv/bin/python -m pytest tests -q" in guidance
    assert "`POST /api/" not in guidance
    assert "`GET /api/" not in guidance


def test_historical_persistence_compatibility_stays_removed() -> None:
    db_text = (ROOT / "erp_web/db.py").read_text(encoding="utf-8")
    product_text = (
        ROOT / "erp_web/product_model/merge_model.py"
    ).read_text(encoding="utf-8")
    image_text = (
        ROOT / "erp_web/product_model/image_pool_model.py"
    ).read_text(encoding="utf-8")
    app_config_text = (
        ROOT / "erp_web/app_config.py"
    ).read_text(encoding="utf-8")
    context_text = (ROOT / "erp_web/context.py").read_text(encoding="utf-8")
    research_text = (
        ROOT / "erp_web/product_research_config.py"
    ).read_text(encoding="utf-8")

    for retired_db_symbol in (
        "_LEGACY_TABLES",
        "_migrate_v4_to_v5",
        "_recover_platform_drafts_v5",
        "platform_drafts_v4",
    ):
        assert retired_db_symbol not in db_text
    for retired_product_symbol in (
        "_LEGACY_PRODUCT_WRITE_FIELDS",
        "legacy_fallback",
        "image_pool_legacy_views",
    ):
        assert retired_product_symbol not in product_text
        assert retired_product_symbol not in image_text
    assert "migrate_legacy_ai_config" not in app_config_text
    assert "_apply_legacy_model_values" not in app_config_text
    assert "legacy_store_config_paths" not in context_text
    assert "legacy_app_config_paths" not in context_text
    assert "LEGACY_AI_SEARCH_METHOD_IDS" not in research_text


def test_route_and_facade_layers_do_not_import_runtime_star() -> None:
    banned = "import *"
    for path in python_files("erp_web/http_route_units", "erp_web/facades"):
        text = path.read_text(encoding="utf-8")
        assert banned not in text, f"{path.relative_to(ROOT)} should use explicit imports"


def test_http_routes_use_explicit_facades_and_validated_request_objects() -> None:
    for path in sorted((ROOT / "erp_web/http_route_units").glob("*_routes.py")):
        text = path.read_text(encoding="utf-8")
        assert "runtime_units" not in text, (
            f"{path.relative_to(ROOT)} should depend on a facade/service, "
            "not a business runtime unit"
        )
        tree = ast.parse(text)
        body_reads = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "read_body"
        ]
        validated_reads = [
            nested
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "validate_request_payload"
            and any(keyword.arg == "endpoint" for keyword in node.keywords)
            for nested in ast.walk(node)
            if isinstance(nested, ast.Call)
            and isinstance(nested.func, ast.Attribute)
            and nested.func.attr == "read_body"
        ]
        assert len(body_reads) == len(validated_reads), (
            f"{path.relative_to(ROOT)} 必须按 endpoint 校验每个请求体"
        )

    router = (ROOT / "erp_web/http_routes.py").read_text(encoding="utf-8")
    assert "from . import runtime" not in router
    assert "RequestValidationError" in router


def test_browser_debug_core_does_not_reverse_service_dependencies() -> None:
    browser_ai = (
        ROOT / "erp_web/services/browser_ai_runtime.py"
    ).read_text(encoding="utf-8")
    browser_core = (
        ROOT / "erp_web/services/browser_debug_service.py"
    ).read_text(encoding="utf-8")
    source_collect_browser = (
        ROOT / "erp_web/runtime_units/source_collect_browser.py"
    ).read_text(encoding="utf-8")
    assert "runtime_units" not in browser_ai
    assert "runtime_units" not in browser_core
    assert "from erp_web.services.browser_debug_service import" in source_collect_browser


def test_runtime_publish_and_collect_aggregators_use_explicit_exports() -> None:
    removed_aggregators = [
        ROOT / "erp_web/runtime_units/publish_runtime.py",
        ROOT / "erp_web/runtime_units/source_collect.py",
    ]
    assert all(not path.exists() for path in removed_aggregators)
    for path in [
        ROOT / "erp_web/runtime_units/source_collect_workflows.py",
        ROOT / "erp_web/runtime_units/source_sites.py",
        ROOT / "erp_web/runtime_units/publish_bus.py",
        ROOT / "erp_web/runtime_units/publish_helpers.py",
        ROOT / "erp_web/runtime_units/publish_logs_runtime.py",
        ROOT / "erp_web/runtime_units/publish_mercadolibre.py",
        ROOT / "erp_web/runtime_units/publish_validation.py",
        ROOT / "erp_web/runtime_units/publish_workflows.py",
    ]:
        text = path.read_text(encoding="utf-8")
        assert "import *" not in text, f"{path.relative_to(ROOT)} should list exported symbols explicitly"
        assert "__all__" in text, f"{path.relative_to(ROOT)} should document its public API"


def test_refactored_model_and_marketplace_units_do_not_use_wildcard_imports() -> None:
    for path in python_files("erp_web/product_model", "erp_web/marketplaces"):
        if path.name in {"__init__.py", "common.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "import *" not in text, f"{path.relative_to(ROOT)} should use explicit imports"


def test_refactored_runtime_units_do_not_use_wildcard_imports() -> None:
    for path in python_files("erp_web/runtime_units"):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "import *" not in text, f"{path.relative_to(ROOT)} should use explicit imports"


def test_image_pool_core_breaks_runtime_import_cycles() -> None:
    product_store_impl = (ROOT / "erp_web/stores/product_store.py").read_text(encoding="utf-8")
    image_pool = (ROOT / "erp_web/runtime_units/image_pool.py").read_text(encoding="utf-8")
    publish_mercadolibre = (ROOT / "erp_web/runtime_units/publish_mercadolibre.py").read_text(encoding="utf-8")

    assert not (ROOT / "erp_web/runtime_units/product_store.py").exists()
    assert "from erp_web.runtime_units.image_pool import" not in product_store_impl
    assert "from .publish_mercadolibre import" not in image_pool
    assert "from erp_web.runtime_units.image_pool_core import" in product_store_impl
    assert "from .image_pool_core import" in publish_mercadolibre


def test_publish_image_https_delivery_has_a_single_provider_boundary() -> None:
    service = ROOT / "erp_web/services/image_delivery_service.py"
    adapter = (ROOT / "erp_web/runtime_units/publish_adapter.py").read_text(
        encoding="utf-8"
    )
    ozon = (ROOT / "erp_web/runtime_units/publish_ozon.py").read_text(
        encoding="utf-8"
    )
    mercadolibre = (
        ROOT / "erp_web/runtime_units/publish_mercadolibre.py"
    ).read_text(encoding="utf-8")

    assert service.exists()
    assert "image_delivery.prepare_product" in adapter
    for platform_module in (ozon, mercadolibre):
        assert "ERP_IMAGE_HTTPS_" not in platform_module
        assert "LocalStaticHttpsProvider" not in platform_module


def test_runtime_product_store_is_a_pure_delegation_layer() -> None:
    """产品持久化只归 stores.ProductStore，不再保留 runtime 委托层。"""
    assert not (ROOT / "erp_web/runtime_units/product_store.py").exists()
    text = (ROOT / "erp_web/stores/product_store.py").read_text(encoding="utf-8")
    assert "class ProductStore" in text
    assert "get_context()" not in text


def test_product_research_entry_points_follow_layer_boundaries() -> None:
    route = ROOT / "erp_web/http_route_units/product_research_routes.py"
    facade = ROOT / "erp_web/facades/product_research_facade.py"
    service = ROOT / "erp_web/services/product_research_service.py"
    schema = ROOT / "erp_web/schemas/product_research.py"
    assert all(path.exists() for path in (route, facade, service, schema))

    route_targets = {
        target for _, target in imported_targets([route])
    }
    facade_targets = {
        target for _, target in imported_targets([facade])
    }
    service_targets = {
        target for _, target in imported_targets([service])
    }
    assert any(
        target.endswith("facades.product_research_facade")
        for target in route_targets
    )
    assert "erp_web.services.product_research_service" in facade_targets
    assert "erp_web.schemas.product_research" in service_targets
    assert not any("runtime_units" in target for target in route_targets)


def test_ai_business_services_do_not_import_model_sdks_directly() -> None:
    allowed = {"ai_pydantic_image_model.py"}
    for path in python_files("erp_web/services"):
        if path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        assert "from openai import" not in text, (
            f"{path.relative_to(ROOT)} 应通过集中 Pydantic Model 边界调用"
        )
        assert "import openai" not in text, (
            f"{path.relative_to(ROOT)} 应通过集中 Pydantic Model 边界调用"
        )


def test_ai_provider_selection_uses_catalog_contract() -> None:
    example = (ROOT / "config/app_config.example.json").read_text(encoding="utf-8")
    panel = (ROOT / "front/src/components/auth/AuthSettingsPanel.vue").read_text(
        encoding="utf-8"
    )

    assert '"provider_id"' in example
    assert '"provider_family"' not in example
    assert 'data-testid="ai-provider-id"' in panel
    assert 'data-testid="ai-provider-family"' not in panel


def test_ai_provider_and_ai_work_entry_points_are_explicit() -> None:
    entry_points = [
        ROOT / "erp_web/services/ai_provider_contracts.py",
        ROOT / "erp_web/services/ai_gateway.py",
        ROOT / "erp_web/services/ai_gateway_providers.py",
        ROOT / "erp_web/services/ai_gateway_cli_provider.py",
        ROOT / "erp_web/services/ai_gateway_browser_provider.py",
        ROOT / "erp_web/services/ai_gateway_provider_types.py",
        ROOT / "erp_web/services/ai_generation_settings.py",
        ROOT / "erp_web/services/ai_provider_catalog.py",
        ROOT / "erp_web/services/ai_model_factory.py",
        ROOT / "erp_web/services/ai_direct_request_service.py",
        ROOT / "erp_web/services/ai_model_discovery.py",
        ROOT / "erp_web/services/ai_model_errors.py",
        ROOT / "erp_web/services/ai_model_probe_service.py",
        ROOT / "erp_web/services/ai_pydantic_image_model.py",
        ROOT / "erp_web/services/ai_gateway_probe.py",
        ROOT / "erp_web/services/ai_agent_dependencies.py",
        ROOT / "erp_web/services/ai_agent_factory.py",
        ROOT / "erp_web/services/ai_agent_instrumentation.py",
        ROOT / "erp_web/services/ai_agent_state_store.py",
        ROOT / "erp_web/services/ai_tool_bridge.py",
        ROOT / "erp_web/services/category_attribute_fill_agent_service.py",
        ROOT / "erp_web/services/category_match_agent_service.py",
        ROOT / "erp_web/services/global_agent_service.py",
        ROOT / "erp_web/services/ai_gateway_provider_profiles.py",
        ROOT / "erp_web/services/ai_gateway_provider_prompting.py",
        ROOT / "erp_web/stores/pydantic_message_store.py",
        ROOT / "erp_web/http_route_units/ai_work_routes.py",
        ROOT / "front/src/views/AiWorkView.vue",
    ]
    missing = [
        str(path.relative_to(ROOT))
        for path in entry_points
        if not path.exists()
    ]
    assert not missing, f"AI 公开入口缺失：{missing}"

    for path in entry_points:
        if path.suffix != ".py":
            continue
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        if path.name.endswith(("_provider.py", "_providers.py")):
            assert any(
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == "__all__"
                    for target in (
                        node.targets
                        if isinstance(node, ast.Assign)
                        else [node.target]
                    )
                )
                for node in tree.body
            ), f"{path.relative_to(ROOT)} 必须显式声明公开导出"

    ai_route = (
        ROOT / "erp_web/http_route_units/ai_work_routes.py"
    ).read_text(encoding="utf-8")
    assert "get_context().pydantic_messages" in ai_route


def test_ai_work_has_one_pydantic_message_storage_contract() -> None:
    database = (ROOT / "erp_web/db.py").read_text(encoding="utf-8")
    store = (
        ROOT / "erp_web/stores/pydantic_message_store.py"
    ).read_text(encoding="utf-8")
    route = (
        ROOT / "erp_web/http_route_units/ai_work_routes.py"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS pydantic_message_histories" in database
    assert "messages_json BLOB NOT NULL" in database
    assert "ModelMessagesTypeAdapter.dump_json" in store
    assert "ModelMessagesTypeAdapter.validate_json" in store
    assert 'CONVERSATIONS_PATH = "/api/v1/ai-work/conversations"' in route
    storage_contract = database + store
    for retired in (
        "ai_" + "sessions",
        "AiWork" + "Journal",
        "AiWork" + "Event",
    ):
        assert retired not in storage_contract
    for retired_route_contract in (
        "after_" + "seq",
        "wait_" + "ms",
        '"child' + 'ren"',
        '"eve' + 'nts"',
        '"ra' + 'w"',
    ):
        assert retired_route_contract not in route


def test_context_map_mentions_shared_ai_tool_execution_entry_points() -> None:
    text = (ROOT / "docs/ai-context-map.md").read_text(encoding="utf-8")
    for entry_point in [
        "erp_web/schemas/ai_tools.py",
        "erp_web/schemas/ai_trace.py",
        "erp_web/services/ai_tool_declaration.py",
        "erp_web/services/ai_tool_compiler.py",
        "erp_web/services/ai_tool_catalog.py",
        "erp_web/services/ai_tool_registry.py",
        "erp_web/services/ai_tool_runtime.py",
        "erp_web/services/ai_provider_catalog.py",
        "erp_web/services/ai_model_factory.py",
        "erp_web/services/ai_direct_request_service.py",
        "erp_web/services/ai_model_discovery.py",
        "erp_web/services/ai_model_probe_service.py",
        "erp_web/services/ai_pydantic_image_model.py",
        "erp_web/services/ai_agent_dependencies.py",
        "erp_web/services/ai_agent_factory.py",
        "erp_web/services/ai_agent_instrumentation.py",
        "erp_web/stores/pydantic_message_store.py",
        "erp_web/stores/draft_query_snapshot_store.py",
        "erp_web/services/ai_agent_state_store.py",
        "erp_web/services/ai_tool_bridge.py",
        "erp_web/services/category_attribute_fill_agent_service.py",
        "erp_web/services/category_match_agent_service.py",
    ]:
        assert entry_point in text
    assert "erp_web/services/ai_tool_annotation.py" not in text


def test_agent_provider_errors_keep_provider_semantics() -> None:
    factory_text = (
        ROOT / "erp_web/services/ai_agent_factory.py"
    ).read_text(encoding="utf-8")
    context_map = (ROOT / "docs/ai-context-map.md").read_text(encoding="utf-8")

    assert '"MODEL_PROVIDER_ERROR"' not in factory_text
    assert "model_http_error_payload" in factory_text
    assert '"AI_PROVIDER_RESPONSE_INVALID"' in factory_text
    assert "不得改写为其他业务含义" in context_map


def test_pydantic_ai_types_stay_in_focused_runtime_boundaries() -> None:
    allowed = {
        ROOT / "erp_web/services/ai_provider_catalog.py",
        ROOT / "erp_web/services/ai_model_factory.py",
        ROOT / "erp_web/services/ai_direct_request_service.py",
        ROOT / "erp_web/services/ai_model_discovery.py",
        ROOT / "erp_web/services/ai_model_errors.py",
        ROOT / "erp_web/services/ai_model_probe_service.py",
        ROOT / "erp_web/services/ai_pydantic_image_model.py",
        ROOT / "erp_web/services/ai_gateway_probe.py",
        ROOT / "erp_web/services/ai_tool_bridge.py",
        ROOT / "erp_web/services/ai_agent_factory.py",
        ROOT / "erp_web/services/ai_agent_instrumentation.py",
        ROOT / "erp_web/services/ai_agent_state_store.py",
        ROOT / "erp_web/stores/pydantic_message_store.py",
        ROOT / "erp_web/services/category_attribute_fill_agent_service.py",
        ROOT / "erp_web/services/category_match_agent_service.py",
        ROOT / "erp_web/services/global_agent_service.py",
        ROOT / "erp_web/services/global_agent_chat_service.py",
        ROOT / "erp_web/services/vercel_ai_ui_service.py",
    }
    offenders = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path, target in imported_targets(sorted((ROOT / "erp_web").rglob("*.py")))
        if target.startswith("pydantic_ai") and path not in allowed
    ]
    assert not offenders, (
        "Pydantic AI 类型只能存在于集中 Agent/Model/Bridge/持久化边界：\n"
        + "\n".join(offenders)
    )


def test_pydantic_ui_protocol_has_single_owner() -> None:
    ui_owner = ROOT / "erp_web/services/vercel_ai_ui_service.py"
    offenders = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path, target in imported_targets(sorted((ROOT / "erp_web").rglob("*.py")))
        if target.startswith("pydantic_ai.ui") and path != ui_owner
    ]
    assert not offenders, (
        "Vercel UI 协议类型只能由 vercel_ai_ui_service 导入：\n"
        + "\n".join(offenders)
    )
    # 协议 service 不得装配 Agent，也不直接调用第二个 run loop。
    text = ui_owner.read_text(encoding="utf-8")
    assert "Agent(" not in text
    assert "run_stream_events(" not in text
    assert "run_sync(" not in text


def test_ai_chat_turn_claims_are_run_control_only() -> None:
    database = (ROOT / "erp_web/db.py").read_text(encoding="utf-8")
    marker = "CREATE TABLE IF NOT EXISTS ai_chat_turn_claims"
    assert marker in database
    ddl = database.split(marker, 1)[1].split(");", 1)[0]
    columns: set[str] = set()
    for line in ddl.splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith(("PRIMARY", "UNIQUE", "FOREIGN", "CONSTRAINT", "CHECK")):
            continue
        first = stripped.split()[0]
        if first in {"(", ")"}:
            continue
        columns.add(first)
    assert columns == {
        "claim_id",
        "conversation_id",
        "client_message_id",
        "profile_id",
        "actor_id",
        "tenant_id",
        "status",
        "claimed_at",
        "finished_at",
    }, f"ai_chat_turn_claims 只能保存运行控制元数据：{columns}"


def test_ai_chat_run_registry_is_context_singleton() -> None:
    context = (ROOT / "erp_web/context.py").read_text(encoding="utf-8")
    registry = (
        ROOT / "erp_web/services/ai_chat_run_registry.py"
    ).read_text(encoding="utf-8")
    assert "AiChatRunRegistry" in context
    assert "chat_runs" in context
    assert "class AiChatRunRegistry" in registry
    # registry 只是并发屏障，不持久化消息或业务状态。
    assert "sqlite" not in registry.lower()
    assert "message_store" not in registry


def test_no_ui_message_persistence_or_dual_write() -> None:
    database = (ROOT / "erp_web/db.py").read_text(encoding="utf-8")
    store = (
        ROOT / "erp_web/stores/pydantic_message_store.py"
    ).read_text(encoding="utf-8")
    assert "ui_messages" not in database.lower()
    assert "UIMessage" not in database
    assert "UIMessage" not in store
    # 唯一消息事实来源仍是 pydantic_message_histories.messages_json。
    assert "messages_json BLOB NOT NULL" in database


def test_sse_payload_uses_official_encoding_only() -> None:
    service = (
        ROOT / "erp_web/services/vercel_ai_ui_service.py"
    ).read_text(encoding="utf-8")
    route = (
        ROOT / "erp_web/http_route_units/ai_chat_routes.py"
    ).read_text(encoding="utf-8")
    # 项目代码不手写 Vercel chunk / SSE data 行。
    for text in (service, route):
        assert "data: " not in text
        assert '"type":"' not in text
    assert "encode_stream(" in service
    assert "transform_stream(" in service
    assert "write_sse_chunk" in route


def test_chat_entry_points_do_not_depend_on_global_task_orchestration() -> None:
    chat_modules = [
        ROOT / "erp_web/services/global_agent_chat_service.py",
        ROOT / "erp_web/services/vercel_ai_ui_service.py",
        ROOT / "erp_web/facades/ai_chat_facade.py",
        ROOT / "erp_web/services/global_chat_tools.py",
    ]
    banned = (
        "erp_web.services.global_task_controller",
        "erp_web.schemas.global_tasks",
        "erp_web.stores.global_task_store",
        "erp_web.runtime_units.global_task_tools",
    )
    for path in chat_modules:
        targets = {target for _, target in imported_targets([path])}
        offenders = {
            target
            for target in targets
            if any(target == item or target.startswith(item + ".") for item in banned)
        }
        assert not offenders, (
            f"{path.relative_to(ROOT)} 不得依赖 Global Task 编排：{offenders}"
        )
    business_service = chat_modules[0].read_text(encoding="utf-8")
    protocol_service = chat_modules[1].read_text(encoding="utf-8")
    chat_facade = chat_modules[2].read_text(encoding="utf-8")
    assert "build_global_chat_toolset(" in business_service
    assert "self.toolset" in business_service
    assert "AiToolSet" not in protocol_service
    assert "toolset=" not in protocol_service
    assert "context.global_tasks" not in chat_facade
    assert "context.draft_query_snapshots" in chat_facade


def test_draft_query_snapshot_store_is_independent_context_owner() -> None:
    context = (ROOT / "erp_web/context.py").read_text(encoding="utf-8")
    store = (
        ROOT / "erp_web/stores/draft_query_snapshot_store.py"
    ).read_text(encoding="utf-8")
    global_task_store = (
        ROOT / "erp_web/stores/global_task_store.py"
    ).read_text(encoding="utf-8")

    assert "def draft_query_snapshots(" in context
    assert "class DraftQuerySnapshotStore" in store
    assert "schemas.global_tasks" not in store
    assert "save_draft_query_snapshot(" in store
    assert "DraftQuerySnapshotStore(db)" in global_task_store


def test_pydantic_tool_bridge_can_only_execute_through_erp_runtime() -> None:
    bridge = ROOT / "erp_web/services/ai_tool_bridge.py"
    text = bridge.read_text(encoding="utf-8")

    assert "dependencies.tool_runtime.execute(" in text
    assert "binding.executor(" not in text
    for retired_path_symbol in (
        "AiTaskRunner",
        "JsonToolTurnProviderAdapter",
        "AiToolTurn",
        "AiToolTurnRequest",
        "_JSON_TOOL_PROTOCOL_SYSTEM",
        "protocol_version",
    ):
        assert retired_path_symbol not in text


def test_deferred_resume_has_durable_claim_checkpoint_and_result_replay() -> None:
    factory = (ROOT / "erp_web/services/ai_agent_factory.py").read_text(
        encoding="utf-8"
    )
    state_store = (ROOT / "erp_web/services/ai_agent_state_store.py").read_text(
        encoding="utf-8"
    )
    runtime = (ROOT / "erp_web/services/ai_tool_runtime.py").read_text(
        encoding="utf-8"
    )

    for marker in (
        "claim_for_resume(",
        "mark_tool_execution_started(",
        "mark_resume_ready(",
        "load_ready_for_replay(",
        "release_claim_for_retry(",
        '"in_doubt"',
    ):
        assert marker in state_store
    assert "before_executor=" in factory
    assert "mark_tool_execution_started(" in factory
    assert "mark_resume_ready(" in factory
    assert "load_ready_for_replay(" in factory
    assert 'definition.side_effect == "write"' in runtime


def test_retired_agent_runtime_and_json_tool_protocol_stay_removed() -> None:
    retired_files = (
        ROOT / "erp_web/services/ai_task_runner.py",
        ROOT / "erp_web/services/ai_tool_provider_adapters.py",
        ROOT / "tests/test_ai_task_runner.py",
    )
    assert all(not path.exists() for path in retired_files)

    retired_symbols = (
        "AiTaskRunner",
        "AiTaskExecutionError",
        "JsonToolTurnProviderAdapter",
        "_JSON_TOOL_PROTOCOL_SYSTEM",
        "AiToolTurnProvider",
        "AiToolTurnRequest",
        "CAPABILITY_TOOL_TURN",
        "AiToolTurn",
        "AiToolCall",
        "protocol_version",
        "tool_loop",
    )
    scanned = sorted((ROOT / "erp_web").rglob("*.py")) + [
        ROOT / "config/app_config.example.json",
        ROOT / "config/prompts/category_product_match.json",
        ROOT / "docs/ai-context-map.md",
        ROOT / "front/src/api/workflow/publishing.ts",
        ROOT / "front/src/types/workflow.ts",
    ]
    offenders = [
        f"{path.relative_to(ROOT)} -> {symbol}"
        for path in scanned
        for symbol in retired_symbols
        if symbol in path.read_text(encoding="utf-8")
    ]
    assert not offenders, "旧 Agent Runtime/JSON protocol 残留：\n" + "\n".join(offenders)


def test_agent_construction_has_one_production_owner() -> None:
    owner = ROOT / "erp_web/services/ai_agent_factory.py"
    offenders = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "erp_web").rglob("*.py"))
        if path != owner and "Agent(" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"Pydantic Agent 只能由 ai_agent_factory 装配：{offenders}"
    assert owner.read_text(encoding="utf-8").count("= Agent(") == 1


def test_category_facade_uses_only_the_focused_pydantic_agent_service() -> None:
    facade = ROOT / "erp_web/facades/category_match_facade.py"
    text = facade.read_text(encoding="utf-8")

    assert "run_category_match_agent" in text
    assert "agent_service(" in text
    assert "PydanticToolBridge" not in text
    assert "create_pydantic_model_binding" not in text
    assert "pydantic_ai" not in text
    assert "AiProviderClient" not in text


def test_provider_specific_generation_fields_have_one_owner() -> None:
    owner = ROOT / "erp_web/services/ai_generation_settings.py"
    markers = (
        "reasoning_effort",
        "enable_thinking",
        "thinking_budget",
        "max_completion_tokens",
    )
    owner_text = owner.read_text(encoding="utf-8")
    assert all(marker in owner_text for marker in markers)
    offenders: list[str] = []
    for path in sorted((ROOT / "erp_web").rglob("*.py")):
        if path == owner:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)} -> {marker}")
    assert not offenders, (
        "厂商生成字段只能由 ai_generation_settings 转换：\n"
        + "\n".join(offenders)
    )


def test_ai_tool_runtime_is_domain_agnostic_and_toolsets_are_explicit() -> None:
    services = ROOT / "erp_web/services"
    declaration = services / "ai_tool_declaration.py"
    compiler = services / "ai_tool_compiler.py"
    catalog = services / "ai_tool_catalog.py"
    registry = services / "ai_tool_registry.py"
    runtime = services / "ai_tool_runtime.py"
    core_paths = [declaration, compiler, catalog, registry, runtime]
    texts = {
        path: path.read_text(encoding="utf-8")
        for path in core_paths
    }

    banned_domain_roots = (
        "erp_web.facades",
        "erp_web.http_route_units",
        "erp_web.marketplaces",
        "erp_web.product_model",
        "erp_web.runtime_units",
        "erp_web.stores",
    )
    banned_domain_markers = (
        "category",
        "image_pool",
        "mercadolibre",
        "ozon",
        "product",
        "publish",
        "source_collect",
        "store_credentials",
    )
    domain_import_offenders = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path, target in imported_targets(core_paths)
        if target.startswith(banned_domain_roots)
        or any(marker in target.casefold() for marker in banned_domain_markers)
    ]
    assert not domain_import_offenders, (
        "AI Tool 基础设施不得反向依赖领域模块：\n"
        + "\n".join(domain_import_offenders)
    )

    banned_layer_dependencies = {
        declaration: (
            "ai_tool_compiler",
            "ai_tool_catalog",
            "ai_tool_registry",
            "ai_tool_runtime",
        ),
        compiler: ("ai_tool_catalog", "ai_tool_runtime"),
        catalog: ("ai_tool_declaration", "ai_tool_runtime"),
        registry: (
            "ai_tool_declaration",
            "ai_tool_compiler",
            "ai_tool_catalog",
            "ai_tool_runtime",
        ),
        runtime: (
            "ai_tool_declaration",
            "ai_tool_compiler",
            "ai_tool_catalog",
        ),
    }
    layer_import_offenders = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path, target in imported_targets(core_paths)
        if any(
            dependency in target
            for dependency in banned_layer_dependencies[path]
        )
    ]
    assert not layer_import_offenders, (
        "AI Tool 声明、编译、Catalog、Registry、Runtime 依赖方向错误：\n"
        + "\n".join(layer_import_offenders)
    )

    for path, text in texts.items():
        for dynamic_import_marker in ("importlib", "import_module", "__import__("):
            assert dynamic_import_marker not in text, (
                f"{path.relative_to(ROOT)} 不得扫描包或动态导入工具"
            )

    registry_text = texts[registry]
    catalog_text = texts[catalog]
    assert "MappingProxyType" in registry_text
    assert "class AiToolRegistry" not in registry_text
    assert "EMPTY_AI_TOOL_REGISTRY" not in registry_text
    assert "class AiToolCatalog" in catalog_text


def test_ai_tool_core_symbols_have_single_owners_and_retired_alias_stays_removed() -> None:
    services = ROOT / "erp_web/services"
    expected_owners = {
        "Injected": services / "ai_tool_declaration.py",
        "AiToolMetadata": services / "ai_tool_declaration.py",
        "ai_tool": services / "ai_tool_declaration.py",
        "get_ai_tool_metadata": services / "ai_tool_declaration.py",
        "CompiledAiTool": services / "ai_tool_compiler.py",
        "AiToolCompiler": services / "ai_tool_compiler.py",
        "AiToolBindingScope": services / "ai_tool_catalog.py",
        "AiToolCatalog": services / "ai_tool_catalog.py",
    }
    actual_owners: dict[str, list[Path]] = {
        symbol: [] for symbol in expected_owners
    }
    for path in sorted((ROOT / "erp_web").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in actual_owners:
                    actual_owners[node.name].append(path)
    for symbol, expected_owner in expected_owners.items():
        assert actual_owners[symbol] == [expected_owner], (
            f"{symbol} 必须只由 {expected_owner.relative_to(ROOT)} 定义，"
            f"实际为 {[str(path.relative_to(ROOT)) for path in actual_owners[symbol]]}"
        )

    retired_alias = services / "ai_tool_annotation.py"
    assert not retired_alias.exists(), "不得恢复第二套 @ai_tool 声明入口"
    annotation_imports = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path, target in imported_targets(
            sorted((ROOT / "erp_web").rglob("*.py"))
            + sorted((ROOT / "tests").rglob("*.py"))
        )
        if "ai_tool_annotation" in target
    ]
    assert not annotation_imports, (
        "生产代码和测试不得依赖已退役 ai_tool_annotation：\n"
        + "\n".join(annotation_imports)
    )


def test_pydantic_agent_is_the_only_tool_loop_owner() -> None:
    services = ROOT / "erp_web/services"
    provider_contract_text = (
        ROOT / "erp_web/services/ai_provider_contracts.py"
    ).read_text(encoding="utf-8")
    assert not (services / "ai_task_runner.py").exists()
    assert not (services / "ai_tool_provider_adapters.py").exists()
    assert "AiToolTurnProvider" not in provider_contract_text
    assert "AiToolTurnRequest" not in provider_contract_text
    assert "CAPABILITY_TOOL_TURN" not in provider_contract_text
    assert "start_conversation" not in provider_contract_text


def test_context_map_mentions_category_search_entry_points() -> None:
    text = (ROOT / "docs/ai-context-map.md").read_text(encoding="utf-8")
    for entry_point in [
        "erp_web/schemas/category.py",
        "erp_web/marketplaces/category_provider.py",
        "erp_web/runtime_units/category_searchers.py",
        "erp_web/runtime_units/category_providers.py",
        "erp_web/runtime_units/ozon_category_api.py",
        "tests/test_category_searchers.py",
        "tests/test_ozon_category_api.py",
    ]:
        assert entry_point in text
    assert "erp_web/runtime_units/category_retrieval.py" not in text


def test_category_match_vertical_slice_has_explicit_stable_boundaries() -> None:
    context_map = (ROOT / "docs/ai-context-map.md").read_text(encoding="utf-8")
    route = ROOT / "erp_web/http_route_units/category_routes.py"
    http_facade = ROOT / "erp_web/facades/category_facade.py"
    match_facade = ROOT / "erp_web/facades/category_match_facade.py"
    agent_service = ROOT / "erp_web/services/category_match_agent_service.py"
    category_tools = ROOT / "erp_web/runtime_units/category_tools.py"
    prompt = ROOT / "config/prompts/category_product_match.json"
    frontend_actions = ROOT / "front/src/stores/workflow/actions/publishing.ts"
    for path in (
        route,
        http_facade,
        match_facade,
        agent_service,
        category_tools,
        prompt,
        frontend_actions,
    ):
        assert path.exists(), f"PR3 入口缺失：{path.relative_to(ROOT)}"
        assert str(path.relative_to(ROOT)) in context_map

    route_text = route.read_text(encoding="utf-8")
    http_facade_text = http_facade.read_text(encoding="utf-8")
    match_text = match_facade.read_text(encoding="utf-8")
    agent_text = agent_service.read_text(encoding="utf-8")
    tool_text = category_tools.read_text(encoding="utf-8")
    model_config = (
        ROOT / "erp_web/services/ai_model_config.py"
    ).read_text(encoding="utf-8")
    frontend_text = frontend_actions.read_text(encoding="utf-8")

    # 同步 focused 入口：类型化结果由 /api/v1/category-match 独占；
    # 专用 run 协议（start/result routes）已删除，实时展示关联由
    # HTTP 公共边界的 presentation claim 完成。
    assert '"/api/category-match"' not in route_text
    assert '"/api/v1/category-match"' in route_text
    assert "handle_category_match" in route_text
    assert "handle_category_match_start" not in route_text
    assert "/runs" not in route_text
    assert "/api/category-ai-identify-product" not in route_text
    assert "/api/category-ai-suggest" not in route_text
    assert "load_category_match_subject" in http_facade_text
    assert "category_match_payload" in http_facade_text
    assert "run_sync(" not in agent_text
    assert "def match_category(" in match_text
    assert "run_category_match_agent" in match_text
    assert "AiAgentFactory" in agent_text
    assert "CategoryMatchOutputValidator" in agent_text
    assert "build_category_match_toolset(" in match_text
    assert "CATEGORY_SEARCH_TOOL_DEFINITIONS" in tool_text
    assert "side_effect=\"write\"" not in tool_text
    assert '"category.product_match"' in model_config
    for field in (
        '"toolset_id": "category.search"',
        '"budget_profile": "category.match.default"',
        '"result_schema": "category_match.v1"',
    ):
        assert field in model_config
    assert '"execution_mode"' not in model_config
    assert "matchCategory(" in frontend_text
    assert "identifyProductForCategory" not in frontend_text
    assert "isCategoryProductMatchEnabled" not in frontend_text


def test_category_attribute_fill_uses_agent_enum_tool_boundary() -> None:
    context_map = (ROOT / "docs/ai-context-map.md").read_text(encoding="utf-8")
    runtime = ROOT / "erp_web/runtime_units/category_attribute_ai_fill.py"
    tools = ROOT / "erp_web/runtime_units/category_attribute_tools.py"
    service = ROOT / "erp_web/services/category_attribute_fill_agent_service.py"
    prompt = ROOT / "config/prompts/category_attribute_fill.json"
    for path in (runtime, tools, service, prompt):
        assert path.exists()
        assert str(path.relative_to(ROOT)) in context_map

    runtime_text = runtime.read_text(encoding="utf-8")
    tool_text = tools.read_text(encoding="utf-8")
    service_text = service.read_text(encoding="utf-8")
    model_config = (
        ROOT / "erp_web/services/ai_model_config.py"
    ).read_text(encoding="utf-8")

    assert "run_category_attribute_fill_agent" in runtime_text
    assert "run_ai_use_case" not in runtime_text
    assert 'name=CATEGORY_ATTRIBUTE_VALUE_SEARCH_TOOL' in tool_text
    assert '"category_attribute_values_search"' in tool_text
    assert "AiToolCatalog.compile" in tool_text
    assert "AiToolDefinition(" not in tool_text
    assert "def search_executor(" not in tool_text
    assert "side_effect=\"write\"" not in tool_text
    assert "CategoryAttributeFillOutputValidator" in service_text
    assert "AiAgentFactory" in service_text
    for field in (
        '"toolset_id": "category.attribute_values"',
        '"budget_profile": "category.attribute_fill.default"',
        '"result_schema": "category_attribute_fill.v2"',
    ):
        assert field in model_config


def test_category_match_facade_is_the_only_owner_of_match_orchestration() -> None:
    match_facade = ROOT / "erp_web/facades/category_match_facade.py"
    tool_runtime = ROOT / "erp_web/services/ai_tool_runtime.py"
    category_tools = ROOT / "erp_web/runtime_units/category_tools.py"
    match_text = match_facade.read_text(encoding="utf-8")
    runtime_text = tool_runtime.read_text(encoding="utf-8")
    tool_text = category_tools.read_text(encoding="utf-8")

    assert "while True:" not in match_text
    assert "while True:" not in tool_text
    assert "category_" not in runtime_text.lower()
    assert "mercadolibre" not in runtime_text.lower()
    assert "ozon" not in runtime_text.lower()
    assert "fetch_category_record" not in tool_runtime.read_text(encoding="utf-8")


def test_category_search_uses_bound_polymorphism_and_normalized_shapes() -> None:
    searcher_text = (
        ROOT / "erp_web/runtime_units/category_searchers.py"
    ).read_text(encoding="utf-8")
    tool_text = (
        ROOT / "erp_web/runtime_units/category_tools.py"
    ).read_text(encoding="utf-8")
    schema_text = (
        ROOT / "erp_web/schemas/category.py"
    ).read_text(encoding="utf-8")
    provider_contract_text = (
        ROOT / "erp_web/marketplaces/category_provider.py"
    ).read_text(encoding="utf-8")
    assert not (ROOT / "erp_web/runtime_units/category_retrieval.py").exists()
    assert "class CategorySearcher(Protocol)" in provider_contract_text
    assert "def search_categories(self, keyword: str)" in provider_contract_text
    assert "class CategoryNavigator(Protocol)" in provider_contract_text
    assert "def browse_categories(self, parent_ids: list[str])" in provider_contract_text
    assert "FullTreeCategoryProvider" not in provider_contract_text
    assert "RemoteDiscoveryCategoryProvider" not in provider_contract_text
    assert "_CATEGORY_SEARCHER_FACTORIES" in searcher_text
    assert "isinstance(provider" not in searcher_text
    assert 'name="search_categories"' in tool_text
    assert 'name="browse_categories"' in tool_text
    assert '"platform":' not in tool_text
    assert '"site":' not in tool_text
    assert "retrieve_category_candidates" not in tool_text
    assert "get_category_detail" not in tool_text
    assert "get_category_attributes" not in tool_text
    assert "path_segments: list[str]" in schema_text
    assert "\n    id:" not in schema_text
    assert "\n    path:" not in schema_text
    assert "category_path:" not in schema_text


def test_ai_work_does_not_recreate_tool_or_event_projection() -> None:
    api_schema = (ROOT / "erp_web/schemas/ai_work.py").read_text(
        encoding="utf-8"
    )
    runtime = (ROOT / "erp_web/services/ai_tool_runtime.py").read_text(
        encoding="utf-8"
    )
    factory = (ROOT / "erp_web/services/ai_agent_factory.py").read_text(
        encoding="utf-8"
    )
    for retired in (
        "AiWork" + "Event",
        "AiWork" + "Recorder",
        "Conversation" + "AiWork" + "Recorder",
        "TOOL_CALL_" + "STARTED",
        "TOOL_CALL_" + "FINISHED",
        "agent." + "request",
        "agent." + "transcript",
    ):
        assert retired not in api_schema + runtime + factory


def test_ai_gateway_stays_a_small_stable_facade() -> None:
    gateway = ROOT / "erp_web/services/ai_gateway.py"
    text = gateway.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(gateway))
    assert not any(isinstance(node, ast.ClassDef) for node in tree.body)
    assert "__all__" in text
    for module in (
        "ai_gateway_parsing",
        "ai_gateway_probe",
        "ai_gateway_providers",
    ):
        assert module in text
    assert "AiProviderClient" in text


def test_ai_provider_implementations_stay_in_focused_modules() -> None:
    services = ROOT / "erp_web/services"
    facade = services / "ai_gateway_providers.py"
    modules = {
        "ai_gateway_cli_provider.py": {"CodexCliProvider"},
        "ai_gateway_browser_provider.py": {"BrowserAiProvider"},
    }

    facade_text = facade.read_text(encoding="utf-8")
    facade_tree = ast.parse(facade_text)
    facade_classes = {
        node.name for node in facade_tree.body if isinstance(node, ast.ClassDef)
    }
    assert facade_classes == {"AiProviderClient"}
    assert len(facade_text.splitlines()) < 500
    assert "AI_PROVIDER_REGISTRY" in facade_text

    for filename, expected_classes in modules.items():
        path = services / filename
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        classes = {
            node.name for node in tree.body if isinstance(node, ast.ClassDef)
        }
        assert expected_classes.issubset(classes)
        assert "__all__" in text
        assert "import *" not in text
        assert "ai_gateway_providers" not in text, (
            f"{filename} 不得反向依赖注册表门面，以免形成循环"
        )
        assert "ai_gateway_http_providers" not in text, (
            f"{filename} 不得依赖已退役 HTTP 实现"
        )

    for filename in (
        "ai_gateway_provider_types.py",
        "ai_gateway_provider_profiles.py",
        "ai_gateway_provider_prompting.py",
    ):
        text = (services / filename).read_text(encoding="utf-8")
        assert "__all__" in text
        assert "ai_gateway_providers" not in text


def test_ai_provider_modules_have_no_definition_shadowed_by_alias() -> None:
    """防止已拆出的实现再次被文件后部的同名赋值静默覆盖。"""

    provider_paths = [
        ROOT / "erp_web/services/ai_gateway_cli_provider.py",
        ROOT / "erp_web/services/ai_gateway_browser_provider.py",
        ROOT / "erp_web/services/ai_gateway_providers.py",
    ]
    shadowed: list[str] = []
    for path in provider_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        definitions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                definition = definitions.get(target.id)
                if definition and definition.end_lineno < node.lineno:
                    shadowed.append(
                        f"{path.name}:{definition.lineno} {target.id} -> {node.lineno}"
                    )
    assert not shadowed, "存在被后续同名别名覆盖的死实现：\n" + "\n".join(shadowed)


def test_all_capability_probes_share_one_provider_loop() -> None:
    probe_text = (ROOT / "erp_web/services/ai_gateway_probe.py").read_text(
        encoding="utf-8"
    )
    provider_paths = [
        ROOT / "erp_web/services/ai_gateway_cli_provider.py",
        ROOT / "erp_web/services/ai_gateway_browser_provider.py",
    ]
    provider_text = "\n".join(
        path.read_text(encoding="utf-8") for path in provider_paths
    )
    assert "def run_capability_probes(" in probe_text
    assert provider_text.count("probe_runtime.run_capability_probes(") == 2
    api_probe_text = (
        ROOT / "erp_web/services/ai_model_probe_service.py"
    ).read_text(encoding="utf-8")
    assert api_probe_text.count("ai_gateway_probe.run_capability_probes(") == 1
    assert "create_pydantic_probe_binding(" in api_probe_text
    assert "required_capabilities=[capability]" not in api_probe_text
    auth_panel_text = (
        ROOT / "front/src/components/auth/AuthSettingsPanel.vue"
    ).read_text(encoding="utf-8")
    assert "capabilities.add(capability)" not in auth_panel_text
    for provider in (
        "CodexCliProvider",
        "BrowserAiProvider",
    ):
        assert f"class {provider}" in provider_text
    assert provider_text.count("def probe_capability(") >= 2


def test_api_inference_has_one_pydantic_direct_boundary() -> None:
    services = ROOT / "erp_web/services"
    retired = (
        services / "ai_gateway_http_providers.py",
        services / "ai_image_provider.py",
    )
    assert all(not path.exists() for path in retired)

    direct_owner = services / "ai_direct_request_service.py"
    owner_text = direct_owner.read_text(encoding="utf-8")
    assert "direct.model_request(" in owner_text
    assert "direct.model_request_stream(" in owner_text
    assert "create_pydantic_model_binding(" in owner_text

    direct_call_offenders = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "erp_web").rglob("*.py"))
        if path != direct_owner
        and (
            "direct.model_request(" in path.read_text(encoding="utf-8")
            or "direct.model_request_stream(" in path.read_text(encoding="utf-8")
        )
    ]
    assert not direct_call_offenders, (
        "普通 API 推理只能由 ai_direct_request_service 执行："
        f"{direct_call_offenders}"
    )

    gateway = (services / "ai_gateway_providers.py").read_text(encoding="utf-8")
    assert "ai_direct_request_service.chat_json(" in gateway
    assert "ai_direct_request_service.generate_images(" in gateway
    assert "ai_direct_request_service.edit_images(" in gateway
    assert "OpenAICompatibleProvider" not in gateway
    assert "OpenAIResponsesProvider" not in gateway
    assert "OpenAIImageProvider" not in gateway

    model_config = (services / "ai_model_config.py").read_text(encoding="utf-8")
    capability_profile_block = model_config.split(
        "def normalize_capability_profiles(", 1
    )[1].split("\ndef normalize_connection_type(", 1)[0]
    assert 'profile["request_body"]' not in capability_profile_block
    generation = (services / "ai_generation_settings.py").read_text(
        encoding="utf-8"
    )
    assert "def apply_generation_settings(" not in generation


def test_ai_api_modules_do_not_build_raw_http_requests() -> None:
    for filename in (
        "ai_gateway.py",
        "ai_gateway_providers.py",
        "ai_direct_request_service.py",
        "ai_model_probe_service.py",
        "ai_pydantic_image_model.py",
    ):
        text = (ROOT / "erp_web/services" / filename).read_text(encoding="utf-8")
        assert "urllib.request" not in text
        assert "urlopen(" not in text
    probe_script = (ROOT / "scripts/test_ai_api.py").read_text(encoding="utf-8")
    assert "urllib.request" not in probe_script
    assert "def request_body(" not in probe_script
    assert "ai_direct_request_service.chat_json(" in probe_script


def test_state_contract_is_versioned_validated_and_redacted() -> None:
    route_text = (
        ROOT / "erp_web/http_route_units/get_routes.py"
    ).read_text(encoding="utf-8")
    schema_text = (ROOT / "erp_web/schemas/api.py").read_text(encoding="utf-8")
    assert '"schemaVersion": API_SCHEMA_VERSION' in route_text
    assert "validate_app_state_response(state)" in route_text
    assert "config_service.public_app_config" in route_text
    assert "config_service.public_store_config" in route_text
    assert "def validate_app_state_response(" in schema_text
    state_body = route_text.split("def handle_state(", 1)[1].split(
        "\ndef handle_products_index(", 1
    )[0]
    for legacy_bulk_field in (
        '"productsIndex"',
        '"draftsIndex"',
        '"publishLogs"',
        '"mercadolibreOrderNotifications"',
    ):
        assert legacy_bulk_field not in state_body


def test_frontend_workflow_has_one_real_route_and_no_fake_user_auth() -> None:
    router_text = (ROOT / "front/src/router/index.ts").read_text(encoding="utf-8")
    client_text = (ROOT / "front/src/api/client.ts").read_text(encoding="utf-8")
    assert router_text.count("component: workflowComponent") == 1
    assert "legacyWorkflowEntries" in router_text
    assert "name: 'WorkflowHome'" in router_text
    assert "accessToken" not in client_text
    assert "refreshToken" not in client_text
    assert "Authorization" not in client_text
    assert not (ROOT / "front/src/stores/auth.ts").exists()


def test_frontend_workflow_state_is_split_by_domain() -> None:
    facade = (ROOT / "front/src/stores/workflow.ts").read_text(encoding="utf-8")
    for domain_store in (
        "activity",
        "catalog",
        "collection",
        "publishing",
        "settings",
    ):
        path = ROOT / f"front/src/stores/workflow/{domain_store}.ts"
        assert path.exists()
        assert f"useWorkflow{domain_store.title()}Store" in path.read_text(encoding="utf-8")
    assert "async function loadAiConfig(" not in facade
    assert "async function refreshMercadoLibreRemoteItems(" not in facade


def test_frontend_workflow_action_factories_use_explicit_narrow_ports() -> None:
    for domain in ("collection", "catalog", "pricing", "publishing"):
        action_path = ROOT / f"front/src/stores/workflow/actions/{domain}.ts"
        action_text = action_path.read_text(encoding="utf-8")
        port_name = f"Workflow{domain.title()}ActionsPort"
        factory_name = f"createWorkflow{domain.title()}Actions"
        assert f"type {port_name} = Pick<" in action_text, (
            f"{action_path.relative_to(ROOT)} 必须显式声明依赖端口"
        )
        assert f"{factory_name}(runtime: {port_name})" in action_text, (
            f"{action_path.relative_to(ROOT)} 不得接收完整 WorkflowRuntime"
        )
        assert f"{factory_name}(runtime: WorkflowRuntime)" not in action_text


def test_frontend_product_contract_rejects_future_and_writes_current_version() -> None:
    generated = (
        ROOT / "front/src/types/workflow.generated.ts"
    ).read_text(encoding="utf-8")
    product_normalizer = (
        ROOT / "front/src/api/workflow/normalizers/product.ts"
    ).read_text(encoding="utf-8")
    assert "export const PRODUCT_SCHEMA_VERSION = 2 as const" in generated
    assert "assertCurrentProductWireSchema(record)" in product_normalizer
    assert "REMOVED_PRODUCT_FIELDS" in product_normalizer
    assert "const currentSchema =" not in product_normalizer
    assert "schema_version: PRODUCT_SCHEMA_VERSION" in product_normalizer
    assert "\n    id: product.productId" not in product_normalizer
    assert "source_url: product.source.sourceUrl" in product_normalizer
    backend_product = generated.split(
        "export interface BackendProduct {",
        1,
    )[1].split("\n}", 1)[0]
    for retired_field in (
        "id",
        "title",
        "source_url",
        "source_platform",
        "source_images",
        "source_image_urls",
        "category_id",
        "sale_price",
    ):
        assert f"  {retired_field}?:" not in backend_product


def test_publish_currency_contract_has_no_market_or_draft_fallback() -> None:
    ozon_publish = (ROOT / "erp_web/runtime_units/publish_ozon.py").read_text(encoding="utf-8")
    draft_schema = (ROOT / "erp_web/schemas/product.py").read_text(encoding="utf-8")
    registry = (ROOT / "erp_web/marketplace_registry.py").read_text(encoding="utf-8")

    assert 'or "RUB"' not in ozon_publish
    assert 'draft.get("currency")' not in ozon_publish
    assert "class PlatformDraft" in draft_schema
    platform_draft = draft_schema.split("class PlatformDraft", 1)[1].split("class Product", 1)[0]
    assert "\n    price:" not in platform_draft
    assert "\n    currency:" not in platform_draft
    assert '"market_currency": "RUB", "listing_currency": ""' in registry


def test_global_agent_static_capability_map_has_exactly_nine_entries() -> None:
    expected = {
        "drafts.query",
        "draft.prepare_for_market",
        "product.read",
        "category.match",
        "product.attributes.fill",
        "product.attributes.update",
        "product.images.prepare",
        "product.publish.validate",
        "product.publish.request",
    }
    facade = ROOT / "erp_web/facades/global_agent_facade.py"
    service = ROOT / "erp_web/services/global_agent_service.py"

    assert assigned_string_set(facade, "GLOBAL_TASK_CAPABILITY_NAMES") == expected
    assert assigned_string_set(service, "GLOBAL_TASK_PLAN_CAPABILITIES") == expected

    tree = ast.parse(facade.read_text(encoding="utf-8"), filename=str(facade))
    static_maps = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        if not all(
            isinstance(key, ast.Constant) and isinstance(key.value, str)
            for key in node.keys
        ):
            continue
        static_maps.append({str(key.value) for key in node.keys})
    assert expected in static_maps, "facade 必须显式绑定九项 Capability 函数"


def test_global_agent_http_and_controller_keep_narrow_boundaries() -> None:
    route = ROOT / "erp_web/http_route_units/global_agent_routes.py"
    controller = ROOT / "erp_web/services/global_task_controller.py"
    route_text = route.read_text(encoding="utf-8")
    controller_text = controller.read_text(encoding="utf-8")

    expected_paths = {
        "/api/global-task-start",
        "/api/global-task-state",
        "/api/global-task-input",
        "/api/global-task-publish-confirm",
        "/api/global-task-cancel",
    }
    route_tree = ast.parse(route_text, filename=str(route))
    handled_paths: set[str] = set()
    for node in ast.walk(route_tree):
        if isinstance(node, ast.Dict):
            values = {
                str(key.value)
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if values == expected_paths:
                handled_paths = values
                break
    assert handled_paths == expected_paths

    route_imports = {target for _, target in imported_targets([route])}
    assert any("facades.global_agent_facade" in target for target in route_imports)
    assert not any("runtime_units" in target for target in route_imports)

    controller_imports = {target for _, target in imported_targets([controller])}
    banned_import_parts = (
        "ai_tool_bridge",
        "ai_tool_catalog",
        "ai_tool_compiler",
        "ai_tool_registry",
        "ai_tool_runtime",
        "global_task_tools",
    )
    assert not any(
        part in target
        for target in controller_imports
        for part in banned_import_parts
    ), "Controller 只能直接调用静态 Capability，不得依赖 Tool executor"
    for banned_symbol in (
        "AiToolRuntime",
        "AiToolBinding",
        "build_global_task_planning_toolset",
        ".executor",
    ):
        assert banned_symbol not in controller_text

    wait_contract_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            route,
            ROOT / "erp_web/http_routes.py",
            ROOT / "erp_web/schemas/requests.py",
        )
    )
    for retired_wait_name in (
        "/api/global-task-wait",
        "global_task_wait",
        "waitGlobalTask",
    ):
        assert retired_wait_name not in wait_contract_text


def test_global_agent_planning_toolset_is_one_explicit_read_only_tool() -> None:
    tools_path = ROOT / "erp_web/runtime_units/global_task_tools.py"
    service_path = ROOT / "erp_web/services/global_agent_service.py"
    tool_text = tools_path.read_text(encoding="utf-8")
    service_text = service_path.read_text(encoding="utf-8")

    assert "GLOBAL_TASK_AI_TOOLS = (drafts_query,)" in tool_text
    assert "GLOBAL_TASK_TOOL_CATALOG = AiToolCatalog.compile(" in tool_text
    assert "allowed_tools=(DRAFTS_QUERY_TOOL,)" in tool_text
    assert 'side_effect="none"' in tool_text
    assert 'permission=GLOBAL_TASK_READ_PERMISSION' in tool_text
    assert 'side_effect="write"' not in tool_text
    assert "allow_write=False" in service_text
    assert "GLOBAL_TASK_PLAN_PERMISSION = \"global.task.read\"" in service_text
    for retired_fast_path_symbol in (
        "_try_fast_answer",
        "_is_active_draft_count_goal",
        "_is_draft_market_goal",
        "resolve_fresh_active_draft_count_answer",
    ):
        assert retired_fast_path_symbol not in service_text
    assert "query_drafts(" not in service_text, (
        "自然语言必须先由主 Agent 理解，并通过只读 drafts_query Tool 进入 "
        "AiToolRuntime；GlobalAgentService 不得直接查询草稿"
    )


def test_global_agent_market_runtime_uses_injection_without_facade_reverse_imports() -> None:
    runtime_paths = [
        ROOT / "erp_web/runtime_units/global_task_tools.py",
        ROOT / "erp_web/runtime_units/market_capability_support.py",
        ROOT / "erp_web/runtime_units/category_capabilities.py",
        ROOT / "erp_web/runtime_units/attribute_fill_capabilities.py",
        ROOT / "erp_web/runtime_units/market_pricing_capability.py",
        ROOT / "erp_web/runtime_units/market_prepare_capabilities.py",
        ROOT / "erp_web/runtime_units/product_capabilities.py",
        ROOT / "erp_web/runtime_units/publish_capabilities.py",
    ]
    reverse_imports = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path, target in imported_targets(runtime_paths)
        if target == "erp_web.facades" or target.startswith("erp_web.facades.")
    ]
    assert not reverse_imports, (
        "全局 Agent runtime unit 不得反向依赖 facade：\n"
        + "\n".join(reverse_imports)
    )

    category_text = (
        ROOT / "erp_web/runtime_units/category_capabilities.py"
    ).read_text(encoding="utf-8")
    market_text = (
        ROOT / "erp_web/runtime_units/market_prepare_capabilities.py"
    ).read_text(encoding="utf-8")
    facade_text = (
        ROOT / "erp_web/facades/global_agent_facade.py"
    ).read_text(encoding="utf-8")
    assert "matcher: CategoryMatcher" in category_text
    assert "category_capability: CategoryCapability | None = None" in market_text
    assert "matcher=run_category_match" in facade_text
    assert "category_capability=lambda request" in facade_text


def test_context_map_documents_global_agent_vertical_entry_points() -> None:
    context_map = (ROOT / "docs/ai-context-map.md").read_text(encoding="utf-8")
    required_entries = (
        "## 全局 Agent 顺序任务流",
        "erp_web/http_route_units/global_agent_routes.py",
        "erp_web/facades/global_agent_facade.py",
        "erp_web/services/global_task_controller.py",
        "erp_web/stores/global_task_store.py",
        "erp_web/services/global_agent_service.py",
        "erp_web/runtime_units/global_task_tools.py",
        "erp_web/runtime_units/market_capability_support.py",
        "erp_web/runtime_units/category_capabilities.py",
        "erp_web/runtime_units/attribute_fill_capabilities.py",
        "erp_web/runtime_units/market_pricing_capability.py",
        "erp_web/runtime_units/market_prepare_capabilities.py",
        "erp_web/stores/pydantic_message_store.py",
        "/api/global-task-start",
        "/api/global-task-state",
        "/api/global-task-input",
        "/api/global-task-publish-confirm",
        "/api/global-task-cancel",
        "/api/v1/ai-work/conversations",
    )
    missing = [entry for entry in required_entries if entry not in context_map]
    assert not missing, f"AI Context Map 缺少全局 Agent 入口：{missing}"
