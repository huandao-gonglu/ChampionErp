from __future__ import annotations

from collections import deque
from typing import Any

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.settings import ModelSettings

from erp_web.context import get_context
from erp_web.schemas.ai_tools import AiToolDefinition
from erp_web.runtime_units.category_tools import (
    CategoryCandidateLedger,
    build_category_search_toolset,
)
from erp_web.services.ai_agent_factory import AiAgentExecutionError, AiAgentFactory
from erp_web.services.ai_model_factory import PydanticModelBinding
from erp_web.services.ai_tool_registry import AiToolSet, deadline_aware_tool_executor
from erp_web.services.category_match_agent_service import run_category_match_agent


class Searcher:
    def __init__(self, results: list[list[dict[str, Any]]]) -> None:
        self.results = deque(results)
        self.keywords: list[str] = []

    def search_categories(self, keyword: str) -> dict[str, Any]:
        self.keywords.append(keyword)
        return {
            "keyword": keyword,
            "candidates": self.results.popleft(),
            "source": "test",
        }


def candidate(category_id: str) -> dict[str, Any]:
    return {
        "category_id": category_id,
        "name": "Ventiladores",
        "path_segments": ["Hogar", "Ventiladores"],
        "platform": "mercadolibre",
        "site": "MLM",
        "publishable": True,
    }


PAYLOAD = {
    "target": {"platform": "mercadolibre", "site": "MLM", "language": "es-MX"},
    "product": {"source": {"title": "风扇"}, "target": {"title": "Ventilador"}},
}


def factory_for(model: FunctionModel) -> AiAgentFactory:
    context = get_context()

    def binding(*args, **kwargs):
        del args, kwargs
        return PydanticModelBinding(
            model=model,
            model_settings=ModelSettings(temperature=0),
            model_id="test-model",
            model_name="test-model",
            provider_id="test",
            provider_family="test",
            api_style="chat_completions",
        )

    return AiAgentFactory(
        app_dir=context.paths.app_dir,
        app_config={},
        journal=context.ai_journal,
        model_binding_factory=binding,
    )


def final_output(agent_info: AgentInfo, payload: dict[str, Any], call_id: str) -> ModelResponse:
    assert len(agent_info.output_tools) == 1
    return ModelResponse(
        parts=[ToolCallPart(agent_info.output_tools[0].name, payload, tool_call_id=call_id)]
    )


def test_agent_uses_native_tool_call_and_typed_output() -> None:
    searcher = Searcher([[candidate("MLM-FAN")]])
    ledger = CategoryCandidateLedger()
    toolset = build_category_search_toolset(searcher=searcher, ledger=ledger)
    turns = 0

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        nonlocal turns
        turns += 1
        if turns == 1:
            assert "candidates" not in str(messages)
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_categories",
                        {"keyword": "ventilador"},
                        tool_call_id="search-1",
                    )
                ]
            )
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert returns[-1].tool_call_id == "search-1"
        return final_output(
            agent_info,
            {
                "selected_category_id": "MLM-FAN",
                "abstained": False,
                "model_confidence": 0.91,
                "evidence": ["商品主体一致"],
            },
            "final-1",
        )

    result = run_category_match_agent(
        PAYLOAD,
        toolset,
        ledger,
        timeout_seconds=10,
        factory=factory_for(FunctionModel(model)),
    )

    assert result.output["selected_category_id"] == "MLM-FAN"
    assert result.output["model_confidence"] == 0.91
    assert searcher.keywords == ["ventilador"]
    assert result.trace["conversation_id"].startswith("aic_")
    assert result.trace["task_run_id"].startswith("task_")
    assert result.outcome is not None
    assert result.outcome.usage["tool_calls"] == 1
    events = get_context().ai_journal.read_events(result.trace["conversation_id"])
    request_event = next(event for event in events if event.get("name") == "agent.request")
    assert "风扇" in str(request_event["value"])
    assert request_event["value"]["limits"]["max_model_requests"] == 5
    transcript_event = next(
        event for event in events if event.get("name") == "agent.transcript"
    )
    assert [row["kind"] for row in transcript_event["value"]["messages"]] == [
        "request",
        "response",
        "request",
        "response",
        "request",
    ]
    started = next(event for event in events if event.get("name") == "TOOL_CALL_STARTED")
    finished = next(event for event in events if event.get("name") == "TOOL_CALL_FINISHED")
    assert started["value"]["arguments"] == {"keyword": "ventilador"}
    assert finished["value"]["output"]["candidates"][0]["category_id"] == "MLM-FAN"


