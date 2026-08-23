"""Global Task Deferred 迁移的架构守卫（迁移计划阶段 7）。

约束：
- 禁止第二 Agent loop：Global Task 业务/持久化层不得依赖 Pydantic AI，
  任务推进只由后台 worker 完成，Agent run 只经 factory/chat/continuation；
- 禁止自研 deferred codec：Pydantic 官方 Deferred 生命周期类型只能出现在
  白名单模块，其它模块不得自定义等价编解码；
- 禁止前端任务推进：前端只允许纯 GET 读取与明确用户命令，不得保留任何
  写刷新调用。
- 禁止恢复旧数据库中的 Deferred/Global Task：当前数据库没有 legacy Task
  取消或旧版本 upgrade 路径。

AI 工具上下文边界与写入一致性守卫（修复计划第 11 节）：
- side_effect="write" 的 Capability 输出不得包含无界完整业务聚合对象；
- 保存类回执不得使用无界 dict 顶层完整资源返回；
- Global Task 参数持久化必须保留 exclude_unset；
- write executor 之后的投影错误必须携带明确副作用状态；
- 通用整对象保存不得回到 Global Task 常用 allowlist；
- 不新增自研 Agent loop / 消息历史协议。
"""

from __future__ import annotations

import ast

from tests.architecture.support import (
    ROOT,
    imported_targets,
    parse_python,
    python_files,
)

GLOBAL_TASK_BUSINESS_FILES = (
    "erp_web/services/global_task_controller.py",
    "erp_web/stores/global_task_store.py",
    "erp_web/facades/global_task_facade.py",
)

DEFERRED_LIFECYCLE_SYMBOLS = frozenset(
    {"DeferredToolRequests", "DeferredToolResults", "CallDeferred"}
)

SANCTIONED_DEFERRED_MODULES = frozenset(
    {
        "erp_web/services/ai_agent_factory.py",
        "erp_web/services/ai_tool_bridge.py",
        "erp_web/services/global_agent_chat_service.py",
        "erp_web/services/global_task_continuation_service.py",
    }
)


