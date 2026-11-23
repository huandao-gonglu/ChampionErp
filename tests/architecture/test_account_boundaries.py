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


def test_shared_settings_owner_is_only_bound_by_composition_root():
    offenders = []
    for path in python_files("erp_web"):
        if path.name in {"account_runtime.py", "context.py", "config_store.py"}:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr in {"shared_config", "shared_paths"}:
                        offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"共享配置归属只能由组合根绑定，业务请求不得切换共享空间：{offenders}"


def test_alibaba_provider_resolution_goes_through_authorization_service():
    """1688 方案选择归授权服务按账号空间解析，其他模块不得绕过角色判断直接读取。"""
    allowed_config_readers = {"config_store.py", "alibaba_authorization_service.py"}
    config_offenders = []
    no_arg_offenders = []
    direct_provider_offenders = []
    for path in python_files("erp_web"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"alibaba_application", "alibaba_api_application"}:
                if path.name not in allowed_config_readers:
                    config_offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.func.attr}")
                elif path.name == "alibaba_authorization_service.py" and node.func.attr == "alibaba_application":
                    if not node.args and not node.keywords:
                        no_arg_offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            if isinstance(node.func, ast.Name) and node.func.id in {"SelfUseAlibabaProvider", "OfficialAlibabaProvider"}:
                if path.name != "alibaba_api_client.py":
                    direct_provider_offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.func.id}")
    assert not config_offenders, f"不得绕过授权服务直接读取全局 1688 方案：{config_offenders}"
    assert not no_arg_offenders, f"授权服务必须显式传入已按角色解析的 provider：{no_arg_offenders}"
    assert not direct_provider_offenders, f"官方 1688 客户端只能经 alibaba_provider 工厂创建：{direct_provider_offenders}"
