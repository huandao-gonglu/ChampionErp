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


def test_desktop_only_composes_existing_server() -> None:
    entry = [ROOT / "erp_web/desktop.py"]
    targets = {target for _, target in imported_targets(entry)}
    assert "erp_web.server.main" in targets
    assert not any(target.startswith(("pydantic_ai", "erp_web.http_handler", "http.server")) for target in targets)


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
    assert calls.index("assert_credentials_migrated") < calls.index("mark_interrupted_publish_jobs")


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


def test_alibaba_business_facades_use_authorization_owner() -> None:
    for name in ("alibaba_self_purchase_facade", "order_procurement_facade", "order_notification_facade", "agent_capability_facade"):
        tree = parse_python(f"erp_web/facades/{name}.py")
        calls = [dotted_name(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)]
        assert "alibaba_runtime_config" in calls
        assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                       and node.func.attr == "get" and node.args
                       and isinstance(node.args[0], ast.Constant) and node.args[0].value == "1688_api"
                       for node in ast.walk(tree))
