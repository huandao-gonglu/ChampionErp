"""原生运行边界故障、并发、消息来源和慢客户端验收。"""

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic_ai.messages import ToolReturnPart, UserPromptPart, ModelRequest
from pydantic_ai.models.function import FunctionModel, DeltaToolCall
from pydantic_ai.models.test import TestModel

from erp_web.services.agent_job_service import AgentJobService
from erp_web.services.capability_input_provenance import user_supplied_input
from erp_web.stores.pydantic_message_store import PydanticMessageStoreError
from tests.test_native_agent_integration import (
    service,
    body,
    tools,
    binding,
    CONVERSATION,
)
from tests.test_native_domain_workflow import setup_domain


def test_same_product_patches_do_not_lose_fields_and_stale_draft_is_rejected(
    monkeypatch,
):
    context, ids = setup_domain(monkeypatch)
    product_id = context.products.draft_record(ids[0])["product_id"]
    gate = threading.Barrier(2)

    def update(patch):
        gate.wait(timeout=2)
        context.products.save_product_profile({"product_id": product_id, **patch})

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(update, [{"brand": "修改品牌"}, {"model": "修改型号"}]))
    product = context.products.load_product_from_index(product_id)
    assert (product["brand"], product["model"]) == ("修改品牌", "修改型号")
    original = context.products.draft_record(ids[0])
    first = {**original, "title": "第一次更新"}
    assert context.products.save_draft_detail(first)[2] == 200
    stale = {**original, "stock": "999"}
    assert context.products.save_draft_detail(stale)[2] == 409
    assert context.products.draft_record(ids[0])["title"] == "第一次更新"
    # 普通局部补丁不携带旧快照，可以继续合并独立字段。
    assert (
        context.products.save_draft_detail({"draft_id": ids[0], "stock": "8"})[2] == 200
    )
    assert context.products.draft_record(ids[0])["title"] == "第一次更新"


def test_user_fact_reference_requires_real_message_entity_and_exact_value():
    scope = {
        "user_facts": json.dumps(
            [
                {
                    "message_id": "m1",
                    "draft_ids": ["draft-a"],
                    "text": "请选择 MLM:remote",
                }
            ]
        )
    }
    args = dict(value=["MLM:remote"], source_message_id="m1", entity_id="draft-a")
    assert user_supplied_input(scope, "sales_target", **args)
    assert not user_supplied_input(
        scope, "sales_target", **{**args, "source_message_id": "made-up"}
    )
    assert not user_supplied_input(
        scope, "sales_target", **{**args, "entity_id": "draft-b"}
    )
    assert not user_supplied_input(
        scope, "sales_target", **{**args, "value": ["MLB:remote"]}
    )
    assert not user_supplied_input(
        {"user_input_keys": '["sales_target"]'}, "sales_target", **args
    )


def test_cancel_while_waiting_native_approval_does_not_resume_or_write(tmp_path):
    executed = []

    async def model(messages, info):
        if not any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts):
            yield {
                0: DeltaToolCall(
                    name="delete", json_args='{"draft_id":"a"}', tool_call_id="delete"
                )
            }
        else:
            pytest.fail("点击停止不应再调用模型生成确认回复")

    ui = service(
        tmp_path,
        FunctionModel(stream_function=model),
        tools(
            binding(
                "delete",
                lambda args, ctx: executed.append(1) or {},
                approval=True,
                write=True,
            )
        ),
    )
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert ui.call_store.pending(CONVERSATION)
    ui.cancel_run(CONVERSATION, "user-1")
    assert executed == [] and ui.call_store.pending(CONVERSATION) is None


