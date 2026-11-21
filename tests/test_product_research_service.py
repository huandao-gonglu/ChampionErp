from __future__ import annotations
import json
import pytest
from erp_web.context import get_context
from erp_web.facades import product_research_facade
from erp_web.product_research_config import default_product_research_config, normalize_product_research_config
from erp_web.services import ai_gateway, ai_gateway_providers, product_research_service
from erp_web.services.product_research_service import ProductResearchRunRegistry


def hot_product_payload() -> dict:
    return {
        "search_mode": "target_only",
        "keywords": ["kitchen organizer"],
        "markets": {"target_markets": ["amazon-us"], "reference_markets": []},
        "result_options": {"limit": 6, "sort_by": "rank"},
    }


def test_running_run_is_marked_failed_after_backend_restart() -> None:
    created_at = product_research_service._utc_now()
    run = {
        "run_id": "cached_running",
        "status": "running",
        "search_mode": "target_only",
        "created_at": created_at,
        "completed_at": "",
        "request": hot_product_payload(),
        "items": [
            {
                "id": "hot_cached_1",
                "title": "Cached partial item",
                "rank": 1,
                "source_url": "https://example.com/cached-partial",
            }
        ],
        "source_status": [],
        "description": "AI 正在返回结果",
        "progress_description": "partial token",
    }
    get_context().research.store(run)

    # 重启 = 新 Registry 构造：DB 里 running 的 run 标 failed，候选保留。
    registry = ProductResearchRunRegistry(get_context().db)
    restored = registry.get(run["run_id"])

    assert restored is not None
    assert restored["status"] == "failed"
    assert "后台任务已中断" in restored["description"]
    assert restored["items"][0]["title"] == "Cached partial item"
    assert restored["source_status"][0]["provider_strategy"] == "run_registry"
    assert restored["completed_at"]
    assert registry.get_active() is None


def test_get_active_hot_product_run_returns_latest_non_terminal() -> None:
    registry = get_context().research
    created_at = product_research_service._utc_now()
    queued_run = {
        "run_id": "test_active_queued",
        "status": "queued",
        "search_mode": "target_only",
        "created_at": created_at,
        "completed_at": "",
        "request": hot_product_payload(),
        "items": [],
        "source_status": [],
        "description": "queued",
        "progress_description": "",
    }
    completed_run = {
        **queued_run,
        "run_id": "test_active_completed",
        "status": "completed",
        "completed_at": created_at,
    }
    running_run = {
        **queued_run,
        "run_id": "test_active_running",
        "status": "running",
        "description": "running",
    }

    registry.store(queued_run)
    registry.store(completed_run)
    registry.store(running_run)

    active = product_research_service.get_active_hot_product_run()

    assert active is not None
    assert active["run_id"] == "test_active_running"
    registry.update("test_active_queued", status="completed")
    registry.update("test_active_running", status="completed")
    assert product_research_service.get_active_hot_product_run() is None

def test_public_product_research_config_masks_source_secrets() -> None:
    config = default_product_research_config()
    config["source_registry"] = [
        {
            "id": "custom_api",
            "name": "Custom API",
            "source_type": "api",
            "platform": "google_trends",
            "enabled": True,
            "priority": 1,
            "supported_markets": ["US"],
            "supported_languages": ["en"],
            "supported_data_types": ["keyword_trend"],
            "auth_required": True,
            "config_json": {
                "provider_strategy": "configured_api",
                "api_key": "secret-token-123456",
                "vendor_api_key": "vendor-secret-123456",
                "cookie": "cookie-secret-123456",
                "private_key": "private-secret-123456",
                "source_key": "source-secret-123456",
                "base_url": "https://api.example.com",
            },
        }
    ]

    public_config = product_research_service.public_product_research_config(config)
    source_config = public_config["source_registry"][0]["config_json"]

    assert source_config["api_key"] != "secret-token-123456"
    assert source_config["api_key"].startswith("secr")
    for key, secret in {
        "vendor_api_key": "vendor-secret-123456",
        "cookie": "cookie-secret-123456",
        "private_key": "private-secret-123456",
        "source_key": "source-secret-123456",
    }.items():
        assert source_config[key] != secret
        assert source_config[key]
    assert source_config["base_url"] == "https://api.example.com"

def test_masked_custom_provider_secrets_restore_from_current_config() -> None:
    current = {
        "search_providers": [
            {
                "id": "custom",
                "config_json": {
                    "vendor_api_key": "vendor-secret-123456",
                    "nested": [
                        {"private_key": "private-secret-123456"}
                    ],
                },
            }
        ]
    }
    public = product_research_service.public_product_research_config(
        current
    )
    incoming = {
        "search_providers": public["search_providers"],
    }

    restored = (
        product_research_facade._restore_masked_provider_secrets(
            incoming,
            current,
        )
    )
    config = restored["search_providers"][0]["config_json"]
    assert config["vendor_api_key"] == "vendor-secret-123456"
    assert (
        config["nested"][0]["private_key"]
        == "private-secret-123456"
    )
