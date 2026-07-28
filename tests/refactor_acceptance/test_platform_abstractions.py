from __future__ import annotations

import importlib

from erp_web.context import get_context
from erp_web.facades import publish_facade
from erp_web.marketplace_registry import (
    CAP_CATEGORY_ATTRIBUTES,
    CAP_CATEGORY_SEARCH,
    CAP_PUBLISH,
    MARKETPLACE_SPECS,
    category_id_field,
    marketplace_spec,
    platform_has_capability,
    platform_preset_key,
    platform_title_limit,
)
from erp_web.marketplaces.publisher import PlatformPublisher
from erp_web.runtime_units import publish_adapter, runtime_api
from erp_web.runtime_units.category_providers import category_provider_for
from erp_web.runtime_units.source_sites import (
    SOURCE_SITES,
    detect_source_site,
    parse_source_snapshot,
    source_site,
)

from .helpers import (
    ROOT,
    dotted_name,
    forbidden_calls,
    format_findings,
    function_calls_name,
    function_definitions,
    imported_targets,
    platform_literal_branches,
    scoped_calls,
    string_literal_occurrences,
)

PLATFORM_LITERALS = {
    *(spec.key for spec in MARKETPLACE_SPECS),
    *(site.key for site in SOURCE_SITES),
    "wildberries",
}


# 验收：所有声明发布能力的平台都必须拥有独立 PlatformPublisher 实现，不能共用占位适配器。
def test_publish_capabilities_have_real_platform_publishers() -> None:
    required_methods = {
        "resolve_category",
        "required_attributes_missing",
        "validate_draft",
        "build_payload",
        "validate_payload",
        "publish",
    }
    assert required_methods.issubset(PlatformPublisher.__dict__)
    publishable = {
        spec.key for spec in MARKETPLACE_SPECS if CAP_PUBLISH in spec.capabilities
    }
    assert publishable, "注册表必须至少声明一个真实可发布平台"
    assert publishable == set(publish_adapter._PUBLISHERS)
    assert len({id(adapter) for adapter in publish_adapter._PUBLISHERS.values()}) == len(
        publish_adapter._PUBLISHERS
    )
    for platform, adapter in publish_adapter._PUBLISHERS.items():
        assert publish_adapter.publishing_adapter_for(platform) is adapter
        assert getattr(adapter, "platform", platform) == platform
        for method in required_methods:
            assert callable(getattr(adapter, method, None)), f"{platform} 缺少 {method}"


# 验收：平台能力必须集中声明在 registry，通用层只能通过 capability 查询。
def test_marketplace_registry_declares_capabilities() -> None:
    for spec in MARKETPLACE_SPECS:
        assert isinstance(spec.capabilities, frozenset)
        assert spec.key
        for capability in spec.capabilities:
            assert platform_has_capability(spec.key, capability)
    common_paths = [
        ROOT / path
        for path in (
            "erp_web/runtime_units/category_store.py",
            "erp_web/facades/publish_facade.py",
            "erp_web/runtime_units/runtime_api.py",
            "erp_web/runtime_units/image_pool_core.py",
            "erp_web/stores/product_store.py",
        )
    ]
    offenders = platform_literal_branches(common_paths, PLATFORM_LITERALS)
    assert not offenders, (
        "通用层仍绕过 capability registry 判断具体平台：\n"
        + format_findings(offenders)
    )


# 验收：采集源检测、解析、登录诊断和浏览器配置必须集中在 SourceSite 注册表。
def test_collection_sources_use_source_site_registry() -> None:
    for spec in SOURCE_SITES:
        assert spec.browser_profile
        assert spec.error_code("DEFAULT")
        assert callable(spec.parser)
        assert spec.required_quality_fields
    assert detect_source_site("https://detail.1688.com/offer/1.html") == "1688"
    assert detect_source_site("https://www.amazon.com/dp/TEST") == "amazon"
    assert detect_source_site("https://example.com/item/1") == "generic"

    amazon = source_site("amazon")
    flags, reason = amazon.diagnose(
        "https://www.amazon.com/errors/validateCaptcha",
        "Robot Check",
        "Enter the characters you see below",
        "Amazon CAPTCHA",
    )
    assert flags["is_captcha_page"] is True
    assert reason == "ROBOT"
    assert amazon.error_code(reason).startswith("AMAZON_")
    parsed = parse_source_snapshot(
        "generic",
        "<html><head><title>通用商品标题</title></head><body></body></html>",
        "https://example.com/item/1",
    )
    assert parsed["name"] == "通用商品标题"
    workflow_paths = [
        ROOT / path
        for path in (
            "erp_web/runtime_units/source_collect_workflows.py",
            "erp_web/runtime_units/source_collect.py",
            "erp_web/runtime_units/collect_helpers.py",
        )
    ]
    offenders = platform_literal_branches(workflow_paths, PLATFORM_LITERALS)
    assert not offenders, (
        "采集工作流仍绕过 SourceSite 写平台分支：\n"
        + format_findings(offenders)
    )


