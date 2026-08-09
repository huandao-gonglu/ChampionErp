from __future__ import annotations

from erp_web import http_routes
from erp_web.context import get_context
from erp_web.facades import publish_facade
from erp_web.marketplace_registry import (
    CAP_CATEGORY_ATTRIBUTES,
    CAP_CATEGORY_SEARCH,
    CAP_PUBLISH,
    MARKETPLACE_SPECS,
)
from erp_web.marketplaces.publisher import PlatformPublisher
from erp_web.runtime_units import publish_adapter, publish_workflows
from erp_web.runtime_units.category_providers import (
    category_provider_for,
)
from erp_web.runtime_units.category_searchers import create_category_searcher
from erp_web.runtime_units.runtime_api import publish_product
from erp_web.runtime_units.store_credentials import (
    resolve_store_auth_tester,
)
from erp_web.services import ai_model_config, ai_prompt_templates

from .support import called_leaf_names


def test_publish_capabilities_match_real_publishers() -> None:
    required_methods = {
        "prepare_product",
        "resolve_category",
        "required_attributes_missing",
        "validate_draft",
        "build_payload",
        "validate_payload",
        "publish",
    }
    assert required_methods.issubset(PlatformPublisher.__dict__)
    publishable = {
        spec.key
        for spec in MARKETPLACE_SPECS
        if CAP_PUBLISH in spec.capabilities
    }
    assert publishable
    assert publishable == set(publish_adapter._PUBLISHERS)
    assert len(
        {
            id(adapter)
            for adapter in publish_adapter._PUBLISHERS.values()
        }
    ) == len(publish_adapter._PUBLISHERS)
    for platform, adapter in publish_adapter._PUBLISHERS.items():
        assert publish_adapter.publishing_adapter_for(platform) is adapter
        for method in required_methods:
            assert callable(getattr(adapter, method, None)), (
                f"{platform} 缺少 {method}"
            )


def test_category_capabilities_match_real_providers() -> None:
    for spec in MARKETPLACE_SPECS:
        has_category_capability = bool(
            spec.capabilities
            & {CAP_CATEGORY_SEARCH, CAP_CATEGORY_ATTRIBUTES}
        )
        provider = category_provider_for(spec.key)
        assert (provider is not None) is has_category_capability
        if provider is not None:
            assert provider.platform == spec.key
            assert callable(provider.detail)
            assert callable(provider.resolve_site)
        if CAP_CATEGORY_SEARCH in spec.capabilities:
            searcher = create_category_searcher(
                spec.key,
                site="global" if spec.key == "ozon" else "MLM",
            )
            assert callable(searcher.search_categories)


def test_store_auth_testers_are_registry_driven() -> None:
    registered = [
        spec for spec in MARKETPLACE_SPECS if spec.test_auth
    ]
    assert registered
    for spec in MARKETPLACE_SPECS:
        tester = resolve_store_auth_tester(spec)
        if spec.test_auth:
            assert spec.credential_keys()
            assert callable(tester)
        else:
            assert tester is None


def test_business_ai_use_cases_share_one_executor() -> None:
    executors = {
        "erp_web/runtime_units/text_translation.py": "run_ai_use_case",
        "erp_web/runtime_units/category_attribute_ai_fill.py": (
            "run_category_attribute_fill_agent"
        ),
    }
    for relative_path, executor in executors.items():
        calls = called_leaf_names(relative_path)
        assert calls.count(executor) == 1
        duplicated = {
            name
            for name in calls
            if name in {
                "load_app_config",
                "load_prompt_pair",
                "chat_json",
            }
        }
        assert not duplicated, (
            f"{relative_path} 重复实现 AI 请求编排："
            f"{sorted(duplicated)}"
        )


def test_text_translation_is_the_only_translation_contract() -> None:
    retired_use_cases = {
        "category.attribute_translation",
        "category.result_translation",
    }
    retired_endpoints = {
        "/api/category-attribute-translations",
        "/api/category-result-translations",
    }

    assert "text.translate" in ai_model_config.AI_USE_CASES
    assert "text.translate" in ai_prompt_templates.DEFAULT_AI_USE_CASE_PROMPTS
    assert "/api/text-translate" in http_routes.POST_API_ROUTES
    assert retired_use_cases.isdisjoint(ai_model_config.AI_USE_CASES)
    assert retired_use_cases.isdisjoint(ai_prompt_templates.DEFAULT_AI_USE_CASE_PROMPTS)
    assert retired_endpoints.isdisjoint(http_routes.POST_API_ROUTES)


def test_unsupported_publish_paths_fail_closed(monkeypatch) -> None:
    unsupported = [
        spec.key
        for spec in MARKETPLACE_SPECS
        if CAP_PUBLISH not in spec.capabilities
    ] + ["__unknown_platform__"]

    def draft_context(body: dict):
        platform = str(body.get("platform") or "")
        return {
            "platform": platform,
            "site": "global",
            "target": {
                "platform": platform,
                "site": "global",
            },
            "draft": {
                "platform": platform,
                "site": "global",
            },
            "productContext": {},
            "product": {"product_id": "architecture-product"},
        }, None, 200

    monkeypatch.setattr(
        get_context().products,
        "load_required_product_from_body",
        lambda body: (
            {"product_id": "architecture-product"},
            None,
            200,
        ),
    )
    monkeypatch.setattr(
        publish_workflows,
        "load_required_draft_publish_context",
        draft_context,
    )

    def assert_unsupported(result: dict) -> None:
        assert result["ok"] is False
        assert result.get("supported") is False
        assert result.get("status") == "unsupported"
        assert not result.get("job_id")

    for platform in unsupported:
        assert_unsupported(publish_product({}, platform, {}))
        published, _ = publish_facade.publish_product_payload(
            {"platform": platform}
        )
        previewed, _ = publish_facade.preview_publish_payload(
            {"platform": platform}
        )
        queued, _ = publish_facade.enqueue_publish_job(
            {"platform": platform}
        )
        assert_unsupported(published)
        assert_unsupported(previewed)
        assert_unsupported(queued)

    database = get_context().db
    assert database.list_pending_publish_jobs() == []
    assert database.list_publish_logs(limit=100) == []
