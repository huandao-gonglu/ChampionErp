from __future__ import annotations

import json

from erp_web.product_research_config import default_product_research_config, normalize_product_research_config
from services import product_research_service


def product_research_payload() -> dict:
    return {
        "search_mode": "target_plus_reference",
        "markets": {
            "target_markets": ["US"],
            "reference_markets": ["GB", "CA"],
        },
        "product_intent": {
            "china_element_required": True,
            "upgrade_variant_required": True,
        },
        "filters": {
            "include_china_element_types": ["mahjong", "calligraphy"],
            "upgrade_types": ["gift_box", "custom_name", "localized_explanation"],
            "exclude_risks": ["food", "battery", "children_product", "medical_device", "cosmetics", "liquid"],
        },
        "sources": {
            "demand_sources": ["google_trends", "etsy", "ebay"],
        },
        "result_options": {
            "limit": 5,
            "sort_by": "opportunity_score",
        },
    }


def test_create_search_task_returns_candidates_and_source_status(tmp_path) -> None:
    config = normalize_product_research_config(default_product_research_config())
    task = product_research_service.create_search_task(tmp_path, product_research_payload(), config)

    assert task["status"] == "completed"
    assert task["items"]
    assert task["signals"]
    assert any(row["source"] == "google_trends" and row["status"] == "success" for row in task["source_status"])
    assert any(row["source"] == "etsy" and row["status"] == "success" for row in task["source_status"])
    assert any(row["source"] == "ebay" and row["status"] == "configuration_required" for row in task["source_status"])
    assert task["items"][0]["opportunity_score"] >= task["items"][-1]["opportunity_score"]

    loaded = product_research_service.load_search_task(tmp_path, task["task_id"])
    assert loaded
    assert loaded["task_id"] == task["task_id"]


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


def test_target_market_derives_bound_search_provider(tmp_path) -> None:
    config = default_product_research_config()
    config["search_providers"] = [
        {
            "id": "api_amazon",
            "name": "api_亚马逊",
            "source_type": "api",
            "platform": "amazon",
            "enabled": True,
            "priority": 1,
            "supported_markets": ["US", "GB"],
            "supported_languages": ["en"],
            "supported_data_types": ["marketplace_products"],
            "auth_required": False,
            "config_json": {"provider_strategy": "seeded_mock"},
        }
    ]
    config["target_markets"] = [
        {"market": "US", "language": "en", "currency": "USD", "provider_ids": ["api_amazon"]},
        {"market": "UK", "language": "en", "currency": "GBP", "provider_ids": ["api_amazon"]},
    ]
    body = product_research_payload()
    body["markets"] = {"target_markets": ["UK"], "reference_markets": []}
    body.pop("sources")

    task = product_research_service.create_search_task(tmp_path, body, normalize_product_research_config(config))

    assert task["request"]["sources"]["demand_sources"] == ["api_amazon"]
    assert task["items"]
    assert any(row["source"] == "api_amazon" and row["status"] in {"success", "cached"} for row in task["source_status"])
    assert any(signal["source_id"] == "api_amazon" for signal in task["signals"])


def test_keyword_only_request_does_not_merge_default_china_elements() -> None:
    config = normalize_product_research_config(default_product_research_config())
    body = product_research_payload()
    body["keywords"] = ["pet storage", "car organizer"]
    body["filters"].pop("include_china_element_types")
    body["filters"]["upgrade_types"] = []

    request = product_research_service.normalize_search_request(body, config)
    expanded = product_research_service.expand_keywords(request, config)

    assert request["filters"]["include_china_element_types"] == []
    assert request["filters"]["upgrade_types"] == []
    assert [item["keyword"] for item in expanded] == ["pet storage", "car organizer"]
    assert {item["china_element_type"] for item in expanded} == {"custom_keyword"}


