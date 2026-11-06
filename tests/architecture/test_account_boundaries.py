"""个人身份不进入模型工具，并发业务不能切换进程默认空间。"""
import ast

from .support import ROOT, imported_targets, python_files


def test_account_modules_do_not_depend_on_agent_execution():
    files = [ROOT / f"erp_web/{name}" for name in (
        "schemas/accounts.py", "stores/account_store.py", "services/account_service.py", "facades/account_facade.py",
    )]
    offenders = [(str(path.relative_to(ROOT)), target) for path, target in imported_targets(files)
                 if "pydantic_ai" in target or "ai_tool" in target or "ai_agent" in target]
    assert not offenders, f"账号与模型执行必须分离：{offenders}"


def test_only_startup_installs_the_process_default_context():
    offenders = []
    for path in python_files("erp_web"):
        if path.name in {"context.py", "server.py"}:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "erp_web.context":
                if any(item.name in {"set_context", "clear_context"} for item in node.names):
                    offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"请求和后台执行须通过 bind_context 绑定个人空间：{offenders}"
