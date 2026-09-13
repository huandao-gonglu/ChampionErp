"""停止按钮直接取消原生运行，不等待模型完成，也不自动续跑。"""

import asyncio
import json
import threading

import pytest
from pydantic_ai.models.function import FunctionModel, DeltaToolCall
from pydantic_ai.messages import TextPart, UserPromptPart

from erp_web.services.agent_job_service import AgentJobService
from erp_web.services.ai_chat_run_registry import AiChatRunRegistry
from erp_web.services.global_agent_chat_service import GLOBAL_CHAT_PROFILE
from erp_web.services.vercel_ai_ui_service import VercelUiProtocolError
from tests.test_native_agent_integration import service, body, tools, binding, CONVERSATION


@pytest.mark.parametrize("phase", ["waiting", "text", "tool_arguments"])
def test_stop_interrupts_model_without_waiting_for_next_delta(tmp_path, phase):
    entered, interrupted = threading.Event(), threading.Event()
    executed, chunks = [], []
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        latest = [p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart)][-1]
        if latest == "新的问题":
            yield "新问题完成"
            return
        if phase == "text":
            yield "已经输出的部分"
        elif phase == "tool_arguments":
            yield {0: DeltaToolCall(name="write", json_args='{"draft_id":', tool_call_id="write")}
        entered.set()
        try:
            await asyncio.sleep(60)
        finally:
            interrupted.set()
        yield "不应输出"

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(binding(
        "write", lambda args, ctx: executed.append(1) or {}, write=True,
    )))
    run = ui.prepare_run(body())
    thread = threading.Thread(target=lambda: asyncio.run(run.stream(chunks.append)))
    thread.start()
    try:
        assert entered.wait(3)
        assert ui.cancel_run(CONVERSATION, "user-1")["ok"]
        assert interrupted.wait(2), "停止必须中断正在等待的模型"
        thread.join(3)
        assert not thread.is_alive()
        assert executed == []
        payload = b"".join(chunks)
        assert b'"type":"abort"' in payload
        assert b'"type":"error"' not in payload
        assert ui.claim_store.find_for_conversation(CONVERSATION).status == "cancelled"
        assert ui.call_store.conversations() == []
        if phase == "text":
            assert any(isinstance(p, TextPart) and "已经输出" in p.content
                       for m in ui.chat_service.trusted_history(CONVERSATION) for p in m.parts)
        worker = AgentJobService(ui_service=ui, job_readers={})
        try:
            worker.scan()
            assert calls == 1
        finally:
            worker.close()
        # 原生框架修复中断的工具参数历史；下一条明确输入可以正常启动。
        asyncio.run(ui.prepare_run(body("新的问题", "new")).stream(lambda _: None))
        assert calls == 2
        assert ui.claim_store.find_for_conversation(CONVERSATION).status == "completed"
    finally:
        ui.run_registry.token(CONVERSATION).cancel()
        thread.join(3)


def test_stop_before_request_arrives_or_producer_starts(tmp_path):
    executed = []

    async def model(messages, info):
        executed.append(1)
        yield "完成"

    ui = service(tmp_path, FunctionModel(stream_function=model))
    ui.cancel_run(CONVERSATION, "late")
    with pytest.raises(VercelUiProtocolError) as error:
        ui.prepare_run(body("迟到消息", "late"))
    assert error.value.code == "AI_CHAT_TURN_ALREADY_ACCEPTED"
    run = ui.prepare_run(body("尚未启动", "next"))
    ui.cancel_run(CONVERSATION, "next")
    asyncio.run(run.stream(lambda _: None))
    assert not executed
    assert not ui.run_registry.is_active(CONVERSATION)
    assert ui.call_store.conversations() == []


