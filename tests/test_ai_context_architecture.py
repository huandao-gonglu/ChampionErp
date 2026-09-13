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
import json

from tests.architecture.support import (
    ROOT,
    imported_targets,
    parse_python,
    python_files,
)


DEFERRED_LIFECYCLE_SYMBOLS = frozenset(
    {"DeferredToolRequests", "DeferredToolResults", "CallDeferred"}
)

SANCTIONED_DEFERRED_MODULES = frozenset(
    {
        "erp_web/services/ai_agent_factory.py",
        "erp_web/services/ai_tool_bridge.py",
        "erp_web/services/global_agent_chat_service.py",
        "erp_web/services/agent_job_service.py",
        "erp_web/services/agent_run_storage.py",
        "erp_web/stores/agent_call_store.py",
        "erp_web/services/vercel_ai_ui_service.py",
    }
)


def _relative_posix(path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_agent_memory_is_loaded_only_by_the_main_chat_service():
    """长期记忆通过现有主 Agent 指令装配进入，不散落到工具或传输层。"""
    consumers = {
        _relative_posix(path)
        for path, target in imported_targets(python_files(ROOT / "erp_web"))
        if target.startswith("erp_web.services.agent_memory")
    }
    assert consumers == {"erp_web/services/global_agent_chat_service.py"}
    memory_imports = imported_targets([ROOT / "erp_web/services/agent_memory.py"])
    assert not any(target.startswith(("pydantic_ai", "erp_web.runtime_units", "erp_web.stores"))
                   for _, target in memory_imports)


def test_agent_tool_budget_visibility_uses_native_prepare_tools():
    """预算只从原生 RunContext 读取，集中装配时保留原生最终输出通道。"""
    tree = parse_python(ROOT / "erp_web/services/ai_agent_factory.py")
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "PrepareTools"
        for node in ast.walk(tree)
    )
    preparer = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_prepare_tools_within_usage_limit"
    )
    attributes = {
        ast.unparse(node)
        for node in ast.walk(preparer)
        if isinstance(node, ast.Attribute)
    }
    assert {
        "ctx.usage.tool_calls",
        "ctx.deps.tool_runtime.max_tool_calls",
    } <= attributes


def test_listing_grouping_is_owned_by_pure_domain_rules():
    paths = [
        ROOT / "erp_web/schemas/category_grouping.py",
    ]
    assert not any(
        any(
            part in target
            for part in ("runtime_units", "services", "stores", "pydantic_ai")
        )
        for _, target in imported_targets(paths)
    )
    for name in (
        "product_model/category_model.py",
        "runtime_units/category_attribute_updates.py",
        "runtime_units/sku_publish_projection.py",
        "runtime_units/publish_ozon.py",
    ):
        assert any(
            target.startswith("erp_web.schemas.category_grouping.")
            for _, target in imported_targets([ROOT / "erp_web" / name])
        )


def test_sku_images_have_one_asset_reference_contract() -> None:
    from erp_web.schemas.requests import IMAGE_ACTION
    from erp_web.services import image_service

    assert "set_sku" not in IMAGE_ACTION.choices
    assert not hasattr(image_service, "set_sku_image")
    for path in (
        "erp_web/runtime_units/sku_publish_projection.py",
        "erp_web/runtime_units/sku_publish_adapter.py",
    ):
        tree = parse_python(ROOT / path)
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "image"
            for node in ast.walk(tree)
        ), "SKU 发布只能按资产 ID 关联，不能恢复 URL 匹配"
    dependencies = [
        target
        for _, target in imported_targets(
            [ROOT / "erp_web/product_model/sku_image_model.py"]
        )
    ]
    assert not any(
        any(
            part in target
            for part in ("runtime_units", "stores", "services", "context")
        )
        for target in dependencies
    )


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
    source = (ROOT / "erp_web/stores/pydantic_message_store.py").read_text(
        encoding="utf-8"
    )
    for banned in (
        "repair_orphaned_tool_returns",
        "INTERRUPTED_TOOL_RETURN_CONTENT",
        "SYNTHESIZED_TOOL_RETURN_METADATA_KEY",
    ):
        assert banned not in source, (
            f"message store 不得保留合成 tool return 逻辑：{banned}"
        )


