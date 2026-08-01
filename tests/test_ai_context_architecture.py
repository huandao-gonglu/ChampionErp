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
        "erp_web/services/ai_tool_runtime.py",
    ):
        assert current_owner in guidance
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
    allowed = {"ai_image_provider.py"}
    for path in python_files("erp_web/services"):
        if path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        assert "from openai import" not in text, f"{path.relative_to(ROOT)} should call an AI Provider"


def test_ai_provider_and_ai_work_entry_points_are_explicit() -> None:
    entry_points = [
        ROOT / "erp_web/services/ai_provider_contracts.py",
        ROOT / "erp_web/services/ai_gateway.py",
        ROOT / "erp_web/services/ai_gateway_providers.py",
        ROOT / "erp_web/services/ai_gateway_http_providers.py",
        ROOT / "erp_web/services/ai_gateway_cli_provider.py",
        ROOT / "erp_web/services/ai_gateway_browser_provider.py",
        ROOT / "erp_web/services/ai_gateway_provider_types.py",
        ROOT / "erp_web/services/ai_gateway_provider_profiles.py",
        ROOT / "erp_web/services/ai_gateway_provider_prompting.py",
        ROOT / "erp_web/services/ai_image_provider.py",
        ROOT / "erp_web/services/ai_work_service.py",
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
    assert "get_context().ai_journal" in ai_route


def test_context_map_mentions_shared_ai_tool_execution_entry_points() -> None:
    text = (ROOT / "docs/ai-context-map.md").read_text(encoding="utf-8")
    for entry_point in [
        "erp_web/schemas/ai_tools.py",
        "erp_web/schemas/ai_trace.py",
        "erp_web/services/ai_invocation.py",
        "erp_web/services/ai_tool_registry.py",
        "erp_web/services/ai_tool_runtime.py",
        "erp_web/services/ai_task_runner.py",
        "erp_web/services/ai_tool_provider_adapters.py",
    ]:
        assert entry_point in text


def test_ai_tool_runtime_is_domain_agnostic_and_toolsets_are_explicit() -> None:
    runtime_text = (
        ROOT / "erp_web/services/ai_tool_runtime.py"
    ).read_text(encoding="utf-8")
    registry_text = (
        ROOT / "erp_web/services/ai_tool_registry.py"
    ).read_text(encoding="utf-8")
    for domain_marker in ("category_", "mercadolibre", "ozon_", "publish_"):
        assert domain_marker not in runtime_text.lower()
    assert "MappingProxyType" in registry_text
    assert "EMPTY_AI_TOOL_REGISTRY = AiToolRegistry({})" in registry_text
    assert "importlib" not in registry_text
    assert "import_module" not in registry_text


def test_ai_task_runner_owns_one_shared_tool_loop_and_provider_has_no_journal_factory() -> None:
    runner_text = (
        ROOT / "erp_web/services/ai_task_runner.py"
    ).read_text(encoding="utf-8")
    provider_contract_text = (
        ROOT / "erp_web/services/ai_provider_contracts.py"
    ).read_text(encoding="utf-8")
    adapter_text = (
        ROOT / "erp_web/services/ai_tool_provider_adapters.py"
    ).read_text(encoding="utf-8")
    assert runner_text.count("while True:") == 1
    assert "provider.run_tool_turn(" in runner_text
    assert "runtime.execute(call)" in runner_text
    assert "start_conversation" not in provider_contract_text
    assert "start_conversation" not in adapter_text


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
    category_tools = ROOT / "erp_web/runtime_units/category_tools.py"
    prompt = ROOT / "config/prompts/category_product_match.json"
    frontend_actions = ROOT / "front/src/stores/workflow/actions/publishing.ts"
    for path in (
        route,
        http_facade,
        match_facade,
        category_tools,
        prompt,
        frontend_actions,
    ):
        assert path.exists(), f"PR3 入口缺失：{path.relative_to(ROOT)}"
        assert str(path.relative_to(ROOT)) in context_map

    route_text = route.read_text(encoding="utf-8")
    http_facade_text = http_facade.read_text(encoding="utf-8")
    match_text = match_facade.read_text(encoding="utf-8")
    tool_text = category_tools.read_text(encoding="utf-8")
    model_config = (
        ROOT / "erp_web/services/ai_model_config.py"
    ).read_text(encoding="utf-8")
    frontend_text = frontend_actions.read_text(encoding="utf-8")

    assert '"/api/category-match": handle_category_match' in route_text
    assert "/api/category-ai-identify-product" not in route_text
    assert "/api/category-ai-suggest" not in route_text
    assert "category_match_payload" in http_facade_text
    assert "def match_category(" in match_text
    assert "AiTaskRunner(" in match_text
    assert "build_category_search_toolset(" in match_text
    assert "CATEGORY_SEARCH_TOOL_DEFINITIONS" in tool_text
    assert "side_effect=\"write\"" not in tool_text
    assert '"category.product_match"' in model_config
    for field in (
        '"execution_mode": "tool_loop"',
        '"toolset_id": "category.search"',
        '"budget_profile": "category.match.default"',
        '"result_schema": "category_match.v1"',
    ):
        assert field in model_config
    assert "matchCategory(" in frontend_text
    assert "identifyProductForCategory" not in frontend_text
    assert "isCategoryProductMatchEnabled" not in frontend_text


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
    assert "FullTreeCategoryProvider" not in provider_contract_text
    assert "RemoteDiscoveryCategoryProvider" not in provider_contract_text
    assert "_CATEGORY_SEARCHER_FACTORIES" in searcher_text
    assert "isinstance(provider" not in searcher_text
    assert 'name="search_categories"' in tool_text
    assert '"platform":' not in tool_text
    assert '"site":' not in tool_text
    assert "retrieve_category_candidates" not in tool_text
    assert "get_category_detail" not in tool_text
    assert "get_category_attributes" not in tool_text
    assert "path_segments: list[str]" in schema_text
    assert "\n    id:" not in schema_text
    assert "\n    path:" not in schema_text
    assert "category_path:" not in schema_text


def test_pr1_does_not_publish_future_ai_work_event_types() -> None:
    event_schema = (ROOT / "erp_web/schemas/ai_work.py").read_text(
        encoding="utf-8"
    )
    recorder = (ROOT / "erp_web/services/ai_invocation.py").read_text(
        encoding="utf-8"
    )
    for future_event_type in (
        '"TASK_STARTED"',
        '"MODEL_CALL_STARTED"',
        '"MODEL_CALL_FINISHED"',
        '"TOOL_CALL_STARTED"',
        '"TOOL_CALL_FINISHED"',
        '"TASK_FINISHED"',
        '"TASK_FAILED"',
    ):
        assert future_event_type not in event_schema
    assert "self.emit_custom(event_type, payload)" in recorder


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
        "ai_gateway_http_providers.py": {
            "OpenAICompatibleProvider",
            "OpenAIResponsesProvider",
        },
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
        if filename != "ai_gateway_http_providers.py":
            assert "ai_gateway_http_providers" not in text, (
                f"{filename} 不得为共享请求/配方反向依赖 HTTP 实现"
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
        ROOT / "erp_web/services/ai_gateway_http_providers.py",
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


def test_ai_capability_probes_share_one_provider_loop() -> None:
    probe_text = (ROOT / "erp_web/services/ai_gateway_probe.py").read_text(
        encoding="utf-8"
    )
    provider_paths = [
        ROOT / "erp_web/services/ai_gateway_http_providers.py",
        ROOT / "erp_web/services/ai_gateway_cli_provider.py",
        ROOT / "erp_web/services/ai_gateway_browser_provider.py",
    ]
    provider_text = "\n".join(
        path.read_text(encoding="utf-8") for path in provider_paths
    )
    assert "def run_capability_probes(" in probe_text
    assert provider_text.count("probe_runtime.run_capability_probes(") == 3
    for provider in (
        "OpenAICompatibleProvider",
        "OpenAIResponsesProvider",
        "CodexCliProvider",
        "BrowserAiProvider",
    ):
        assert f"class {provider}" in provider_text
    assert provider_text.count("def probe_capability(") >= 3


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


def test_frontend_product_contract_rejects_old_and_writes_version_one() -> None:
    generated = (
        ROOT / "front/src/types/workflow.generated.ts"
    ).read_text(encoding="utf-8")
    product_normalizer = (
        ROOT / "front/src/api/workflow/normalizers/product.ts"
    ).read_text(encoding="utf-8")
    assert "export const PRODUCT_SCHEMA_VERSION = 1 as const" in generated
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
