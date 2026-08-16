from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
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


PROFILE = AiAgentExecutionProfile(
    use_case_id="stream.session.test",
    output_type=str,
    toolset_id="stream.session.empty",
    budget_profile="stream.session.v1",
    permissions=frozenset(),
    timeout_seconds=10,
    max_model_requests=2,
    max_tool_calls=1,
    max_tool_output_bytes=4096,
    retries=0,
)
TOOLSET = AiToolSet.bind("stream.session.empty", [], {})
OTHER_TOOLSET = AiToolSet.bind("stream.session.other", [], {})
CONVERSATION = "conversation_global_chat_" + "e" * 32


def _user_message(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _factory(
    tmp_path: Path,
    model: Any,
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


def _text_model(text: str = "流式回复。") -> TestModel:
    return TestModel(custom_output_text=text, call_tools=[])


def test_stream_run_persists_result_messages_on_success(tmp_path: Path) -> None:
    factory, store = _factory(tmp_path, _text_model())

    async def run() -> None:
        async with factory.open_stream_run(
            profile=PROFILE,
            instructions="测试 instructions。",
            toolset=TOOLSET,
            conversation_id=CONVERSATION,
            message_history=[],
        ) as session:
            events = [
                type(event).__name__
                async for event in session.events([_user_message("第一轮")])
            ]
            assert session.completed is True
            assert session.history_persisted is True
            assert "AgentRunResultEvent" in events

    asyncio.run(run())

    history = store.get(CONVERSATION)
    assert history is not None
    messages = history.model_messages()
    assert messages == ModelMessagesTypeAdapter.validate_json(
        history.messages_json
    )
    assert any(isinstance(message, ModelResponse) for message in messages)


def test_second_stream_run_appends_only_the_new_user_turn(
    tmp_path: Path,
) -> None:
    factory, store = _factory(tmp_path, _text_model())

    async def run_turn(history: list[Any], text: str) -> None:
        async with factory.open_stream_run(
            profile=PROFILE,
            instructions="测试 instructions。",
            toolset=TOOLSET,
            conversation_id=CONVERSATION,
            message_history=history,
        ) as session:
            async for _event in session.events([_user_message(text)]):
                pass

    async def scenario() -> None:
        await run_turn([], "第一轮")
        first = store.get(CONVERSATION).model_messages()
        await run_turn(first, "第二轮")
        second = store.get(CONVERSATION).model_messages()

        first_user_texts = _user_texts(first)
        second_user_texts = _user_texts(second)
        # 可信历史加新一轮用户输入，旧消息不重复。
        assert first_user_texts == ["第一轮"]
        assert second_user_texts == ["第一轮", "第二轮"]
        assert len(second) > len(first)

    asyncio.run(scenario())


def _user_texts(messages: list[Any]) -> list[str]:
    texts: list[str] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                texts.append(part.content)
    return texts


def test_stream_run_maps_model_failure_to_safe_error_and_persists_capture(
    tmp_path: Path,
) -> None:
    class FailingModel(TestModel):
        def _request(self, *args: Any, **kwargs: Any) -> Any:
            raise ModelAPIError("test-model", "provider failure")

    factory, store = _factory(tmp_path, FailingModel(call_tools=[]))

    async def run() -> AiAgentExecutionError:
        with pytest.raises(AiAgentExecutionError) as caught:
            async with factory.open_stream_run(
                profile=PROFILE,
                instructions="测试 instructions。",
                toolset=TOOLSET,
                conversation_id=CONVERSATION,
                message_history=[],
            ) as session:
                async for _event in session.events([_user_message("失败轮")]):
                    pass
        return caught.value

    error = asyncio.run(run())
    assert isinstance(error, AiAgentExecutionError)
    history = store.get(CONVERSATION)
    assert history is not None
    assert history.model_messages()


def test_stream_run_requires_matching_toolset(tmp_path: Path) -> None:
    factory, _store = _factory(tmp_path, _text_model())

    async def run() -> AiAgentExecutionError:
        with pytest.raises(AiAgentExecutionError) as caught:
            async with factory.open_stream_run(
                profile=PROFILE,
                instructions="测试 instructions。",
                toolset=OTHER_TOOLSET,
                conversation_id=CONVERSATION,
                message_history=[],
            ):
                pass
        return caught.value

    error = asyncio.run(run())
    assert error.code == "TOOLSET_BINDING_MISMATCH"


def test_stream_session_events_can_only_start_once(tmp_path: Path) -> None:
    factory, _store = _factory(tmp_path, _text_model())

    async def run() -> None:
        async with factory.open_stream_run(
            profile=PROFILE,
            instructions="测试 instructions。",
            toolset=TOOLSET,
            conversation_id=CONVERSATION,
            message_history=[],
        ) as session:
            first = session.events([_user_message("第一轮")])
            with pytest.raises(AiAgentExecutionError) as caught:
                session.events([_user_message("重复注入")])
            assert caught.value.code == "AI_AGENT_STREAM_ALREADY_STARTED"
            async for _event in first:
                pass

    asyncio.run(run())


def test_early_disconnect_persists_captured_messages_and_releases(
    tmp_path: Path,
) -> None:
    factory, store = _factory(tmp_path, _text_model())

    async def run() -> None:
        async with factory.open_stream_run(
            profile=PROFILE,
            instructions="测试 instructions。",
            toolset=TOOLSET,
            conversation_id=CONVERSATION,
            message_history=[],
        ) as session:
            iterator = session.events([_user_message("断连轮")])
            # 只消费第一个事件就关闭，模拟客户端断连。
            await iterator.__anext__()
            await session.aclose_events()

    asyncio.run(run())

    history = store.get(CONVERSATION)
    assert history is not None
    captured = history.model_messages()
    assert captured
    # 断连保存的是已捕获消息，不包含完整助手结果。
    assert any(
        isinstance(message, ModelRequest) for message in captured
    )