def test_output_validator_retries_before_any_search() -> None:
    searcher = Searcher([[candidate("MLM-FAN")]])
    ledger = CategoryCandidateLedger()
    toolset = build_category_search_toolset(searcher=searcher, ledger=ledger)
    turns = 0

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        nonlocal turns
        turns += 1
        output = {
            "selected_category_id": "MLM-FAN",
            "abstained": False,
            "model_confidence": 0.8,
            "evidence": [],
        }
        if turns == 1:
            return final_output(agent_info, output, "premature")
        if turns == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_categories",
                        {"keyword": "ventilador"},
                        tool_call_id="search-after-retry",
                    )
                ]
            )
        return final_output(agent_info, output, "valid")

    result = run_category_match_agent(
        PAYLOAD,
        toolset,
        ledger,
        timeout_seconds=10,
        factory=factory_for(FunctionModel(model)),
    )

    assert result.output["selected_category_id"] == "MLM-FAN"
    assert turns == 3
    assert searcher.keywords == ["ventilador"]


def test_unknown_category_stays_a_stable_agent_error_after_retries() -> None:
    searcher = Searcher([[candidate("MLM-FAN")]])
    ledger = CategoryCandidateLedger()
    toolset = build_category_search_toolset(searcher=searcher, ledger=ledger)
    turns = 0

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        nonlocal turns
        turns += 1
        if turns == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_categories",
                        {"keyword": "ventilador"},
                        tool_call_id="search-1",
                    )
                ]
            )
        return final_output(
            agent_info,
            {
                "selected_category_id": "MLM-INVENTED",
                "abstained": False,
                "model_confidence": 0.9,
                "evidence": [],
            },
            f"invalid-{turns}",
        )

    with pytest.raises(AiAgentExecutionError) as captured:
        run_category_match_agent(
            PAYLOAD,
            toolset,
            ledger,
            timeout_seconds=10,
            factory=factory_for(FunctionModel(model)),
        )

    assert captured.value.code == "MODEL_SELECTED_UNKNOWN_CATEGORY"
    assert "MLM-INVENTED" not in str(captured.value)
    assert searcher.keywords == ["ventilador"]
    events = get_context().ai_journal.read_events(captured.value.conversation_id)
    transcript_event = next(
        event for event in events if event.get("name") == "agent.transcript"
    )
    transcript = str(transcript_event["value"])
    assert "MLM-INVENTED" in transcript
    assert "selected_category_id 必须来自本次 search_categories 的真实结果" in transcript
    assert events[-1]["type"] == "RUN_ERROR"


def test_abstain_requires_three_different_effective_searches() -> None:
    searcher = Searcher([[], [], []])
    ledger = CategoryCandidateLedger()
    toolset = build_category_search_toolset(searcher=searcher, ledger=ledger)
    keywords = iter(["ventilador", "ventilador de mesa", "aparato de ventilación"])
    turns = 0

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        nonlocal turns
        turns += 1
        if turns in {1, 2, 3}:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_categories",
                        {"keyword": next(keywords)},
                        tool_call_id=f"search-{turns}",
                    )
                ]
            )
        return final_output(
            agent_info,
            {
                "selected_category_id": "",
                "abstained": True,
                "model_confidence": 0.1,
                "evidence": ["三次搜索均无结果"],
            },
            "abstain",
        )

    result = run_category_match_agent(
        PAYLOAD,
        toolset,
        ledger,
        timeout_seconds=10,
        factory=factory_for(FunctionModel(model)),
    )

    assert result.output["abstained"] is True
    assert ledger.search_count == 3
    assert searcher.keywords == [
        "ventilador",
        "ventilador de mesa",
        "aparato de ventilación",
    ]


