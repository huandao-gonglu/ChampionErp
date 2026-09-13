from __future__ import annotations

import subprocess
import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from erp_web.schemas.copy import CopyQualityReview
from erp_web.services.ai_model_factory import PydanticModelBinding

from conftest import assert_no_old_path
from erp_web.context import get_context
from erp_web.runtime_units import publish_helpers
from erp_web.services import (
    ai_gateway,
    ai_gateway_providers,
    ai_model_config,
    browser_ai_runtime,
    copy_service,
)
from tests.runtime_test_utils import temp_app_context
from tests.ai_function_model_streaming import streaming_function_model


def _api_model() -> dict[str, object]:
    return {
        "id": "copy_model",
        "name": "Copy Model",
        "connection_type": "api",
        "provider": "OpenAI",
        "provider_id": "openai",
        "api_style": "openai_compatible",
        "api_key": "test-key",
        "base_url": "https://api.example.com/v1",
        "model": "gpt-test",
        "capabilities": ["chat", "json"],
        "enabled": True,
    }


def _patch_structured_copy(
    monkeypatch,
    payload: dict[str, object],
    *,
    writer=None,
    reviewer=None,
) -> dict[str, object]:
    """使用真实 Pydantic Agent，只替换远端生成和独立复核的模型响应。"""
    seen = {"requests": [], "reviews": []}

    def generate(messages, info):
        seen["requests"].append(list(messages))
        seen["output_schema"] = info.model_request_parameters.output_object.json_schema
        seen["messages"] = [
            {"role": "user", "content": part.content}
            for message in messages
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]
        candidate = writer(messages, info) if writer else payload
        return ModelResponse(parts=[TextPart(json.dumps(candidate, ensure_ascii=False))])

    model = streaming_function_model(FunctionModel(generate))

    def binding(*_args, **_kwargs):
        seen["use_case"] = _args[2]
        return PydanticModelBinding(
            model=model, model_settings={"temperature": 0},
            model_id="bound_copy_model", model_name="test-copy",
            provider_id="test", provider_family="test", api_style="chat_completions",
            model_config={"provider": "Test Provider"},
        )

    def fake_chat(*_args, **kwargs):
        assert kwargs["output_type"] is CopyQualityReview
        review_input = json.loads(kwargs["messages"][1]["content"])
        seen["reviews"].append(review_input)
        if reviewer:
            return reviewer(review_input)
        return CopyQualityReview(language_matches=True, explanation="测试文案符合事实及目标语言。")

    monkeypatch.setattr(copy_service, "create_pydantic_model_binding_for_use_case", binding)
    monkeypatch.setattr(copy_service.ai_gateway, "chat_structured", fake_chat)
    return seen


def test_generate_copy_without_api_key_does_not_create_fallback_copy(
    app_dir: Path,
) -> None:
    result = copy_service.generate_copy(
        str(app_dir),
        {
            "name": "Manual test organizer",
            "materials": ["PP"],
            "selling_points": ["Foldable"],
        },
        {
            "ai_models": [
                {
                    **_api_model(),
                    "api_key": "",
                    "api_key_env": "MISSING_TEST_API_KEY",
                }
            ]
        },
        target_market="mercadolibre",
        language="Spanish (Mexico)",
    )

    assert result["ok"] is False
    assert "API Key" in result["error"]
    assert result["copy"] == {}


def test_generate_copy_uses_bound_model_and_registry_language(
    app_dir: Path,
    monkeypatch,
) -> None:
    seen = _patch_structured_copy(
        monkeypatch,
        {
            "title": "Органайзер для дома",
            "description": "Компактный органайзер для хранения вещей дома.",
        },
    )

    result = copy_service.generate_copy(
        str(app_dir),
        {"name": "Manual organizer"},
        {"ai_models": []},
        target_market="ozon",
    )

    assert result["ok"] is True
    assert result["language"] == "ru-RU"
    assert result["provider"] == "Test Provider"
    assert result["copy"]["bullets"] == []
    assert seen["use_case"] == "copy.generate"
    assert "global_title" not in seen["output_schema"]["properties"]


