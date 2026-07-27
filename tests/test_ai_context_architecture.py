from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def python_files(*folders: str) -> list[Path]:
    files: list[Path] = []
    for folder in folders:
        files.extend((ROOT / folder).glob("*.py"))
    return sorted(files)


def test_route_and_facade_layers_do_not_import_runtime_star() -> None:
    banned = "import *"
    for path in python_files("erp_web/http_route_units", "erp_web/facades"):
        text = path.read_text(encoding="utf-8")
        assert banned not in text, f"{path.relative_to(ROOT)} should use explicit imports"


def test_runtime_publish_and_collect_aggregators_use_explicit_exports() -> None:
    for path in [
        ROOT / "erp_web/runtime_units/publish_runtime.py",
        ROOT / "erp_web/runtime_units/source_collect.py",
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
        if path.name in {"__init__.py", "runtime_common.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "import *" not in text, f"{path.relative_to(ROOT)} should use explicit imports"


def test_image_pool_core_breaks_runtime_import_cycles() -> None:
    product_store_unit = (ROOT / "erp_web/runtime_units/product_store.py").read_text(encoding="utf-8")
    product_store_impl = (ROOT / "erp_web/stores/product_store.py").read_text(encoding="utf-8")
    image_pool = (ROOT / "erp_web/runtime_units/image_pool.py").read_text(encoding="utf-8")
    publish_mercadolibre = (ROOT / "erp_web/runtime_units/publish_mercadolibre.py").read_text(encoding="utf-8")

    assert "image_pool import" not in product_store_unit
    assert "from erp_web.runtime_units.image_pool import" not in product_store_impl
    assert "from .publish_mercadolibre import" not in image_pool
    assert "from erp_web.runtime_units.image_pool_core import" in product_store_impl
    assert "from .image_pool_core import" in publish_mercadolibre


def test_runtime_product_store_is_a_pure_delegation_layer() -> None:
    """runtime_units/product_store.py 只许留一行式委托：不得再有业务逻辑或直接 IO。"""
    text = (ROOT / "erp_web/runtime_units/product_store.py").read_text(encoding="utf-8")
    for marker in ("read_json", "write_json", "sqlite3", "open(", "Path(", "deepcopy", "json.load", "json.dump"):
        assert marker not in text, f"runtime_units/product_store.py should not contain {marker!r}"
    assert "get_context()" in text


def test_context_map_mentions_runtime_compatibility_boundary() -> None:
    text = (ROOT / "docs/ai-context-map.md").read_text(encoding="utf-8")
    assert "compatibility aggregator" in text
    assert "Do not add new `from erp_web.runtime import *`" in text


def test_context_map_mentions_product_research_entry_points() -> None:
    text = (ROOT / "docs/ai-context-map.md").read_text(encoding="utf-8")
    assert "erp_web/http_route_units/product_research_routes.py" in text
    assert "erp_web/product_research_config.py" in text
    assert "erp_web/services/product_research_service.py" in text
    assert "erp_web/schemas/product_research.py" in text


def test_ai_business_services_do_not_import_model_sdks_directly() -> None:
    allowed = {"ai_image_provider.py"}
    for path in python_files("erp_web/services"):
        if path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        assert "from openai import" not in text, f"{path.relative_to(ROOT)} should call an AI Provider"


def test_context_map_mentions_ai_provider_and_ai_work_entry_points() -> None:
    text = (ROOT / "docs/ai-context-map.md").read_text(encoding="utf-8")
    for entry_point in [
        "erp_web/services/ai_provider_contracts.py",
        "erp_web/services/ai_image_provider.py",
        "erp_web/services/ai_work_service.py",
        "erp_web/http_route_units/ai_work_routes.py",
        "front/src/views/AiWorkView.vue",
    ]:
        assert entry_point in text


def test_ai_gateway_stays_a_small_stable_facade() -> None:
    gateway = ROOT / "erp_web/services/ai_gateway.py"
    lines = gateway.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 800
    text = "\n".join(lines)
    for module in (
        "ai_gateway_parsing",
        "ai_gateway_probe",
        "ai_gateway_providers",
    ):
        assert module in text
    assert "AiProviderClient" in text


def test_ai_capability_probes_share_one_provider_loop() -> None:
    probe_text = (ROOT / "erp_web/services/ai_gateway_probe.py").read_text(
        encoding="utf-8"
    )
    provider_text = (
        ROOT / "erp_web/services/ai_gateway_providers.py"
    ).read_text(encoding="utf-8")
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
    assert len(facade.splitlines()) < 2400
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


def test_frontend_product_contract_reads_old_but_writes_version_one() -> None:
    normalizers = (
        ROOT / "front/src/api/workflow/normalizers.ts"
    ).read_text(encoding="utf-8")
    assert "export const PRODUCT_SCHEMA_VERSION = 1" in normalizers
    assert "const currentSchema =" in normalizers
    assert "schema_version: PRODUCT_SCHEMA_VERSION" in normalizers
    assert "\n    id: product.productId" not in normalizers
    assert "source_url: product.source.sourceUrl" in normalizers
