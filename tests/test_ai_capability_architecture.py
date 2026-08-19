"""单主 Agent 与全业务 Capability 化的架构守卫（规划 §6/§8 Workstream E）。

守卫内容：
- exposure 覆盖规则（唯一 Catalog、Direct/Task/Internal 集合约束）；
- ``global_task_start`` 的 step union 与 Task allowlist 同源机械投影；
- Controller 与 Task schema 不含 Capability 名称分支、Planner 残留；
- 业务 Catalog 只在组合根编译一次（不存在第二个 Schema compiler/Task Spec）；
- 写能力必须声明幂等与恢复元数据，只读能力不得声明幂等。
"""

from __future__ import annotations

import inspect
import json
import re
import typing
from pathlib import Path

from erp_web.ai_capability_composition import (
    ALL_AI_CAPABILITIES,
    APPLICATION_CAPABILITY_CATALOG,
    GLOBAL_CHAT_DIRECT_CAPABILITIES,
    GLOBAL_TASK_CAPABILITIES,
    INTERNAL_ONLY_CAPABILITIES,
    validate_capability_exposure,
)
from erp_web.runtime_units.global_ai_control_tools import (
    GLOBAL_TASK_CONTROL_CATALOG,
    GlobalTaskStartControlRequest,
)
from erp_web.services.global_task_controller import GlobalTaskController


APP_ROOT = Path(__file__).resolve().parents[1]


def test_capability_exposure_rules_hold() -> None:
    validate_capability_exposure()

    catalog_names = set(APPLICATION_CAPABILITY_CATALOG.tools)
    assert catalog_names == set(
        GLOBAL_CHAT_DIRECT_CAPABILITIES
        | GLOBAL_TASK_CAPABILITIES
        | INTERNAL_ONLY_CAPABILITIES
    )
    # 审批能力只能是 Task 能力，主 Agent 不得直接触发破坏性写入。
    for name, tool in APPLICATION_CAPABILITY_CATALOG.tools.items():
        if tool.definition.approval_required:
            assert name not in GLOBAL_CHAT_DIRECT_CAPABILITIES, name
            assert name in GLOBAL_TASK_CAPABILITIES, name


def test_write_capabilities_declare_idempotency_and_recovery() -> None:
    for name, tool in APPLICATION_CAPABILITY_CATALOG.tools.items():
        definition = tool.definition
        if definition.side_effect == "write":
            assert definition.idempotency == "required", name
            assert definition.idempotency_keys, name
            assert definition.recovery_policy, name
        else:
            assert definition.idempotency == "none", name
            assert definition.idempotency_keys == (), name


def test_task_step_union_is_projected_from_task_allowlist() -> None:
    annotation = GlobalTaskStartControlRequest.model_fields["steps"].annotation
    (inner,) = typing.get_args(annotation)
    union_type = typing.get_args(inner)[0]
    branches = typing.get_args(union_type)
    projected = {
        typing.get_args(branch.model_fields["capability_name"].annotation)[0]
        for branch in branches
    }
    assert projected == set(GLOBAL_TASK_CAPABILITIES)
    for branch in branches:
        name = typing.get_args(branch.model_fields["capability_name"].annotation)[0]
        request_type = branch.model_fields["arguments"].annotation
        assert request_type is APPLICATION_CAPABILITY_CATALOG.tools[name].request_type


def test_controller_and_task_schema_are_capability_name_agnostic() -> None:
    guarded = (
        APP_ROOT / "erp_web" / "services" / "global_task_controller.py",
        APP_ROOT / "erp_web" / "schemas" / "global_tasks.py",
    )
    capability_names = set(APPLICATION_CAPABILITY_CATALOG.tools)
    for path in guarded:
        text = path.read_text(encoding="utf-8")
        leaked = sorted(
            name for name in capability_names if f'"{name}"' in text or f"'{name}'" in text
        )
        assert leaked == [], f"{path.name} 出现 Capability 名称分支：{leaked}"
        lowered = text.lower()
        assert "planner" not in lowered, f"{path.name} 残留 Planner 引用"
        assert "global.task.plan" not in text, f"{path.name} 残留旧 plan 绑定"