def test_generate_copy_rejects_overlong_title_instead_of_truncating(
    app_dir: Path,
    monkeypatch,
) -> None:
    overlong_title = "x" * 61
    _patch_structured_copy(
        monkeypatch,
        {"title": overlong_title, "description": "Description"},
    )

    result = copy_service.generate_copy(
        str(app_dir),
        {"name": "Manual organizer"},
        {"ai_models": []},
        target_market="mercadolibre",
        language="en-US",
    )

    assert result["ok"] is False
    assert "超过 60 个字符" in result["error"]
    assert result["copy"] == {}


@pytest.mark.parametrize(
    "mercadolibre_draft",
    [
        {"site": "CBT"},
        {"target_sites": [{"platform": "mercadolibre", "site": "CBT"}]},
    ],
    ids=("draft-site", "target-sites"),
)
def test_generate_copy_for_cbt_requires_and_preserves_english_global_title(
    app_dir: Path,
    monkeypatch,
    mercadolibre_draft: dict[str, object],
) -> None:
    global_title = "Foldable Storage Organizer"
    seen = _patch_structured_copy(
        monkeypatch,
        {
            "global_title": global_title,
            "title": "Organizador de almacenamiento plegable",
            "description": "Organizador plegable para guardar artículos del hogar.",
        },
    )

    result = copy_service.generate_copy(
        str(app_dir),
        {
            "name": "Foldable storage organizer",
            "drafts": {"mercadolibre": mercadolibre_draft},
        },
        {"ai_models": []},
        target_market="mercadolibre",
        language="es-MX",
    )

    assert result["ok"] is True
    assert result["copy"]["global_title"] == global_title
    schema = seen["output_schema"]
    assert "global_title" in schema["required"]
    global_title_description = schema["properties"]["global_title"]["description"]
    assert "English" in global_title_description or "英文" in global_title_description
    user_prompt = next(
        message["content"]
        for message in seen["messages"]
        if message["role"] == "user"
    )
    assert "global_title" not in user_prompt


@pytest.mark.parametrize(
    ("global_title", "error_marker"),
    [(None, "global_title"), ("x" * 61, "60")],
    ids=("missing", "overlong"),
)
def test_generate_copy_for_cbt_rejects_invalid_global_title(
    app_dir: Path,
    monkeypatch,
    global_title: str | None,
    error_marker: str,
) -> None:
    payload: dict[str, object] = {
        "title": "Organizador plegable",
        "description": "Organizador plegable para el hogar.",
    }
    if global_title is not None:
        payload["global_title"] = global_title
    seen = _patch_structured_copy(monkeypatch, payload)

    result = copy_service.generate_copy(
        str(app_dir),
        {
            "name": "Foldable organizer",
            "drafts": {"mercadolibre": {"site": "CBT"}},
        },
        {"ai_models": []},
        target_market="mercadolibre",
        language="es-MX",
    )

    assert result["ok"] is False
    assert result["copy"] == {}
    retry_parts = [
        part
        for messages in seen["requests"]
        for message in messages
        for part in message.parts
        if isinstance(part, RetryPromptPart)
    ]
    assert retry_parts
    assert error_marker in str([part.content for part in retry_parts])
    assert len(seen["requests"]) == 3


