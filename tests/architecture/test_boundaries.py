from __future__ import annotations

import subprocess
import sys
import ast

from erp_web.context import AppContext, AppPaths
from erp_web.db import ErpDatabase

from .support import (
    ROOT,
    forbidden_calls,
    imported_targets,
    python_files,
    dotted_name,
    parse_python,
)


def test_services_do_not_reverse_depend_on_runtime_units() -> None:
    offenders = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path, target in imported_targets(python_files("erp_web/services"))
        if "runtime_units" in target
    ]
    assert not offenders, "services 不得反向依赖 runtime_units：\n" + "\n".join(
        offenders
    )



def test_integrations_do_not_persist_credentials_in_business_stores() -> None:
    bus = parse_python("erp_web/services/crossborderbus_client.py")
    notification = parse_python("erp_web/services/order_notification_service.py")
    for tree in (bus, notification):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and dotted_name(node.func) in {"self.store.setting", "self.store.set_setting"}:
                assert not any(isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                               and (argument.value in {"credentials", "public_url"} or argument.value.startswith("token:"))
                               for argument in ast.walk(node))
    startup = parse_python("erp_web/server.py")
    main = next(node for node in startup.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    calls = [dotted_name(node.func) for statement in main.body for node in ast.walk(statement) if isinstance(node, ast.Call)]
    assert calls.index("assert_credentials_migrated") < calls.index("accounts.start")


def test_bus_user_authorization_does_not_accept_developer_secrets() -> None:
    from erp_web.schemas.fulfillment import BusCredentials
    from erp_web.schemas.requests import REQUEST_CONTRACTS

    assert set(BusCredentials.model_fields) == {"user_name", "password"}
    assert BusCredentials.model_config["extra"] == "forbid"
    assert set(REQUEST_CONTRACTS["/api/crossborderbus/authorize"].fields) == {"user_name", "password"}


def test_task_confirmation_does_not_own_platform_writes_or_agent_execution() -> None:
    files = [ROOT / "erp_web/services/task_queue_service.py", ROOT / "erp_web/stores/task_confirmation_store.py"]
    targets = {target for _, target in imported_targets(files)}
    assert not any(target.startswith(("pydantic_ai", "erp_web.marketplaces", "erp_web.runtime_units")) for target in targets)
    assert not forbidden_calls(files, {"enqueue", "change", "write", "publish", "publish_payload", "run_sync", "save_product", "save_draft_content"})


def test_release_coordination_is_independent_of_business_job_statuses() -> None:
    files = [ROOT / 'erp_web/release_guard.py', ROOT / 'deploy/release_server.py']
    targets = {target for _, target in imported_targets(files)}
    assert not any(target.startswith(('erp_web.stores', 'erp_web.runtime_units', 'erp_web.services', 'sqlite3'))
                   for target in targets)
    confirmation = parse_python('erp_web/services/task_queue_service.py')
    calls = {dotted_name(node.func) for node in ast.walk(confirmation) if isinstance(node, ast.Call)}
    assert not calls & {'release_guard.submit', 'release_guard.start_thread', 'release_guard.admit'}


def test_public_confirmation_entries_cannot_bypass_the_task_queue() -> None:
    for path, name in (
        ("erp_web/runtime_units/publishing_bus_core.py", "check_publish_result"),
        ("erp_web/services/online_product_service.py", "reconcile"),
    ):
        method = next(node for node in ast.walk(parse_python(path)) if isinstance(node, ast.FunctionDef) and node.name == name)
        calls = {dotted_name(node.func) for node in ast.walk(method) if isinstance(node, ast.Call)}
        assert calls & {"self.task_queue.check", "self.context.task_queue.check"}
        assert not calls & {"self.confirmation.check", "self.reconcile_once"}


def test_publish_startup_does_not_submit_remote_work() -> None:
    tree = parse_python("erp_web/runtime_units/publishing_bus_core.py")
    bus = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PublishingBus")
    for name in ("__init__", "mark_interrupted_jobs"):
        method = next(node for node in bus.body if isinstance(node, ast.FunctionDef) and node.name == name)
        calls = {dotted_name(node.func) for node in ast.walk(method) if isinstance(node, ast.Call)}
        assert not calls & {"self._submit_job", "self.executor.submit", "self._run_platform", "self._run_platform_once"}
    assert not any(isinstance(node, ast.FunctionDef) and node.name in {"recover_pending_jobs", "_resume_state"} for node in bus.body)


def test_watermark_rendering_has_no_network_or_application_state_dependency() -> None:
    files = [ROOT / "erp_web/services/watermark_service.py", ROOT / "erp_web/services/watermark_preview_service.py"]
    targets = {target for _, target in imported_targets(files)}
    assert not any(target.split('.')[0] in {'requests', 'urllib', 'httpx', 'botocore', 'sqlite3'} for target in targets)
    assert not any(target.startswith(('erp_web.context', 'erp_web.stores', 'erp_web.runtime_units')) for target in targets)
    assert not forbidden_calls(files, {'write_json', 'write_bytes', 'write_text', 'unlink', 'save_product', 'save_draft_content'})


def test_facades_do_not_own_external_io() -> None:
    facades = python_files("erp_web/facades")
    import_offenders = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path, target in imported_targets(facades)
        if target.lstrip(".").split(".")[0] in {"requests", "urllib", "sqlite3"}
    ]
    call_offenders = forbidden_calls(
        facades,
        {
            "open",
            "write_json",
            "write_text",
            "write_bytes",
            "Path.write_text",
            "requests.get",
            "requests.post",
            "urlopen",
            "urllib.request.urlopen",
            "sqlite3.connect",
        },
    )
    assert not import_offenders, "Facade 不得直接依赖网络或数据库模块：\n" + "\n".join(
        import_offenders
    )
    assert not call_offenders, "Facade 不得直接执行外部 IO：\n" + "\n".join(
        call_offenders
    )