# 验收：HTTP、CLI、浏览器能力探测必须共享一个循环，不能保留三族平行 probe 函数。
def test_ai_capability_probes_are_provider_polymorphic() -> None:
    probe_definitions = function_definitions("erp_web/services/ai_gateway_probe.py")
    provider_definitions = function_definitions("erp_web/services/ai_gateway_providers.py")
    parallel_helpers = [
        definition.qualname
        for definition in provider_definitions
        if definition.qualname.split(".")[-1].startswith(
            ("_probe_http_", "_probe_cli_", "_probe_browser_")
        )
    ]
    assert any(
        definition.qualname == "run_capability_probes"
        for definition in probe_definitions
    )
    shared_loop = next(
        definition
        for definition in probe_definitions
        if definition.qualname == "run_capability_probes"
    )
    assert any(
        dotted_name(call.func) == "provider.probe_capability"
        for call in scoped_calls(shared_loop)
    )
    probe_methods = [
        definition
        for definition in provider_definitions
        if definition.qualname.split(".")[-1] == "probe_capability"
    ]
    assert len(probe_methods) >= 3
    probe_entrypoints = [
        definition
        for definition in provider_definitions
        if definition.qualname
        in {
            "probe_model_capabilities",
            "probe_cli_model_capabilities",
            "probe_browser_model_capabilities",
        }
    ]
    assert len(probe_entrypoints) == 3
    assert all(
        any(
            dotted_name(call.func).endswith("run_capability_probes")
            for call in scoped_calls(definition)
        )
        for definition in probe_entrypoints
    )
    assert not parallel_helpers, f"仍存在平行能力探测函数：{parallel_helpers}"


# 验收：chat/responses 的 body、流解析和 api_style 判断必须留在 Provider 类内，不能泄漏到共享函数。
def test_chat_and_responses_protocol_logic_is_provider_owned() -> None:
    callers = function_calls_name(
        "erp_web/services/ai_gateway_providers.py",
        "_model_api_style",
    )
    dispatch_only = {
        "_capability_profile",
        "_default_capability_profile",
        "_saved_capability_profile",
        "_provider_for_model",
    }
    leaked_callers = [
        caller
        for caller in callers
        if caller not in dispatch_only
        if not caller.startswith(("OpenAICompatibleProvider.", "OpenAIResponsesProvider."))
    ]
    assert not leaked_callers, f"共享函数仍在判断 api_style：{leaked_callers}"


# 验收：类目搜索和属性读取必须通过 CategoryProvider 统一入口派发。
def test_category_apis_use_category_provider_contract() -> None:
    for platform in ("mercadolibre", "ozon"):
        provider = category_provider_for(platform)
        assert provider is not None
        assert provider.platform == platform
        assert callable(provider.search)
        assert callable(provider.detail)
        assert callable(provider.resolve_site)
    assert category_provider_for("yandex") is None
    common_paths = [
        ROOT / "erp_web/runtime_units/category_store.py",
        ROOT / "erp_web/marketplaces/category_services.py",
    ]
    offenders = platform_literal_branches(common_paths, PLATFORM_LITERALS)
    assert not offenders, (
        "通用类目入口仍绕过 CategoryProvider 手工派发：\n"
        + format_findings(offenders)
    )


# 验收：四个 AI 业务用例必须统一调用 run_ai_use_case，不得复制请求编排函数。
def test_business_ai_use_cases_share_one_executor() -> None:
    for relative_path in (
        "erp_web/runtime_units/category_attribute_translation.py",
        "erp_web/runtime_units/category_result_translation.py",
        "erp_web/runtime_units/category_attribute_ai_fill.py",
        "erp_web/runtime_units/category_product_identify.py",
    ):
        calls = [
            getattr(call.func, "attr", "")
            or getattr(call.func, "id", "")
            for definition in function_definitions(relative_path)
            for call in scoped_calls(definition)
        ]
        assert calls.count("run_ai_use_case") == 1
        duplicated = {
            name
            for name in calls
            if name in {"load_app_config", "load_prompt_pair", "chat_json"}
        }
        assert not duplicated, f"{relative_path} 仍复制请求编排步骤：{sorted(duplicated)}"


# 验收：店铺凭据字段和在线测试入口必须由平台描述符驱动，通用授权层不得硬编码平台分支。
def test_store_credentials_are_registry_driven() -> None:
    module_path = ROOT / "erp_web/runtime_units/store_credentials.py"
    assert module_path.exists(), "店铺授权代码必须从 auth_runtime.py 改名为 store_credentials.py"
    module = importlib.import_module("erp_web.runtime_units.store_credentials")
    assert callable(getattr(module, "test_store_auth", None))
    resolver = getattr(module, "resolve_store_auth_tester", None)
    assert callable(resolver), "store_credentials 必须公开 spec 驱动的 tester 解析入口"
    auth_specs = [spec for spec in MARKETPLACE_SPECS if spec.test_auth]
    assert auth_specs
    for spec in auth_specs:
        assert spec.credential_keys()
        assert callable(resolver(spec))
    offenders = platform_literal_branches([module_path], PLATFORM_LITERALS)
    assert not offenders, (
        "店铺授权通用入口仍硬编码平台分支，应通过 spec.test_auth 派发：\n"
        + format_findings(offenders)
    )