def test_copy_generation_uses_typed_schema_instead_of_prompt_field_contract() -> None:
    service_source = (ROOT / "erp_web/services/copy_service.py").read_text(
        encoding="utf-8"
    )
    assert "ai_gateway.chat_structured(" in service_source
    assert "ai_gateway.chat_json(" not in service_source

    prompt_config = json.loads(
        (ROOT / "config/prompts/copy_generate.json").read_text(encoding="utf-8")
    )
    prompt_text = "\n".join(
        str(prompt_config.get(key) or "") for key in ("system", "user")
    )
    for handwritten_contract in (
        "exactly these keys",
        "title: string",
        "global_title",
    ):
        assert handwritten_contract not in prompt_text, (
            "文案输出字段必须由 Pydantic Schema 提供，业务 Prompt 不得重复字段契约："
            f"{handwritten_contract}"
        )


def test_copy_generation_feedback_is_owned_by_native_agent() -> None:
    """文案修正必须留在原生 Agent 的输出校验链，不能重新开始无反馈的生成。"""
    import inspect
    from erp_web.services.copy_service import CopyOutputValidator, generate_copy

    generation = inspect.getsource(generate_copy)
    validator = inspect.getsource(CopyOutputValidator)
    assert "AiAgentFactory(" in generation
    assert "output_validator=validator" in generation
    assert "PromptedOutput(output_type)" in generation
    assert "ai_gateway.chat_structured(" not in generation
    assert "raise ModelRetry(" in validator
    assert not any(isinstance(node, (ast.For, ast.While)) for node in ast.walk(ast.parse(generation)))


def test_frontend_has_no_task_write_refresh_call() -> None:
    offenders: list[str] = []
    for path in (ROOT / "front/src").rglob("*"):
        if not path.is_file() or path.suffix not in {".ts", ".vue"}:
            continue
        text = path.read_text(encoding="utf-8")
        for banned in ("refreshGlobalTask", "/api/global-task-refresh"):
            if banned in text:
                offenders.append(f"{_relative_posix(path)} -> {banned}")
    assert not offenders, "前端不得保留任何任务写刷新调用：\n" + "\n".join(offenders)


def test_mercadolibre_admin_uses_only_user_products_contract() -> None:
    """管理入口不得恢复本地 item 列表、关闭或特殊确认双轨。"""

    routes = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "erp_web/http_route_units/get_routes.py",
            "erp_web/http_route_units/publish_routes.py",
            "erp_web/schemas/requests.py",
        )
    )
    assert "/api/mercadolibre/user-products" in routes
    assert "/api/mercadolibre/pause-user-product" in routes
    for retired in (
        "/api/mercadolibre/published-items",
        "/api/mercadolibre/close-item",
        "/api/mercadolibre/confirm-real-publish",
    ):
        assert retired not in routes


def test_mercadolibre_publisher_only_has_explicit_cbt_write_paths() -> None:
    source = (ROOT / "erp_web/marketplaces/publishing.py").read_text(encoding="utf-8")
    for retired in (
        "https://api.mercadolibre.com/items",
        '"_global_selling"',
        '"_item_id"',
    ):
        assert retired not in source
    assert "/global/user-products/families" in source
    assert "/global/user-products/" in source
    assert '"https://api.mercadolibre.com/global/items"' in source
    assert "traditional_global_items" in source
    assert "require_mercadolibre_listing_model" in source
    assert "quote(siteless_id" in source


def test_message_part_does_not_mount_task_card() -> None:
    text = (ROOT / "front/src/components/ai-work/AiMessagePart.vue").read_text(
        encoding="utf-8"
    )
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
        DraftDuplicateResult,
        DraftSkuSelectionUpdateResult,
        DraftPricingApplyResult,
        DraftSaveResult,
        DraftStockUpdateResult,
        ProductProfilePatchResult,
        ProductSaveResult,
    )

    for model in (
        DraftDuplicateResult,
        DraftSkuSelectionUpdateResult,
        ProductSaveResult,
        DraftSaveResult,
        ProductProfilePatchResult,
        DraftStockUpdateResult,
        DraftPricingApplyResult,
    ):
        for field_name, field in model.model_fields.items():
            annotation = str(field.annotation)
            assert "dict" not in annotation, (
                f"{model.__name__}.{field_name} 不得使用无界 dict 作为完整资源返回"
            )


def test_write_projection_errors_carry_side_effect_state() -> None:
    source = (ROOT / "erp_web/services/ai_tool_runtime.py").read_text(encoding="utf-8")
    assert "_RESULT_PROJECTION_FAILURE_DETAILS" in source
    assert '"outcome_unknown": True' in source
    assert '"failure_stage": "result_projection"' in source
    assert '"side_effect_may_have_completed": True' in source
    assert 'definition.side_effect != "none"' in source, (
        "write executor 之后的投影错误必须携带明确副作用状态"
    )