def _relative_posix(path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_database_has_no_legacy_deferred_task_migration() -> None:
    source = (ROOT / "erp_web/db.py").read_text(encoding="utf-8")
    for retired_symbol in (
        "_cancel_legacy_unfinished_global_tasks",
        "GLOBAL_TASK_LEGACY_MIGRATION_CANCELLED",
        "_V12_TO_V13_UPGRADE_SQL",
    ):
        assert retired_symbol not in source, (
            f"当前数据库不得恢复旧 Deferred/Global Task：{retired_symbol}"
        )


def test_global_task_business_layer_has_no_pydantic_ai_dependency() -> None:
    offenders = [
        f"{_relative_posix(path)} -> {target}"
        for path, target in imported_targets(
            [ROOT / item for item in GLOBAL_TASK_BUSINESS_FILES]
        )
        if target.lstrip(".").split(".")[0] == "pydantic_ai"
    ]
    assert not offenders, (
        "Global Task 业务层不得依赖 Pydantic AI（禁止第二 Agent loop）：\n"
        + "\n".join(offenders)
    )


def test_deferred_lifecycle_types_only_in_sanctioned_modules() -> None:
    offenders: list[str] = []
    for path in python_files("erp_web"):
        rel = _relative_posix(path)
        for node in ast.walk(parse_python(path)):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if not module.startswith("pydantic_ai"):
                continue
            imported = {alias.name for alias in node.names}
            bad = imported & DEFERRED_LIFECYCLE_SYMBOLS
            if bad and rel not in SANCTIONED_DEFERRED_MODULES:
                offenders.append(f"{rel}:{node.lineno} -> {sorted(bad)}")
    assert not offenders, (
        "官方 Deferred 生命周期类型只允许出现在白名单模块"
        "（禁止自研 deferred codec）：\n" + "\n".join(offenders)
    )


def test_message_store_keeps_no_synthetic_tool_return_repair() -> None:
    source = (
        ROOT / "erp_web/stores/pydantic_message_store.py"
    ).read_text(encoding="utf-8")
    for banned in (
        "repair_orphaned_tool_returns",
        "INTERRUPTED_TOOL_RETURN_CONTENT",
        "SYNTHESIZED_TOOL_RETURN_METADATA_KEY",
    ):
        assert banned not in source, (
            f"message store 不得保留合成 tool return 逻辑：{banned}"
        )


def test_frontend_has_no_task_write_refresh_call() -> None:
    offenders: list[str] = []
    for path in (ROOT / "front/src").rglob("*"):
        if not path.is_file() or path.suffix not in {".ts", ".vue"}:
            continue
        text = path.read_text(encoding="utf-8")
        for banned in ("refreshGlobalTask", "/api/global-task-refresh"):
            if banned in text:
                offenders.append(f"{_relative_posix(path)} -> {banned}")
    assert not offenders, (
        "前端不得保留任何任务写刷新调用：\n" + "\n".join(offenders)
    )


def test_frontend_task_state_read_is_get_only() -> None:
    text = (ROOT / "front/src/api/globalTasks.ts").read_text(encoding="utf-8")
    assert "/api/v1/global-tasks" in text
    fetch_block = text.split("export async function fetchGlobalTask", 1)[1]
    fetch_block = fetch_block.split("export async function", 1)[0]
    assert "apiClient.get" in fetch_block, "任务状态读取必须是纯 GET"
    assert "apiClient.post" not in fetch_block, (
        "fetchGlobalTask 不得发起写请求"
    )


def test_message_part_does_not_mount_task_card() -> None:
    text = (
        ROOT / "front/src/components/ai-work/AiMessagePart.vue"
    ).read_text(encoding="utf-8")
    assert "GlobalTaskApprovalCard" not in text, (
        "任务卡只能在 conversation 级 AiChatPanel 挂载，"
        "消息 part 不得重复渲染可操作任务卡"
    )


# -- 写入一致性与上下文边界守卫（修复计划第 11 节） -------------------------

#: 完整业务聚合对象的字段名；写回执出现它们即代表把无界资源带回模型上下文。
_UNBOUNDED_AGGREGATE_FIELDS = frozenset(
    {
        "product",
        "draft",
        "product_context",
        "raw",
        "productsIndex",
        "draftsIndex",
        "image_pool",
        "imagePool",
    }
)


def _schema_property_names(node, found: set[str]) -> None:
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            found.update(str(name) for name in properties)
        for value in node.values():
            _schema_property_names(value, found)
    elif isinstance(node, list):
        for value in node:
            _schema_property_names(value, found)


def test_write_capability_outputs_exclude_unbounded_aggregates() -> None:
    from erp_web.ai_capability_composition import (
        APPLICATION_CAPABILITY_CATALOG,
    )

    offenders: list[str] = []
    for name, tool in sorted(APPLICATION_CAPABILITY_CATALOG.tools.items()):
        definition = tool.definition
        if definition.side_effect != "write":
            continue
        found: set[str] = set()
        _schema_property_names(dict(definition.output_schema), found)
        bad = found & _UNBOUNDED_AGGREGATE_FIELDS
        if bad:
            offenders.append(f"{name} -> {sorted(bad)}")
    assert not offenders, (
        "写 Capability 的输出不得包含无界完整业务聚合对象"
        "（必须返回有界 mutation receipt）：\n" + "\n".join(offenders)
    )


def test_save_receipts_do_not_use_unbounded_dict_resource() -> None:
    from erp_web.schemas.product_write_capabilities import (
        DraftPricingApplyResult,
        DraftSaveResult,
        DraftStockUpdateResult,
        ProductProfilePatchResult,
        ProductSaveResult,
    )

    for model in (
        ProductSaveResult,
        DraftSaveResult,
        ProductProfilePatchResult,
        DraftStockUpdateResult,
        DraftPricingApplyResult,
    ):
        for field_name, field in model.model_fields.items():
            annotation = str(field.annotation)
            assert "dict" not in annotation, (
                f"{model.__name__}.{field_name} 不得使用无界 dict 作为"
                "完整资源返回"
            )


def test_global_task_arguments_persist_with_exclude_unset() -> None:
    source = (
        ROOT / "erp_web/services/global_task_controller.py"
    ).read_text(encoding="utf-8")
    # 创建与补资料两条持久化路径都必须保留部分补丁语义。
    assert source.count("exclude_unset=True") >= 3, (
        "Global Task 参数持久化必须保留 exclude_unset，"
        "未提供字段不得展开成显式空值"
    )


def test_write_projection_errors_carry_side_effect_state() -> None:
    source = (
        ROOT / "erp_web/services/ai_tool_runtime.py"
    ).read_text(encoding="utf-8")
    assert "_RESULT_PROJECTION_FAILURE_DETAILS" in source
    assert '"outcome_unknown": True' in source
    assert '"failure_stage": "result_projection"' in source
    assert '"side_effect_may_have_completed": True' in source
    assert 'definition.side_effect != "none"' in source, (
        "write executor 之后的投影错误必须携带明确副作用状态"
    )


def test_generic_object_saves_not_in_global_task_allowlist() -> None:
    from erp_web.ai_capability_composition import (
        GLOBAL_TASK_CAPABILITIES,
        INTERNAL_ONLY_CAPABILITIES,
    )

    for generic in ("product_save", "draft_save"):
        assert generic not in GLOBAL_TASK_CAPABILITIES, (
            f"通用 {generic} 容易误选 owner，必须由 focused write 取代"
        )
        assert generic in INTERNAL_ONLY_CAPABILITIES, (
            f"{generic} 被移出 Task allowlist 后仍需保留 exposure 归属"
        )
    for focused in (
        "product_profile_patch",
        "draft_stock_update",
        "draft_pricing_apply",
        "product_attributes_update",
    ):
        assert focused in GLOBAL_TASK_CAPABILITIES, (
            f"focused write {focused} 必须进入 Global Task allowlist"
        )


# -- 模型上下文投影与 Provider 协议所有权守卫（修复计划第 15 节） ------------


def test_projection_module_has_no_provider_protocol_or_model_branches() -> None:
    source = (
        ROOT / "erp_web/services/ai_model_context_projection.py"
    ).read_text(encoding="utf-8")
    lowered = source.lower()
    # 投影模块不得实现 Provider thinking 协议映射或按模型名分支。
    for banned in ("reasoning_content", "deepseek", "gpt-", "claude-"):
        assert banned not in lowered, (
            f"投影模块不得包含 Provider 协议字段或模型名分支：{banned}"
        )
    # 不得读写 provider 元数据字段（签名/provider_details 由 adapter 负责）。
    assert ".provider_details" not in source
    assert "provider_details=" not in source
    # 不得导入任何 Provider SDK。
    for provider_import in ("import openai", "import anthropic", "from openai", "from anthropic"):
        assert provider_import not in source


def test_no_pydantic_ai_harness_dependency_or_import() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "pydantic_ai_harness" not in requirements
    assert "pydantic-ai-harness" not in requirements
    offenders: list[str] = []
    for path in python_files("erp_web"):
        text = path.read_text(encoding="utf-8")
        if "pydantic_ai_harness" in text:
            offenders.append(_relative_posix(path))
    assert not offenders, (
        "项目代码不得导入或引用 pydantic_ai_harness：\n" + "\n".join(offenders)
    )


def test_history_projection_uses_official_process_history_capability() -> None:
    source = (
        ROOT / "erp_web/services/ai_agent_factory.py"
    ).read_text(encoding="utf-8")
    assert "ProcessHistory" in source, (
        "模型历史投影必须通过 Pydantic AI 官方 ProcessHistory Capability"
    )
    assert "project_model_context_for_model" in source
    # 不得调用私有 _agent_graph 或读取 Pydantic 内部 new_message_index。
    assert "_agent_graph" not in source
    assert "new_message_index" not in source

