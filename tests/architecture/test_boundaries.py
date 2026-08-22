from __future__ import annotations

import ast
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
        for path, target in imported_targets(
            python_files("erp_web/services")
        )
        if "runtime_units" in target
    ]
    assert not offenders, (
        "services 不得反向依赖 runtime_units：\n"
        + "\n".join(offenders)
    )


def test_facades_do_not_own_external_io() -> None:
    facades = python_files("erp_web/facades")
    import_offenders = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path, target in imported_targets(facades)
        if target.lstrip(".").split(".")[0]
        in {"requests", "urllib", "sqlite3"}
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
    assert not import_offenders, (
        "Facade 不得直接依赖网络或数据库模块：\n"
        + "\n".join(import_offenders)
    )
    assert not call_offenders, (
        "Facade 不得直接执行外部 IO：\n"
        + "\n".join(call_offenders)
    )


def test_root_compatibility_packages_stay_removed() -> None:
    retired_packages = (
        "routes",
        "services",
        "product_model_units",
        "marketplace_publish_units",
    )
    existing = [
        package
        for package in retired_packages
        if (ROOT / package).exists()
    ]
    assert not existing, (
        "不得在仓库根目录恢复包内实现的兼容镜像："
        f"{existing}"
    )


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


def test_production_global_task_controller_always_wires_deferred_links() -> None:
    """报告 R-07：生产装配的 GlobalTaskController 必须接线 Deferred ledger。

    无 link 任务的执行 fallback 已删除：接线 ledger 后，recovery 对无 link 的
    recoverable 任务只做隔离取消。若生产装配漏接 deferred_links，孤儿任务会
    重新获得执行路径，因此用 AST 检查固化该接线。
    """

    offenders: list[str] = []
    for path in python_files("erp_web"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if name != "GlobalTaskController":
                continue
            keywords = {keyword.arg for keyword in node.keywords}
            if "deferred_links" not in keywords:
                offenders.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}"
                )
    assert not offenders, (
        "生产代码装配 GlobalTaskController 必须显式接线 deferred_links，"
        "否则无 link 任务会绕过隔离：\n" + "\n".join(offenders)
    )