def test_business_catalog_compiled_only_in_composition_root() -> None:
    allowed = {
        APP_ROOT / "erp_web" / "ai_capability_composition.py",
        # 任务控制 ToolSet 是控制面 Catalog，不含业务能力。
        APP_ROOT / "erp_web" / "runtime_units" / "global_ai_control_tools.py",
        # 属性填充 focused Agent 的 run-scoped 内部工具集（Internal 用途），
        # 不进入主 Agent/Task exposure。
        APP_ROOT / "erp_web" / "runtime_units" / "category_attribute_tools.py",
    }
    offenders: list[str] = []
    for path in sorted((APP_ROOT / "erp_web").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if "AiToolCatalog.compile(" in path.read_text(encoding="utf-8"):
            if path not in allowed:
                offenders.append(str(path.relative_to(APP_ROOT)))
    assert offenders == [], f"业务 Catalog 只允许在组合根编译：{offenders}"
    # 组合根必须显式组合全部领域 tuple，不扫描包、不动态发现。
    assert len(ALL_AI_CAPABILITIES) == len(APPLICATION_CAPABILITY_CATALOG.tools)


# -- P1-1：审批入口不得进入模型可绑定 ToolSet -------------------------------


def test_task_control_toolset_has_exactly_four_tools_and_no_approval() -> None:
    control_names = set(GLOBAL_TASK_CONTROL_CATALOG.tools)
    assert control_names == {
        "global_task_start",
        "global_task_get",
        "global_task_submit_input",
        "global_task_cancel",
    }, control_names
    # 审批/拒绝只走受信 UI/API，不作为模型工具存在。
    assert "global_task_approve" not in control_names
    assert "global_task_reject" not in control_names
    for catalog in (
        GLOBAL_TASK_CONTROL_CATALOG,
        APPLICATION_CAPABILITY_CATALOG,
    ):
        for name in catalog.tools:
            assert name not in {"global_task_approve", "global_task_reject"}, name


def test_global_chat_prompt_routes_writes_to_typed_tasks() -> None:
    prompt_path = APP_ROOT / "config" / "prompts" / "global_chat.json"
    prompt = json.loads(prompt_path.read_text(encoding="utf-8"))
    system = str(prompt.get("system") or "")
    assert "你没有写权限" not in system
    assert "global_task_start" in system
    assert "product_delete" in system
    assert "draft_delete" in system
    assert "pending_approval" in system


# -- P1-2：审批摘要由服务端快照生成，模型不提交 approval 字段 ---------------


def test_approval_capabilities_bind_server_snapshot_and_forbid_model_approval() -> None:
    approval_tools = {
        name: tool
        for name, tool in APPLICATION_CAPABILITY_CATALOG.tools.items()
        if tool.definition.approval_required
    }
    assert approval_tools, "目录必须包含审批能力"
    for name, tool in approval_tools.items():
        # 每个审批能力都声明服务端快照函数；编译器据此在绑定期生成准备器。
        assert callable(tool.metadata.approval_snapshot), name
        # 模型可见 input schema 不得包含 approval 字段（展示/绑定都由快照派生）。
        properties = tool.definition.input_schema.get("properties")
        assert not isinstance(properties, dict) or "approval" not in properties, name


# -- P1-3：只读能力不得写持久化状态 / 外部世界 -------------------------------

_READ_ONLY_FORBIDDEN_WRITE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bsave_[a-z0-9_]+\(",
        r"\bdelete_[a-z0-9_]+\(",
        r"\bpersist_[a-z0-9_]+\(",
        r"\.enqueue\(",
        r"\brequests\.(post|put|delete|patch)\(",
        r"\bhttpx\.(post|put|delete|patch)\(",
        r"urllib\.request\.(urlopen|Request)\(",
        r"\bsqlite3\.connect\(",
    )
)