def test_stopped_deferred_work_is_not_dispatched_or_resumed(tmp_path):
    executed = []

    async def model(messages, info):
        yield {
            0: DeltaToolCall(name="job", json_args='{"draft_id":"a"}', tool_call_id="job"),
            1: DeltaToolCall(name="delete", json_args='{"draft_id":"a"}', tool_call_id="delete"),
        }

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(
        binding("job", lambda args, ctx: executed.append("job") or {}, external=True, write=True),
        binding("delete", lambda args, ctx: executed.append("delete") or {}, approval=True, write=True),
    ))
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert ui.call_store.pending(CONVERSATION)
    ui.cancel_run(CONVERSATION, "user-1")
    # 模拟进程重启后令牌丢失，持久队列仍不能重新调度已停止操作。
    ui.run_registry = AiChatRunRegistry()
    worker = AgentJobService(ui_service=ui, job_readers={})
    try:
        worker.scan()
        assert not worker.futures
        assert not executed
        assert ui.call_store.pending(CONVERSATION) is None
        assert ui.call_store.conversations() == []
        receipt = ui.call_store.receipt(CONVERSATION, "job")
        assert json.loads(receipt["output_json"])["error"]["code"] == "OPERATION_CANCELLED"
    finally:
        worker.close()


def test_stop_during_sync_tool_prevents_followup_and_preserves_actual_receipt(tmp_path):
    entered, release = threading.Event(), threading.Event()
    completed, requests = [], []

    def slow(args, ctx):
        entered.set()
        release.wait(5)
        completed.append(1)
        return {"ok": True}

    async def model(messages, info):
        requests.append(1)
        yield {0: DeltaToolCall(name="slow", json_args='{"draft_id":"a"}', tool_call_id="slow")}

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(binding("slow", slow, write=True)))
    run = ui.prepare_run(body())
    thread = threading.Thread(target=lambda: asyncio.run(run.stream(lambda _: None)))
    thread.start()
    try:
        assert entered.wait(3)
        ui.cancel_run(CONVERSATION, "user-1")
        thread.join(2)
        assert not thread.is_alive(), "Agent 不应等待同步工具自然返回才停止"
        assert not completed
    finally:
        release.set()
        thread.join(3)
    for _ in range(100):
        receipt = ui.call_store.receipt(CONVERSATION, "slow")
        if receipt["status"] == "completed":
            break
        threading.Event().wait(0.01)
    assert receipt["status"] == "completed"
    assert requests == [1]
    assert json.loads(receipt["output_json"])["ok"]


def test_stop_cancels_nested_agent_in_persistent_tool_thread(tmp_path):
    entered, interrupted = threading.Event(), threading.Event()
    completed = []

    async def model(messages, info):
        latest = [p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart)][-1]
        if latest == "子任务":
            entered.set()
            try:
                await asyncio.sleep(60)
            finally:
                interrupted.set()
            yield "不应完成"
        else:
            yield {0: DeltaToolCall(name="job", json_args='{"draft_id":"a"}', tool_call_id="job")}

    def job(args, ctx):
        ui.chat_service.factory.run_sync(
            profile=GLOBAL_CHAT_PROFILE, instructions="子任务", user_prompt="子任务", toolset=tools(),
        )
        completed.append(1)
        return {"ok": True}

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(binding("job", job, external=True, write=True)))
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    worker = AgentJobService(ui_service=ui, job_readers={})
    try:
        worker.scan()
        assert entered.wait(3)
        ui.cancel_run(CONVERSATION, "user-1")
        assert interrupted.wait(2)
        for future in worker.futures.values():
            from pydantic_ai import RunCancelled
            with pytest.raises(RunCancelled):
                future.result(timeout=3)
        worker.scan()
        assert not completed
        assert ui.call_store.pending(CONVERSATION) is None
        assert ui.call_store.conversations() == []
    finally:
        ui.run_registry.token(CONVERSATION).cancel()
        worker.close()


def test_late_duplicate_stop_does_not_cancel_new_input(tmp_path):
    async def model(messages, info):
        yield "完成"

    ui = service(tmp_path, FunctionModel(stream_function=model))
    ui.cancel_run(CONVERSATION, "old")
    run = ui.prepare_run(body("新消息", "new"))
    with pytest.raises(VercelUiProtocolError) as error:
        ui.cancel_run(CONVERSATION, "old")
    assert error.value.code == "AI_CHAT_STOP_TARGET_STALE"
    assert not ui.run_registry.token(CONVERSATION).cancelled
    asyncio.run(run.stream(lambda _: None))
    assert ui.claim_store.find_for_conversation(CONVERSATION).status == "completed"
