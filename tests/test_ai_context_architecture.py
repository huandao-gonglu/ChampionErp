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
import re

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
        "erp_web/services/ai_approval_policy.py",
        "erp_web/services/chat_user_input.py",
        "erp_web/services/global_agent_chat_service.py",
        "erp_web/services/agent_job_service.py",
        "erp_web/services/agent_run_storage.py",
        "erp_web/stores/agent_call_store.py",
        "erp_web/services/vercel_ai_ui_service.py",
    }
)


def test_wechat_is_transport_only_and_jobs_use_shared_native_run():
    """微信不装配模型或工具；历史和恢复继续走同一原生入口。"""
    paths = [ROOT / "erp_web/services/wechat_channel_service.py", ROOT / "erp_web/stores/wechat_channel_store.py"]
    targets = {target for _, target in imported_targets(paths)}
    forbidden = {"openai", "erp_web.services.ai_agent_factory", "erp_web.services.ai_tool_runtime",
                 "erp_web.facades.agent_capability_facade", "erp_web.services.vercel_ai_ui_service"}
    assert not targets & forbidden
    assert "messages_json" not in paths[1].read_text()
    job = (ROOT / "erp_web/services/agent_job_service.py").read_text()
    assert "ChatConversationRun" in job and "VercelAiChatRun" not in job
    from erp_web.http_route_units.wechat_routes import POST_HANDLERS
    from erp_web.schemas.requests import REQUEST_CONTRACTS
    assert set(POST_HANDLERS) <= REQUEST_CONTRACTS.keys()
    assert set(POST_HANDLERS) == {"/api/wechat/login", "/api/wechat/verify", "/api/wechat/unbind", "/api/wechat/settings"}


def test_image_batch_keeps_native_model_boundary_and_domain_only_progress():
    """图片领域调度不能引入 SDK 旁路、第二份消息表或自编 SSE。"""
    batch = ROOT / "erp_web/services/image_batch_service.py"
    store = ROOT / "erp_web/stores/image_batch_store.py"
    targets = {target for _, target in imported_targets([batch, store])}
    assert not any(target.split(".")[0] in {"openai", "httpx", "httpx2", "requests"} for target in targets)
    assert "messages_json" not in store.read_text()
    assert "publish_chunk" not in batch.read_text()
    direct = (ROOT / "erp_web/services/ai_direct_request_service.py").read_text()
    assert "direct.model_request(" in direct
    assert "current_history_sink" in direct
    from erp_web.http_route_units.ai_work_routes import DYNAMIC_GET_HANDLERS
    assert "/api/v1/ai-work/conversations/<conversation_id>/image-batch" in DYNAMIC_GET_HANDLERS
    from erp_web.http_route_units.image_routes import IMAGE_POST_PATHS
    from erp_web.schemas.requests import REQUEST_CONTRACTS
    for path in ("/api/draft-images/state", "/api/draft-images/process", "/api/draft-images/plan-set", "/api/draft-images/generate-set"):
        assert path in IMAGE_POST_PATHS and path in REQUEST_CONTRACTS
    facade = (ROOT / "erp_web/facades/image_facade.py").read_text()
    assert "run_image_batch(" in facade
    assert "image_draft_guard" in (ROOT / "erp_web/stores/product_store.py").read_text()


def test_image_set_uses_native_agent_and_existing_image_execution():
    """套图仅增加设计语义，共用模型、批次、素材池与草稿保存边界。"""
    source = (ROOT / "erp_web/services/image_set_service.py").read_text()
    assert "AiAgentFactory(" in source and "PromptedOutput(ImageSetPlan)" in source
    assert "BinaryContent(" in source and "image_translate_service.edit_images(" in source
    targets = {target for _, target in imported_targets([ROOT / "erp_web/services/image_set_service.py"])}
    assert not any(target.startswith(("openai", "httpx", "sqlite3", "requests")) for target in targets)


def test_draft_image_preparation_reuses_native_deferred_and_domain_owners():
    """整套图片准备复用原生后台工具、图片批次与商品 Store，不增加执行协议。"""
    from erp_web.ai_capability_composition import APPLICATION_CAPABILITY_CATALOG, _WRITE_CAPABILITIES
    tool = APPLICATION_CAPABILITY_CATALOG.tools["draft_images_prepare"].definition
    assert tool.execution_mode == "sync"
    assert APPLICATION_CAPABILITY_CATALOG.tools["draft_images_wait"].definition.execution_mode == "persistent_job"
    assert "draft_images_prepare" in _WRITE_CAPABILITIES
    source = (ROOT / "erp_web/runtime_units/draft_image_prepare.py").read_text()
    assert "run_image_batch(" in source and "products.save_draft_content(" in source
    targets = {target for _, target in imported_targets([ROOT / "erp_web/runtime_units/draft_image_prepare.py"])}
    assert not any(target.startswith(("openai", "httpx", "sqlite3", "requests")) for target in targets)


def test_collection_and_ai_share_browser_process_management():
    """采集与 AI 只选择自己的资料目录，浏览器启动由同一服务负责。"""
    for name in ("collection.py", "browser_ai_runtime.py"):
        source = (ROOT / "desktop_runtime" / name).read_text()
        assert "ensure_browser_debug_session(" in source
        assert "subprocess.Popen" not in source
    for path in (ROOT / "desktop_runtime").glob("*.py"):
        if path.name != "browser_debug_service.py":
            assert "Browser.close" not in path.read_text() and "Target.closeTarget" not in path.read_text()


def test_canvas_images_are_an_explicit_browser_transport_without_api_or_agent_owner():
    """Canvas 只适配浏览器图片能力，不装配 API 客户端、Agent 或另一套任务库。"""
    path = ROOT / "erp_web/services/gemini_canvas_runtime.py"
    targets = {target for _, target in imported_targets([path])}
    forbidden = ("openai", "google.genai", "httpx", "requests", "sqlite3", "pydantic_ai",
                 "erp_web.services.ai_direct_request_service", "erp_web.services.ai_agent_factory",
                 "erp_web.stores")
    assert not any(target.startswith(forbidden) for target in targets)
    from erp_web.services.ai_gateway_providers import AI_PROVIDER_REGISTRY
    from erp_web.services.ai_provider_contracts import AiImageEditProvider
    from erp_web.services.gemini_canvas_runtime import GeminiCanvasProvider
    assert any(isinstance(provider, GeminiCanvasProvider) for provider in AI_PROVIDER_REGISTRY)
    assert issubclass(GeminiCanvasProvider, AiImageEditProvider)


def test_gemini_login_wait_is_browser_lifecycle_not_an_agent_or_job_protocol():
    path = ROOT / "desktop_runtime/gemini_browser_session.py"
    targets = {target for _, target in imported_targets([path])}
    forbidden = ("pydantic_ai", "openai", "httpx", "requests", "sqlite3", "erp_web.stores",
                 "erp_web.runtime_units", "erp_web.services.ai_agent_factory")
    assert not any(target.startswith(forbidden) for target in targets)
    source = path.read_text()
    assert "EmbeddedPage" in source and "connect_browser_ai_page" not in source
    assert "generateContent" not in source and "create_task" not in source


