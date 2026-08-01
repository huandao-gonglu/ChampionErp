from __future__ import annotations

from typing import Any

import pytest

from erp_web.context import get_context
from erp_web.schemas.ai_tools import AiToolCall, AiToolDefinition, AiToolTurn
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.ai_gateway_providers import AiProviderClient
from erp_web.services.ai_invocation import AiInvocation
from erp_web.services.ai_task_runner import AiTaskExecutionError, AiTaskRunner
from erp_web.services.ai_tool_provider_adapters import (
    JsonToolTurnFakeAdapter,
    JsonToolTurnProviderAdapter,
    NativeToolTurnFakeProvider,
)
from erp_web.services.ai_tool_registry import (
    AiToolSet,
    deadline_aware_tool_executor,
)
from erp_web.services.ai_provider_contracts import (
    CAPABILITY_CHAT_JSON,
    AiChatProvider,
)


class RecordingRecorder:
    conversation_id = "aic_test_runner"

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def record(self, event_type: str, **payload: Any) -> None:
        self.events.append((event_type, payload))

    def emit(self, event_type: str, **payload: Any) -> None:
        self.record(event_type, **payload)

    def emit_custom(self, name: str, value: Any) -> None:
        self.record("CUSTOM", name=name, value=value)

    def emit_text_delta(self, delta: str) -> None:
        self.record("TEXT_MESSAGE_CONTENT", delta=delta)

    def finish_assistant_message(self, raw_text: str = "") -> None:
        self.record("TEXT_MESSAGE_END", raw_text=raw_text)

    def finish(self, result: Any) -> None:
        self.record("RUN_FINISHED", result=result)

    def fail(self, error: Exception) -> None:
        self.record("RUN_ERROR", error=str(error))


def definition() -> AiToolDefinition:
    return AiToolDefinition(
        name="lookup_item",
        version="1",
        description="读取一个测试条目",
        input_schema={
            "type": "object",
            "required": ["item_id"],
            "properties": {"item_id": {"type": "string"}},
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
            "additionalProperties": False,
        },
        required_permission="catalog.read",
    )


def scripted_call() -> AiToolCall:
    return AiToolCall(
        call_id="call_lookup",
        tool_name="lookup_item",
        tool_version="1",
        arguments={"item_id": "sku-1"},
        round=1,
    )


def invocation(provider, recorder=None) -> AiInvocation:
    return AiInvocation(
        use_case_id="test.tool_loop",
        capability="tool_turn",
        provider=provider,
        model={"id": "fake-model"},
        required_capabilities=("tool_calling",),
        timeout_seconds=30,
        execution_context=AiExecutionContext.create(
            timeout_seconds=30,
            budget_profile="test.default",
            permissions={"catalog.read"},
        ),
        recorder=recorder or RecordingRecorder(),
    )


def toolset(executions: list[str]) -> AiToolSet:
    tool = definition()

    def execute(arguments, context):
        del context
        executions.append(arguments["item_id"])
        return {"name": f"Item {arguments['item_id']}"}

    return AiToolSet.bind(
        "test.read",
        [tool],
        {tool.name: deadline_aware_tool_executor(execute)},
    )


def test_task_runner_completes_one_native_fake_tool_loop() -> None:
    provider = NativeToolTurnFakeProvider(
        [
            AiToolTurn(type="tool_calls", calls=(scripted_call(),)),
            AiToolTurn.final({"selected": "sku-1"}),
        ]
    )
    recorder = RecordingRecorder()
    executions: list[str] = []

    result = AiTaskRunner().run(
        invocation(provider, recorder),
        messages=[{"role": "user", "content": "find sku-1"}],
        toolset=toolset(executions),
        result_schema={
            "type": "object",
            "required": ["selected"],
            "properties": {"selected": {"type": "string"}},
            "additionalProperties": False,
        },
    )

    assert result == {"selected": "sku-1"}
    assert executions == ["sku-1"]
    assert len(provider.requests) == 2
    assert provider.requests[1].tool_results[0].output == {"name": "Item sku-1"}
    assert [event for event, payload in recorder.events].count("TASK_STARTED") == 1
    assert [event for event, payload in recorder.events].count("TASK_FINISHED") == 1
    assert not any(event == "RUN_ERROR" for event, payload in recorder.events)


def test_native_and_json_fake_adapters_have_equivalent_turn_contracts() -> None:
    native = NativeToolTurnFakeProvider(
        [
            AiToolTurn(type="tool_calls", calls=(scripted_call(),)),
            AiToolTurn.final({"selected": "sku-1"}),
        ]
    )
    json_adapter = JsonToolTurnFakeAdapter(
        [
            {
                "type": "tool_calls",
                "calls": [scripted_call().to_dict()],
            },
            {"type": "final", "result": {"selected": "sku-1"}},
        ]
    )
    native_executions: list[str] = []
    json_executions: list[str] = []

    native_result = AiTaskRunner().run(
        invocation(native),
        messages=[{"role": "user", "content": "find sku-1"}],
        toolset=toolset(native_executions),
    )
    json_result = AiTaskRunner().run(
        invocation(json_adapter),
        messages=[{"role": "user", "content": "find sku-1"}],
        toolset=toolset(json_executions),
    )

    assert native_result == json_result == {"selected": "sku-1"}
    assert native_executions == json_executions == ["sku-1"]
    assert json_adapter.request_payloads[1]["tool_results"][0]["output"] == {
        "name": "Item sku-1"
    }
    assert json_adapter.request_payloads[0]["context"]["task_run_id"].startswith(
        "task_"
    )


