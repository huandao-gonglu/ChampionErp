from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from erp_web.services import ai_gateway_providers
from erp_web.services.ai_provider_contracts import (
    CAPABILITY_CHAT_JSON,
    AiChatProvider,
)


class _StructuredCopy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="本地化标题")
    count: int = Field(description="卖点数量")


class _FakeChatProvider(AiChatProvider):
    provider_id = "fake_chat"

    def __init__(self) -> None:
        self.request: Any | None = None

    def supports(
        self,
        model: dict[str, Any],
        capability: str = CAPABILITY_CHAT_JSON,
    ) -> bool:
        return capability == CAPABILITY_CHAT_JSON

    def chat_json(self, request: Any) -> dict[str, Any]:
        self.request = request
        return {"title": "本地化标题", "count": "2"}

    def test_model(
        self,
        app_dir: Path | str,
        model: dict[str, Any],
        raw_model: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"ok": True}


def test_chat_structured_api_forwards_output_type_without_rewriting_messages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    messages = [{"role": "user", "content": "生成本地化文案。"}]
    original_messages = [dict(message) for message in messages]
    expected = _StructuredCopy(title="本地化标题", count=2)
    captured: dict[str, Any] = {}
    client = SimpleNamespace(
        app_dir=tmp_path,
        use_case_id="copy.generate",
        model={"id": "api-model"},
        required_capabilities=("chat", "json"),
        timeout_seconds=45,
        generation_settings={"temperature": 0},
        connection_type=ai_gateway_providers.ai_model_config.CONNECTION_TYPE_API,
    )

    monkeypatch.setattr(
        ai_gateway_providers.AiProviderClient,
        "for_use_case",
        lambda *_args, **_kwargs: client,
    )

    def fake_chat_structured(**kwargs: Any) -> _StructuredCopy:
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(
        ai_gateway_providers.ai_direct_request_service,
        "chat_structured",
        fake_chat_structured,
    )

    result = ai_gateway_providers.chat_structured(
        tmp_path,
        {},
        "copy.generate",
        messages,
        output_type=_StructuredCopy,
        stream=False,
    )

    assert result is expected
    assert captured["output_type"] is _StructuredCopy
    assert captured["messages"] is messages
    assert messages == original_messages
    assert "schema" not in captured


def test_chat_structured_non_api_appends_generated_schema_and_validates_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    messages = [
        {"role": "system", "content": "仅生成商品文案。"},
        {"role": "user", "content": "生成本地化文案。"},
    ]
    original_messages = [dict(message) for message in messages]
    provider = _FakeChatProvider()
    client = SimpleNamespace(
        app_dir=tmp_path,
        use_case_id="copy.generate",
        model={"id": "local-model"},
        required_capabilities=("chat", "json"),
        timeout_seconds=90,
        generation_settings=None,
        connection_type=ai_gateway_providers.ai_model_config.CONNECTION_TYPE_CLI,
        provider_for=lambda capability: provider,
    )
    monkeypatch.setattr(
        ai_gateway_providers.AiProviderClient,
        "for_use_case",
        lambda *_args, **_kwargs: client,
    )

    result = ai_gateway_providers.chat_structured(
        tmp_path,
        {},
        "copy.generate",
        messages,
        output_type=_StructuredCopy,
        stream=False,
    )

    assert result == _StructuredCopy(title="本地化标题", count=2)
    assert isinstance(result.count, int)
    assert provider.request is not None
    assert provider.request.messages[:-1] == messages
    schema_message = provider.request.messages[-1]
    assert schema_message["role"] == "system"
    expected_schema = TypeAdapter(_StructuredCopy).json_schema(mode="validation")
    encoded_schema = json.dumps(
        expected_schema,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert encoded_schema in schema_message["content"]
    assert messages == original_messages
