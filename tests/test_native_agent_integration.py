"""生产装配的原生对话、审批、并发和持久边界验收。"""

import asyncio
import json
import threading
import time

import pytest

from pydantic_ai.messages import UserPromptPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel, DeltaToolCall
from pydantic_ai.models.test import TestModel

from erp_web.services.ai_tool_registry import AiToolSet
from erp_web.services.global_agent_chat_service import GlobalAgentChatService
from erp_web.services.chat_operation_scope import ChatOperationScope
from erp_web.services.vercel_ai_ui_service import VercelAiUiService
from erp_web.services.ai_chat_run_registry import AiChatRunRegistry
from erp_web.services.approval_session import ApprovalSession
from erp_web.stores.ai_chat_turn_claim_store import AiChatTurnClaimStore
from erp_web.stores.agent_call_store import AgentCallStore
from tests.test_ai_agent_stream_session import _factory
from erp_web.schemas.ai_tools import AiToolDefinition, ToolApprovalSnapshot
from erp_web.services.ai_tool_registry import (
    AiToolBinding,
    deadline_aware_tool_executor,
)
from erp_web.services.agent_job_service import AgentJobService
from erp_web.services.vercel_ai_ui_service import (
    AcceptedChatInput,
    VercelUiProtocolError,
)


def binding(name, executor, *, approval=False, external=False, write=False):
    definition = AiToolDefinition(
        name=name,
        version="1",
        description="隔离验收工具",
        required_permission="product.read",
        input_schema={
            "type": "object",
            "properties": {"draft_id": {"type": "string"}},
            "required": ["draft_id"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        side_effect="write" if write else "none",
        approval_required=approval,
        idempotency="required" if write else "none",
        idempotency_keys=("operation_key",) if write else (),
        execution_mode="persistent_job" if external else "sync",
        recovery_policy="idempotent",
    )
    return AiToolBinding(
        definition,
        deadline_aware_tool_executor(executor),
        (
            lambda args: ToolApprovalSnapshot(
                summary="执行 " + args["draft_id"], canonical_payload=args
            )
        )
        if approval
        else None,
    )


def tools(*items):
    return AiToolSet("global.chat", {item.definition.name: item for item in items})


CONVERSATION = "conversation_global_chat_" + "9" * 32


def body(text="准备草稿", message_id="user-1", **extra):
    return json.dumps(
        {
            "id": CONVERSATION,
            "trigger": "submit-message",
            "messages": [
                {
                    "id": message_id,
                    "role": "user",
                    "parts": [{"type": "text", "text": text}],
                }
            ],
            **extra,
        }
    ).encode()


def service(tmp_path, model, toolset=None):
    factory, messages = _factory(tmp_path, model)
    calls = AgentCallStore(messages.db)
    chat = GlobalAgentChatService(
        app_dir=tmp_path,
        app_config={},
        message_store=messages,
        toolset=toolset or AiToolSet.bind("global.chat", [], {}),
        factory=factory,
        call_store=calls,
        scope_resolver=lambda messages: ChatOperationScope(allowed_write_tools=list((toolset or tools()).bindings), all_drafts=False),
    )
    return VercelAiUiService(
        chat_service=chat,
        claim_store=AiChatTurnClaimStore(messages.db),
        run_registry=AiChatRunRegistry(),
        call_store=calls,
        approval_session=ApprovalSession("test-token"),
    )


def test_chat_inbox_canonical_history_and_official_stream(tmp_path):
    ui = service(tmp_path, TestModel(custom_output_text="完成", call_tools=[]))
    for number in (1, 2):
        run = ui.prepare_run(body(f"第{number}轮", f"u{number}"))
        chunks = []
        asyncio.run(run.stream(chunks.append))
        assert b'"type":"finish"' in b"".join(chunks)
    history = ui.chat_service.trusted_history(CONVERSATION)
    texts = [
        p.content for m in history for p in m.parts if isinstance(p, UserPromptPart)
    ]
    assert texts == ["第1轮", "第2轮"]
    assert ui.call_store.inbox(CONVERSATION) == []
    assert not ui.run_registry.is_active(CONVERSATION)


def test_multiple_external_calls_commit_before_dispatch_and_resume_with_more_tools(
    tmp_path,
):
    executed = []

    async def model(messages, info):
        results = [
            p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)
        ]
        if not results:
            yield {
                i: DeltaToolCall(
                    name="prepare",
                    json_args=json.dumps({"draft_id": str(i)}),
                    tool_call_id=f"job-{i}",
                )
                for i in range(2)
            }
        elif len(results) == 2:
            yield {
                0: DeltaToolCall(
                    name="read_latest",
                    json_args='{"draft_id":"0"}',
                    tool_call_id="read-1",
                )
            }
        else:
            yield "两个草稿已处理并回读"

    ui = service(
        tmp_path,
        FunctionModel(stream_function=model),
        tools(
            binding(
                "prepare",
                lambda args, ctx: (
                    executed.append(args["draft_id"]) or {"draft_id": args["draft_id"]}
                ),
                write=True,
                external=True,
            ),
            binding("read_latest", lambda args, ctx: {"verified": True}),
        ),
    )
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    pending = ui.call_store.pending(CONVERSATION)
    assert pending and len(pending[0].calls) == 2
    assert executed == []
    assert ui.chat_service.message_store.get_version(CONVERSATION) > 0
    worker = AgentJobService(ui_service=ui, job_readers={})
    try:
        for row in ui.call_store.work():
            worker.execute_or_reconcile(row)
        worker.scan()
        for _ in range(100):
            if not ui.run_registry.is_active(CONVERSATION):
                break
            time.sleep(0.01)
        assert executed == ["0", "1"]
        assert ui.call_store.pending(CONVERSATION) is None
        assert "两个草稿已处理" in json.dumps(
            ui.dump_ui_messages(CONVERSATION), ensure_ascii=False
        )
    finally:
        worker.close()


def test_native_approval_validates_server_call_and_mixed_decisions(tmp_path):
    executed = []

    async def model(messages, info):
        results = [
            p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)
        ]
        if not results:
            yield {
                i: DeltaToolCall(
                    name="publish",
                    json_args=json.dumps({"draft_id": str(i)}),
                    tool_call_id=f"approve-{i}",
                )
                for i in range(2)
            }
        else:
            yield "已按批准范围执行"

    ui = service(
        tmp_path,
        FunctionModel(stream_function=model),
        tools(
            binding(
                "publish",
                lambda args, ctx: executed.append(args["draft_id"]) or {"ok": True},
                approval=True,
                write=True,
            )
        ),
    )
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    assert executed == []
    ui_messages = ui.dump_ui_messages(CONVERSATION)["messages"]
    parts = [
        p
        for m in ui_messages
        for p in m["parts"]
        if p.get("state") == "approval-requested"
    ]
    assert len(parts) == 2
    for i, p in enumerate(parts):
        p["state"] = "approval-responded"
        p["approval"]["approved"] = i == 0
    request = json.dumps(
        {
            "id": CONVERSATION,
            "trigger": "submit-message",
            "messages": [{"id": "decisions", "role": "assistant", "parts": parts}],
        }
    ).encode()
    with pytest.raises(VercelUiProtocolError):
        ui.prepare_run(request)
    tampered = json.loads(request)
    tampered["messages"][0]["parts"][0]["input"]["draft_id"] = "outside"
    with pytest.raises(VercelUiProtocolError):
        ui.prepare_run(json.dumps(tampered).encode(), approval_token="test-token")
    asyncio.run(
        ui.prepare_run(request, approval_token="test-token").stream(lambda _: None)
    )
    assert executed == ["0"]