@pytest.mark.parametrize(
    ("target_market", "product"),
    [
        (
            "mercadolibre",
            {
                "name": "Mexico organizer",
                "drafts": {
                    "mercadolibre": {
                        "site": "MLM",
                        "target_sites": [{"site": "MLM"}],
                    }
                },
            },
        ),
        (
            "ozon",
            {
                "name": "Ozon organizer",
                "drafts": {"mercadolibre": {"site": "CBT"}},
            },
        ),
    ],
    ids=("mercadolibre-non-cbt", "non-mercadolibre"),
)
def test_generate_copy_outside_mercadolibre_cbt_does_not_require_global_title(
    app_dir: Path,
    monkeypatch,
    target_market: str,
    product: dict[str, object],
) -> None:
    seen = _patch_structured_copy(
        monkeypatch,
        {
            "title": "Localized organizer",
            "description": "Localized organizer description.",
        },
    )

    result = copy_service.generate_copy(
        str(app_dir),
        product,
        {"ai_models": []},
        target_market=target_market,
        language="en-US",
    )

    assert result["ok"] is True
    assert "global_title" not in result["copy"]
    assert "global_title" not in seen["output_schema"]["properties"]
    user_prompt = next(
        message["content"]
        for message in seen["messages"]
        if message["role"] == "user"
    )
    assert "global_title" not in user_prompt


def test_configured_copy_prompt_contains_target_and_product_context(
    app_dir: Path,
) -> None:
    prompt = copy_service.build_copy_prompt_from_config(
        str(app_dir),
        {},
        {"name": "Manual organizer", "selling_points": ["Foldable"]},
        "ozon",
        "ru-RU",
        "rewrite",
    )

    assert "ru-RU" in prompt["user"]
    assert "Ozon" in prompt["user"]
    assert "Manual organizer" in prompt["user"]
    assert "{$" not in prompt["user"]


def test_copy_retry_receives_previous_draft_and_exact_review_feedback(
    app_dir: Path, monkeypatch,
) -> None:
    rejected = {"title": "Almohadilla", "description": "Para gato hidráulico."}
    corrected = {"title": "Almohadilla", "description": "Apoyo de goma para gato."}
    reason = "来源只说明千斤顶，没有液压类型依据；删除 hidráulico。"

    def writer(messages, _info):
        has_feedback = any(
            isinstance(part, RetryPromptPart)
            for message in messages for part in message.parts
        )
        return corrected if has_feedback else rejected

    def reviewer(review_input):
        invalid = review_input["generated_copy"]["description"] == rejected["description"]
        return CopyQualityReview(
            language_matches=True,
            unsupported_claims=[reason] if invalid else [],
            explanation="存在无依据声称。" if invalid else "已删除无依据限定词。",
        )

    seen = _patch_structured_copy(monkeypatch, rejected, writer=writer, reviewer=reviewer)
    result = copy_service.generate_copy(
        str(app_dir),
        {"name": "千斤顶橡胶垫", "weight_kg": "0.13", "source": {"attributes": {"材质": "橡胶"}}},
        {}, language="es",
    )

    assert result["ok"] is True
    assert result["copy"]["description"] == corrected["description"]
    assert len(seen["requests"]) == len(seen["reviews"]) == 2
    second_parts = [part for message in seen["requests"][1] for part in message.parts]
    assert any(isinstance(part, TextPart) and rejected["description"] in part.content for part in second_parts)
    assert any(isinstance(part, RetryPromptPart) and reason in str(part.content) for part in second_parts)
    for review_input in seen["reviews"]:
        facts = json.loads(review_input["product_facts"])
        assert facts["Weight (kg)"] == "0.13"
        assert "Weight" not in facts
    assert "Weight (kg)" in seen["messages"][0]["content"]

    with get_context().db._connect() as connection:
        histories = connection.execute("SELECT messages_json FROM pydantic_message_histories").fetchall()
    assert len(histories) == 1
    history = histories[0]["messages_json"].decode("utf-8")
    assert reason in history
    assert rejected["description"] in history
    assert corrected["description"] in history


