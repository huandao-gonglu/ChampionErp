from __future__ import annotations

import json
import time

import pytest

from erp_web.context import get_context
from erp_web.facades import product_research_facade
from erp_web.product_research_config import default_product_research_config, normalize_product_research_config
from erp_web.services import ai_gateway_providers, product_research_methods, product_research_service
from erp_web.services.product_research_methods import AiSearchMethod, ProductResearchSearchMethod
from erp_web.services.product_research_service import ProductResearchRunRegistry

# conftest 的 autouse fixture 已为每个测试提供独立 AppContext：
# get_context().research 每次都是全新 Registry + 空的 research_runs 表。


def hot_product_payload() -> dict:
    return {
        "search_mode": "target_only",
        "markets": {"target_markets": ["amazon-us"], "reference_markets": []},
        "result_options": {"limit": 6, "sort_by": "rank"},
    }


def web_search_app_config() -> dict:
    return {
        "ai_models": [
            {
                "id": "web_search_model",
                "provider": "OpenAI",
                "api_key": "ai-key",
                "base_url": "https://ai.example.com/v1",
                "model": "web-search-model",
                "capabilities": ["chat", "json", "web_search"],
            }
        ]
    }


def patch_ai_search(monkeypatch, items: list[dict], seen: dict | None = None) -> None:
    def fake_chat_json(app_dir, app_config, use_case_id, messages, **kwargs):
        if seen is not None:
            seen["app_dir"] = app_dir
            seen["app_config"] = app_config
            seen["use_case_id"] = use_case_id
            seen["messages"] = messages
            seen["kwargs"] = kwargs
        return {"items": items}

    monkeypatch.setattr(product_research_methods.ai_gateway, "chat_json", fake_chat_json)


