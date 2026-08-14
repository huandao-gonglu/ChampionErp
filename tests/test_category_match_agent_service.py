from __future__ import annotations

from collections import deque
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.settings import ModelSettings

from erp_web.context import get_context
from erp_web.schemas.ai_tools import AiToolDefinition
from erp_web.runtime_units.category_tools import (
    CategoryCandidateLedger,
    build_category_match_toolset,
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
        message_store=context.pydantic_messages,
        model_binding_factory=binding,
    )


def toolset_for(searcher: Searcher, ledger: CategoryCandidateLedger) -> AiToolSet:
    return build_category_match_toolset(
        searcher=searcher,
        ledger=ledger,
    ).toolset


def final_output(agent_info: AgentInfo, payload: dict[str, Any], call_id: str) -> ModelResponse:
    assert len(agent_info.output_tools) == 1
    return ModelResponse(
        parts=[ToolCallPart(agent_info.output_tools[0].name, payload, tool_call_id=call_id)]
    )


def test_agent_uses_native_tool_call_and_typed_output() -> None:
    searcher = Searcher([[candidate("MLM-FAN")]])
    ledger = CategoryCandidateLedger()
    toolset = toolset_for(searcher, ledger)
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
    assert result.trace["task_run_id"].startswith("task_")
    assert result.outcome is not None
    assert result.outcome.usage["tool_calls"] == 1
    history = get_context().pydantic_messages.get(result.outcome.conversation_id)
    assert history is not None
    assert history.messages_json == ModelMessagesTypeAdapter.dump_json(
        result.outcome.messages
    )
    persisted = history.model_messages()
    assert "风扇" in str(persisted)
    assert "MLM-FAN" in str(persisted)
    assert any(
        isinstance(part, ToolReturnPart) and part.tool_call_id == "search-1"
        for message in persisted
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


def test_output_validator_retries_before_any_search() -> None:
    searcher = Searcher([[candidate("MLM-FAN")]])
    ledger = CategoryCandidateLedger()
    toolset = toolset_for(searcher, ledger)
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
    toolset = toolset_for(searcher, ledger)
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
    history = get_context().pydantic_messages.get(captured.value.conversation_id)
    assert history is not None
    transcript = str(history.model_messages())
    assert "MLM-INVENTED" in transcript
    assert (
        "selected_category_id 必须来自本次检索工具真实返回的商品类型"
        in transcript
    )


def test_abstain_requires_three_different_effective_searches() -> None:
    searcher = Searcher([[], [], []])
    ledger = CategoryCandidateLedger()
    toolset = toolset_for(searcher, ledger)
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
    toolset = toolset_for(searcher, ledger)
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


def test_search_limit_feedback_precedes_profile_tool_limit() -> None:
    searcher = Searcher([[], [], []])
    ledger = CategoryCandidateLedger()
    toolset = toolset_for(searcher, ledger)
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


def test_provider_api_error_keeps_original_type_and_redacts_secret() -> None:
    ledger = CategoryCandidateLedger()
    toolset = toolset_for(Searcher([[]]), ledger)
    secret = "sk-very-secret-provider-key"

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        del messages, agent_info
        raise ModelAPIError("test-model", f"Provider connection failed for {secret}")

    with pytest.raises(AiAgentExecutionError) as captured:
        run_category_match_agent(
            PAYLOAD,
            toolset,
            ledger,
            timeout_seconds=10,
            factory=factory_for(FunctionModel(model)),
        )

    assert captured.value.code == "ModelAPIError"
    assert "Provider connection failed" in str(captured.value)
    assert secret not in str(captured.value)
    assert captured.value.trace_id
    assert captured.value.run_id
    assert captured.value.conversation_id
    history = get_context().pydantic_messages.get(captured.value.conversation_id)
    assert history is not None
    assert secret not in str(history.model_messages())


def test_provider_http_error_keeps_status_code_message_and_request_id() -> None:
    ledger = CategoryCandidateLedger()
    toolset = toolset_for(Searcher([[]]), ledger)

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        del messages, agent_info
        raise ModelHTTPError(
            403,
            "test-model",
            {
                "error": {
                    "code": "PERMISSION_DENIED",
                    "message": "Free quota exhausted.",
                },
                "request_id": "request-403",
            },
        )

    with pytest.raises(AiAgentExecutionError) as captured:
        run_category_match_agent(
            PAYLOAD,
            toolset,
            ledger,
            timeout_seconds=10,
            factory=factory_for(FunctionModel(model)),
        )

    assert captured.value.code == "PERMISSION_DENIED"
    assert str(captured.value) == (
        "HTTP 403: Free quota exhausted. (request_id=request-403)"
    )
    assert captured.value.retryable is False
    history = get_context().pydantic_messages.get(captured.value.conversation_id)
    assert history is not None
    assert history.model_messages()


def test_empty_provider_response_reports_observed_response_instead_of_schema_error() -> None:
    ledger = CategoryCandidateLedger()
    toolset = toolset_for(Searcher([[]]), ledger)

    def model(messages: list[Any], agent_info: AgentInfo) -> ModelResponse:
        del messages, agent_info
        return ModelResponse(
            parts=[],
            provider_name="alibaba",
            provider_response_id=None,
            provider_details={"background": True},
        )

    with pytest.raises(AiAgentExecutionError) as captured:
        run_category_match_agent(
            PAYLOAD,
            toolset,
            ledger,
            timeout_seconds=10,
            factory=factory_for(FunctionModel(model)),
        )

    assert captured.value.code == "AI_PROVIDER_RESPONSE_INVALID"
    message = str(captured.value)
    assert "provider=alibaba" in message
    assert "background=true" in message
    assert "response_id=null" in message
    assert "parts=0" in message


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

    assert captured.value.code == "TOOL_APPROVAL_REQUIRED"
    assert executions == 0
    history = agent_factory.message_store.get(captured.value.conversation_id)
    assert history is not None
    assert history.model_messages()
    created = set(agent_factory.state_store.root.glob("*.json")) - before
    assert len(created) == 1
    assert agent_factory.state_store.load(next(iter(created)).stem).status == "failed"
