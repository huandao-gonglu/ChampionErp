"""并发队列的原生模型回归，覆盖停错回合、重复投递和状态查询。"""
import asyncio
import threading
import time

import pytest
from pydantic_ai.models.function import FunctionModel

from erp_web.services import chat_queue_service as queue
from erp_web.services.agent_job_service import AgentJobService
from erp_web.services.chat_conversation_service import ChatConversationError
from tests.test_native_agent_integration import CONVERSATION, body, service


def wait_for(predicate):
    deadline = time.monotonic() + 6
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.02)


@pytest.mark.parametrize("replace", [False, True])
def test_queue_action_runs_in_visible_chat_and_stops_only_obsolete_turn(tmp_path, monkeypatch, replace):
    entered, finish, interrupted = threading.Event(), threading.Event(), threading.Event()
    followups = []

    async def model(messages, info):
        if not entered.is_set():
            entered.set()
            try:
                while not finish.is_set():
                    await asyncio.sleep(0.02)
            finally:
                interrupted.set()
            yield "旧任务结束"
        else:
            followups.append(messages)
            yield "已按新消息处理"

    ui = service(tmp_path, FunctionModel(stream_function=model))
    jobs = AgentJobService(ui_service=ui, job_readers={})
    original = ui.prepare_run(body("准备图片", "original"))
    thread = threading.Thread(target=lambda: asyncio.run(original.stream(lambda _: None)))
    thread.start()
    try:
        assert entered.wait(4)
        ui.prepare_run(body("删除本条", "removed", queue_if_busy=True))
        ui.prepare_run(body("改成白底" if replace else "图片做完了吗", "now", queue_if_busy=True))
        ui.prepare_run(body("稍后再看库存", "later", queue_if_busy=True))
        queue.act_on_queue(ui, CONVERSATION, "removed", "delete")
        assert ui.claim_store.get(CONVERSATION, "removed").status == "cancelled"
        assert "删除本条" not in str(ui.call_store.user_facts(CONVERSATION))
        assert ui.call_store.inbox(CONVERSATION) == []

        def classify(chat, snapshots, messages):
            assert snapshots[0]["当前用户要求"] == "准备图片"
            return [0] if replace else []

        monkeypatch.setattr(queue, "superseded_indices", classify)
        queue.act_on_queue(ui, CONVERSATION, "now", "send")
        if replace:
            assert interrupted.wait(2)
            thread.join(3)
            jobs.scan()
            wait_for(lambda: ui.claim_store.get(CONVERSATION, "now").status == "completed")
            assert ui.claim_store.get(CONVERSATION, "original").status == "cancelled"
        else:
            child = queue.parallel_run_id(CONVERSATION, "now")
            wait_for(lambda: ui.claim_store.get(child, "now").status == "completed")
            detail = ui.dump_ui_messages(CONVERSATION)
            assert detail["parallel_replies"][0]["messages"][-1]["parts"][-1]["text"] == "已按新消息处理"
            assert not interrupted.is_set()
            assert ui.claim_store.get(CONVERSATION, "original").status == "claimed"
            assert child not in [item.conversation_id for item in ui.chat_service.message_store.list()]
        assert len(followups) == 1
        assert ui.claim_store.get(CONVERSATION, "later").status == "claimed"
        with pytest.raises(ChatConversationError, match="已被处理"):
            queue.act_on_queue(ui, CONVERSATION, "now", "send")
    finally:
        finish.set()
        thread.join(5)
        jobs.close()


def test_model_claim_excludes_delete_and_failed_routing_restores_waiting_input(tmp_path, monkeypatch):
    ui = service(tmp_path, FunctionModel(stream_function=lambda m, i: None))
    ui.prepare_run(body())
    with pytest.raises(ChatConversationError, match="已被处理"):
        queue.act_on_queue(ui, CONVERSATION, "user-1", "delete")
    ui.prepare_run(body("独立问题", "next", queue_if_busy=True))

    def fail(*args):
        raise RuntimeError("模型暂时不可用")

    monkeypatch.setattr(queue, "superseded_indices", fail)
    with pytest.raises(ChatConversationError, match="立即处理未完成"):
        queue.act_on_queue(ui, CONVERSATION, "next", "send")
    assert [r["message_id"] for r in ui.call_store.inbox(CONVERSATION, include_queued=True)] == ["user-1", "next"]
    assert not ui.run_registry.token(CONVERSATION).cancelled