def test_root_compatibility_packages_stay_removed() -> None:
    retired_packages = (
        "routes",
        "services",
        "product_model_units",
        "marketplace_publish_units",
    )
    existing = [package for package in retired_packages if (ROOT / package).exists()]
    assert not existing, f"不得在仓库根目录恢复包内实现的兼容镜像：{existing}"


def test_frontend_workflow_types_match_backend_schema() -> None:
    generator = ROOT / "scripts/generate_frontend_types.py"
    result = subprocess.run(
        [sys.executable, str(generator), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        "前端 workflow 类型与后端 schema 不一致；"
        "请运行类型生成脚本。\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_app_context_owns_stateful_services(tmp_path) -> None:
    first = AppContext(
        AppPaths.from_app_dir(tmp_path / "first"),
        ErpDatabase(tmp_path / "first/erp.sqlite3"),
    )
    second = AppContext(
        AppPaths.from_app_dir(tmp_path / "second"),
        ErpDatabase(tmp_path / "second/erp.sqlite3"),
    )
    first_bus = first.publishing_bus
    second_bus = second.publishing_bus
    try:
        assert first.products._db is first.db
        assert first.config._db is first.db
        assert first.research._db is first.db
        assert first.exchange_rates._db is first.db
        assert first_bus.store is first.db
        assert second_bus.store is second.db
        assert first_bus is not second_bus
    finally:
        first.close()
        second.close()


def test_retired_agent_engine_and_endpoints_are_absent() -> None:
    for relative in (
        "erp_web/services/global_task_controller.py",
        "erp_web/services/global_task_continuation_service.py",
        "erp_web/runtime_units/global_ai_control_tools.py",
        "erp_web/stores/pydantic_deferred_task_link_store.py",
        "erp_web/stores/pydantic_ai_event_outbox_store.py",
        "erp_web/http_route_units/global_agent_routes.py",
        "front/src/components/ai-work/GlobalTaskApprovalCard.vue",
    ):
        assert not (ROOT / relative).exists(), relative
    for path in python_files("erp_web"):
        source = path.read_text()
        assert "GlobalTaskController" not in source
        assert "global_task_start" not in source


def test_category_value_limits_have_one_pure_owner_for_yandex_writes() -> None:
    path = ROOT / 'erp_web/schemas/category_constraints.py'
    targets = {target for _, target in imported_targets([path])}
    assert not any(target.startswith(('erp_web.context', 'erp_web.stores', 'erp_web.runtime_units', 'erp_web.services')) for target in targets)
    for consumer in ('runtime_units/publish_yandex.py', 'runtime_units/category_attribute_updates.py', 'services/online_product_attributes.py'):
        source = (ROOT / 'erp_web' / consumer).read_text()
        assert 'from erp_web.schemas.category_constraints import category_attribute_constraint_error' in source
        assert 'category_attribute_constraint_error(' in source
    assert 'constraints.get("maxLength")' not in (ROOT / 'erp_web/services/online_product_attributes.py').read_text()


def test_ogl_calculation_has_no_network_store_or_runtime_dependency() -> None:
    files = [ROOT / 'erp_web/international_shipping/ogl.py']
    imports = {target for _, target in imported_targets(files)}
    assert not any(target.startswith(('erp_web.context', 'erp_web.stores', 'erp_web.runtime_units', 'urllib', 'requests', 'httpx', 'sqlite3', 'pathlib')) for target in imports)
    assert not forbidden_calls(files, {'open', 'read_text', 'write_text', 'urlopen'})
    retired = (ROOT / 'erp_web/international_shipping/platform_api.py').read_text()
    assert '/v2/delivery-method/list' not in retired
    assert '/v2/warehouse/list' not in retired


def test_alibaba_business_entries_use_authorization_owner() -> None:
    for path in (
        "erp_web/facades/alibaba_self_purchase_facade.py",
        "erp_web/facades/order_procurement_facade.py",
        "erp_web/facades/order_notification_facade.py",
        "erp_web/runtime_units/source_collect_1688_api.py",
    ):
        tree = parse_python(path)
        calls = [dotted_name(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)]
        assert "alibaba_runtime_config" in calls
        assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                       and node.func.attr == "get" and node.args
                       and isinstance(node.args[0], ast.Constant) and node.args[0].value == "1688_api"
                       for node in ast.walk(tree))


def test_onebound_transport_and_authorization_keep_existing_owners():
    """万邦只适配网关，不持有业务状态或另建下单、支付生命周期。"""
    from erp_web.schemas.alibaba_authorization import OneBoundAuthorizationRequest
    from erp_web.schemas.requests import REQUEST_CONTRACTS
    from erp_web.ai_capability_coverage import AI_CAPABILITY_COVERAGE_MANIFEST

    path = ROOT / 'erp_web/services/onebound_api_transport.py'
    imports = {target for _, target in imported_targets([path])}
    assert not any(target.startswith(('erp_web.stores', 'erp_web.runtime_units', 'sqlite3')) for target in imports)
    assert not forbidden_calls([path], {'urlopen', 'save_product', 'claim_payment', 'claim'})
    calls = {dotted_name(node.func) for node in ast.walk(parse_python(str(path))) if isinstance(node, ast.Call)}
    assert 'managed_urlopen' in calls
    endpoint = '/api/alibaba/authorization/onebound'
    assert set(REQUEST_CONTRACTS[endpoint].fields) == {'session'}
    assert set(OneBoundAuthorizationRequest.model_fields) == {'session'}
    assert OneBoundAuthorizationRequest.model_config['extra'] == 'forbid'
    entries = [row for row in AI_CAPABILITY_COVERAGE_MANIFEST if row.path == endpoint]
    assert len(entries) == 1 and entries[0].disposition == 'excluded'


def test_desktop_runtime_has_no_business_initialization_or_backend_packaging():
    """执行器仅依赖共享纯模块；后端也不能重新导入本机实现。"""
    paths = [*python_files('desktop_runtime'), *python_files('erp_shared')]
    targets = {target for _, target in imported_targets(paths)}
    assert not any(target.startswith(('erp_web', 'sqlite3', 'pydantic_ai', 'openai')) for target in targets)
    assert not forbidden_calls(paths, {'get_context', 'build_default_context', 'ErpDatabase', 'save_product', 'save_draft_content'})
    targets = {target for _, target in imported_targets(python_files('erp_web'))}
    assert not any(target.startswith('desktop_runtime') for target in targets)
    for retired in ('erp_web/desktop.py', 'scripts/desktop_backend.spec', 'scripts/desktop_backend_entry.py',
                    'scripts/build_desktop_backend.py', 'front/desktop/backend.cjs'):
        assert not (ROOT / retired).exists()
    import json
    package = json.loads((ROOT / 'front/package.json').read_text())
    assert all('backend' not in resource['to'] for resource in package['build']['extraResources'])
    assert 'collect_submodules("erp_web")' not in (ROOT / 'scripts/desktop_runtime.spec').read_text()


def test_desktop_transport_routes_use_contracts_and_do_not_become_ai_tools():
    from erp_web.http_route_units.desktop_routes import HANDLED_PATHS
    from erp_web.schemas.requests import REQUEST_CONTRACTS
    from erp_web.ai_capability_coverage import AI_CAPABILITY_COVERAGE_MANIFEST
    assert HANDLED_PATHS <= REQUEST_CONTRACTS.keys()
    entries = [entry for entry in AI_CAPABILITY_COVERAGE_MANIFEST if entry.path in HANDLED_PATHS]
    assert {entry.path for entry in entries} == HANDLED_PATHS
    assert all(entry.disposition == 'excluded' for entry in entries)
