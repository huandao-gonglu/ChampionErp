from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

from erp_web.db import ErpDatabase
from erp_web.services.ai_agent_factory import (
    AiAgentExecutionError,
    AiAgentExecutionProfile,
    AiAgentFactory,
)
from erp_web.services.ai_model_factory import PydanticModelBinding
from erp_web.schemas.ai_tools import AiToolDefinition, AiToolExecutionError
from erp_web.services.ai_tool_registry import AiToolSet, deadline_aware_tool_executor
from erp_web.stores.pydantic_message_store import PydanticMessageStore
from tests.ai_function_model_streaming import streaming_function_model


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str


PROFILE = AiAgentExecutionProfile(
    use_case_id="message.persistence.test",
    output_type=Answer,
    toolset_id="message.persistence.empty",
    budget_profile="message.persistence.v1",
    permissions=frozenset(),
    timeout_seconds=10,
    max_model_requests=2,
    max_tool_calls=1,
    max_tool_output_bytes=4096,
    retries=0,
)
TOOLSET = AiToolSet.bind("message.persistence.empty", [], {})


def _factory(
    tmp_path: Path,
    model: FunctionModel,
) -> tuple[AiAgentFactory, PydanticMessageStore]:
    message_store = PydanticMessageStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    streaming_model = streaming_function_model(model)

    def binding(*args: Any, **kwargs: Any) -> PydanticModelBinding:
        del args, kwargs
        return PydanticModelBinding(
            model=streaming_model,
            model_settings=ModelSettings(temperature=0),
            model_id="test-model",
            model_name="test-model",
            provider_id="test",
            provider_family="test",
            api_style="chat_completions",
        )

    return (
        AiAgentFactory(
            app_dir=tmp_path,
            app_config={},
            message_store=message_store,
            model_binding_factory=binding,
        ),
        message_store,
    )


def test_successful_run_saves_result_all_messages(tmp_path: Path) -> None:
    def model(_messages: list[Any], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"answer": "完成"},
                    tool_call_id="final_1",
                )
            ]
        )

    factory, store = _factory(tmp_path, FunctionModel(model))

    outcome = factory.run_sync(
        profile=PROFILE,
        instructions="返回结构化答案。",
        user_prompt="执行测试。",
        toolset=TOOLSET,
    )

    history = store.get(outcome.conversation_id)
    assert history is not None
    assert history.messages_json == ModelMessagesTypeAdapter.dump_json(outcome.messages)


def test_failed_run_saves_captured_model_messages(tmp_path: Path) -> None:
    def model(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
        raise ModelAPIError("test-model", "provider failure")

    factory, store = _factory(tmp_path, FunctionModel(model))

    with pytest.raises(AiAgentExecutionError) as caught:
        factory.run_sync(
            profile=PROFILE,
            instructions="返回结构化答案。",
            user_prompt="执行失败测试。",
            toolset=TOOLSET,
        )

    history = store.get(caught.value.conversation_id)
    assert history is not None
    assert history.model_messages()


def test_failure_before_any_model_message_does_not_create_empty_conversation(
    tmp_path: Path,
) -> None:
    message_store = PydanticMessageStore(ErpDatabase(tmp_path / "erp.sqlite3"))

    def broken_binding(*args: Any, **kwargs: Any) -> PydanticModelBinding:
        del args, kwargs
        raise RuntimeError("binding unavailable")

    factory = AiAgentFactory(
        app_dir=tmp_path,
        app_config={},
        message_store=message_store,
        model_binding_factory=broken_binding,
    )

    with pytest.raises(AiAgentExecutionError):
        factory.run_sync(
            profile=PROFILE,
            instructions="返回结构化答案。",
            user_prompt="不会进入模型。",
            toolset=TOOLSET,
        )

    assert message_store.list() == []


def test_store_accepts_complete_tool_call_pair(tmp_path: Path) -> None:
    store = PydanticMessageStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    "lookup_item",
                    {"item_id": "sku-1"},
                    tool_call_id="paired-call",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "lookup_item",
                    {"item_id": "sku-1"},
                    tool_call_id="paired-call",
                )
            ]
        ),
    ]

    saved = store.save("conversation-paired", messages)

    assert saved.model_messages() == messages