# 验收：类目字段、预设键和标题限制必须从 registry 读取，不能在业务层复制平台判断。
def test_platform_field_mappings_are_registry_owned() -> None:
    for spec in MARKETPLACE_SPECS:
        assert category_id_field(spec.key) == spec.category_field
        assert platform_preset_key(spec.key) == spec.preset_key
        assert platform_title_limit(spec.key) == spec.title_limit
        assert spec.language
        assert spec.description_limit > 0
    common_paths = [
        ROOT / path
        for path in (
            "erp_web/services/copy_service.py",
            "erp_web/stores/product_store.py",
            "erp_web/runtime_units/copy_generation.py",
            "erp_web/runtime_units/image_pool_core.py",
            "erp_web/facades/publish_facade.py",
            "erp_web/runtime_units/runtime_api.py",
        )
    ]
    offenders = platform_literal_branches(common_paths, PLATFORM_LITERALS)
    assert not offenders, (
        "平台字段/能力分支仍泄漏在通用层：\n" + format_findings(offenders)
    )


# 验收：wildberries 必须正式注册或从生产代码完全删除，不能继续保留僵尸配置分支。
def test_wildberries_is_registered_or_removed() -> None:
    spec = marketplace_spec("wildberries")
    if spec is not None:
        assert spec.capabilities, "wildberries 不能只注册空壳描述符"
        if CAP_PUBLISH in spec.capabilities:
            assert publish_adapter.publishing_adapter_for(spec.key) is not None
        if spec.capabilities & {
            CAP_CATEGORY_SEARCH,
            CAP_CATEGORY_ATTRIBUTES,
        }:
            assert category_provider_for(spec.key) is not None
        return
    active_paths = [
        ROOT / path
        for path in (
            "erp_web/listing_planner.py",
            "erp_web/marketplaces/payloads.py",
            "erp_web/marketplaces/category_services.py",
            "erp_web/http_route_units/static_routes.py",
        )
    ]
    offenders = string_literal_occurrences(active_paths, "wildberries")
    assert not offenders, (
        "wildberries 未注册却仍残留生产字符串常量：\n"
        + format_findings(offenders)
    )


# 验收：facade 只能做输入适配和 domain 调用，不得直接写文件或承载发布副作用。
def test_facades_are_thin_adapters() -> None:
    facade_files = sorted((ROOT / "erp_web/facades").glob("*_facade.py"))
    import_offenders = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path, target in imported_targets(facade_files)
        if target.split(".")[0] in {"requests", "urllib", "sqlite3"}
    ]
    call_offenders = forbidden_calls(
        facade_files,
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
    oversized = [
        f"{definition.path.relative_to(ROOT)}:{definition.lineno} "
        f"{definition.qualname} — {definition.node.end_lineno - definition.lineno + 1} 行"
        for path in facade_files
        for definition in function_definitions(path)
        if definition.node.end_lineno - definition.lineno + 1 > 40
    ]
    assert not import_offenders, "facade 仍直接依赖网络/数据库模块：\n" + "\n".join(import_offenders)
    assert not call_offenders, "facade 仍直接执行副作用：\n" + format_findings(call_offenders)
    assert not oversized, "facade 仍承载大段业务编排：\n" + "\n".join(oversized)


# 验收：未实现的平台从 runtime、facade、预览和队列路径都必须失败关闭。
def test_unsupported_publish_paths_never_report_success_or_create_jobs(monkeypatch) -> None:
    unsupported = [
        spec.key for spec in MARKETPLACE_SPECS if CAP_PUBLISH not in spec.capabilities
    ] + ["__unknown_platform__"]

    def draft_context(body: dict):
        platform = str(body.get("platform") or "")
        return {
            "platform": platform,
            "site": "global",
            "target": {"platform": platform, "site": "global"},
            "draft": {"platform": platform, "site": "global"},
            "productContext": {},
            "product": {"product_id": "acceptance-product"},
        }, None, 200

    monkeypatch.setattr(
        publish_facade,
        "load_required_product_from_body",
        lambda body: ({"product_id": "acceptance-product"}, None, 200),
    )
    monkeypatch.setattr(
        publish_facade,
        "load_required_draft_publish_context",
        draft_context,
    )

    def assert_unsupported(result: dict) -> None:
        assert result["ok"] is False
        assert result.get("supported") is False
        assert result.get("status") == "unsupported"
        assert not result.get("job_id")

    for platform in unsupported:
        assert_unsupported(runtime_api.publish_product({}, platform, {}))
        published, _ = publish_facade.publish_product_payload({"platform": platform})
        previewed, _ = publish_facade.preview_publish_payload({"platform": platform})
        queued, _ = publish_facade.enqueue_publish_job({"platform": platform})
        assert_unsupported(published)
        assert_unsupported(previewed)
        assert_unsupported(queued)
    database = get_context().db
    assert database.list_pending_publish_jobs() == []
    with database._connect() as connection:
        job_count = connection.execute(
            "SELECT COUNT(*) FROM publish_jobs"
        ).fetchone()[0]
    assert job_count == 0
    assert database.list_publish_logs(limit=100) == []