def test_purchase_payment_has_explicit_command_and_approved_agent_entry():
    """签约只读检查，真实支付必须经过显式 HTTP 命令或原生审批工具。"""
    from erp_web.ai_capability_coverage import AI_CAPABILITY_COVERAGE_MANIFEST
    from erp_web.http_route_units.order_routes import POST_HANDLERS, GET_HANDLERS
    from erp_web.schemas.requests import REQUEST_CONTRACTS

    for suffix in ('pay', 'payment-status'):
        path = '/api/orders/alibaba-purchase/' + suffix
        assert path in POST_HANDLERS and path not in GET_HANDLERS
        assert path in REQUEST_CONTRACTS
        entries = [entry for entry in AI_CAPABILITY_COVERAGE_MANIFEST if entry.path == path]
        assert len(entries) == 1 and entries[0].disposition == 'capability'
    contract = REQUEST_CONTRACTS['/api/orders/alibaba-purchase/pay']
    assert set(contract.fields) == {'preview_id', 'confirmed_total_fen'}
    assert set(contract.required) == {'preview_id', 'confirmed_total_fen'}
    status = REQUEST_CONTRACTS['/api/orders/alibaba-purchase/payment-status']
    assert status.required == ('pay_channel',)
    assert status.fields['pay_channel'].choices == frozenset({'alipay', 'shegou'})


def test_retired_yunexpress_has_no_config_routes_or_agent_tools():
    """云途入口已退役，配置、HTTP 与主 Agent 不得继续暴露相关功能。"""
    from erp_web.ai_capability_composition import APPLICATION_CAPABILITY_CATALOG
    from erp_web.app_config import default_app_config, normalize_app_config
    from erp_web.http_routes import POST_API_ROUTES
    from erp_web.schemas.requests import REQUEST_CONTRACTS

    assert "yunexpress" not in default_app_config()
    assert "yunexpress" not in normalize_app_config({})
    for paths in (POST_API_ROUTES, REQUEST_CONTRACTS):
        assert not any(path.startswith("/api/logistics/yunexpress/") for path in paths)
    assert not any(
        name.startswith("logistics_shipment_")
        for name in APPLICATION_CAPABILITY_CATALOG.tools
    )
    for path in (
        "erp_web/runtime_units/yunexpress_client.py",
        "erp_web/runtime_units/logistics_capabilities.py",
        "erp_web/schemas/logistics_capabilities.py",
        "erp_web/facades/logistics_facade.py",
        "erp_web/http_route_units/logistics_routes.py",
    ):
        assert not (ROOT / path).exists(), path


def test_international_shipping_has_no_erp_or_agent_dependencies():
    """物流模块只接收参数，不读取 ERP 账号、启动 Agent 或维护第二套核价流程。"""
    folder = ROOT / "erp_web/international_shipping"
    for path in python_files(folder):
        tree = parse_python(path)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            assert not any(name.startswith(("erp_web", "sqlite3", "pydantic_ai")) for name in names), path
    pricing = (ROOT / "erp_web/services/pricing_service.py").read_text()
    assert "ML_SHIPPING_FALLBACK_TABLE" not in pricing
    assert "estimate_ml_shipping_usd" not in pricing
    assert not (ROOT / "erp_web/http_route_units/tariff_routes.py").exists()


def test_category_text_format_is_bounded_metadata_without_platform_ids_in_editor():
    """AI 和编辑器共享有界格式摘要，规则 helper 不依赖持久化或平台请求。"""
    from erp_web.schemas.category_definition import CategoryAttributeSummary

    fields = CategoryAttributeSummary.model_fields
    assert {"text_format", "format_hint"}.issubset(fields)
    assert "constraints" not in fields
    editor = (ROOT / "front/src/components/domain/CategoryAttributesPanel.vue").read_text()
    assert "57046341" not in editor
    imports = imported_targets([ROOT / "erp_web/schemas/category.py"])
    assert not any(target.startswith(("erp_web.runtime_units", "erp_web.stores", "sqlite3", "urllib")) for _, target in imports)


def _relative_posix(path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_frontend_backdrops_do_not_close_from_a_single_self_event():
    """新增弹窗不能绕过公共的按下/松开来源校验，重新引入拖选误关。"""
    binding = re.compile(r"(?:@|v-on:)(?:click|mousedown|mouseup|pointerdown|pointerup)((?:\.[\w-]+)*)\s*=")
    violations = []
    for path in (ROOT / "front/src").rglob("*.vue"):
        for match in binding.finditer(path.read_text(encoding="utf-8")):
            if "self" in match.group(1).split("."):
                violations.append(f"{_relative_posix(path)}: {match.group()}")
    assert not violations, "遮罩关闭须使用 WorkspaceDialog 或 useBackdropDismiss：\n" + "\n".join(violations)


def test_agent_instructions_have_separate_system_and_user_owners():
    """系统规则只能来自发布资源，用户偏好只由设置入口写入。"""
    consumers = {
        _relative_posix(path)
        for path, target in imported_targets(python_files(ROOT / "erp_web"))
        if target.startswith("erp_web.services.agent_instructions")
    }
    assert consumers == {"erp_web/services/global_agent_chat_service.py"}
    source = (ROOT / "erp_web/services/ai_prompt_templates.py").read_text()
    assert "app_dir" not in source and "write_prompt_json" not in source
    assert not (ROOT / "erp_web/services/agent_memory.py").exists()
    for path in (ROOT / "erp_web/runtime_units").glob("*.py"):
        assert "save_user_agents" not in path.read_text(), path
    assert not (ROOT / "erp_web/assets/account-defaults/prompts").exists()
    assert not (ROOT / "config/agents.md").exists()


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


def test_chat_permissions_are_not_inferred_from_conversation():
    """权限由代码目录与 Runtime 决定，不允许恢复按回合推断权限的第二模型。"""
    assert not (ROOT / "erp_web/services/chat_operation_scope.py").exists()
    forbidden = {"allowed_write_tools", "scope_resolver", "resolve_chat_operation_scope", "ChatOperationScope"}
    for path in python_files(ROOT / "erp_web"):
        for node in ast.walk(parse_python(path)):
            if isinstance(node, ast.Name):
                assert node.id not in forbidden, path
            elif isinstance(node, ast.Attribute):
                assert node.attr not in forbidden, path
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in forbidden, path
    runtime = (ROOT / "erp_web/services/ai_tool_runtime.py").read_text()
    assert "definition.required_permission not in execution.permissions" in runtime
    assert 'definition.side_effect == "write" and not execution.allow_write' in runtime


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
        "runtime_units/category_attribute_access.py",
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
    assert "PromptedOutput(output_type)" in service_source
    assert "ai_gateway.chat_json(" not in service_source

    prompt_config = json.loads(
        (ROOT / "erp_web/assets/prompts/copy_generate.json").read_text(encoding="utf-8")
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


def test_copy_generation_has_no_extra_ai_review() -> None:
    """文案只通过原生 Agent 生成，格式有效后不再调用模型复核。"""
    import inspect
    from erp_web.services import copy_service
    from erp_web.schemas import copy as copy_schema

    generation = inspect.getsource(copy_service.generate_copy)
    service = inspect.getsource(copy_service)
    assert "AiAgentFactory(" in generation
    assert "PromptedOutput(output_type)" in generation
    assert "output_validator=" not in generation
    assert "ai_gateway" not in service
    for retired in ("CopyOutputValidator", "review_copy_quality", "COPY_QUALITY_REJECTED"):
        assert not hasattr(copy_service, retired)
        assert retired not in service
    assert not hasattr(copy_schema, "CopyQualityReview")
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


def test_online_management_has_one_entry_and_retires_local_publication_routes() -> None:
    """在线管理只有持久化远端快照入口，不允许旧本地列表或暂停旁路。"""

    routes = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "erp_web/http_route_units/get_routes.py",
            "erp_web/http_route_units/online_product_routes.py",
            "erp_web/http_route_units/publish_routes.py",
            "erp_web/schemas/requests.py",
        )
    )
    assert "/api/online-products" in routes
    assert "/api/online-products/change" in routes
    assert "/api/online-products/change-batch" in routes
    assert "/api/online-products/change-batch/preview" in routes
    assert "/api/mercadolibre/user-products" not in routes
    assert "/api/mercadolibre/pause-user-product" not in routes
    for retired in (
        "/api/mercadolibre/published-items",
        "/api/mercadolibre/close-item",
        "/api/mercadolibre/confirm-real-publish",
    ):
        assert retired not in routes


