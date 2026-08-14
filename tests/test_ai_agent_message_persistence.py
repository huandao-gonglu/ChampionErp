from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

from erp_web.db import ErpDatabase
from erp_web.services.ai_agent_factory import (
    AiAgentExecutionError,
    AiAgentExecutionProfile,
    AiAgentFactory,
)
from erp_web.services.ai_model_factory import PydanticModelBinding
from erp_web.services.ai_tool_registry import AiToolSet
from erp_web.stores.pydantic_message_store import PydanticMessageStore


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

    def binding(*args: Any, **kwargs: Any) -> PydanticModelBinding:
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
