from __future__ import annotations

import json

import pytest

from erp_web.product_research_config import default_product_research_config, normalize_product_research_config
from services import product_research_service


def hot_product_payload() -> dict:
    return {
        "search_mode": "target_only",
        "markets": {"target_markets": ["amazon-us"], "reference_markets": []},
        "keywords": ["pet storage"],
        "result_options": {"limit": 6, "sort_by": "rank"},
    }


def test_create_hot_product_run_returns_market_hot_product_candidates(tmp_path) -> None:
    config = normalize_product_research_config(default_product_research_config())

    run = product_research_service.create_hot_product_run(tmp_path, hot_product_payload(), config)

    assert run["status"] == "completed"
    assert run["run_id"].startswith("prr_")
    assert len(run["items"]) == 2
    assert not (tmp_path / "data" / "cache" / "product_research" / "tasks").exists()

    first = run["items"][0]
    assert first["id"] == "hot_amazon-us_1"
    assert first["title"] == "pet storage organizer set"
    assert first["image_url"].startswith("https://")
    assert first["rank"] == 1
    assert first["source_url"].startswith("https://amazon.com/s?")
    assert first["platform"] == "amazon"
    assert first["site"] == "amazon.com"
    assert first["market_id"] == "amazon-us"
    assert first["keyword"] == "pet storage"
    assert first["price"]["currency"] == "USD"
    assert first["rating"] > 0
    assert first["review_count"] > 0
    assert first["hot_score"] > run["items"][-1]["hot_score"]
    assert first["source_name"] == "市场候选数据"
    assert first["collected_at"]
    assert run["source_status"] == [
        {
            "source": "market_hot_products",
            "source_id": "market_hot_products",
            "market": "amazon-us",
            "status": "success",
            "items_found": 2,
            "error_message": "",
            "provider_strategy": "market_data",
        }
    ]


def test_multiple_keywords_are_ranked_in_one_temporary_result(tmp_path) -> None:
    config = normalize_product_research_config(default_product_research_config())
    body = hot_product_payload()
    body["keywords"] = ["pet storage", "desk organizer"]
    body["result_options"]["limit"] = 5

    run = product_research_service.create_hot_product_run(tmp_path, body, config)

    assert [item["rank"] for item in run["items"]] == [1, 2, 3, 4]
    assert [item["keyword"] for item in run["items"]] == [
        "pet storage",
        "pet storage",
        "desk organizer",
        "desk organizer",
    ]


def test_hot_product_run_returns_empty_when_market_has_no_saved_data(tmp_path) -> None:
    config = default_product_research_config()
    config["target_markets"] = [
        {
            "id": "amazon-us",
            "platform": "amazon",
            "site": "amazon.com",
            "display_name": "Amazon US",
        }
    ]
    config["market_hot_products"] = [{"market_id": "amazon-us", "items": []}]

    run = product_research_service.create_hot_product_run(tmp_path, hot_product_payload(), normalize_product_research_config(config))

    assert run["items"] == []
    assert run["source_status"][0]["status"] == "empty"
    assert run["source_status"][0]["error_message"] == "目标市场还没有候选商品数据"


def test_normalize_search_request_requires_keywords() -> None:
    config = normalize_product_research_config(default_product_research_config())
    body = hot_product_payload()
    body["keywords"] = []

    with pytest.raises(ValueError, match="keywords is required"):
        product_research_service.normalize_search_request(body, config)


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
                "base_url": "https://api.example.com",
            },
        }
    ]

    public_config = product_research_service.public_product_research_config(config)
    source_config = public_config["source_registry"][0]["config_json"]

    assert source_config["api_key"] != "secret-token-123456"
    assert source_config["api_key"].startswith("secr")
    assert source_config["base_url"] == "https://api.example.com"