def web_search_app_config_with_prompt_file(tmp_path, user_prompt: str, system_prompt: str = "System prompt from file.") -> dict:
    prompt_dir = tmp_path / "config"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompt_dir / "test_ai_prompt.json"
    prompt_path.write_text(
        json.dumps(
            {
                "description": "测试 AI 搜索提示词",
                "system": system_prompt,
                "user": user_prompt,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    app_config = web_search_app_config()
    app_config["ai_use_case_prompts"] = {
        "research.web_search": {
            "path": "config/test_ai_prompt.json",
        }
    }
    return app_config


def test_ai_search_source_rejects_retired_model_override() -> None:
    config = default_product_research_config()
    config["source_registry"][0]["config_json"]["ai_model_id"] = "legacy_model"
    config["source_registry"][0]["config_json"]["model_id"] = "another_legacy_model"
    config["target_markets"][0]["search_methods"] = [
        {
            "method_id": "ai_web_search",
            "config_json": {
                "ai_model_id": "legacy_model",
                "model_id": "another_legacy_model",
            },
        }
    ]

    with pytest.raises(ValueError, match="已退役字段"):
        normalize_product_research_config(config)


def test_create_hot_product_run_returns_ai_search_candidates(tmp_path, monkeypatch) -> None:
    seen: dict = {}
    patch_ai_search(
        monkeypatch,
        [
            {
                "title": "Silicone kitchen utensil rest",
                "image_url": "https://example.com/kitchen-rest.jpg",
                "rank": 1,
                "source_url": "https://www.amazon.com/dp/example-kitchen-rest",
                "keyword": "kitchen",
                "price": {"amount": 24.99, "currency": "USD"},
                "rating": 4.6,
                "review_count": 1200,
                "hot_score": 91,
            },
            {
                "title": "Magnetic measuring spoons",
                "image_url": "https://example.com/measuring-spoons.jpg",
                "rank": 2,
                "source_url": "https://www.amazon.com/dp/example-spoons",
                "keyword": "kitchen",
                "price": {"amount": 18.5, "currency": "USD"},
                "rating": 4.4,
                "review_count": 840,
                "hot_score": 84,
            },
        ],
        seen,
    )
    config = normalize_product_research_config(default_product_research_config())

    run = product_research_service.create_hot_product_run(tmp_path, hot_product_payload(), config, web_search_app_config())

    assert run["status"] == "completed"
    assert run["run_id"].startswith("prr_")
    assert len(run["items"]) == 2
    assert not (tmp_path / "data" / "cache" / "product_research").exists()
    record = get_context().db.load_research_run(run["run_id"])
    assert record["status"] == "completed"
    assert record["method"] == "target_only"
    assert record["items"][0]["title"] == "Silicone kitchen utensil rest"
    assert record["items"][1]["title"] == "Magnetic measuring spoons"
    assert record["params"]["request"]["markets"]["target_markets"] == ["amazon-us"]
    assert "items" not in record["params"]
    log_path = tmp_path / "data" / "logs" / "product_research_runs.jsonl"
    assert log_path.exists()
    log_lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(log_lines) == 1
    assert log_lines[0]["run_id"] == run["run_id"]
    assert log_lines[0]["target_markets"] == ["amazon-us"]
    assert log_lines[0]["items_count"] == 2
    assert log_lines[0]["source_status"][0]["status"] == "success"
    assert log_lines[0]["items_preview"][0]["title"] == "Silicone kitchen utensil rest"
    assert log_lines[0]["items_preview"][0]["source_url"] == "https://www.amazon.com/dp/example-kitchen-rest"

    first = run["items"][0]
    assert first["id"].startswith("hot_amazon-us_")
    assert first["title"] == "Silicone kitchen utensil rest"
    assert first["image_url"] == "https://example.com/kitchen-rest.jpg"
    assert first["rank"] == 1
    assert first["source_url"] == "https://www.amazon.com/dp/example-kitchen-rest"
    assert first["platform"] == "amazon"
    assert first["site"] == "amazon.com"
    assert first["market_id"] == "amazon-us"
    assert first["keyword"] == "kitchen"
    assert first["price"]["currency"] == "USD"
    assert first["rating"] > 0
    assert first["review_count"] > 0
    assert first["hot_score"] > run["items"][-1]["hot_score"]
    assert first["source_name"] == "AI 搜索"
    assert first["collected_at"]
    assert seen["use_case_id"] == "research.web_search"
    assert seen["kwargs"]["stream"] is True
    assert seen["kwargs"]["response_format"] is False
    assert "当前热卖" in seen["messages"][1]["content"]
    assert "pet storage" not in seen["messages"][1]["content"]
    assert "{keywords}" not in seen["messages"][1]["content"]
    assert run["source_status"][0]["source"] == "AI 搜索"
    assert run["source_status"][0]["source_id"] == "ai_web_search"
    assert run["source_status"][0]["market"] == "amazon-us"
    assert run["source_status"][0]["status"] == "success"
    assert run["source_status"][0]["items_found"] == 2
    assert run["source_status"][0]["error_message"] == ""
    assert run["source_status"][0]["provider_strategy"] == "ai_web_search"
    assert run["source_status"][0]["raw_items_found"] == 2
    assert run["source_status"][0]["items_filtered"] == 0


def test_create_hot_product_run_async_polls_stream_description(tmp_path, monkeypatch) -> None:
    item = {
        "title": "Async silicone sink tray",
        "image_url": "https://example.com/sink-tray.jpg",
        "rank": 1,
        "source_url": "https://www.amazon.com/dp/example-sink-tray",
        "keyword": "kitchen",
        "price": {"amount": 14.99, "currency": "USD"},
        "rating": 4.7,
        "review_count": 560,
        "hot_score": 89,
    }

    def fake_chat_json(app_dir, app_config, use_case_id, messages, **kwargs):
        callback = kwargs.get("token_callback")
        if callback:
            callback(json.dumps(item, ensure_ascii=False) + "\n")
            # 窗口开大一点：全量跑测试时轮询线程也必须能观察到“流式进行中”状态。
            time.sleep(0.3)
            callback('{"title":"Ignored missing source","rank":2}\n')
            time.sleep(0.1)
        return {"items": [item]}

    monkeypatch.setattr(product_research_methods.ai_gateway, "chat_json", fake_chat_json)
    config = normalize_product_research_config(default_product_research_config())

    run = product_research_service.create_hot_product_run_async(tmp_path, hot_product_payload(), config, web_search_app_config())

    assert run["status"] == "queued"
    streamed = None
    for _ in range(300):
        streamed = product_research_service.get_hot_product_run(run["run_id"])
        if streamed and "AI 正在返回结果" in streamed.get("description", ""):
            break
        time.sleep(0.01)
    assert streamed is not None
    assert streamed["items"][0]["title"] == "Async silicone sink tray"
    assert "AI 正在返回结果" in streamed.get("description", "")

    final = streamed
    for _ in range(300):
        final = product_research_service.get_hot_product_run(run["run_id"])
        if final and final.get("status") in {"completed", "failed"}:
            break
        time.sleep(0.02)
    assert final is not None
    assert final["status"] == "completed"
    assert final["items"][0]["title"] == "Async silicone sink tray"
    assert final["description"] == "运行完成，找到 1 个候选商品。"
    record = get_context().db.load_research_run(run["run_id"])
    assert record["status"] == "completed"
    assert record["items"][0]["title"] == "Async silicone sink tray"


def test_ai_search_retries_without_stream_when_stream_is_forbidden(tmp_path, monkeypatch) -> None:
    item = {
        "title": "Non-stream silicone sink tray",
        "image_url": "https://example.com/non-stream-sink-tray.jpg",
        "rank": 1,
        "source_url": "https://www.amazon.com/dp/example-non-stream",
        "keyword": "kitchen",
        "price": {"amount": 13.99, "currency": "USD"},
        "rating": 4.5,
        "review_count": 380,
        "hot_score": 82,
    }
    calls: list[bool] = []

    def fake_chat_json(app_dir, app_config, use_case_id, messages, **kwargs):
        calls.append(bool(kwargs.get("stream")))
        if kwargs.get("stream"):
            raise product_research_methods.ai_gateway.AIHTTPError(
                status_code=403,
                reason="Forbidden",
                detail="stream is not allowed",
                model_id="web_search_model",
                model_name="web-search-model",
                api_style="openai_compatible",
                endpoint="ai.example.com/v1/chat/completions",
            )
        return {"items": [item]}

    monkeypatch.setattr(product_research_methods.ai_gateway, "chat_json", fake_chat_json)
    config = normalize_product_research_config(default_product_research_config())

    run = product_research_service.create_hot_product_run(tmp_path, hot_product_payload(), config, web_search_app_config())

    assert calls == [True, False]
    assert run["status"] == "completed"
    assert run["items"][0]["title"] == "Non-stream silicone sink tray"
    assert run["source_status"][0]["status"] == "success"
    assert run["source_status"][0]["stream_enabled"] is False
    assert run["source_status"][0]["stream_fallback_used"] is True


def test_ai_search_does_not_retry_official_client_forbidden_error(tmp_path, monkeypatch) -> None:
    calls: list[bool] = []

    def fake_chat_json(app_dir, app_config, use_case_id, messages, **kwargs):
        calls.append(bool(kwargs.get("stream")))
        raise product_research_methods.ai_gateway.AIHTTPError(
            status_code=403,
            reason="Forbidden",
            detail='{"error":{"message":"This account only allows Codex official clients","type":"forbidden_error"}}',
            model_id="web_search_model",
            model_name="web-search-model",
            api_style="openai_responses",
            endpoint="ai.example.com/v1/responses",
        )

    monkeypatch.setattr(product_research_methods.ai_gateway, "chat_json", fake_chat_json)
    config = normalize_product_research_config(default_product_research_config())

    run = product_research_service.create_hot_product_run(tmp_path, hot_product_payload(), config, web_search_app_config())

    assert calls == [True]
    assert run["status"] == "completed"
    assert run["items"] == []
    assert run["source_status"][0]["status"] == "failed"
    assert "only allows Codex official clients" in run["source_status"][0]["error_message"]
    assert run["source_status"][0]["stream_enabled"] is True
    assert run["source_status"][0]["stream_fallback_used"] is False


def test_hot_product_run_restores_completed_run_from_db_after_restart(tmp_path, monkeypatch) -> None:
    patch_ai_search(
        monkeypatch,
        [
            {
                "title": "Cached travel charger",
                "image_url": "https://example.com/cached-travel-charger.jpg",
                "rank": 1,
                "source_url": "https://www.amazon.com/dp/cached-travel-charger",
            }
        ],
    )
    config = normalize_product_research_config(default_product_research_config())
    run = product_research_service.create_hot_product_run(tmp_path, hot_product_payload(), config, web_search_app_config())

    # 新 Registry = 模拟进程重启后的空内存；历史 run 必须能从表里查回。
    registry = ProductResearchRunRegistry(get_context().db)
    restored = registry.get(run["run_id"])

    assert restored is not None
    assert restored["run_id"] == run["run_id"]
    assert restored["status"] == "completed"
    assert restored["items"][0]["title"] == "Cached travel charger"
    assert restored["description"] == run["description"]


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


def test_incoming_keywords_are_ignored_for_market_hot_products(tmp_path, monkeypatch) -> None:
    seen: dict = {}
    patch_ai_search(
        monkeypatch,
        [
            {
                "title": "Desk organizer tray",
                "image_url": "https://example.com/desk-3.jpg",
                "rank": 3,
                "source_url": "https://example.com/desk-3",
                "keyword": "office",
            },
            {
                "title": "Kitchen storage basket",
                "image_url": "https://example.com/kitchen-1.jpg",
                "rank": 1,
                "source_url": "https://example.com/kitchen-1",
                "keyword": "kitchen",
            },
            {
                "title": "Travel cable organizer",
                "image_url": "https://example.com/travel-2.jpg",
                "rank": 2,
                "source_url": "https://example.com/travel-2",
                "keyword": "travel",
            },
        ],
        seen,
    )
    config = normalize_product_research_config(default_product_research_config())
    body = hot_product_payload()
    body["keywords"] = ["pet storage", "desk organizer"]
    body["result_options"]["limit"] = 5

    run = product_research_service.create_hot_product_run(tmp_path, body, config, web_search_app_config())

    assert [item["rank"] for item in run["items"]] == [1, 2, 3]
    assert [item["keyword"] for item in run["items"]] == ["kitchen", "travel", "office"]
    assert "pet storage" not in seen["messages"][1]["content"]
    assert "desk organizer" not in seen["messages"][1]["content"]


def test_hot_product_run_returns_empty_when_ai_returns_no_traceable_items(tmp_path, monkeypatch) -> None:
    patch_ai_search(monkeypatch, [])
    config = default_product_research_config()
    config["target_markets"] = [
        {
            "id": "amazon-us",
            "platform": "amazon",
            "site": "amazon.com",
            "display_name": "Amazon US",
            "search_methods": [{"method_id": "ai_web_search", "enabled": True, "config_json": {}}],
        }
    ]

    run = product_research_service.create_hot_product_run(
        tmp_path,
        hot_product_payload(),
        normalize_product_research_config(config),
        web_search_app_config(),
    )

    assert run["items"] == []
    assert run["source_status"][0]["status"] == "empty"
    assert run["source_status"][0]["error_message"] == "AI 返回了 0 条原始候选。"


def test_hot_product_run_returns_empty_when_market_has_no_search_methods(tmp_path) -> None:
    config = default_product_research_config()
    config["target_markets"] = [
        {
            "id": "amazon-us",
            "platform": "amazon",
            "site": "amazon.com",
            "display_name": "Amazon US",
            "search_methods": [],
        }
    ]

    run = product_research_service.create_hot_product_run(tmp_path, hot_product_payload(), normalize_product_research_config(config))

    assert run["items"] == []
    assert run["source_status"][0]["status"] == "empty"
    assert run["source_status"][0]["error_message"] == "目标市场还没有关联搜索手段"


def test_target_market_normalization_adds_search_method_binding() -> None:
    config = normalize_product_research_config(default_product_research_config())
    method = config["search_providers"][0]
    market = config["target_markets"][0]

    assert market["search_methods"][0]["method_id"] == "ai_web_search"
    assert market["search_methods"][0]["prompt"] == ""
    assert "prompt" not in market["search_methods"][0]["config_json"]
    assert "prompt_template" not in method["config_json"]
    assert "prompt_override" not in market["search_methods"][0]["config_json"]
    assert "prompt_templates" not in config


def test_ai_search_renders_prompt_template_from_file(tmp_path, monkeypatch) -> None:
    seen: dict = {}
    patch_ai_search(
        monkeypatch,
        [
            {
                "title": "Template rendered item",
                "image_url": "https://example.com/template-rendered.jpg",
                "rank": 1,
                "source_url": "https://www.amazon.com/dp/template-rendered",
            }
        ],
        seen,
    )
    app_config = web_search_app_config_with_prompt_file(
        tmp_path,
        "市场={$displayName}; 平台={$platform}; 站点={$site}; 货币={$currency}; 数量={$limit}",
    )
    config = default_product_research_config()
    config["target_markets"] = [
        {
            "id": "amazon-us",
            "platform": "amazon",
            "site": "amazon.com",
            "display_name": "Amazon US",
            "search_methods": [{"method_id": "ai_web_search", "enabled": True, "config_json": {}}],
        }
    ]

    product_research_service.create_hot_product_run(
        tmp_path,
        hot_product_payload(),
        normalize_product_research_config(config),
        app_config,
    )

    prompt = seen["messages"][1]["content"]
    assert seen["messages"][0]["content"] == "System prompt from file."
    assert "市场=Amazon US" in prompt
    assert "平台=amazon" in prompt
    assert "站点=amazon.com" in prompt
    assert "货币=USD" in prompt
    assert "数量=6" in prompt
    assert "{$" not in prompt


def test_ai_search_uses_target_market_saved_prompt(tmp_path, monkeypatch) -> None:
    seen: dict = {}
    patch_ai_search(
        monkeypatch,
        [
            {
                "title": "Saved prompt item",
                "image_url": "https://example.com/saved-prompt.jpg",
                "rank": 1,
                "source_url": "https://www.amazon.com/dp/saved-prompt",
            }
        ],
        seen,
    )
    config = default_product_research_config()
    config["target_markets"] = [
        {
            "id": "amazon-us",
            "platform": "amazon",
            "site": "amazon.com",
            "display_name": "Amazon US",
            "search_methods": [
                {
                    "method_id": "ai_web_search",
                    "enabled": True,
                    "prompt": "Saved market prompt for Amazon US",
                    "config_json": {},
                }
            ],
        }
    ]

    product_research_service.create_hot_product_run(
        tmp_path,
        hot_product_payload(),
        normalize_product_research_config(config),
        web_search_app_config_with_prompt_file(tmp_path, "Default template {$displayName}"),
    )

    assert seen["messages"][1]["content"] == "Saved market prompt for Amazon US"


def test_normalize_config_rejects_retired_prompt_template_fields() -> None:
    config = default_product_research_config()
    config["source_registry"][0]["config_json"]["prompt_template"] = "old provider prompt"
    config["source_registry"][0]["config_json"]["promptTemplatePath"] = "config/old.txt"
    config["target_markets"][0]["search_methods"] = [
        {
            "method_id": "ai_web_search",
            "enabled": True,
            "config_json": {
                "prompt": "old market prompt",
                "prompt_override": "old override",
            },
        }
    ]

    with pytest.raises(ValueError, match="已退役字段"):
        normalize_product_research_config(config)


def test_ai_search_filters_candidates_without_image_url(tmp_path, monkeypatch) -> None:
    patch_ai_search(
        monkeypatch,
        [
            {
                "title": "No image candidate",
                "rank": 1,
                "source_url": "https://www.amazon.com/dp/no-image",
            },
            {
                "title": "Direct image candidate",
                "image_url": "https://example.com/direct-image.jpg",
                "rank": 2,
                "source_url": "https://www.amazon.com/dp/direct-image",
            },
        ],
    )
    config = normalize_product_research_config(default_product_research_config())

    run = product_research_service.create_hot_product_run(tmp_path, hot_product_payload(), config, web_search_app_config())

    assert [item["title"] for item in run["items"]] == ["Direct image candidate"]
    assert run["items"][0]["image_url"] == "https://example.com/direct-image.jpg"
    assert run["source_status"][0]["items_found"] == 1
    assert run["source_status"][0]["items_filtered"] == 1
    assert run["source_status"][0]["diagnostic_message"] == "AI 返回 2 条原始候选，整理后 1 条；过滤原因：1 条缺少图片 URL。"


def test_ai_gateway_parse_jsonl_items_text() -> None:
    payload = product_research_methods.ai_gateway.parse_json_text(
        "\n".join(
            [
                '{"title":"A","rank":1,"source_url":"https://example.com/a"}',
                '{"title":"B","rank":2,"source_url":"https://example.com/b"}',
            ]
        )
    )

    assert [item["title"] for item in payload["items"]] == ["A", "B"]


def test_ai_gateway_chat_json_reports_http_error_detail(tmp_path, monkeypatch) -> None:
    def fake_direct_chat(**kwargs):
        raise product_research_methods.ai_gateway.AIHTTPError(
            status_code=403,
            reason="Forbidden",
            detail="web search forbidden for this model",
            model_id="web_search_model",
            model_name="web-search-model",
            api_style="openai_compatible",
            endpoint="ai.example.com/openai_compatible",
        )

    monkeypatch.setattr(
        ai_gateway_providers.ai_direct_request_service,
        "chat_json",
        fake_direct_chat,
    )

    with pytest.raises(product_research_methods.ai_gateway.AIHTTPError) as exc_info:
        product_research_methods.ai_gateway.chat_json(
            tmp_path,
            web_search_app_config(),
            "research.web_search",
            [{"role": "user", "content": "Find products"}],
            model_id="web_search_model",
        )

    message = str(exc_info.value)
    assert "HTTP 403" in message
    assert "ai.example.com/openai_compatible" in message
    assert "web search forbidden for this model" in message


def test_normalize_config_rejects_retired_seeded_sources() -> None:
    config = default_product_research_config()
    config["search_providers"] = [
        {
            "id": "google_trends_seeded",
            "name": "Google Trends Seeded",
            "source_type": "api",
            "platform": "google_trends",
            "enabled": True,
            "priority": 1,
            "config_json": {"provider_strategy": "seeded_mock"},
        },
        {
            "id": "ai_market_search_seeded",
            "name": "AI 搜索",
            "source_type": "ai_search",
            "platform": "ai_model",
            "enabled": True,
            "priority": 1,
            "config_json": {"provider_strategy": "seeded_mock"},
        },
    ]
    config["target_markets"] = [
        {
            "id": "amazon-us",
            "platform": "amazon",
            "site": "amazon.com",
            "display_name": "Amazon US",
            "search_methods": [
                {"method_id": "google_trends_seeded", "enabled": True, "config_json": {}},
                {"method_id": "ai_market_search_seeded", "enabled": True, "config_json": {}},
            ],
        }
    ]

    with pytest.raises(ValueError, match="seeded_mock 已退役"):
        normalize_product_research_config(config)


def test_normalize_config_rejects_retired_target_market_aliases() -> None:
    config = default_product_research_config()
    config["target_markets"] = [
        {
            "marketId": "amazon-us",
            "platform": "amazon",
            "site": "amazon.com",
            "displayName": "Amazon US",
            "searchMethods": [],
        }
    ]

    with pytest.raises(ValueError, match="已退役字段"):
        normalize_product_research_config(config)


def test_ai_search_method_implements_search_method_contract() -> None:
    assert isinstance(AiSearchMethod(), ProductResearchSearchMethod)


def test_normalize_search_request_ignores_keywords() -> None:
    config = normalize_product_research_config(default_product_research_config())
    body = hot_product_payload()
    body["keywords"] = ["pet storage"]

    normalized = product_research_service.normalize_search_request(body, config)

    assert normalized["keywords"] == []


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


def test_ai_web_search_provider_connection_uses_function_binding_model(tmp_path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_chat_json(app_dir, app_config, use_case_id, messages, **kwargs):
        seen["use_case_id"] = use_case_id
        seen["model_id"] = product_research_service.ai_gateway.resolve_model_for_use_case(
            app_dir,
            app_config,
            use_case_id,
        )["id"]
        seen["prompt"] = messages[1]["content"]
        return {
            "items": [
                {
                    "title": "Pet storage baskets trend",
                    "source_url": "https://example.com/pet-storage-trend",
                }
            ]
        }

    monkeypatch.setattr(
        product_research_service.ai_gateway,
        "chat_json",
        fake_chat_json,
    )
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
        },
    }

    app_config = web_search_app_config_with_prompt_file(
        tmp_path,
        "Test market {$market}; language {$language}; keyword {$keyword}",
    )
    config = default_product_research_config()

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
        normalize_product_research_config(config),
        tmp_path,
        app_config,
    )

    assert result["ok"] is True
    assert result["items_found"] == 1
    assert result["sample"]["source_url"] == "https://example.com/pet-storage-trend"
    assert seen["use_case_id"] == "research.web_search"
    assert seen["model_id"] == "web_search_model"
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