def test_ai_web_search_adds_source_backed_signals(tmp_path, monkeypatch) -> None:
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
                                                "keyword": "pet storage",
                                                "market": "US",
                                                "title": "Pet storage baskets trend on US marketplace",
                                                "source_name": "Example Search",
                                                "source_url": "https://example.com/pet-storage-trend",
                                                "product_url": "https://example.com/pet-storage-product",
                                                "metrics": {"content_heat": 82},
                                                "confidence": 0.82,
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
        seen["timeout"] = str(timeout)
        body = json.loads(request.data.decode("utf-8"))
        seen["model"] = body["model"]
        seen["prompt"] = body["messages"][1]["content"]
        return FakeResponse()

    monkeypatch.setattr(product_research_service.ai_gateway.urllib.request, "urlopen", fake_urlopen)
    config = default_product_research_config()
    config["search_providers"] = [
        {
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
                "max_items": 12,
                "require_source_url": True,
            },
        }
    ]
    config["target_markets"] = [
        {"market": "US", "language": "en", "currency": "USD", "provider_ids": ["ai_market_search"]},
    ]
    body = product_research_payload()
    body["keywords"] = ["pet storage"]
    body["filters"].pop("include_china_element_types")
    body["filters"]["upgrade_types"] = []
    body["sources"] = {"demand_sources": ["ai_market_search"]}
    body["product_intent"] = {"keyword_required": True}

    task = product_research_service.create_search_task(
        tmp_path,
        body,
        normalize_product_research_config(config),
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

    ai_status = next(row for row in task["source_status"] if row["source_id"] == "ai_market_search")
    ai_signal = next(signal for signal in task["signals"] if signal["source_id"] == "ai_market_search")
    assert seen["url"] == "https://ai.example.com/v1/chat/completions"
    assert seen["auth"] == "Bearer ai-key"
    assert seen["model"] == "web-search-model"
    assert "pet storage" in seen["prompt"]
    assert ai_status["status"] == "success"
    assert ai_status["items_found"] == 1
    assert ai_signal["keyword"] == "pet storage"
    assert ai_signal["product_url"] == "https://example.com/pet-storage-product"
    assert ai_signal["data_type"] == "ai_web_search"
    assert any("AI 市场搜索" in item["related_sources"] for item in task["items"])

    result = product_research_service.test_search_provider_connection(
        {
            "provider": config["search_providers"][0],
            "options": {
                "market": "US",
                "language": "en",
                "keyword": "pet storage",
                "data_type": "ai_web_search",
            },
        },
        normalize_product_research_config(config),
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
    assert result["source_id"] == "ai_market_search"
    assert result["provider_strategy"] == "ai_web_search"
    assert result["items_found"] == 1


def test_configured_api_provider_uses_saved_request_mapping(tmp_path, monkeypatch) -> None:
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
                                "keyword": "mahjong gift lamp",
                                "price": {"amount": 29.99, "currency": "USD"},
                                "url": "https://example.com/mahjong-lamp",
                                "image_url": "https://example.com/mahjong-lamp.jpg",
                                "review_count": 120,
                                "rating": 4.8,
                            }
                        ]
                    }
                }
            ).encode("utf-8")

    seen: dict[str, str] = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["api_key"] = request.get_header("X-api-key") or request.get_header("x-api-key") or ""
        seen["timeout"] = str(timeout)
        return FakeResponse()

    monkeypatch.setattr(product_research_service.urllib.request, "urlopen", fake_urlopen)
    config = default_product_research_config()
    config["search_providers"] = [
        {
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
                    "keyword_path": "keyword",
                    "price_path": "price.amount",
                    "currency_path": "price.currency",
                    "url_path": "url",
                    "image_path": "image_url",
                },
            },
        }
    ]
    config["target_markets"] = [
        {"market": "US", "language": "en", "currency": "USD", "provider_ids": ["api_amazon"]},
    ]
    body = product_research_payload()
    body["markets"] = {"target_markets": ["US"], "reference_markets": []}
    body["filters"]["include_china_element_types"] = ["mahjong"]
    body.pop("sources")

    task = product_research_service.create_search_task(tmp_path, body, normalize_product_research_config(config))

    assert "market=US" in seen["url"]
    assert seen["api_key"] == "secret-token"
    assert task["items"]
    assert task["signals"][0]["title"] == "Mahjong gift lamp"
    assert task["signals"][0]["product_url"] == "https://example.com/mahjong-lamp"
    assert any(row["source"] == "api_amazon" and row["status"] == "success" for row in task["source_status"])

    result = product_research_service.test_search_provider_connection(
        {
            "provider": config["search_providers"][0],
            "options": {
                "market": "US",
                "language": "en",
                "keyword": "mahjong gift",
                "data_type": "marketplace_products",
            },
        },
        normalize_product_research_config(config),
    )

    assert result["ok"] is True
    assert result["items_found"] == 1
    assert result["sample"]["title"] == "Mahjong gift lamp"
