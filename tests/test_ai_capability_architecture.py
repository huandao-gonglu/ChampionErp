"""显式领域能力目录、权限和原生工具边界的架构守卫。"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from erp_web.ai_capability_composition import (
    ALL_AI_CAPABILITIES,
    APPLICATION_CAPABILITY_CATALOG,
    GLOBAL_CHAT_CAPABILITIES,
    _WRITE_CAPABILITIES,
    INTERNAL_ONLY_CAPABILITIES,
    validate_capability_exposure,
)
from erp_web.schemas.platform_query_capabilities import (
    ProductsIndexQueryRequest,
    ProductsIndexQueryResult,
)


APP_ROOT = Path(__file__).resolve().parents[1]


def test_capability_exposure_rules_hold() -> None:
    validate_capability_exposure()

    catalog_names = set(APPLICATION_CAPABILITY_CATALOG.tools)
    assert catalog_names == set(
        GLOBAL_CHAT_CAPABILITIES | _WRITE_CAPABILITIES | INTERNAL_ONLY_CAPABILITIES
    )
    # 审批能力只能是 Task 能力，主 Agent 不得直接触发破坏性写入。
    for name, tool in APPLICATION_CAPABILITY_CATALOG.tools.items():
        if tool.definition.approval_required:
            assert name not in GLOBAL_CHAT_CAPABILITIES, name
            assert name in _WRITE_CAPABILITIES, name


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


def test_business_catalog_compiled_only_in_composition_root() -> None:
    allowed = {
        APP_ROOT / "erp_web" / "ai_capability_composition.py",
        # 任务控制 ToolSet 是控制面 Catalog，不含业务能力。
        APP_ROOT / "erp_web" / "runtime_units" / "global_ai_control_tools.py",
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


def test_approval_level_is_not_model_controlled_and_retired_debug_surface_is_absent() -> (
    None
):
    all_model_schemas = repr(
        {
            name: tool.definition.input_schema
            for catalog in (APPLICATION_CAPABILITY_CATALOG,)
            for name, tool in catalog.tools.items()
        }
    )
    assert "task_approval_mode" not in all_model_schemas

    retired_files = (
        APP_ROOT / "erp_web" / "debug_autonomy_gate.py",
        APP_ROOT / "erp_web" / "facades" / "debug_autonomy_facade.py",
        APP_ROOT / "erp_web" / "http_route_units" / "debug_autonomy_routes.py",
        APP_ROOT / "erp_web" / "schemas" / "debug_autonomy.py",
        APP_ROOT / "erp_web" / "services" / "debug_autonomy_service.py",
        APP_ROOT / "front" / "src" / "api" / "debugAutonomy.ts",
        APP_ROOT / "front" / "src" / "stores" / "debugAutonomy.ts",
    )
    assert not [path for path in retired_files if path.exists()]

    retired_command = "/" + "allow"
    retired_endpoint = "/api/v1/" + "debug-autonomy"
    guarded_files = (
        APP_ROOT / "config" / "prompts" / "global_chat.json",
        APP_ROOT / "erp_web" / "http_routes.py",
        APP_ROOT / "erp_web" / "services" / "vercel_ai_ui_service.py",
        APP_ROOT / "front" / "src" / "stores" / "aiChat.ts",
    )
    for path in guarded_files:
        source = path.read_text(encoding="utf-8")
        assert retired_command not in source, path
        assert retired_endpoint not in source, path


def test_product_index_query_supports_snapshot_bound_position_resolution() -> None:
    """“第几个商品”必须绑定服务端快照，不能由模型用历史列表换算 ID。"""

    request_fields = ProductsIndexQueryRequest.model_fields
    result_fields = ProductsIndexQueryResult.model_fields

    assert "snapshot_id" in request_fields
    assert "positions" in request_fields
    assert "snapshot_id" in result_fields
    assert "selected_items" in result_fields


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
    """同步 I/O 入口或其绑定查询适配器必须把剩余时间传给底层 HTTP/SDK。"""

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
        if name == "category_search":
            from erp_web.runtime_units.category_query_capabilities import (
                _CategoryQuerySearcher,
            )

            assert (
                "_CategoryQuerySearcher(platform, site, scope.searcher, execution)"
                in source
            )
            source = inspect.getsource(_CategoryQuerySearcher.search_categories)
        if "bounded_timeout_seconds(" not in source:
            offenders.append(name)
    assert offenders == [], (
        "以下阻塞 I/O 能力未把 execution.bounded_timeout_seconds() 传给底层调用："
        + ", ".join(offenders)
    )


def test_category_search_catalog_uses_keyword_list_contract() -> None:
    definition = APPLICATION_CAPABILITY_CATALOG.tools["category_search"].definition
    assert definition.version == "3"
    properties = definition.input_schema["properties"]
    assert "product_type" in properties
    assert "keywords" in properties and "query" not in properties
    assert properties["keywords"]["maxItems"] == 64


def test_external_side_effect_capabilities_never_auto_retry_after_dispatch() -> None:
    """调用外部平台的写能力，在请求发出后不得把异常包装成 retryable=True：
    副作用结果未知时必须按 outcome_unknown 处理，禁止自动重试造成重复操作。"""

    external_dispatch_capabilities = (
        "logistics_shipment_create",
        "mercadolibre_user_product_pause",
        "product_publish_direct",
    )
    offenders: list[str] = []
    for name in external_dispatch_capabilities:
        tool = APPLICATION_CAPABILITY_CATALOG.tools[name]
        source = inspect.getsource(tool.function)
        if "retryable=True" in source:
            offenders.append(name)
    assert offenders == [], (
        "以下外部写能力不得在副作用发出后声明 retryable=True：" + ", ".join(offenders)
    )


# -- P2-5：Job Status Reader 注册表领域无关，Controller 不依赖领域模块 ------