def test_restart_after_external_dispatch_does_not_repeat_unknown_side_effect(tmp_path):
    executed = []

    async def model(messages, info):
        results = [
            p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)
        ]
        if not results:
            yield {
                0: DeltaToolCall(
                    name="external",
                    json_args='{"draft_id":"a"}',
                    tool_call_id="external",
                )
            }
        else:
            assert results[-1].content["error"]["details"]["outcome_unknown"] is True
            yield "操作结果待对账"

    ui = service(
        tmp_path,
        FunctionModel(stream_function=model),
        tools(
            binding(
                "external",
                lambda args, ctx: executed.append(1) or {},
                external=True,
                write=True,
            )
        ),
    )
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert ui.call_store.claim_job(CONVERSATION, "external")
    # 模拟远端已收到请求，但进程在完成回执落盘之前退出。
    assert ui.call_store.mark_interrupted_writes() == 1
    worker = AgentJobService(ui_service=ui, job_readers={})
    try:
        worker.scan()
        for _ in range(100):
            if not ui.run_registry.is_active(CONVERSATION):
                break
            time.sleep(0.01)
        assert executed == []
        assert ui.call_store.pending(CONVERSATION) is None
        assert "待对账" in json.dumps(
            ui.dump_ui_messages(CONVERSATION), ensure_ascii=False
        )
    finally:
        worker.close()


def test_failed_model_is_not_restarted_from_inbox_and_client_receives_native_error(
    tmp_path,
):
    async def model(messages, info):
        raise RuntimeError("模拟断网")
        yield

    ui = service(tmp_path, FunctionModel(stream_function=model))
    chunks = []
    asyncio.run(ui.prepare_run(body()).stream(chunks.append))
    payload = b"".join(chunks)
    assert b'"type":"error"' in payload and b'"finishReason":"error"' in payload
    assert ui.call_store.conversations() == []
    assert ui.claim_store.find_for_conversation(CONVERSATION).status == "failed"


def test_history_cas_rejects_stale_writer(tmp_path):
    ui = service(tmp_path, TestModel(custom_output_text="完成", call_tools=[]))
    messages = [ModelRequest(parts=[UserPromptPart("原始输入")])]
    assert ui.call_store.commit(CONVERSATION, messages, expected_version=0) == 1
    with pytest.raises(PydanticMessageStoreError, match="版本"):
        ui.call_store.commit(CONVERSATION, [], expected_version=0)
    assert (
        ui.chat_service.trusted_history(CONVERSATION)[0].parts[0].content == "原始输入"
    )


def test_slow_client_gets_explicit_error_and_recovers_complete_native_history(
    tmp_path, monkeypatch
):
    import erp_web.services.vercel_ai_ui_service as transport

    monkeypatch.setattr(transport, "MAX_DELIVERY_QUEUE_CHUNKS", 2)

    async def model(messages, info):
        for _ in range(200):
            yield "片段"

    ui = service(tmp_path, FunctionModel(stream_function=model))
    chunks = []

    def slow(chunk):
        time.sleep(0.01)
        chunks.append(chunk)

    asyncio.run(ui.prepare_run(body()).stream(slow))
    payload = b"".join(chunks)
    assert payload.count(b'"type":"start"') == 1
    assert payload.count(b'"type":"finish"') == 1
    assert b'"finishReason":"error"' in payload
    assert "片段" * 200 in json.dumps(
        ui.dump_ui_messages(CONVERSATION), ensure_ascii=False
    )


def test_commit_failure_never_dispatches_external_side_effect(tmp_path, monkeypatch):
    executed = []

    async def model(messages, info):
        yield {
            0: DeltaToolCall(
                name="external", json_args='{"draft_id":"a"}', tool_call_id="external"
            )
        }

    ui = service(
        tmp_path,
        FunctionModel(stream_function=model),
        tools(
            binding(
                "external",
                lambda args, ctx: executed.append(1) or {},
                external=True,
                write=True,
            )
        ),
    )
    commit = ui.call_store.commit

    def fail_commit(*args, **kwargs):
        if kwargs.get("requests") is not None:
            raise OSError("模拟提交前崩溃")
        return commit(*args, **kwargs)

    monkeypatch.setattr(ui.call_store, "commit", fail_commit)
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert executed == []
    assert ui.call_store.pending(CONVERSATION) is None
    assert ui.call_store.work() == []
    assert ui.call_store.conversations() == []