def test_online_batch_reuses_domain_queue_and_platform_batch_adapter():
    """批量写入不另建执行线程或在 HTTP 中发送平台修改。"""
    source = (ROOT / "erp_web/services/online_product_batch.py").read_text()
    for required in ("enqueue_batch", "dispatch_batch", "validate_changes", "write_batch", "submission_status"):
        assert required in source
    for forbidden in ("runtime_units", "Thread(", "requests.", "sqlite3", "Agent("):
        assert forbidden not in source
    store = (ROOT / "erp_web/stores/online_product_store.py").read_text()
    assert "def dispatch_batch" in store and "self._assert_lease(conn, job)" in store


def test_online_ai_exposes_existing_resource_interfaces_without_scenario_tools():
    from erp_web.ai_capability_composition import APPLICATION_CAPABILITY_CATALOG
    expected = {"online_products_read", "online_products_attributes", "online_products_refresh_status", "online_products_refresh_detail", "online_products_change", "online_products_changes_apply", "online_products_job_read", "online_products_sync", "online_products_reconcile", "online_products_close", "online_products_retry"}
    assert {name for name in APPLICATION_CAPABILITY_CATALOG.tools if name.startswith("online_")} == expected
    source = (ROOT / "erp_web/runtime_units/online_product_capabilities.py").read_text()
    assert "online_product_service import OnlineProductService" in source
    assert "@ai_tool(" in source
    for forbidden in ("requests.", "urllib", "sqlite3", "adapter.write", "Agent(", "while True"):
        assert forbidden not in source
    detail_reader = ROOT / "erp_web/services/online_product_detail.py"
    for _, name in imported_targets([detail_reader]):
        assert not name.startswith(("erp_web.runtime_units", "erp_web.services.ai_", "pydantic_ai"))


def test_online_ai_lists_use_bounded_projection_instead_of_page_payload():
    """工具列表不能重新携带整组详情或历史任务；单件详情保留公开业务契约。"""
    from erp_web.schemas.online_product_capabilities import OnlineReadRequest, OnlineReadResult
    request = OnlineReadRequest.model_json_schema()["properties"]
    assert request["limit"]["minimum"] == 1 and request["limit"]["maximum"] == 50
    assert request["fields"]["items"]["type"] == "string" and "enum" not in request["fields"]["items"]
    schema = OnlineReadResult.model_json_schema()
    assert "jobs" not in schema["properties"]
    summary = schema["$defs"]["OnlineListingSummary"]["properties"]
    assert not {"content", "snapshot", "capabilities"} & summary.keys()
    projection = schema["$defs"]["OnlineProductFields"]["properties"]
    assert {"id", "group_id", "version", "synced_at", "details_state", "errors", "values", "missing_fields"} <= projection.keys()
    assert "snapshot" not in projection and "records" in schema["properties"]
    assert "item_ids" not in schema["$defs"]["OnlineGroupSummary"]["properties"]
    sync = schema["$defs"]["OnlineSyncSummary"]["properties"]
    assert not {"items", "request", "result"} & sync.keys()
    source = (ROOT / "erp_web/runtime_units/online_product_capabilities.py").read_text()
    assert "_call(service.read_page," in source
    assert "_call(service.list," not in source


def test_online_changes_complete_on_submission_without_remote_job_wait():
    """修改仍走原生审批/持久投递，但回执不能把 Agent 绑定到平台终态。"""
    from erp_web.schemas.online_product_capabilities import OnlineSubmissionResult
    from erp_web.runtime_units.online_product_capabilities import online_products_change, online_products_retry
    from typing import get_type_hints
    for capability in (online_products_change, online_products_retry):
        assert get_type_hints(capability)["return"] is OnlineSubmissionResult
    fields = OnlineSubmissionResult.model_fields
    assert "accepted" in fields and "job_id" in fields
    assert "job_type" not in fields and "evidence" not in fields


def test_online_completion_reuses_native_validation_without_another_agent_loop():
    """完成检查仅读原生回执，领域恢复和写入仍由既有工具执行。"""
    source = (ROOT / "erp_web/services/online_product_completion.py").read_text()
    for required in ("ModelRetry", "UserPromptPart", "ToolReturnPart", "remaining_seconds"):
        assert required in source
    for forbidden in ("Agent(", "run_sync(", "run_stream", "sqlite3", "while ", "re.search", "re.match"):
        assert forbidden not in source
    chat = (ROOT / "erp_web/services/global_agent_chat_service.py").read_text()
    assert "output_validator=validate_chat_output" in chat
    assert "return validate_online_completion(ctx, output)" in chat
    for file in ("ai_agent_factory.py", "ai_tool_runtime.py", "ai_tool_catalog.py", "ai_tool_declaration.py"):
        assert "online_product_completion" not in (ROOT / "erp_web/services" / file).read_text()