def _reachable_same_module_sources(entry, *, max_functions: int = 60) -> list[str]:
    """收集入口函数及其同模块可达被调函数的源码，用于副作用静态扫描。"""

    sources: list[str] = []
    seen: set[int] = set()
    queue = [entry]
    while queue and len(seen) < max_functions:
        func = queue.pop()
        code = getattr(func, "__code__", None)
        if code is None or id(code) in seen:
            continue
        seen.add(id(code))
        try:
            sources.append(inspect.getsource(func))
        except (OSError, TypeError):
            continue
        globals_map = getattr(func, "__globals__", {}) or {}
        module_name = getattr(func, "__module__", "")
        for name in code.co_names:
            candidate = globals_map.get(name)
            candidate_code = getattr(candidate, "__code__", None)
            if (
                callable(candidate)
                and candidate_code is not None
                and getattr(candidate, "__module__", "") == module_name
                and id(candidate_code) not in seen
            ):
                queue.append(candidate)
    return sources


def test_read_only_capabilities_have_no_write_side_effects() -> None:
    offenders: list[str] = []
    for name, tool in APPLICATION_CAPABILITY_CATALOG.tools.items():
        if tool.definition.side_effect != "none":
            continue
        for source in _reachable_same_module_sources(tool.function):
            for pattern in _READ_ONLY_FORBIDDEN_WRITE_PATTERNS:
                if pattern.search(source):
                    offenders.append(f"{name}: {pattern.pattern}")
    assert offenders == [], (
        "只读能力（side_effect='none'）不得写持久化状态或外部世界：\n"
        + "\n".join(sorted(offenders))
    )


# -- P1-4：同步阻塞 I/O 必须接收并使用有界超时 ------------------------------


def test_blocking_io_capabilities_thread_bounded_timeout() -> None:
    """进行同步阻塞 I/O 的能力必须在函数体内调用 bounded_timeout_seconds()，
    把外层剩余时间传给底层 HTTP/SDK，而不是丢弃 execution。"""

    blocking_io_capabilities = (
        "category_search",
        "category_attributes_query",
        "category_attribute_values_query",
        "category_precheck",
        "logistics_shipment_create",
    )
    offenders: list[str] = []
    for name in blocking_io_capabilities:
        tool = APPLICATION_CAPABILITY_CATALOG.tools[name]
        source = inspect.getsource(tool.function)
        if "bounded_timeout_seconds(" not in source:
            offenders.append(name)
    assert offenders == [], (
        "以下阻塞 I/O 能力未把 execution.bounded_timeout_seconds() 传给底层调用："
        + ", ".join(offenders)
    )


def test_external_side_effect_capabilities_never_auto_retry_after_dispatch() -> None:
    """调用外部平台的写能力，在请求发出后不得把异常包装成 retryable=True：
    副作用结果未知时必须按 outcome_unknown 处理，禁止自动重试造成重复操作。"""

    external_dispatch_capabilities = (
        "logistics_shipment_create",
        "platform_item_close",
        "product_publish_direct",
        "publish_real_confirm",
    )
    offenders: list[str] = []
    for name in external_dispatch_capabilities:
        tool = APPLICATION_CAPABILITY_CATALOG.tools[name]
        source = inspect.getsource(tool.function)
        if "retryable=True" in source:
            offenders.append(name)
    assert offenders == [], (
        "以下外部写能力不得在副作用发出后声明 retryable=True："
        + ", ".join(offenders)
    )


# -- P2-5：Job Status Reader 注册表领域无关，Controller 不依赖领域模块 ------


def test_controller_job_readers_are_injected_and_domain_agnostic() -> None:
    controller_source = (
        APP_ROOT / "erp_web" / "services" / "global_task_controller.py"
    ).read_text(encoding="utf-8")
    # Controller 通过注入的 reader 注册表按 job_type 解析状态，
    # 不得直接 import 领域 runtime_units（publish/research 等）。
    assert "erp_web.runtime_units" not in controller_source
    assert "from erp_web.runtime_units" not in controller_source
    # job_status_readers 是构造期必填项，保证 Job 状态读取受信且可注册。
    signature = inspect.signature(GlobalTaskController.__init__)
    assert "job_status_readers" in signature.parameters