def test_store_repairs_unmatched_tool_call_when_history_is_loaded(
    tmp_path: Path,
) -> None:
    store = PydanticMessageStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    incomplete = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    "lookup_item",
                    {"item_id": "sku-1"},
                    tool_call_id="orphan-call",
                )
            ]
        )
    ]

    saved = store.save("conversation-incomplete", incomplete)
    repaired = saved.model_messages()
    returns = [
        part
        for message in repaired
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]

    assert len(returns) == 1
    assert returns[0].tool_call_id == "orphan-call"
    assert returns[0].outcome == "interrupted"
    assert "interrupted" in str(returns[0].content).lower()


def test_repaired_history_is_stable_across_repeated_reads(tmp_path: Path) -> None:
    store = PydanticMessageStore(ErpDatabase(tmp_path / "erp.sqlite3"))
    store.save(
        "conversation-stable-repair",
        [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_item",
                        {"item_id": "sku-1"},
                        tool_call_id="stable-orphan",
                    )
                ]
            )
        ],
    )

    first = store.get("conversation-stable-repair")
    second = store.get("conversation-stable-repair")

    assert first is not None
    assert second is not None
    assert first.model_messages() == second.model_messages()


def test_failed_multi_tool_run_persists_a_return_for_every_call(
    tmp_path: Path,
) -> None:
    def definition(name: str) -> AiToolDefinition:
        return AiToolDefinition(
            name=name,
            version="1",
            description=f"{name} 测试工具",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "required": ["ok"],
                "properties": {"ok": {"type": "boolean"}},
                "additionalProperties": False,
            },
            required_permission="test.read",
            side_effect="none",
        )

    first_definition = definition("first_read")
    second_definition = definition("second_read")

    def first_executor(_arguments: dict[str, Any], _context: Any) -> dict[str, bool]:
        return {"ok": True}

    def second_executor(_arguments: dict[str, Any], _context: Any) -> Any:
        raise AiToolExecutionError("PRODUCT_NOT_FOUND", "商品不存在。")

    toolset = AiToolSet.bind(
        "message.persistence.multi_tool",
        [first_definition, second_definition],
        {
            "first_read": deadline_aware_tool_executor(first_executor),
            "second_read": deadline_aware_tool_executor(second_executor),
        },
    )
    profile = AiAgentExecutionProfile(
        use_case_id="message.persistence.multi_tool",
        output_type=Answer,
        toolset_id=toolset.toolset_id,
        budget_profile="message.persistence.multi_tool.v1",
        permissions=frozenset({"test.read"}),
        timeout_seconds=10,
        max_model_requests=3,
        max_tool_calls=2,
        max_tool_output_bytes=4096,
        retries=0,
    )

    model_turns = 0

    def model(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
        nonlocal model_turns
        model_turns += 1
        if model_turns == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart("first_read", {}, tool_call_id="call-first"),
                    ToolCallPart("second_read", {}, tool_call_id="call-second"),
                ]
            )
        # 业务错误已作为 ToolReturn 回到模型；随后发生 Provider 故障，验证
        # 失败历史仍完整保留上一轮每个并行 tool call 的 return。
        raise ModelAPIError("test-model", "provider unavailable")

    factory, store = _factory(tmp_path, FunctionModel(model))

    with pytest.raises(AiAgentExecutionError) as caught:
        factory.run_sync(
            profile=profile,
            instructions="依次调用两个工具。",
            user_prompt="执行多工具失败测试。",
            toolset=toolset,
        )

    history = store.get(caught.value.conversation_id)
    assert history is not None
    messages = history.model_messages()
    call_ids = [
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]
    return_ids = [
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]

    assert sorted(call_ids) == ["call-first", "call-second"]
    assert sorted(return_ids) == sorted(call_ids)