def test_ai_web_search_provider_connection_uses_configured_model(tmp_path, monkeypatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "items": [
                                            {
                                                "title": "Pet storage baskets trend",
                                                "source_url": "https://example.com/pet-storage-trend",
                                            }
                                        ]
                                    }
                                )
                            }
                        }
                    ]
                }
            ).encode("utf-8")

    seen: dict[str, str] = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization") or request.get_header("authorization") or ""
        body = json.loads(request.data.decode("utf-8"))
        seen["model"] = body["model"]
        seen["prompt"] = body["messages"][1]["content"]
        return FakeResponse()

    monkeypatch.setattr(product_research_service.ai_gateway.urllib.request, "urlopen", fake_urlopen)
    provider = {
        "id": "ai_market_search",
        "name": "AI 市场搜索",
        "source_type": "ai_search",
        "platform": "ai_model",
        "enabled": True,
        "priority": 1,
        "supported_markets": ["US"],
        "supported_languages": ["en"],
        "supported_data_types": ["ai_web_search"],
        "auth_required": False,
        "config_json": {
            "provider_strategy": "ai_web_search",
            "ai_model_id": "web_search_model",
        },
    }

    result = product_research_service.test_search_provider_connection(
        {
            "provider": provider,
            "options": {
                "market": "US",
                "language": "en",
                "keyword": "pet storage",
                "data_type": "ai_web_search",
            },
        },
        normalize_product_research_config(default_product_research_config()),
        tmp_path,
        {
            "ai_models": [
                {
                    "id": "web_search_model",
                    "provider": "OpenAI-Compatible",
                    "api_key": "ai-key",
                    "base_url": "https://ai.example.com/v1",
                    "model": "web-search-model",
                    "capabilities": ["chat", "json", "web_search"],
                }
            ]
        },
    )

    assert result["ok"] is True
    assert result["items_found"] == 1
    assert result["sample"]["source_url"] == "https://example.com/pet-storage-trend"
    assert seen["url"] == "https://ai.example.com/v1/chat/completions"
    assert seen["auth"] == "Bearer ai-key"
    assert seen["model"] == "web-search-model"
    assert "pet storage" in seen["prompt"]


def test_configured_api_provider_connection_uses_saved_request_mapping(monkeypatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "data": {
                        "items": [
                            {
                                "title": "Mahjong gift lamp",
                                "url": "https://example.com/mahjong-lamp",
                            }
                        ]
                    }
                }
            ).encode("utf-8")

    seen: dict[str, str] = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["api_key"] = request.get_header("X-api-key") or request.get_header("x-api-key") or ""
        return FakeResponse()

    monkeypatch.setattr(product_research_service.urllib.request, "urlopen", fake_urlopen)
    provider = {
        "id": "api_amazon",
        "name": "api_亚马逊",
        "source_type": "api",
        "platform": "amazon",
        "enabled": True,
        "priority": 1,
        "supported_markets": ["US"],
        "supported_languages": ["en"],
        "supported_data_types": ["marketplace_products"],
        "auth_required": True,
        "config_json": {
            "provider_strategy": "configured_api",
            "request": {
                "method": "GET",
                "url": "https://api.example.com/hot",
                "auth_type": "api_key_header",
                "api_key_header": "x-api-key",
                "api_key": "secret-token",
                "query": {"market": "{market}", "q": "{keyword}"},
            },
            "response": {
                "items_path": "data.items",
                "title_path": "title",
                "url_path": "url",
            },
        },
    }

    result = product_research_service.test_search_provider_connection(
        {
            "provider": provider,
            "options": {
                "market": "US",
                "language": "en",
                "keyword": "mahjong gift",
                "data_type": "marketplace_products",
            },
        },
        normalize_product_research_config(default_product_research_config()),
    )

    assert result["ok"] is True
    assert result["items_found"] == 1
    assert result["sample"]["title"] == "Mahjong gift lamp"
    assert result["sample"]["source_url"] == "https://example.com/mahjong-lamp"
    assert "market=US" in seen["url"]
    assert seen["api_key"] == "secret-token"