def test_running_user_correction_is_received_before_next_write(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    writes = []

    def slow(args, ctx):
        entered.set()
        release.wait(3)
        return {"ok": True}

    async def model(messages, info):
        results = [
            p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)
        ]
        texts = [
            p.content
            for m in messages
            for p in m.parts
            if isinstance(p, UserPromptPart)
        ]
        if not results:
            yield {
                0: DeltaToolCall(
                    name="slow", json_args='{"draft_id":"a"}', tool_call_id="slow"
                )
            }
        elif "改为只读" in texts:
            yield "改为只读查询"
        else:
            yield {
                0: DeltaToolCall(
                    name="write", json_args='{"draft_id":"a"}', tool_call_id="write"
                )
            }

    ui = service(
        tmp_path,
        FunctionModel(stream_function=model),
        tools(
            binding("slow", slow),
            binding(
                "write", lambda args, ctx: writes.append(1) or {"ok": True}, write=True
            ),
        ),
    )
    run = ui.prepare_run(body())
    thread = threading.Thread(target=lambda: asyncio.run(run.stream(lambda _: None)))
    thread.start()
    assert entered.wait(2)
    receipt = ui.prepare_run(body("改为只读", "correction"))
    assert isinstance(receipt, AcceptedChatInput)
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert writes == []
    texts = [
        p.content
        for m in ui.chat_service.trusted_history(CONVERSATION)
        for p in m.parts
        if isinstance(p, UserPromptPart)
    ]
    assert texts == ["准备草稿", "改为只读"]