def test_copy_rejection_exhausts_native_retries_without_saving_draft(
    app_dir: Path, monkeypatch,
) -> None:
    from erp_web.runtime_units.content_capabilities import ContentCapabilityScope, copy_generate
    from erp_web.schemas.ai_trace import AiExecutionContext
    from erp_web.schemas.content_capabilities import CopyGenerateRequest
    from erp_web.services.capability_errors import BusinessCapabilityError

    context = get_context()
    product = context.products.save_product({
        "product_id": "copy-rejected", "name": "橡胶垫",
        "drafts": {"mercadolibre": {"enabled": True, "title": "原始标题"}},
    })
    draft_id = product["drafts"]["mercadolibre"]["draft_id"]
    before = context.db.load_product_model("copy-rejected")
    reason = "来源没有防划痕功能的依据。"
    seen = _patch_structured_copy(
        monkeypatch,
        {"global_title": "Rubber Jack Pad", "title": "Almohadilla", "description": "Evita arañazos."},
        reviewer=lambda _input: CopyQualityReview(
            language_matches=True, unsupported_claims=[reason], explanation="声明无依据。",
        ),
    )

    with pytest.raises(BusinessCapabilityError) as error:
        copy_generate(
            CopyGenerateRequest(draft_id=draft_id, language="es"),
            ContentCapabilityScope(context.products, lambda: {}),
            AiExecutionContext.create(timeout_seconds=30, budget_profile="test"),
        )

    assert error.value.code == "COPY_GENERATE_FAILED"
    assert reason in str(error.value)
    assert len(seen["requests"]) == len(seen["reviews"]) == 3
    assert context.db.load_product_model("copy-rejected") == before


def test_copy_review_transport_error_does_not_request_content_rewrite(
    app_dir: Path, monkeypatch,
) -> None:
    def reviewer(_review_input):
        raise TimeoutError("测试复核超时")

    seen = _patch_structured_copy(
        monkeypatch, {"title": "Almohadilla", "description": "Goma."}, reviewer=reviewer,
    )
    result = copy_service.generate_copy(str(app_dir), {"name": "橡胶垫"}, {}, language="es")
    assert result["ok"] is False
    assert result["copy"] == {}
    assert len(seen["requests"]) == len(seen["reviews"]) == 1


def test_configured_copy_prompt_does_not_duplicate_output_schema(
    app_dir: Path,
) -> None:
    cbt_prompt = copy_service.build_copy_prompt_from_config(
        str(app_dir),
        {},
        {
            "name": "Manual organizer",
            "drafts": {"mercadolibre": {"site": "CBT"}},
        },
        "mercadolibre",
        "es-MX",
        "rewrite",
    )

    assert "global_title" not in cbt_prompt["user"]
    assert not any(
        field_declaration in cbt_prompt["user"]
        for field_declaration in (
            "title: string",
            "description: string",
            "bullets: array",
            "alt_titles: array",
            "search_keywords: array",
        )
    )


def test_copy_service_does_not_hardcode_keys(
    repo_dir: Path,
    old_path_markers: tuple[str, ...],
) -> None:
    assert_no_old_path((repo_dir / "erp_web/services/copy_service.py").read_text(), old_path_markers)