def test_replacement_recalls_deferred_work_before_execution(tmp_path, monkeypatch):
    from pydantic_ai.models.function import DeltaToolCall
    from tests.test_native_agent_integration import binding, tools

    executed = []
    seen = []

    async def model(messages, info):
        seen.append(messages)
        if len(seen) == 1:
            yield {0: DeltaToolCall(name="prepare", json_args='{"draft_id":"a"}', tool_call_id="old")}
        else:
            yield "采用新要求"

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(binding(
        "prepare", lambda args, ctx: executed.append(args) or {}, external=True,
    )))
    run = ui.prepare_run(body("制作详情图"))
    asyncio.run(run.stream(lambda _: None))
    assert ui.call_store.pending(CONVERSATION)
    ui.prepare_run(body("只保留原图", "change", queue_if_busy=True))
    monkeypatch.setattr(queue, "chat_structured", lambda *args, **kwargs: queue.TaskRelation(superseded=[0]))
    queue.act_on_queue(ui, CONVERSATION, "change", "send")
    wait_for(lambda: ui.claim_store.get(CONVERSATION, "change").status == "completed")
    assert ui.claim_store.get(CONVERSATION, "user-1").status == "cancelled"
    assert ui.call_store.pending(CONVERSATION) is None
    assert "OPERATION_CANCELLED" in ui.call_store.receipt(CONVERSATION, "old")["output_json"]
    assert executed == []


def test_correction_targets_only_affected_parallel_task(tmp_path, monkeypatch):
    from erp_web.services.chat_images import prompt_summary

    entered = [threading.Event(), threading.Event()]
    release = threading.Event()

    async def model(messages, info):
        prompt = prompt_summary(messages)
        if "改为黑色" in prompt:
            yield "已改为黑色"
            return
        entered[int("生成白底图" in prompt)].set()
        while not release.is_set():
            await asyncio.sleep(0.02)
        yield "完成"

    ui = service(tmp_path, FunctionModel(stream_function=model))
    jobs = AgentJobService(ui_service=ui, job_readers={})
    root = ui.prepare_run(body("核对订单", "orders"))
    thread = threading.Thread(target=lambda: asyncio.run(root.stream(lambda _: None)))
    thread.start()

    def classify(chat, snapshots, messages):
        if "改为黑色" in prompt_summary(messages):
            return [idx for idx, snapshot in enumerate(snapshots) if snapshot["当前用户要求"] == "生成白底图"]
        return []

    monkeypatch.setattr(queue, "superseded_indices", classify)
    try:
        assert entered[0].wait(4)
        ui.prepare_run(body("生成白底图", "images", queue_if_busy=True))
        queue.act_on_queue(ui, CONVERSATION, "images", "send")
        assert entered[1].wait(4)
        ui.prepare_run(body("背景改为黑色", "revise", queue_if_busy=True))
        queue.act_on_queue(ui, CONVERSATION, "revise", "send")
        child = queue.parallel_run_id(CONVERSATION, "images")
        wait_for(lambda: not ui.run_registry.is_active(child))
        jobs.scan()
        wait_for(lambda: ui.claim_store.get(child, "revise").status == "completed")
        assert ui.claim_store.get(child, "images").status == "cancelled"
        assert ui.claim_store.get(CONVERSATION, "orders").status == "claimed"
        replies = ui.dump_ui_messages(CONVERSATION)["parallel_replies"]
        assert [reply["conversation_id"] for reply in replies] == [child]
    finally:
        release.set()
        thread.join(5)
        jobs.close()