def test_generic_object_saves_not_in_global_task_allowlist() -> None:
    from erp_web.ai_capability_composition import (
        _WRITE_CAPABILITIES,
        INTERNAL_ONLY_CAPABILITIES,
    )

    for generic in ("product_save", "draft_save"):
        assert generic not in _WRITE_CAPABILITIES, (
            f"通用 {generic} 容易误选 owner，必须由 focused write 取代"
        )
        assert generic in INTERNAL_ONLY_CAPABILITIES, (
            f"{generic} 被移出 Task allowlist 后仍需保留 exposure 归属"
        )
    for focused in (
        "product_profile_patch",
        "draft_stock_update",
        "draft_duplicate",
        "draft_sku_selection_update",
        "draft_pricing_apply",
        "product_attributes_update",
    ):
        assert focused in _WRITE_CAPABILITIES, (
            f"focused write {focused} 必须进入 Global Task allowlist"
        )


# -- 模型上下文投影与 Provider 协议所有权守卫（修复计划第 15 节） ------------


def test_projection_module_has_no_provider_protocol_or_model_branches() -> None:
    source = (ROOT / "erp_web/services/ai_model_context_projection.py").read_text(
        encoding="utf-8"
    )
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
    for provider_import in (
        "import openai",
        "import anthropic",
        "from openai",
        "from anthropic",
    ):
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
    assert not offenders, "项目代码不得导入或引用 pydantic_ai_harness：\n" + "\n".join(
        offenders
    )


def test_history_projection_uses_official_model_request_hook() -> None:
    source = (ROOT / "erp_web/services/ai_agent_factory.py").read_text(encoding="utf-8")
    assert "model_request=project_model_request" in source
    assert "ProcessHistory(" not in source, "请求投影不能替换用于审计的完整原生 run 历史。"
    # 不得调用私有 _agent_graph 或读取 Pydantic 内部 new_message_index。
    assert "_agent_graph" not in source
    assert "new_message_index" not in source


def test_mercadolibre_publish_has_no_direct_http_or_ai_bypass() -> None:
    workflow_source = (ROOT / "erp_web/runtime_units/publish_workflows.py").read_text(
        encoding="utf-8"
    )
    capability_source = (
        ROOT / "erp_web/runtime_units/publish_admin_capabilities.py"
    ).read_text(encoding="utf-8")

    assert 'if platform == "mercadolibre"' in workflow_source
    assert '"MERCADOLIBRE_PUBLISH_BUS_REQUIRED"' in workflow_source
    assert 'if platform == "mercadolibre"' in capability_source
    assert '"MERCADOLIBRE_PUBLISH_BUS_REQUIRED"' in capability_source


def test_platform_publish_registry_uses_sku_group_entry_point() -> None:
    from erp_web.runtime_units.publish_adapter import publishing_adapter_for
    from erp_web.runtime_units.sku_publish_adapter import SkuGroupPublishingAdapter

    for platform in ("mercadolibre", "ozon", "yandex"):
        adapter = publishing_adapter_for(platform)
        assert isinstance(adapter, SkuGroupPublishingAdapter)
        assert adapter.item_adapter.platform == platform
        assert not isinstance(adapter.item_adapter, SkuGroupPublishingAdapter)


def test_attribute_writes_do_not_create_an_agent_runtime():
    paths = [
        ROOT / "erp_web/runtime_units/category_attribute_updates.py",
        ROOT / "erp_web/runtime_units/product_capabilities.py",
    ]
    assert not any(
        any(
            part in target
            for part in (
                "pydantic_ai",
                "ai_agent_factory",
                "ai_model_factory",
                "ai_direct_request_service",
            )
        )
        for _, target in imported_targets(paths)
    )
    custom = [ROOT / "erp_web/schemas/sku_custom_attributes.py"]
    assert not any(
        any(
            part in target
            for part in ("runtime_units", "services", "stores", "pydantic_ai")
        )
        for _, target in imported_targets(custom)
    )


def test_agent_budget_retry_uses_native_tool_validator_without_response_rewriting():
    factory = (ROOT / "erp_web/services/ai_agent_factory.py").read_text()
    bridge = (ROOT / "erp_web/services/ai_tool_bridge.py").read_text()
    budget = (ROOT / "erp_web/services/ai_agent_budget.py").read_text()
    assert "args_validator=validate_arguments" in bridge
    assert "raise AgentToolBudgetRetry" not in bridge
    assert "tool_calls_limit=self._profile.max_tool_calls," in factory
    assert "after_model_request" not in budget
    assert "ModelResponse(" not in budget and "RetryPromptPart(" not in budget