def test_responses_stream_guard_only_validates_decoded_native_events():
    """错误补丁限于 SDK 事件检查，不重新实现传输、消息编码或 Agent 执行。"""
    path = ROOT / "erp_web/services/ai_responses_stream.py"
    source = path.read_text()
    targets = {name.split(".")[0] for _, name in imported_targets([path])}
    assert targets <= {"collections", "openai", "pydantic_ai"}
    for forbidden in ("json.loads", "json.dumps", "httpx", "Agent(", "ModelRetry", "request_stream(", "sleep("):
        assert forbidden not in source
    assert "super()._process_streamed_response" in source


def test_online_operation_policy_is_pure_and_shared_by_execution_and_store():
    """回执与冲突语义不能由 HTTP、AI 或后台各自复制，也不能发起额外平台请求。"""
    model = ROOT / "erp_web/product_model/online_operation_model.py"
    targets = {name for _, name in imported_targets([model])}
    assert {name.split(".")[0] for name in targets} <= {"__future__", "typing"}
    for path in ("services/online_product_service.py", "stores/online_product_store.py"):
        targets = {name for _, name in imported_targets([ROOT / "erp_web" / path])}
        assert any(name.startswith("erp_web.product_model.online_operation_model.") for name in targets)


def test_online_source_images_use_existing_delivery_and_upload_boundaries():
    """源选图只负责业务关联；网络上传与 HTTPS 交付复用已有边界。"""
    routes = (ROOT / "erp_web/http_route_units/online_product_routes.py").read_text()
    assert "/api/online-products/source-images" in routes
    images = (ROOT / "erp_web/services/online_product_images.py").read_text()
    assert "image_delivery.prepare_product" in images
    assert not any(name.startswith("erp_web.runtime_units") for _, name in imported_targets([ROOT / "erp_web/services/online_product_images.py"]))
    upload = (ROOT / "erp_web/runtime_units/online_mercadolibre_images.py").read_text()
    assert "from erp_web.marketplaces.config_http import upload_mercadolibre_picture" in upload
    assert "safe_image_urlopen" in upload


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
    from erp_web.schemas.draft_pricing import DraftPricingResult
    from erp_web.schemas.product_write_capabilities import (
        DraftDuplicateResult,
        DraftSkuSelectionUpdateResult,
        DraftSaveResult,
        ProductProfilePatchResult,
        ProductSaveResult,
    )

    for model in (
        DraftDuplicateResult,
        DraftSkuSelectionUpdateResult,
        ProductSaveResult,
        DraftSaveResult,
        ProductProfilePatchResult,
        DraftPricingResult,
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
        "draft_duplicate",
        "draft_sku_selection_update",
        "product_sku_facts_update",
        "draft_package_estimates_confirm",
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


def test_python_execution_uses_official_code_mode_at_one_boundary() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "pydantic-ai-harness[codemode]==" in requirements
    owners = {
        _relative_posix(path)
        for path, target in imported_targets(python_files(ROOT / "erp_web"))
        if target.startswith("pydantic_ai_harness")
    }
    assert owners == {"erp_web/services/ai_code_mode.py"}
    source = (ROOT / "erp_web/services/ai_code_mode.py").read_text(encoding="utf-8")
    assert "CodeMode(" in source and "check_before_tool_call" in source
    assert "definition.approval_required" in source
    assert 'definition.execution_mode != "persistent_job"' in source
    assert not any(name in source for name in ("exec(", "eval(", "subprocess", "sqlite3", "runtime_units", "draft_id", "sku_id"))
    # 两种入口只调整可见工具，不能重新实现官方执行、重试或 REPL 生命周期。
    tree = parse_python(ROOT / "erp_web/services/ai_code_mode.py")
    wrapper = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DirectAndPythonToolset")
    assert {node.name for node in wrapper.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))} == {"get_tools"}
    from erp_web.services.global_agent_chat_service import GLOBAL_CHAT_PROFILE
    assert GLOBAL_CHAT_PROFILE.python_tools


def test_history_projection_uses_official_model_request_hook() -> None:
    source = (ROOT / "erp_web/services/ai_agent_factory.py").read_text(encoding="utf-8")
    assert "model_request=project_model_request" in source
    assert "ProcessHistory(" not in source, "请求投影不能替换用于审计的完整原生 run 历史。"
    # 不得调用私有 _agent_graph 或读取 Pydantic 内部 new_message_index。
    assert "_agent_graph" not in source
    assert "new_message_index" not in source


def test_tool_discovery_and_result_projection_keep_generic_boundaries() -> None:
    factory = (ROOT / "erp_web/services/ai_agent_factory.py").read_text(encoding="utf-8")
    assert "ToolSearch(strategy=match_tool_queries, max_results=3)" in factory
    bridge = (ROOT / "erp_web/services/ai_tool_bridge.py").read_text(encoding="utf-8")
    assert "tool.defer_loading = self.toolset.defer_loading" in bridge
    assert "native.prepared(toolset.prepare_tools)" in bridge
    for generic in (factory, bridge):
        assert "agent_tool_visibility" not in generic and "draft_package_estimates_" not in generic
    for name in ("ai_tool_discovery.py", "ai_tool_result_projection.py"):
        source = (ROOT / "erp_web/services" / name).read_text(encoding="utf-8")
        assert not any(value in source for value in ("runtime_units", "facades", "sqlite3", "yandex", "ozon", "exec(", "eval("))


