from __future__ import annotations

import subprocess
import sys

from erp_web.context import AppContext, AppPaths
from erp_web.db import ErpDatabase

from .support import (
    ROOT,
    forbidden_calls,
    imported_targets,
    python_files,
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