def test_attribute_ai_has_only_the_main_conversation_entry():
    from erp_web.ai_capability_composition import APPLICATION_CAPABILITY_CATALOG, GLOBAL_CHAT_CAPABILITIES, _WRITE_CAPABILITIES
    from erp_web.http_route_units.category_routes import HANDLED_PATHS
    from erp_web.schemas.requests import REQUEST_CONTRACTS
    from erp_web.services.ai_model_config import AI_USE_CASES
    from erp_web.services.ai_prompt_templates import DEFAULT_AI_USE_CASE_PROMPTS

    retired_tools = {"product_attributes_fill", "draft_sku_attributes_fill", "category_attribute_values_search"}
    assert not retired_tools.intersection(APPLICATION_CAPABILITY_CATALOG.tools)
    assert {"draft_attributes_read", "category_attributes_query", "category_attribute_values_query"} <= GLOBAL_CHAT_CAPABILITIES
    assert {"product_attributes_update", "draft_sku_attributes_update"} <= _WRITE_CAPABILITIES
    assert "/api/category-ai-fill" not in HANDLED_PATHS | REQUEST_CONTRACTS.keys()
    assert "category.attribute_fill" not in AI_USE_CASES.keys() | DEFAULT_AI_USE_CASE_PROMPTS.keys()
    for path in ['erp_web/runtime_units/attribute_fill_capabilities.py', 'erp_web/runtime_units/category_attribute_ai_fill.py', 'erp_web/runtime_units/category_attribute_tools.py', 'erp_web/runtime_units/category_brand_values.py', 'erp_web/runtime_units/sku_attribute_batch.py', 'erp_web/runtime_units/sku_attribute_capabilities.py', 'erp_web/runtime_units/sku_attribute_fill.py', 'erp_web/runtime_units/sku_attribute_images.py', 'erp_web/runtime_units/sku_source_attributes.py', 'erp_web/services/category_attribute_fill_agent_service.py', 'erp_web/services/sku_attribute_fill_agent_service.py', 'erp_web/services/attribute_fact_review.py', 'erp_web/services/attribute_model_input.py', 'erp_web/schemas/category_attribute.py', 'erp_web/schemas/category_attribute_validation.py', 'erp_web/schemas/category_attribute_evidence.py', 'erp_web/schemas/category_attribute_feature_evidence.py', 'erp_web/schemas/category_attribute_sku_scope.py', 'erp_web/schemas/attribute_image_evidence.py', 'config/prompts/category_attribute_fill.json', 'front/src/components/domain/DraftSkuAttributeBatchFill.vue']:
        assert not (ROOT / path).exists(), path
    for directory in (ROOT / "erp_web", ROOT / "front/src"):
        for path in directory.rglob("*"):
            if path.suffix not in {".py", ".ts", ".vue"} or "__tests__" in path.parts:
                continue
            source = path.read_text()
            assert not any(name in source for name in retired_tools | {"fillAttributesByAi", "fillCategoryAttributes", "/api/category-ai-fill"}), path


def test_stop_uses_native_cancellation_instead_of_a_queued_prompt():
    from pydantic_ai import CancellationToken
    from erp_web.http_route_units.ai_chat_routes import POST_HANDLERS, CHAT_CANCEL_PATH
    from erp_web.schemas.requests import REQUEST_CONTRACTS
    from erp_web.services.ai_chat_run_registry import AiChatRunRegistry

    assert CHAT_CANCEL_PATH in POST_HANDLERS and CHAT_CANCEL_PATH in REQUEST_CONTRACTS
    assert isinstance(AiChatRunRegistry().token("conversation"), CancellationToken)
    factory = (ROOT / "erp_web/services/ai_agent_factory.py").read_text()
    assert "cancellation_token=current_cancellation_token()" in factory
    assert "except RunCancelled" in factory
    front = (ROOT / "front/src/stores/aiChat.ts").read_text()
    assert "cancelChatRun(id, messageId)" in front
    assert "instance.stop()" in front
    for path in ("front/src/stores/aiChat.ts", "erp_web/services/agent_run_storage.py", "erp_web/services/vercel_ai_ui_service.py"):
        assert "取消当前操作" not in (ROOT / path).read_text()