def test_api_chat_uses_pydantic_direct_boundary(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_direct_chat(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        ai_gateway_providers.ai_direct_request_service,
        "chat_json",
        fake_direct_chat,
    )

    result = ai_gateway.chat_json(
        tmp_path,
        {"ai_models": [_api_model()]},
        "copy.generate",
        [{"role": "user", "content": "Return JSON."}],
        temperature=0.35,
        max_tokens=128,
        stream=False,
    )

    assert result == {"ok": True}
    assert seen["use_case_id"] == "copy.generate"
    assert seen["temperature"] == 0.35
    assert seen["max_tokens"] == 128
    assert seen["required_capabilities"] == ("chat", "json")
    assert all(
        provider.provider_id != "pydantic_direct"
        for provider in ai_gateway.AI_PROVIDER_REGISTRY
    )


def test_api_chat_rejects_business_extra_body(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        ai_gateway_providers.ai_direct_request_service,
        "chat_json",
        lambda **kwargs: pytest.fail("非法 extra_body 不应到达 Pydantic 请求"),
    )

    with pytest.raises(ValueError, match="不允许业务层传入 extra_body"):
        ai_gateway.chat_json(
            tmp_path,
            {"ai_models": [_api_model()]},
            "copy.generate",
            [{"role": "user", "content": "Return JSON."}],
            extra_body={"tools": []},
            stream=False,
        )


def test_api_models_never_enter_external_provider_registry() -> None:
    normalized = ai_model_config.normalize_ai_model(_api_model())
    with pytest.raises(RuntimeError, match="Pydantic Direct Model"):
        ai_gateway._provider_for_model(normalized)
    assert {
        type(provider) for provider in ai_gateway.AI_PROVIDER_REGISTRY
    } == {ai_gateway.CodexCliProvider, ai_gateway.BrowserAiProvider}


def test_codex_cli_chat_json_uses_local_command(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        ai_gateway.shutil,
        "which",
        lambda command: f"/usr/local/bin/{command}" if command == "codex" else "",
    )

    def fake_run(args, input, text, capture_output, cwd, timeout, check):
        Path(args[args.index("-o") + 1]).write_text(
            '{"ok":true,"title":"Codex OK"}',
            encoding="utf-8",
        )
        calls.append({"args": args, "input": input, "cwd": cwd, "timeout": timeout})
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(ai_gateway.subprocess, "run", fake_run)
    result = ai_gateway.chat_json(
        tmp_path,
        {
            "ai_models": [
                {
                    "id": "codex_cli_text",
                    "connection_type": "cli",
                    "provider": "Codex CLI",
                    "cli_tool": "codex",
                    "command": "codex",
                    "model": "gpt-5-codex",
                    "capabilities": ["chat", "json"],
                }
            ]
        },
        "copy.generate",
        [{"role": "user", "content": "Return title."}],
        stream=False,
    )

    assert result == {"ok": True, "title": "Codex OK"}
    assert calls[0]["args"][:2] == ["codex", "exec"]
    assert "最终输出必须是一个合法 JSON 对象" in calls[0]["input"]


def test_browser_ai_provider_uses_browser_runtime(tmp_path: Path, monkeypatch) -> None:
    prompts: list[str] = []

    def fake_run_chat(app_dir, model, prompt, timeout=180):
        prompts.append(prompt)
        return browser_ai_runtime.BrowserAiRunResult(
            text='{"ok":true,"title":"Browser OK"}',
            image_urls=[],
            provider="chatgpt",
            browser_url="https://chatgpt.com/",
            profile_dir=str(tmp_path / "browser_profile"),
            port=9333,
            ready=True,
        )

    monkeypatch.setattr(
        ai_gateway.browser_ai_runtime,
        "run_browser_ai_chat",
        fake_run_chat,
    )
    browser_model = {
        "id": "browser_text",
        "connection_type": "browser",
        "provider": "Browser AI",
        "browser_provider": "chatgpt",
        "capabilities": ["chat", "json"],
        "enabled": True,
    }
    result = ai_gateway.chat_json(
        tmp_path,
        {"ai_models": [browser_model]},
        "copy.generate",
        [{"role": "user", "content": "Return title."}],
        stream=False,
    )

    assert result == {"ok": True, "title": "Browser OK"}
    assert "最终输出必须是一个合法 JSON 对象" in prompts[0]


def test_assign_upc_writes_current_product_and_returns_full_payload(
    tmp_path: Path,
) -> None:
    with temp_app_context(tmp_path):
        (tmp_path / "upc_pool.json").write_text(
            '{"values":["725272000007"],"used":[]}',
            encoding="utf-8",
        )
        get_context().products.save_product(
            {
                "name": "UPC test product",
                # 只有具备真实业务内容的草稿才会持久化；默认草稿模板不是
                # 独立平台事实，不能用商品主档 UPC 隐式回填。
                "drafts": {
                    "mercadolibre": {
                        "enabled": True,
                        "title": "UPC test Mercado Libre draft",
                    }
                },
            }
        )

        result = publish_helpers.assign_upc()

        assert result["ok"] is True
        assert result["upc"] == "725272000007"
        assert result["product"]["upc"] == "725272000007"
        assert result["product"]["drafts"]["mercadolibre"]["upc"] == "725272000007"
        assert isinstance(result["productsIndex"], list)