def test_internal_draft_operations_do_not_build_page_responses() -> None:
    """内部属性、图片、文案和核价复用 Store 内容入口，页面另行组装。"""
    for relative in (
        "erp_web/runtime_units/product_capabilities.py",
        "erp_web/runtime_units/draft_changes_capability.py",
        "erp_web/runtime_units/market_capability_support.py",
        "erp_web/runtime_units/market_prepare_capabilities.py",
        "erp_web/runtime_units/draft_pricing.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "load_draft_detail_from_index" not in source, relative
        assert "save_draft_detail" not in source, relative
    source = (ROOT / "erp_web/stores/product_store.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    store = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ProductStore")
    methods = {node.name: node for node in store.body if isinstance(node, ast.FunctionDef)}
    for name in ("load_draft_content", "save_draft_content"):
        called = {node.func.attr for node in ast.walk(methods[name]) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        assert not called.intersection({"load_products_index", "load_drafts_index", "draft_product_context", "_draft_detail_response"})


def test_grouped_draft_changes_remain_a_business_capability() -> None:
    """批量保存不另建脚本执行器、数据库写入或 Agent 循环。"""
    from erp_web.ai_capability_composition import APPLICATION_CAPABILITY_CATALOG, _WRITE_CAPABILITIES
    assert "draft_changes_apply" in _WRITE_CAPABILITIES
    tool = APPLICATION_CAPABILITY_CATALOG.tools["draft_changes_apply"]
    assert tool.definition.required_permission == "draft.write"
    source = (ROOT / "erp_web/runtime_units/draft_changes_capability.py").read_text(encoding="utf-8")
    assert "save_draft_content" in source and "mutation_scope" in source
    assert "expected_updated_at" in source
    assert not any(name in source for name in ("pydantic_ai", "sqlite3", "upsert_draft_model", "exec(", "eval("))


def test_publish_confirmation_has_one_submission_and_one_read_entry() -> None:
    from erp_web.http_route_units.publish_routes import POST_HANDLERS
    from erp_web.ai_capability_composition import APPLICATION_CAPABILITY_CATALOG

    assert "/api/publish-product" not in POST_HANDLERS
    assert "product_publish_direct" not in APPLICATION_CAPABILITY_CATALOG.tools
    assert "/api/publish-bus/reconcile" in POST_HANDLERS
    source = (ROOT / "erp_web/runtime_units/publish_result_confirmation.py").read_text()
    assert "poll_publish_status" in source
    assert "publish_payload(" not in source
    assert "pydantic_ai" not in source
    frontend = (ROOT / "front/src/views/workflow/WorkflowView.vue").read_text()
    assert "publishJobsPollTimer" not in frontend


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


def test_attribute_query_and_write_share_access_rules():
    for name in ("category_query_capabilities.py", "category_attribute_updates.py"):
        assert any(
            target == "erp_web.runtime_units.category_attribute_access.attribute_write_scope"
            for _, target in imported_targets([ROOT / "erp_web/runtime_units" / name])
        )


def test_attribute_ai_has_only_the_main_conversation_entry():
    from erp_web.ai_capability_composition import APPLICATION_CAPABILITY_CATALOG, GLOBAL_CHAT_CAPABILITIES, _WRITE_CAPABILITIES
    from erp_web.http_route_units.category_routes import HANDLED_PATHS
    from erp_web.schemas.requests import REQUEST_CONTRACTS
    from erp_web.services.ai_model_config import AI_USE_CASES
    from erp_web.services.ai_prompt_templates import SYSTEM_PROMPT_FILES

    retired_tools = {"product_attributes_fill", "draft_sku_attributes_fill", "category_attribute_values_search"}
    assert not retired_tools.intersection(APPLICATION_CAPABILITY_CATALOG.tools)
    assert {"draft_attributes_read", "draft_sku_variants_check", "category_attributes_query", "category_attribute_values_query"} <= GLOBAL_CHAT_CAPABILITIES
    assert {"product_attributes_update", "draft_sku_attributes_update"} <= _WRITE_CAPABILITIES
    assert "/api/category-ai-fill" not in HANDLED_PATHS | REQUEST_CONTRACTS.keys()
    assert "category.attribute_fill" not in AI_USE_CASES.keys() | SYSTEM_PROMPT_FILES.keys()
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


def test_description_belongs_only_to_drafts():
    """旧卖点仅可在持久化迁移与拒绝旧契约的边界出现。"""
    allowed = {
        "erp_web/stores/product_description_migration.py",
        "erp_web/product_model/merge_model.py",
    }
    retired = {"selling_points", "bullets", "bullet_points", "include_bullets", "bullets_found_count", "description_found"}
    for path in python_files(ROOT / "erp_web"):
        if _relative_posix(path) in allowed:
            continue
        for node in ast.walk(parse_python(path)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in retired, (path, node.lineno, node.value)
    for relative in (
        "front/src/components/domain/ProductEditorPanel.vue",
        "front/src/components/domain/DraftEditorPanel.vue",
        "front/src/components/domain/CopyPanel.vue",
        "front/src/api/workflow/normalizers/product.ts",
    ):
        source = (ROOT / relative).read_text()
        assert "sellingPoints" not in source and ".bullets" not in source

    from erp_web.schemas.product import Product, ProductSource, PlatformDraft
    from erp_web.schemas.product_capabilities import ProductFacts
    from erp_web.schemas.product_write_capabilities import ProductProfilePatch

    for shape in (Product, ProductSource, ProductFacts, ProductProfilePatch):
        assert "description" not in shape.__annotations__
    assert "description" in PlatformDraft.__annotations__


def test_draft_pricing_has_one_business_entry_and_no_raw_ai_or_http_calculator() -> None:
    """页面、AI 与复合准备共享业务装配；通用脚本能力无需承担 SKU 取数核价。"""
    from erp_web.ai_capability_composition import APPLICATION_CAPABILITY_CATALOG
    from erp_web.http_route_units.product_routes import POST_HANDLERS
    assert "pricing_calculate" not in APPLICATION_CAPABILITY_CATALOG.tools
    assert {"draft_pricing_preview", "draft_pricing_apply"} <= APPLICATION_CAPABILITY_CATALOG.tools.keys()
    assert "/api/calculate-price" not in POST_HANDLERS
    assert {"/api/draft-pricing/preview", "/api/draft-pricing/apply", "/api/draft-pricing/restore"} <= POST_HANDLERS.keys()
    for relative in ("erp_web/facades/draft_pricing_facade.py", "erp_web/runtime_units/draft_pricing_capabilities.py", "erp_web/runtime_units/market_prepare_capabilities.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "from erp_web.runtime_units.draft_pricing import" in source
        assert "price_draft(" in source
    assert not (ROOT / "erp_web/runtime_units/market_pricing_capability.py").exists()
    front = (ROOT / "front/src/stores/workflow/actions/pricing.ts").read_text(encoding="utf-8")
    assert "priceDraft(" in front and "inputForSku" not in front and "saveDraftApi" not in front


def test_online_sync_uses_platform_readers_and_pure_snapshot_projection():
    """同步统一消费领域批次，平台负责批量或有限并发，投影层保持纯函数。"""
    from erp_web.runtime_units.online_mercadolibre import MercadoOnlineAdapter
    from erp_web.runtime_units.online_ozon import OzonOnlineAdapter
    from erp_web.runtime_units.online_yandex import YandexOnlineAdapter
    import inspect
    for adapter, entry in ((MercadoOnlineAdapter, "sync_mercado"), (OzonOnlineAdapter, "sync_ozon"), (YandexOnlineAdapter, "sync_yandex")):
        assert not hasattr(adapter, "discover")
        assert entry in inspect.getsource(adapter.sync)
    sync = (ROOT / "erp_web/services/online_product_sync.py").read_text()
    assert "adapter.sync(" in sync and "adapter.read(" not in sync
    shared = (ROOT / "erp_web/marketplaces/online_sync.py").read_text()
    assert "single_listing_batches" not in shared
    for platform in ("yandex", "ozon"):
        projection = ast.parse((ROOT / f"erp_web/runtime_units/online_{platform}_snapshot.py").read_text())
        imports = [node.module or "" for node in ast.walk(projection) if isinstance(node, ast.ImportFrom)]
        assert not any(any(part in module for part in ("http", "stores", "context", "service")) for module in imports)


def test_online_feedback_is_public_and_yandex_names_use_the_category_catalog():
    """卡片反馈不混入同步错误，属性名称走统一类目入口且投影保持纯函数。"""
    import inspect
    from erp_web.runtime_units.online_yandex import YandexOnlineAdapter
    from erp_web.schemas.online_products import OnlineProduct, OnlineStatus, PlatformIssue
    assert 'platform_issues' in OnlineProduct.model_fields
    assert 'platform_issues' in OnlineStatus.model_fields
    assert 'snapshot' not in OnlineProduct.model_fields
    assert set(PlatformIssue.model_fields) == {'severity', 'source', 'code', 'message', 'comment'}
    source = inspect.getsource(YandexOnlineAdapter.attribute_names)
    assert 'get_category_catalog().attribute_definitions' in source
    assert 'request(' not in source


def test_external_response_policy_is_pure_and_receives_interface_context():
    """响应规则不依赖业务运行时；接口身份从统一入口显式传入。"""
    policy = parse_python(ROOT / "erp_web/services/platform_request_policy.py")
    project_imports = [node.module for node in ast.walk(policy)
                       if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("erp_web")]
    assert project_imports == ["erp_web.schemas.external_requests"]
    manager = parse_python(ROOT / "erp_web/services/external_request_manager.py")
    calls = [node for node in ast.walk(manager) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "classify_response"]
    assert len(calls) == 1
    assert any(keyword.arg == "interface" and ast.unparse(keyword.value) == "ctx.interface"
               for keyword in calls[0].keywords)


def test_external_api_sends_have_one_managed_boundary():
    """业务 HTTP 保持受控；本机 CDP 与原生 AI 客户端具有独立传输边界。"""
    # 账号服务 RPC 属于共享控制面，不创建业务空间，也不写业务请求历史或保存认证票据。
    allowed = {"erp_web/services/external_request_manager.py", "erp_web/services/browser_debug_http.py", "erp_web/services/newapi_http.py",
               "erp_web/services/registration_verification.py"}
    violations = []
    for path in python_files(ROOT / "erp_web"):
        relative = _relative_posix(path)
        if relative in allowed:
            continue
        tree = parse_python(path)
        imports = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    imports[alias.asname or alias.name] = node.module + '.' + alias.name
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = ast.unparse(node.func)
                root, separator, rest = target.partition('.')
                target = imports.get(root, root) + (separator + rest if separator else '')
                if target in {"urllib.request.urlopen", "urllib.request.urlretrieve", "urllib.request.build_opener", "http.client.HTTPConnection", "http.client.HTTPSConnection", "urllib3.PoolManager"}:
                    violations.append(f"{relative}:{node.lineno}:{target}")
                if target.startswith(("requests.", "httpx.", "httpx2.", "aiohttp.")) and target.rsplit(".",1)[-1] in {"get","post","put","delete","request","stream","Client","AsyncClient","ClientSession","Session","AsyncHTTPTransport","HTTPTransport"} and root in imports:
                    if relative != "erp_web/services/ai_provider_catalog.py":
                        violations.append(f"{relative}:{node.lineno}:{target}")
    assert not violations, "外部发送必须经过统一管理："+"\n".join(violations)
    factory = (ROOT / "erp_web/services/ai_provider_catalog.py").read_text()
    assert "external_request" not in factory and "max_retries" not in factory
    assert "http_client=" not in factory
    assert not (ROOT / "erp_web/services/external_httpx_transport.py").exists()
    assert not (ROOT / "erp_web/services/external_request_diagnostics.py").exists()
    bus = (ROOT / "erp_web/runtime_units/publishing_bus_core.py").read_text()
    assert "max_retries" not in bus and "retry_delay_seconds" not in bus
    assert "fetch_ozon_shop_name" not in (ROOT / "erp_web/marketplaces/category_services.py").read_text()


def test_publish_preparation_is_an_explicit_write_tool():
    """图片准备使用现有原生工具边界，查询场景只暴露只读发布校验。"""
    from erp_web.ai_capability_composition import APPLICATION_CAPABILITY_CATALOG, GLOBAL_CHAT_CAPABILITIES, _WRITE_CAPABILITIES

    prepare = APPLICATION_CAPABILITY_CATALOG.tools["product_publish_prepare"].definition
    validate = APPLICATION_CAPABILITY_CATALOG.tools["product_publish_validate"].definition
    request = APPLICATION_CAPABILITY_CATALOG.tools["product_publish_request"].definition
    assert prepare.side_effect == "write"
    assert not prepare.approval_required
    assert validate.side_effect == "none"
    assert request.approval_required
    assert "product_publish_prepare" in _WRITE_CAPABILITIES
    assert "product_publish_prepare" not in GLOBAL_CHAT_CAPABILITIES


def test_publish_submission_ends_ai_operation_without_terminal_job_wait():
    """发布仅等待原生审批，队列接收后通过普通工具回执完成。"""
    from erp_web.ai_capability_composition import APPLICATION_CAPABILITY_CATALOG
    from erp_web.facades.agent_capability_facade import build_job_status_readers

    request = APPLICATION_CAPABILITY_CATALOG.tools["product_publish_request"].definition
    assert request.execution_mode == "sync"
    assert request.approval_required
    assert "job_type" not in request.output_schema.get("properties", {})
    assert "job_id" in request.output_schema["required"]
    assert "publish" not in build_job_status_readers()
    reader_source = (ROOT / "erp_web/runtime_units/domain_job_readers.py").read_text()
    assert "class PublishJobStatusReader" not in reader_source


def test_image_hosting_has_one_sdk_boundary_and_no_retired_delivery():
    """S3 只在交付边界装配；预检/worker 不上传，隧道路径彻底退役。"""
    sdk_imports = {
        _relative_posix(path)
        for path, target in imported_targets(python_files(ROOT / "erp_web"))
        if target.startswith(("botocore", "boto3"))
    }
    assert sdk_imports == {"erp_web/services/s3_image_storage.py"}
    assert not (ROOT / "scripts/image_https_tunnel.sh").exists()
    for path in [*(ROOT / "erp_web").rglob("*.py"), ROOT / "scripts/dev.sh", ROOT / "config/.env.example"]:
        source = path.read_text()
        for retired in ("prepare_is_local_only", "LocalStaticHttpsProvider", "ExistingUrlOnlyProvider", "ERP_IMAGE_HTTPS_", "local_static"):
            assert retired not in source, (path, retired)
    storage = (ROOT / "erp_web/services/s3_image_storage.py").read_text()
    assert '"before-send.s3"' in storage and "managed_urlopen" in storage
    assert '"total_max_attempts": 1' in storage
    assert 'request_checksum_calculation="when_required"' in storage
    config = (ROOT / "erp_web/services/image_hosting_config.py").read_text()
    assert not any(word in config for word in ("get_context", "urlopen", "sqlite3", "requests."))
    content = (ROOT / "erp_web/services/image_content.py").read_text()
    assert not any(word in content for word in ("get_context", "urlopen", "sqlite3", "requests.", "botocore"))
    facade = (ROOT / "erp_web/facades/image_hosting_facade.py").read_text()
    assert ".client." not in facade
    routes = (ROOT / "erp_web/http_route_units/image_hosting_routes.py").read_text()
    assert "runtime_units" not in routes
    assert routes.count("handler.read_body()") == routes.count("validate_request_payload(handler.read_body(), endpoint=handler.path)")


def test_draft_sales_facts_are_owned_only_by_skus() -> None:
    from erp_web.schemas.product import PlatformDraft
    from erp_web.schemas.product_write_capabilities import DraftReadView
    from erp_web.ai_capability_composition import _WRITE_CAPABILITIES
    from erp_web.runtime_units.product_write_capabilities import DRAFT_WRITE_AI_CAPABILITIES

    retired = {"sku", "stock", "upc", "package_dimensions", "publication"}
    assert not retired.intersection(PlatformDraft.__annotations__)
    assert not retired.intersection(DraftReadView.model_fields)
    assert "draft_stock_update" not in _WRITE_CAPABILITIES
    assert all(function.__name__ != "draft_stock_update" for function in DRAFT_WRITE_AI_CAPABILITIES)


def test_orders_have_one_persistent_pipeline_and_thin_routes():
    """三平台共用收件箱，页面读取不能推进平台请求，退役入口不得重建。"""
    root = ROOT / 'erp_web'
    assert not (root / 'runtime_units/mercadolibre_orders.py').exists()
    assert not (root / 'http_route_units/mercadolibre_routes.py').exists()
    route = root / 'http_route_units/order_routes.py'
    imports = imported_targets([route])
    assert not any(target.startswith('erp_web.runtime_units') for _, target in imports)
    service = (root / 'services/order_notification_service.py').read_text()
    receive = service.split('    def receive(', 1)[1].split('    def integrations(', 1)[0]
    assert 'self.store.receive(' in receive
    assert '.read(' not in receive and '.sync(' not in receive
    for path in [root / 'services/order_notification_service.py', root / 'stores/order_notification_store.py']:
        assert 'pydantic_ai' not in path.read_text()
    assert '/api/mercadolibre/orders' not in (ROOT / 'front/src/api/orders.ts').read_text()
    # 店铺发现只属于后台平台适配器；页面与账号范围读取不得外发请求。
    shops = service.split('    def shops(', 1)[1].split('    def save_yandex_shops(', 1)[0]
    assert 'self.store.setting(' in shops
    assert 'fetch_yandex' not in shops and 'request_yandex' not in shops
    for unit, dependency in [
        ('runtime_units/orders_yandex.py', 'erp_web.marketplaces.yandex_http.fetch_yandex_shop_names'),
        ('runtime_units/online_yandex.py', 'erp_web.marketplaces.yandex_http'),
    ]:
        targets = imported_targets([root / unit])
        assert any(target == dependency for _, target in targets)
        assert 'fetch_yandex_shop_names(' in (root / unit).read_text()
    ozon = (root / 'runtime_units/orders_ozon.py').read_text()
    assert '/v3/posting/fbs/list' not in ozon
    assert '/v2/posting/fbo/list' not in ozon


def test_order_procurement_keeps_routes_thin_and_dashboard_summary_only():
    route = ROOT / "erp_web/http_route_units/order_routes.py"
    assert not any(target.startswith("erp_web.runtime_units") for _, target in imported_targets([route]))
    dashboard = (ROOT / "front/src/views/workflow/DashboardView.vue").read_text()
    assert "OrderSummaryCard" in dashboard
    assert "OrderCenterPanel" not in dashboard
    assert not (ROOT / "front/src/components/domain/OrderNotificationsPanel.vue").exists()
    source = (ROOT / "erp_web/runtime_units/order_source_bindings.py").read_text()
    assert "approved_publications" in source
    assert "load_draft" not in source


def test_platform_labels_reuse_marketplace_transport_and_pdf_hosting_boundary():
    """面单读取不新建授权旁路、SDK 或 Agent 生命周期，HTTP 路由仅调用 facade。"""
    service = ROOT / "erp_web/services/platform_label_service.py"
    targets = {target for _, target in imported_targets([service])}
    assert "erp_web.marketplaces.yandex_http.request_yandex_pdf" in targets
    assert "erp_web.services.fulfillment_label_service.deliver_label_bytes" in targets
    assert not any(target.startswith(("erp_web.runtime_units", "pydantic_ai", "urllib", "boto")) for target in targets)
    route = ROOT / "erp_web/http_route_units/fulfillment_routes.py"
    assert not any(target.startswith(("erp_web.runtime_units", "erp_web.services")) for _, target in imported_targets([route]))
    source = service.read_text()
    assert 'box.get("fulfilmentId")' in source
    assert 'box.get("orderNum")' not in source


def test_order_platform_images_keep_online_reads_in_store_and_assembly_in_facade():
    """订单展示仅读取已同步图片，平台账号与持久化依赖由 facade 显式装配。"""
    service = ROOT / "erp_web/services/order_procurement_service.py"
    targets = {target for _, target in imported_targets([service])}
    assert not any(target.startswith(("erp_web.runtime_units", "erp_web.marketplaces", "sqlite3", "urllib", "httpx")) for target in targets)
    facade = ROOT / "erp_web/facades/order_procurement_facade.py"
    targets = {target for _, target in imported_targets([facade])}
    assert "erp_web.stores.online_product_store.OnlineProductStore" in targets
    assert "erp_web.runtime_units.online_yandex.YandexOnlineAdapter" not in targets


def test_order_query_uses_domain_facts_without_drafts_or_remote_adapters():
    service = ROOT / "erp_web/services/order_query_service.py"
    targets = {target for _, target in imported_targets([service])}
    assert not any(target.startswith(("erp_web.runtime_units", "erp_web.marketplaces", "sqlite3", "httpx", "pydantic_ai")) for target in targets)
    assert "load_draft" not in service.read_text()
    resolver = ROOT / "erp_web/services/order_source_resolution.py"
    targets = {target for _, target in imported_targets([resolver])}
    assert not any(target.startswith(("erp_web.stores", "erp_web.context", "erp_web.marketplaces")) for target in targets)
    procurement = (ROOT / "erp_web/services/order_procurement_service.py").read_text()
    assert "resolve_source_selection(" in procurement and "resolve_source_selection(" in service.read_text()


def test_buyer_chat_capabilities_reuse_domain_service_and_native_approval():
    unit = ROOT / "erp_web/runtime_units/order_chat_capabilities.py"
    targets = {target for _, target in imported_targets([unit])}
    assert "erp_web.services.order_chat_service.OrderChatService" in targets
    assert not any(target.startswith(("erp_web.marketplaces", "erp_web.context", "pydantic_ai", "sqlite3")) for target in targets)
    service = ROOT / "erp_web/services/order_chat_service.py"
    targets = {target for _, target in imported_targets([service])}
    assert not any(target.startswith(("erp_web.stores", "erp_web.context", "pydantic_ai")) for target in targets)
    source = (ROOT / "erp_web/facades/agent_capability_facade.py").read_text()
    assert "OrderChatCapabilityScope(service=lambda: order_chat_facade.create_service(context))" in source


def test_request_recovery_is_owned_by_system_settings_and_never_replays_business():
    route = ROOT / 'erp_web/http_route_units/external_request_routes.py'
    assert not any(target.startswith(('erp_web.runtime_units', 'erp_web.services', 'erp_web.stores')) for _, target in imported_targets([route]))
    service = ROOT / 'erp_web/services/external_request_control_service.py'
    assert not any(target.startswith(('erp_web.runtime_units', 'erp_web.marketplaces', 'urllib', 'httpx', 'pydantic_ai')) for _, target in imported_targets([service]))
    from erp_web.http_routes import POST_API_ROUTES
    assert '/api/external-requests/recover' in POST_API_ROUTES
    assert '/api/external-requests/history/clear' in POST_API_ROUTES
    assert '/api/orders/recover-sync' not in POST_API_ROUTES
    assert 'ExternalRequestControlPanel' in (ROOT / 'front/src/components/domain/SystemSettingsPanel.vue').read_text()
    assert 'ExternalRequestControlPanel' not in (ROOT / 'front/src/components/auth/AuthSettingsPanel.vue').read_text()
    assert 'recover-sync' not in (ROOT / 'front/src/components/domain/OrderSyncStatusPanel.vue').read_text()


def test_order_progress_sync_is_explicitly_composed_and_not_a_read_side_effect():
    root = ROOT / 'erp_web'
    service = root / 'services/order_notification_service.py'
    targets = imported_targets([service])
    assert not any('alibaba' in target or 'fulfillment' in target for _, target in targets)
    facade = (root / 'facades/order_notification_facade.py').read_text()
    assert 'progress_sync=refresh_progress' in facade
    reads = facade.split('def read_orders(', 1)[1].split('def _sync_status(', 1)[0]
    assert 'sync_order_progress(' not in reads
    source = service.read_text()
    assert source.index('self.store.save_snapshot(') < source.index('self.progress_sync(snapshot.identity, checkpoint)')


def test_order_center_query_reads_domain_owners_without_network_dependencies():
    source = ROOT / 'erp_web/services/order_center_query_service.py'
    targets = imported_targets([source])
    assert not any('runtime_units' in target or 'external_request' in target or '_client' in target
                   for _, target in targets)
    facade = (ROOT / 'erp_web/facades/order_notification_facade.py').read_text()
    assert 'OrderCenterQueryService(' in facade
    routes = (ROOT / 'erp_web/http_route_units/order_routes.py').read_text()
    assert 'ORDER_CENTER_FILTER_KEYS' in routes


def test_procurement_does_not_own_parcel_lifecycle():
    """采购只管理采购单；新增或作废不得改变独立包裹。"""
    from erp_web.http_route_units.order_routes import POST_HANDLERS
    from erp_web.schemas.requests import REQUEST_CONTRACTS
    for path in ('/api/orders/supplement-lookup', '/api/orders/supplement-record'):
        assert path not in POST_HANDLERS
        assert path not in REQUEST_CONTRACTS
    source = (ROOT / 'erp_web/stores/order_procurement_store.py').read_text()
    assert 'FulfillmentStore' not in source
    from erp_web.schemas.fulfillment import DomesticParcel
    from erp_web.schemas.order_procurement import PurchaseRegistration
    assert {'line_key', 'purchase_record_id'}.isdisjoint(DomesticParcel.model_fields)
    assert {'line_key', 'quantity', 'revision'}.isdisjoint(PurchaseRegistration.model_fields)
    assert 'UPDATE fulfillments' not in source
    route_targets = {target for _, target in imported_targets([ROOT / 'erp_web/http_route_units/order_routes.py'])}
    assert not any(target.startswith('erp_web.services') for target in route_targets)


def test_warranty_compiler_is_shared_by_publishing_and_online_changes():
    from erp_web.product_model.yandex_warranty_model import compile_yandex_guarantee_period
    from erp_web.runtime_units import publish_yandex, publish_validation, online_yandex
    from erp_web.services import online_product_changes
    for module in (publish_yandex, publish_validation, online_yandex, online_product_changes):
        assert module.compile_yandex_guarantee_period is compile_yandex_guarantee_period


def test_chat_product_images_use_separate_read_and_write_permissions():
    from erp_web.ai_capability_composition import APPLICATION_CAPABILITY_CATALOG, GLOBAL_CHAT_CAPABILITIES, _WRITE_CAPABILITIES

    read = APPLICATION_CAPABILITY_CATALOG.tools["product_image_read"].definition
    save = APPLICATION_CAPABILITY_CATALOG.tools["chat_images_save_to_product"].definition
    assert read.name in GLOBAL_CHAT_CAPABILITIES
    assert (read.side_effect, read.required_permission) == ("none", "product.read")
    assert save.name in _WRITE_CAPABILITIES and save.name not in GLOBAL_CHAT_CAPABILITIES
    assert (save.side_effect, save.required_permission) == ("write", "image.write")
    assert set(save.input_schema["properties"]) == {"product_id", "attachment_ids"}


def test_shared_business_queries_are_domain_independent_and_not_ai_specific():
    """查询引擎不调用 HTTP/Agent；页面和 AI 都绑定同一业务资源目录。"""
    engine = ROOT / 'erp_web/services/business_data.py'
    targets = {target for _, target in imported_targets([engine])}
    assert not any('runtime_units' in target or 'pydantic_ai' in target or 'facades' in target for target in targets)
    for relative in ('erp_web/facades/business_data_facade.py', 'erp_web/facades/agent_capability_facade.py'):
        assert 'build_business_data_catalog' in (ROOT / relative).read_text()
    from erp_web.business_query_composition import BUSINESS_QUERIES
    from erp_web.http_route_units import business_query_routes
    from erp_web.http_routes import GET_ROUTE_UNITS
    assert business_query_routes in GET_ROUTE_UNITS
    assert set(BUSINESS_QUERIES.http_queries) == set(business_query_routes.GET_HANDLERS)
    for route_unit in GET_ROUTE_UNITS:
        if route_unit is not business_query_routes:
            assert not set(BUSINESS_QUERIES.http_queries).intersection(getattr(route_unit, 'GET_API_ROUTES', ()))
    from erp_web.ai_capability_composition import GLOBAL_CHAT_CAPABILITIES
    assert {'business_data_catalog', 'business_data_read'} <= GLOBAL_CHAT_CAPABILITIES
    assert not any(name.startswith('shipping_tariff_') for name in GLOBAL_CHAT_CAPABILITIES)