def test_partial_approvals_are_idempotent_and_wait_for_all_native_decisions(tmp_path):
    executed = []

    async def model(messages, info):
        if not any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts):
            yield {
                i: DeltaToolCall(
                    name="delete",
                    json_args=json.dumps({"draft_id": str(i)}),
                    tool_call_id=f"delete-{i}",
                )
                for i in range(2)
            }
        else:
            yield "已完成批准的操作"

    ui = service(
        tmp_path,
        FunctionModel(stream_function=model),
        tools(
            binding(
                "delete",
                lambda args, ctx: executed.append(args["draft_id"]) or {},
                approval=True,
                write=True,
            )
        ),
    )
    asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    parts = [
        p
        for m in ui.dump_ui_messages(CONVERSATION)["messages"]
        for p in m["parts"]
        if p.get("state") == "approval-requested"
    ]

    def decision(part, approved):
        part["state"] = "approval-responded"
        part["approval"]["approved"] = approved
        return json.dumps(
            {
                "id": CONVERSATION,
                "trigger": "submit-message",
                "messages": [{"id": "approval", "role": "assistant", "parts": [part]}],
            }
        ).encode()

    first = decision(parts[0], True)
    assert isinstance(
        ui.prepare_run(first, approval_token="test-token"), AcceptedChatInput
    )
    assert isinstance(
        ui.prepare_run(first, approval_token="test-token"), AcceptedChatInput
    )
    assert executed == []
    assert len(ui.dump_ui_messages(CONVERSATION)["pending_tool_calls"]) == 1
    asyncio.run(
        ui.prepare_run(decision(parts[1], False), approval_token="test-token").stream(
            lambda _: None
        )
    )
    assert executed == ["0"]


def test_new_request_after_cancel_prepares_newly_authorized_tools(tmp_path):
    """取消后的新请求须在工具准备前更新权限，不得整轮只暴露只读工具。"""
    executed = []
    observations = []

    async def model(messages, info):
        latest = [p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart)][-1]
        names = [tool.name for tool in info.function_tools]
        observations.append((latest, names))
        if any(isinstance(p, ToolReturnPart) and p.tool_name == "product_attributes_update"
                 for m in messages for p in m.parts):
            yield "公共属性已处理"
        else:
            assert "product_attributes_update" in names
            yield {0: DeltaToolCall(name="product_attributes_update", json_args='{"draft_id":"draft-1"}', tool_call_id="fill-after-cancel")}

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(binding(
        "product_attributes_update", lambda args, ctx: executed.append(args["draft_id"]) or {"ok": True}, write=True)))
    ui.cancel_run(CONVERSATION, "cancel")
    asyncio.run(ui.prepare_run(body("继续填写公共属性", "continue")).stream(lambda _: None))
    assert observations[-1][0] == "继续填写公共属性"
    assert executed == ["draft-1"]


def test_followup_replaces_previous_operation_tools_before_first_request(tmp_path):
    """公共属性切换到 SKU 时，第一条模型请求就只获得新操作权限。"""
    executed = []
    observations = []

    async def model(messages, info):
        latest = [p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart)][-1]
        expected = {"填写公共属性": "product_attributes_update", "填写全部 SKU": "draft_sku_attributes_update"}[latest]
        names = {tool.name for tool in info.function_tools}
        observations.append((latest, names))
        assert names == {expected}
        if any(isinstance(p, ToolReturnPart) and p.tool_name == expected for m in messages for p in m.parts):
            yield "已处理"
        else:
            yield {0: DeltaToolCall(name=expected, json_args='{"draft_id":"draft-1"}', tool_call_id=expected)}

    ui = service(tmp_path, FunctionModel(stream_function=model), tools(*[
        binding(name, lambda args, ctx, name=name: executed.append(name) or {}, write=True)
        for name in ("product_attributes_update", "draft_sku_attributes_update")]))
    ui.chat_service.scope_resolver = lambda texts: ChatOperationScope(
        allowed_write_tools=["draft_sku_attributes_update" if texts[-1] == "填写全部 SKU" else "product_attributes_update"], all_drafts=False)
    for index, text in enumerate(("填写公共属性", "填写全部 SKU")):
        asyncio.run(ui.prepare_run(body(text, f"scope-{index}")).stream(lambda _: None))
    assert executed == ["product_attributes_update", "draft_sku_attributes_update"], observations
