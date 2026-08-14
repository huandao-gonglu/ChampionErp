from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from erp_web.services.ai_agent_instrumentation import (
    REDACTED_VALUE,
    AiAgentInstrumentation,
    SafeJsonlSpanExporter,
    build_safe_trace_attributes,
    current_trace_id,
    sanitize_trace_value,
)


def _read_spans(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_pydantic_agent_spans_keep_usage_tools_and_trace_correlation(tmp_path: Path) -> None:
    path = tmp_path / "private" / "agent-spans.jsonl"
    instrumentation = AiAgentInstrumentation(path)
    model_calls = 0

    def model_function(messages, agent_info):
        nonlocal model_calls
        del messages, agent_info
        model_calls += 1
        if model_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_product",
                        {"product_id": "不得写入的商品标题"},
                        tool_call_id="call-1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("不得写入的最终结果")])

    agent = Agent(FunctionModel(model_function), name="category_match_contract")
    agent.instrument = instrumentation.settings

    @agent.tool_plain
    def lookup_product(product_id: str) -> dict[str, str]:
        return {"title": product_id, "api_key": "sk-tool-secret-12345678"}

    with instrumentation.start_run_span(
        use_case_id="category.product_match",
        conversation_id="conversation-1",
        invocation_id="invocation-1",
        business_entity_ids={
            "product_id": "product-1",
            "title": "这个字段必须被丢弃",
        },
    ) as trace_context:
        assert current_trace_id() == trace_context.trace_id
        result = agent.run_sync("不得写入的用户 prompt")
        trace_context.set_agent_run_id(str(result.run_id))

    assert result.output == "不得写入的最终结果"
    spans = _read_spans(path)
    names = [str(span["name"]) for span in spans]
    assert "erp.ai.agent.run" in names
    assert any(name.startswith("chat ") for name in names)
    assert "execute_tool lookup_product" in names
    assert any(name.startswith("invoke_agent ") for name in names)
    assert {span["trace_id"] for span in spans} == {trace_context.trace_id}

    outer = next(span for span in spans if span["name"] == "erp.ai.agent.run")
    outer_attributes = outer["attributes"]
    assert outer_attributes["erp.ai.conversation_id"] == "conversation-1"
    assert outer_attributes["erp.ai.invocation_id"] == "invocation-1"
    assert outer_attributes["erp.ai.entity.product_id"] == "product-1"
    assert "erp.ai.entity.title" not in outer_attributes
    assert outer_attributes["erp.ai.trace_id"] == trace_context.trace_id
    assert outer_attributes["erp.ai.agent_run_id"] == str(result.run_id)

    usage_values = [
        value
        for span in spans
        for key, value in span["attributes"].items()
        if "usage" in key
    ]
    assert usage_values
    assert all(isinstance(value, (int, float)) for value in usage_values)

    serialized = path.read_text(encoding="utf-8")
    assert "不得写入的商品标题" not in serialized
    assert "不得写入的最终结果" not in serialized
    assert "不得写入的用户 prompt" not in serialized
    assert "sk-tool-secret-12345678" not in serialized
    assert path.stat().st_mode & 0o777 == 0o600
    assert instrumentation.settings.include_content is False
    assert instrumentation.settings.include_binary_content is False
    assert instrumentation.settings.include_model_request_parameters is False


def test_recursive_scrubbing_covers_credentials_product_fields_and_exceptions(
    tmp_path: Path,
) -> None:
    nested = sanitize_trace_value(
        {
            "safe": {"count": 3},
            "credentials": {"api_key": "sk-nested-secret-12345678"},
            "product": {
                "title": "禁止记录的标题",
                "description": "禁止记录的描述",
            },
            "gen_ai.usage.input_tokens": 17,
        }
    )
    assert nested["safe"] == {"count": 3}
    assert nested["credentials"] == REDACTED_VALUE
    assert nested["product"]["title"] == REDACTED_VALUE
    assert nested["product"]["description"] == REDACTED_VALUE
    assert nested["gen_ai.usage.input_tokens"] == 17

    path = tmp_path / "exceptions.jsonl"
    instrumentation = AiAgentInstrumentation(path)
    with pytest.raises(RuntimeError, match="provider failed"):
        with instrumentation.start_run_span(
            use_case_id="category.product_match",
            invocation_id="invocation-2",
        ) as trace_context:
            trace_context._span.set_attribute("api_key", "sk-live-secret-12345678")
            trace_context._span.set_attribute("product.title", "私密商品标题")
            trace_context._span.set_attribute("gen_ai.usage.input_tokens", 17)
            trace_context._span.set_attribute("gen_ai.usage.cost", 0.012)
            raise RuntimeError("provider failed: api_key=sk-exception-secret-12345678")

    spans = _read_spans(path)
    outer = next(span for span in spans if span["name"] == "erp.ai.agent.run")
    attributes = outer["attributes"]
    assert attributes["api_key"] == REDACTED_VALUE
    assert attributes["product.title"] == REDACTED_VALUE
    assert attributes["gen_ai.usage.input_tokens"] == 17
    assert attributes["gen_ai.usage.cost"] == 0.012
    exception_event = next(event for event in outer["events"] if event["name"] == "exception")
    assert exception_event["attributes"]["exception.type"] == "RuntimeError"
    assert exception_event["attributes"]["exception.message"] == REDACTED_VALUE
    assert exception_event["attributes"]["exception.stacktrace"] == REDACTED_VALUE

    serialized = path.read_text(encoding="utf-8")
    assert "sk-live-secret-12345678" not in serialized
    assert "sk-exception-secret-12345678" not in serialized
    assert "私密商品标题" not in serialized
    assert "provider failed" not in serialized


def test_observability_writer_failure_never_changes_agent_result(tmp_path: Path) -> None:
    def broken_writer(path: Path, line: str) -> None:
        del path, line
        raise OSError("observability backend unavailable")

    exporter = SafeJsonlSpanExporter(tmp_path / "unwritable.jsonl", writer=broken_writer)
    instrumentation = AiAgentInstrumentation(
        tmp_path / "unwritable.jsonl",
        exporter=exporter,
    )
    agent = Agent(
        FunctionModel(
            lambda messages, info: ModelResponse(parts=[TextPart("business result")])
        )
    )
    agent.instrument = instrumentation.settings

    with instrumentation.start_run_span(
        use_case_id="category.product_match",
        conversation_id="conversation-3",
        invocation_id="invocation-3",
    ):
        result = agent.run_sync("business input")

    assert result.output == "business result"
    assert exporter.failed_export_count >= 1
    assert exporter.export(()) is SpanExportResult.SUCCESS


def test_safe_business_attributes_only_accept_fixed_ids() -> None:
    attributes = build_safe_trace_attributes(
        use_case_id="category.product_match",
        conversation_id="conversation-4",
        invocation_id="invocation-4",
        business_entity_ids={
            "product_id": "product-4",
            "store_id": "store-4",
            "title": "禁止记录的标题",
            "api_key": "sk-secret-12345678",
            "Nested.Product_ID": "invalid-key",
        },
    )

    assert attributes == {
        "erp.ai.use_case_id": "category.product_match",
        "erp.ai.conversation_id": "conversation-4",
        "erp.ai.invocation_id": "invocation-4",
        "erp.ai.entity.product_id": "product-4",
        "erp.ai.entity.store_id": "store-4",
    }


def test_opentelemetry_sdk_is_locked_to_the_runtime_version() -> None:
    requirements = (
        Path(__file__).resolve().parents[1] / "requirements.txt"
    ).read_text(encoding="utf-8")
    assert requirements.count("opentelemetry-sdk==1.44.0") == 1
    assert version("opentelemetry-sdk") == "1.44.0"