def test_new_runner_chain_creates_exactly_one_ai_work_conversation() -> None:
    provider = NativeToolTurnFakeProvider([AiToolTurn.final({"ok": True})])
    client = AiProviderClient(
        app_dir=get_context().paths.app_dir,
        use_case_id="test.single_invocation",
        model={"id": "fake-model", "model": "fake-model"},
        required_capabilities=("tool_calling",),
        timeout_seconds=30,
    )
    ai_invocation = client.start_invocation(
        "tool_turn",
        provider,
        {"messages": [{"role": "user", "content": "finish"}]},
        budget_profile="test.default",
        permissions={"catalog.read"},
    )

    result = AiTaskRunner().run(
        ai_invocation,
        messages=[{"role": "user", "content": "finish"}],
        toolset=AiToolSet.bind("test.empty", [], {}),
    )

    conversations = get_context().ai_journal.list_conversations(limit=10)
    assert result == {"ok": True}
    assert len(conversations) == 1
    assert conversations[0]["conversation_id"] == ai_invocation.recorder.conversation_id
    events = get_context().ai_journal.read_events(
        ai_invocation.recorder.conversation_id
    )
    assert [event["type"] for event in events].count("RUN_STARTED") == 1
    assert [event["type"] for event in events].count("RUN_FINISHED") == 1
    task_run_ids = {
        event.get("task_run_id")
        for event in events
        if event.get("task_run_id")
    }
    assert task_run_ids == {ai_invocation.execution_context.task_run_id}


def test_provider_claiming_tool_turn_but_returning_plain_text_is_rejected() -> None:
    class FalseCapabilityProvider(NativeToolTurnFakeProvider):
        def run_tool_turn(self, request):
            self.requests.append(request)
            return "ordinary text"

    provider = FalseCapabilityProvider([])

    with pytest.raises(AiTaskExecutionError) as caught:
        AiTaskRunner().run(
            invocation(provider),
            messages=[{"role": "user", "content": "finish"}],
            toolset=AiToolSet.bind("test.empty", [], {}),
        )

    assert caught.value.code == "MODEL_RESPONSE_SCHEMA_INVALID"


def test_production_json_adapter_reuses_chat_provider_and_runner_contract() -> None:
    class ScriptedChatProvider(AiChatProvider):
        provider_id = "scripted-chat"

        def __init__(self) -> None:
            self.responses = [
                {
                    "type": "tool_calls",
                    "calls": [scripted_call().to_dict()],
                },
                {"type": "final", "result": {"selected": "sku-1"}},
            ]
            self.requests = []

        def supports(self, model, capability):
            return capability == CAPABILITY_CHAT_JSON

        def chat_json(self, request):
            self.requests.append(request)
            return self.responses.pop(0)

        def test_model(self, app_dir, model, raw_model=None):
            return {"ok": True}

    chat_provider = ScriptedChatProvider()
    adapter = JsonToolTurnProviderAdapter(
        chat_provider,
        app_dir=get_context().paths.app_dir,
    )
    executions: list[str] = []

    result = AiTaskRunner().run(
        invocation(adapter),
        messages=[{"role": "user", "content": "find sku-1"}],
        toolset=toolset(executions),
    )

    assert result == {"selected": "sku-1"}
    assert executions == ["sku-1"]
    assert len(chat_provider.requests) == 2
    assert chat_provider.requests[0].conversation is not None
    assert chat_provider.requests[0].messages[0]["role"] == "system"
    assert "受控的 JSON tool protocol" in (
        chat_provider.requests[0].messages[0]["content"]
    )
    assert "tool_results" in chat_provider.requests[1].messages[-1]["content"]


def test_production_json_adapter_rejects_non_object_protocol_response() -> None:
    class PlainTextChatProvider(AiChatProvider):
        provider_id = "plain-chat"

        def supports(self, model, capability):
            return capability == CAPABILITY_CHAT_JSON

        def chat_json(self, request):
            return "ordinary text"

        def test_model(self, app_dir, model, raw_model=None):
            return {"ok": True}

    adapter = JsonToolTurnProviderAdapter(
        PlainTextChatProvider(),
        app_dir=get_context().paths.app_dir,
    )

    with pytest.raises(AiTaskExecutionError) as caught:
        AiTaskRunner().run(
            invocation(adapter),
            messages=[{"role": "user", "content": "finish"}],
            toolset=AiToolSet.bind("test.empty", [], {}),
        )

    assert caught.value.code == "TOOL_PROTOCOL_UNSUPPORTED"


def test_pipeline_toolset_rejects_tool_calls_without_an_extra_model_round() -> None:
    provider = NativeToolTurnFakeProvider(
        [
            AiToolTurn(type="tool_calls", calls=(scripted_call(),)),
            AiToolTurn.final({"selected": "sku-1"}),
        ]
    )

    with pytest.raises(AiTaskExecutionError) as caught:
        AiTaskRunner().run(
            invocation(provider),
            messages=[{"role": "user", "content": "finish"}],
            toolset=AiToolSet.bind("test.empty", [], {}),
        )

    assert caught.value.code == "TOOL_NOT_ALLOWED"
    assert len(provider.requests) == 1