def test_duplicate_keyword_is_deduplicated_and_does_not_count_as_three_searches() -> None:
    searcher = Searcher([[]])
    ledger = CategoryCandidateLedger()
    toolset = build_category_search_toolset(searcher=searcher, ledger=ledger)
    turns = 0

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        nonlocal turns
        del messages
        turns += 1
        if turns in {1, 2}:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_categories",
                        {"keyword": "ventilador"},
                        tool_call_id=f"duplicate-{turns}",
                    )
                ]
            )
        return final_output(
            agent_info,
            {
                "selected_category_id": "",
                "abstained": True,
                "model_confidence": 0.1,
                "evidence": [],
            },
            f"early-{turns}",
        )

    with pytest.raises(AiAgentExecutionError) as captured:
        run_category_match_agent(
            PAYLOAD,
            toolset,
            ledger,
            timeout_seconds=10,
            factory=factory_for(FunctionModel(model)),
        )

    assert captured.value.code == "CATEGORY_SEARCH_INCOMPLETE"
    assert searcher.keywords == ["ventilador"]
    assert ledger.search_count == 1


def test_fourth_unique_search_is_stopped_by_profile_tool_limit() -> None:
    searcher = Searcher([[], [], []])
    ledger = CategoryCandidateLedger()
    toolset = build_category_search_toolset(searcher=searcher, ledger=ledger)
    turns = 0

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        nonlocal turns
        del messages, agent_info
        turns += 1
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "search_categories",
                    {"keyword": f"keyword-{turns}"},
                    tool_call_id=f"search-{turns}",
                )
            ]
        )

    with pytest.raises(AiAgentExecutionError) as captured:
        run_category_match_agent(
            PAYLOAD,
            toolset,
            ledger,
            timeout_seconds=10,
            factory=factory_for(FunctionModel(model)),
        )

    assert captured.value.code == "AI_AGENT_USAGE_LIMIT_EXCEEDED"
    assert searcher.keywords == ["keyword-1", "keyword-2", "keyword-3"]
    assert ledger.search_count == 3


def test_provider_exception_is_normalized_without_fallback_or_secret() -> None:
    ledger = CategoryCandidateLedger()
    toolset = build_category_search_toolset(searcher=Searcher([[]]), ledger=ledger)
    secret = "provider-secret-body"

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        del messages, agent_info
        raise ModelAPIError("test-model", secret)

    with pytest.raises(AiAgentExecutionError) as captured:
        run_category_match_agent(
            PAYLOAD,
            toolset,
            ledger,
            timeout_seconds=10,
            factory=factory_for(FunctionModel(model)),
        )

    assert captured.value.code == "MODEL_PROVIDER_ERROR"
    assert secret not in str(captured.value)
    assert captured.value.trace_id
    assert captured.value.run_id
    assert captured.value.conversation_id
    events = get_context().ai_journal.read_events(captured.value.conversation_id)
    assert events[-1]["type"] == "RUN_ERROR"
    assert events[-1]["trace_id"] == captured.value.trace_id
    assert events[-1]["run_id"] == captured.value.run_id
    assert secret not in str(events)


def test_unexpected_category_approval_finishes_persisted_pending_state() -> None:
    executions = 0

    def executor(arguments, context):
        nonlocal executions
        del arguments, context
        executions += 1
        return {"keyword": "fan", "candidates": [], "source": "test"}

    definition = AiToolDefinition(
        name="search_categories",
        version="1",
        description="测试审批边界",
        input_schema={
            "type": "object",
            "required": ["keyword"],
            "properties": {"keyword": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        required_permission="category.read",
        side_effect="none",
        approval_required=True,
    )
    toolset = AiToolSet.bind(
        "category.search",
        [definition],
        {definition.name: deadline_aware_tool_executor(executor)},
    )

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        del messages, agent_info
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "search_categories",
                    {"keyword": "fan"},
                    tool_call_id="unexpected-approval",
                )
            ]
        )

    agent_factory = factory_for(FunctionModel(model))
    before = set(agent_factory.state_store.root.glob("*.json"))
    with pytest.raises(AiAgentExecutionError) as captured:
        run_category_match_agent(
            PAYLOAD,
            toolset,
            CategoryCandidateLedger(),
            timeout_seconds=10,
            factory=agent_factory,
        )

    assert captured.value.code == "AI_TOOL_APPROVAL_REQUIRED"
    assert executions == 0
    created = set(agent_factory.state_store.root.glob("*.json")) - before
    assert len(created) == 1
    assert agent_factory.state_store.load(next(iter(created)).stem).status == "failed"
